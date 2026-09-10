"""The servicer's two observable outputs: the `McpRequestResult` and the span.

Not in the brief. They earn their place because three of Task 3's requirements
are invisible to `build_result`/`parse_call` unit tests and would otherwise
first be exercised on stage:

- exit criterion 3, that `policy.decision` is *in the caller's trace* -- which
  is a claim about `span.parent`, and the only way to be sure the span is a
  child rather than a root that happens to look like one;
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

from pds import telemetry
from pds.server import AUTONOMY_LEVEL, PolicyServicer

import ext_mcp_pb2 as pb
import ext_mcp_pb2_grpc as pb_grpc

AGENT = "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"

# Task 1 measured this exact `mcp_request` payload on the wire for a
# `propose_payment` call. Reused verbatim so these tests are exercised against
# the real shape, including the float amount.
MEASURED_PROPOSE = (b'{"name":"propose_payment","arguments":'
                    b'{"claim_id":"CLM-1842","amount":6200.0,"currency":"EUR"}}')

# A trace context to be a child of. The trace and span ids are arbitrary but
# fixed, so the assertions can name them.
PARENT_TRACE_ID = "41f5e359fa09260644706d31ffe3f327"
PARENT_SPAN_ID = "642cdc04679138cf"
TRACEPARENT = f"00-{PARENT_TRACE_ID}-{PARENT_SPAN_ID}-01".encode()


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


def _execute(amount, currency="EUR"):
    return json.dumps({"name": "execute_payment",
                       "arguments": {"payment_id": "PAY-1", "amount": amount,
                                     "currency": currency}}).encode()


# --- the decision -----------------------------------------------------------


def test_the_demo_refusal_end_to_end(spans):
    """The M2.2 headline: 6200 EUR on `execute_payment` is refused, and the
    refusal text the model narrates is on `AuthorizationError.reason`."""
    result = PolicyServicer().CheckRequest(
        _request(params=_execute(6200.0)), None)
    assert result.WhichOneof("result") == "error"
    assert "6200" in result.error.reason and "6000" in result.error.reason
    body = json.loads(result.error.mcp_error)
    assert body["data"] == {"rule": "settlement-agent.assisted.execute_limit",
                            "limit": 6000.0, "requested": 6200.0,
                            "retryable": False, "decision": "deny"}


def test_a_payment_within_the_limit_is_permitted(spans):
    """Exit criterion 6: the platform must permit as well as refuse."""
    result = PolicyServicer().CheckRequest(_request(params=_execute(5800.0)), None)
    assert result.WhichOneof("result") == "pass"


# --- F5, at the boundary ----------------------------------------------------


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
    """The reviewer drove `NaN`, `-Infinity` and `-1e309` through
    `CheckRequest` and all three came back PERMIT -- stamped with the limit
    rule's own name, so the audit record claimed the EUR 6,000 rule had
    considered them and said yes. Both layers refuse now, and the rule name
    says which one did it.
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
    `serde_json::from_slice` does not, and Task 1 measured that an unparseable
    `mcp_error` is dropped with a `warn!` while the deny is still returned. So
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
    """The bytes Task 1 actually observed. Proposing 6200 must still work --
    the demo depends on the model proposing it and then being refused."""
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
    """The response side is unused in M2.2; mutating it would put the PDS in
    the data path of every successful call for no gain."""
    result = PolicyServicer().CheckResponse(
        pb.McpResponse(method="tools/call", mcp_response=b'{"content":[]}'), None)
    assert result.WhichOneof("result") == "pass"
    assert not result.HasField("error")


# --- the span ---------------------------------------------------------------


def test_the_decision_span_is_a_child_of_the_incoming_trace(spans):
    """Exit criterion 3, asserted the only way that distinguishes a child from
    a root: `span.parent` must carry the *caller's* ids.

    Task 1 measured `traceparent` being forwarded to the guardrail hook (run C),
    which is what makes this achievable rather than aspirational.
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
        _request(params=_execute(5800.0), traceparent=traceparent), None)
    assert result.WhichOneof("result") == "pass"
    span, = spans.get_finished_spans()
    assert span.parent is None


def test_the_deny_span_carries_the_decision_and_both_numbers(spans):
    PolicyServicer().CheckRequest(_request(params=_execute(6200.0)), None)
    span, = spans.get_finished_spans()
    assert span.attributes[telemetry.ATTR_DECISION] == "deny"
    assert span.attributes[telemetry.ATTR_RULE] == \
        "settlement-agent.assisted.execute_limit"
    assert span.attributes[telemetry.ATTR_CALLER] == AGENT
    assert span.attributes[telemetry.ATTR_TOOL] == "execute_payment"
    assert span.attributes[telemetry.ATTR_AMOUNT] == 6200.0
    assert span.attributes[telemetry.ATTR_CURRENCY] == "EUR"
    assert span.attributes[telemetry.ATTR_LIMIT] == 6000.0
    assert span.attributes[telemetry.ATTR_AUTONOMY] == AUTONOMY_LEVEL
    assert span.attributes[telemetry.ATTR_RETRYABLE] is False


def test_a_permit_span_says_permit_and_is_not_an_error(spans):
    """A refusal is this service working. A trace must not show a red span for
    the one thing M2.2 exists to do, and must not show a green one for a deny
    either -- the attribute is what carries the decision, not the status."""
    PolicyServicer().CheckRequest(_request(params=_execute(5800.0)), None)
    span, = spans.get_finished_spans()
    assert span.attributes[telemetry.ATTR_DECISION] == "permit"
    assert span.status.status_code.name == "UNSET"

    spans.clear()
    PolicyServicer().CheckRequest(_request(params=_execute(6200.0)), None)
    deny, = spans.get_finished_spans()
    assert deny.status.status_code.name == "UNSET"


def test_every_attribute_we_emit_is_declared_and_visibly_ours(spans):
    """Gap 3's guard. `telemetry.ATTRIBUTES` is the one list Task 7's contract
    documents, so an attribute set on the span but missing from it would be
    undocumented telemetry -- and the `policy.`/`northstar.` prefixes are how a
    reader tells our invented keys from the `gen_ai.*`/`mcp.*` ones the
    frameworks emit."""
    for params in (_execute(6200.0), _execute(5800.0), MEASURED_PROPOSE, b"junk"):
        PolicyServicer().CheckRequest(_request(params=params), None)
    emitted = {k for span in spans.get_finished_spans() for k in span.attributes}
    assert emitted, "no attributes were set at all"
    assert emitted <= set(telemetry.ATTRIBUTES)
    assert all(k.startswith(("policy.", "northstar.")) for k in emitted)


def test_a_missing_value_is_absent_from_the_span_not_zero(spans):
    """An amount recorded as 0.0 reads as a request for nothing, which is a
    different (and false) statement from 'no amount was sent'."""
    PolicyServicer().CheckRequest(_request(params=b"junk", caller=None), None)
    span, = spans.get_finished_spans()
    assert telemetry.ATTR_AMOUNT not in span.attributes
    assert telemetry.ATTR_CALLER not in span.attributes
    assert telemetry.ATTR_TOOL not in span.attributes


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
            assert stub.CheckResponse(
                pb.McpResponse(method="tools/call"),
                timeout=10).WhichOneof("result") == "pass"
    finally:
        server.stop(grace=None).wait()
