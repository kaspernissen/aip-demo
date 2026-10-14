"""The servicer's two observable outputs: the `McpRequestResult` and the span.

Three properties here are invisible to `build_result`/`parse_call` unit tests
and would otherwise first be exercised on stage:

- that `policy.decision` is *in the caller's trace* -- which is a claim about
  `span.parent`, and the only way to be sure the span is a child rather than
  a root that happens to look like one;
- "`CheckRequest` must never raise", which is a claim about the servicer, not
  about the pure functions it calls;
- that the servicer is actually reachable as
  `agentgateway.dev.ext_mcp.ExtMcp` -- a method-name typo would compile fine
  and answer UNIMPLEMENTED to the gateway.
"""
from __future__ import annotations

import json
from concurrent import futures

import grpc
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openfeature.exception import ErrorCode

from pds import telemetry
from pds.autonomy import RESTRICTIVE_LEVEL
from pds.server import PolicyServicer

import ext_mcp_pb2 as pb
import ext_mcp_pb2_grpc as pb_grpc

import pds.server as _pds_server_module

# Captured at module-import time, before `_fake_flag_client` below
# (module-scoped, autouse) ever runs and replaces `pds.server._flag_client`
# with `_FailClosedFlagClient` for every test in this file. This is the
# actual object `api.set_provider`/`_flag_client.add_hooks([TracingHook()])`
# built at `pds.server` import -- the real module-scope client, not a test
# double.
_REAL_FLAG_CLIENT = _pds_server_module._flag_client

AGENT = "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"

# The exact `mcp_request` payload measured on the wire for a `propose_payment`
# call. Reused verbatim so these tests are exercised against the real shape,
# including the float amount.
MEASURED_PROPOSE = (b'{"name":"propose_payment","arguments":'
                    b'{"claim_id":"CLM-1842","amount":6200.0,"currency":"EUR"}}')

# A trace context to be a child of. The trace and span ids are arbitrary but
# fixed, so the assertions can name them.
PARENT_TRACE_ID = "41f5e359fa09260644706d31ffe3f327"
PARENT_SPAN_ID = "642cdc04679138cf"
TRACEPARENT = f"00-{PARENT_TRACE_ID}-{PARENT_SPAN_ID}-01".encode()


class _FailClosedFlagClient:
    """Deterministic double for `pds.server._flag_client`, installed for this
    whole module by `_fake_flag_client` below.

    `tests/test_autonomy.py` already exercises the real `FlagdProvider` and
    `GrpcResolver` end to end, including the fail-closed path against a
    genuinely unreachable sidecar (there is none in this test environment
    either) -- this suite's own job, per the module docstring, is the
    servicer's observable outputs, not a second proof of flagd integration.

    Measured, not assumed, why this needs to be a double rather than the real
    module-scope client left in place: `openfeature-provider-flagd==0.5.2`'s
    provider status model marks a failed *initial* connect as
    `ProviderStatus.ERROR` (`openfeature/provider/_registry.py`'s
    `_update_provider_status`), which `OpenFeatureClient._assert_provider_
    status` does NOT short-circuit on -- only `NOT_READY` and `FATAL` do. So
    once that one-time ~500ms init window elapses, every subsequent
    `get_string_details` call in this environment (no sidecar reachable)
    actually reaches `GrpcResolver._resolve` and blocks for the configured
    `deadline_ms` (default 500) before failing closed -- turning this
    module's run time from sub-second into tens of seconds, and making it
    depend on wall-clock timing of a service that is never there. This
    double removes both problems and, as a side effect, never runs
    `TracingHook` (a hook is an `OpenFeatureClient` concern; this class
    implements `get_string_details` itself, so no
    `feature_flag.evaluation` span event is added here) -- correct for this
    suite, since that event's presence is `test_autonomy.py`'s concern too.

    The default construction (`value=None`) reproduces the real not-ready
    path measured in `test_autonomy.py` and in
    `test_the_deny_span_carries_the_decision_and_both_numbers`'s own
    docstring -- `error_code=PROVIDER_NOT_READY`, no exception -- rather than
    an `error_code=None` state, which the real SDK never produces on any
    error path. `value`/`reason` are overridable per instance so a single
    test can install a second double that returns a genuine `autonomous`
    resolution (reason `TARGETING_MATCH`, the reason flagd's own targeting
    engine reports, not this class's fail-closed default) -- see
    `test_the_autonomous_resolution_reaches_policyinput_and_changes_the_outcome`
    below.
    """

    class _Provider:
        def get_metadata(self):
            return _Metadata("FakeFlagdProvider")

    def __init__(self, value=None, reason="ERROR",
                 error_code=ErrorCode.PROVIDER_NOT_READY, flag_metadata=None):
        self.provider = self._Provider()
        self._value = value
        self._reason = reason
        self._error_code = error_code
        self._flag_metadata = flag_metadata

    def get_string_details(self, flag_key, default_value, evaluation_context=None):
        return _Details(
            value=self._value if self._value is not None else default_value,
            reason=self._reason, error_code=self._error_code,
            flag_metadata=self._flag_metadata)


