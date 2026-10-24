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
new top-level directory would be one more place to look.

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
import os
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
# The customer-ui server, reached the way the browser tests reach it: the kind
# node's hostPort 8080 -> NodePort 30080 (deploy/kind/stage*.yaml). scripts/
# start-demo.sh runs no port-forward for it, and this needs none.
CUSTOMER_UI_URL_DEFAULT = os.environ.get("CUSTOMER_UI_URL", "http://localhost:8080")
# A settlement is three sequential model turns plus MCP round trips; 180s
# matches every other call site in this suite that drives one.
SETTLEMENT_TIMEOUT_S = 180

OBSERVABILITY_NAMESPACE = "observability"
OPENSEARCH_SERVICE = "opensearch-cluster-master"
TRACE_INDEX = "ss4o_traces-default-namespace"
DECISION_SPAN = "northstar.policy.decision"
PDS_SERVICE = "policy-decision-service"
PAYMENTS_API = "payments-api"
EXECUTE_TOOL = "execute_payment"

ATTR_DECISION = "northstar.policy.decision"
ATTR_POLICY_TOOL = "northstar.policy.tool.name"
ATTR_POLICY_RULE = "northstar.policy.rule"
ATTR_POLICY_CALLER = "northstar.policy.caller.spiffe_id"
ATTR_POLICY_LIMIT = "northstar.policy.limit.amount"
ATTR_POLICY_AMOUNT = "northstar.policy.request.amount"
ATTR_AUTONOMY = "northstar.policy.autonomy.level"
ATTR_FLAG_VERSION = "feature_flag.version"
ATTR_CLAIM_ID = "northstar.claim.id"
ATTR_REVIEW_REVIEWER = "northstar.review.reviewer.id"
ATTR_REVIEW_DECISION = "northstar.review.decision"
ATTR_REVIEW_WAIT_S = "northstar.review.wait.duration"  # double, seconds
REVIEW_DECISION_SPAN = "northstar.review.decision"

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


TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}


def _sse_results(response: httpx.Response):
    for line in response.iter_lines():
        if line.startswith("data:"):
            frame = json.loads(line[len("data:"):].strip())
            if "error" in frame:
                raise AssertionError(f"claims-agent stream error: {frame['error']}")
            yield frame["result"]


def _snapshot(event: dict, prev: dict) -> dict:
    status = event.get("status") or {}
    snap = dict(prev)
    snap["task_id"] = event.get("id") or event.get("taskId") or prev.get("task_id")
    snap["context_id"] = event.get("contextId") or prev.get("context_id")
    if status.get("state"):
        snap["state"] = status["state"]
    for part in reversed((status.get("message") or {}).get("parts") or []):
        if part.get("kind") == "text":
            try:
                snap["reply"] = json.loads(part["text"])
                break
            except (KeyError, json.JSONDecodeError):
                continue
    return snap


def _jsonrpc(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}


def push_config(*, customer_ui_url: str = CUSTOMER_UI_URL_DEFAULT) -> dict:
    """A push URL and a fresh token from the customer-ui server, as the browser
    gets them: `{"url", "token"}`."""
    response = httpx.post(f"{customer_ui_url}/claims/push-config", timeout=15)
    response.raise_for_status()
    body = response.json()
    assert body.get("url") and body.get("token"), f"push-config returned {sorted(body)}"
    return body


