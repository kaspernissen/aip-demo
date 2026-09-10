"""The reviewer's decision, and the `review.decision` span.

`decide()` is this service's one piece of real logic; main.py's job is only
to parse the HTTP form and render whatever this returns. Every figure that
reaches the approval payload or the span is read back from the ledger at
decision time (`ledger.get(payment_id)`, called fresh here rather than
trusting anything the listing page rendered earlier or anything a form field
might carry) -- amounts, currency, the rule, the limit, the refusal's trace
and span ids, and above all `refusedAt`, the one clock this function is
allowed to compute `review.elapsed_ms` from. There is deliberately no
"page load" timestamp anywhere in this module: the spec's own words are that
elapsed time measures how long the *human* took, not how long a browser tab
was open, and a timer started when `GET /` was handled would measure the
latter.

The span is only ever created once a real, refused ledger record for this
exact payment is in hand (`PaymentNotAwaitingReview` and a ledger lookup
failure both raise *before* `tracer.start_as_current_span`) -- a review that
never actually happened (a stale link, a race with another reviewer, an
unknown payment id) is not telemetry this service owns to invent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

_VALID_DECISIONS = ("approve", "reject")


class PaymentNotAwaitingReview(RuntimeError):
    """The ledger's own record for this payment is not `status: "refused"` --
    already executed, only proposed, or already reviewed by someone else in
    the meantime. There is nothing coherent to record a decision about: a
    real `refusedAt` may not even exist, so `review.elapsed_ms` could not be
    computed honestly, and this function raises before creating any span."""


class LedgerLike(Protocol):
    async def get(self, payment_id: str) -> dict[str, Any]: ...


class ApproverLike(Protocol):
    async def execute_approved(self, payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class ReviewResult:
    payment_id: str
    decision: str
    reviewer_id: str
    reviewer_spiffe_id: str
    elapsed_ms: int
    # The ledger's own record of what execute_approved returned, only ever
    # set on a *successful* approval. None for a reject, and None for an
    # approval that failed -- render.py's honesty rule reads directly off
    # this being None rather than off any success/failure flag of its own.
    approved_execution: dict[str, Any] | None = None
    # Set only when decision == "approve" and the senior agent refused or
    # could not be reached. Never set for a reject (there was nothing to
    # attempt).
    approval_error: str | None = None


def _elapsed_ms(refused_at: str, now: datetime) -> int:
    """Milliseconds from the ledger's own `refusedAt` to `now`.

    `refused_at` is payments-api's `time.Now().UTC().Format(time.RFC3339)`
    (systems/payments-api/handlers.go) -- e.g. "2026-09-10T10:00:00Z".
    `datetime.fromisoformat` on the installed Python 3.13 parses the "Z"
    suffix directly, but the explicit replace below makes that independent
    of which Python parses it, rather than relying on a version-specific
    parser behaviour.
    """
    refused_dt = datetime.fromisoformat(refused_at.replace("Z", "+00:00"))
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
    approver: ApproverLike,
    reviewer_spiffe_id: str,
    now: datetime | None = None,
) -> ReviewResult:
    """Process a reviewer's decision on a refused payment.

    Raises ValueError for a malformed request (an unknown `decision` value,
    or an empty `reviewer_id`) before any ledger call -- the same
    "fail loudly, never proceed on a guess" discipline
    agents/settlement-agent/executor.py's `_validate_approval` holds for the
    approval payload one hop downstream.

    Raises whatever `ledger.get` raises (ledger.PaymentNotFound,
    ledger.LedgerUnavailable) unchanged, and PaymentNotAwaitingReview if the
    ledger's record for this payment is not `status: "refused"` -- in both
    cases before any span exists.
    """
    reviewer_id = reviewer_id.strip()
    if decision not in _VALID_DECISIONS:
        raise ValueError(
            f"decision must be one of {_VALID_DECISIONS!r}, got {decision!r}"
        )
    if not reviewer_id:
        raise ValueError("reviewer_id is required")

    record = await ledger.get(payment_id)
    if record.get("status") != "refused":
        raise PaymentNotAwaitingReview(
            f"payment {payment_id!r}: ledger status is "
            f"{record.get('status')!r}, not 'refused' -- nothing to review"
        )

    refusal = record.get("refusal") or {}
    elapsed_ms = _elapsed_ms(refusal.get("refusedAt", ""), now or datetime.now(timezone.utc))

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(
        "review.decision",
        kind=SpanKind.SERVER,
        attributes={
            "review.payment.id": payment_id,
            "review.decision": decision,
            "review.reviewer.id": reviewer_id,
            "review.reviewer.spiffe_id": reviewer_spiffe_id,
            "review.refused.trace_id": refusal.get("refusedTraceId") or "",
            "review.elapsed_ms": elapsed_ms,
        },
    ) as span:
        result = ReviewResult(
            payment_id=payment_id,
            decision=decision,
            reviewer_id=reviewer_id,
            reviewer_spiffe_id=reviewer_spiffe_id,
            elapsed_ms=elapsed_ms,
        )

        if decision == "approve":
            payload = {
                "paymentId": payment_id,
                "amount": record.get("amount"),
                "currency": record.get("currency"),
                "approval": {"reviewerId": reviewer_id},
                "refusedTraceId": refusal.get("refusedTraceId") or "",
                "refusedSpanId": refusal.get("refusedSpanId") or "",
            }
            try:
                result.approved_execution = await approver.execute_approved(payload)
            except Exception as exc:
                # Surface the failure honestly rather than showing a success
                # (the task brief's own words): the span records that this
                # approval did not go through, and result.approval_error is
                # what render.py reads to say so on the page. The decision
                # attribute above is left as "approve" -- that is still what
                # the reviewer chose, independent of whether it executed.
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                result.approval_error = str(exc)

        return result
