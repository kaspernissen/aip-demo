"""Asserts the first agent-to-agent handoff — claims-agent delegating to
settlement-agent over A2A — is correctly traced, and that the chain now
reaches all the way down through the MCP hop into payments-api.

Chain under test: A2A client -> claims-agent -> settlement-agent -> payments-mcp
-> payments-api.

This test originates the request, so it mints its own trace/span ID and
sends it as a `traceparent` header, then looks the trace up by that exact ID
(`wait_for_trace_id`), never by search. Because the injected root span never
itself lands in Jaeger, claims-agent's entry span is a legitimate orphan by
the harness's definition; the exclusion below is pinned to exactly that one
span (its operation *and* its `parent_id`), not a count tolerance.

**The MCP-hop gap is closed.** settlement-agent's MCP client
(`mcp_tools.py`) injects `params._meta` (traceparent + baggage) via the
lower-level `ClientSession.send_request()` API — `mcp==1.9.4`'s high-level
`call_tool()` has no way to set `_meta` at all (see
`agents/settlement-agent/NOTES.md` §10). FastMCP 4.0.3's server-side
extraction joins the trace, so `test_all_four_backend_services_are_in_one_trace`
below asserts real, confirmed-by-capture behaviour: the whole chain, from
this test's injected root through claims-agent, settlement-agent,
payments-mcp and payments-api, is one trace. (The `northstar.claim.id`
*attribute* still does not cross the MCP hop — a separate, confirmed
FastMCP gap; see `tests/e2e/test_claim_correlation.py`.)

**This file's fixture stays at €6,200, which the platform refuses.** The
PDS denies `execute_payment` above the €6,000 allowance for
settlement-agent (`platform/policy-decision-service/pds/rules.py`), so
`test_message_send_returns_the_settlement_json` below asserts
`status == "awaiting_approval"` (M8; "refused" before it) — every other field of the documented reply unchanged,
because a refusal is a full reply that claims-agent forwards field for field.
`test_payment_was_executed_with_no_governance` is **retired**: its premise
was the *absence* of governance, which no amount can restore, and
`tests/e2e/test_policy_denial.py` supersedes it at both ends of the rule. The
retirement note where it stood records what it asserted and why it went; do
not re-add it under a different amount. The remaining five tests are
unaffected — `propose_payment` is permitted unconditionally
(`UNLIMITED_TOOLS`) and alone lands spans in all four services, so the
parentage, delegation, orphan, four-service and error-propagation assertions
all still hold and still prove what they were written to prove.
"""
import json
import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tests.harness.jaeger import JaegerClient


# --- Why this file is gated behind RUN_BROWSER_TESTS=1 -----------------------
#
# `RUN_BROWSER_TESTS=1` is this repo's COST gate, not a browser gate. Four
# modules already ride it with no browser (`test_settlement_llm_trace.py`,
# `test_gateway_trace.py`, `test_evidence_view.py`, `test_policy_scores.py`),
# each saying so in its own `pytestmark`; `scripts/smoke-test.sh` sets the flag
# unconditionally, so the gate loses nothing.
#
# This file drives a real settlement on a bare `pytest tests/` -- along with
# `test_claim_correlation.py` and `test_settlement_agent_trace.py` (one
# module-scoped driving fixture each), that is three settlements' worth of
# Anthropic tokens, on someone's account, from a command the README presents
# as the ordinary way to run the suite.
#
# What is deliberately NOT gated, and why the line is drawn at tokens: three
# ungated tests still write an `executed` row to the ledger -- measured by
# ledger delta over a bare run, 2 x EUR 6,200 from `test_payments_api_trace.py`
# and `test_payments_mcp_trace.py` (neither goes through agentgateway, so no
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
CLAIMS_URL = os.environ.get("CLAIMS_AGENT_URL", "http://localhost:19001")

ENTRY_SPAN = "POST /"  # StarletteInstrumentor's ASGI server span

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_CARD_CONTRACT = REPO_ROOT / "contracts" / "agent-cards" / "claims-agent.json"


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
    # Aligned on the same reasoned timeout test_settlement_llm_trace.py and
    # test_settlement_agent_trace.py both use, rather than a third, narrower
    # value for the same underlying operation (settlement-agent's
    # settlement, one extra A2A hop away from here) -- a settlement is three
    # sequential model turns plus two MCP round trips, not one hardcoded
    # function call, so a 529-triggered backoff on a slow uplink deserves
    # real headroom. This call also runs
    # inside a module-scoped fixture, so a timeout here would report all of
    # this file's tests as ERROR rather than one named failure.
    response = httpx.post(f"{CLAIMS_URL}/", json=payload, headers=headers, timeout=180)
    response.raise_for_status()
    return response.json()


