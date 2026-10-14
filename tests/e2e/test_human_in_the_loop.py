"""M8: the agent prepares, a human releases. Live, stage 7.

Task 1 covers the ledger and the release; Task 2 adds the A2A task states and
the stream; Task 3 adds trace B and the histogram.

The dial is maker-checker. At the resting `assisted` level the settlement agent
may release nothing, so a claim is left `awaiting_approval` and a human decides
it through the reviewer-ui's JSON route. An approval is released by the
reviewer-ui itself, through agentgateway, under its own SPIFFE ID, and the PDS
permits it only because it reads `awaiting_approval` from the ledger. A
rejection is final.

This module spends one model settlement per claim fixture and, for the release,
moves a real EUR 5,800 in the demo ledger. It is gated like every live module
that drives a settlement: `RUN_BROWSER_TESTS=1`, and it FAILS rather than skips
when the cluster is absent.
"""
from __future__ import annotations

import os
import threading
import time
import uuid

import httpx
import pytest

from tests.harness import contract, live

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason=(
        "needs the live stage-7 cluster, spends one settlement's worth of "
        "model tokens per claim and releases a real payment as the reviewer-ui; "
        "gated behind the same RUN_BROWSER_TESTS=1 flag "
        "scripts/smoke-test.sh sets unconditionally"
    ),
)

REVIEWER = "alex.mercer"
REVIEWER_UI_SPIFFE_ID = live.REVIEWER_UI_SPIFFE_ID
AMOUNT = 5800.0


def _claim(amount: float) -> dict:
    contract.assert_assisted("before a maker-checker claim")
    return _claim_at_current_level(amount)


def _claim_at_current_level(amount: float) -> dict:
    claim_id = f"CLM-HITL-{uuid.uuid4().hex[:8]}"
    with live.claims_agent_route() as url:
        run = live.submit_claim(claim_id, amount, claims_agent_url=url)
    return {"claim_id": claim_id, **run}


@pytest.fixture(scope="module")
def approved():
    run = _claim(AMOUNT)
    assert run["reply"]["status"] == "awaiting_approval", run
    payment_id = run["reply"]["paymentId"]
    return {**run, "payment_id": payment_id,
            "review": live.review(payment_id, "approve", REVIEWER)}


@pytest.fixture(scope="module")
def rejected():
    run = _claim(AMOUNT)
    assert run["reply"]["status"] == "awaiting_approval", run
    payment_id = run["reply"]["paymentId"]
    return {**run, "payment_id": payment_id,
            "review": live.review(payment_id, "reject", REVIEWER)}


def test_assisted_5800_waits_with_rule_and_a_zero_limit(approved):
    # The ledger keeps the refusal beside the payment; the task's own reply
    # carries the same rule and limit for the customer.
    assert approved["reply"]["rule"] == "settlement-agent.assisted.execute_limit", approved["reply"]
    assert approved["reply"]["limit"] == 0.0, approved["reply"]
    refusal = live.get_payment(approved["payment_id"])["refusal"]
    assert refusal["rule"] == "settlement-agent.assisted.execute_limit", refusal
    assert refusal["limit"] == 0.0, refusal
    assert refusal["requested"] == AMOUNT, refusal


def test_assisted_5800_is_input_required_then_approve_completes_it(approved):
    assert approved["state"] == "input-required"
    assert approved["review"]["resumedState"] is not None, approved["review"]
    with live.claims_agent_route():
        task = live.get_task(approved["task_id"])
    assert task["state"] == "completed"
    assert task["reply"]["status"] == "executed" and task["reply"]["decision"] == "approved"


def test_reject_completes_the_task_declined(rejected):
    assert rejected["review"]["resumedState"] is not None, rejected["review"]
    with live.claims_agent_route():
        task = live.get_task(rejected["task_id"])
    assert task["state"] == "completed" and task["reply"]["status"] == "rejected"
    assert task["reply"]["decision"] == "rejected"


def test_autonomous_5800_completes_with_no_wait_and_6200_waits():
    with live.autonomy(contract.AUTONOMOUS):
        settled = _claim_at_current_level(5800.0)
        waits = _claim_at_current_level(6200.0)
        try:
            assert settled["state"] == "completed" and settled["reply"]["status"] == "executed"
            assert waits["state"] == "input-required"
            assert waits["reply"]["rule"] == "settlement-agent.autonomous.execute_limit"
            assert waits["reply"]["limit"] == 6000.0
        finally:
            live.review(waits["reply"]["paymentId"], "reject", "demo-cleanup")


def test_a_resubscribe_held_open_ten_minutes_delivers_completed():
    run = _claim(AMOUNT)
    result = {}
    with live.claims_agent_route():
        listener = threading.Thread(target=lambda: result.update(
            live.resubscribe(run["task_id"], timeout=900)))
        listener.start()
        time.sleep(600)
        still_open = listener.is_alive()
        live.review(run["reply"]["paymentId"], "approve", REVIEWER)
        listener.join(timeout=60)
    assert still_open, f"the stream ended before the decision: {result}"
    assert result.get("state") == "completed", result
    assert result["reply"]["status"] == "executed"


