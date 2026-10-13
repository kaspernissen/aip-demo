"""Calls against the live `kind-northstar` cluster, shared by the e2e tests and
the stage driver (`scripts/demo`).

**Why this module exists, and why it lives in `tests/harness/`.** The five-minute
demo flow (`docs/superpowers/specs/2026-10-12-northstar-m7-bounded-design.md`
section 7) is a thin driver over things this repo's tests already prove: submit
a claim to claims-agent over A2A, mint a SPIFFE JWT-SVID and speak MCP to the
gateway, read the evidence for a claim out of OpenSearch. The spec's rule is
**no new mechanism** -- "the stage path and the tested path are the same code" --
so those helpers moved here from the test files that used to own them, and both
the tests and the driver import them. A second copy in the driver would be a
copy that can quietly stop matching what the tests assert.

It is `tests/harness/` rather than a new top-level package for two reasons. The
helpers it replaces were already imported by name across a dozen test modules
and the harness is where `jaeger.py`, `kube.py` and `contract.py` (all of which
the driver also uses) already live, so there is one import root to learn. And a
new top-level directory would sit outside `GATE_PATHS`
(`tests/unit/test_telemetry_contract_counts.py`), so a change to the code the
stage runs would not demote the gate row; `tests/` is inside it.

**What is here is plain functions, not pytest fixtures.** The fixtures in
`test_caller_identity.py` (`gateway_url`, `mint_svid`) are thin wrappers over
`gateway_port_forward` and `mint_svid` below, so their behaviour is unchanged.

**Failure style.** Helpers `assert` with a message, as the test files did. The
driver catches `AssertionError` and prints it as a failure, so a helper written
for a test reads sensibly on stage too.

**Credential hygiene.** Tokens are minted at runtime and returned to the caller
only. Nothing here prints or logs one; `claims()` decodes a payload without
verifying it so a message can name the identity that was presented.
"""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import json
import re
import subprocess
import sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import requests

KUBE_CONTEXT = "kind-northstar"
GATEWAY_NAMESPACE = "northstar-platform"
SPIRE_SERVER_POD = "spire-server-0"

# The audience settlement-agent's real SVIDs are minted for, and the single
# entry in the gateway's `jwtAuth.audiences` list.
GATEWAY_AUDIENCE = "spiffe://northstar.local/ns/northstar-platform/sa/agentgateway"
CALLER_SPIFFE_ID = "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
MCP_PROTOCOL_VERSION = "2025-06-18"

# agentgateway v1.5.0 answers a jwtAuth rejection with this status and a
# `text/plain` body, measured -- see `Outcome.refused`.
UNAUTHORIZED = 401

CLAIMS_AGENT_URL_DEFAULT = "http://localhost:19001"
# A settlement is three sequential model turns plus MCP round trips; 180s
# matches every other call site in this suite that drives one.
SETTLEMENT_TIMEOUT_S = 180

OBSERVABILITY_NAMESPACE = "observability"
OPENSEARCH_SERVICE = "opensearch-cluster-master"
TRACE_INDEX = "ss4o_traces-default-namespace"
DECISION_SPAN = "policy.decision"
PDS_SERVICE = "policy-decision-service"
PAYMENTS_API = "payments-api"
EXECUTE_TOOL = "execute_payment"

ATTR_DECISION = "policy.decision"
ATTR_POLICY_TOOL = "policy.tool.name"
ATTR_POLICY_RULE = "policy.rule"
ATTR_POLICY_CALLER = "policy.caller.spiffe_id"
ATTR_POLICY_LIMIT = "policy.limit.amount"
ATTR_POLICY_AMOUNT = "policy.request.amount"
ATTR_AUTONOMY = "policy.autonomy.level"
ATTR_FLAG_VERSION = "feature_flag.version"
ATTR_CLAIM_ID = "northstar.claim.id"
ATTR_REVIEW_REVIEWER = "review.reviewer.id"
ATTR_REVIEW_DECISION = "review.decision"
REVIEW_DECISION_SPAN = "review.decision"

# One page is enough for a claim's whole story; `spans_in_traces` asserts it.
SPAN_PAGE_SIZE = 1000
# Clock-skew allowance on a window's lower bound. Small on purpose.
WINDOW_SKEW_S = 30.0


