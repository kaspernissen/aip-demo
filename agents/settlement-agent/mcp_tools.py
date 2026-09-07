"""MCP client for payments-mcp.

settlement-agent is the only agent permitted to reach payments-mcp. That
boundary is configuration here and enforcement at the gateway in M2.

Trace context crosses this boundary in the JSON-RPC params._meta bag, handled
by the MCP SDK. Nothing is injected into HTTP headers by hand.

Task 7: mcp==1.9.4's high-level ClientSession.call_tool() (used until this
task) has no way to set params._meta, and does not inject one automatically
(confirmed empirically, see NOTES.md and tests/e2e/test_payments_mcp_trace.py,
which used the same technique as test-side infrastructure). This client now
goes through the lower-level ClientSession.send_request() with a hand-built
CallToolRequest instead, injecting the ambient OTel context (trace context
*and* baggage — whatever the global propagator produces) into
params._meta via the standard `opentelemetry.propagate.inject()` API. This is
the one piece of production code in the repo that needs the lower-level
request API rather than the SDK's own convenience wrapper; see this task's
report for whether that gap is upstream-contribution evidence.
"""
from __future__ import annotations

from typing import Any

import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from opentelemetry import propagate


class ToolCallError(RuntimeError):
    """Raised when an MCP tool call comes back with isError=True.

    payments-api's 404 (payment not found) and 409 (already executed) surface
    here as the tool's own error text (verified empirically against the
    deployed payments-mcp — see NOTES.md), not as a transport-level
    exception. Raising this with that text, rather than swallowing the
    failure or returning a placeholder success, lets it reach the caller: an
    unhandled exception from AgentExecutor.execute() is turned into a
    JSON-RPC error response by the a2a-sdk's own request handler (verified
    empirically), so the caller sees the real reason, not a flattened
    "executed" status.
    """


def _leaf_exception(exc: BaseException) -> BaseException:
    """Recursively unwrap a (possibly nested) BaseExceptionGroup to a leaf
    exception, for surfacing through a JSON-RPC error message.

    Prefers a ToolCallError leaf if one is present anywhere in the group —
    that is the case this whole mechanism exists for, and it is the one
    whose text a caller actually needs to see. Falls back to the first leaf
    found if no ToolCallError is present (fine for M1.1's single-failure
    paths; a real anyio cancellation/transport error ahead of a ToolCallError
    would previously have won by accident of ordering — see review nit 9).
    """
    if not isinstance(exc, BaseExceptionGroup):
        return exc
    leaves = [_leaf_exception(sub) for sub in exc.exceptions]
    for leaf in leaves:
        if isinstance(leaf, ToolCallError):
            return leaf
    return leaves[0] if leaves else exc


def _unwrap(result: Any) -> dict:
    """Extract the structured payload from a CallToolResult."""
    if getattr(result, "isError", False):
        detail = "; ".join(
            block.text
            for block in (result.content or [])
            if getattr(block, "text", None)
        )
        raise ToolCallError(detail or "tool call failed with no error detail")

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # FastMCP wraps scalar and dict returns under "result" in some versions.
        return structured.get("result", structured)
    raise ValueError(f"tool call returned no structured content: {result!r}")


class PaymentsTools:
    def __init__(self, mcp_url: str):
        self._mcp_url = mcp_url

    async def _call(self, tool: str, arguments: dict) -> dict:
        try:
            async with streamablehttp_client(self._mcp_url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    # Inject whatever the ambient OTel context carries
                    # (trace context, and baggage if any is set — e.g.
                    # northstar.claim.id) into a plain string carrier via the
                    # standard propagate.inject() API, exactly as
                    # HTTPXClientInstrumentor does for ordinary HTTP headers
                    # elsewhere in this repo. The only difference here is the
                    # carrier: params._meta (SEP-414's unprefixed
                    # traceparent/tracestate/baggage keys), not an HTTP
                    # header, because there is no HTTP header across an MCP
                    # JSON-RPC boundary.
                    carrier: dict[str, str] = {}
                    propagate.inject(carrier)

                    request = types.ClientRequest(
                        types.CallToolRequest(
                            method="tools/call",
                            params=types.CallToolRequestParams(
                                name=tool,
                                arguments=arguments,
                                _meta=types.RequestParams.Meta(**carrier) if carrier else None,
                            ),
                        )
                    )
                    result = await session.send_request(request, types.CallToolResult)
                    return _unwrap(result)
        except BaseExceptionGroup as eg:
            # streamablehttp_client and ClientSession are each backed by their
            # own anyio TaskGroup, so an exception raised inside the `async
            # with` body is wrapped once per nesting level on the way out
            # (confirmed empirically: a single ToolCallError arrives here as a
            # doubly-nested BaseExceptionGroup). Left alone, the a2a-sdk's
            # error handler would stringify the wrapper itself ("unhandled
            # errors in a TaskGroup"), burying the tool's real 404/409
            # message. `except*` is the wrong tool here — re-raising a plain
            # exception from inside an `except*` clause gets re-wrapped by
            # design (PEP 654) — so unwrap by hand instead and re-raise the
            # leaf exception the caller actually needs to see.
            raise _leaf_exception(eg) from eg

    async def propose(self, claim_id: str, amount: float, currency: str = "EUR") -> dict:
        return await self._call(
            "propose_payment",
            {"claim_id": claim_id, "amount": amount, "currency": currency},
        )

    async def execute(self, payment_id: str) -> dict:
        return await self._call("execute_payment", {"payment_id": payment_id})
