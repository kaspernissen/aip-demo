"""Asserts northstar.claim.id correlation, at the boundary it actually reaches.

Task 7's brief (Step 9) sketched one blanket assertion — northstar.claim.id
present on every span across {claims-agent, settlement-agent, payments-mcp,
payments-api}. Two structural facts, both verified by live capture (see
agents/claims-agent/NOTES.md, agents/settlement-agent/NOTES.md,
tools/payments-mcp/NOTES.md, and this task's report for the raw evidence),
mean that blanket claim is not achievable, and this file asserts the real,
capture-verified boundary instead of a weakened or hopeful version of it
(A19 — this is a plan defect, recorded here and in the task report, not
silently patched around):

1. **claims-agent's own spans are split across two asyncio tasks.** a2a-sdk's
   DefaultRequestHandler runs AgentExecutor.execute() in its own producer task
   (asyncio.create_task, in default_request_handler.py) *before* that task has
   parsed the claim and attached northstar.claim.id to its own context.
   Baggage attached inside one task is invisible to a sibling task whose own
   copy of the ambient context was captured earlier — confirmed with an
   isolated reproduction against a2a-sdk's own DefaultRequestHandler (see the
   task report). Concretely:
   - claims-agent's ASGI entry span ("POST /") and every a2a-sdk-owned
     request-handling/event-queue/consumer span run in the "main" task a2a-sdk
     manages, which predates the attach — the same structural reason
     test_browser_root_span.py's browser root span predates the claim id, one
     layer deeper in the stack.
   - Only spans created directly inside the executor's own coroutine *after*
     the attach carry it: the outbound A2A CLIENT span to settlement-agent,
     and the EventQueue.enqueue_event call for the final JSON reply.
   This is not a decoration gap to close: a2a-sdk's own spans are third-party
   telemetry this project does not modify or invent attributes for, and the
   task boundary is intrinsic to a2a-sdk's execution model (0.3.7), not a bug
   in correlation.py or executor.py.

2. **FastMCP 4.0.3 does not propagate baggage through params._meta.** Read
   directly from the installed package: `fastmcp/telemetry.py`'s
   `extract_trace_context()` only ever copies the `traceparent`/`tracestate`
   keys out of `_meta` into its carrier before calling `propagate.extract()` —
   never `baggage`, even though mcp_tools.py's client now injects a `baggage`
   key into `_meta` per SEP-414 (see agents/settlement-agent/mcp_tools.py).
   So the MCP hop joins the *trace* (traceparent crosses correctly — proven
   by the six flipped xfails in test_claims_chain_trace.py and
   test_settlement_agent_trace.py), but northstar.claim.id never reaches
   payments-mcp's own spans, and therefore never reaches its outbound call to
   payments-api either. This is the "fourth candidate upstream gap" the
   brief's Step 11 anticipated; per its own instruction, these two services
   are correlated by trace ID (already proven elsewhere in this suite), not
   by the attribute — the gap is recorded, not faked.
"""
import json
import os
import uuid
from types import SimpleNamespace

import httpx
import pytest

from tests.harness.jaeger import JaegerClient, assert_attr_on_all_spans

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
CLAIMS_URL = os.environ.get("CLAIMS_AGENT_URL", "http://localhost:19001")

CLAIM_ID_KEY = "northstar.claim.id"
ENTRY_SPAN = "POST /"  # StarletteInstrumentor's ASGI server span (A18)

# Every span in these services carries northstar.claim.id, confirmed by
# capture: settlement-agent receives it via an inbound baggage header
# (extracted at the ASGI boundary before any of its own internal tasks
# spawn), so every task it later spawns inherits it.
FULLY_CORRELATED_SERVICES = {"settlement-agent"}

# The MCP hop and everything downstream of it: trace-ID-joined (proven
# elsewhere), never attribute-correlated, per the confirmed FastMCP gap above.
MCP_GAP_SERVICES = {"payments-mcp", "payments-api"}