def submit_claim(
    claim_id: str,
    amount: float,
    *,
    claims_agent_url: str = CLAIMS_AGENT_URL_DEFAULT,
    customer_ui_url: str = CUSTOMER_UI_URL_DEFAULT,
    timeout: float = SETTLEMENT_TIMEOUT_S,
    traceparent: str | None = None,
) -> dict:
    """One claim, submitted to claims-agent over A2A `message/stream`, no browser.

    The caller originates the request, so it mints its own trace id and sends it
    as a `traceparent` (or is handed one) rather than searching for a plausible
    trace afterwards. The push config (`{url, token}`) comes from the customer-ui
    server's `POST /claims/push-config` and rides in
    `configuration.pushNotificationConfig`, as the browser's does.

    The stream is read **to its end**: claims-agent ends it at `input-required`
    or a terminal state, so there is nothing to close early. Returns
    `{"trace_id", "task_id", "context_id", "state", "reply", "push_token"}`.
    `reply` is the claim outcome claims-agent hands back (`status`, `paymentId`,
    `rule`, `limit`, `narrative`, ...). `push_token` is what `await_outcome`
    waits on. The token is a credential for one claim: never print it.

    A claim is never sent with a blocking `message/send`: one routed to a human
    would block for the whole wait.
    """
    if traceparent is None:
        traceparent = f"00-{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-01"
    trace_id = traceparent.split("-")[1]
    push = push_config(customer_ui_url=customer_ui_url)
    request = _jsonrpc("message/stream", {
        "message": {
            "role": "user", "messageId": uuid.uuid4().hex,
            "parts": [{"kind": "text", "text": json.dumps({
                "claimId": claim_id, "policyNumber": "POL-77120",
                "description": "Rear-end collision at a junction", "estimatedAmount": amount})}]},
        "configuration": {"pushNotificationConfig": {"url": push["url"], "token": push["token"]}},
    })
    snap: dict = {"trace_id": trace_id, "push_token": push["token"]}
    with httpx.stream(
        "POST", f"{claims_agent_url}/", json=request,
        headers={"traceparent": traceparent, "accept": "text/event-stream"},
        timeout=httpx.Timeout(timeout, read=timeout),
    ) as response:
        response.raise_for_status()
        if "text/event-stream" not in response.headers.get("content-type", ""):
            # A JSON-RPC error answers as a plain JSON body (e.g. -32602, push URL refused).
            body = response.read()
            try:
                error = json.loads(body).get("error")
            except (ValueError, AttributeError):
                error = None
            raise AssertionError(
                f"claims-agent refused claim {claim_id!r}: "
                f"{(error or {}).get('message') if error else body[:300]!r}")
        for event in _sse_results(response):
            snap = _snapshot(event, snap)
    assert snap.get("state"), f"claims-agent streamed no task state for claim {claim_id!r}"
    assert snap.get("reply") is not None, f"claims-agent streamed no reply for claim {claim_id!r}: {snap}"
    return snap


