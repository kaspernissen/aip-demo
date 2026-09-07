"""Asserts what the A2A hop actually traces today, and pins the confirmed
propagation gaps down as xfail rather than asserting behaviour that
empirically does not exist.

Chain under test: A2A client -> settlement-agent -> payments-mcp -> payments-api.

Two gaps were confirmed empirically against this exact deployed pod before
this test was written (full evidence, including verbatim trace IDs and span
attribute dumps, is in agents/settlement-agent/NOTES.md):

1. **a2a-sdk 0.3.7's telemetry does not extract inbound W3C trace context**
   from the A2A HTTP request. A `traceparent` header sent on the request is
   completely ignored: the resulting entry span
   (`JSONRPCHandler.on_message_send`) always starts a brand-new root trace
   with a fresh, unrelated trace ID, and carries zero attributes beyond OTel
   scope bookkeeping (no `gen_ai.*`, no context/task identity). A14's
   technique of minting a trace/span ID and asserting the entry span's
   `parent_id` therefore cannot be used here — this is not a testing gap,
   it's a genuine, verified SDK limitation (worth an upstream issue; see
   NOTES.md). Trace lookup below falls back to `wait_for_trace`
   (search-based) — per A14's own carve-out for a trace ID that "genuinely
   cannot be known up front," here forced by the SDK rather than by a
   browser origin as in Task 7's case.
2. **settlement-agent's MCP client does not inject `params._meta`.**
   `mcp==1.9.4`'s high-level `ClientSession.call_tool()` (used in
   `mcp_tools.py`, per this task's explicit scope) has no way to set it, and
   FastMCP only extracts trace context from `params._meta` — never from HTTP
   headers, confirmed in Task 3's NOTES.md — so payments-mcp's tool-call
   spans never join this trace, even though settlement-agent's own outbound
   HTTP request to payments-mcp *does* carry a `traceparent` header (from
   `HTTPXClientInstrumentor`'s ordinary ambient-context injection — see
   `test_settlement_agent_outbound_mcp_calls_are_traced` below). Making the
   client inject `_meta` is Task 7's job, not this file's or
   `mcp_tools.py`'s (see that module's own docstring).

Given both gaps, this file asserts what is real today (settlement-agent's own
trace is internally coherent — single root, no orphans — and its outbound
MCP-transport HTTP calls are correctly nested inside it) and marks the
not-yet-true cross-service assertions `xfail(strict=True)`: either gap
closing flips the matching test to an unexpected pass, which pytest reports
as a failure under `strict=True` — forcing this file to be revisited rather
than silently staying green while claiming less than it should.
"""
import json
import os
import time
import uuid

import httpx
import pytest

from tests.harness.jaeger import JaegerClient, assert_no_orphans, assert_single_root

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
SETTLEMENT_URL = os.environ.get("SETTLEMENT_AGENT_URL", "http://localhost:19002")