# --- kubectl -----------------------------------------------------------------


def kubectl(
    *args: str, stdin: str | None = None, timeout: int = 90
) -> subprocess.CompletedProcess:
    """`kubectl` against the demo cluster, always with a timeout and never any
    other context."""
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# --- claims-agent (A2A) ------------------------------------------------------


def claims_agent_alive(url: str = CLAIMS_AGENT_URL_DEFAULT) -> bool:
    """Liveness by HTTP request, never by `lsof`: a dead `kubectl port-forward`
    keeps its listening socket (this project has had one false healthy report
    from exactly that)."""
    try:
        requests.get(f"{url}/.well-known/agent-card.json", timeout=3)
    except requests.RequestException:
        return False
    return True


@contextlib.contextmanager
def claims_agent_route(url: str = CLAIMS_AGENT_URL_DEFAULT):
    """claims-agent reachable at `url`: reuse a live forward, else own one.

    Anything this starts is terminated on exit; a forward that was already up
    is never touched.
    """
    if claims_agent_alive(url):
        yield url
        return
    port = urllib.parse.urlsplit(url).port
    assert port, f"{url!r} names no port, so no forward can be established for it"
    proc = subprocess.Popen(
        [
            "kubectl", "--context", KUBE_CONTEXT, "-n", "northstar-agents",
            "port-forward", "svc/claims-agent", f"{port}:9001",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not claims_agent_alive(url):
            time.sleep(1)
        assert claims_agent_alive(url), (
            f"could not reach claims-agent at {url} within 30s after starting a "
            "port-forward -- is the kind-northstar cluster up?"
        )
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def submit_claim(
    claim_id: str,
    amount: float,
    *,
    claims_agent_url: str = CLAIMS_AGENT_URL_DEFAULT,
    timeout: float = SETTLEMENT_TIMEOUT_S,
) -> dict:
    """One settlement, submitted to claims-agent over A2A, no browser.

    The caller originates the request, so it mints its own trace id and sends it
    as a `traceparent` rather than searching for a plausible trace afterwards.
    Returns `{"trace_id", "reply"}`; `reply` is the claim outcome claims-agent
    hands back (`status`, `paymentId`, `narrative`, ...).
    """
    trace_id = uuid.uuid4().hex
    traceparent = f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01"
    response = httpx.post(
        f"{claims_agent_url}/",
        json={
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": uuid.uuid4().hex,
                    "parts": [
                        {
                            "kind": "text",
                            "text": json.dumps(
                                {
                                    "claimId": claim_id,
                                    "policyNumber": "POL-77120",
                                    "description": "Rear-end collision at a junction",
                                    "estimatedAmount": amount,
                                }
                            ),
                        }
                    ],
                }
            },
        },
        headers={"traceparent": traceparent},
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    assert "error" not in body, (
        f"claims-agent refused claim {claim_id!r}: {response.text[:500]}"
    )
    reply = json.loads(body["result"]["parts"][0]["text"])
    return {"trace_id": trace_id, "reply": reply}


# --- the gateway (MCP) -------------------------------------------------------


def claims(token: str) -> dict:
    """The token's payload, decoded and **not** verified.

    Verification is the gateway's job; this exists only so a message can name
    the identity that was presented without printing a usable credential.
    """
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


# The settlement-agent's deny parser, loaded from its file (test-only: the same
# borrowing tests/test_policy_deny_contract.py does), so the agent and the e2e
# tests share one parser rather than two copies that can drift.
REPO_ROOT = Path(__file__).resolve().parents[2]
_DENIAL_TEXT = REPO_ROOT / "agents" / "settlement-agent" / "policy_denial_text.py"
_spec = importlib.util.spec_from_file_location("policy_denial_text", _DENIAL_TEXT)
_denial_text = importlib.util.module_from_spec(_spec)
# dataclasses resolves the module by name while the class body runs.
sys.modules.setdefault("policy_denial_text", _denial_text)
_spec.loader.exec_module(_denial_text)
parse_deny = _denial_text.parse_deny


@dataclass
class Outcome:
    """The normalised result of one direct MCP call through the gateway.

    `refused` means *the gateway rejected the caller's identity* -- not merely
    "something non-2xx happened". A bare `tools/call` with a perfectly valid
    token also returns a non-2xx status (measured: `400 mcp: session header is
    required for non-initialize requests`), so a `refused = not ok` definition
    would report the accepted path as refused too and a suite built on it would
    go green on a gateway with its `jwtAuth` policy deleted.

    So `refused` is true for exactly two shapes: **HTTP 401 with a `text/plain`
    body** (what agentgateway v1.6.0 does in every rejection case measured), and
    a JSON-RPC error object riding on a non-2xx status (handled in case a later
    version moves the rejection into the MCP layer, so the property keeps being
    asserted instead of silently inverting).
    """

    http_status: int
    body: str
    stage: str
    jsonrpc_error: dict | None = None
    tool_result: dict | None = None
    claims: dict = field(default_factory=dict)

    @property
    def denial(self):
        """The PDS's policy denial, if this tools/call result is one.

        agentgateway v1.6.0 returns it as HTTP 200 with an `isError` tool result
        whose text ends in the `[policy-denial ...]` tag."""
        result = self.tool_result or {}
        if not result.get("isError"):
            return None
        text = " ".join(c.get("text", "") for c in result.get("content") or [])
        return parse_deny(text)

    @property
    def refused(self) -> bool:
        if self.http_status == UNAUTHORIZED:
            return True
        return self.jsonrpc_error is not None and self.http_status >= 400

    def __repr__(self) -> str:  # noqa: D105 -- assertion messages only
        return (
            f"<call {self.stage}: HTTP {self.http_status} body={self.body[:200]!r} "
            f"jsonrpc_error={self.jsonrpc_error!r} claims={self.claims!r}>"
        )


def post(url: str, payload: dict, headers: dict) -> httpx.Response:
    return httpx.post(
        f"{url}/mcp",
        json=payload,
        headers={
            "content-type": "application/json",
            # Streamable HTTP requires both; the gateway answers `initialize`
            # as an SSE event stream, which `json_body` unwraps.
            "accept": "application/json, text/event-stream",
            **headers,
        },
        timeout=30,
    )


def json_body(response: httpx.Response) -> dict | None:
    """The JSON-RPC message out of either an `application/json` or an SSE body.

    agentgateway returns MCP responses as `text/event-stream` (measured), so a
    bare `response.json()` raises on the accepted path. Returns None when the
    body is not JSON-RPC at all, which is what a `text/plain` refusal is.
    """
    text = response.text
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[len("data:") :].strip())
            except json.JSONDecodeError:
                return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _token_claims(headers: dict) -> dict:
    authorization = headers.get("authorization") or headers.get("Authorization")
    if authorization and authorization.lower().startswith("bearer "):
        try:
            return claims(authorization.split(" ", 1)[1])
        except Exception:  # noqa: BLE001 -- a deliberately malformed token
            return {"<undecodable>": True}
    return {}