def file_claim(claim_id: str, amount: float, traceparent: str | None = None) -> dict:
    return send_message(
        json.dumps(
            {
                "claimId": claim_id,
                "policyNumber": "POL-77120",
                "description": "Rear-end collision at a junction",
                "estimatedAmount": amount,
            }
        ),
        traceparent=traceparent,
    )


def test_agent_card_is_served():
    response = httpx.get(f"{CLAIMS_URL}/.well-known/agent-card.json", timeout=15)
    response.raise_for_status()
    card = response.json()

    # contracts/agent-cards/<component>.json is the single source of truth,
    # staged into the build context by rebuild-and-deploy.sh. Diff the
    # whole document, not just a couple of fields, so a served/contract drift
    # fails here instead of shipping silently.
    contract = json.loads(AGENT_CARD_CONTRACT.read_text())
    assert card == contract, (
        "served agent card does not match the committed contract "
        f"({AGENT_CARD_CONTRACT}) — rebuild and redeploy claims-agent"
    )


@pytest.fixture(scope="module")
def chain_trace():
    # Mint our own trace/span ID, send it as a traceparent header (an
    # A2A/HTTP boundary), and look it up by that exact ID — never by search.
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    traceparent = f"00-{trace_id}-{span_id}-01"

    claim_id = f"CLM-TEST-{uuid.uuid4().hex[:8]}"
    result = file_claim(claim_id, 6200, traceparent=traceparent)
    assert "error" not in result, f"claims-agent call failed: {result}"

    client = JaegerClient(JAEGER_URL)
    trace = client.wait_for_trace_id(trace_id, operation=ENTRY_SPAN, timeout=120)

    # Both claims-agent's and settlement-agent's own BatchSpanProcessors
    # default to a 5s scheduled export delay, and each is a *separate*
    # process exporting on its own timer, independently of the httpx CLIENT
    # span for the outbound call and of each other. By the time the HTTP
    # response comes back, settlement-agent has already finished its own
    # work in-process, but any of these spans may not have reached the
    # collector yet. `wait_for_trace_id`'s `operation` check is satisfied the
    # instant claims-agent's own "POST /" span lands, which races ahead of
    # the rest — observed directly across repeated captures: one showed only
    # {"claims-agent"}; another showed both services but with
    # settlement-agent's entry span an orphan because claims-agent's own
    # outbound httpx CLIENT span (its declared parent) hadn't landed yet.
    # Keep polling the same trace ID (never a fresh search) until the trace
    # is fully assembled — settlement-agent present and no orphan beyond the
    # one expected synthetic root — or we give up.
    def is_complete(t) -> bool:
        if "settlement-agent" not in t.services():
            return False
        return all(
            o.operation == ENTRY_SPAN and o.service == "claims-agent" and o.parent_id == span_id
            for o in t.orphans()
        )

    deadline = time.time() + 45
    while not is_complete(trace) and time.time() < deadline:
        time.sleep(2)
        trace = client.get_trace(trace_id)

    # is_complete() proves claims-agent and settlement-agent have both fully
    # landed, but says nothing about a *third* service's spans landing later
    # still, on that service's own independent BatchSpanProcessor timer —
    # payments-mcp and payments-api join this same trace on their own export
    # timers. Snapshotting the instant is_complete() first holds risks
    # handing test_all_four_backend_services_are_in_one_trace a trace that
    # is missing those not-yet-landed spans. So: once is_complete() holds
    # (or its own deadline elapses), keep polling until the *total span
    # count* has gone unchanged for a continuous quiet window.
    #
    # That quiet window MUST be strictly longer than the export interval it
    # is waiting on, or it is not a quiet window at all. Every service here
    # uses `BatchSpanProcessor(OTLPSpanExporter())` with no
    # schedule_delay_millis override (see each service's bootstrap.py), i.e.
    # the OTel SDK's default 5s export delay. A quiet window no longer than
    # that delay would very often see a batch land just after the window
    # already looked quiet, so the loop would exit early anyway. Instead:
    # track the wall-clock time of the last observed span-count change and
    # only accept the trace as stable once that change is more than
    # QUIET_WINDOW_S (10s) in the past — any span landing resets the clock.
    # 10s, not just-over-5s: a bare 1s margin over the nominal 5s export
    # delay is not enough once real network/collector latency is added on
    # top of the timer itself -- a 6s window can still miss a batch that
    # lands at 6.5s. If the BatchSpanProcessor's schedule delay is ever
    # tuned away from the SDK default in any service's bootstrap.py,
    # QUIET_WINDOW_S must move with it.
    QUIET_WINDOW_S = 10  # comfortably > 5s BatchSpanProcessor default export delay
    POLL_INTERVAL_S = 1
    stability_deadline = time.time() + 90
    previous_count = len(trace.spans)
    last_change = time.time()
    while (
        time.time() - last_change < QUIET_WINDOW_S and time.time() < stability_deadline
    ):
        time.sleep(POLL_INTERVAL_S)
        trace = client.get_trace(trace_id)
        count = len(trace.spans)
        if count != previous_count:
            previous_count = count
            last_change = time.time()

    return SimpleNamespace(
        trace=trace, trace_id=trace_id, span_id=span_id, claim_id=claim_id, result=result
    )