class _Metadata:
    def __init__(self, name):
        self.name = name


class _Details:
    def __init__(self, value, reason, variant=None, flag_metadata=None,
                 error_code=None):
        self.value = value
        self.reason = reason
        self.variant = variant
        self.flag_metadata = flag_metadata or {}
        self.error_code = error_code


@pytest.fixture(scope="module", autouse=True)
def _fake_flag_client():
    """Installs `_FailClosedFlagClient` in place of `pds.server`'s real
    module-scope OpenFeature client for the duration of this module, and
    restores the real one afterward."""
    import pds.server as server

    original = server._flag_client
    server._flag_client = _FailClosedFlagClient()
    yield
    server._flag_client = original


@pytest.fixture(scope="module", autouse=True)
def _provider():
    """One real SDK TracerProvider for the module, exporting in memory.

    `configure()` is not called: it would install an OTLP exporter pointing at
    nothing. Everything else about the span path is the real SDK, and the
    global propagator is registered explicitly here for the same reason
    `configure()` registers it -- `context_from_headers` reads `traceparent`
    with it, and without it every span below would be a root.
    """
    from opentelemetry.baggage.propagation import W3CBaggagePropagator
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )

    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter


@pytest.fixture
def spans(_provider):
    _provider.clear()
    return _provider


def _request(*, method="tools/call", caller=AGENT, params=MEASURED_PROPOSE,
             traceparent=TRACEPARENT):
    request = pb.McpRequest(method=method, service_names=["payments-mcp"])
    if caller is not None:
        request.metadata_context.update({"caller": caller})
    if params is not None:
        request.mcp_request = params
    if traceparent is not None:
        request.headers.append(pb.McpHeader(key="traceparent", value=traceparent))
    return request


def _autonomous(monkeypatch):
    monkeypatch.setattr(
        _pds_server_module, "_flag_client",
        _FailClosedFlagClient(value="autonomous", reason="TARGETING_MATCH",
                              error_code=None),
    )


def _execute(amount, currency="EUR"):
    return json.dumps({"name": "execute_payment",
                       "arguments": {"payment_id": "PAY-1", "amount": amount,
                                     "currency": currency}}).encode()


# --- the decision -----------------------------------------------------------


def test_the_demo_refusal_end_to_end(spans):
    """The headline case: at `assisted` the agent may release nothing, so
    5800 EUR on `execute_payment` is refused, and the refusal text the model
    narrates is on `AuthorizationError.reason`."""
    result = PolicyServicer().CheckRequest(
        _request(params=_execute(5800.0)), None)
    assert result.WhichOneof("result") == "error"
    assert "5800" in result.error.reason and "0 EUR limit" in result.error.reason
    assert result.error.reason.endswith(
        "[policy-denial rule=settlement-agent.assisted.execute_limit "
        "limit=0.00 requested=5800.00]")
    body = json.loads(result.error.mcp_error)
    assert body["data"] == {"rule": "settlement-agent.assisted.execute_limit",
                            "limit": 0.0, "requested": 5800.0,
                            "retryable": False, "decision": "deny"}


