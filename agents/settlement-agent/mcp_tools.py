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
into an HTTP header. That is the httpx instrumentation's job, done for every
outbound request in this process, and it is what carries the trace across the
gateway (tests/e2e/test_gateway_trace.py). `params._meta` is a second,
MCP-native carrier for the same context.

**Corrected 2026-09-11 (M3 remaining-bumps, step 1).** The paragraph above said
"That is HTTPXClientInstrumentor's job, done for every outbound httpx request
in this process". Half of that is now wrong and the half that matters is the
distribution: `mcp` 2.x's streamable-HTTP client is built on `httpx2`, not
`httpx`, so the instrumentor that injects `traceparent` on THIS hop is
`HTTPX2ClientInstrumentor`. Both are applied in bootstrap.py (the second has
been since M1.2 Task 8, for `anthropic`), so nothing had to be added -- but a
reader who deleted the `httpx2` one as "the anthropic one" would silently
sever this hop, which is why the correction is written down rather than left
as a stale sentence that still reads plausibly.

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
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.shared.exceptions import MCPError
from opentelemetry import propagate
from pydantic_ai import ToolFailed

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

    This is the load-bearing type of M2.2. The hazard it exists to prevent:
    "If a denial reads as a retryable tool error, the agent can sit on stage
    retrying a payment it will never be allowed to make."

    Three properties, each deliberate:

      * **It is a `pydantic_ai.ToolFailed`, so the model sees it.** PydanticAI
        turns a `ToolFailed` raised anywhere under a tool call into a
        `ToolReturnPart(outcome='failed')` carrying `.message`
        (`tool_manager.py::_wrap_error_as_failed`, read from the installed
        2.42.0 -- 2.41.0 until 2026-09-11, re-read at the bump and unchanged).
        The model therefore reads the refusal and can narrate it,
        which is the whole point: before this, a denial left `_call` as a bare
        `MCPError`, propagated out of `Agent.run()`, and the A2A caller got a
        JSON-RPC `-32603` where a customer-facing explanation belongs
        (measured in Task 4, §11 concern 4).
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
      * **It carries the structured facts, not just prose.** `str(MCPError)`
        yields only `error.message` and silently drops `error.data`, so a
        handler that re-raises the leaf loses the rule, the limit and the
        requested amount. Those are read off `exc.error.data` here and kept as
        attributes.

    `retryable` is carried verbatim from the PDS rather than assumed: the PDS
    emits `"retryable": false` unconditionally for a deny (its own
    `_result()` comment says why), and a deny that claimed to be retryable
    would be a contradiction at the source. What makes this exception terminal
    is the type, not the flag; the flag is evidence.
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


def _deny_marker(exc: BaseException) -> dict[str, Any] | None:
    """The PDS's structured deny payload, if `exc` carries one.

    Recognition is by the structured marker `decision == "deny"` and never by
    string-matching `error.message`. The message is prose written for a human
    (and for the model to narrate); it will be reworded, and a reworded message
    must not silently turn a terminal denial back into a retryable error.

    Measured shape (Task 1 finding 1, Task 3's body): agentgateway parses the
    PDS's `AuthorizationError.mcp_error` — itself a JSON-RPC error object —
    whole into `error.data`, so the marker sits one level down at
    `exc.error.data["data"]`. The un-nested position is accepted too, so this
    still recognises a denial if the gateway ever stops double-wrapping;
    both are structural checks for the same key, not a widening of what counts
    as a denial.
    """
    error = getattr(exc, "error", None)
    if error is None:
        return None
    data = getattr(error, "data", None)
    if not isinstance(data, dict):
        return None
    for candidate in (data.get("data"), data):
        if isinstance(candidate, dict) and candidate.get("decision") == "deny":
            return candidate
    return None


