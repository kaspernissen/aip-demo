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
    claim_id = f"CLM-HITL-{uuid.uuid4().hex[:8]}"
    contract.assert_assisted("before a maker-checker claim")
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
    # Read from the ledger, which keeps the refusal beside the payment: the
    # claims-agent reply carries neither the rule nor the limit.
    refusal = live.get_payment(approved["payment_id"])["refusal"]
    assert refusal["rule"] == "settlement-agent.assisted.execute_limit", refusal
    assert refusal["limit"] == 0.0, refusal
    assert refusal["requested"] == AMOUNT, refusal


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