def test_message_send_returns_the_settlement_json(chain_trace):
    """a2a-sdk 0.3.7's blocking message/send handler returns as soon as it
    sees the FIRST Message-kind event enqueued, silently dropping anything
    enqueued after -- the same class of bug that affects settlement-agent's
    executor (see agents/settlement-agent/NOTES.md §8). A narration message
    enqueued before the final JSON result would mean this test's caller only
    ever sees that narration text and never the settlement JSON this agent's
    documented interface promises. executor.py avoids this: the narration is
    logged, not enqueued, and exactly one Message — the final JSON result —
    is enqueued. This test asserts the payload's substance, not merely that
    no error occurred, so a regression back to a two-enqueue executor would
    fail it.

    **`status` is `refused`, and every other field still holds.**
    The fixture files the claim at €6,200, above the €6,000 allowance the
    platform enforces on `execute_payment` for settlement-agent
    (`platform/policy-decision-service/pds/rules.py`), so the execution is
    denied and settlement-agent replies `refused` — which claims-agent's
    executor forwards outward unchanged, field for field, because a refusal is
    a reply and not an error.

    That forwarding is why nothing here had to be relaxed, and it is worth
    being explicit about what this test therefore still catches. Its subject is
    claims-agent's *wire contract*: two hops out from the ledger, a caller must
    receive the documented
    `{claimId, policyNumber, paymentId, amount, currency, status, narrative}`
    JSON object, and not the "Claim ... received. Investigating." narration
    a2a-sdk's first-message-wins behaviour would otherwise have handed back.
    All seven fields are still asserted, including the €6,200 and the `PAY-`
    payment id the **ledger really recorded for the proposal**
    (`propose_payment` is permitted unconditionally — `UNLIMITED_TOOLS`) and
    the `narrative` the model wrote about the refusal. A refusal that arrived
    as a JSON-RPC error, or that lost a field crossing claims-agent, would fail
    this test — and should."""
    payload = json.loads(chain_trace.result["result"]["parts"][0]["text"])
    assert payload["claimId"] == chain_trace.claim_id
    assert payload["policyNumber"] == "POL-77120"
    assert payload["paymentId"].startswith("PAY-")
    assert payload["amount"] == 6200
    assert payload["currency"] == "EUR"
    assert payload["status"] == "awaiting_approval"
    assert payload["narrative"], "narrative must be non-empty"