def test_a_payment_within_the_limit_is_permitted(spans, monkeypatch):
    """The platform must permit as well as refuse: at `autonomous`, 5800 is
    inside the 6000 limit."""
    _autonomous(monkeypatch)
    result = PolicyServicer().CheckRequest(_request(params=_execute(5800.0)), None)
    assert result.WhichOneof("result") == "pass"


# --- amounts at the boundary of what is payable ------------------------------


@pytest.mark.parametrize("literal,expected_rule", [
    # Refused by `parse_call`, so `decide` is handed no amount at all.
    ("NaN", "malformed_request"),
    ("Infinity", "malformed_request"),
    ("-Infinity", "malformed_request"),
    ("1e309", "malformed_request"),
    # Finite, so these reach `decide` and are refused by the rule -- under a
    # name that does not claim the EUR 6,000 limit approved them.
    ("0", "invalid_amount"),
    ("-0.0", "invalid_amount"),
    ("-500000.0", "invalid_amount"),
])
def test_f5_an_unpayable_amount_is_refused_end_to_end(spans, literal,
                                                      expected_rule):
    """`NaN`, `-Infinity` and `-1e309` driven through `CheckRequest` must not
    come back PERMIT stamped with the limit rule's own name -- that would
    make the audit record claim the EUR 6,000 rule had considered them and
    said yes. Both layers refuse, and the rule name says which one did it.
    """
    result = PolicyServicer().CheckRequest(
        _request(params=(b'{"name":"execute_payment","arguments":{"amount":'
                         + literal.encode() + b',"currency":"EUR"}}')), None)
    assert result.WhichOneof("result") == "error", "PERMITTED an unpayable amount"
    body = json.loads(result.error.mcp_error)
    assert body["data"]["rule"] == expected_rule
    assert body["data"]["retryable"] is False


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e309"])
def test_the_deny_body_stays_strictly_valid_json(spans, literal):
    """Python's `json.loads` accepts `NaN`; the gateway's
    `serde_json::from_slice` does not, and an unparseable `mcp_error` is
    dropped with a `warn!` while the deny is still returned. So
    a non-finite amount echoed into `data.requested` would silently cost the
    whole structured refusal. `parse_constant` is what makes this strict --
    without it `json.loads` would accept the very `NaN` being guarded against
    and the test would pass over a body the gateway throws away.
    """
    def reject(name):
        raise AssertionError(f"body contains the non-JSON literal {name}")

    result = PolicyServicer().CheckRequest(
        _request(params=(b'{"name":"execute_payment","arguments":{"amount":'
                         + literal.encode() + b"}}")), None)
    body = json.loads(result.error.mcp_error, parse_constant=reject)
    assert body["data"]["requested"] is None


def test_the_span_never_carries_a_non_finite_amount(spans):
    """An OTLP double attribute can hold NaN quite happily, and a NaN in a
    trace backend is a value nobody can filter on. `parse_call` refusing it
    upstream is what keeps it off the span as well as out of the body."""
    PolicyServicer().CheckRequest(
        _request(params=b'{"name":"execute_payment","arguments":{"amount":NaN}}'),
        None)
    span, = spans.get_finished_spans()
    assert telemetry.ATTR_AMOUNT not in span.attributes
    assert span.attributes[telemetry.ATTR_DECISION] == "deny"


def test_the_measured_propose_payload_is_permitted(spans):
    """The bytes actually observed on the wire. Proposing 6200 must still
    work -- the demo depends on the model proposing it and then being
    refused."""
    result = PolicyServicer().CheckRequest(_request(), None)
    assert result.WhichOneof("result") == "pass"


def test_a_missing_identity_is_a_deny_not_a_crash(spans):
    result = PolicyServicer().CheckRequest(
        _request(caller=None, params=_execute(100.0)), None)
    assert result.WhichOneof("result") == "error"
    assert json.loads(result.error.mcp_error)["data"]["rule"] == "unknown_caller"


