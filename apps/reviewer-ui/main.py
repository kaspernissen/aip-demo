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
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

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


# The reviewer's queue: every refusal the ledger is currently holding for a
# human. One owned span, `review.queue`, added in the M3 final-review fix
# round (controller ruling R36) for the same reason `review.decision` gained
# its ledger read -- without a current span, `HTTPXClientInstrumentor`'s
# CLIENT span for `list_refused()` was the root of a trace of its own, and
# payments-api's server span for the read landed in yet another. Measured
# before the change: zero shared trace ids across the 30 most recent traces
# of each shape.
#
# **How the name was chosen, since an authored span name is a claim.** This
# repo's owned spans are `<domain>.<the thing this span is>`, never a
# framework's method-and-route: `claim.submit` (customer-ui),
# `policy.decision` (the PDS), `review.decision` (review.py), and the
# telemetry contract's "Domain attributes we own" table is keyed by that
# prefix. `review.queue` is the same shape under the same `review.` domain
# this service already owns: the reviewer's queue of refusals awaiting a
# decision, which is exactly what this handler produces. It is deliberately
# NOT `GET /` (that is the framework's name for a route, and this service
# applies no framework instrumentation on purpose -- bootstrap.py) and not
# `review.list` (which names the HTTP verb's shape rather than the domain
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
    with tracer.start_as_current_span("review.queue", kind=SpanKind.SERVER) as span:
        try:
            payments = await ledger_client.list_refused()
        except ledger.LedgerUnavailable as exc:
            # The same honesty rule review.py keeps for a failed approval: the
            # span says the read did not work, rather than ending Unset beside
            # a 502 the page is about to render.
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
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
