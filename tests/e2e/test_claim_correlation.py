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
    response = httpx.post(
        f"{CLAIMS_URL}/", json=payload, headers={"traceparent": traceparent}, timeout=120
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Confirmed upstream gap (see module docstring / tools/payments-mcp/"
        "NOTES.md): FastMCP 4.0.3's extract_trace_context() only copies "
        "traceparent/tracestate out of params._meta, never baggage, even "
        "though mcp_tools.py's client injects a baggage key per SEP-414. "
        "The MCP hop still joins the *trace* (traceparent crosses fine — see "
        "the six flipped xfails elsewhere in this suite); only the "
        "northstar.claim.id attribute itself does not reach payments-mcp or "
        "payments-api, which are correlated by trace ID instead (brief Step "
        "11's own sanctioned fallback). Flip this and delete it the day "
        "FastMCP starts propagating baggage through params._meta."
    ),
)
def test_claim_id_is_on_every_mcp_hop_span(correlated_trace):
    assert_attr_on_all_spans(correlated_trace.trace, CLAIM_ID_KEY, services=MCP_GAP_SERVICES)
