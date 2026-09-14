"""The two-step join, asserted on the query bodies rather than on a comment.

`scorer/opensearch.py`'s header says step 2 must not also filter on the claim
id. A comment saying so is exactly what has failed in this repo before, so
this file reads the bodies the client actually builds and fails if the
narrowing ever reappears. Nothing here opens a socket: `search` is replaced
with a recorder.
"""
from __future__ import annotations

import json

import pytest

import fixtures
from scorer.opensearch import (
    DEFAULT_WINDOW_S,
    MAX_TRACES_PER_CLAIM,
    SPAN_PAGE_SIZE,
    TRACE_INDEX,
    OpenSearchClient,
    OpenSearchError,
    TrajectoryTruncated,
)
from scorer.trajectory import ATTR_CLAIM_ID

SINCE_MS = 1789322500000


class RecordingClient(OpenSearchClient):
    """An `OpenSearchClient` whose `search` answers from a script and records
    every body it was given."""

    def __init__(self, responses):
        super().__init__(base_url="http://recorded.invalid:9200")
        self.responses = list(responses)
        self.bodies = []

    def search(self, body):
        self.bodies.append(body)
        return self.responses.pop(0)


def _trace_bucket_response(*trace_ids, sum_other_doc_count=0, error_bound=0):
    """A step-1 response.

    The two aggregation-level keys default to 0 — OpenSearch always sends
    both — so that the happy-path callers below read as they did before the
    M4 final review, and the truncation cases have to ask for it explicitly.
    """
    return {
        "aggregations": {
            "traces": {
                "sum_other_doc_count": sum_other_doc_count,
                "doc_count_error_upper_bound": error_bound,
                "buckets": [
                    {
                        "key": trace_id,
                        "doc_count": 10,
                        "first": {"value": 1789322500000 + index},
                    }
                    for index, trace_id in enumerate(trace_ids)
                ],
            }
        }
    }


def _hits_response(documents, total=None):
    return {
        "hits": {
            "hits": [{"_source": d} for d in documents],
            "total": {"value": len(documents) if total is None else total},
        }
    }


def _filters(body):
    return body["query"]["bool"]["filter"]


def test_step_one_filters_on_the_claim_id_and_the_window():
    client = RecordingClient([_trace_bucket_response("aaaa", "bbbb")])
    assert client.trace_ids_for_claim("CLM-1842", SINCE_MS) == ["aaaa", "bbbb"]

    filters = _filters(client.bodies[0])
    assert {"term": {f"attributes.{ATTR_CLAIM_ID}.keyword": "CLM-1842"}} in filters
    assert {"range": {"startTime": {"gte": SINCE_MS}}} in filters


def test_step_two_filters_on_trace_ids_and_never_on_the_claim_id():
    """GAP 4 -- DO NOT COLLAPSE THE TWO STEPS INTO ONE FILTER.

    `northstar.claim.id` does not cross the MCP boundary, so a claim-id filter
    here would drop every `policy.decision` span -- which is every span these
    scorers read. The service would then score an empty trajectory and report
    five compliant `not-applicable`s for a settlement that really happened.
    """
    client = RecordingClient([_hits_response([])])
    client.spans_in_traces(["aaaa", "bbbb"], SINCE_MS)

    body = client.bodies[0]
    filters = _filters(body)
    assert {"terms": {"traceId": ["aaaa", "bbbb"]}} in filters
    assert {"range": {"startTime": {"gte": SINCE_MS}}} in filters

    rendered = json.dumps(body)
    assert ATTR_CLAIM_ID not in rendered, (
        "the span query names the claim id, so the join has been collapsed "
        f"back into one filter: {rendered}"
    )
    assert body["size"] == SPAN_PAGE_SIZE
    assert body["track_total_hits"] is True
    assert body["sort"] == [{"startTime": "asc"}]


