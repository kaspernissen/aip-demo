"""MCP client for payments-mcp.

settlement-agent is the only agent permitted to reach payments-mcp. That
boundary is configuration here and enforcement at the gateway.

Two different things cross this boundary, in two different carriers, and they
are separate concerns that happen to share a hop:

  * Trace context rides in the JSON-RPC `params._meta` bag (SEP-414), injected
    below via the standard propagator. It is about *correlation* -- it says
    which trace this call belongs to and nothing about who is calling.
  * Identity rides in an HTTP `Authorization: Bearer` header, as a JWT-SVID
    fetched from svid.py. It is about *authentication* -- it says who is
    calling and nothing about the trace.

Neither substitutes for the other, and neither may be folded into the other.
Trace context is not a credential (it is attacker-supplied, and treating it as
identity would be a security bug); the SVID is not correlation (it says
nothing about the call). agentgateway happens to read both -- the header for
JWT verification, and, separately, the `traceparent` HTTP header that
HTTPX2ClientInstrumentor injects for the trace -- but as two independent
mechanisms.

Note what is *not* claimed here: this file does not hand-inject `traceparent`
into an HTTP header. That is the httpx instrumentation's job, done for every
outbound request in this process, and it is what carries the trace across the
gateway (tests/e2e/test_gateway_trace.py). Because `mcp`'s streamable-HTTP
client is built on `httpx2` rather than `httpx`, it is `HTTPX2ClientInstrumentor`
(applied in bootstrap.py) that injects `traceparent` on this hop -- deleting
it as "the anthropic-only instrumentor" would silently sever this hop.
`params._meta` is a second, MCP-native carrier for the same context.

This client goes through the lower-level `ClientSession.send_request()` with
a hand-built `CallToolRequest`, injecting the ambient OTel context (trace
context *and* baggage — whatever the global propagator produces) into
`params._meta` via the standard `opentelemetry.propagate.inject()` API,
because the high-level `ClientSession.call_tool()` has no way to set
`params._meta` and does not inject one automatically (confirmed empirically,
see NOTES.md and tests/e2e/test_payments_mcp_trace.py, which uses the same
technique as test-side infrastructure). This is the one piece of production
code in the repo that needs the lower-level request API rather than the
SDK's own convenience wrapper.
"""
from __future__ import annotations

import os
from typing import Any

import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from opentelemetry import propagate
from pydantic_ai import ToolFailed