def call_tool_directly(
    gateway_url: str, tool: str, arguments: dict, headers: dict
) -> Outcome:
    """Speak MCP to the gateway's `/mcp` and normalise the outcome.

    Deliberately performs the **full** streamable-HTTP sequence -- `initialize`,
    then `tools/call` with the session id the gateway hands back -- rather than
    firing a bare `tools/call`. A bare `tools/call` cannot succeed at all (no
    session header), so a test built on it could never distinguish "refused"
    from "accepted". And `initialize` passes through the same route and the
    same `jwtAuth` policy (measured: it 401s identically without a token), so an
    identity that is refused never gets a session, and an identity that is
    accepted goes on to really execute the tool: `refused` then means "could not
    open an MCP session through the gateway", and its negation is witnessed by a
    real tool result rather than by the absence of an error.
    """
    token_claims = _token_claims(headers)
    init = post(
        gateway_url,
        {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "identity-probe", "version": "0"},
            },
        },
        headers,
    )
    init_body = json_body(init)
    if init.status_code != 200 or init_body is None or "result" not in init_body:
        return Outcome(
            http_status=init.status_code,
            body=init.text,
            stage="initialize",
            jsonrpc_error=(init_body or {}).get("error"),
            claims=token_claims,
        )

    session_id = init.headers.get("mcp-session-id")
    assert session_id, (
        "the gateway accepted `initialize` but returned no `mcp-session-id` "
        f"header, so this probe cannot continue: headers={dict(init.headers)!r}"
    )

    called = post(
        gateway_url,
        {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        {**headers, "mcp-session-id": session_id},
    )
    body = json_body(called)
    return Outcome(
        http_status=called.status_code,
        body=called.text,
        stage="tools/call",
        jsonrpc_error=(body or {}).get("error"),
        tool_result=(body or {}).get("result"),
        claims=token_claims,
    )


def list_tools_directly(gateway_url: str, headers: dict) -> tuple[Outcome, list[str]]:
    """`initialize` then `tools/list` through the gateway.

    Returns the normalised outcome and the tool names it listed (empty when the
    gateway refused the identity at `initialize`, which is the point of calling
    this without a token). Same two-step shape as `call_tool_directly`, for the
    same reason: a bare `tools/list` has no session and so cannot tell "refused"
    from "accepted".
    """
    token_claims = _token_claims(headers)
    init = post(
        gateway_url,
        {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "identity-probe", "version": "0"},
            },
        },
        headers,
    )
    init_body = json_body(init)
    if init.status_code != 200 or init_body is None or "result" not in init_body:
        return (
            Outcome(
                http_status=init.status_code,
                body=init.text,
                stage="initialize",
                jsonrpc_error=(init_body or {}).get("error"),
                claims=token_claims,
            ),
            [],
        )
    session_id = init.headers.get("mcp-session-id")
    assert session_id, "the gateway accepted `initialize` but returned no session id"
    session_headers = {**headers, "mcp-session-id": session_id}
    # The MCP handshake's own acknowledgement; harmless if the gateway ignores it.
    post(
        gateway_url,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        session_headers,
    )
    listed = post(
        gateway_url,
        {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "tools/list"},
        session_headers,
    )
    body = json_body(listed)
    names = [t["name"] for t in ((body or {}).get("result") or {}).get("tools", [])]
    return (
        Outcome(
            http_status=listed.status_code,
            body=listed.text,
            stage="tools/list",
            jsonrpc_error=(body or {}).get("error"),
            tool_result=(body or {}).get("result"),
            claims=token_claims,
        ),
        names,
    )


