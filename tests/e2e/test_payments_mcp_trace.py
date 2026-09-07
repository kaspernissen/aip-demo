"""Asserts the MCP hop is traced and joined to the payments-api span.

This is the test that catches the params._meta propagation trap: if MCP context
were assumed to travel in HTTP headers, the payments-api span would land in a
separate trace and test_mcp_span_is_parent_of_payments_api_span would fail.

Trace lookup note (A14): this test's own process never sends a `traceparent`
— it drives the MCP tool calls through a plain (uninstrumented) `mcp` client,
so the trace ID is minted by payments-mcp itself when it receives the first
request, not by this test. The trace ID genuinely cannot be known up front,
so this falls into the `wait_for_trace` exception A14 carves out (the same
one the browser-rooted chain in Task 7 uses), rather than the
`wait_for_trace_id` path Task 2's test uses for a request it originates
itself.
"""
import os
import uuid

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from tests.harness.jaeger import JaegerClient, assert_no_orphans, assert_span_kind

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
MCP_URL = os.environ.get("PAYMENTS_MCP_URL", "http://localhost:18000/mcp")


async def _call_tools(claim_id: str) -> dict:
    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            proposed = await session.call_tool(
                "propose_payment",
                {"claim_id": claim_id, "amount": 6200, "currency": "EUR"},
            )
            payment = proposed.structuredContent or {}
            payment_id = payment.get("paymentId") or payment.get("result", {}).get("paymentId")
            executed = await session.call_tool("execute_payment", {"payment_id": payment_id})
            return {"payment_id": payment_id, "executed": executed}


@pytest.fixture(scope="module")
def mcp_trace():
    import anyio

    claim_id = f"CLM-TEST-{uuid.uuid4().hex[:8]}"
    anyio.run(_call_tools, claim_id)

    client = JaegerClient(JAEGER_URL)
    trace = client.wait_for_trace(
        service="payments-mcp", operation="tools/call execute_payment", timeout=90
    )
    # wait_for_trace returns as soon as the matching span is visible, which can
    # be before its BatchSpanProcessor-exported descendants (the outbound
    # httpx CLIENT span and the payments-api SERVER span) have landed in
    # Jaeger's store. Poll the same trace ID by GET until the payments-api leg
    # is present, using only the harness's public get_trace — no new method,
    # no change to jaeger.py beyond what A14 already added.
    import time

    deadline = time.time() + 30
    while time.time() < deadline:
        if any(
            s.service == "payments-api" and "execute" in s.operation
            for s in trace.spans
        ):
            break
        time.sleep(1)
        trace = client.get_trace(trace.trace_id)
    return trace


def test_tool_call_emits_a_span_named_per_mcp_semconv(mcp_trace):
    assert mcp_trace.has_span("tools/call execute_payment"), (
        "expected span name '{mcp.method.name} {target}' per the OTel MCP "
        f"semantic conventions; found: {sorted(s.operation for s in mcp_trace.spans)}"
    )


def test_tool_call_span_carries_mcp_semantic_conventions(mcp_trace):
    span = mcp_trace.span("tools/call execute_payment")
    assert span.attrs.get("mcp.method.name") == "tools/call"
    # Tool name lives in the GenAI namespace. mcp.tool.name does not exist.
    assert span.attrs.get("gen_ai.tool.name") == "execute_payment"
    assert "mcp.tool.name" not in span.attrs
    assert "mcp.session.id" in span.attrs


def test_mcp_span_is_parent_of_payments_api_span(mcp_trace):
    """Guards the params._meta propagation contract."""
    mcp_span = mcp_trace.span("tools/call execute_payment")
    descendants = {s.span_id: s for s in mcp_trace.spans}

    def is_descendant_of(span, ancestor_id):
        seen = set()
        current = span
        while current.parent_id and current.parent_id not in seen:
            if current.parent_id == ancestor_id:
                return True
            seen.add(current.parent_id)
            current = descendants.get(current.parent_id)
            if current is None:
                return False
        return False

    api_spans = [
        s for s in mcp_trace.spans
        if s.service == "payments-api" and "execute" in s.operation
    ]
    assert api_spans, "payments-api span is not in the same trace as the MCP span"
    assert is_descendant_of(api_spans[0], mcp_span.span_id)


def test_trace_has_no_orphans(mcp_trace):
    assert_no_orphans(mcp_trace)


def test_span_kinds_are_correct(mcp_trace):
    assert_span_kind(mcp_trace, "tools/call execute_payment", "server")
