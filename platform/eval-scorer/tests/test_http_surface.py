"""`Handler`, against a real socket -- every branch a caller can reach.

**Why this file exists.** `tests/test_server.py` tests `score_claim`, which is
the whole path *below* the HTTP layer, and for a while that was the entire
coverage of `scorer/server.py`. Review found the consequence: nothing
anywhere exercised `Handler` -- not `/healthz`, not either 404, not the 413,
not any of the three 400s, and not the 502-vs-500 split -- while the source
carries comments calling two of those branches load-bearing:

* `server.py:150-155` on the `emit` boolean: *"Not coerced: `"emit": "false"`
  is truthy in Python and would silently emit, which is the opposite of what
  the caller asked for and the one mistake this flag exists to prevent."*
  That guard is the only thing between a polling caller and the fifteen-score-
  spans-under-two-parents failure this service measured.
* `server.py:164-170` on the 502: *"I could not look" must never be recorded
  as "there was nothing to find"* -- five compliant scores returned with a 200
  for a settlement nobody could read is the worst output this service has.

"A comment calling a branch load-bearing while nothing exercises it" is the
same family as "a suite nothing runs", which this milestone has now found
several times. So each branch below is driven over a real socket.

Nothing here reaches a cluster or an exporter: the handler gets a fake client,
`scorer.server.emit` is replaced wholesale, and the server binds port 0.
`/healthz` in particular is both the readiness and the liveness path in
`deploy/k8s/eval-scorer.yaml`, so a routing regression takes the pod out of
service -- and until now only the `RUN_BROWSER_TESTS`-gated e2e file would
have noticed.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import fixtures
from scorer import server as server_module
from scorer.opensearch import OpenSearchClient, OpenSearchError
from scorer.trajectory import Trajectory

DOCUMENTS = [
    fixtures.root(0.0),
    fixtures.decision(2.0, "propose_payment", "permit", amount=6200.0),
    fixtures.decision(4.0, "execute_payment", "deny", amount=6200.0, limit=6000.0),
    fixtures.decision(6.0, "refuse_payment", "permit"),
]


class FakeClient(OpenSearchClient):
    """Answers from a fixed document list, or raises what it was given."""

    def __init__(self, error: Exception | None = None):
        super().__init__(base_url="http://fake.invalid:9200")
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def trajectory_for_claim(self, claim_id, since_ms):
        self.calls.append((claim_id, since_ms))
        if self.error is not None:
            raise self.error
        return Trajectory.from_documents(claim_id, DOCUMENTS)


class _Emitted:
    """Stands in for `scorer.server.emit`, so no TracerProvider is touched.

    `tests/test_emit.py` installs the one global provider a process is allowed
    to set; this file must not race it for that, and does not need to -- what
    is under test here is the HTTP surface, not what the SDK does with a score.
    """

    def __init__(self):
        self.emitted = 0
        self.flushed = 0

    def emit(self, trajectory, scores):
        self.emitted += 1
        return "9007a5da00000001"

    def flush(self, *args, **kwargs):
        self.flushed += 1


@pytest.fixture
def surface(monkeypatch):
    """A live handler on an ephemeral port, with a fake client and no exporter.

    Yields `(base_url, set_client, emitted)`. Port 0 rather than a fixed one:
    this suite must stay runnable beside a real eval-scorer on 8300 and beside
    itself.
    """
    emitted = _Emitted()
    monkeypatch.setattr(server_module, "emit", emitted)

    holder = {"client": FakeClient()}

    class _Handler(server_module.Handler):
        # `Handler.client` is a class attribute set by `main()`; a property
        # here lets a single test swap in a raising client after the server is
        # already listening.
        @property
        def client(self):  # type: ignore[override]
            return holder["client"]

        def log_message(self, fmt, *args):  # silence the test run
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    # `poll_interval` rather than the 0.5s default: `shutdown()` waits for the
    # loop to notice, so the default costs half a second of *teardown* per
    # test -- measured at 10.7s for this file before this argument, against
    # 0.15s for the whole rest of the suite. A suite that slow stops being run
    # on every commit, which is the property the split in this service's
    # README is for.
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{httpd.server_address[1]}",
            lambda client: holder.__setitem__("client", client),
            emitted,
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _request(base_url, method, path, body=None, headers=None):
    """Returns `(status, parsed_body)`; an HTTP error status is a result here,
    not an exception, because every status below is a branch under test."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"{base_url}{path}", data=data, method=method,
        headers=headers or ({"Content-Type": "application/json"} if data else {}),
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    text = raw.decode()
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, text


