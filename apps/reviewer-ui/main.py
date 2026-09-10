"""reviewer-ui's FastAPI app.

An A2A *client* only: it serves no agent card and answers no A2A request of
its own, so unlike claims-agent/settlement-agent there is no
A2AStarletteApplication here, and no agentgateway/payments-mcp/Policy
Decision Service anywhere on this service's egress
(deploy/platform/cilium-policies.yaml's reviewer-ui-egress-allowlist is the
enforced version of that). Its two peers are payments-api (ledger.py, a plain
REST GET) and senior-settlement-agent (approval.py, a raw A2A JSON-RPC POST).
"""
import bootstrap  # noqa: F401  configures OTel before anything else runs

import os

import uvicorn
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

import approval
import identity
import ledger
import render
import review

app = FastAPI()

ledger_client = ledger.LedgerClient(
    os.environ.get(
        "PAYMENTS_API_URL",
        "http://payments-api.northstar-systems.svc.cluster.local:8080",
    )
)

approval_client = approval.ApprovalClient(
    os.environ.get(
        "SENIOR_SETTLEMENT_AGENT_URL",
        "http://senior-settlement-agent.northstar-agents.svc.cluster.local:9002",
    )
)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index():
    try:
        payments = await ledger_client.list_refused()
    except ledger.LedgerUnavailable as exc:
        return HTMLResponse(render.error_page(str(exc)), status_code=502)
    return HTMLResponse(render.list_page(payments))


@app.post("/review/{payment_id}", response_class=HTMLResponse)
async def submit_review(
    payment_id: str, reviewer_id: str = Form(...), decision: str = Form(...)
):
    try:
        reviewer_spiffe_id = await identity.workload_spiffe_id()
    except identity.IdentityUnavailable as exc:
        return HTMLResponse(render.error_page(str(exc)), status_code=502)

    try:
        result = await review.decide(
            payment_id,
            reviewer_id,
            decision,
            ledger=ledger_client,
            approver=approval_client,
            reviewer_spiffe_id=reviewer_spiffe_id,
        )
    except ValueError as exc:
        return HTMLResponse(render.error_page(str(exc)), status_code=400)
    except ledger.PaymentNotFound as exc:
        return HTMLResponse(render.error_page(str(exc)), status_code=404)
    except review.PaymentNotAwaitingReview as exc:
        return HTMLResponse(render.error_page(str(exc)), status_code=409)
    except ledger.LedgerUnavailable as exc:
        return HTMLResponse(render.error_page(str(exc)), status_code=502)

    outcome = render.ResultView(
        payment_id=result.payment_id,
        decision=result.decision,
        reviewer_id=result.reviewer_id,
        elapsed_ms=result.elapsed_ms,
        executed=result.approved_execution,
        error=result.approval_error,
    )
    return HTMLResponse(render.result_page(outcome))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
