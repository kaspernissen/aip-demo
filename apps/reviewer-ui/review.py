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

**Correction, M3 final-review fix round 2026-09-11 (controller ruling R36) --
the span now opens BEFORE the ledger read, and this paragraph used to say the
opposite.** It read:

> The span is only ever created once a real, refused ledger record for this
> exact payment is in hand (`PaymentNotAwaitingReview` and a ledger lookup
> failure both raise *before* `tracer.start_as_current_span`) -- a review that
> never actually happened (a stale link, a race with another reviewer, an
> unknown payment id) is not telemetry this service owns to invent.

That was a defensible rule about *which reviews deserve a span*, and it had a
measured cost nobody had looked for until the whole-branch review did: with no
span current, `HTTPXClientInstrumentor`'s CLIENT span for `ledger.get` became
the root of a **trace of its own**, and the `traceparent` it injected named
that root -- so payments-api's server span for the read landed in a second,
unrelated trace too. Measured before the change, over the 30 most recent
traces of each shape: **zero** shared trace ids between reviewer-ui's outbound
`GET` spans and payments-api's `GET /payments` spans, and a 67-span approval
trace containing no reviewer-ui ledger read at all. This project's whole
product is the single correlated trace, and this milestone's honesty story is
that every figure the human reads comes from the ledger; those reads being
untraceable was that story failing in the one surface M3 added.

So `ledger.get` now runs *inside* `review.decision`, and the consequence is
stated rather than hidden: a stale link, a race with another reviewer or an
unknown payment id **does** now produce a `review.decision` span. It carries
the four attributes that are known before the read (payment id, decision,
reviewer id, reviewer SPIFFE ID) and not the two that are read off the record
(`review.refused.trace_id`, `review.elapsed_ms`), and
`start_as_current_span`'s own default `set_status_on_exception`/
`record_exception` mark it ERROR with the exception on it. That is a more
honest record than the silence it replaces -- a reviewer clicked, and nothing
was recorded -- and it is the only shape in which the ledger read is part of
the reviewer's trace at all.
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
    computed honestly, and this function raises without ever setting it.

    This sentence ended "and this function raises before creating any span"
    until the M3 final-review fix round (R36). It now raises *inside* the
    `review.decision` span, which ends ERROR carrying this exception -- see
    the module docstring's correction note for the measurement that forced
    the change."""


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
    cases from *inside* the `review.decision` span, which ends ERROR carrying
    the exception (R36; see the module docstring).
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
    # rule this module exists for, unchanged by R36: no figure on this span
    # comes from the HTTP request.
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
        # Inside the span deliberately (R36): this is the call whose
        # `HTTPXClientInstrumentor` CLIENT span -- and the `traceparent` it
        # injects -- had no parent to attach to, which put both it and
        # payments-api's own server span in a trace of their own.
        record = await ledger.get(payment_id)
        if record.get("status") != "refused":
            raise PaymentNotAwaitingReview(
                f"payment {payment_id!r}: ledger status is "
                f"{record.get('status')!r}, not 'refused' -- nothing to review"
            )

        refusal = record.get("refusal") or {}
        elapsed_ms = _elapsed_ms(
            refusal.get("refusedAt", ""), now or datetime.now(timezone.utc)
        )
        span.set_attribute(
            "review.refused.trace_id", refusal.get("refusedTraceId") or ""
        )
        span.set_attribute("review.elapsed_ms", elapsed_ms)

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
