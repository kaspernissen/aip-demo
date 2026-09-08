"""Asserts the MCP hop is traced and joined to the payments-api span.

Trace lookup (A14 / A14a): this test originates its own MCP `execute_payment`
call, so it mints its own W3C trace context and injects it — not as an HTTP
header (there is no `traceparent` header across an MCP boundary), but into
the JSON-RPC `params._meta` field, which is where MCP carries trace context
(SEP-414). `mcp==1.9.4`'s high-level `ClientSession.call_tool()` has no way to
set `_meta`, so the injected call goes through the lower-level
`ClientSession.send_request()` with a hand-built `CallToolRequest` whose
`params._meta = {"traceparent": ...}` (via `RequestParams.Meta`, which allows
extra fields). The trace is then looked up by the exact ID this test
generated (`wait_for_trace_id`), never by service/operation search — a search
was proven live to return an unrelated trace from 14 minutes earlier (see
task-3-review.md finding 1), which would pass even with propagation broken.

This is test-side infrastructure only: it proves FastMCP's *server-side*
extraction of `params._meta.traceparent` works (see the parent-ID assertion
below and NOTES.md), not that our production MCP client (settlement-agent)
injects it — that remains Task 7's job. No HTTP-header workaround is used
anywhere.

**S5 (final-review.md):** `contracts/mcp/payments-mcp.json` — spec §7's
"source of truth" for this tool's schema — had drifted from what the live
server actually serves: it claimed `amount: {minimum: 0}`, `currency:
{enum: ["EUR"]}`, and no `additionalProperties`, none of which FastMCP's
auto-generated schema (from the plain Python type hints in `server.py`)
actually produces, and nothing compared them. A one-time correction to the
file would only prevent today's drift, not tomorrow's — the agent-card half
of `contracts/` is already diffed against the live server this way
(`test_claims_chain_trace.py`/`test_settlement_agent_trace.py`), and the MCP
tool schema deserved the same guard, not a one-off fix. `test_tool_schema_matches_the_committed_contract`
below does that: it diffs the *whole* served `list_tools()` document against
the committed file, the same pattern the agent-card tests already use.
"""
import json
import os
import uuid
from pathlib import Path

import pytest
import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from tests.harness.jaeger import JaegerClient, assert_span_kind

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
MCP_URL = os.environ.get("PAYMENTS_MCP_URL", "http://localhost:18000/mcp")

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_CONTRACT = REPO_ROOT / "contracts" / "mcp" / "payments-mcp.json"


async def _call_tools(claim_id: str, traceparent: str) -> dict:
    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            proposed = await session.call_tool(
                "propose_payment",
                {"claim_id": claim_id, "amount": 6200, "currency": "EUR"},
            )
            payment = proposed.structuredContent or {}
            payment_id = payment.get("paymentId") or payment.get("result", {}).get("paymentId")

            # The high-level call_tool() has no way to set params._meta, so the
            # execute call — the one this test looks its trace up by — goes
            # through the lower-level request API with a hand-built _meta.
            request = types.ClientRequest(
                types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(
                        name="execute_payment",
                        arguments={"payment_id": payment_id},
                        _meta=types.RequestParams.Meta(traceparent=traceparent),
                    ),
                )
            )
            executed = await session.send_request(request, types.CallToolResult)
            return {"payment_id": payment_id, "executed": executed}


@pytest.fixture(scope="module")
def executed_call():
    import anyio

    claim_id = f"CLM-TEST-{uuid.uuid4().hex[:8]}"

    # Mint our own W3C trace context and inject it into params._meta on the
    # execute call, so we can look this exact call's trace up by ID afterwards
    # instead of searching for it.
    trace_id = uuid.uuid4().hex  # 32 hex chars, non-zero (uuid4 version nibble)
    span_id = uuid.uuid4().hex[:16]  # 16 hex chars, non-zero for the same reason
    traceparent = f"00-{trace_id}-{span_id}-01"

    anyio.run(_call_tools, claim_id, traceparent)

    client = JaegerClient(JAEGER_URL)
    trace = client.wait_for_trace_id(
        trace_id, operation="tools/call execute_payment", timeout=90
    )
    # wait_for_trace_id returns as soon as the *matching* span (the one named
    # in `operation`) is visible; it does not wait for that span's
    # descendants exported by a different process (payments-api's own
    # BatchSpanProcessor) to have landed too. Empirically this lag is real
    # and not fully eliminated by wait_for_trace_id's own poll interval — an
    # earlier version of this fixture without this step flaked on
    # test_mcp_span_is_parent_of_payments_api_span. Poll the same trace ID by
    # GET (the harness's existing public method — no change to jaeger.py)
    # until the payments-api leg is present.
    import time

    deadline = time.time() + 30
    while time.time() < deadline:
        if any(
            s.service == "payments-api" and "execute" in s.operation
            for s in trace.spans
        ):
            break
        time.sleep(1)
        trace = client.get_trace(trace_id)
    return trace, trace_id, span_id