from policy_denial_text import parse_deny
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
    """Raised when an MCP tool call comes back with is_error=True.

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


class PolicyDenial(ToolFailed):
    """The platform refused this call, and no retry will change that.

    This is the load-bearing type for policy refusals. The hazard it exists to
    prevent: "If a denial reads as a retryable tool error, the agent can sit
    on stage retrying a payment it will never be allowed to make."

    Three properties, each deliberate:

      * **It is a `pydantic_ai.ToolFailed`, so the model sees it.** PydanticAI
        turns a `ToolFailed` raised anywhere under a tool call into a
        `ToolReturnPart(outcome='failed')` carrying `.message`
        (`tool_manager.py::_wrap_error_as_failed`, read from the installed
        2.42.0). The model therefore reads the refusal and can narrate it,
        which is the whole point: without this, a denial would leave `_call`
        throwing a bare `MCPError`, propagated out of `Agent.run()`, and the
        A2A caller would get a JSON-RPC `-32603` where a customer-facing
        explanation belongs.
      * **It is emphatically NOT a `pydantic_ai.ModelRetry`.** `ModelRetry` is
        handed back to the model *as a correction prompt* and consumes the
        tool's retry budget (`_wrap_error_as_retry` -> `RetryPromptPart`) — the
        model is actively told to try again, which is the retry loop. Unlike
        `ModelRetry`, `ToolFailed` "does not prepend retry/correction
        instructions and does not consume the tool's retry budget" (its own
        docstring). `ModelRetry` and `ToolFailed` are siblings under
        `Exception`, so this is not a subclass by construction — and
        `test_a_policy_denial_is_not_a_ModelRetry` pins it anyway, because the
        two names are one keystroke apart.
      * **It carries the structured facts, not just prose.** agentgateway
        v1.6.0 drops `error.data`, so the rule, the limit and the requested
        amount come from the tag at the end of the deny text
        (`policy_denial_text.parse_deny`) and are kept as attributes.

    `retryable` is always False: a deny is final. What makes this exception
    terminal is the type, not the flag; the flag is evidence.
    """

    def __init__(
        self,
        message: str,
        *,
        rule: str | None = None,
        limit: float | None = None,
        requested: float | None = None,
        retryable: bool = False,
        body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.rule = rule
        self.limit = limit
        # `requested` is `None` when the PDS could not read an amount at all
        # (a hostile or unparseable argument): it emits `"requested": null` and
        # denies, keeping the body strictly valid JSON. Not an error here.
        self.requested = requested
        self.retryable = retryable
        self.body = body if body is not None else {}


def _leaf_exception(exc: BaseException) -> BaseException:
    """Recursively unwrap a (possibly nested) BaseExceptionGroup to a leaf
    exception, for surfacing through a JSON-RPC error message.

    Prefers a PolicyDenial leaf, then a ToolCallError leaf, if one is present
    anywhere in the group — that is the case this whole mechanism exists for,
    and it is the one whose text a caller actually needs to see. Falls back to
    the first leaf found if neither is present -- fine for a single-failure
    path; a real anyio cancellation/transport error ahead of one of them would
    otherwise win by accident of ordering.
    """
    if not isinstance(exc, BaseExceptionGroup):
        return exc
    leaves = [_leaf_exception(sub) for sub in exc.exceptions]
    for wanted in (PolicyDenial, ToolCallError):
        for leaf in leaves:
            if isinstance(leaf, wanted):
                return leaf
    return leaves[0] if leaves else exc


def _unwrap(result: Any) -> dict:
    """Extract the structured payload from a CallToolResult.

    `mcp`'s Python object exposes `CallToolResult.isError` as `is_error` and
    `.structuredContent` as `.structured_content`; the JSON field names on the
    wire are unchanged, because `isError`/`structuredContent` remain the
    pydantic aliases (read off `CallToolResult.model_fields` in the installed
    2.2.0, not from the changelog). So this is a rename in this process only —
    nothing about what payments-mcp sends changed.
    """
    if getattr(result, "is_error", False):
        detail = "; ".join(
            block.text
            for block in (result.content or [])
            if getattr(block, "text", None)
        )
        denial = parse_deny(detail)
        if denial is not None:
            raise PolicyDenial(
                f"policy refused this call: {denial.reason} (rule {denial.rule})"
                " — this refusal is final. Making the same call again will be "
                "refused again. Explain to the claimant what happened instead.",
                rule=denial.rule,
                limit=denial.limit,
                requested=denial.requested,
                retryable=False,
                body={"rule": denial.rule, "limit": denial.limit,
                      "requested": denial.requested, "decision": "deny"},
            )
        raise ToolCallError(detail or "tool call failed with no error detail")

    structured = getattr(result, "structured_content", None)
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
            # Three points about `mcp`'s `streamable_http_client`, each read
            # off the installed 2.2.0 source rather than the migration guide,
            # because getting the first one wrong would mean sending no
            # credential at all while every test still passed:
            #
            #   * `streamablehttp_client` (no underscore before "http") does
            #     not exist at this version; `streamable_http_client` is the
            #     current name.
            #   * It takes **no `headers=` parameter**. Its own docstring says
            #     so: "To configure headers, authentication, or other HTTP
            #     settings, create an httpx2.AsyncClient and pass it here."
            #     `create_mcp_http_client(headers=...)` is the SDK's own
            #     constructor for exactly that, and it is what applies the
            #     SDK's recommended MCP timeouts -- which the no-argument path
            #     would otherwise have applied for us. The client is entered as
            #     a context manager here because `streamable_http_client` only
            #     manages the lifecycle of a client it created itself
            #     (`client_provided` in its source); one passed in is ours to
            #     close.
            #   * It yields a **two**-tuple (`as (read, write)`), not a
            #     session-id callback as a third element.
            #
            # The client is still an ordinary HTTP client in this process, so
            # trace context still rides out in the `traceparent` header
            # injected by instrumentation rather than by this file. It is
            # `httpx2` rather than `httpx`, so the instrumentor that does it
            # is `HTTPX2ClientInstrumentor` -- already applied in
            # bootstrap.py, for `anthropic`. Both are applied there.
            async with create_mcp_http_client(
                headers={"Authorization": f"Bearer {svid}"},
            ) as http_client:
                async with streamable_http_client(
                    self._mcp_url,
                    http_client=http_client,
                ) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()

                        # Inject whatever the ambient OTel context carries
                        # (trace context, and baggage if any is set — e.g.
                        # northstar.claim.id) into a plain string carrier via
                        # the standard propagate.inject() API, exactly as
                        # HTTPXClientInstrumentor does for ordinary HTTP headers
                        # elsewhere in this repo. The only difference here is
                        # the carrier: params._meta (SEP-414's unprefixed
                        # traceparent/tracestate/baggage keys) rather than an
                        # HTTP header, because a JSON-RPC message can be relayed
                        # over transports that have no headers at all (stdio, a
                        # multiplexed stream), so the context has to travel with
                        # the message.
                        #
                        # This is emphatically NOT the carrier the JWT-SVID
                        # uses. The SVID goes in the HTTP
                        # Authorization header set above, one level down at the
                        # transport, and the two never mix -- see this module's
                        # docstring for why folding one into the other would be
                        # a security bug rather than a simplification.
                        carrier: dict[str, str] = {}
                        propagate.inject(carrier)

                        # `types.ClientRequest` is a plain union of request
                        # models, and `send_request` accepts a member of that
                        # union directly (its signature reads
                        # `types.ClientRequest | types.Request[Any, Any]`), so
                        # `request` below is passed as-is, with no wrapping.
                        #
                        # `params._meta` is the TypedDict `RequestParamsMeta`
                        # -- "an open map: arbitrary keys round-trip via
                        # `extra_items=Any`", its own docstring -- so the
                        # carrier dict IS the value passed to `_meta` below.
                        # The serialised wire shape, checked with
                        # `model_dump(by_alias=True, exclude_none=True)`
                        # against the installed 2.2.0:
                        # `{'method': 'tools/call', 'params': {'_meta':
                        # {'traceparent': ...}, 'name': ..., 'arguments': ...}}`.
                        request = types.CallToolRequest(
                            method="tools/call",
                            params=types.CallToolRequestParams(
                                name=tool,
                                arguments=arguments,
                                _meta=carrier if carrier else None,
                            ),
                        )
                        result = await session.send_request(
                            request, types.CallToolResult
                        )
                        return _unwrap(result)
        except BaseExceptionGroup as eg:
            # streamable_http_client and ClientSession are each backed by their
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
            #
            # A policy denial is no longer a JSON-RPC error: agentgateway
            # v1.6.0 returns it as an `isError` tool result, which `_unwrap`
            # turns into a PolicyDenial inside the `async with`, so it arrives
            # here as a leaf like any ToolCallError. A JSON-RPC error from the
            # gateway is therefore a malfunction and propagates as one.
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

    async def get(self, payment_id: str) -> dict:
        """Read back what the ledger recorded for a payment, through the same
        governed MCP path every other payments call in this repo takes, not a
        new HTTP path."""
        return await self._call("get_payment", {"payment_id": payment_id})

    async def refuse(
        self,
        payment_id: str,
        *,
        rule: str,
        limit: float,
        requested: float,
        narrative: str,
        refused_trace_id: str,
        refused_span_id: str,
        a2a_task_id: str = "",
    ) -> dict:
        """Record why a payout was refused, beside the payout (`POST
        /payments/{id}/refuse`, reached through the same governed MCP path
        every other payments call in this repo takes -- not a new HTTP path
        from this agent to payments-api).

        payments-api's refuse endpoint validates more than `rule`: it also
        rejects an already-refused payment and confirms `requested` against
        the recorded proposal. `refused_span_id` travels alongside
        `refused_trace_id` so a reader can name the settlement-agent span
        active when the refusal was recorded, not only the trace as a whole.
        `a2a_task_id` is the customer's A2A task, kept on the ledger so the
        reviewer-ui can resume it after a human decides.
        """
        return await self._call(
            "refuse_payment",
            {
                "payment_id": payment_id,
                "rule": rule,
                "limit": limit,
                "requested": requested,
                "narrative": narrative,
                "refused_trace_id": refused_trace_id,
                "refused_span_id": refused_span_id,
                "a2a_task_id": a2a_task_id,
            },
        )
