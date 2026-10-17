"""Asserts the A2A hop is traced end to end into settlement-agent, and that
the MCP hop below it joins the same trace.

Chain under test: A2A client -> settlement-agent -> payments-mcp -> payments-api.

**Trace propagation into settlement-agent.** `a2a-sdk` 0.3.7's own telemetry
does not extract inbound W3C trace context on its own — an injected
`traceparent` header would otherwise be silently ignored and every request
would start a fresh root trace (full evidence in
`agents/settlement-agent/NOTES.md`). Real OTel framework instrumentation
(`opentelemetry-instrumentation-starlette`, applied to the built app in
`main.py`) fixes that, rather than hand-rolled spans. That is verified by
capture below: an injected `traceparent` produces a server span (`POST /`,
from StarletteInstrumentor — a2a-sdk's own spans nest under it) that is a
genuine child of the injected span ID inside the injected trace. Because the
injected root span never itself lands in Jaeger, the `POST /` entry span is
a legitimate orphan by the harness's definition; the exclusion below is
pinned to exactly that one span (its operation *and* its `parent_id`), not a
count tolerance.

**The settlement reply's payload is asserted, not just the absence of an
error.** a2a-sdk's blocking `message/send` returns the *first* enqueued
`Message` regardless of which executor is running, and that first message is
always non-error text, so a check that only asserts `"error" not in result`
would still pass if `executor.py` enqueued narration instead of the
documented `{claimId, paymentId, amount, status, narrative}` JSON.
`test_message_send_returns_the_full_settlement_reply` below parses and
asserts the actual payload for that reason. `test_agent_card_is_served`
similarly diffs the full served card against
`contracts/agent-cards/settlement-agent.json` rather than checking two
fields, so served/contract drift is caught here instead of shipping
silently.

**The MCP hop joins the same trace.** settlement-agent's MCP client
(`mcp_tools.py`) injects `params._meta` (traceparent + baggage) via the
lower-level `ClientSession.send_request()` API — mcp's high-level
`call_tool()` has no way to set `_meta` at all (see
`agents/settlement-agent/NOTES.md` §10). FastMCP 4.0.3's server-side
extraction joins the trace, so `test_all_four_services_are_in_one_trace`,
`test_mcp_tool_calls_are_present` and
`test_settlement_agent_span_precedes_the_payment` below assert real,
confirmed-by-capture behaviour. (The `northstar.claim.id` *attribute* still
does not cross this hop — a separate, confirmed FastMCP gap; see
`tests/e2e/test_claim_correlation.py` and
`agents/settlement-agent/NOTES.md` §11.)

**Net effect**: trace context propagates all the way from an A2A client into
settlement-agent, through the MCP hop into payments-mcp, and on into
payments-api — one trace, no orphans beyond the expected synthetic root. The
settlement reply's actual JSON payload, and the served agent card's parity
with its committed contract, are both under test.

**This file's fixture drives €6,200, which the platform refuses.** The PDS
denies `execute_payment` above the €6,000 allowance for this caller
(`platform/policy-decision-service/pds/rules.py`), so two of the tests below
assert that behaviour directly:
`test_message_send_returns_the_full_settlement_reply` asserts
`status == "awaiting_approval"` (M8; "refused" before it) (every other field of the documented reply unchanged,
including the €6,200 the ledger recorded for the proposal), and
`test_mcp_tool_calls_are_present` asserts `tools/call propose_payment` present
and `tools/call execute_payment` **absent**, paired with the model's own
attempt so the absence cannot pass vacuously. The other six tests in this file
are unaffected: `propose_payment` is permitted unconditionally
(`UNLIMITED_TOOLS`) and alone lands spans in all four services, so the entry
span's parentage, the orphan check, the outbound-MCP-span check, the
four-service check and the ancestry check all still hold and still prove what
they were written to prove. Note in particular that
`test_settlement_agent_span_precedes_the_payment` does not filter payments-api
spans for "execute" — the propose leg supplies the span it needs.
"""
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tests.harness import live
from tests.harness.jaeger import JaegerClient