def test_no_trace_ids_means_no_second_query_at_all():
    """An empty `terms` filter matches nothing but still costs a round trip,
    and `{"terms": {"traceId": []}}` is the kind of query that quietly becomes
    a match-all after a refactor."""
    client = RecordingClient([])
    assert client.spans_in_traces([], SINCE_MS) == []
    assert client.bodies == []


def test_a_truncated_page_raises_rather_than_scoring_what_it_got():
    """The one failure mode that must never degrade into an answer: a
    truncated trajectory scores HIGHER than the real one, because a retry that
    fell off the end of the page reads as compliance."""
    client = RecordingClient([_hits_response([fixtures.root(0.0)], total=4000)])
    with pytest.raises(TrajectoryTruncated) as excinfo:
        client.spans_in_traces(["aaaa"], SINCE_MS)
    assert "1 of 4000" in str(excinfo.value)
    assert isinstance(excinfo.value, OpenSearchError)


# --- M4 final review, H1: step 1 truncated silently while step 2 refused to --


def test_step_one_refuses_a_truncated_trace_list():
    """The HIGH finding of M4's final review, pinned in the direction it bites.

    `trace_ids_for_claim` capped the terms aggregation at
    `MAX_TRACES_PER_CLAIM` and inspected neither `sum_other_doc_count` nor
    `doc_count_error_upper_bound` — thirty lines above `spans_in_traces`,
    which refuses to score a truncated page and explains why in a message
    written for this milestone.

    Measured against the live index on 2026-09-14, this was not hypothetical:
    `CLM-1842` had **57** distinct traces across 2,263 documents, and the
    aggregation returned 20 with `sum_other_doc_count: 874`.
    """
    client = RecordingClient(
        [_trace_bucket_response(*(f"trace{n:02d}" for n in range(20)),
                                sum_other_doc_count=874)]
    )
    with pytest.raises(TrajectoryTruncated) as excinfo:
        client.trace_ids_for_claim("CLM-1842", SINCE_MS)
    message = str(excinfo.value)
    assert "874" in message, f"the dropped-document count must be named: {message}"
    assert f"{MAX_TRACES_PER_CLAIM}" in message
    assert "CLM-1842" in message
    assert isinstance(excinfo.value, OpenSearchError), (
        "server.py answers 502 on OpenSearchError and 500 on anything else; a "
        "truncated read is 'the store could not answer completely', not a bug "
        "in this service"
    )


def test_the_step_one_refusal_says_which_traces_get_dropped():
    """A terms aggregation drops the SMALLEST buckets.

    That is the whole severity of the finding rather than a detail: in a
    supervised pair the smaller trace is the approval trace, which holds the
    second execute decision — the span whose absence inverted
    `stopped_when_refused` from 0.0 to 1.0 during Task 4's fix round. An
    operator who reads "some traces were dropped" and not "the ones that get
    dropped are the ones that make a trajectory look compliant" will widen the
    cap and move on.
    """
    client = RecordingClient([_trace_bucket_response("aaaa", sum_other_doc_count=1)])
    with pytest.raises(TrajectoryTruncated) as excinfo:
        client.trace_ids_for_claim("CLM-1842", SINCE_MS)
    message = str(excinfo.value).lower()
    assert "smallest" in message
    assert "compliant" in message


def test_step_one_refuses_an_approximate_bucket_count():
    """`doc_count_error_upper_bound` — the multi-shard half.

    Measured 0 on this single-shard index, and checked anyway: the setting
    that makes it non-zero is a chart value (`opensearch.replicas`, shard
    count) that nobody would connect to a policy score. When it is non-zero the
    *ordering* that decides which traces survive the cut is approximate, so
    `sum_other_doc_count == 0` stops being evidence that nothing was lost.
    """
    client = RecordingClient([_trace_bucket_response("aaaa", "bbbb", error_bound=3)])
    with pytest.raises(TrajectoryTruncated) as excinfo:
        client.trace_ids_for_claim("CLM-1842", SINCE_MS)
    assert "doc_count_error_upper_bound" in str(excinfo.value)