@pytest.mark.parametrize("kwargs", [
    {"params": b"not json"},
    {"params": None},
    {"caller": None, "params": None, "traceparent": None},
    {"method": "tools/list"},
    {"params": _execute("6200"), "traceparent": b"garbage"},
])
def test_check_request_never_raises_and_never_permits_by_accident(spans, kwargs):
    """Any request we cannot evaluate must come back as a deny.

    A gRPC error instead would be routed by the gateway's `failureMode` and
    reported as `mcpGuardrails checkRequest failed` -- a PDS *outage*, which is
    a different sentence from a policy refusal.
    """
    result = PolicyServicer().CheckRequest(_request(**kwargs), None)
    assert result.WhichOneof("result") == "error"
    assert result.error.code == pb.AuthorizationError.PERMISSION_DENIED
    assert result.error.reason


def test_check_request_denies_when_the_policy_itself_blows_up(spans, monkeypatch):
    """The outer `except` is load-bearing, so exercise it rather than trust it.

    Injecting the failure into `decide` (not into the wire parsing) is what
    makes this a test of the servicer's guarantee and not of `parse_call`.
    """
    import pds.server as server

    def boom(_):
        raise RuntimeError("policy table corrupt")

    monkeypatch.setattr(server, "decide", boom)
    result = PolicyServicer().CheckRequest(_request(params=_execute(100.0)), None)
    assert result.WhichOneof("result") == "error"
    body = json.loads(result.error.mcp_error)
    assert body["data"]["rule"] == "internal_error"
    assert body["data"]["retryable"] is False
    # And the cause is on the span, so a trace shows the failure and not only
    # the refusal it was turned into.
    span = spans.get_finished_spans()[-1]
    assert span.status.status_code.name == "ERROR"
    assert [e.name for e in span.events] == ["exception"]
    assert span.attributes[telemetry.ATTR_RULE] == "internal_error"


def test_check_response_passes_unmodified(spans):
    """The response side is unused; mutating it would put the PDS in
    the data path of every successful call for no gain."""
    result = PolicyServicer().CheckResponse(
        pb.McpResponse(method="tools/call", mcp_response=b'{"content":[]}'), None)
    assert result.WhichOneof("result") == "pass"
    assert not result.HasField("error")


# --- the span ---------------------------------------------------------------


def test_the_decision_span_is_a_child_of_the_incoming_trace(spans):
    """Asserted the only way that distinguishes a child from a root:
    `span.parent` must carry the *caller's* ids.

    `traceparent` is forwarded to the guardrail hook (measured), which is
    what makes this achievable rather than aspirational.
    """
    PolicyServicer().CheckRequest(_request(params=_execute(6200.0)), None)
    span, = spans.get_finished_spans()
    assert span.name == "policy.decision"
    assert span.parent is not None, "root span: the traceparent was not extracted"
    assert f"{span.parent.trace_id:032x}" == PARENT_TRACE_ID
    assert f"{span.parent.span_id:016x}" == PARENT_SPAN_ID
    assert f"{span.context.trace_id:032x}" == PARENT_TRACE_ID
    assert span.context.span_id != span.parent.span_id
    assert span.kind.name == "SERVER"


def test_without_a_traceparent_the_span_is_a_root_and_nothing_breaks(spans):
    """The negative control for the test above. If it passed with no
    `traceparent` present, it would not be testing propagation at all."""
    PolicyServicer().CheckRequest(
        _request(params=_execute(6200.0), traceparent=None), None)
    span, = spans.get_finished_spans()
    assert span.parent is None


