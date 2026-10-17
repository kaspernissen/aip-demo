"""reviewer-ui's FastAPI app: the human half of maker-checker.

The settlement agent prepares every payment; above its band it leaves the
payment `awaiting_approval`, and a human decides here. Two peers, and no
other egress (deploy/platform/cilium-policies.yaml's reviewer-ui-egress-
allowlist is the enforced version of that): payments-api (ledger.py, plain
REST: read the queue, reject) and agentgateway (gateway.py: release the
money, as an `execute_payment` tool call under this workload's own SPIFFE
ID). The PDS permits that call only for a payment it reads as awaiting
approval from the ledger itself, so this service cannot release anything the
ledger does not say is waiting.
"""
import bootstrap  # noqa: F401  configures OTel before anything else runs

import os

import uvicorn
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

import claims
import gateway
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

gateway_client = gateway.GatewayClient(
    os.environ.get(
        "AGENTGATEWAY_MCP_URL",
        "http://agentgateway.northstar-platform.svc.cluster.local:3000/mcp",
    )
)

claims_client = claims.ClaimsClient(
    os.environ.get(
        "CLAIMS_AGENT_URL",
        "http://claims-agent.northstar-agents.svc.cluster.local:9001",
    )
)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# The reviewer's queue: every payment the ledger is currently holding for a
# human. One owned span, `northstar.review.queue`, wraps the ledger read for the same
# reason `northstar.review.decision` does -- without a current span,
# `HTTPXClientInstrumentor`'s CLIENT span for `list_awaiting()` would be the
# root of a trace of its own, and payments-api's server span for the read
# would land in yet another.
#
# **How the name was chosen, since an authored span name is a claim.** This
# repo's owned spans are `<domain>.<the thing this span is>`, never a
# framework's method-and-route: `northstar.claim.submit` (customer-ui),
# `northstar.policy.decision` (the PDS), `northstar.review.decision` (review.py), and the
# telemetry contract's "Domain attributes we own" table is keyed by that
# prefix. `northstar.review.queue` is the same shape under the same `northstar.review.` domain
# this service already owns: the reviewer's queue of payments awaiting a
# decision, which is exactly what this handler produces. It is deliberately
# NOT `GET /` (that is the framework's name for a route, and this service
# applies no framework instrumentation on purpose -- bootstrap.py) and not
# `northstar.review.list` (which names the HTTP verb's shape rather than the domain
# object).
#
# It carries **no attributes**, also deliberately. Every figure on this page
# is a ledger record, and payments-api's own `GET /payments` span -- now in
# this trace, which is the whole point -- is what describes the read. Adding
# a seventh `review.*` key to publish a count would widen an inventory
# docs/telemetry-contract.md states as six, for a question no one asked.
@app.get("/", response_class=HTMLResponse)
async def index():
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("northstar.review.queue", kind=SpanKind.SERVER) as span:
        try:
            payments = await ledger_client.list_awaiting()
        except ledger.LedgerUnavailable as exc:
            # The same honesty rule review.py keeps for a failed approval: the
            # span says the read did not work, rather than ending Unset beside
            # a 502 the page is about to render.
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            return HTMLResponse(render.error_page(str(exc)), status_code=502)
        return HTMLResponse(render.list_page(payments))


async def _decide(payment_id: str, reviewer_id: str, decision: str):
    """Run review.decide and return (result, None) or (None, (status, message)).
    One mapping of exceptions to statuses for both the form and the JSON route."""
    try:
        reviewer_spiffe_id = await identity.workload_spiffe_id()
    except identity.IdentityUnavailable as exc:
        return None, (502, str(exc))

    try:
        result = await review.decide(
            payment_id,
            reviewer_id,
            decision,
            ledger=ledger_client,
            gateway=gateway_client,
            claims=claims_client,
            reviewer_spiffe_id=reviewer_spiffe_id,
        )
    except ValueError as exc:
        return None, (400, str(exc))
    except ledger.PaymentNotFound as exc:
        return None, (404, str(exc))
    except (review.PaymentNotAwaitingReview, ledger.PaymentNotAwaiting) as exc:
        return None, (409, str(exc))
    except ledger.LedgerUnavailable as exc:
        return None, (502, str(exc))
    return result, None


@app.post("/review/{payment_id}", response_class=HTMLResponse)
async def submit_review(
    payment_id: str, reviewer_id: str = Form(...), decision: str = Form(...)
):
    result, failure = await _decide(payment_id, reviewer_id, decision)
    if failure is not None:
        status, message = failure
        return HTMLResponse(render.error_page(message), status_code=status)

    outcome = render.ResultView(
        payment_id=result.payment_id,
        decision=result.decision,
        reviewer_id=result.reviewer_id,
        elapsed_ms=result.elapsed_ms,
        executed=result.executed,
        error=result.error,
        rejected=result.rejected,
        resumed_state=result.resumed_state,
        resume_error=result.resume_error,
    )
    return HTMLResponse(render.result_page(outcome))


# The JSON routes the demo driver and the e2e tests use: the same queue and the
# same decision as the page, without the HTML.
@app.get("/api/awaiting")
async def api_awaiting():
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("northstar.review.queue", kind=SpanKind.SERVER) as span:
        try:
            return await ledger_client.list_awaiting()
        except ledger.LedgerUnavailable as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            return JSONResponse({"error": str(exc)}, status_code=502)


class ReviewRequest(BaseModel):
    reviewerId: str
    decision: str


@app.post("/api/review/{payment_id}")
async def api_review(payment_id: str, body: ReviewRequest):
    result, failure = await _decide(payment_id, body.reviewerId, body.decision)
    if failure is not None:
        status, message = failure
        return JSONResponse({"error": message}, status_code=status)
    return {
        "paymentId": result.payment_id,
        "decision": result.decision,
        "reviewerId": result.reviewer_id,
        "elapsedMs": result.elapsed_ms,
        "autonomyLevel": result.autonomy_level,
        "executed": result.executed,
        "rejected": result.rejected,
        "error": result.error,
        "resumedState": result.resumed_state,
        "resumeError": result.resume_error,
        "resentOnly": result.resent_only,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