def test_an_untruncated_read_is_not_refused():
    """The positive control, without which every test above passes against a
    method that raises unconditionally.

    This is the same vacuity check `tests/unit/test_mutation_protocol.py`
    carries for `mutate-guard.sh`: two refusal tests and no control would be
    satisfied by a function that refuses everything.
    """
    client = RecordingClient([_trace_bucket_response("aaaa", "bbbb")])
    assert client.trace_ids_for_claim("CLM-1842", SINCE_MS) == ["aaaa", "bbbb"]


def test_a_full_bucket_list_with_nothing_dropped_is_still_fine():
    """Exactly `MAX_TRACES_PER_CLAIM` buckets and nothing beyond them.

    The boundary that a `len(buckets) >= MAX_TRACES_PER_CLAIM` implementation
    would get wrong: a claim that touched exactly 20 traces is completely read,
    and refusing it would turn a correct answer into a 502. The question is
    whether OpenSearch had to drop anything, which is what
    `sum_other_doc_count` answers and what a length comparison only guesses at.
    """
    ids = [f"trace{n:02d}" for n in range(MAX_TRACES_PER_CLAIM)]
    client = RecordingClient([_trace_bucket_response(*ids)])
    assert client.trace_ids_for_claim("CLM-1842", SINCE_MS) == ids


def test_the_cap_clears_what_one_gate_run_puts_in_the_window():
    """The cap has a measured floor, and this is the half a test can hold.

    Deliberately **not** a test that `MAX_TRACES_PER_CLAIM == 20`. Restating a
    constant in a test protects nothing — it reddens for a correct tuning
    change and catches no defect, and the guard that matters (refusing a
    truncated read) is now independent of the cap's value, which is why raising
    the cap no longer re-opens the H1 defect. Mutating the constant to 2000
    leaves this file green, and that is the intended property rather than a
    gap.

    What is worth pinning is the other direction. Measured 2026-09-14: one
    `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` contributes **8** distinct
    `CLM-1842` traces, all inside `DEFAULT_WINDOW_S`. A cap below that turns
    every score request issued during or just after a gate run into a 502 —
    correct, honest, and useless. `DEFAULT_WINDOW_S` is asserted alongside it
    because the figure is "8 per 15 minutes": shrink the window and the same
    cap buys more headroom, so the two cannot be read apart.
    """
    assert MAX_TRACES_PER_CLAIM >= 8, (
        f"MAX_TRACES_PER_CLAIM is {MAX_TRACES_PER_CLAIM}, below the 8 traces "
        "one measured gate run puts inside DEFAULT_WINDOW_S -- every score "
        "request during a gate would be refused as truncated. Re-measure "
        "before lowering it; the figure is recorded on the constant."
    )
    assert DEFAULT_WINDOW_S == 900, (
        "the 8-traces-per-run floor above was measured against a 15-minute "
        f"window and DEFAULT_WINDOW_S is now {DEFAULT_WINDOW_S}s. Widening the "
        "window admits more runs and needs a re-measurement, not a bigger cap "
        "chosen by feel."
    )


def test_the_cap_the_query_asks_for_is_the_published_constant():
    """The constant and the query cannot drift apart.

    `MAX_TRACES_PER_CLAIM` now carries a measured rationale; a query with a
    hard-coded 20 beside it would leave that rationale describing nothing.
    """
    client = RecordingClient([_trace_bucket_response("aaaa")])
    client.trace_ids_for_claim("CLM-1842", SINCE_MS)
    terms = client.bodies[0]["aggs"]["traces"]["terms"]
    assert terms["size"] == MAX_TRACES_PER_CLAIM
    assert terms["field"] == "traceId.keyword"