def test_claims_agent_entry_span_is_parented_to_the_injected_context(chain_trace):
    """Proves context *propagated* from the test client into claims-agent
    (StarletteInstrumentor) — not merely that a matching span exists
    somewhere in the store.

    Both claims-agent and settlement-agent use the same StarletteInstrumentor
    setup and so both name their entry span "POST /" — `Trace.span()`
    matches by operation name alone and raises on more than one match, so
    this looks the claims-agent one up directly by filtering on `.service`
    rather than calling `trace.span(ENTRY_SPAN)`, which would be ambiguous
    the moment a second A2A hop joins the trace."""
    trace = chain_trace.trace
    entries = [
        s for s in trace.spans if s.operation == ENTRY_SPAN and s.service == "claims-agent"
    ]
    assert len(entries) == 1, (
        f"expected exactly one claims-agent {ENTRY_SPAN!r} span, found {len(entries)}"
    )
    entry = entries[0]
    assert entry.parent_id == chain_trace.span_id, (
        f"{ENTRY_SPAN!r} span's parent is {entry.parent_id!r}, expected the "
        f"injected span id {chain_trace.span_id!r} — inbound trace context "
        "did not propagate"
    )
    # Jaeger's TraceID.String() drops a whole-number leading zero nibble when
    # the high 64 bits start with a zero hex digit, so the returned ID can be
    # shorter than 32 hex chars. Zero-pad rather than strip leading zeros from
    # both sides, which could mask a genuine mismatch.
    assert trace.trace_id.lower().zfill(32) == chain_trace.trace_id.lower()


def test_claims_agent_delegates_to_settlement_agent_in_the_same_trace(chain_trace):
    """The hop this file proves that nothing else measures directly (see
    module docstring / NOTES.md question 1): does claims-agent's outbound
    A2A call to settlement-agent carry the ambient trace context?
    HTTPXClientInstrumentor (bootstrap.py) instruments the httpx.AsyncClient
    delegation.A2ADelegate builds, injecting a traceparent header from
    whatever the ambient OTel context is at call time — which, thanks to
    the StarletteInstrumentor fix on the inbound side, is the real
    externally-injected context rather than a disconnected root. Verified by
    capture: settlement-agent's own entry span shows up as a genuine
    descendant of claims-agent's span in this same trace."""
    trace = chain_trace.trace
    assert "settlement-agent" in trace.services(), (
        f"settlement-agent never joined this trace; services present: "
        f"{sorted(trace.services())} — outbound A2A context propagation is broken"
    )

    by_id = {s.span_id: s for s in trace.spans}
    claims_ids = {s.span_id for s in trace.spans if s.service == "claims-agent"}
    settlement_spans = [s for s in trace.spans if s.service == "settlement-agent"]
    assert settlement_spans, "no settlement-agent spans in this trace"

    def ancestors(span):
        seen = set()
        current = span
        while current and current.parent_id and current.parent_id not in seen:
            seen.add(current.parent_id)
            yield current.parent_id
            current = by_id.get(current.parent_id)

    assert any(
        claims_ids & set(ancestors(s)) for s in settlement_spans
    ), "settlement-agent span does not descend from any claims-agent span"


def test_claims_agent_trace_has_no_unexpected_orphans(chain_trace):
    """The injected span ID is never itself a real span in this trace, so
    claims-agent's entry span (parented to it) is an unavoidable, legitimate
    orphan. Pinned to exactly that one span — operation *and* parent_id — so a
    genuine propagation defect anywhere else (settlement-agent's own entry
    span losing its parent, for instance) still fails this test.

    `assert_single_root` (the harness's usual check) cannot be used here — it
    would never hold for a synthetic root by construction. `trace.roots() ==
    []` is the positive complement: every span actually stored in this trace
    has *some* parent, and the only span without one (the injected root)
    never lands in Jaeger at all. Together with the orphan-exclusion
    assertion below, this pins "every span has a parent, and the only
    external one is the injected root" precisely, rather than leaving that
    half of the no-orphans claim unchecked."""
    trace = chain_trace.trace
    assert trace.roots() == [], (
        f"expected no real root spans (the trace's root is the synthetic, "
        f"never-collected injected span); found: {trace.roots()}"
    )
    orphans = trace.orphans()
    unexpected = [
        s
        for s in orphans
        if not (s.operation == ENTRY_SPAN and s.service == "claims-agent" and s.parent_id == chain_trace.span_id)
    ]
    assert not unexpected, (
        "orphan spans present beyond the expected synthetic root, context "
        f"propagation is broken: {[(s.service, s.operation, s.parent_id) for s in unexpected]}"
    )