@pytest.mark.parametrize("traceparent", [
    b"garbage",
    b"00-not-hex-01",
    b"\xff\xfe",                                    # not even UTF-8
    b"00-00000000000000000000000000000000-0000000000000000-01",  # all-zero ids
])
def test_an_unusable_traceparent_costs_the_parent_and_nothing_else(spans,
                                                                   traceparent):
    """The decision must survive a header we cannot parse. Losing the parent
    costs correlation; raising would cost the decision, and would be reported
    as a PDS outage."""
    spans.clear()
    result = PolicyServicer().CheckRequest(
        _request(params=MEASURED_PROPOSE, traceparent=traceparent), None)
    assert result.WhichOneof("result") == "pass"
    span, = spans.get_finished_spans()
    assert span.parent is None


def test_the_deny_span_carries_the_decision_and_both_numbers(spans):
    """No flagd sidecar is reachable from this suite (it runs outside the
    cluster), so `resolve_autonomy` takes its fail-closed path and the level
    used is `RESTRICTIVE_LEVEL`. `ATTR_AUTONOMY_REASON` is `ERROR` because
    the OpenFeature SDK's own not-ready path (measured against the live
    sidecar mid-startup: `error_code=PROVIDER_NOT_READY`, no exception)
    reports it that way, not because `resolve_autonomy`'s own `except`
    fired."""
    PolicyServicer().CheckRequest(_request(params=_execute(6200.0)), None)
    span, = spans.get_finished_spans()
    assert span.attributes[telemetry.ATTR_DECISION] == "deny"
    assert span.attributes[telemetry.ATTR_RULE] == \
        "settlement-agent.assisted.execute_limit"
    assert span.attributes[telemetry.ATTR_CALLER] == AGENT
    assert span.attributes[telemetry.ATTR_TOOL] == "execute_payment"
    assert span.attributes[telemetry.ATTR_AMOUNT] == 6200.0
    assert span.attributes[telemetry.ATTR_CURRENCY] == "EUR"
    assert span.attributes[telemetry.ATTR_LIMIT] == 0.0
    assert span.attributes[telemetry.ATTR_AUTONOMY] == RESTRICTIVE_LEVEL
    assert span.attributes[telemetry.ATTR_AUTONOMY_REASON] == "ERROR"
    assert telemetry.ATTR_FLAG_VERSION not in span.attributes, (
        "no flagd sidecar is reachable here, so there is no version to carry"
    )
    assert span.attributes[telemetry.ATTR_RETRYABLE] is False


def test_the_autonomous_resolution_reaches_policyinput_and_changes_the_outcome(
        spans, monkeypatch):
    """The servicer-level guard that a hard-coded autonomy level would slip
    past unnoticed. Every other test in this module drives
    `_FailClosedFlagClient`'s default, which returns `RESTRICTIVE_LEVEL`
    ("assisted") -- so none of them can distinguish
    `autonomy_level=resolution.level` from a regression back to a literal
    `autonomy_level="assisted"`.

    This test installs a second double that returns a genuine `autonomous`
    resolution with `reason="TARGETING_MATCH"` and `error_code=None` -- the
    reason flagd's own targeting engine reports on a real contract match,
    carrying no error the way a real match never does (this class's own
    default `error_code=PROVIDER_NOT_READY` would force the level back down
    if left in place here) -- and drives the same €6,200 `execute_payment`
    scenario the headline denial uses. At `autonomous`,
    `EXECUTE_LIMITS[(settlement-agent, "autonomous")]` is €6,000, so the
    5,800 payment that every other test in this module sees denied must now
    be permitted, under the rule name that names the level actually used.
    Asserted on all four: level, rule, limit, and decision.
    """
    import pds.server as server

    monkeypatch.setattr(
        server, "_flag_client",
        _FailClosedFlagClient(value="autonomous", reason="TARGETING_MATCH",
                              error_code=None),
    )
    result = PolicyServicer().CheckRequest(_request(params=_execute(5800.0)), None)
    assert result.WhichOneof("result") == "pass"
    span, = spans.get_finished_spans()
    assert span.attributes[telemetry.ATTR_AUTONOMY] == "autonomous"
    assert span.attributes[telemetry.ATTR_RULE] == \
        "settlement-agent.autonomous.execute_limit"
    assert span.attributes[telemetry.ATTR_LIMIT] == 6000.0
    assert span.attributes[telemetry.ATTR_DECISION] == "permit"


