"""Triage: a claim decided on the ledger whose customer was never told.

A claim filed in the customer-ui and decided in the reviewer-ui can be decided
on the ledger while the resume to the claims-agent is lost. The payment has left
`/api/awaiting`, the reviewer page has no button for it, and the customer's page
stays "Awaiting human approval" because the A2A task is still `input-required`.
`scripts/demo approve|reject <claim|last>` finds it from the ledger and the
task, and re-sends the decision through the reviewer-ui's own JSON route, which
tells the claims-agent again and never moves money twice.

**How the lost resume is made, and what that does and does not claim.** The
harness files the claim (the customer-ui's path, without the browser) and then
takes the money decision straight at payments-api (`/execute` or `/reject`,
reachable through the host's port-forward), which is the ledger state the
reviewer-ui's own release or reject leaves, minus the resume it would have sent
next. The decision therefore skips the gateway and the PDS here, and nothing in
this file claims otherwise; what is under test is the heal: the driver's
discovery, the reviewer-ui's re-send and the claims-agent's completion.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest

from tests.harness import contract, live

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO = REPO_ROOT / "scripts" / "demo"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
AMOUNT = 5800.0

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason=(
        "needs the live stage-7 cluster and spends one model settlement per claim; "
        "gated behind the same RUN_BROWSER_TESTS=1 flag scripts/smoke-test.sh sets "
        "unconditionally"
    ),
)


def _demo(state_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DEMO_STATE_DIR": str(state_dir)}
    env.pop("DEMO_COLOR", None)
    result = subprocess.run([str(DEMO), *args], capture_output=True, text=True,
                            timeout=240, env=env, cwd=REPO_ROOT)
    result.stdout, result.stderr = ANSI.sub("", result.stdout), ANSI.sub("", result.stderr)
    return result


def _lost_resume(decision: str) -> dict:
    """File a claim, then decide it on the ledger only. Returns the run plus the id."""
    contract.assert_assisted("before a lost-resume claim")
    claim_id = f"CLM-LOST-{uuid.uuid4().hex[:6].upper()}"
    with live.claims_agent_route() as url:
        run = live.submit_claim(claim_id, AMOUNT, claims_agent_url=url, timeout=170)
    assert run["reply"]["status"] == "awaiting_approval", run
    payment_id = run["reply"]["paymentId"]
    assert live.get_payment(payment_id).get("a2aTaskId") == run["task_id"], (
        "the ledger must carry the task id the heal re-sends to")
    if decision == "approve":
        response = httpx.post(f"{live.PAYMENTS_API_URL}/payments/{payment_id}/execute",
                              json={"amount": AMOUNT, "currency": "EUR"}, timeout=15)
    else:
        response = httpx.post(f"{live.PAYMENTS_API_URL}/payments/{payment_id}/reject",
                              json={"reviewerId": "lost-resume-test"}, timeout=15)
    assert response.status_code == 200, (response.status_code, response.text)
    # The state the customer is stuck in: decided on the ledger, task still waiting.
    assert live.get_payment(payment_id)["status"] == ("executed" if decision == "approve" else "rejected")
    assert live.get_task(run["task_id"])["state"] == "input-required"
    assert payment_id not in [p["paymentId"] for p in httpx.get(
        f"{live.REVIEWER_UI_URL}/api/awaiting", timeout=15).json()], "it must have left the queue"
    return {**run, "claim_id": claim_id, "payment_id": payment_id}


def test_approve_last_heals_a_claim_whose_resume_was_lost_and_the_task_settles(tmp_path):
    run = _lost_resume("approve")
    out = _demo(tmp_path, "approve", "last")
    assert out.returncode == 0, f"{out.stdout}\n{out.stderr}"
    assert run["claim_id"] in out.stdout and "already on the ledger" in out.stdout, out.stdout
    assert "[completed]" in out.stdout, out.stdout
    task = live.get_task(run["task_id"])
    assert task["state"] == "completed", task
    assert task["reply"]["status"] == "executed" and task["reply"]["decision"] == "approved", task
    # Money moved once: the ledger record is the one the direct release made.
    assert live.get_payment(run["payment_id"])["status"] == "executed"
    # Nothing is left to heal.
    again = _demo(tmp_path, "approve", "last")
    assert again.returncode == 1 and "no claim is waiting" in again.stderr, again.stderr
    (claim,) = json.loads((tmp_path / "state.json").read_text())["claims"]
    assert claim["id"] == run["claim_id"] and claim["status"] == "executed", claim


def test_reject_by_claim_id_heals_a_rejected_claim_and_refuses_the_other_direction(tmp_path):
    run = _lost_resume("reject")
    wrong = _demo(tmp_path, "approve", run["claim_id"])
    assert wrong.returncode == 1 and "already rejected on the ledger" in wrong.stderr, wrong.stderr
    assert live.get_task(run["task_id"])["state"] == "input-required", "a refusal must change nothing"
    out = _demo(tmp_path, "reject", run["claim_id"])
    assert out.returncode == 0, f"{out.stdout}\n{out.stderr}"
    task = live.get_task(run["task_id"])
    assert task["state"] == "completed" and task["reply"]["status"] == "rejected", task
