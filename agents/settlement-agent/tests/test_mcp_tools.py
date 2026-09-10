"""Pins the two correctness fixes in mcp_tools.py (review Finding 2 / A19),
and (Task 7) the client-side params._meta injection that closes the MCP hop.

Both original fixes were verified live in the original round (NOTES.md §7),
but neither had a test: deleting either the `isError` branch in `_unwrap` or
the `except BaseExceptionGroup` clause in `_call` would still leave every
prior test green while a real 404/409 from payments-api was buried again —
exactly the failure mode this file guards against. Concrete regression checks:

- Delete the `if getattr(result, "isError", False): ...` branch from
  `_unwrap` -> `test_unwrap_raises_tool_call_error_with_the_tools_own_text`
  fails (falls through to the generic "no structured content" ValueError).
- Delete the `except BaseExceptionGroup as eg: raise _leaf_exception(eg)`
  clause from `_call` ->
  `test_call_unwraps_a_doubly_nested_exception_group_to_the_tool_call_error`
  fails (a bare ExceptionGroup propagates instead of ToolCallError).

The `_call` test also exercises pytest.ini's `asyncio_mode = auto` for a
real reason (review nit 7): it is the one test in this suite that needs it.

Task 7 adds `test_call_injects_params_meta_traceparent_from_the_ambient_context`
and `test_call_injects_params_meta_baggage_from_the_ambient_context`: reverting
`_call` to `session.call_tool(tool, arguments)` (the pre-Task-7 shape) makes
both fail, since `call_tool` has no way to set `_meta` at all (see
NOTES.md and mcp_tools.py's own module docstring).

M2.1 Task 4 adds the identity half, at the bottom of this file, and changes
every `_call` test's transport fake: the old `lambda url:
_FakeReadWriteContext()` accepted no `headers` argument, so it could not have
observed a credential even if one were sent. See `_capturing_transport`.

M2.2 Task 5 adds the denial half, in the last section: `PolicyDenial`, and the
branch in `_call` that produces it. Its own header block carries that
section's regression checks. The agent-level half of the same task -- how many
execute attempts a refused settlement makes, and what the A2A caller gets --
is in `tests/test_settlement.py`, which imports this file's denial fixtures so
there is one definition of the exception shape the deployed gateway produces.
"""
from __future__ import annotations

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from opentelemetry import baggage, context, trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

import mcp_tools
from correlation import set_claim_id
from mcp_tools import (
    PaymentsTools,
    PolicyDenial,
    ToolCallError,
    _leaf_exception,
    _policy_denial,
    _unwrap,
)
from svid import SvidUnavailable


class _FakeTextBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeCallToolResult:
    """Stands in for mcp.types.CallToolResult without pulling in the real
    class, whose `structuredContent` is a pydantic "extra" field only
    present when the server actually sends one (NOTES.md §7) — so a plain
    object that simply omits the attribute reproduces that shape exactly."""

    def __init__(self, isError: bool = False, content=None, structuredContent=None):
        self.isError = isError
        self.content = content or []
        if structuredContent is not None:
            self.structuredContent = structuredContent


def test_unwrap_raises_tool_call_error_with_the_tools_own_text():
    result = _FakeCallToolResult(
        isError=True,
        content=[_FakeTextBlock("Error calling tool 'execute_payment': Client error '404 Not Found'")],
    )
    with pytest.raises(ToolCallError, match="404 Not Found"):
        _unwrap(result)


def test_unwrap_joins_multiple_error_text_blocks():
    result = _FakeCallToolResult(
        isError=True,
        content=[_FakeTextBlock("first"), _FakeTextBlock("second")],
    )
    with pytest.raises(ToolCallError, match="first; second"):
        _unwrap(result)


def test_unwrap_returns_the_structured_payload_on_success():
    result = _FakeCallToolResult(structuredContent={"paymentId": "PAY-000001"})
    assert _unwrap(result) == {"paymentId": "PAY-000001"}


def test_unwrap_unwraps_the_result_wrapper_key():
    # FastMCP wraps scalar/dict returns under "result" in some versions
    # (mcp_tools.py's own comment) — confirm _unwrap accounts for it.
    result = _FakeCallToolResult(structuredContent={"result": {"paymentId": "PAY-000002"}})
    assert _unwrap(result) == {"paymentId": "PAY-000002"}


def test_unwrap_raises_value_error_when_no_structured_content_and_not_an_error():
    result = _FakeCallToolResult()  # isError=False, no structuredContent at all
    with pytest.raises(ValueError, match="no structured content"):
        _unwrap(result)


