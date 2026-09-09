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
"""
from __future__ import annotations

import pytest
from opentelemetry import baggage, context, trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

import mcp_tools
from correlation import set_claim_id
from mcp_tools import PaymentsTools, ToolCallError, _leaf_exception, _unwrap
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
        await tools.execute("PAY-999999")


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
    await tools.execute("PAY-000001")

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