@pytest.fixture(scope="module")
def claim_id() -> str:
    return f"CLM-TEST-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def correlated_trace(claim_id):
    # A14: this test originates the request, so it mints its own trace/span
    # ID and sends it as a traceparent header, then looks the trace up by
    # that exact ID — never by service search. (The brief's Step 9 sketch
    # used a plain wait_for_trace(service="claims-agent") search; per A14
    # that is unsound whenever the test itself originates the request, so
    # this fixture uses wait_for_trace_id instead — another brief defect,
    # recorded per A19.)
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    traceparent = f"00-{trace_id}-{span_id}-01"

    payload = {
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
                                "estimatedAmount": 6200,
                            }
                        ),
                    }
                ],
            }
        },
    }
    # 180s, matching every other call site that drives a settlement: since M1.2
    # this is three sequential model turns plus two MCP round trips, not one
    # hardcoded function call. Observed latency is 2.6-7.9s, so the margin is
    # for the Anthropic SDK's exponential backoff after a 429/529 on a slow
    # uplink -- conference wifi. A timeout here raises inside a module-scoped
    # fixture, which reports every test in the file as ERROR with a traceback
    # rather than one named failure.
    response = httpx.post(
        f"{CLAIMS_URL}/", json=payload, headers={"traceparent": traceparent}, timeout=180
    )
    response.raise_for_status()
    assert "error" not in response.json(), response.text

    client = JaegerClient(JAEGER_URL)
    # A20: wait for the whole reachable chain to land and hold steady. All
    # four backend services join this *trace* (traceparent crosses every
    # hop, including MCP) even though only some of them gain the attribute.
    trace = client.wait_for_trace_id(
        trace_id,
        operation=ENTRY_SPAN,
        timeout=120,
        services={"claims-agent", "settlement-agent", "payments-mcp", "payments-api"},
    )
    return SimpleNamespace(trace=trace, trace_id=trace_id, span_id=span_id, claim_id=claim_id)


def test_correlated_trace_entry_span_is_parented_to_the_injected_context(correlated_trace):
    """A14's other half: minting trace_id/span_id and injecting a traceparent
    header is only sound evidence of propagation if something actually
    checks that the resulting entry span is a *child* of the injected span
    id, not merely that a trace with the injected trace_id exists (Jaeger
    would happily return an unrelated orphan sharing the same trace_id if
    propagation were silently broken at the very first hop). Mirrors
    test_claims_chain_trace.py's identical idiom for the same claims-agent
    entry span. Per A17, the one orphan this whole chain is expected to
    contain is pinned precisely (operation and parent_id), not tolerated by
    a bare count or name allowlist."""
    trace = correlated_trace.trace
    entry = trace.span(ENTRY_SPAN, service="claims-agent")
    assert entry.parent_id == correlated_trace.span_id, (
        f"{ENTRY_SPAN!r} span's parent is {entry.parent_id!r}, expected the "
        f"injected span id {correlated_trace.span_id!r} — inbound trace "
        "context was not honoured at the very first hop"
    )

    unexpected = [
        s
        for s in trace.orphans()
        if not (
            s.operation == ENTRY_SPAN
            and s.service == "claims-agent"
            and s.parent_id == correlated_trace.span_id
        )
    ]
    assert not unexpected, (
        "orphan spans present beyond the expected synthetic root (the "
        f"injected traceparent's own span id): {[(s.service, s.operation, s.parent_id) for s in unexpected]}"
    )


def test_claim_id_is_on_every_settlement_agent_span(correlated_trace):
    """settlement-agent receives northstar.claim.id via an inbound baggage
    header (from claims-agent's outbound A2A call), extracted at the ASGI
    boundary before any of settlement-agent's own internal a2a-sdk tasks
    spawn — so, unlike claims-agent (see the module docstring), every span
    settlement-agent produces for this request carries it, with no
    exceptions to pin."""
    assert_attr_on_all_spans(
        correlated_trace.trace, CLAIM_ID_KEY, services=FULLY_CORRELATED_SERVICES
    )