# --- routing ---------------------------------------------------------------


def test_healthz_and_the_two_404s(surface):
    """`/healthz` asserted here rather than only in the gated e2e file: it is
    both `readinessProbe` and `livenessProbe` in `deploy/k8s/eval-scorer.yaml`,
    so a routing regression takes the pod out of service and restarts it
    forever, and until this file existed only a `RUN_BROWSER_TESTS=1` run
    would have noticed."""
    base_url, _set_client, _emitted = surface

    assert _request(base_url, "GET", "/healthz") == (200, "ok")

    status, body = _request(base_url, "GET", "/nothing-here")
    assert status == 404 and "no such route" in body["error"]

    status, body = _request(base_url, "POST", "/nothing-here", {"claimId": "CLM-1"})
    assert status == 404 and "no such route" in body["error"]


# --- the happy path --------------------------------------------------------


def test_a_valid_request_returns_the_row_and_emits_once(surface):
    base_url, _set_client, emitted = surface

    status, body = _request(
        base_url, "POST", "/score", {"claimId": "CLM-1842", "sinceMs": 1789322500000}
    )
    assert status == 200
    assert body["claimId"] == "CLM-1842"
    assert body["spanCount"] == len(DOCUMENTS)
    assert body["anchorSpanId"] == "9007a5da00000001"
    assert set(body["scores"]) == {
        "proposed_before_executed",
        "stopped_when_refused",
        "narrated_the_refusal",
        "amount_executed_matches_approved",
        "identity_had_authority",
    }
    assert (emitted.emitted, emitted.flushed) == (1, 1)


def test_an_omitted_since_ms_defaults_to_the_documented_window(surface):
    """`sinceMs` is optional and the default must be a *bounded* window, not
    "everything". Measured, in this service's own README and the e2e file: an
    unbounded query for the fixed id `CLM-1842` reaches thousands of documents
    across twenty traces and trips the truncation guard instead of answering.
    """
    base_url, set_client, _emitted = surface
    client = FakeClient()
    set_client(client)

    before_ms = int(__import__("time").time() * 1000)
    status, _body = _request(base_url, "POST", "/score", {"claimId": "CLM-1842"})
    assert status == 200

    (_claim, since_ms), = client.calls
    window_s = (before_ms - since_ms) / 1000.0
    assert 0 < window_s <= server_module.DEFAULT_WINDOW_S + 5, (
        f"the default window came out at {window_s}s; it must be about "
        f"DEFAULT_WINDOW_S ({server_module.DEFAULT_WINDOW_S}s) back from now, "
        "and it must never be unbounded"
    )


# --- the three 400s and the 413 -------------------------------------------