def _policy_denial(exc: BaseException) -> PolicyDenial | None:
    """Translate a policy refusal into a `PolicyDenial`, or return `None`.

    `None` for everything else, which is the regression guard that matters
    here: a transport error must stay a transport error. The PDS being
    *unreachable* also arrives as an `MCPError` ("mcpGuardrails checkRequest
    failed: upstream call failed: Connect: Connection refused", measured in
    Task 4's failClosed test) and carries no marker — that is an outage, not a
    decision, and reporting it as "policy said no" would be a lie the model
    would then narrate.
    """
    marker = _deny_marker(exc)
    if marker is None:
        return None

    # The narration text. `error.message` is where the PDS puts the
    # human-readable reason precisely because it is the one place
    # `str(MCPError)` preserves (R13); `str(exc)` is the same string, and is
    # the fallback if some other carrier ever grows a marker.
    reason = getattr(getattr(exc, "error", None), "message", None) or str(exc)
    rule = marker.get("rule")

    detail = f"policy refused this call: {reason}"
    if rule:
        detail += f" (rule {rule})"
    detail += (
        " — this refusal is final. Making the same call again will be refused "
        "again. Explain to the claimant what happened instead."
    )
    return PolicyDenial(
        detail,
        rule=rule,
        limit=marker.get("limit"),
        requested=marker.get("requested"),
        retryable=bool(marker.get("retryable", False)),
        body=marker,
    )


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
    """Extract the structured payload from a CallToolResult.

    M3 remaining-bumps step 1: `mcp` 2.x renamed both attributes read here.
    `CallToolResult.isError` is `is_error` and `.structuredContent` is
    `.structured_content` on the Python object; the JSON field names on the
    wire are unchanged, because 2.x keeps `isError`/`structuredContent` as the
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
            # M3 remaining-bumps step 1 (mcp 1.28.1 -> 2.2.0). Three changes
            # here, each read off the installed 2.2.0 source rather than the
            # migration guide, because getting the first one wrong would mean
            # sending no credential at all while every test still passed:
            #
            #   * `streamablehttp_client` is gone. At 1.28.1 it already carried
            #     `@deprecated("Use `streamable_http_client` instead.")`; 2.x
            #     deleted it. `streamable_http_client` is the replacement.
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
            #   * It yields a **two**-tuple, not a three-tuple: the third
            #     element at 1.28.1 was a session-id callback this code never
            #     used (`as (read, write, _)`).
            #
            # What did NOT change, and is the reason this stays one hop rather
            # than two: the client is still an ordinary HTTP client in this
            # process, so trace context still rides out in the `traceparent`
            # header injected by instrumentation rather than by this file. It
            # is now `httpx2` rather than `httpx`, so the instrumentor that
            # does it is `HTTPX2ClientInstrumentor` -- already applied in
            # bootstrap.py since M1.2 Task 8, for `anthropic`. Both are applied
            # there; neither was added for this bump.
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
                        # M2.1 Task 4 note: this is emphatically NOT the carrier
                        # the JWT-SVID uses. The SVID goes in the HTTP
                        # Authorization header set above, one level down at the
                        # transport, and the two never mix -- see this module's
                        # docstring for why folding one into the other would be
                        # a security bug rather than a simplification.
                        carrier: dict[str, str] = {}
                        propagate.inject(carrier)

                        # mcp 2.x: `types.ClientRequest` is a plain union of
                        # request models, not the 1.x `RootModel` wrapper, and
                        # `send_request` accepts a member of that union
                        # directly (its signature reads
                        # `types.ClientRequest | types.Request[Any, Any]`). So
                        # the `ClientRequest(...)` wrapping is gone, not
                        # replaced.
                        #
                        # `params._meta` is now the TypedDict
                        # `RequestParamsMeta` -- "an open map: arbitrary keys
                        # round-trip via `extra_items=Any`", its own docstring
                        # -- so the carrier dict IS the value; 1.x's
                        # `RequestParams.Meta(**carrier)` model no longer
                        # exists. The serialised wire shape is byte-identical,
                        # checked with `model_dump(by_alias=True,
                        # exclude_none=True)` against the installed 2.2.0:
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
            # M2.2: and if that leaf is the platform refusing, translate it
            # into a PolicyDenial first. The leaf is an
            # `mcp.shared.exceptions.MCPError` (measured — the gateway denies
            # on HTTP 200 with a JSON-RPC error body, so `raise_for_status()`
            # never fires and there is no `httpx.HTTPStatusError` anywhere on
            # this path), whose `str()` keeps the reason but drops
            # `error.data`. Re-raising the leaf unchanged is what lost the
            # structure and left the A2A caller a bare `-32603`.
            leaf = _leaf_exception(eg)
            raise (_policy_denial(leaf) or leaf) from eg
        except MCPError as exc:
            # Belt and braces. Every measurement so far shows the MCPError
            # arriving doubly wrapped by the two anyio TaskGroups, so the
            # clause above is the live path. This one exists because the
            # wrapping is anyio's business, not ours: if a future anyio or mcp
            # stopped wrapping, a denial would silently stop being terminal
            # and the failure mode would be a retry loop on stage — the exact
            # hazard this module now guards. Cheaper to cover than to detect.
            denial = _policy_denial(exc)
            if denial is None:
                raise
            raise denial from exc

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
        """Read back what the ledger recorded for a payment (M3 Task 3 fix
        round, R11). `execute_approved` calls this before executing an
        approval, to confirm a refusal record actually exists and that its
        recorded amount matches -- through the same governed MCP path every
        other payments call in this repo takes, not a new HTTP path."""
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
    ) -> dict:
        """Record why a payout was refused, beside the payout (M3 Task 2's
        `POST /payments/{id}/refuse`, reached through the same governed MCP
        path every other payments call in this repo takes -- not a new HTTP
        path from this agent to payments-api).

        M3 Task 3 review fix round (R12) superseded validating only `rule`:
        payments-api's refuse endpoint now also rejects an already-refused
        payment and confirms `requested` against the recorded proposal.
        `refused_span_id` travels alongside `refused_trace_id` so
        `execute_approved` can link to the settlement-agent span active when
        the refusal was recorded -- settlement-agent's own A2A server span,
        not the Policy Decision Service's `policy.decision` span (finding 3 /
        R13) -- rather than only to the trace as a whole (see settlement.py's
        `_refusal_link` and its measurement note).
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
            },
        )