@contextlib.contextmanager
def gateway_port_forward():
    """A port-forward to `svc/agentgateway`, owned and cleaned up by the caller.

    `:3000` (no local port) makes `kubectl` choose a free ephemeral port and
    print it, so this cannot shadow one of the four repo-managed forwards even
    by accident. The chosen port is parsed out of kubectl's own "Forwarding
    from 127.0.0.1:<port> -> 3000" line rather than guessed.
    """
    proc = subprocess.Popen(
        [
            "kubectl", "--context", KUBE_CONTEXT, "-n", GATEWAY_NAMESPACE,
            "port-forward", "svc/agentgateway", ":3000",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    port = None
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            match = re.search(r"Forwarding from 127\.0\.0\.1:(\d+) -> 3000", line)
            if match:
                port = int(match.group(1))
                break
        assert port, (
            "could not establish a port-forward to svc/agentgateway in "
            f"{GATEWAY_NAMESPACE} -- is the kind-northstar cluster up and the "
            "gateway deployed? (this fails rather than skipping when the "
            "cluster is not there)"
        )
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def mint_svid(audience: str, subject: str = CALLER_SPIFFE_ID) -> str:
    """Mint a real JWT-SVID for any (subject, audience) pair, via SPIRE itself.

    `spire-server jwt mint` rather than the Workload API on purpose: the
    Workload API only ever answers with the identity of whoever is calling it,
    so it *cannot* produce a wrong-audience-but-otherwise-genuine token. Minting
    server-side keeps everything else about the token real -- same signing key,
    same `kid`, same `iss`, same subject SPIRE would attest for the agent.
    """
    result = kubectl(
        "-n", GATEWAY_NAMESPACE, "exec", SPIRE_SERVER_POD, "-c", "spire-server",
        "--", "/opt/spire/bin/spire-server", "jwt", "mint",
        "-spiffeID", subject, "-audience", audience, "-output", "json",
        timeout=60,
    )
    assert result.returncode == 0, (
        "`spire-server jwt mint` failed, so the SVID this step needs could not "
        f"be built:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # Deliberately not logged: the value is a live bearer credential.
    return json.loads(result.stdout)["svid"]["token"]


# --- OpenSearch --------------------------------------------------------------


def opensearch(path: str, body: dict | None = None) -> dict:
    """One OpenSearch request, made from inside the cluster.

    `kubectl exec ... curl` rather than a port-forward on purpose: this should
    not add another thing that can leave a socket behind, and the repo's
    standing rule is to own every forward you start.
    """
    args = [
        "-n", OBSERVABILITY_NAMESPACE, "exec", "-i", f"svc/{OPENSEARCH_SERVICE}",
        "--", "curl", "-s", "-X", "GET" if body is None else "POST",
        f"http://localhost:9200{path}",
    ]
    stdin = None
    if body is not None:
        args += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
        stdin = json.dumps(body)
    result = kubectl(*args, stdin=stdin)
    assert result.returncode == 0, (
        f"could not reach OpenSearch at {path!r}:\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover -- diagnostic path
        raise AssertionError(
            f"OpenSearch returned a non-JSON body for {path!r}: "
            f"{result.stdout[:500]!r}"
        ) from exc


def search(body: dict) -> dict:
    """One `_search` against the trace index, with a missing index as a failure.

    `opensearch` asserts that `curl` ran; it does not assert that OpenSearch
    liked the request. A 404 `index_not_found_exception` comes back as a
    perfectly well-formed JSON body with an `error` key and no `hits`, so
    without this it would surface as a `KeyError` rather than as a named red.
    """
    result = opensearch(f"/{TRACE_INDEX}/_search", body)
    assert "error" not in result, (
        f"OpenSearch refused the evidence query against {TRACE_INDEX!r}: "
        f"{json.dumps(result.get('error'))[:600]}\n"
        "If this says `index_not_found_exception`, the index this reads does "
        "not exist -- a stage-4 cluster with no trace ingest. Check the "
        "`opensearch/trace` exporter in the deployed collector config and the "
        "collector's own log for `Exporting failed`."
    )
    return result


def window_filter(window_start_ms: int) -> dict:
    """The lower bound every evidence query carries.

    Epoch milliseconds rather than an ISO string: `startTime` is mapped
    `{"type": "date"}` with no explicit format, so both parse, and millis
    cannot be misread as a local time by anything. The bound is load-bearing,
    not hygiene: a fixed claim id accumulates one set of traces per run forever.
    """
    return {"range": {"startTime": {"gte": window_start_ms}}}


def window_start_ms_now() -> int:
    return int((time.time() - WINDOW_SKEW_S) * 1000)


def trace_ids_for_claim(claim_id: str, window_start_ms: int) -> list[str]:
    """Step 1 of the join: claim id -> the trace ids that claim touched.

    **GAP 4 -- DO NOT COLLAPSE THE TWO STEPS INTO ONE FILTER.** A single
    `attributes.northstar.claim.id = X` filter returns a strict and silently
    incomplete subset of the claim's story. `northstar.claim.id` travels as W3C
    baggage and is copied onto spans only inside the processes that hold it
    (customer-ui, claims-agent, settlement-agent). It does not cross the MCP
    boundary, so payments-mcp and payments-api spans never carry it, and the
    policy decision service is reached over the gateway's guardrail hop, which
    carries no baggage either, so `policy.decision` spans never carry it. **The
    trace id is the join.** See `tests/e2e/test_evidence_view.py`, which asserts
    this so nobody can collapse the join.

    Returns more than one trace id for a supervised claim (the refusal trace and
    the human approval's own trace); oldest first.
    """
    buckets = search(
        {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {f"attributes.{ATTR_CLAIM_ID}.keyword": claim_id}},
                        window_filter(window_start_ms),
                    ]
                }
            },
            "aggs": {
                "traces": {
                    "terms": {"field": "traceId.keyword", "size": 20},
                    "aggs": {"first": {"min": {"field": "startTime"}}},
                }
            },
        }
    )["aggregations"]["traces"]["buckets"]
    return [b["key"] for b in sorted(buckets, key=lambda b: b["first"]["value"])]


