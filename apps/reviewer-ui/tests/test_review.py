"""review.py: the reviewer's decision, and the review.decision span.

Every figure that reaches the span or the approval payload is read back from
the ledger at decision time -- never from whatever the listing page rendered
earlier, and never recomputed. In particular `review.elapsed_ms` is derived
from the ledger's own `refusedAt`, measured against a `now` this test controls
-- not from a timer this process happened to start when a page loaded (the
spec's own words, and the property a implementer could most easily get wrong
by wiring up `time.monotonic()` in main.py instead).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

import review
from review import PaymentNotAwaitingReview, decide

REFUSED_PAYMENT = {
    "paymentId": "PAY-000053",
    "claimId": "CLM-1842",
    "amount": 6200.0,
    "currency": "EUR",
    "status": "refused",
    "refusal": {
        "rule": "settlement-agent.assisted.execute_limit",
        "limit": 6000.0,
        "requested": 6200.0,
        "narrative": "I'm unable to complete this payment myself.",
        "refusedTraceId": "331edba8cb21223e9de2f244360c42ef",
        "refusedSpanId": "b7997877797c1b27",
        "refusedAt": "2026-09-10T10:00:00+00:00",
    },
}

NOW = datetime(2026, 9, 10, 10, 0, 5, tzinfo=timezone.utc)  # 5s after refusedAt
REVIEWER_SPIFFE_ID = "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"


class FakeLedger:
    def __init__(self, record=None, get_error=None):
        self.calls: list[tuple] = []
        self._record = record if record is not None else dict(REFUSED_PAYMENT)
        self._get_error = get_error
        # What `HTTPXClientInstrumentor` would read in production to parent
        # this read's CLIENT span and to build the `traceparent` it injects.
        self.span_context_at_get: object | None = None

    async def get(self, payment_id):
        self.calls.append(("get", payment_id))
        self.span_context_at_get = trace.get_current_span().get_span_context()
        if self._get_error is not None:
            raise self._get_error
        return self._record


class FakeApprover:
    def __init__(self, result=None, error=None):
        self.calls: list[dict] = []
        self._result = result if result is not None else {
            "paymentId": "PAY-000053",
            "amount": 6200.0,
            "currency": "EUR",
            "status": "executed",
            "reviewerId": "alex.mercer",
        }
        self._error = error

    async def execute_approved(self, payload):
        self.calls.append(payload)
        if self._error is not None:
            raise self._error
        return self._result


@pytest.fixture
def traced(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr(review.trace, "get_tracer", lambda name: tracer)
    return exporter


async def test_approve_calls_the_approver_with_ledger_sourced_figures(traced):
    ledger = FakeLedger()
    approver = FakeApprover()

    await decide(
        "PAY-000053", "alex.mercer", "approve",
        ledger=ledger, approver=approver,
        reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )

    assert len(approver.calls) == 1
    payload = approver.calls[0]
    # Every figure traces to REFUSED_PAYMENT above, not to any value this
    # function might have been tempted to accept as an argument instead.
    assert payload == {
        "paymentId": "PAY-000053",
        "amount": 6200.0,
        "currency": "EUR",
        "approval": {"reviewerId": "alex.mercer"},
        "refusedTraceId": "331edba8cb21223e9de2f244360c42ef",
        "refusedSpanId": "b7997877797c1b27",
    }


async def test_reject_never_calls_the_approver(traced):
    ledger = FakeLedger()
    approver = FakeApprover()

    result = await decide(
        "PAY-000053", "alex.mercer", "reject",
        ledger=ledger, approver=approver,
        reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )

    assert approver.calls == []
    assert result.decision == "reject"
    assert result.approved_execution is None


async def test_the_review_decision_span_carries_exactly_the_spec_attributes(traced):
    exporter = traced
    await decide(
        "PAY-000053", "alex.mercer", "approve",
        ledger=FakeLedger(), approver=FakeApprover(),
        reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "review.decision"
    assert span.kind == SpanKind.SERVER
    assert dict(span.attributes) == {
        "review.payment.id": "PAY-000053",
        "review.decision": "approve",
        "review.reviewer.id": "alex.mercer",
        "review.reviewer.spiffe_id": REVIEWER_SPIFFE_ID,
        "review.refused.trace_id": "331edba8cb21223e9de2f244360c42ef",
        "review.elapsed_ms": 5000,
    }


async def test_elapsed_ms_is_measured_from_the_ledgers_refusedat_not_a_page_load_timer(traced):
    """The property the spec calls out by name. `now` here is 5 seconds after
    the ledger's own refusedAt -- there is no other clock in this function's
    signature for it to have used."""
    result = await decide(
        "PAY-000053", "alex.mercer", "reject",
        ledger=FakeLedger(), approver=FakeApprover(),
        reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )
    assert result.elapsed_ms == 5000


async def test_a_reject_decision_still_gets_a_span_with_review_decision_reject(traced):
    exporter = traced
    await decide(
        "PAY-000053", "alex.mercer", "reject",
        ledger=FakeLedger(), approver=FakeApprover(),
        reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )
    span = exporter.get_finished_spans()[0]
    assert span.attributes["review.decision"] == "reject"


async def test_an_approval_that_the_senior_agent_refuses_is_reported_not_hidden(traced):
    """UnverifiedApproval, or any other failure from the approver, must reach
    the caller as a fact on the result -- never silently reported as if the
    payment executed."""
    exporter = traced
    approver = FakeApprover(error=RuntimeError(
        "senior-settlement-agent returned an error: payment PAY-000053: "
        "ledger status is 'executed', not 'refused'"
    ))

    result = await decide(
        "PAY-000053", "alex.mercer", "approve",
        ledger=FakeLedger(), approver=approver,
        reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )

    assert result.approved_execution is None
    assert result.approval_error is not None
    assert "ledger status is 'executed'" in result.approval_error

    span = exporter.get_finished_spans()[0]
    assert span.status.status_code == StatusCode.ERROR
    # The decision itself is still what the reviewer chose, regardless of
    # whether the execution the reviewer asked for actually happened.
    assert span.attributes["review.decision"] == "approve"


async def test_a_payment_not_awaiting_review_ends_the_span_error_and_never_approves(traced):
    """Racing another reviewer, or a stale link to an already-executed
    payment.

    A `review.decision` span exists and ends ERROR carrying the exception,
    which is a more honest record than silence: a reviewer clicked, and
    nothing could be recorded otherwise. The approver is never called, and
    the two ledger-derived attributes are absent, because there was no
    usable record to read them off.
    """
    ledger = FakeLedger(record={**REFUSED_PAYMENT, "status": "executed"})
    approver = FakeApprover()

    with pytest.raises(PaymentNotAwaitingReview):
        await decide(
            "PAY-000053", "alex.mercer", "approve",
            ledger=ledger, approver=approver,
            reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
        )

    assert approver.calls == []
    spans = traced.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "review.decision"
    assert span.status.status_code == StatusCode.ERROR
    assert dict(span.attributes) == {
        "review.payment.id": "PAY-000053",
        "review.decision": "approve",
        "review.reviewer.id": "alex.mercer",
        "review.reviewer.spiffe_id": REVIEWER_SPIFFE_ID,
    }


async def test_the_ledger_read_happens_inside_the_review_decision_span(traced):
    """The property that matters, asserted where it actually lives:
    `ledger.get` must run with `review.decision` current.

    That span context is exactly what `HTTPXClientInstrumentor` parents its
    CLIENT span off and what it writes into the injected `traceparent`, so
    this is the difference between payments-api's server span joining the
    reviewer's trace and starting a second one. Measured before the fix: 30
    reviewer-ui `GET` spans and 30 payments-api `GET /payments` spans sharing
    zero trace ids.
    """
    ledger = FakeLedger()
    await decide(
        "PAY-000053", "alex.mercer", "approve",
        ledger=ledger, approver=FakeApprover(),
        reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )

    span = traced.get_finished_spans()[0]
    assert span.name == "review.decision"
    assert ledger.span_context_at_get is not None
    assert ledger.span_context_at_get.is_valid, (
        "ledger.get ran with no valid span current, so its outbound CLIENT "
        "span would root a trace of its own"
    )
    assert ledger.span_context_at_get.span_id == span.context.span_id


async def test_an_unknown_decision_value_is_rejected_before_any_ledger_call(traced):
    ledger = FakeLedger()
    with pytest.raises(ValueError):
        await decide(
            "PAY-000053", "alex.mercer", "maybe",
            ledger=ledger, approver=FakeApprover(),
            reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
        )
    assert ledger.calls == []


async def test_an_empty_reviewer_id_is_rejected_before_any_ledger_call(traced):
    ledger = FakeLedger()
    with pytest.raises(ValueError):
        await decide(
            "PAY-000053", "  ", "approve",
            ledger=ledger, approver=FakeApprover(),
            reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
        )
    assert ledger.calls == []


async def test_a_ledger_lookup_failure_propagates_and_ends_the_span_error(traced):
    """The exception propagates unchanged, which is what main.py maps to a
    404/502, and it also lands on a `review.decision` span rather than
    vanishing -- for the same reason the not-awaiting-review test above
    records."""
    ledger = FakeLedger(get_error=RuntimeError("payment not found"))
    with pytest.raises(RuntimeError, match="payment not found"):
        await decide(
            "PAY-999999", "alex.mercer", "approve",
            ledger=ledger, approver=FakeApprover(),
            reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
        )
    spans = traced.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "review.decision"
    assert spans[0].status.status_code == StatusCode.ERROR
    assert "review.elapsed_ms" not in spans[0].attributes
