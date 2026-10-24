"""Calls against the live `kind-northstar` cluster, shared by the e2e tests and
the stage driver (`scripts/demo`).

**Why this module exists, and why it lives in `tests/harness/`.** The five-minute
demo flow (`docs/superpowers/specs/2026-10-12-northstar-m7-bounded-design.md`
section 7) is a thin driver over things this repo's tests already prove: submit
a claim to claims-agent over A2A, mint a SPIFFE JWT-SVID and speak MCP to the
gateway. The spec's rule is
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

import atexit
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

# claims-agent and the customer-ui server, reached the way the browser reaches
# them: kind host ports onto NodePorts (deploy/kind/cluster.yaml).
CLAIMS_AGENT_URL_DEFAULT = "http://localhost:19001"
CUSTOMER_UI_URL_DEFAULT = os.environ.get("CUSTOMER_UI_URL", "http://localhost:8080")
# A settlement is three sequential model turns plus MCP round trips; 180s
# matches every other call site in this suite that drives one.
SETTLEMENT_TIMEOUT_S = 180

OBSERVABILITY_NAMESPACE = "observability"
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
    """Liveness by HTTP request: any answer from the agent card counts."""
    try:
        requests.get(f"{url}/.well-known/agent-card.json", timeout=3)
    except requests.RequestException:
        return False
    return True


@contextlib.contextmanager
def claims_agent_route(url: str = CLAIMS_AGENT_URL_DEFAULT):
    """claims-agent answering at `url`, waited for up to 30s.

    The default URL is a kind host port, so there is nothing to start here: a
    redeploy of claims-agent only needs a moment to come back.
    """
    deadline = time.monotonic() + 30
    while not claims_agent_alive(url):
        assert time.monotonic() < deadline, (
            f"claims-agent does not answer at {url} -- is the kind-northstar "
            "cluster up? (./scripts/preflight.sh checks every localhost URL)"
        )
        time.sleep(1)
    yield url


# --- side doors: the mesh-protected services -----------------------------------
#
# payments-api, payments-mcp and settlement-agent each have a Linkerd Server
# that admits only named workload identities
# (deploy/platform/reach/linkerd-policies.yaml). A request from the laptop has
# no identity, so these three have no host port: through a NodePort they would
# refuse it, which is the point. Tests and `scripts/demo` still read the ledger
# and call these services directly, so they use `kubectl port-forward`, which
# lands inside the pod behind its proxy. Each forward is opened on first use,
# on the port its URL names, reused if that port already answers, and stopped
# when the process exits.

PAYMENTS_API_URL = os.environ.get("PAYMENTS_API_URL", "http://localhost:18080")
PAYMENTS_MCP_URL = os.environ.get("PAYMENTS_MCP_URL", "http://localhost:18000/mcp")
SETTLEMENT_AGENT_URL_DEFAULT = os.environ.get("SETTLEMENT_AGENT_URL", "http://localhost:19002")

_SIDE_DOORS: dict[str, subprocess.Popen] = {}


def _answers(url: str) -> bool:
    try:
        requests.get(url, timeout=3)
    except requests.RequestException:
        return False
    return True


def _close_side_doors() -> None:
    for proc in _SIDE_DOORS.values():
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
    _SIDE_DOORS.clear()


atexit.register(_close_side_doors)


def side_door(url: str, namespace: str, service: str, remote_port: int, probe_path: str) -> str:
    """`url` answering, through a port-forward to `svc/<service>` if needed.

    Returns `url` unchanged. Any HTTP answer on `probe_path` counts as up.
    """
    split = urllib.parse.urlsplit(url)
    base = f"{split.scheme}://{split.netloc}"
    if _answers(base + probe_path):
        return url
    if base not in _SIDE_DOORS or _SIDE_DOORS[base].poll() is not None:
        assert split.port, f"{url!r} names no port, so no forward can be opened for it"
        _SIDE_DOORS[base] = subprocess.Popen(
            ["kubectl", "--context", KUBE_CONTEXT, "-n", namespace,
             "port-forward", f"svc/{service}", f"{split.port}:{remote_port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    deadline = time.monotonic() + 30
    while not _answers(base + probe_path):
        assert time.monotonic() < deadline, (
            f"could not reach {service} at {base} within 30s of starting a "
            f"port-forward to svc/{service} in {namespace} -- is the "
            "kind-northstar cluster up, and is nothing else holding that port?"
        )
        time.sleep(1)
    return url


def payments_api_url() -> str:
    """The ledger's base URL (GET /healthz answers without an identity)."""
    return side_door(PAYMENTS_API_URL, "northstar-systems", "payments-api", 8080, "/healthz")


def payments_mcp_url() -> str:
    """payments-mcp's MCP endpoint, directly, not through the gateway."""
    return side_door(PAYMENTS_MCP_URL, "northstar-tools", "payments-mcp", 8000, "/mcp")


def settlement_agent_url() -> str:
    """settlement-agent's A2A base URL, skipping claims-agent."""
    return side_door(
        SETTLEMENT_AGENT_URL_DEFAULT, "northstar-agents", "settlement-agent", 9002,
        "/.well-known/agent-card.json",
    )


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