def test_claim_id_reaches_claims_agents_own_spans_created_after_intake(correlated_trace):
    """The two spans claims-agent's own code creates directly, inside the
    executor's own coroutine, after set_claim_id/otel_context.attach runs:
    the outbound A2A CLIENT span to settlement-agent, and the
    EventQueue.enqueue_event call carrying the final JSON reply. Both are
    proven present by capture; see the module docstring for why the ASGI
    entry span and a2a-sdk's own request-handling spans are not expected to
    carry it (they run in a task a2a-sdk spawned before the attach)."""
    trace = correlated_trace.trace

    outbound = [
        s
        for s in trace.spans
        if s.service == "claims-agent"
        and s.kind == "client"
        and "settlement-agent" in (s.attrs.get("url.full") or "")
    ]
    assert outbound, "no outbound CLIENT span from claims-agent to settlement-agent found"
    for span in outbound:
        assert span.attrs.get(CLAIM_ID_KEY) == correlated_trace.claim_id, (
            f"outbound A2A span missing/wrong {CLAIM_ID_KEY}: {span.attrs}"
        )

    enqueue = trace.span(
        "a2a.server.events.event_queue.EventQueue.enqueue_event", service="claims-agent"
    )
    assert enqueue.attrs.get(CLAIM_ID_KEY) == correlated_trace.claim_id, (
        f"claims-agent's enqueue_event span missing/wrong {CLAIM_ID_KEY}: {enqueue.attrs}"
    )


def test_claim_id_value_is_correct(correlated_trace):
    """Wherever the attribute does appear (settlement-agent throughout,
    claims-agent's two post-intake spans), its value must be exactly this
    request's claim id — never a stale or default value."""
    values = {
        s.attrs[CLAIM_ID_KEY] for s in correlated_trace.trace.spans if CLAIM_ID_KEY in s.attrs
    }
    assert values == {correlated_trace.claim_id}


def test_trace_is_findable_by_claim_id(correlated_trace):
    """Search by claim id is the operator workflow this attribute exists for.
    claims-agent's own enqueue_event span carries the tag (see
    test_claim_id_reaches_claims_agents_own_spans_created_after_intake), so a
    tag search against claims-agent finds this exact trace."""
    client = JaegerClient(JAEGER_URL)
    found = client.find_trace(
        service="claims-agent", tags={CLAIM_ID_KEY: correlated_trace.claim_id}
    )
    assert found is not None
    assert found.trace_id == correlated_trace.trace.trace_id


def test_claim_id_is_on_every_mcp_hop_span(correlated_trace):
    """Confirmed upstream gap (see module docstring / tools/payments-mcp/
    NOTES.md): FastMCP 4.0.3's extract_trace_context() only copies
    traceparent/tracestate out of params._meta, never baggage, even though
    mcp_tools.py's client injects a baggage key per SEP-414. The MCP hop
    still joins the *trace* (traceparent crosses fine — see the six flipped
    xfails elsewhere in this suite); only the northstar.claim.id attribute
    itself does not reach payments-mcp or payments-api, which are correlated
    by trace ID instead (brief Step 11's own sanctioned fallback).

    This is a manual xfail, not `@pytest.mark.xfail(strict=True)` (review
    finding, should-fix 1): a bare decorator on a test that depends on the
    module-scoped `correlated_trace` fixture also catches exceptions raised
    during that fixture's *own* setup (pytest's xfail machinery covers the
    setup phase, not just the call phase) — so if the MCP hop were severed
    entirely and `wait_for_trace_id` timed out before the chain ever
    assembled, that would be reported as a quiet `xfailed` too, identical to
    this test's own documented condition, even though the fixture never got
    far enough to run this test's body at all. Catching only `AssertionError`
    from this one call keeps the blast radius to exactly the condition this
    test documents: if the fixture itself fails, there is no marker here to
    intercept it, so it surfaces as a loud `ERROR`, not `xfailed`. If the
    assertion below ever stops failing (FastMCP starts propagating baggage),
    the `else` branch below fails loudly too, the same way
    `xfail(strict=True)`'s XPASS-becomes-fail behaviour would — narrowing
    only ever adds strictness here, never removes it.
    """
    try:
        assert_attr_on_all_spans(correlated_trace.trace, CLAIM_ID_KEY, services=MCP_GAP_SERVICES)
    except AssertionError:
        pytest.xfail(
            "Confirmed upstream gap: FastMCP 4.0.3's extract_trace_context() "
            "never copies baggage out of params._meta. Flip this and delete "
            "the try/except the day FastMCP starts propagating baggage "
            "through params._meta."
        )
    else:
        pytest.fail(
            "northstar.claim.id now reaches every MCP-hop span — the "
            "FastMCP baggage gap this test documents appears to be fixed "
            "upstream. Replace this try/except with a plain assertion, and "
            "update the module docstring, agents/settlement-agent/NOTES.md "
            "§11, and tools/payments-mcp/NOTES.md §6, which all still "
            "describe the gap as open."
        )