def test_settlement_failure_reaches_the_caller_with_its_real_reason():
    """A settlement failure must reach claims-agent's own caller with the
    underlying reason intact — not flattened into a success, not swallowed.
    A negative estimatedAmount makes settlement-agent's decide_settlement
    raise ValueError("amount must not be negative"); a2a-sdk's request
    handler turns that unhandled exception into a JSON-RPC error carrying
    str(exc) (verified in settlement-agent's own request handling).
    delegation.A2ADelegate.send()
    re-raises that as RuntimeError("settlement-agent returned an error: ...");
    executor.py does not catch it, so claims-agent's own a2a-sdk request
    handler does the same thing one hop further out. This proves the whole
    round trip, not just delegation.py's unit test."""
    claim_id = f"CLM-TEST-{uuid.uuid4().hex[:8]}"
    result = file_claim(claim_id, -100)
    assert "error" in result, f"expected a JSON-RPC error, got: {result}"
    message = result["error"].get("message", "")
    assert "amount must not be negative" in message, (
        f"real failure reason was not preserved end to end: {message!r}"
    )


def test_all_four_backend_services_are_in_one_trace(chain_trace):
    """settlement-agent's MCP client (mcp_tools.py) injects params._meta
    (traceparent + baggage) into the MCP hop via the lower-level
    ClientSession.send_request() API, closing what would otherwise be a
    broken hop in the chain. Confirmed by capture: all four backend
    services land in the one trace this test originates."""
    expected = {"claims-agent", "settlement-agent", "payments-mcp", "payments-api"}
    services = chain_trace.trace.services()
    assert expected <= services, (
        f"expected one trace across the whole chain; missing: {sorted(expected - services)}"
    )


# --- RETIRED: test_payment_was_executed_with_no_governance ------------------
#
# This is a record of a retired test, not a cleanup artifact. Read it before
# writing anything like it again.
#
# **What it asserted.** Against this file's own €6,200 `chain_trace` fixture:
#
#     operations = {s.operation for s in chain_trace.trace.spans}
#     assert "tools/call execute_payment" in operations
#     api_span = next(
#         s for s in chain_trace.trace.spans
#         if s.service == "payments-api" and "execute" in s.operation
#     )
#     assert api_span.attrs.get("http.response.status_code") == 200
#
# i.e. payments-mcp really received the execute call, payments-api really
# executed it and answered 200, and nowhere in that chain was there a policy
# check: a model's request alone moved €6,200 of somebody else's money, and
# the suite was green about it. It was an honest record of a deliberate
# absence of governance.
#
# **That absence no longer exists, so the test was falsified on purpose.**
# The PDS denies `execute_payment` above the €6,000 allowance for
# settlement-agent (`platform/policy-decision-service/pds/rules.py`), the
# gateway's `mcpGuardrails` hook enforces that decision on every
# `tools/call`, and at €6,200 the execution never leaves the gateway: no
# `tools/call execute_payment` span at payments-mcp, no `POST
# /payments/{paymentId}/execute` at payments-api, no forward CLIENT span at
# agentgateway. Both assertions above are now false, and the `next(...)`
# raises `StopIteration`.
#
# **Why it was retired rather than re-pointed.** Re-pointing the amount to
# €5,800 would have turned it green again — and green for the opposite reason
# to the one it was written for. The payment would go through *because a rule
# permits it*, not because no rule exists, so the test would report the same
# colour whether the platform had governance or had none: the "green and red
# mean the same thing" defect class this suite guards against elsewhere (see
# `tests/e2e/test_policy_denial.py::test_criterion_5_...`'s design note). Its
# premise — the absence of governance — is not a thing that can be re-pointed
# once that absence is gone.
#
# **What supersedes it.** `tests/e2e/test_policy_denial.py`, which asserts the
# true behaviour at both ends of the same one rule: criterion 1, that
# €6,200 is denied and no payment executes (the exact absences listed above,
# read off the platform's own `policy.decision` span rather than off prose),
# and criterion 6, that €5,800 still executes end to end under that same rule
# name. Between them they cover everything this test covered and the thing it
# structurally could not: that the platform is deciding, rather than either
# permitting everything or refusing everything.
#
# `test_all_four_backend_services_are_in_one_trace` above keeps the trace-
# completeness half of what this file proves, and still holds — the propose leg
# alone lands spans in all four services.
# -----------------------------------------------------------------------------