def test_the_flag_version_lands_on_the_span_when_the_contract_carries_one(
        spans, monkeypatch):
    """`server.py`'s `if resolution.version is not None:
    span.set_attribute(...)` branch needs a test that actually populates a
    version, since neither the live sidecar's `autonomy-level` flag nor this
    suite's other doubles ever do. `test_autonomy.py` already proves
    `resolve_autonomy` extracts `version` off `flag_metadata`; this proves
    the extracted value also reaches the span.
    """
    import pds.server as server

    monkeypatch.setattr(
        server, "_flag_client",
        _FailClosedFlagClient(value="assisted", reason="TARGETING_MATCH",
                              error_code=None, flag_metadata={"version": "7"}),
    )
    PolicyServicer().CheckRequest(_request(params=_execute(5800.0)), None)
    span, = spans.get_finished_spans()
    assert span.attributes[telemetry.ATTR_FLAG_VERSION] == "7"


def test_a_permit_span_says_permit_and_is_not_an_error(spans, monkeypatch):
    """A refusal is this service working. A trace must not show a red span for
    the one thing this service exists to do, and must not show a green one
    for a deny either -- the attribute is what carries the decision, not the
    status."""
    _autonomous(monkeypatch)
    PolicyServicer().CheckRequest(_request(params=_execute(5800.0)), None)
    span, = spans.get_finished_spans()
    assert span.attributes[telemetry.ATTR_DECISION] == "permit"
    assert span.status.status_code.name == "UNSET"

    spans.clear()
    PolicyServicer().CheckRequest(_request(params=_execute(6200.0)), None)
    deny, = spans.get_finished_spans()
    assert deny.status.status_code.name == "UNSET"


def test_every_attribute_we_emit_is_declared_and_visibly_ours(spans):
    """Gap 3's guard. `telemetry.ATTRIBUTES` is the one list the telemetry
    contract documents, so an attribute set on the span but missing from it
    would be undocumented telemetry -- and the `policy.`/`northstar.`
    prefixes are how a reader tells our invented keys from the
    `gen_ai.*`/`mcp.*` ones the frameworks emit.

    One attribute in that list (`feature_flag.version`, an existing OTel
    semantic convention) is not one this service invented;
    `telemetry.INVENTED_ATTRIBUTES` is the subset the prefix check still
    applies to. It is not exercised by the four calls below (no flagd
    sidecar is reachable from this suite, so `resolve_autonomy` never gets a
    version to attach) -- `tests/test_autonomy.py` covers its extraction,
    and this guard's job is only to keep the *set* honest.
    """
    for params in (_execute(6200.0), _execute(5800.0), MEASURED_PROPOSE, b"junk"):
        PolicyServicer().CheckRequest(_request(params=params), None)
    emitted = {k for span in spans.get_finished_spans() for k in span.attributes}
    assert emitted, "no attributes were set at all"
    assert emitted <= set(telemetry.ATTRIBUTES)
    invented = emitted - {telemetry.ATTR_FLAG_VERSION}
    assert invented <= set(telemetry.INVENTED_ATTRIBUTES)
    assert all(k.startswith(("policy.", "northstar.")) for k in invented)


def test_a_missing_value_is_absent_from_the_span_not_zero(spans):
    """An amount recorded as 0.0 reads as a request for nothing, which is a
    different (and false) statement from 'no amount was sent'."""
    PolicyServicer().CheckRequest(_request(params=b"junk", caller=None), None)
    span, = spans.get_finished_spans()
    assert telemetry.ATTR_AMOUNT not in span.attributes
    assert telemetry.ATTR_CALLER not in span.attributes
    assert telemetry.ATTR_TOOL not in span.attributes