@pytest.fixture(scope="module")
def mcp_trace(executed_call):
    trace, _trace_id, _span_id = executed_call
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


def test_tool_call_span_is_parented_to_the_injected_context(executed_call):
    # This is the strengthened version of "a span matching my tag exists
    # somewhere": it proves this specific request produced this specific
    # span, parented to the exact span ID this test injected via
    # params._meta, inside the exact trace ID this test injected. That proves
    # FastMCP's server-side _meta extraction actually works, not merely that
    # a plausible-looking span exists somewhere.
    trace, trace_id, span_id = executed_call
    span = trace.span("tools/call execute_payment")
    assert span.parent_id == span_id, (
        f"tools/call execute_payment span's parent is {span.parent_id!r}, "
        f"expected the injected span id {span_id!r} — the server did not "
        f"extract our traceparent from params._meta"
    )
    # Jaeger's TraceID.String() drops a whole-number leading zero nibble (see
    # model.TraceID.String() in jaeger-idl) when the high 64 bits start with a
    # zero hex digit, so compare normalized rather than requiring an exact
    # 32-char match (same normalization as tests/e2e/test_payments_api_trace.py).
    assert trace.trace_id.lower().lstrip("0") == trace_id.lower().lstrip("0")


def test_mcp_span_is_parent_of_payments_api_span(mcp_trace):
    """Guards downstream propagation from payments-mcp to payments-api.

    This is the ordinary W3C traceparent-over-HTTP-header hop (httpx
    instrumentation -> otelhttp), not the params._meta boundary itself —
    that one is guarded by test_tool_call_span_is_parented_to_the_injected_context
    above. Both must hold for the chain to be intact end to end.
    """
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


def test_trace_has_no_unexpected_orphans(mcp_trace, executed_call):
    # The entry span's declared parent is the span ID *this test* invented and
    # injected via params._meta — it was never a real span anywhere, so by
    # `Trace.orphans()`'s definition (declared parent absent from the trace)
    # it is unavoidably reported as an orphan. That is the intended shape of
    # an externally-rooted trace, not a propagation defect (the same reason
    # tests/e2e/test_payments_api_trace.py, which injects a synthetic parent
    # the same way, never calls assert_no_orphans either). What still matters,
    # and is still checked here: no *other* span in the trace is an orphan —
    # that would mean a real internal hop (e.g. payments-mcp -> payments-api)
    # dropped context, which this test must still catch.
    _trace, _trace_id, injected_span_id = executed_call
    orphans = mcp_trace.orphans()
    unexpected = [
        s
        for s in orphans
        if not (s.operation == "tools/call execute_payment" and s.parent_id == injected_span_id)
    ]
    assert not unexpected, (
        "orphan spans present, context propagation is broken: "
        f"{[(s.service, s.operation, s.parent_id) for s in unexpected]}"
    )


def test_span_kinds_are_correct(mcp_trace):
    assert_span_kind(mcp_trace, "tools/call execute_payment", "server")


def test_tool_schema_matches_the_committed_contract():
    """S5 (final-review.md): diffs the whole served tool list against
    contracts/mcp/payments-mcp.json, so a served/contract drift (a changed
    type hint, a new/removed parameter, an annotation flip) fails here
    instead of shipping silently — mirroring the agent-card diff tests
    (test_claims_chain_trace.py/test_settlement_agent_trace.py) rather than
    leaving this file's schema as a one-time, unguarded correction."""
    import anyio

    async def _list_tools():
        async with streamablehttp_client(MCP_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.inputSchema,
                        "annotations": (
                            tool.annotations.model_dump(by_alias=True, exclude_none=True)
                            if tool.annotations
                            else None
                        ),
                    }
                    for tool in result.tools
                ]

    served = anyio.run(_list_tools)
    contract = json.loads(MCP_CONTRACT.read_text())
    served_by_name = {t["name"]: t for t in served}
    contract_by_name = {t["name"]: t for t in contract["tools"]}

    assert served_by_name.keys() == contract_by_name.keys(), (
        f"tool set drifted: served {sorted(served_by_name)}, "
        f"contract {sorted(contract_by_name)}"
    )
    for name, contract_tool in contract_by_name.items():
        served_tool = served_by_name[name]
        for field in ("description", "inputSchema", "annotations"):
            assert served_tool[field] == contract_tool[field], (
                f"{name}.{field} drifted from the committed contract "
                f"({MCP_CONTRACT}):\nserved:   {served_tool[field]!r}\n"
                f"contract: {contract_tool[field]!r}"
            )