def await_outcome(
    push_token: str,
    *,
    timeout: float,
    drop_after: float | None = None,
    customer_ui_url: str = CUSTOMER_UI_URL_DEFAULT,
) -> dict:
    """Wait for the claim's terminal state on the customer-ui's event stream
    (`GET /claims/events`, token in the `x-claim-token` header), which relays the
    snapshots claims-agent pushes.

    Returns `{"state", "reply", "events"}` once a terminal snapshot arrives. If
    `drop_after` seconds pass with no event the stream is closed and the result
    carries `dropped=True`, the way a lost connection looks to the browser.
    A stream that ends with no terminal snapshot is an assertion failure.
    """
    snap: dict = {"events": 0}
    # `drop_after` is a quiet period: a read timeout of that length closes the
    # stream the way a lost connection would.
    read = drop_after if drop_after else timeout
    try:
        with httpx.stream(
            "GET", f"{customer_ui_url}/claims/events",
            headers={"x-claim-token": push_token, "accept": "text/event-stream"},
            timeout=httpx.Timeout(timeout, read=read),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                snap = {**_snapshot(json.loads(line[len("data:"):].strip()), snap),
                        "events": snap["events"] + 1}
                if snap.get("state") in TERMINAL_STATES:
                    return snap
    except httpx.ReadTimeout:
        if not drop_after:
            raise
        return {**snap, "dropped": True}
    raise AssertionError(f"the customer-ui event stream ended with no terminal snapshot: state {snap.get('state')!r}")


UPSTREAM_MODEL_FAILURE = re.compile(r"ModelAPIError|Connection error|Name or service not known", re.I)


def is_upstream_model_failure(run: dict) -> bool:
    """True when a claim's task FAILED before any payment existed because the
    settlement agent could not reach the model provider (a connection or DNS
    failure on the way to api.anthropic.com), which no test of ours is about.

    Deliberately narrow: a failed task with a payment id, or with any other
    error text, is a real failure and is never retried on this ground.
    """
    reply = run.get("reply") or {}
    return (
        run.get("state") == "failed"
        and not reply.get("paymentId")
        and bool(UPSTREAM_MODEL_FAILURE.search(json.dumps(reply)))
    )


def get_task(task_id: str, *, claims_agent_url: str = CLAIMS_AGENT_URL_DEFAULT) -> dict:
    """tasks/get: the task's state and the last reply it carried."""
    response = httpx.post(f"{claims_agent_url}/", json=_jsonrpc("tasks/get", {"id": task_id}), timeout=30)
    response.raise_for_status()
    body = response.json()
    assert "error" not in body, f"tasks/get {task_id}: {body['error']}"
    snap = _snapshot(body["result"], {})
    return {"state": snap.get("state"), "reply": snap.get("reply"), "context_id": snap.get("context_id")}


def release_wait(payment_id: str, reviewer_id: str = "demo-cleanup") -> dict:
    """Decide a claim that is waiting for a human (reject it) and return the
    ledger record as it was *while waiting*.

    Trace A no longer stays open while a claim waits: claims-agent's `execute()`
    returns at `input-required`, so trace A is finished at the hand-off and its
    spans have all landed. The decision and the resume run in trace B. This is
    now the way to clean up a parked claim (no payment left awaiting approval, no
    task left waiting), and the pre-decision ledger record it returns is the
    evidence for what the wait looked like.
    """
    before = get_payment(payment_id)
    decided = review(payment_id, "reject", reviewer_id)
    assert not decided.get("error"), (
        f"release_wait could not reject {payment_id}: {decided['error']} -- the claim is still "
        "waiting and its trace will never settle"
    )
    return before


@contextlib.contextmanager
def autonomy(level: str):
    """The flag at `level` for the duration, always restored to assisted.

    M8's resting state releases nothing; a test that needs the agent to settle
    a payment itself runs inside `with live.autonomy(contract.AUTONOMOUS):`.
    """
    from tests.harness import contract

    contract.set_targeted_branch(level)
    try:
        yield
    finally:
        contract.set_targeted_branch(contract.ASSISTED)


PAYMENTS_API_URL = os.environ.get("PAYMENTS_API_URL", "http://localhost:18080")


def get_payment(payment_id: str) -> dict:
    """Read a payment straight back from the ledger it lives in -- the ledger
    is the authority on status, amount and the refusal record, never the reply
    a settlement or an approval happened to carry."""
    response = httpx.get(f"{PAYMENTS_API_URL}/payments/{payment_id}", timeout=10)
    assert response.status_code == 200, (
        f"could not read payment {payment_id} back from the ledger at "
        f"{PAYMENTS_API_URL} (HTTP {response.status_code}): {response.text[:300]}"
    )
    return response.json()


def list_payments(status: str) -> list[dict]:
    """Every ledger payment in `status`, as payments-api lists them
    (`GET /payments?status=`)."""
    response = httpx.get(f"{PAYMENTS_API_URL}/payments", params={"status": status}, timeout=10)
    assert response.status_code == 200, (
        f"could not list {status!r} payments from the ledger at {PAYMENTS_API_URL} "
        f"(HTTP {response.status_code}): {response.text[:300]}"
    )
    return response.json()


REVIEWER_UI_URL = os.environ.get("REVIEWER_UI_URL", "http://localhost:8081")
REVIEWER_UI_SPIFFE_ID = (
    "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"
)


def review(payment_id: str, decision: str, reviewer_id: str) -> dict:
    """A reviewer's decision through reviewer-ui's own JSON route."""
    response = httpx.post(
        f"{REVIEWER_UI_URL}/api/review/{payment_id}",
        json={"reviewerId": reviewer_id, "decision": decision},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


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


GATEWAY_CONFIG = REPO_ROOT / "deploy" / "platform" / "agentgateway" / "config.yaml"


def ceiling_eur() -> int:
    """The gateway's hard ceiling on execute_payment, read from its config (the
    CEL rule's `amount <= N`), never retyped."""
    match = re.search(r"mcp\.tool\.arguments\.amount <= (\d+)", GATEWAY_CONFIG.read_text())
    assert match, f"no execute_payment ceiling rule found in {GATEWAY_CONFIG}"
    return int(match.group(1))


def new_trace_id() -> str:
    return uuid.uuid4().hex


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


CLAIM_LOOKBACK_S = 900


def claim_first_seen_ms(claim_id: str) -> int | None:
    """When the first span carrying this claim id started (epoch ms), looking
    back at most `CLAIM_LOOKBACK_S`; None if OpenSearch has none yet.

    A claim filed in the browser leaves no trace in the demo driver's state, so
    the driver asks the record itself where that claim's window begins. Bounded
    on the way back so a re-used claim id cannot drag the window to last week.
    """
    lower = int((time.time() - CLAIM_LOOKBACK_S) * 1000)
    result = search({
        "size": 0,
        "query": {"bool": {"filter": [
            {"term": {f"attributes.{ATTR_CLAIM_ID}.keyword": claim_id}},
            window_filter(lower),
        ]}},
        "aggs": {"first": {"min": {"field": "startTime"}}},
    })
    value = result["aggregations"]["first"]["value"]
    return int(value) if value is not None else None


def claim_ids_since(window_start_ms: int, filed_after_ms: int | None = None) -> list[str]:
    """Every claim FILED since the window opened, oldest first: how
    `demo evidence` finds the claims a human filed in the browser.

    "Filed" means the claim's first span ever -- the minimum `startTime` over
    all time, not just inside the window -- is at or after the window start.
    A claim carried over from before the run (left waiting by an earlier
    test, declined by `demo reset` inside this window) has later spans in the
    window and must not be reported as this run's.

    `filed_after_ms` is where "filed in this run" begins when that is later than
    the window: the window opens `WINDOW_SKEW_S` before the run so its own first
    spans are not cut, and a claim an earlier test file filed inside that margin
    would otherwise pass the first-span test. Defaults to the window start.
    """
    filed_after_ms = window_start_ms if filed_after_ms is None else filed_after_ms
    in_window = [
        b["key"]
        for b in search({
            "size": 0,
            "query": {"bool": {"filter": [
                {"exists": {"field": f"attributes.{ATTR_CLAIM_ID}.keyword"}},
                window_filter(window_start_ms),
            ]}},
            "aggs": {"claims": {
                "terms": {"field": f"attributes.{ATTR_CLAIM_ID}.keyword", "size": 100},
            }},
        })["aggregations"]["claims"]["buckets"]
    ]
    if not in_window:
        return []
    # No window filter here: the first span EVER is the question.
    buckets = search({
        "size": 0,
        "query": {"bool": {"filter": [
            {"terms": {f"attributes.{ATTR_CLAIM_ID}.keyword": in_window}},
        ]}},
        "aggs": {"claims": {
            "terms": {"field": f"attributes.{ATTR_CLAIM_ID}.keyword", "size": 100},
            "aggs": {"first": {"min": {"field": "startTime"}}},
        }},
    })["aggregations"]["claims"]["buckets"]
    filed = [b for b in buckets if b["first"]["value"] >= filed_after_ms]
    return [b["key"] for b in sorted(filed, key=lambda b: b["first"]["value"])]


def trace_ids_for_claim(claim_id: str, window_start_ms: int) -> list[str]:
    """Step 1 of the join: claim id -> the trace ids that claim touched.

    **GAP 4 -- DO NOT COLLAPSE THE TWO STEPS INTO ONE FILTER.** A single
    `attributes.northstar.claim.id = X` filter returns a strict and silently
    incomplete subset of the claim's story. `northstar.claim.id` travels as W3C
    baggage and is copied onto spans only inside the processes that hold it
    (customer-ui, claims-agent, settlement-agent). It does not cross the MCP
    boundary, so payments-mcp and payments-api spans never carry it, and the
    policy decision service is reached over the gateway's guardrail hop, which
    carries no baggage either, so `northstar.policy.decision` spans never carry it. **The
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
    # Paginated with `search_after`, because a claim that waited for a human has a
    # long trace: a2a-sdk adds a polling span every 0.5 s for as long as it waits
    # (docs/telemetry-contract.md, M8), so ten minutes is over a thousand spans.
    # Sorted by (startTime, spanId) so the pages neither overlap nor skip.
    query = {
        "bool": {
            "filter": [
                {"terms": {"traceId": trace_ids}},
                window_filter(window_start_ms),
            ]
        }
    }
    sort = [{"startTime": "asc"}, {"spanId.keyword": "asc"}]
    collected: list[dict] = []
    after = None
    while True:
        body = {"size": SPAN_PAGE_SIZE, "query": query, "sort": sort, "track_total_hits": True}
        if after is not None:
            body["search_after"] = after
        result = search(body)["hits"]
        hits = result["hits"]
        collected += [hit["_source"] for hit in hits]
        # `track_total_hits` is set because OpenSearch caps the reported total at
        # 10,000 by default, which would make the comparison below itself lie.
        total = (result.get("total") or {}).get("value", len(collected))
        if len(hits) < SPAN_PAGE_SIZE or len(collected) >= total:
            break
        after = hits[-1]["sort"]
    assert len(collected) == total, (
        f"the trace-id join returned {len(collected)} of {total} matching documents "
        "after paginating, so the evidence row would be built from a truncated span "
        "set; do not read the short list."
    )
    return collected


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
      `approved_by`/`human_wait_s` who approved the release, and how long the
                                   human took (None when no human approved)
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
    # The review that released the money: who approved, and how long the human
    # took (the span's own `northstar.review.wait.duration`, in seconds, read off the ledger's
    # awaitingSince). Both None when no human approved.
    # A resume-only decision (`northstar.review.resume_only`: the ledger already held the
    # decision and the call only told the customer again) carries no wait duration
    # and is not the approval.
    approving = [r for r in reviews
                 if attrs(r).get(ATTR_REVIEW_DECISION) == "approve"
                 and attrs(r).get(ATTR_REVIEW_WAIT_S) is not None]
    approval = approving[-1] if approving else {}
    wait_s = attrs(approval).get(ATTR_REVIEW_WAIT_S)

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
        "approved_by": attrs(approval).get(ATTR_REVIEW_REVIEWER),
        "human_wait_s": wait_s,
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


# --- Prometheus ----------------------------------------------------------------

OBSERVABILITY_NAMESPACE = "observability"
PROMETHEUS_SERVICE = "prometheus"


@contextlib.contextmanager
def prometheus_port_forward():
    """An ephemeral port-forward to `svc/prometheus`; yields its base URL.

    `:9090` with no local port makes kubectl choose a free one and print it, so
    this cannot shadow `scripts/start-demo.sh`'s own 9090 forward: a stray
    forward silently shadowing a host port has cost this project a measurement
    before.
    """
    proc = subprocess.Popen(
        ["kubectl", "--context", KUBE_CONTEXT, "-n", OBSERVABILITY_NAMESPACE,
         "port-forward", f"svc/{PROMETHEUS_SERVICE}", ":9090"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    port = None
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            match = re.search(r"Forwarding from 127\.0\.0\.1:(\d+) -> 9090", line)
            if match:
                port = int(match.group(1))
                break
        assert port, (
            f"could not establish a port-forward to svc/{PROMETHEUS_SERVICE} "
            f"in {OBSERVABILITY_NAMESPACE} -- is the kind-northstar cluster up?"
        )
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


def prometheus_query(prometheus_url: str, path: str, params=()) -> dict:
    """A Prometheus HTTP API call, made with curl through the forward (so the
    command in a task report is the command the test runs)."""
    command = ["curl", "-s", "--max-time", "30"]
    for param in params:
        command += ["--data-urlencode", param]
    command.append(f"{prometheus_url}{path}")
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (
        f"curl failed against {path}: rc={result.returncode} "
        f"stderr={result.stderr[:400]!r}"
    )
    body = json.loads(result.stdout)
    assert body.get("status") == "success", f"{path} returned {body!r}"
    return body["data"]


def prometheus_series(selector: str, *, prometheus_url: str | None = None,
                      fresh_window_s: float = 900, timeout_s: float = 150.0) -> list[dict]:
    """Series matching `selector` with a sample inside `fresh_window_s`, polled
    until `timeout_s`. A stale series is not proof the path works today.

    Opens its own port-forward unless given a `prometheus_url`."""
    with contextlib.ExitStack() as stack:
        url = prometheus_url or stack.enter_context(prometheus_port_forward())
        deadline = time.monotonic() + timeout_s
        while True:
            now = time.time()
            found = prometheus_query(
                url, "/api/v1/series",
                [f"match[]={selector}", f"start={now - fresh_window_s:.0f}",
                 f"end={now:.0f}"])
            if found or time.monotonic() >= deadline:
                return found
            time.sleep(10)


def prometheus_sum(selector: str, *, prometheus_url: str | None = None) -> float:
    """The sum of an instant query's current values; 0.0 if the series does not
    exist yet. For "did this count go up", read before and after."""
    with contextlib.ExitStack() as stack:
        url = prometheus_url or stack.enter_context(prometheus_port_forward())
        result = prometheus_query(url, "/api/v1/query", [f"query={selector}"])
        return sum(float(item["value"][1]) for item in result["result"])


# --- settlement-agent (A2A), the violating path --------------------------------

SETTLEMENT_AGENT_URL_DEFAULT = "http://localhost:19002"


@contextlib.contextmanager
def settlement_agent_route(url: str = SETTLEMENT_AGENT_URL_DEFAULT):
    """settlement-agent's A2A endpoint reachable at `url` (its own forward,
    owned and torn down here). Used only by the test that has to make the
    refused identity act again."""
    port = urllib.parse.urlsplit(url).port
    proc = subprocess.Popen(
        ["kubectl", "--context", KUBE_CONTEXT, "-n", "northstar-agents",
         "port-forward", "svc/settlement-agent", f"{port}:9002"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                requests.get(f"{url}/.well-known/agent-card.json", timeout=3)
                break
            except requests.RequestException:
                time.sleep(1)
        else:
            raise AssertionError(f"could not reach settlement-agent at {url} within 30s")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def send_claim_to_settlement_agent(
    claim_id: str, amount: float, *, url: str, traceparent: str | None = None,
    timeout: float = SETTLEMENT_TIMEOUT_S,
) -> dict:
    """The claim sent straight to settlement-agent over A2A `message/send`,
    skipping claims-agent: the refused identity proposes and tries to execute
    AGAIN for a claim that is already waiting for a human. Returns the JSON
    reply the agent gives (`status`, `paymentId`, `rule`, ...)."""
    headers = {"traceparent": traceparent} if traceparent else {}
    request = _jsonrpc("message/send", {"message": {
        "role": "user", "messageId": uuid.uuid4().hex,
        "parts": [{"kind": "text", "text": json.dumps({
            "claimId": claim_id, "amount": amount, "currency": "EUR"})}]}})
    response = httpx.post(f"{url}/", json=request, headers=headers, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    assert "error" not in body, f"settlement-agent refused the A2A call: {body['error']}"
    result = body["result"]
    parts = result.get("parts") or (result.get("status", {}).get("message") or {}).get("parts") or []
    for part in reversed(parts):
        if part.get("kind") == "text":
            try:
                return json.loads(part["text"])
            except json.JSONDecodeError:
                continue
    raise AssertionError(f"settlement-agent returned no JSON reply: {result!r}")