def test_a_dropped_stream_reconnects_and_still_sees_the_outcome():
    run = _claim(AMOUNT)
    with live.claims_agent_route():
        first = live.resubscribe(run["task_id"], timeout=60, drop_after=3)
        assert first.get("dropped"), first
        live.review(run["reply"]["paymentId"], "reject", REVIEWER)
        once = live.get_task(run["task_id"])          # the one read after a drop
        if once["state"] not in live.TERMINAL_STATES:
            once = live.resubscribe(run["task_id"], timeout=60)
    assert once["state"] == "completed" and once["reply"]["status"] == "rejected"


def test_the_ledger_shows_the_wait_before_the_human_acts(approved):
    # Read after the fixture released it: the record keeps what the wait was.
    record = live.get_payment(approved["payment_id"])
    assert record["awaitingSince"], record


def test_approve_releases_the_payment_through_the_pds(approved):
    assert approved["review"]["error"] is None, approved["review"]
    assert approved["review"]["executed"]["status"] == "executed"
    assert live.get_payment(approved["payment_id"])["status"] == "executed"


def test_reject_records_the_reviewer_and_moves_no_money(rejected):
    assert rejected["review"]["error"] is None, rejected["review"]
    assert rejected["review"]["rejected"]["status"] == "rejected"
    assert rejected["review"]["rejected"]["rejection"]["reviewerId"] == REVIEWER
    assert live.get_payment(rejected["payment_id"])["status"] == "rejected"


def _execute_as_reviewer_ui(payment_id: str):
    """execute_payment straight at the gateway, under reviewer-ui's own SVID
    (minted by SPIRE for that subject, the way test_keystone mints tokens)."""
    with live.gateway_port_forward() as gateway_url:
        token = live.mint_svid(live.GATEWAY_AUDIENCE, subject=REVIEWER_UI_SPIFFE_ID)
        return live.call_tool_directly(
            gateway_url, "execute_payment",
            {"payment_id": payment_id, "amount": AMOUNT, "currency": "EUR"},
            {"authorization": f"Bearer {token}"},
        )


def test_execute_after_reject_is_refused(rejected):
    # The reviewer-ui itself: the ledger no longer shows awaiting_approval -> 409.
    with pytest.raises(httpx.HTTPStatusError) as raised:
        live.review(rejected["payment_id"], "approve", REVIEWER)
    assert raised.value.response.status_code == 409
    # The platform: even a direct call under reviewer-ui's identity is denied.
    outcome = _execute_as_reviewer_ui(rejected["payment_id"])
    assert outcome.denial is not None, outcome
    assert outcome.denial.rule == "human-approved.execute"


def test_the_release_was_decided_under_the_human_rule(approved):
    # The payment is already executed, so the human rule must now deny it.
    outcome = _execute_as_reviewer_ui(approved["payment_id"])
    assert outcome.denial is not None, outcome
    assert outcome.denial.rule == "human-approved.execute"


# --- the two bypasses the review found (M8 review I1 and I2) ----------------


@pytest.fixture(scope="module")
def waiting():
    """A payment left awaiting approval and not decided, for the bypass tests.
    Rejected at teardown so the ledger is not left holding a wait."""
    run = _claim(AMOUNT)
    assert run["reply"]["status"] == "awaiting_approval", run
    payment_id = run["reply"]["paymentId"]
    yield payment_id
    try:
        live.review(payment_id, "reject", REVIEWER)
    except httpx.HTTPError:
        pass


_DIRECT = (
    "import urllib.request, urllib.error\n"
    "r = urllib.request.Request("
    "'http://payments-api.northstar-systems.svc.cluster.local:8080/payments/{id}/execute',"
    " method='POST', data=b'{{\"amount\": 5800, \"currency\": \"EUR\"}}',"
    " headers={{'content-type': 'application/json'}})\n"
    "try:\n"
    "    print('STATUS', urllib.request.urlopen(r, timeout=10).status)\n"
    "except urllib.error.HTTPError as e:\n"
    "    print('STATUS', e.code, e.read()[:40])\n"
)


def test_reviewer_ui_cannot_execute_directly_against_payments_api(waiting):
    """payments-api has no caller auth; only Cilium's L7 rule on the
    reviewer-ui's egress stops a direct POST /execute that skips the gateway
    and the PDS. The proxy answers 403 before the ledger sees it."""
    result = live.kubectl(
        "-n", "northstar-agents", "exec", "deploy/reviewer-ui", "--",
        "python", "-c", _DIRECT.format(id=waiting), timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "STATUS 403" in result.stdout, result.stdout
    assert "Access denied" in result.stdout, result.stdout
    assert live.get_payment(waiting)["status"] == "awaiting_approval"


def test_the_agent_cannot_release_a_payment_it_routed_to_a_human_even_at_autonomous(waiting):
    """Maker-checker: the PDS reads the ledger for the agent's execute too, so
    at `autonomous`, under the limit, a payment left waiting is still not the
    agent's to release."""
    with live.autonomy(contract.AUTONOMOUS):
        with live.gateway_port_forward() as gateway_url:
            token = live.mint_svid(live.GATEWAY_AUDIENCE)  # settlement-agent's own
            outcome = live.call_tool_directly(
                gateway_url, "execute_payment",
                {"payment_id": waiting, "amount": AMOUNT, "currency": "EUR"},
                {"authorization": f"Bearer {token}"},
            )
    assert outcome.denial is not None, outcome
    assert outcome.denial.rule == "settlement-agent.payment_not_proposed", outcome.denial
    assert live.get_payment(waiting)["status"] == "awaiting_approval"