def send_message(text: str) -> dict:
    """Minimal A2A JSON-RPC message/send call.

    Uses raw JSON-RPC rather than the SDK client so the test asserts the wire
    contract, not the SDK's convenience layer. No `traceparent` header is
    sent: confirmed gap 1 above means one would be silently ignored, so
    sending one would only suggest a propagation attempt that isn't real.
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
    response = httpx.post(f"{SETTLEMENT_URL}/", json=payload, timeout=60)
    response.raise_for_status()
    return response.json()


def test_agent_card_is_served():
    response = httpx.get(f"{SETTLEMENT_URL}/.well-known/agent-card.json", timeout=15)
    response.raise_for_status()
    card = response.json()
    assert card["name"] == "settlement-agent"
    assert any(skill["id"] == "settle-claim" for skill in card["skills"])


@pytest.fixture(scope="module")
def settlement_trace():
    claim_id = f"CLM-TEST-{uuid.uuid4().hex[:8]}"
    result = send_message(
        json.dumps({"claimId": claim_id, "amount": 6200, "currency": "EUR"})
    )
    assert "error" not in result, f"A2A call failed: {result}"

    client = JaegerClient(JAEGER_URL)
    # Search-based, not by-ID (see module docstring, gap 1): a2a-sdk 0.3.7
    # does not extract an inbound traceparent header, so this test cannot
    # mint and inject its own trace ID the way A14 otherwise requires.
    # `wait_for_trace` has no lookback parameter (it always uses find_trace's
    # 1h default), so this polls `find_trace` directly with a short lookback
    # instead, to bound the residual risk A14 warns about (an unrelated
    # older trace matching the same service) — using the harness's existing
    # public method, not a change to jaeger.py.
    deadline = time.time() + 90
    trace = None
    while time.time() < deadline:
        trace = client.find_trace(service="settlement-agent", lookback="5m", limit=1)
        if trace is not None:
            break
        time.sleep(2)
    assert trace is not None, "no settlement-agent trace found within 90s"
    return trace


def test_settlement_agent_trace_is_internally_coherent(settlement_trace):
    """What's real today: settlement-agent's own trace has exactly one root
    and no orphans. This is genuine, in-process ambient-context propagation
    (the a2a-sdk's own spans and the httpx-instrumented outbound calls below
    share Python's current OTel context) — unrelated to either gap above,
    and would fail if that in-process propagation ever regressed."""
    assert_single_root(settlement_trace)
    assert_no_orphans(settlement_trace)


def test_settlement_agent_outbound_mcp_calls_are_traced(settlement_trace):
    """HTTPXClientInstrumentor (bootstrap.py, added for this task per A13)
    emits CLIENT spans for the MCP streamable-http transport's own HTTP
    calls to payments-mcp, nested inside the A2A request span. This proves
    this agent's own outbound HTTP hop is instrumented and uses the stable
    HTTP semconv spelling (A13) — even though the far side does not yet join
    the trace (gap 2)."""
    client_spans = [s for s in settlement_trace.spans if s.kind == "client"]
    assert client_spans, "no outbound CLIENT spans found for the MCP transport"
    for span in client_spans:
        target = span.attrs.get("url.full") or ""
        assert "payments-mcp" in target, f"unexpected outbound target: {span.attrs}"
        # Stable HTTP semconv spelling (A13): OTEL_SEMCONV_STABILITY_OPT_IN=http
        # is set on the deployment manifest specifically so these come out
        # this way rather than the legacy http.method/http.url/http.status_code.
        assert "http.request.method" in span.attrs
        assert "http.method" not in span.attrs


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Confirmed gap 2 (see module docstring / NOTES.md): settlement-agent's "
        "MCP client does not inject params._meta, so FastMCP's tool-call spans "
        "never join this trace. Flip once client-side _meta injection (Task 7) "
        "makes this true, and remove this marker."
    ),
)
def test_all_four_services_are_in_one_trace(settlement_trace):
    services = settlement_trace.services()
    assert {"settlement-agent", "payments-mcp", "payments-api"} <= services, (
        f"expected one trace spanning the chain; got services: {sorted(services)}"
    )


@pytest.mark.xfail(
    strict=True,
    reason="Same confirmed gap as test_all_four_services_are_in_one_trace.",
)
def test_mcp_tool_calls_are_present(settlement_trace):
    operations = {s.operation for s in settlement_trace.spans}
    assert "tools/call propose_payment" in operations
    assert "tools/call execute_payment" in operations


@pytest.mark.xfail(
    strict=True,
    reason="Same confirmed gap as test_all_four_services_are_in_one_trace.",
)
def test_settlement_agent_span_precedes_the_payment(settlement_trace):
    agent_spans = [s for s in settlement_trace.spans if s.service == "settlement-agent"]
    api_spans = [s for s in settlement_trace.spans if s.service == "payments-api"]
    assert agent_spans, "no settlement-agent spans"
    assert api_spans, "no payments-api spans"
    agent_ids = {s.span_id for s in agent_spans}
    # At least one span in the trace must descend from a settlement-agent span.
    by_id = {s.span_id: s for s in settlement_trace.spans}

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