def spans_in_traces(trace_ids: list[str], window_start_ms: int) -> list[dict]:
    """Step 2 of the join: every span in those traces, claim id or not.

    Filtering on the claim id here as well -- which reads like a harmless
    belt-and-braces narrowing -- would drop the decision spans and the money
    hops again.
    """
    if not trace_ids:
        return []
    result = search(
        {
            "size": SPAN_PAGE_SIZE,
            "query": {
                "bool": {
                    "filter": [
                        {"terms": {"traceId": trace_ids}},
                        window_filter(window_start_ms),
                    ]
                }
            },
            "sort": [{"startTime": "asc"}],
            "track_total_hits": True,
        }
    )["hits"]
    hits = result["hits"]
    # `track_total_hits` is set because OpenSearch caps the reported total at
    # 10,000 by default, which would make this comparison itself lie.
    total = (result.get("total") or {}).get("value", len(hits))
    assert len(hits) == total, (
        f"the trace-id join returned {len(hits)} of {total} matching documents "
        f"-- `size` is {SPAN_PAGE_SIZE} and these traces no longer fit in one "
        "page, so the evidence row is built from a truncated span set. Raise "
        "SPAN_PAGE_SIZE or paginate; do not read the short list."
    )
    return [hit["_source"] for hit in hits]