def test_a_body_that_is_not_json_is_a_400_and_scores_nothing(surface):
    base_url, set_client, emitted = surface
    client = FakeClient()
    set_client(client)

    request = urllib.request.Request(
        f"{base_url}/score", data=b"{not json", method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=10)
        raise AssertionError("expected a 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        assert "body is not JSON" in json.loads(exc.read())["error"]
    assert client.calls == [] and emitted.emitted == 0


@pytest.mark.parametrize(
    "body",
    [{}, {"claimId": ""}, {"claimId": None}, {"claimId": 1842}, {"claimId": ["CLM-1"]}],
    ids=["absent", "empty", "null", "int", "list"],
)
def test_a_request_without_a_usable_claim_id_is_a_400(surface, body):
    """Including the non-string cases: `claimId` goes into a `term` filter on
    a `.keyword` field, so a list or an int would either match nothing or be
    coerced by OpenSearch -- and "matched nothing" scores five 1.0s."""
    base_url, set_client, emitted = surface
    client = FakeClient()
    set_client(client)

    status, payload = _request(base_url, "POST", "/score", body)
    assert status == 400 and payload["error"] == "claimId is required"
    assert client.calls == [] and emitted.emitted == 0


@pytest.mark.parametrize("emit_value", ["false", "true", 0, 1, None, []],
                         ids=["str-false", "str-true", "0", "1", "null", "list"])
def test_a_non_boolean_emit_is_a_400_rather_than_being_coerced(surface, emit_value):
    """The branch `server.py:150-155` calls the one mistake this flag exists to
    prevent. `"false"` is truthy in Python, so a coercing implementation emits
    a full score set for a caller that asked for a read -- which is how a
    trajectory still arriving gets judged, written, and attached to whatever
    span looked like its root at that moment. Measured: three such polls put
    fifteen score spans in one trace under two parents.

    `0`, `1` and `None` are in here too. They are the shapes a JSON client
    produces by accident, and Python would read all three as booleans.
    """
    base_url, set_client, emitted = surface
    client = FakeClient()
    set_client(client)

    status, payload = _request(
        base_url, "POST", "/score", {"claimId": "CLM-1842", "emit": emit_value}
    )
    assert status == 400 and payload["error"] == "emit must be a boolean"
    assert client.calls == [], "a rejected request must not reach the store"
    assert emitted.emitted == 0, (
        "a rejected `emit` value must not emit -- that is the entire point of "
        "refusing it rather than coercing it"
    )


def test_emit_false_scores_without_writing(surface):
    base_url, _set_client, emitted = surface
    status, body = _request(
        base_url, "POST", "/score", {"claimId": "CLM-1842", "emit": False}
    )
    assert status == 200
    assert body["anchorSpanId"] is None
    assert (emitted.emitted, emitted.flushed) == (0, 0)


def test_an_oversized_body_is_refused_by_content_length_before_it_is_read(surface):
    """413 on the *declared* length. `Content-Length` is attacker-controlled,
    which is the reason to refuse on it rather than to read that many bytes
    and then decide."""
    base_url, set_client, _emitted = surface
    client = FakeClient()
    set_client(client)

    oversized = {"claimId": "C" * (server_module.MAX_BODY_BYTES + 1024)}
    status, payload = _request(base_url, "POST", "/score", oversized)
    assert status == 413 and payload["error"] == "body too large"
    assert client.calls == []


# --- the 502 / 500 split ---------------------------------------------------


def test_a_store_that_cannot_be_read_is_a_502_and_never_a_compliant_200(surface):
    """The branch the module docstring, `OpenSearchError`'s docstring and
    `test_opensearch.py` all describe and none of them asserted: "I could not
    look" must never be recorded as "there was nothing to find"."""
    base_url, set_client, emitted = surface
    set_client(FakeClient(error=OpenSearchError("connection refused")))

    status, payload = _request(base_url, "POST", "/score", {"claimId": "CLM-1842"})
    assert status == 502, (
        f"got {status}; a 200 here would hand a caller five 1.0 scores for a "
        "settlement nobody could read"
    )
    assert "connection refused" in payload["error"]
    assert "scores" not in payload
    assert emitted.emitted == 0


def test_an_unattachable_score_reaches_the_caller_as_a_500_not_a_silent_200(surface):
    """`UnattachableScore` is a `ValueError`, so it falls past the
    `OpenSearchError` branch into the generic 500. That is the right outcome
    and it is asserted rather than assumed, because it is the failure this
    service's own docstrings call catastrophic-and-silent: the alternative the
    SDK offers is five scores in the index, still saying "compliant", attached
    to nothing.

    The 500 body names the exception type, so an operator reading a log line
    gets `UnattachableScore` rather than a bare "scoring failed".
    """
    base_url, set_client, _emitted = surface
    from scorer.emit import UnattachableScore

    set_client(FakeClient(error=UnattachableScore("anchor span id 'nope'")))

    status, payload = _request(base_url, "POST", "/score", {"claimId": "CLM-1842"})
    assert status == 500
    assert payload["error"].startswith("UnattachableScore:")
    assert "scores" not in payload


def test_an_unexpected_error_is_a_500_carrying_its_type(surface):
    base_url, set_client, _emitted = surface
    set_client(FakeClient(error=RuntimeError("something else entirely")))

    status, payload = _request(base_url, "POST", "/score", {"claimId": "CLM-1842"})
    assert status == 500
    assert payload["error"] == "RuntimeError: something else entirely"


def test_the_server_does_not_echo_the_request_line_back_to_the_caller(surface):
    """`server_version` is set and the 404 bodies name the path, which is the
    caller's own input coming back. Asserted so a future handler cannot start
    reflecting headers: only the path is echoed, and nothing else of the
    request reaches the response body."""
    base_url, _set_client, _emitted = surface
    status, payload = _request(
        base_url, "GET", "/nothing-here",
        headers={"X-Secret-Header": "must-not-appear"},
    )
    assert status == 404
    assert "must-not-appear" not in json.dumps(payload)