class _Response:
    """The two methods `urllib.request.urlopen`'s context manager needs."""

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_an_opensearch_error_body_is_raised_and_not_read_as_no_hits(monkeypatch):
    """A 404 `index_not_found_exception` is a well-formed JSON body with an
    `error` key and no `hits`. Read as "no hits" it would score every claim
    compliant-by-absence, which is "I could not look" recorded as "there was
    nothing to find".

    Patched at `urlopen` rather than at `search`, deliberately: the error
    check lives *inside* `search`, so a fake that replaces `search` would test
    the fake. That is the mistake this test was written with and is the reason
    it is written this way now.
    """
    monkeypatch.setattr(
        "scorer.opensearch.urllib.request.urlopen",
        lambda request, timeout=None: _Response(
            {"error": {"type": "index_not_found_exception", "reason": "no such index"}}
        ),
    )
    with pytest.raises(OpenSearchError) as excinfo:
        OpenSearchClient("http://fake.invalid:9200").trace_ids_for_claim(
            "CLM-1842", SINCE_MS
        )
    assert "index_not_found_exception" in str(excinfo.value)


def test_an_unreachable_store_is_an_opensearch_error_not_a_bare_oserror(monkeypatch):
    """`server.py` answers 502 on `OpenSearchError` and 500 on anything else.
    A raw `URLError` escaping here would be reported as a bug in this service
    rather than as an unreachable backend."""

    def _refuse(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("scorer.opensearch.urllib.request.urlopen", _refuse)
    with pytest.raises(OpenSearchError):
        OpenSearchClient("http://fake.invalid:9200").trace_ids_for_claim(
            "CLM-1842", SINCE_MS
        )


def test_a_non_json_body_is_an_opensearch_error(monkeypatch):
    class _Garbage(_Response):
        def __init__(self):
            self._payload = b"<html>502 Bad Gateway</html>"

    monkeypatch.setattr(
        "scorer.opensearch.urllib.request.urlopen",
        lambda request, timeout=None: _Garbage(),
    )
    with pytest.raises(OpenSearchError):
        OpenSearchClient("http://fake.invalid:9200").trace_ids_for_claim(
            "CLM-1842", SINCE_MS
        )


def test_the_search_url_names_the_index_this_task_measured(monkeypatch):
    seen = {}

    def _capture(request, timeout=None):
        seen["url"] = request.full_url
        seen["method"] = request.method
        seen["body"] = json.loads(request.data)
        return _Response(_trace_bucket_response("aaaa"))

    monkeypatch.setattr("scorer.opensearch.urllib.request.urlopen", _capture)
    OpenSearchClient("http://fake.invalid:9200").trace_ids_for_claim("CLM-1842", SINCE_MS)
    assert seen["url"] == f"http://fake.invalid:9200/{TRACE_INDEX}/_search"
    assert seen["method"] == "POST"
    assert seen["body"]["size"] == 0


def test_the_trajectory_is_built_from_both_steps():
    client = RecordingClient(
        [
            _trace_bucket_response(fixtures.REFUSAL_TRACE, fixtures.APPROVAL_TRACE),
            _hits_response(
                [
                    fixtures.root(0.0),
                    fixtures.decision(2.0, "propose_payment", "permit", amount=6200.0),
                    fixtures.decision(
                        4.0, "execute_payment", "deny", amount=6200.0, limit=6000.0
                    ),
                ]
            ),
        ]
    )
    trajectory = client.trajectory_for_claim("CLM-1842", SINCE_MS)
    assert trajectory.claim_id == "CLM-1842"
    assert len(trajectory.spans) == 3
    assert [d.tool for d in trajectory.decisions()] == [
        "propose_payment",
        "execute_payment",
    ]


def test_the_index_name_is_the_one_task_1_measured():
    assert TRACE_INDEX == "ss4o_traces-default-namespace"


def test_the_base_url_comes_from_the_environment_when_set(monkeypatch):
    monkeypatch.setenv("NORTHSTAR_OPENSEARCH_URL", "http://elsewhere:9200/")
    assert OpenSearchClient().base_url == "http://elsewhere:9200"
    assert OpenSearchClient("http://explicit:9200").base_url == "http://explicit:9200"
