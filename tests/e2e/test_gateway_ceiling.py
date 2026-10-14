"""The gateway's ceiling, live: a payment over it is stopped before the PDS is asked."""
import pytest

from tests.harness import live
from tests.harness.jaeger import JaegerClient

JAEGER_API = "http://localhost:16686"
OVER = live.ceiling_eur() + 10_000  # derived from the config, never retyped


def _payment(amount):
    return {"payment_id": "PAY-999999", "amount": amount, "currency": "EUR"}


@pytest.fixture(scope="module")
def gateway():
    with live.gateway_port_forward() as url:
        yield url, {"authorization": f"Bearer {live.mint_svid(live.GATEWAY_AUDIENCE)}"}


def test_over_the_ceiling_is_a_403_from_the_gateway(gateway):
    url, headers = gateway
    outcome = live.call_tool_directly(url, live.EXECUTE_TOOL, _payment(float(OVER)), headers)
    assert outcome.http_status == 403, outcome
    assert outcome.body.strip() == "authorization failed", outcome


def test_the_403_leaves_no_policy_decision_span(gateway):
    url, headers = gateway
    trace_id = live.new_trace_id()
    live.call_tool_directly(url, live.EXECUTE_TOOL, _payment(float(OVER)),
                            {**headers, "traceparent": f"00-{trace_id}-{'1' * 16}-01"})
    trace = JaegerClient(JAEGER_API).wait_for_trace_id(trace_id, services={"agentgateway"}, timeout=60)
    assert trace.spans, "the gateway recorded no span for the denied call"
    assert not [s for s in trace.spans if s.operation == "policy.decision"], trace.spans


def test_initialize_and_tools_list_still_pass(gateway):
    url, headers = gateway
    outcome, tools = live.list_tools_directly(url, headers)
    assert outcome.http_status == 200 and live.EXECUTE_TOOL in tools, outcome


def test_a_string_amount_is_denied_not_waved_through(gateway):
    url, headers = gateway
    outcome = live.call_tool_directly(url, live.EXECUTE_TOOL, _payment(str(OVER)), headers)
    assert outcome.http_status == 403, outcome