def evidence_for(claim_id: str, window_start_ms: int) -> dict:
    """One row of the evidence view, read out of OpenSearch exactly as the
    saved dashboard's panels read it.

    Each key is something a panel of `deploy/observability/dashboards/
    evidence.ndjson` puts on screen:

      `trace_ids`                  panel 1, "Claims in this window"
      `policy_decision`/`rule`/    panel 2, "What the platform decided"
        `limit`/`requested`/
        `autonomy_level`/
        `contract_version`/
        `executing_identity`/
        `executing_rule`
      `reviewer`/`review_decision` panel 3, "Was a human in the loop?"
      `money_hops`                 panel 4, the ledger hops

    `contract_version` is expected to read `None` today (flagd's response
    carries no version against this deployment) -- the honest value, not a gap.
    """
    trace_ids = trace_ids_for_claim(claim_id, window_start_ms)
    spans = spans_in_traces(trace_ids, window_start_ms)

    def attrs(span: dict) -> dict:
        # ss4o writes attribute keys FLAT and dotted into `_source` while
        # OpenSearch expands them into objects for the mapping -- so the Python
        # lookup is the dotted string and the query path is the dotted path.
        return span.get("attributes") or {}

    decisions = [
        span
        for span in spans
        if span.get("name") == DECISION_SPAN
        and attrs(span).get(ATTR_POLICY_TOOL) == EXECUTE_TOOL
    ]
    # The decision on the agent's OWN attempt to move the money: the first
    # `execute_payment` decision in the window.
    first_decision = decisions[0] if decisions else {}
    # Who was actually allowed to move the money: the last PERMITTED
    # `execute_payment` decision.
    permitted = [d for d in decisions if attrs(d).get(ATTR_DECISION) == "permit"]
    executor = permitted[-1] if permitted else {}

    reviews = [span for span in spans if span.get("name") == REVIEW_DECISION_SPAN]
    review = reviews[-1] if reviews else {}

    money = [
        span
        for span in spans
        if (span.get("resource") or {}).get("service.name") == PAYMENTS_API
    ]

    return {
        "claim_id": claim_id,
        "trace_ids": trace_ids,
        "policy_decision": attrs(first_decision).get(ATTR_DECISION),
        "rule": attrs(first_decision).get(ATTR_POLICY_RULE),
        "limit": attrs(first_decision).get(ATTR_POLICY_LIMIT),
        "requested": attrs(first_decision).get(ATTR_POLICY_AMOUNT),
        "autonomy_level": attrs(first_decision).get(ATTR_AUTONOMY),
        "contract_version": attrs(first_decision).get(ATTR_FLAG_VERSION),
        "reviewer": attrs(review).get(ATTR_REVIEW_REVIEWER),
        "review_decision": attrs(review).get(ATTR_REVIEW_DECISION),
        # Who asked, on the agent's own first attempt -- the identity the
        # gateway verified and the decision was made about. Distinct from
        # `executing_identity`, which exists only if something was permitted.
        "caller_identity": attrs(first_decision).get(ATTR_POLICY_CALLER),
        "executing_identity": attrs(executor).get(ATTR_POLICY_CALLER),
        "executing_rule": attrs(executor).get(ATTR_POLICY_RULE),
        "money_hops": [span["name"] for span in money],
        "decision_count": len(decisions),
        "span_count": len(spans),
    }