def test_leaf_exception_unwraps_a_doubly_nested_group():
    leaf = ToolCallError("404 Not Found")
    doubly_nested = BaseExceptionGroup("outer", [BaseExceptionGroup("inner", [leaf])])
    assert _leaf_exception(doubly_nested) is leaf


def test_leaf_exception_returns_a_plain_exception_unchanged():
    leaf = ValueError("not a group at all")
    assert _leaf_exception(leaf) is leaf


def test_leaf_exception_prefers_a_tool_call_error_over_other_siblings():
    # Review nit 9: `exceptions[0]` unconditionally is fragile if a
    # cancellation/transport error ever lands ahead of the real
    # ToolCallError in the group. Confirm the preference holds even when the
    # ToolCallError is not first.
    tool_error = ToolCallError("404 Not Found")
    group = BaseExceptionGroup("outer", [RuntimeError("unrelated"), tool_error])
    assert _leaf_exception(group) is tool_error


class _FakeReadWriteContext:
    """Stands in for streamablehttp_client(url, headers=...)'s async context
    manager."""

    async def __aenter__(self):
        return (None, None, None)

    async def __aexit__(self, exc_type, exc, tb):
        return False


# ---------------------------------------------------------------------------
# M2.1 Task 4: identity on the wire.
# ---------------------------------------------------------------------------
_FAKE_SVID = "fake.jwt.svid"


def _fake_svid_fetcher(audiences: list):
    """Stands in for svid.fetch_jwt_svid, recording the audience it was asked
    for. `_call` awaits this before it opens any transport, so without a
    stand-in every `_call` test would try to reach a real Workload API
    socket."""

    async def _fetch(audience: str) -> str:
        audiences.append(audience)
        return _FAKE_SVID

    return _fetch