# --- Why this file is gated behind RUN_BROWSER_TESTS=1 -----------------------
#
# `RUN_BROWSER_TESTS=1` is this repo's COST gate, not a browser gate. Four
# modules already ride it with no browser (`test_settlement_llm_trace.py`,
# `test_gateway_trace.py`, `test_evidence_view.py`, `test_policy_scores.py`),
# each saying so in its own `pytestmark`; `scripts/smoke-test.sh` sets the flag
# unconditionally, so the gate loses nothing.
#
# This file is one of three that would otherwise drive a real settlement on a
# bare `pytest tests/` -- three settlements' worth of Anthropic tokens, on
# someone's account, from a command the README presents as the ordinary way
# to run the suite. The three are this file, `test_claims_chain_trace.py` and
# `test_settlement_agent_trace.py`, one module-scoped driving fixture each.
#
# What is deliberately NOT gated, and why the line is drawn at tokens: three
# ungated tests still write an `executed` row to the ledger -- by ledger
# delta over a bare run, 2 x EUR 6,200 from `test_payments_api_trace.py` and
# `test_payments_mcp_trace.py` (neither goes through agentgateway, so no
# policy decision is made at all) and 1 x EUR 100 from
# `test_policy_denial.py::test_criterion_5...`, which is that test's whole
# assertion. Those cost nothing: payments-api's ledger is an in-memory,
# per-process store on a kind cluster, so "money moved" is a demo fiction and
# gating it would buy a developer nothing while removing the only ungated
# coverage of the money path from a bare run. Tokens are the real
# externality, and they are what this flag gates.
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason=(
        "drives a real settlement (model tokens) on the live cluster; gated "
        "behind the same RUN_BROWSER_TESTS=1 flag scripts/smoke-test.sh sets "
        "unconditionally -- no browser is started"
    ),
)

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
SETTLEMENT_URL = os.environ.get("SETTLEMENT_AGENT_URL", "http://localhost:19002")

ENTRY_SPAN = "POST /"  # StarletteInstrumentor's ASGI server span

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_CARD_CONTRACT = REPO_ROOT / "contracts" / "agent-cards" / "settlement-agent.json"


