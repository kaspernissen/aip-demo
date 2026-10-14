"""The reviewer's decision, and the `review.decision` span.

`decide()` is this service's one piece of real logic; main.py's job is only
to parse the HTTP form and render whatever this returns. Every figure that
reaches the release call or the span is read back from the ledger at
decision time (`ledger.get(payment_id)`, called fresh here rather than
trusting anything the listing page rendered earlier or anything a form field
might carry) -- amounts, currency, the rule, the limit, the refusal's trace
and span ids, and above all `awaitingSince`, the one clock this function is
allowed to compute `review.elapsed_ms` from. There is deliberately no
"page load" timestamp anywhere in this module: the spec's own words are that
elapsed time measures how long the *human* took, not how long a browser tab
was open, and a timer started when `GET /` was handled would measure the
latter.

**The `review.decision` span opens BEFORE the ledger read.** With no span
current, `HTTPXClientInstrumentor`'s CLIENT span for `ledger.get` would be
the root of a trace of its own, and the `traceparent` it injects would name
that root -- so payments-api's server span for the read would land in a
second, unrelated trace instead of joining the reviewer's own. This project's
whole product is the single correlated trace, so every ledger read a human
decision depends on has to be part of it.

`ledger.get` therefore runs *inside* `review.decision`, and the consequence
is stated rather than hidden: a stale link, a race with another reviewer, or
an unknown payment id **does** produce a `review.decision` span. It carries
the four attributes that are known before the read (payment id, decision,
reviewer id, reviewer SPIFFE ID) and not the two that are read off the record
(`review.refused.trace_id`, `review.elapsed_ms`), and
`start_as_current_span`'s own default `set_status_on_exception`/
`record_exception` mark it ERROR with the exception on it. That is a more
honest record than silence -- a reviewer clicked, and nothing was recorded --
and it is the only shape in which the ledger read is part of the reviewer's
trace at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from opentelemetry import baggage, trace
from opentelemetry import context as otel_context
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags

import approval_metrics
from claims import TaskNotWaiting
from levels import autonomy_level_of

_VALID_DECISIONS = ("approve", "reject")
AWAITING_APPROVAL = "awaiting_approval"
# What the ledger shows once each decision has been taken. A decision that is
# already on the ledger can be *re-told to the customer*, never taken again.
LEDGER_STATUS_OF = {"approve": "executed", "reject": "rejected"}


class PaymentNotAwaitingReview(RuntimeError):
    """The ledger's own record for this payment is not `status:
    "awaiting_approval"` -- already executed, rejected, only proposed, or
    already reviewed by someone else in the meantime. There is nothing
    coherent to record a decision about: a real `awaitingSince` may not even exist, so `review.elapsed_ms` could not be
    computed honestly, and this function raises without ever setting it.

    This raises *inside* the `review.decision` span, which ends ERROR
    carrying this exception -- see the module docstring for why the span
    must already be open when this happens."""


class LedgerLike(Protocol):
    async def get(self, payment_id: str) -> dict[str, Any]: ...

    async def reject(self, payment_id: str, reviewer_id: str) -> dict[str, Any]: ...


class GatewayLike(Protocol):
    async def execute_payment(
        self, payment_id: str, amount: float, currency: str
    ) -> dict[str, Any]: ...


class ClaimsLike(Protocol):
    async def resume(self, task_id: str, payload: dict[str, Any]) -> str: ...


@dataclass
class ReviewResult:
    payment_id: str
    decision: str
    reviewer_id: str
    reviewer_spiffe_id: str
    elapsed_ms: int
    # The autonomy level the refusal happened at, read off the PDS rule name
    # (levels.py).
    autonomy_level: str = "unknown"
    # The ledger record the gateway returned, only ever set on a *successful*
    # release. render.py's honesty rule reads directly off this being None
    # rather than off any success/failure flag of its own.
    executed: dict[str, Any] | None = None
    # The ledger record after a reject; None for an approve.
    rejected: dict[str, Any] | None = None
    # Set when the release or the reject did not happen: the platform's refusal
    # text as it came back, or the failure.
    error: str | None = None
    # The customer's task after the resume was accepted (claims.py), or why it
    # could not be resumed. The money decision stands either way; these only
    # say whether the customer was told.
    resumed_state: str | None = None
    resume_error: str | None = None
    # True when this call took no money decision: the ledger already showed it,
    # and the call only told the customer's waiting task again (see `decide`).
    resent_only: bool = False


def _elapsed_ms(awaiting_since: str, now: datetime) -> int:
    """Milliseconds from the ledger's own `awaitingSince` to `now`.

    `awaiting_since` is payments-api's `time.Now().UTC().Format(time.RFC3339)`
    (systems/payments-api/handlers.go) -- e.g. "2026-09-10T10:00:00Z".
    `datetime.fromisoformat` on the installed Python 3.13 parses the "Z"
    suffix directly, but the explicit replace below makes that independent
    of which Python parses it, rather than relying on a version-specific
    parser behaviour.
    """
    refused_dt = datetime.fromisoformat(awaiting_since.replace("Z", "+00:00"))
    if refused_dt.tzinfo is None:
        refused_dt = refused_dt.replace(tzinfo=timezone.utc)
    delta_ms = (now - refused_dt).total_seconds() * 1000
    # Clamped at zero rather than reported negative: the two clocks involved
    # (payments-api's and this process's) are both container clocks in the
    # same cluster, so a negative delta would be clock skew, not a human
    # deciding before the refusal happened -- and a negative elapsed time is
    # not a number anyone reading this span on stage should have to explain.
    return max(0, round(delta_ms))


async def decide(
    payment_id: str,
    reviewer_id: str,
    decision: str,
    *,
    ledger: LedgerLike,
    gateway: GatewayLike,
    claims: ClaimsLike,
    reviewer_spiffe_id: str,
    now: datetime | None = None,
) -> ReviewResult:
    """Process a reviewer's decision on a payment awaiting approval.

    Raises ValueError for a malformed request (an unknown `decision` value,
    or an empty `reviewer_id`) before any ledger call -- the same
    "fail loudly, never proceed on a guess" discipline
    the platform's own policy holds one hop downstream.

    Raises whatever `ledger.get` raises (ledger.PaymentNotFound,
    ledger.LedgerUnavailable) unchanged, and PaymentNotAwaitingReview if the
    ledger's record for this payment is not `status: "awaiting_approval"` -- in both
    cases from *inside* the `review.decision` span, which ends ERROR carrying
    the exception (see the module docstring).
    """
    reviewer_id = reviewer_id.strip()
    if decision not in _VALID_DECISIONS:
        raise ValueError(
            f"decision must be one of {_VALID_DECISIONS!r}, got {decision!r}"
        )
    if not reviewer_id:
        raise ValueError("reviewer_id is required")

    tracer = trace.get_tracer(__name__)
    # The four attributes below are everything that is known before the ledger
    # is read. The other two (`review.refused.trace_id`, `review.elapsed_ms`)
    # are set after it, because they are read off the record -- the honesty
    # rule this module exists for: no figure on this span comes from the HTTP
    # request.
    with tracer.start_as_current_span(
        "review.decision",
        kind=SpanKind.SERVER,
        attributes={
            "review.payment.id": payment_id,
            "review.decision": decision,
            "review.reviewer.id": reviewer_id,
            "review.reviewer.spiffe_id": reviewer_spiffe_id,
        },
    ) as span:
        # Inside the span deliberately: this is the call whose
        # `HTTPXClientInstrumentor` CLIENT span -- and the `traceparent` it
        # injects -- had no parent to attach to, which put both it and
        # payments-api's own server span in a trace of their own.
        record = await ledger.get(payment_id)
        if (
            record.get("status") == LEDGER_STATUS_OF[decision]
            and record.get("a2aTaskId")
        ):
            # The decision is already on the ledger, and the customer may never
            # have been told (the answer to the release was lost, claims-agent
            # was unreachable, or the decision landed before the task parked).
            # Re-send the resume: claims-agent re-reads the ledger and takes no
            # word from this message, so sending it twice is safe. If the task
            # is no longer waiting there is nothing left to heal, and the
            # caller gets the same 409 as for any decided payment.
            return await _resend_resume(
                span, record, payment_id, reviewer_id, decision,
                reviewer_spiffe_id=reviewer_spiffe_id, claims=claims)
        if record.get("status") != AWAITING_APPROVAL:
            raise PaymentNotAwaitingReview(
                f"payment {payment_id!r}: ledger status is "
                f"{record.get('status')!r}, not 'awaiting_approval' -- nothing to review"
            )

        refusal = record.get("refusal") or {}
        elapsed_ms = _elapsed_ms(
            record.get("awaitingSince", ""), now or datetime.now(timezone.utc)
        )
        span.set_attribute(
            "review.refused.trace_id", refusal.get("refusedTraceId") or ""
        )
        span.set_attribute("review.elapsed_ms", elapsed_ms)
        # The join key the evidence view and eval-scorer read a claim's whole
        # trajectory by: this decision happens minutes after, and in a
        # different trace from, the refusal, and the claim id is what ties the
        # two together (Gap 4). Read off the ledger record, like everything
        # else on this span.
        trace_id, span_id = refusal.get("refusedTraceId"), refusal.get("refusedSpanId")
        if trace_id and span_id:
            # Trace B (this decision) is its own trace; the link is what ties it
            # back to trace A, where the refusal happened. Added after start
            # because the ids come from the ledger read, which must itself run
            # inside the span (module docstring). The SDK keeps a valid link
            # and drops an invalid one.
            span.add_link(SpanContext(
                trace_id=int(trace_id, 16), span_id=int(span_id, 16),
                is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED)))
        claim_token = None
        if record.get("claimId"):
            span.set_attribute("northstar.claim.id", record["claimId"])
            claim_token = otel_context.attach(
                baggage.set_baggage("northstar.claim.id", record["claimId"]))

        result = ReviewResult(
            payment_id=payment_id,
            decision=decision,
            reviewer_id=reviewer_id,
            reviewer_spiffe_id=reviewer_spiffe_id,
            elapsed_ms=elapsed_ms,
            autonomy_level=autonomy_level_of(refusal.get("rule")),
        )

        try:
            try:
                if decision == "approve":
                    # The ledger's amount and currency, never a form field. The
                    # PDS still decides, and reads the ledger status itself: a
                    # check this function did for itself would be a check a
                    # compromised reviewer-ui skips.
                    result.executed = await gateway.execute_payment(
                        payment_id, record["amount"], record["currency"]
                    )
                else:
                    result.rejected = await ledger.reject(payment_id, reviewer_id)
            except Exception as exc:  # noqa: BLE001 -- surfaced on the result and the span
                # Surface the failure honestly rather than showing a success: the
                # span records that this decision did not go through, and
                # result.error is what render.py reads to say so on the page. The
                # decision attribute above is left as the reviewer chose it.
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                result.error = str(exc)

            # Tell the customer, only after the money decision went through. The
            # claims-agent does not trust this message: it reads the ledger.
            if result.error is None and record.get("a2aTaskId"):
                try:
                    result.resumed_state = await claims.resume(record["a2aTaskId"], {
                        "decision": "approved" if decision == "approve" else "rejected",
                        "paymentId": payment_id, "reviewerId": reviewer_id})
                except Exception as exc:  # noqa: BLE001 -- the money decision stands; say so
                    result.resume_error = str(exc)

            if result.error is None:
                approval_metrics.record_wait(
                    elapsed_ms / 1000.0,
                    decision="approved" if decision == "approve" else "rejected",
                    autonomy_level=result.autonomy_level)
        finally:
            if claim_token is not None:
                otel_context.detach(claim_token)

        return result


async def _resend_resume(
    span, record: dict[str, Any], payment_id: str, reviewer_id: str, decision: str,
    *, reviewer_spiffe_id: str, claims: ClaimsLike,
) -> ReviewResult:
    """Tell the customer's still-waiting task about a decision the ledger holds.

    No money moves and no `review.elapsed_ms` is set: the human's wait was
    measured by the call that took the decision, and a second figure read off
    `awaitingSince` now would be a wrong one, so the span says what it is
    (`review.resume_only`) and the histogram records nothing.
    """
    span.set_attribute("review.resume_only", True)
    claim_token = None
    if record.get("claimId"):
        span.set_attribute("northstar.claim.id", record["claimId"])
        claim_token = otel_context.attach(
            baggage.set_baggage("northstar.claim.id", record["claimId"]))
    refusal = record.get("refusal") or {}
    result = ReviewResult(
        payment_id=payment_id, decision=decision, reviewer_id=reviewer_id,
        reviewer_spiffe_id=reviewer_spiffe_id, elapsed_ms=0,
        autonomy_level=autonomy_level_of(refusal.get("rule")),
        resent_only=True,
        executed=record if decision == "approve" else None,
        rejected=record if decision == "reject" else None,
    )
    try:
        result.resumed_state = await claims.resume(record["a2aTaskId"], {
            "decision": "approved" if decision == "approve" else "rejected",
            "paymentId": payment_id, "reviewerId": reviewer_id})
    except TaskNotWaiting as exc:
        raise PaymentNotAwaitingReview(
            f"payment {payment_id!r}: ledger status is {record.get('status')!r} "
            f"and the customer's task is not waiting ({exc}) -- nothing to heal") from exc
    except Exception as exc:  # noqa: BLE001 -- the decision stands; say the customer is still untold
        result.resume_error = str(exc)
    finally:
        if claim_token is not None:
            otel_context.detach(claim_token)
    return result