def claim_message(text: str) -> dict:
    """A new claim's A2A message. `text` is a JSON object for the
    deterministic path, or the customer's own words for the intake model."""
    return {"role": "user", "messageId": uuid.uuid4().hex,
            "parts": [{"kind": "text", "text": text}]}


def answer_message(task_id: str, context_id: str, text: str) -> dict:
    """The customer's answer to claims-agent's question, on the SAME task: the
    task id and context id say which claim it answers."""
    return {"role": "user", "messageId": uuid.uuid4().hex,
            "taskId": task_id, "contextId": context_id,
            "parts": [{"kind": "text", "text": text}]}


def _new_traceparent() -> str:
    return f"00-{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-01"


def _stream_task(claims_agent_url: str, request: dict, traceparent: str, timeout: float,
                 what: str, snap: dict) -> dict:
    """POST one `message/stream` and read it to its end; returns the last snapshot."""
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
                f"claims-agent refused {what}: "
                f"{(error or {}).get('message') if error else body[:300]!r}")
        for event in _sse_results(response):
            snap = _snapshot(event, snap)
    assert snap.get("state"), f"claims-agent streamed no task state for {what}"
    assert snap.get("reply") is not None, f"claims-agent streamed no reply for {what}: {snap}"
    return snap


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
    claim = json.dumps({
        "claimId": claim_id, "policyNumber": "POL-77120",
        "description": "Rear-end collision at a junction", "estimatedAmount": amount})
    return submit_text(claim, claims_agent_url=claims_agent_url, customer_ui_url=customer_ui_url,
                       timeout=timeout, traceparent=traceparent, what=f"claim {claim_id!r}")


def submit_text(
    text: str,
    *,
    claims_agent_url: str = CLAIMS_AGENT_URL_DEFAULT,
    customer_ui_url: str = CUSTOMER_UI_URL_DEFAULT,
    timeout: float = SETTLEMENT_TIMEOUT_S,
    traceparent: str | None = None,
    what: str = "the free-text claim",
) -> dict:
    """A claim as the customer wrote it, through the same path as `submit_claim`.

    Text that is not a JSON object goes to claims-agent's intake model. The
    stream may end at `input-required` with `reply["status"] ==
    "needs_information"` and a `question`; answer it with `answer_claim` on the
    returned `task_id` and `context_id`. Returns what `submit_claim` returns.
    """
    traceparent = traceparent or _new_traceparent()
    push = push_config(customer_ui_url=customer_ui_url)
    request = _jsonrpc("message/stream", {
        "message": claim_message(text),
        "configuration": {"pushNotificationConfig": {"url": push["url"], "token": push["token"]}},
    })
    snap: dict = {"trace_id": traceparent.split("-")[1], "push_token": push["token"]}
    return _stream_task(claims_agent_url, request, traceparent, timeout, what, snap)


def answer_claim(
    run: dict,
    text: str,
    *,
    claims_agent_url: str = CLAIMS_AGENT_URL_DEFAULT,
    timeout: float = SETTLEMENT_TIMEOUT_S,
    traceparent: str | None = None,
) -> dict:
    """The customer's answer to claims-agent's question, on the same task.

    `run` is what `submit_text` (or a previous `answer_claim`) returned. The
    answer is its own trace, so the result's `trace_id` is new; `task_id`,
    `context_id` and `push_token` are carried over. No push config is sent:
    the one sent with the claim stays with the task.
    """
    traceparent = traceparent or _new_traceparent()
    request = _jsonrpc("message/stream", {
        "message": answer_message(run["task_id"], run["context_id"], text)})
    snap = {"trace_id": traceparent.split("-")[1], "task_id": run["task_id"],
            "context_id": run["context_id"], "push_token": run.get("push_token")}
    return _stream_task(claims_agent_url, request, traceparent, timeout,
                        f"the answer on task {run['task_id']}", snap)


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


def get_payment(payment_id: str) -> dict:
    """Read a payment straight back from the ledger it lives in -- the ledger
    is the authority on status, amount and the refusal record, never the reply
    a settlement or an approval happened to carry."""
    response = httpx.get(f"{payments_api_url()}/payments/{payment_id}", timeout=10)
    assert response.status_code == 200, (
        f"could not read payment {payment_id} back from the ledger at "
        f"{PAYMENTS_API_URL} (HTTP {response.status_code}): {response.text[:300]}"
    )
    return response.json()


def list_payments(status: str) -> list[dict]:
    """Every ledger payment in `status`, as payments-api lists them
    (`GET /payments?status=`)."""
    response = httpx.get(f"{payments_api_url()}/payments", params={"status": status}, timeout=10)
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
    print it, so this cannot shadow a port anything else uses. The chosen port is parsed out of kubectl's own "Forwarding
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


# --- the run window -----------------------------------------------------------


def window_start_ms_now() -> int:
    return int((time.time() - WINDOW_SKEW_S) * 1000)


# --- Prometheus ----------------------------------------------------------------

OBSERVABILITY_NAMESPACE = "observability"
PROMETHEUS_SERVICE = "prometheus"


@contextlib.contextmanager
def prometheus_port_forward():
    """An ephemeral port-forward to `svc/prometheus`; yields its base URL.

    `:9090` with no local port makes kubectl choose a free one and print it.
    Independent of the localhost:9090 host port, so a test still reads
    Prometheus itself if that mapping is missing.
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