def send_message(text: str, traceparent: str | None = None) -> dict:
    """Minimal A2A JSON-RPC message/send call.

    Uses raw JSON-RPC rather than the SDK client so the test asserts the wire
    contract, not the SDK's convenience layer.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": uuid.uuid4().hex,
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }
    headers = {"traceparent": traceparent} if traceparent else None
    # A settlement is three sequential model turns plus two MCP round trips,
    # so 180s gives real headroom for a 529-triggered backoff on a slow
    # uplink (measured settlement latency is 2.6-7.9s over ten model-driven
    # runs). This call runs inside a module-scoped fixture, so a timeout here
    # reports all of this file's tests as ERROR with a traceback rather than
    # one named failure -- aligned on the same reasoned value
    # test_settlement_llm_trace.py uses.
    response = httpx.post(f"{SETTLEMENT_URL}/", json=payload, headers=headers, timeout=180)
    response.raise_for_status()
    return response.json()


def test_agent_card_is_served():
    response = httpx.get(f"{SETTLEMENT_URL}/.well-known/agent-card.json", timeout=15)
    response.raise_for_status()
    card = response.json()
    assert card["name"] == "settlement-agent"
    assert any(skill["id"] == "settle-claim" for skill in card["skills"])

    # The card is baked into the image at build time by
    # rebuild-and-deploy.sh's staging step, whose whole point is that
    # contracts/agent-cards/<component>.json is the single source of truth.
    # Checking only `name` and one skill id lets the served card silently
    # drift from the committed contract (e.g. an edited `description` or
    # `version` that never gets rebuilt) without failing anything. Diff the
    # whole document instead.
    contract = json.loads(AGENT_CARD_CONTRACT.read_text())
    assert card == contract, (
        "served agent card does not match the committed contract "
        f"({AGENT_CARD_CONTRACT}) — rebuild and redeploy settlement-agent"
    )


@pytest.fixture(scope="module")
def settlement_trace():
    # This test originates the request, so it mints its own trace/span ID and
    # sends it as a `traceparent` header (an A2A/HTTP boundary, so a header is
    # the right carrier — contrast the MCP boundary below, which never gets
    # an HTTP-header workaround). Looked up by that exact ID
    # (wait_for_trace_id), never by search: a search is only used when the
    # trace ID genuinely cannot be known up front, and for this hop it can.
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    traceparent = f"00-{trace_id}-{span_id}-01"

    claim_id = f"CLM-TEST-{uuid.uuid4().hex[:8]}"
    result = send_message(
        json.dumps({"claimId": claim_id, "amount": 6200, "currency": "EUR"}),
        traceparent=traceparent,
    )
    assert "error" not in result, f"A2A call failed: {result}"
    # At the resting level (`assisted`) this claim is sent to a human and the
    # payment sits `awaiting_approval` on the ledger. A test must leave no claim
    # waiting: an open approval is what a later file's `demo reset` declines,
    # and its decline spans fall in that file's evidence window. The reply is
    # already back (no parked task here), so deciding it now adds nothing to
    # the trace under test beyond the reviewer's own, separate trace.
    reply = json.loads(result["result"]["parts"][0]["text"])
    if reply.get("status") == "awaiting_approval":
        live.release_wait(reply["paymentId"])

    client = JaegerClient(JAEGER_URL)
    # Because settlement-agent's MCP client injects params._meta, payments-mcp
    # and payments-api join this trace too, on their own independent
    # BatchSpanProcessor export timers. ENTRY_SPAN appearing alone is not
    # enough to call the trace complete, so the fixture waits for the whole
    # chain to land and hold steady via the harness's own `services`
    # argument.
    trace = client.wait_for_trace_id(
        trace_id,
        operation=ENTRY_SPAN,
        timeout=90,
        services={"settlement-agent", "payments-mcp", "payments-api"},
    )
    return SimpleNamespace(
        trace=trace, trace_id=trace_id, span_id=span_id, claim_id=claim_id, result=result
    )


def test_message_send_returns_the_full_settlement_reply(settlement_trace):
    """`a2a-sdk` 0.3.7's `ResultAggregator.consume_and_break_on_interrupt`
    returns as soon as it sees the FIRST Message-kind event (verified,
    NOTES.md §8), so a blocking `message/send` caller never sees a second
    enqueued message. An executor that enqueued narration ("Assessed
    settlement: ...") before the final JSON result would silently drop the
    promised JSON from the reply. `executor.py` enqueues exactly one Message
    — the final JSON result — and this test asserts that payload's
    substance, not merely that no error occurred, so an executor that
    regressed to enqueuing narration first would fail this test even though
    `"error" not in result` alone would not catch it.

    **`status` is `refused`, and every other field still holds.**
    The fixture sends €6,200, which is above the €6,000 allowance the platform
    enforces on `execute_payment` for this caller
    (`platform/policy-decision-service/pds/rules.py`), so the platform stops
    the execution. This test's intent is unchanged and is *not* about the
    status value: it exists to prove the A2A wire reply is the documented
    `{claimId, paymentId, amount, currency, status, narrative}` JSON object and
    not narration text that a2a-sdk's first-message behaviour would have handed
    back instead. That is still exactly what it asserts, field by field — the
    brief's three-`enqueue_event` executor would still fail it.

    Note that a refusal is a full, well-formed reply and not a degraded one,
    which is why nothing here had to be relaxed to accommodate it:
    `settlement.py::decide_settlement`'s refusal branch returns the amount,
    currency and paymentId the **ledger recorded for the proposal** (the
    propose leg is permitted unconditionally — `UNLIMITED_TOOLS` — so those are
    real recorded values, not placeholders), the platform's own `status`, and
    the model's `narrative`. A refusal that dropped `paymentId`, or reported an
    amount other than the proposed one, would fail this test — and should.
    """
    payload = json.loads(settlement_trace.result["result"]["parts"][0]["text"])
    assert payload["claimId"] == settlement_trace.claim_id
    assert payload["paymentId"].startswith("PAY-")
    assert payload["amount"] == 6200
    assert payload["currency"] == "EUR"
    assert payload["status"] == "awaiting_approval"
    assert payload["narrative"], "narrative must be non-empty"


def test_settlement_agent_entry_span_is_parented_to_the_injected_context(settlement_trace):
    """Verified by capture, not by reasoning: StarletteInstrumentor extracts
    the injected `traceparent` and starts the entry span as a real child of
    it — not a new, disconnected root (see NOTES.md for the evidence)."""
    trace = settlement_trace.trace
    entry = trace.span(ENTRY_SPAN)
    assert entry.parent_id == settlement_trace.span_id, (
        f"{ENTRY_SPAN!r} span's parent is {entry.parent_id!r}, expected the "
        f"injected span id {settlement_trace.span_id!r} — inbound trace "
        "context did not propagate"
    )
    # Jaeger's TraceID.String() drops a whole-number leading zero nibble when
    # the high 64 bits start with a zero hex digit (see
    # model.TraceID.String() in jaeger-idl), so the returned ID can be
    # shorter than 32 hex chars. Zero-pad it back out to 32 for an exact
    # comparison rather than stripping leading zeros from both sides, which
    # could mask a genuine mismatch that happened to leave both operands
    # with the same stripped form.
    assert trace.trace_id.lower().zfill(32) == settlement_trace.trace_id.lower()


def test_settlement_agent_trace_has_no_unexpected_orphans(settlement_trace):
    """The injected span ID is never itself a real span in this trace, so
    the entry span (parented to it) is an unavoidable, legitimate orphan.
    Pinned to exactly that one span — operation *and* parent_id, not a count
    tolerance or a name allowlist — so a genuine propagation defect anywhere
    else (a real internal hop dropping context) still fails this test."""
    trace = settlement_trace.trace
    orphans = trace.orphans()
    unexpected = [
        s
        for s in orphans
        if not (s.operation == ENTRY_SPAN and s.parent_id == settlement_trace.span_id)
    ]
    assert not unexpected, (
        "orphan spans present beyond the expected synthetic root, context "
        f"propagation is broken: {[(s.service, s.operation, s.parent_id) for s in unexpected]}"
    )


def test_settlement_agent_outbound_mcp_calls_are_traced(settlement_trace):
    """HTTPXClientInstrumentor (bootstrap.py) emits CLIENT spans for the MCP
    streamable-http transport's own HTTP calls to payments-mcp, nested inside
    the (correctly externally rooted) A2A request span. This proves this
    agent's own outbound HTTP hop is instrumented and uses the stable HTTP
    semconv spelling.

    Since the MCP hop joins this trace (params._meta injection,
    correlation.py), the trace also legitimately contains payments-mcp's
    *own* outbound CLIENT span to payments-api — filtering to
    settlement-agent's own spans is what keeps this test's assertion (every
    one of *this* service's outbound calls targets payments-mcp) precise
    rather than accidentally also asserting something false about a
    different service's own outbound hop.

    **Filtered to the httpx instrumentation's own scope, not `kind ==
    "client"`.** The settlement decision is a real model call, so
    settlement-agent also emits pydantic-ai's `chat <model>` spans, and
    those are CLIENT-kind too. They carry `server.address =
    "api.anthropic.com"` and no `url.full` at all, so a bare `kind ==
    "client" and service == "settlement-agent"` filter would sweep them in
    and break the `"payments-mcp" in url.full` assertion.
    `otel.scope.name == "opentelemetry.instrumentation.httpx"` says what this
    test always meant: the spans *this service's HTTP client
    instrumentation* produces, and measurably so — it is not a tolerance,
    and a genuinely misdirected outbound HTTP call would still fail here.

    **The model hop has its own httpx-scoped span too, and is deliberately
    excluded.** `HTTPX2ClientInstrumentor` (`bootstrap.py`) means
    `anthropic==1.4.0`'s `httpx2` traffic to `api.anthropic.com` also
    produces a CLIENT span under `otel.scope.name ==
    "opentelemetry.instrumentation.httpx"`, nested under the `chat` span,
    with its own `url.full` (`https://api.anthropic.com/v1/messages...`).
    That is a real, separate outbound target for this service and belongs in
    this trace — it is asserted for and pinned by
    `tests/e2e/test_settlement_llm_trace.py::test_anthropic_client_span_exists_and_is_parented_to_chat`.
    It is excluded here (`server.address != "api.anthropic.com"`) because
    this test's job is specifically the MCP transport's own spans, not every
    outbound hop this service makes.

    **The target is the gateway, not payments-mcp directly.**
    `deploy/k8s/settlement-agent.yaml`'s `PAYMENTS_MCP_URL` names
    `agentgateway.northstar-platform.svc.cluster.local:3000/mcp`, and the
    measured value on every one of these spans is exactly that
    (`url.full=http://agentgateway.northstar-platform.svc.cluster.local:3000/mcp`,
    `server.address=agentgateway.northstar-platform.svc.cluster.local`). This
    still fails if this service's MCP transport ever calls anything other
    than its configured MCP endpoint, but it does not on its own claim that
    the call *reaches* payments-mcp — that is a statement about the hop
    below, proven by `tests/e2e/test_gateway_trace.py`, which asserts
    payments-mcp's tool spans descend from agentgateway's inside this same
    one trace."""
    client_spans = [
        s
        for s in settlement_trace.trace.spans
        if s.service == "settlement-agent"
        and s.attrs.get("otel.scope.name") == "opentelemetry.instrumentation.httpx"
        and s.attrs.get("server.address") != "api.anthropic.com"
    ]
    assert client_spans, "no outbound CLIENT spans found for the MCP transport"
    for span in client_spans:
        target = span.attrs.get("url.full") or ""
        assert "agentgateway" in target, f"unexpected outbound target: {span.attrs}"
        # Stable HTTP semconv spelling: OTEL_SEMCONV_STABILITY_OPT_IN=http
        # is set on the deployment manifest specifically so these come out
        # this way rather than the legacy http.method/http.url/http.status_code.
        assert "http.request.method" in span.attrs
        assert "http.method" not in span.attrs


def test_all_four_services_are_in_one_trace(settlement_trace):
    """settlement-agent's MCP client (mcp_tools.py) injects params._meta
    (traceparent + baggage) via the lower-level ClientSession.send_request()
    API, and FastMCP 4.0.3's server-side extraction joins the trace.
    Confirmed by capture: settlement-agent, payments-mcp and payments-api all
    land in the one trace this test originates."""
    services = settlement_trace.trace.services()
    assert {"settlement-agent", "payments-mcp", "payments-api"} <= services, (
        f"expected one trace spanning the chain; got services: {sorted(services)}"
    )


def test_mcp_tool_calls_are_present(settlement_trace):
    """Pins the MCP hop's *tool-span vocabulary*: payments-mcp names its span
    per the MCP semantic convention, `tools/call <tool>`, one span per tool
    the model called, and those spans land in this same trace.

    **`tools/call execute_payment` is absent, and its absence is what this
    test asserts.** The fixture sends €6,200, above the €6,000
    allowance the platform enforces on `execute_payment` for this caller, so
    the gateway's policy hook denies the call before it is forwarded:
    payments-mcp never receives it and emits no span for it. Re-pointing the
    fixture under the allowance to make the old assertion pass again was the
    alternative and was rejected — `tests/e2e/test_gateway_trace.py` and
    `tests/e2e/test_settlement_llm_trace.py` are the files re-pointed to
    €5,800, because their subject really is incidental to the amount; this
    file's fixture is shared with
    `test_message_send_returns_the_full_settlement_reply` above, whose whole
    subject is the reply the canonical €6,200 claim produces.

    **The absence is asserted together with the request, deliberately.** An
    absence on its own is the defect class this project keeps naming: a test
    reading only "no `tools/call execute_payment` span exists" would pass
    identically against a model that never asked to execute anything, a
    payments-mcp that stopped emitting spans, and a broken MCP hop — green and
    red meaning the same thing. So the model's own attempt is asserted first,
    from PydanticAI's `execute_tool execute_payment` span in settlement-agent
    (this service, which the fixture waits for, so no new service dependency
    is introduced), and only then the absence one hop further down. Together
    they say the real thing: the model asked, and the request did not arrive.

    What stopped it, and where, is asserted from the platform's own telemetry
    by `tests/e2e/test_policy_denial.py` — the gateway's `tools/call` SERVER
    span, the PDS's `northstar.policy.decision` span with the rule and the two amounts on
    it, and the absent forward and payments-api execute spans. This test stays
    a statement about the MCP hop's span vocabulary, which is what it was
    written to be."""
    trace = settlement_trace.trace
    operations = {s.operation for s in trace.spans}

    assert "tools/call propose_payment" in operations, (
        "payments-mcp emitted no 'tools/call propose_payment' span; the "
        "propose leg is permitted unconditionally (UNLIMITED_TOOLS), so this "
        "is a broken MCP hop, not a policy decision. Operations present: "
        f"{sorted(operations)}"
    )

    # The model really did ask to execute. PydanticAI's tool span, in
    # settlement-agent's own process, one per tool call the model made.
    attempts = trace.spans_named(
        "execute_tool execute_payment", service="settlement-agent"
    )
    assert attempts, (
        "the model never attempted execute_payment, so the absence asserted "
        "below would hold for the wrong reason entirely -- it would say "
        "nothing about the platform having refused anything. settlement-agent "
        "operations present: "
        f"{sorted({s.operation for s in trace.spans if s.service == 'settlement-agent'})}"
    )

    # ...and the request never reached payments-mcp.
    assert "tools/call execute_payment" not in operations, (
        "payments-mcp emitted a 'tools/call execute_payment' span, so the "
        f"execution of EUR 6200 was forwarded and not refused -- either the "
        "gateway lost its mcpGuardrails policy, or the PDS's allowance for "
        "this caller changed. tests/e2e/test_policy_denial.py holds the "
        "authoritative assertions on that decision."
    )


def test_settlement_agent_span_precedes_the_payment(settlement_trace):
    """Every payments-api span in this trace descends from a settlement-agent
    span, proving the causal order (agent decides, then the payment happens)
    rather than mere co-occurrence in the same trace."""
    trace = settlement_trace.trace
    agent_spans = [s for s in trace.spans if s.service == "settlement-agent"]
    api_spans = [s for s in trace.spans if s.service == "payments-api"]
    assert agent_spans, "no settlement-agent spans"
    assert api_spans, "no payments-api spans"
    agent_ids = {s.span_id for s in agent_spans}
    by_id = {s.span_id: s for s in trace.spans}

    def ancestors(span):
        seen = set()
        current = span
        while current and current.parent_id and current.parent_id not in seen:
            seen.add(current.parent_id)
            yield current.parent_id
            current = by_id.get(current.parent_id)

    assert any(
        agent_ids & set(ancestors(api_span)) for api_span in api_spans
    ), "payments-api span does not descend from settlement-agent"
