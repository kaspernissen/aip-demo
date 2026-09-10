"""MCP client for payments-mcp.

settlement-agent is the only agent permitted to reach payments-mcp. That
boundary is configuration here and enforcement at the gateway in M2.

Two different things cross this boundary, in two different carriers, and they
are separate concerns that happen to share a hop:

  * Trace context rides in the JSON-RPC `params._meta` bag (SEP-414), injected
    below via the standard propagator. It is about *correlation* -- it says
    which trace this call belongs to and nothing about who is calling.
  * Identity rides in an HTTP `Authorization: Bearer` header, as a JWT-SVID
    fetched from svid.py (M2.1 Task 4). It is about *authentication* -- it
    says who is calling and nothing about the trace.

Neither substitutes for the other, and neither may be folded into the other.
Trace context is not a credential (it is attacker-supplied, and treating it as
identity would be the security bug this milestone exists to avoid); the SVID
is not correlation (it says nothing about the call). agentgateway happens to
read both -- the header for JWT verification, and, separately, the
`traceparent` HTTP header that HTTPXClientInstrumentor injects for the trace
-- but as two independent mechanisms.

Note what is *not* claimed here: this file does not hand-inject `traceparent`
into an HTTP header. That is HTTPXClientInstrumentor's job, done for every
outbound httpx request in this process, and it is what carries the trace
across the gateway (tests/e2e/test_gateway_trace.py). `params._meta` is a
second, MCP-native carrier for the same context.

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

import os
from typing import Any

import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from opentelemetry import propagate

from svid import fetch_jwt_svid

# The audience every JWT-SVID this client presents is minted for: the
# gateway's own registered SPIFFE ID. Read from the environment
# (deploy/k8s/settlement-agent.yaml sets it) with the same literal as the
# fallback, matching how this service treats its other deployment-time
# configuration. It must equal agentgateway's jwtAuth `audiences` entry in
# deploy/platform/agentgateway/config.yaml -- one string, two ends of the
# same check.
_GATEWAY_AUDIENCE = os.getenv(
    "NORTHSTAR_GATEWAY_AUDIENCE",
    "spiffe://northstar.local/ns/northstar-platform/sa/agentgateway",
)


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
        # Identity first: a SPIRE-issued JWT-SVID for the gateway audience,
        # presented as an HTTP bearer token. Raises SvidUnavailable if the
        # Workload API is unreachable, and does so BEFORE any MCP traffic --
        # a call this workload cannot prove it is entitled to make should not
        # reach the gateway at all.
        #
        # `Authorization: Bearer <token>` is not a guess: it is
        # agentgateway v1.5.0's default credential location for jwtAuth,
        # `AuthorizationLocation::bearer_header()` -- header
        # `http::header::AUTHORIZATION`, prefix `"Bearer "` --
        # in crates/agentgateway/src/http/auth/mod.rs, which
        # crates/agentgateway/src/http/jwt.rs uses as the `location` default.
        svid = await fetch_jwt_svid(_GATEWAY_AUDIENCE)
        try:
            # `headers=` on this function IS live in mcp==1.28.1, despite two
            # same-named parameters nearby that are not -- checked against the
            # installed source, because getting this wrong would mean sending
            # no credential at all while every test still passed:
            #   * `streamablehttp_client(url, headers=...)` forwards headers
            #     straight to `httpx_client_factory(headers=headers, ...)`
            #     (streamable_http.py:709-713), which sets them as the
            #     httpx.AsyncClient's default headers. Per-request headers
            #     come from `_prepare_headers()`, which sets only accept,
            #     content-type, mcp-session-id and mcp-protocol-version, so
            #     Authorization is not overwritten.
            #   * `StreamableHTTPTransport.__init__`'s `headers` is explicitly
            #     deprecated-and-ignored ("Parameters ... are deprecated and
            #     will be ignored", streamable_http.py:95-99/127-139), and so
            #     is `RequestContext.headers` ("Deprecated - no longer used",
            #     line 84). Neither is the one used here.
            # `streamablehttp_client` itself carries
            # `@deprecated("Use `streamable_http_client` instead.")` at
            # 1.28.1 (line 685) -- pre-existing, unrelated to this change, and
            # migrating the transport is separate work: this call site is what
            # Task 3's surviving trace was measured against.
            async with streamablehttp_client(
                self._mcp_url,
                headers={"Authorization": f"Bearer {svid}"},
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    # Inject whatever the ambient OTel context carries
                    # (trace context, and baggage if any is set — e.g.
                    # northstar.claim.id) into a plain string carrier via the
                    # standard propagate.inject() API, exactly as
                    # HTTPXClientInstrumentor does for ordinary HTTP headers
                    # elsewhere in this repo. The only difference here is the
                    # carrier: params._meta (SEP-414's unprefixed
                    # traceparent/tracestate/baggage keys) rather than an HTTP
                    # header, because a JSON-RPC message can be relayed over
                    # transports that have no headers at all (stdio, a
                    # multiplexed stream), so the context has to travel with
                    # the message.
                    #
                    # M2.1 Task 4 note: this is emphatically NOT the carrier
                    # the JWT-SVID uses. The SVID goes in the HTTP
                    # Authorization header set above, one level down at the
                    # transport, and the two never mix -- see this module's
                    # docstring for why folding one into the other would be a
                    # security bug rather than a simplification.
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

    async def execute(self, payment_id: str, amount: float, currency: str = "EUR") -> dict:
        # The amount travels with the execution call rather than being looked
        # up from the id. An API that takes only an opaque id cannot be
        # governed at a choke point: agentgateway's policy hook sees the
        # `tools/call` arguments and nothing else, and payments-api can only
        # confirm an instruction that states what it is paying.
        return await self._call(
            "execute_payment",
            {"payment_id": payment_id, "amount": amount, "currency": currency},
        )