def test_the_real_module_scope_client_has_the_tracing_hook_registered():
    """The other half of the tracing-hook guard.

    `test_autonomy.py::test_the_tracing_hook_puts_a_feature_flag_evaluation_
    event_on_the_active_span` proves `TracingHook`, once registered, does
    what `server.py`'s comment claims. This proves the other half: that
    `server.py`'s own module-scope client -- `_REAL_FLAG_CLIENT`, captured at
    the top of this file before `_fake_flag_client` (module-scoped, autouse)
    ever gets a chance to replace `server._flag_client` with a double for
    every other test here -- is the one actually carrying it.
    """
    from openfeature.contrib.hook.opentelemetry import TracingHook

    assert any(isinstance(hook, TracingHook) for hook in _REAL_FLAG_CLIENT.hooks), (
        "no TracingHook registered on pds.server's module-scope OpenFeature "
        "client -- the feature_flag.evaluation event server.py's comment "
        "promises would never be added to any span"
    )


# --- the wire ---------------------------------------------------------------


def test_the_servicer_answers_on_the_real_grpc_service_name(spans):
    """A round trip over a real gRPC channel.

    `add_ExtMcpServicer_to_server` binds by method name; a typo would leave the
    gateway getting UNIMPLEMENTED, which `failClosed` reports as a PDS outage.
    Nothing else in this suite would notice.
    """
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    pb_grpc.add_ExtMcpServicer_to_server(PolicyServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = pb_grpc.ExtMcpStub(channel)
            result = stub.CheckRequest(_request(params=_execute(6200.0)),
                                       timeout=10)
            assert result.WhichOneof("result") == "error"
            assert "6200" in result.error.reason
            assert result.error.reason.endswith("requested=6200.00]")
            assert stub.CheckResponse(
                pb.McpResponse(method="tools/call"),
                timeout=10).WhichOneof("result") == "pass"
    finally:
        server.stop(grace=None).wait()


# --- the human approval reads the ledger -------------------------------------

REVIEWER = "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"


def _execute_payment(payment_id="PAY-000001", amount=5800.0):
    return json.dumps({"name": "execute_payment",
                       "arguments": {"payment_id": payment_id, "amount": amount,
                                     "currency": "EUR"}}).encode()


def test_a_reviewer_execute_reads_the_ledger_and_permits_when_awaiting(monkeypatch, spans):
    monkeypatch.setattr(_pds_server_module, "payment_status", lambda pid: "awaiting_approval")
    result = PolicyServicer().CheckRequest(
        _request(caller=REVIEWER, params=_execute_payment()), None)
    assert result.WhichOneof("result") == "pass"
    span, = spans.get_finished_spans()
    assert span.attributes[telemetry.ATTR_RULE] == "human-approved.execute"
    assert span.attributes[telemetry.ATTR_LEDGER_STATUS] == "awaiting_approval"


def test_a_reviewer_execute_denies_when_the_ledger_is_unreadable(monkeypatch, spans):
    monkeypatch.setattr(_pds_server_module, "payment_status", lambda pid: None)
    result = PolicyServicer().CheckRequest(
        _request(caller=REVIEWER, params=_execute_payment()), None)
    assert result.WhichOneof("result") == "error"
    assert "rule=human-approved.execute" in result.error.reason
    span, = spans.get_finished_spans()
    assert span.attributes[telemetry.ATTR_LEDGER_STATUS] == "unreadable"


def test_the_agent_never_triggers_a_ledger_read(monkeypatch, spans):
    calls = []
    monkeypatch.setattr(_pds_server_module, "payment_status", lambda pid: calls.append(pid))
    PolicyServicer().CheckRequest(_request(caller=AGENT, params=_execute_payment()), None)
    assert calls == []
    span, = spans.get_finished_spans()
    assert telemetry.ATTR_LEDGER_STATUS not in span.attributes


def test_a_reviewer_tool_that_is_not_execute_triggers_no_ledger_read(monkeypatch, spans):
    calls = []
    monkeypatch.setattr(_pds_server_module, "payment_status", lambda pid: calls.append(pid))
    params = json.dumps({"name": "get_payment", "arguments": {"payment_id": "PAY-000001"}}).encode()
    PolicyServicer().CheckRequest(_request(caller=REVIEWER, params=params), None)
    assert calls == []
