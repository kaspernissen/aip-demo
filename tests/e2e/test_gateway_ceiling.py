"""The gateway's ceiling, live: a payment over it is stopped before the PDS is asked."""
import os
import uuid

import httpx
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


def test_a_sessions_get_and_delete_are_not_stopped_by_the_ceiling(gateway):
    """The rule judges payments. The MCP client's own GET and DELETE on /mcp are
    not payments and must not be 403 (they were, before the method guard)."""
    import requests

    url, headers = gateway
    base = {**headers, "content-type": "application/json",
            "accept": "application/json, text/event-stream"}
    init = requests.post(url, headers=base, timeout=20, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "ceiling-test", "version": "1"}}})
    assert init.status_code == 200, init.text
    session = {**base, "mcp-session-id": init.headers["mcp-session-id"]}
    requests.post(url, headers=session, timeout=20,
                  json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    with requests.get(url, headers=session, timeout=5, stream=True) as got:
        assert got.status_code != 403, got.status_code
    deleted = requests.delete(url, headers=session, timeout=10)
    assert deleted.status_code != 403, deleted.status_code


@pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="a real claim spends model tokens; gated behind the RUN_BROWSER_TESTS=1 flag smoke-test.sh sets",
)
def test_a_claim_over_the_ceiling_fails_and_nobody_is_asked(gateway):
    """The behaviour the ceiling carries, pinned (docs/demo-runbook.md appendix):
    above the ceiling the agent's execute is a 403 from the gateway, not a deny
    from the PDS, so the task ends `failed`, the payment stays `proposed`, no
    reviewer ever sees it and no `policy.decision` span exists for the execute.
    By design nobody, a human included, may release it through this gateway.
    """
    claim_id = f"CLM-CEIL-{uuid.uuid4().hex[:8]}"
    over = float(OVER)
    with live.claims_agent_route() as url:
        run = live.submit_claim(claim_id, over, claims_agent_url=url, timeout=170)
    assert run["state"] == "failed", run
    assert run["reply"]["status"] == "failed", run["reply"]

    # The ledger: the proposal is there, and it was never executed or sent to a human.
    payments = httpx.get(f"{live.PAYMENTS_API_URL}/payments", timeout=10).json()
    mine = [p for p in payments if p.get("claimId") == claim_id]
    assert [p["status"] for p in mine] == ["proposed"], mine
    assert live.get_payment(mine[0]["paymentId"])["status"] == "proposed"
    awaiting = httpx.get(f"{live.REVIEWER_UI_URL}/api/awaiting", timeout=15).json()
    assert claim_id not in [a.get("claimId") for a in awaiting], "a reviewer would see this claim"

    # The trace: the gateway stopped the execute, and the PDS was never asked about it.
    trace = JaegerClient(JAEGER_API).wait_for_trace_id(
        run["trace_id"], services={"agentgateway"}, timeout=90)
    executes = [s for s in trace.spans if s.service == live.PDS_SERVICE
                and s.operation == live.DECISION_SPAN
                and s.attrs.get(live.ATTR_POLICY_TOOL) == live.EXECUTE_TOOL]
    assert executes == [], executes