def _capturing_transport(captured: dict):
    """Stands in for mcp_tools.streamablehttp_client, recording the url and
    headers it was called with.

    The old fakes were `lambda url: _FakeReadWriteContext()` -- positional-only
    and header-blind, so a `_call` that forgot the credential entirely would
    still have passed every test in this file. Capturing both means the
    Authorization header is asserted on rather than assumed."""

    def _factory(url, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeReadWriteContext()

    return _factory


class _FakeSessionRaisingNestedGroup:
    """Stands in for mcp.ClientSession, reproducing the doubly-nested
    BaseExceptionGroup that anyio's TaskGroups actually produce (verified
    empirically, NOTES.md §7) around a single ToolCallError raised from
    the tool call. Task 7: `_call` now calls `send_request`, not `call_tool`
    (see mcp_tools.py) — this fake follows that same call site so this test
    still exercises the real code path."""

    def __init__(self, read, write):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def initialize(self):
        pass

    async def send_request(self, request, result_type):
        leaf = ToolCallError(
            "Error calling tool 'execute_payment': Client error '404 Not Found' "
            "for url 'http://payments-api/payments/PAY-999999/execute'"
        )
        raise BaseExceptionGroup("outer", [BaseExceptionGroup("inner", [leaf])])

    async def call_tool(self, name, arguments):
        # Review nit 6: without this method, reverting `_call` to the
        # pre-Task-7 `session.call_tool(...)` shape would fail this fake
        # with a bare `AttributeError` (no such attribute), not a message
        # that says anything about what's actually wrong. Fail loudly with
        # the real reason instead, in case a future revert lands here.
        raise AssertionError(
            "_call() invoked session.call_tool(), not send_request() — this "
            "fake exercises the send_request() path Task 7 introduced (see "
            "mcp_tools.py's module docstring); call_tool() gives no way to "
            "set params._meta at all"
        )


async def test_call_unwraps_a_doubly_nested_exception_group_to_the_tool_call_error(monkeypatch):
    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", _fake_svid_fetcher([]))
    monkeypatch.setattr(
        mcp_tools, "streamablehttp_client", _capturing_transport({})
    )
    monkeypatch.setattr(mcp_tools, "ClientSession", _FakeSessionRaisingNestedGroup)

    tools = PaymentsTools("http://fake-mcp/mcp")
    with pytest.raises(ToolCallError, match="404 Not Found"):
        await tools.execute("PAY-999999", 6200.0, "EUR")


class _FakeSessionCapturingRequest:
    """Stands in for mcp.ClientSession, capturing the request passed to
    send_request instead of executing anything, so params._meta can be
    inspected directly — the one thing session.call_tool() (mcp==1.9.4's
    high-level API) gives no way to observe or control."""

    captured: dict = {}

    def __init__(self, read, write):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def initialize(self):
        pass

    async def send_request(self, request, result_type):
        _FakeSessionCapturingRequest.captured["request"] = request
        return _FakeCallToolResult(structuredContent={"paymentId": "PAY-000001"})

    async def call_tool(self, name, arguments):
        # Review nit 6: same rationale as _FakeSessionRaisingNestedGroup's
        # call_tool above — a substantive failure, not AttributeError, if
        # _call is ever reverted to the pre-Task-7 call_tool() shape.
        raise AssertionError(
            "_call() invoked session.call_tool(), not send_request() — "
            "params._meta was never set because call_tool() gives no way "
            "to set it at all (see mcp_tools.py's module docstring)"
        )


def _remote_span_context() -> SpanContext:
    return SpanContext(
        trace_id=0x0123456789ABCDEF0123456789ABCDEF,
        span_id=0x0123456789ABCDEF,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )


async def test_call_injects_params_meta_traceparent_from_the_ambient_context(monkeypatch):
    """mcp==1.9.4's high-level ClientSession.call_tool() has no way to set
    params._meta at all (agents/settlement-agent/NOTES.md), so `_call` reaches
    for the lower-level send_request() API instead — the same technique
    tests/e2e/test_payments_mcp_trace.py used as test infrastructure (A14a).
    This test proves the *production* client does the injection, which is
    Task 7's job specifically (that e2e test's injection does not pre-empt
    it)."""
    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", _fake_svid_fetcher([]))
    monkeypatch.setattr(
        mcp_tools, "streamablehttp_client", _capturing_transport({})
    )
    monkeypatch.setattr(mcp_tools, "ClientSession", _FakeSessionCapturingRequest)
    _FakeSessionCapturingRequest.captured = {}

    ctx = trace.set_span_in_context(NonRecordingSpan(_remote_span_context()))
    token = context.attach(ctx)
    try:
        tools = PaymentsTools("http://fake-mcp/mcp")
        await tools.propose("CLM-1842", 6200, "EUR")
    finally:
        context.detach(token)

    request = _FakeSessionCapturingRequest.captured["request"]
    meta = request.root.params.meta
    assert meta is not None, "params._meta was never set — no context was injected"
    assert meta.traceparent == (
        "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    )


async def test_call_injects_params_meta_baggage_from_the_ambient_context(monkeypatch):
    """The MCP boundary carries baggage the same way it carries trace context
    (SEP-414's unprefixed `baggage` key): whatever propagate.inject() puts in
    the carrier from the ambient context — including northstar.claim.id, set
    via correlation.set_claim_id — must reach params._meta.baggage."""
    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", _fake_svid_fetcher([]))
    monkeypatch.setattr(
        mcp_tools, "streamablehttp_client", _capturing_transport({})
    )
    monkeypatch.setattr(mcp_tools, "ClientSession", _FakeSessionCapturingRequest)
    _FakeSessionCapturingRequest.captured = {}

    ctx = set_claim_id("CLM-1842")
    token = context.attach(ctx)
    try:
        tools = PaymentsTools("http://fake-mcp/mcp")
        await tools.propose("CLM-1842", 6200, "EUR")
    finally:
        context.detach(token)

    request = _FakeSessionCapturingRequest.captured["request"]
    meta = request.root.params.meta
    assert meta is not None, "params._meta was never set — no context was injected"
    assert "northstar.claim.id=CLM-1842" in meta.baggage


async def test_call_omits_meta_entirely_when_context_is_empty(monkeypatch):
    """No active span, no baggage: propagate.inject() produces an empty
    carrier, and _meta should be omitted (None) rather than sent as an empty
    object — matching the pre-Task-7 wire shape for a caller with nothing to
    propagate."""
    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", _fake_svid_fetcher([]))
    monkeypatch.setattr(
        mcp_tools, "streamablehttp_client", _capturing_transport({})
    )
    monkeypatch.setattr(mcp_tools, "ClientSession", _FakeSessionCapturingRequest)
    _FakeSessionCapturingRequest.captured = {}

    token = context.attach(context.Context())
    try:
        tools = PaymentsTools("http://fake-mcp/mcp")
        await tools.propose("CLM-1842", 6200, "EUR")
    finally:
        context.detach(token)

    request = _FakeSessionCapturingRequest.captured["request"]
    assert request.root.params.meta is None


# ---------------------------------------------------------------------------
# M2.1 Task 4: the JWT-SVID rides in an HTTP header, the trace context rides
# in params._meta, and the two carriers stay separate.
#
# Concrete regression checks:
# - Drop `headers=` from the streamablehttp_client call ->
#   test_call_sends_the_jwt_svid_as_an_authorization_bearer_header fails
#   (headers is None, so no credential reaches the gateway at all).
# - Drop the `Bearer ` prefix -> the same test fails on the exact value.
#   agentgateway strips exactly `"Bearer "` before validating
#   (AuthorizationLocation::bearer_header()), so a bare token is not a
#   cosmetic difference: the gateway would try to verify the string
#   "eyJ...".
# - Change the audience `_call` asks for -> test_call_mints_the_svid_for_the
#   _gateway_audience fails. A token minted for the wrong audience is
#   rejected by the gateway's `audiences` check, which presents as a 401 with
#   nothing wrong in the token's signature.
# - Move `fetch_jwt_svid` inside the `async with` ->
#   test_call_opens_no_transport_when_no_svid_is_available fails (a transport
#   is opened for a call this workload cannot prove it may make).
# - Fold the SVID into params._meta, or the traceparent into the headers ->
#   test_the_identity_and_trace_carriers_do_not_bleed_into_each_other fails.
# ---------------------------------------------------------------------------


async def test_call_sends_the_jwt_svid_as_an_authorization_bearer_header(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", _fake_svid_fetcher([]))
    monkeypatch.setattr(mcp_tools, "streamablehttp_client", _capturing_transport(captured))
    monkeypatch.setattr(mcp_tools, "ClientSession", _FakeSessionCapturingRequest)
    _FakeSessionCapturingRequest.captured = {}

    tools = PaymentsTools("http://fake-mcp/mcp")
    await tools.propose("CLM-1842", 6200, "EUR")

    assert captured["headers"] == {"Authorization": f"Bearer {_FAKE_SVID}"}


async def test_call_mints_the_svid_for_the_gateway_audience(monkeypatch):
    audiences: list[str] = []
    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", _fake_svid_fetcher(audiences))
    monkeypatch.setattr(mcp_tools, "streamablehttp_client", _capturing_transport({}))
    monkeypatch.setattr(mcp_tools, "ClientSession", _FakeSessionCapturingRequest)
    _FakeSessionCapturingRequest.captured = {}

    tools = PaymentsTools("http://fake-mcp/mcp")
    await tools.execute("PAY-000001", 6200.0, "EUR")

    assert audiences == [mcp_tools._GATEWAY_AUDIENCE]
    # And that audience is the gateway's SPIFFE ID, not a bare label: the same
    # string appears in deploy/platform/agentgateway/config.yaml's jwtAuth
    # `audiences` and in deploy/platform/spire/registration.yaml's ID
    # template. Asserted as a full SPIFFE URI so a drift to something like
    # "agentgateway" cannot pass.
    assert mcp_tools._GATEWAY_AUDIENCE.startswith("spiffe://northstar.local/")


async def test_call_opens_no_transport_when_no_svid_is_available(monkeypatch):
    """SvidUnavailable must reach the caller, and no MCP traffic may be sent.

    A call whose identity cannot be proven should never arrive at the gateway
    in the first place -- not arrive unauthenticated and get refused there.
    """
    captured: dict = {}

    async def boom(audience):
        raise SvidUnavailable("no socket")

    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", boom)
    monkeypatch.setattr(mcp_tools, "streamablehttp_client", _capturing_transport(captured))
    monkeypatch.setattr(mcp_tools, "ClientSession", _FakeSessionCapturingRequest)

    tools = PaymentsTools("http://fake-mcp/mcp")
    with pytest.raises(SvidUnavailable):
        await tools.propose("CLM-1842", 6200, "EUR")
    assert captured == {}, "a transport was opened despite having no identity"


async def test_the_identity_and_trace_carriers_do_not_bleed_into_each_other(monkeypatch):
    """The header carries identity only; params._meta carries context only.

    Both are populated on the same call here, so this fails if either one
    starts carrying the other's payload -- the specific confusion
    mcp_tools.py's module docstring warns about.
    """
    captured: dict = {}
    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", _fake_svid_fetcher([]))
    monkeypatch.setattr(mcp_tools, "streamablehttp_client", _capturing_transport(captured))
    monkeypatch.setattr(mcp_tools, "ClientSession", _FakeSessionCapturingRequest)
    _FakeSessionCapturingRequest.captured = {}

    ctx = trace.set_span_in_context(NonRecordingSpan(_remote_span_context()))
    token = context.attach(ctx)
    try:
        tools = PaymentsTools("http://fake-mcp/mcp")
        await tools.propose("CLM-1842", 6200, "EUR")
    finally:
        context.detach(token)

    headers = captured["headers"]
    meta = _FakeSessionCapturingRequest.captured["request"].root.params.meta

    # Identity is in the header and nowhere else.
    assert headers["Authorization"] == f"Bearer {_FAKE_SVID}"
    assert _FAKE_SVID not in repr(meta), "the SVID leaked into params._meta"

    # Trace context is in params._meta. This client does not hand-inject
    # traceparent into headers -- HTTPXClientInstrumentor does that, one layer
    # down, for every outbound request -- so it must not appear here.
    assert meta.traceparent == "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    assert {k.lower() for k in headers} == {"authorization"}, (
        f"the identity header carrier grew beyond Authorization: {sorted(headers)}"
    )


# ---------------------------------------------------------------------------
# The amount travels with the execution call.
#
# agentgateway's policy hook decides on what the `tools/call` request itself
# contains -- the PDS receives `mcp_request` carrying {"name", "arguments"}
# verbatim -- so an execute call whose only argument is an opaque payment id
# is un-governable at that choke point, and payments-api has no stated
# instruction to confirm against either. These two tests pin the arguments
# dict, which is the thing both the policy layer and the system of record
# actually see.
# ---------------------------------------------------------------------------


async def test_execute_sends_the_amount_in_the_tool_call_arguments(monkeypatch):
    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", _fake_svid_fetcher([]))
    monkeypatch.setattr(mcp_tools, "streamablehttp_client", _capturing_transport({}))
    monkeypatch.setattr(mcp_tools, "ClientSession", _FakeSessionCapturingRequest)
    _FakeSessionCapturingRequest.captured = {}

    tools = PaymentsTools("http://fake-mcp/mcp")
    await tools.execute("PAY-000001", 6200.0, "EUR")

    params = _FakeSessionCapturingRequest.captured["request"].root.params
    assert params.name == "execute_payment"
    assert params.arguments == {
        "payment_id": "PAY-000001",
        "amount": 6200.0,
        "currency": "EUR",
    }


async def test_execute_defaults_the_currency_but_never_the_amount():
    """The currency has a sensible default; the amount must be stated.

    `execute(payment_id)` with no amount is the pre-fix signature, and it is
    exactly the shape that leaves the choke point with nothing to decide on.
    It must be a TypeError, not a silently-defaulted zero.
    """
    tools = PaymentsTools("http://fake-mcp/mcp")
    with pytest.raises(TypeError):
        await tools.execute("PAY-000001")


# ---------------------------------------------------------------------------
# M2.2 Task 5: a policy denial is terminal, and the model gets to see it.
#
# The spec calls this the load-bearing hazard: "If a denial reads as a
# retryable tool error, the agent can sit on stage retrying a payment it will
# never be allowed to make. The implementation gets an explicit test for the
# absence of a retry loop, not merely for the presence of a deny."
#
# The exception that actually arrives here was measured, not guessed
# (task-1-findings.md §3): `mcp.shared.exceptions.McpError`, because the
# gateway denies on **HTTP 200** with an `application/json` JSON-RPC error
# body, so `raise_for_status()` is a no-op and there is no
# `httpx.HTTPStatusError` on this path at all. Every fixture below therefore
# builds a real `McpError` around a real `mcp.types.ErrorData`, in the real
# nesting the gateway produces, rather than a hand-rolled stand-in.
#
# Concrete regression checks:
# - Make `PolicyDenial` subclass `ModelRetry` ->
#   test_a_policy_denial_is_not_a_ModelRetry fails immediately, and
#   test_the_agent_makes_exactly_one_execute_attempt_after_a_denial (in
#   test_settlement.py) fails on the count.
# - Key `_deny_marker` on the message text instead of `decision` ->
#   test_a_denial_shaped_message_with_no_marker_is_not_a_denial fails.
# - Drop the `_policy_denial` call from `_call`'s handler ->
#   test_call_translates_a_denied_tool_call_into_a_policy_denial fails (a bare
#   McpError propagates, which is exactly the pre-task behaviour that gave the
#   A2A caller a -32603 with no narration).
# - Read only `str(exc)` instead of `exc.error.data` ->
#   test_a_policy_denial_carries_the_structured_facts_str_drops fails.
# ---------------------------------------------------------------------------

# The reason string the deployed PDS emits, verbatim (task-1-findings.md /
# the M2.2 deny body). It is on `error.message` deliberately: that is the one
# place `str(McpError)` preserves, and therefore the only text that can reach
# the model that must narrate the refusal.
DENY_REASON = (
    "requested 6200 EUR exceeds the 6000 EUR limit for this agent at "
    "autonomy level assisted"
)


def _denial_body(*, retryable: bool = False, requested: float | None = 6200.0) -> dict:
    """The PDS's `mcp_error` payload, verbatim in shape.

    It is itself a JSON-RPC error object; agentgateway parses it whole into
    `error.data`, so the deny marker ends up one level down at
    `error.data["data"]`.
    """
    return {
        "code": -32003,
        "message": DENY_REASON,
        "data": {
            "rule": "settlement-agent.assisted.execute_limit",
            "limit": 6000.0,
            # `null` when the PDS could not read an amount at all (a hostile or
            # unparseable argument) -- the body stays strictly valid JSON.
            "requested": requested,
            "retryable": retryable,
            "decision": "deny",
        },
    }


def _denial_error(**kwargs) -> McpError:
    """The exception as it arrives inside mcp_tools, end to end.

    `error.code` is the gateway's own `-32001` (PERMISSION_DENIED), *not* the
    `-32003` inside the body -- `mcp_error` only ever lands in `data`
    (task-1-findings.md §1). Pinning the gateway's code here means a test that
    accidentally started depending on -32003 would be caught.
    """
    return McpError(
        ErrorData(code=-32001, message=DENY_REASON, data=_denial_body(**kwargs))
    )


def _as_anyio_group(exc: BaseException) -> BaseExceptionGroup:
    """The doubly-nested wrapping the two anyio TaskGroups actually produce."""
    return BaseExceptionGroup("outer", [BaseExceptionGroup("inner", [exc])])


def test_a_policy_denial_becomes_a_terminal_result_not_a_retry():
    """The whole hazard in one assertion."""
    denial = _policy_denial(_denial_error(retryable=False))
    assert isinstance(denial, PolicyDenial)
    assert denial.retryable is False


def test_a_policy_denial_is_not_a_ModelRetry():
    """PydanticAI hands ModelRetry back to the model as 'try again'. If a
    denial is ever a subclass of it, the agent loops on stage."""
    from pydantic_ai import ModelRetry

    assert not issubclass(PolicyDenial, ModelRetry)


def test_a_policy_denial_is_a_ToolFailed_so_the_model_sees_it():
    """The other half of the same decision, and the half that makes the demo
    work: `ToolFailed` is the framework's terminal-failure-the-model-sees
    channel, which is why the refusal can be narrated at all. Without it the
    denial propagates out of Agent.run() and the caller gets a bare -32603."""
    from pydantic_ai import ToolFailed

    assert issubclass(PolicyDenial, ToolFailed)


def test_the_denial_message_reaches_the_model_intact():
    """The model narrates this text, so it must survive unwrapping.

    `str(McpError)` yields `error.message` and nothing else, so the reason has
    to be carried across into PolicyDenial's own message -- which is what
    PydanticAI puts in the failed ToolReturnPart the model reads.
    """
    denial = _policy_denial(_denial_error())
    assert DENY_REASON in denial.message
    assert denial.message == str(denial)
    # The two numbers the narration needs.
    assert "6000" in str(denial)
    assert "6200" in str(denial)
    # And it tells the model the answer is final, in the message itself, so
    # the terminality does not rest solely on framework behaviour.
    assert "final" in str(denial)


def test_a_policy_denial_carries_the_structured_facts_str_drops():
    """`str(McpError)` silently drops `error.data`. Task 1 measured the
    consequence: the deny marker did not reach the A2A caller at all. The
    structured facts have to be read off `exc.error.data`."""
    denial = _policy_denial(_denial_error())
    assert denial.rule == "settlement-agent.assisted.execute_limit"
    assert denial.limit == 6000.0
    assert denial.requested == 6200.0
    assert denial.body["decision"] == "deny"
    # The rule is named in the message too -- it is what an operator watching
    # the demo needs in order to point at the line that refused.
    assert "settlement-agent.assisted.execute_limit" in str(denial)
    # The machine-readable numbers are on attributes and stay off the prose:
    # str() carries the PDS's own reason text, not a re-rendering of the body.
    assert "6000.0" not in str(denial)
    assert "6200.0" not in str(denial)


def test_a_denial_with_an_unreadable_amount_carries_requested_as_none():
    """For a hostile or unparseable amount the PDS emits `"requested": null`
    and denies. Parsing must tolerate it rather than raise while handling a
    denial."""
    denial = _policy_denial(_denial_error(requested=None))
    assert isinstance(denial, PolicyDenial)
    assert denial.requested is None
    assert denial.limit == 6000.0


def test_a_transport_error_is_still_a_transport_error():
    """Regression guard: the new branch must not swallow real failures and
    report them as policy denials.

    This exact message is the PDS-unreachable case, measured in Task 4's
    failClosed test. It arrives as an McpError too, and it carries no marker.
    An outage reported as "policy said no" is a lie the model would go on to
    narrate to a claimant.
    """
    outage = McpError(
        ErrorData(
            code=-32001,
            message=(
                "mcpGuardrails checkRequest failed: upstream call failed: "
                "Connect: Connection refused (os error 111)"
            ),
        )
    )
    assert _policy_denial(outage) is None


def test_a_denial_shaped_message_with_no_marker_is_not_a_denial():
    """Recognition is structural, never prose.

    Same reason text as a real denial, no structured body. A message is
    written for humans and will be reworded; if the branch keyed on the text,
    a rewording would turn a terminal denial into a retryable error silently
    -- and here, the reverse: any upstream that happened to echo the reason
    would be mistaken for the policy layer.
    """
    lookalike = McpError(ErrorData(code=-32603, message=DENY_REASON))
    assert _policy_denial(lookalike) is None


def test_a_permit_marker_is_not_a_denial():
    """A body that says `permit` is not a denial, *even carrying deny prose*.

    Fix round 1, finding F6: this test used the message `"fine"`, which made it
    inert against the one mutation it should have caught. Swapping the
    structural check for a prose one while still reading the facts out of the
    body --

        for candidate in (data.get("data"), data):
            if isinstance(candidate, dict) and "exceeds the" in str(exc):
                return candidate

    -- passed all 73 tests, because no test ever handed the detector deny
    prose together with a body that structurally says permit. That is the
    realistic shape of the mistake: someone reaching for the message keeps
    reading the body for the rule and the limit, so every test that asserts on
    those still passes.

    The two ways to get the detection wrong pull in opposite directions, so
    both messages have to appear somewhere in this file: this test is
    prose-that-must-be-ignored-because-the-structure-says-permit, and
    `test_a_denial_shaped_message_with_no_marker_is_not_a_denial` is
    prose-with-no-structure-at-all. Neither alone is sufficient.
    """
    body = _denial_body()
    body["data"]["decision"] = "permit"
    permitted = McpError(ErrorData(code=-32001, message=DENY_REASON, data=body))
    assert _policy_denial(permitted) is None, (
        "a permit was read as a denial -- the detector is keying on the "
        "reason text rather than on data.decision"
    )


def test_an_ordinary_exception_carrying_no_error_attribute_is_not_a_denial():
    """ToolCallError, ValueError, SvidUnavailable -- none of them have
    `.error`, and none of them may be re-typed as a policy decision."""
    assert _policy_denial(ToolCallError("payment already executed")) is None
    assert _policy_denial(ValueError("nope")) is None


def test_the_marker_is_recognised_un_nested_too():
    """The measured shape nests the marker under `error.data["data"]` because
    the gateway parses the PDS's whole JSON-RPC error object into `data`. The
    un-nested position is accepted as well, so a gateway that stopped
    double-wrapping would not silently make denials retryable."""
    marker = _denial_body()["data"]
    flat = McpError(ErrorData(code=-32001, message=DENY_REASON, data=marker))
    denial = _policy_denial(flat)
    assert isinstance(denial, PolicyDenial)
    assert denial.rule == "settlement-agent.assisted.execute_limit"


class _FakeSessionDenying:
    """Stands in for mcp.ClientSession, raising the denial exactly as measured:
    an `McpError` doubly wrapped by the two anyio TaskGroups."""

    def __init__(self, read, write):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def initialize(self):
        pass

    async def send_request(self, request, result_type):
        raise _as_anyio_group(_denial_error())


class _FakeSessionDenyingUnwrapped(_FakeSessionDenying):
    """The same denial with no TaskGroup wrapping at all -- the shape a future
    anyio or mcp that stopped wrapping would produce."""

    async def send_request(self, request, result_type):
        raise _denial_error()


@pytest.mark.parametrize(
    "session", [_FakeSessionDenying, _FakeSessionDenyingUnwrapped]
)
async def test_call_translates_a_denied_tool_call_into_a_policy_denial(
    monkeypatch, session
):
    """End to end through the production `_call` handler, both wrappings.

    Without the translation, `_call` re-raises the McpError leaf: it
    propagates out of Agent.run(), the a2a-sdk turns it into JSON-RPC -32603
    with no `result`, and nothing narrates the refusal (measured, Task 4
    §11.4).
    """
    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", _fake_svid_fetcher([]))
    monkeypatch.setattr(mcp_tools, "streamablehttp_client", _capturing_transport({}))
    monkeypatch.setattr(mcp_tools, "ClientSession", session)

    tools = PaymentsTools("http://fake-mcp/mcp")
    with pytest.raises(PolicyDenial) as excinfo:
        await tools.execute("PAY-000001", 6200.0, "EUR")

    assert excinfo.value.rule == "settlement-agent.assisted.execute_limit"
    assert excinfo.value.retryable is False
    assert DENY_REASON in str(excinfo.value)


class _FakeSessionTransportFailing(_FakeSessionDenying):
    async def send_request(self, request, result_type):
        raise _as_anyio_group(
            McpError(
                ErrorData(
                    code=-32001,
                    message=(
                        "mcpGuardrails checkRequest failed: upstream call "
                        "failed: Connect: Connection refused (os error 111)"
                    ),
                )
            )
        )


async def test_call_leaves_a_transport_failure_as_an_mcp_error(monkeypatch):
    """The `failClosed` outage path, through the production handler.

    It must reach the caller as the failure it is, so `decide_settlement` lets
    it propagate to a JSON-RPC error rather than handing the model a refusal
    to narrate.
    """
    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", _fake_svid_fetcher([]))
    monkeypatch.setattr(mcp_tools, "streamablehttp_client", _capturing_transport({}))
    monkeypatch.setattr(
        mcp_tools, "ClientSession", _FakeSessionTransportFailing
    )

    tools = PaymentsTools("http://fake-mcp/mcp")
    with pytest.raises(McpError) as excinfo:
        await tools.execute("PAY-000001", 6200.0, "EUR")
    assert not isinstance(excinfo.value, PolicyDenial)
    assert "Connection refused" in str(excinfo.value)


async def test_refuse_sends_every_field_in_the_tool_call_arguments(monkeypatch):
    """M3 Task 3: recording a refusal through the same governed MCP path --
    not a new HTTP path from this agent to payments-api. The refuse endpoint
    validates only that `rule` is non-empty (M3 Task 2), so this client is
    what has to send every field, always."""
    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", _fake_svid_fetcher([]))
    monkeypatch.setattr(mcp_tools, "streamablehttp_client", _capturing_transport({}))
    monkeypatch.setattr(mcp_tools, "ClientSession", _FakeSessionCapturingRequest)
    _FakeSessionCapturingRequest.captured = {}

    tools = PaymentsTools("http://fake-mcp/mcp")
    await tools.refuse(
        "PAY-000001",
        rule="settlement-agent.assisted.execute_limit",
        limit=6000.0,
        requested=6200.0,
        narrative="I proposed the assessed amount but I am not authorised to pay it.",
        refused_trace_id="331edba8cb21223e9de2f244360c42ef",
        refused_span_id="b7997877797c1b27",
    )

    params = _FakeSessionCapturingRequest.captured["request"].root.params
    assert params.name == "refuse_payment"
    assert params.arguments == {
        "payment_id": "PAY-000001",
        "rule": "settlement-agent.assisted.execute_limit",
        "limit": 6000.0,
        "requested": 6200.0,
        "narrative": "I proposed the assessed amount but I am not authorised to pay it.",
        "refused_trace_id": "331edba8cb21223e9de2f244360c42ef",
        "refused_span_id": "b7997877797c1b27",
    }


async def test_get_sends_the_payment_id_in_the_tool_call_arguments(monkeypatch):
    """M3 Task 3 review fix round (finding 1, R11): execute_approved reads the
    ledger back through this call before executing an approval."""
    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", _fake_svid_fetcher([]))
    monkeypatch.setattr(mcp_tools, "streamablehttp_client", _capturing_transport({}))
    monkeypatch.setattr(mcp_tools, "ClientSession", _FakeSessionCapturingRequest)
    _FakeSessionCapturingRequest.captured = {}

    tools = PaymentsTools("http://fake-mcp/mcp")
    await tools.get("PAY-000001")

    params = _FakeSessionCapturingRequest.captured["request"].root.params
    assert params.name == "get_payment"
    assert params.arguments == {"payment_id": "PAY-000001"}


async def test_call_still_unwraps_a_tool_call_error_after_the_denial_branch(
    monkeypatch,
):
    """The new branch sits in the same handler as the M1.1 unwrapping and must
    not have displaced it: payments-api's 404/409 still arrives as a
    ToolCallError carrying its own text."""
    monkeypatch.setattr(mcp_tools, "fetch_jwt_svid", _fake_svid_fetcher([]))
    monkeypatch.setattr(mcp_tools, "streamablehttp_client", _capturing_transport({}))
    monkeypatch.setattr(mcp_tools, "ClientSession", _FakeSessionRaisingNestedGroup)

    tools = PaymentsTools("http://fake-mcp/mcp")
    with pytest.raises(ToolCallError, match="404 Not Found") as excinfo:
        await tools.execute("PAY-999999", 6200.0, "EUR")
    assert not isinstance(excinfo.value, PolicyDenial)
