"""review.py: the reviewer's decision, and the northstar.review.decision span.

Every figure that reaches the span or the release call is read back from
the ledger at decision time -- never from whatever the listing page rendered
earlier, and never recomputed. In particular `northstar.review.wait.duration` is derived
from the ledger's own `awaitingSince`, measured against a `now` this test controls
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
from gateway import ExecutionRefused
from claims import ResumeFailed
from review import PaymentNotAwaitingReview, decide

AWAITING_PAYMENT = {
    "paymentId": "PAY-000053",
    "claimId": "CLM-1842",
    "amount": 5800.0,
    "currency": "EUR",
    "status": "awaiting_approval",
    "a2aTaskId": "task-1",
    "awaitingSince": "2026-09-10T10:00:00+00:00",
    "refusal": {
        "rule": "settlement-agent.assisted.execute_limit",
        "limit": 0.0,
        "requested": 5800.0,
        "narrative": "I'm unable to complete this payment myself.",
        "refusedTraceId": "331edba8cb21223e9de2f244360c42ef",
        "refusedSpanId": "b7997877797c1b27",
    },
}

NOW = datetime(2026, 9, 10, 10, 0, 5, tzinfo=timezone.utc)  # 5s after awaitingSince
REVIEWER_SPIFFE_ID = "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"


class FakeLedger:
    def __init__(self, record=None, get_error=None, reject_error=None):
        self.calls: list[tuple] = []
        self._record = record if record is not None else dict(AWAITING_PAYMENT)
        self._get_error = get_error
        self._reject_error = reject_error
        # What `HTTPXClientInstrumentor` would read in production to parent
        # this read's CLIENT span and to build the `traceparent` it injects.
        self.span_context_at_get: object | None = None

    async def get(self, payment_id):
        self.calls.append(("get", payment_id))
        self.span_context_at_get = trace.get_current_span().get_span_context()
        if self._get_error is not None:
            raise self._get_error
        return self._record

    async def reject(self, payment_id, reviewer_id):
        self.calls.append(("reject", payment_id, reviewer_id))
        if self._reject_error is not None:
            raise self._reject_error
        return {"paymentId": payment_id, "status": "rejected",
                "rejection": {"reviewerId": reviewer_id, "rejectedAt": "2026-09-10T10:00:05Z"}}


class FakeGateway:
    def __init__(self, result=None, error=None):
        self.calls: list[tuple] = []
        self._result = result if result is not None else {
            "paymentId": "PAY-000053",
            "amount": 5800.0,
            "currency": "EUR",
            "status": "executed",
        }
        self._error = error

    async def execute_payment(self, payment_id, amount, currency):
        self.calls.append((payment_id, amount, currency))
        if self._error is not None:
            raise self._error
        return self._result


class FakeClaims:
    def __init__(self, error=None):
        self.calls: list[tuple] = []
        self._error = error

    async def resume(self, task_id, payload):
        self.calls.append((task_id, payload))
        if self._error is not None:
            raise self._error
        return "input-required"


@pytest.fixture
def traced(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr(review.trace, "get_tracer", lambda name: tracer)
    return exporter


async def test_approve_releases_with_the_ledgers_amount_and_currency(traced):
    ledger = FakeLedger()
    gateway = FakeGateway()

    result = await decide(
        "PAY-000053", "alex.mercer", "approve",
        ledger=ledger, gateway=gateway,
        claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )

    # Every figure traces to AWAITING_PAYMENT above, never to a value this
    # function might have been tempted to accept as an argument instead.
    assert gateway.calls == [("PAY-000053", 5800.0, "EUR")]
    assert result.executed == {"paymentId": "PAY-000053", "amount": 5800.0,
                               "currency": "EUR", "status": "executed"}
    assert result.rejected is None and result.error is None
    assert ("reject", "PAY-000053", "alex.mercer") not in ledger.calls


async def test_reject_calls_the_ledger_and_never_the_gateway(traced):
    ledger = FakeLedger()
    gateway = FakeGateway()

    result = await decide(
        "PAY-000053", "alex.mercer", "reject",
        ledger=ledger, gateway=gateway,
        claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )

    assert gateway.calls == []
    assert ("reject", "PAY-000053", "alex.mercer") in ledger.calls
    assert result.decision == "reject"
    assert result.executed is None
    assert result.rejected["status"] == "rejected"


async def test_the_autonomy_level_is_read_off_the_refusals_rule(traced):
    result = await decide(
        "PAY-000053", "alex.mercer", "reject",
        ledger=FakeLedger(), gateway=FakeGateway(),
        claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )
    assert result.autonomy_level == "assisted"


async def test_the_review_decision_span_carries_exactly_the_spec_attributes(traced):
    exporter = traced
    await decide(
        "PAY-000053", "alex.mercer", "approve",
        ledger=FakeLedger(), gateway=FakeGateway(),
        claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "northstar.review.decision"
    assert span.kind == SpanKind.SERVER
    assert dict(span.attributes) == {
        "northstar.review.payment.id": "PAY-000053",
        "northstar.review.decision": "approve",
        "northstar.review.reviewer.id": "alex.mercer",
        "northstar.review.reviewer.spiffe_id": REVIEWER_SPIFFE_ID,
        "northstar.review.wait.duration": 5.0,
        # Not in the spec's list: the join key the evidence view reads a
        # claim's trajectory by, which the retired
        # settlement.execute_approved span used to carry into trace B.
        "northstar.claim.id": "CLM-1842",
    }


async def test_elapsed_ms_is_measured_from_the_ledgers_awaitingsince_not_a_page_load_timer(traced):
    """The property the spec calls out by name. `now` here is 5 seconds after
    the ledger's own awaitingSince -- there is no other clock in this function's
    signature for it to have used."""
    result = await decide(
        "PAY-000053", "alex.mercer", "reject",
        ledger=FakeLedger(), gateway=FakeGateway(),
        claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )
    assert result.elapsed_ms == 5000


async def test_the_wait_duration_is_a_double_in_seconds_not_integer_milliseconds(traced):
    """840 ms on the ledger's clock is recorded as 0.84 (registry: double, s)."""
    exporter = traced
    await decide(
        "PAY-000053", "alex.mercer", "approve",
        ledger=FakeLedger(), gateway=FakeGateway(),
        claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID,
        now=datetime(2026, 9, 10, 10, 0, 0, 840000, tzinfo=timezone.utc),
    )
    (span,) = exporter.get_finished_spans()
    value = span.attributes["northstar.review.wait.duration"]
    assert value == 0.84 and isinstance(value, float)


async def test_the_refusal_is_a_span_link_not_a_trace_id_attribute(traced):
    exporter = traced
    await decide(
        "PAY-000053", "alex.mercer", "approve",
        ledger=FakeLedger(), gateway=FakeGateway(),
        claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )
    (span,) = exporter.get_finished_spans()
    (link,) = span.links
    assert format(link.context.trace_id, "032x") == "331edba8cb21223e9de2f244360c42ef"
    assert not any("refused" in key for key in span.attributes)


async def test_a_reject_decision_still_gets_a_span_with_review_decision_reject(traced):
    exporter = traced
    await decide(
        "PAY-000053", "alex.mercer", "reject",
        ledger=FakeLedger(), gateway=FakeGateway(),
        claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )
    span = exporter.get_finished_spans()[0]
    assert span.attributes["northstar.review.decision"] == "reject"


async def test_a_release_the_platform_refuses_is_reported_not_hidden(traced):
    """A refused release must reach the caller as a fact on the result --
    never silently reported as if the payment executed."""
    exporter = traced
    text = ("a human approval may only release a payment awaiting approval; the "
            "ledger shows 'executed' [policy-denial rule=human-approved.execute "
            "limit=none requested=5800.00]")
    gateway = FakeGateway(error=ExecutionRefused(text))

    result = await decide(
        "PAY-000053", "alex.mercer", "approve",
        ledger=FakeLedger(), gateway=gateway,
        claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )

    assert result.executed is None
    assert result.error == text

    span = exporter.get_finished_spans()[0]
    assert span.status.status_code == StatusCode.ERROR
    # The decision itself is still what the reviewer chose, regardless of
    # whether the release they asked for actually happened.
    assert span.attributes["northstar.review.decision"] == "approve"


async def test_a_payment_not_awaiting_review_ends_the_span_error_and_never_releases(traced):
    """Racing another reviewer, or a stale link to an already-executed
    payment.

    A `northstar.review.decision` span exists and ends ERROR carrying the exception,
    which is a more honest record than silence: a reviewer clicked, and
    nothing could be recorded otherwise. The gateway is never called, and
    the two ledger-derived attributes are absent, because there was no
    usable record to read them off.
    """
    ledger = FakeLedger(record={**AWAITING_PAYMENT, "status": "proposed"})
    gateway = FakeGateway()

    with pytest.raises(PaymentNotAwaitingReview):
        await decide(
            "PAY-000053", "alex.mercer", "approve",
            ledger=ledger, gateway=gateway,
            claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
        )

    assert gateway.calls == []
    spans = traced.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "northstar.review.decision"
    assert span.status.status_code == StatusCode.ERROR
    assert dict(span.attributes) == {
        "northstar.review.payment.id": "PAY-000053",
        "northstar.review.decision": "approve",
        "northstar.review.reviewer.id": "alex.mercer",
        "northstar.review.reviewer.spiffe_id": REVIEWER_SPIFFE_ID,
    }


async def test_the_ledger_read_happens_inside_the_review_decision_span(traced):
    """The property that matters, asserted where it actually lives:
    `ledger.get` must run with `northstar.review.decision` current.

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
        ledger=ledger, gateway=FakeGateway(),
        claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
    )

    span = traced.get_finished_spans()[0]
    assert span.name == "northstar.review.decision"
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
            ledger=ledger, gateway=FakeGateway(),
            claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
        )
    assert ledger.calls == []


async def test_an_empty_reviewer_id_is_rejected_before_any_ledger_call(traced):
    ledger = FakeLedger()
    with pytest.raises(ValueError):
        await decide(
            "PAY-000053", "  ", "approve",
            ledger=ledger, gateway=FakeGateway(),
            claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
        )
    assert ledger.calls == []


async def test_a_ledger_lookup_failure_propagates_and_ends_the_span_error(traced):
    """The exception propagates unchanged, which is what main.py maps to a
    404/502, and it also lands on a `northstar.review.decision` span rather than
    vanishing -- for the same reason the not-awaiting-review test above
    records."""
    ledger = FakeLedger(get_error=RuntimeError("payment not found"))
    with pytest.raises(RuntimeError, match="payment not found"):
        await decide(
            "PAY-999999", "alex.mercer", "approve",
            ledger=ledger, gateway=FakeGateway(),
            claims=FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW,
        )
    spans = traced.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "northstar.review.decision"
    assert spans[0].status.status_code == StatusCode.ERROR
    assert "northstar.review.wait.duration" not in spans[0].attributes


async def test_approve_resumes_the_customers_task_with_the_ledgers_task_id(traced):
    claims = FakeClaims()
    result = await decide("PAY-000053", "alex.mercer", "approve", ledger=FakeLedger(),
                          gateway=FakeGateway(), claims=claims,
                          reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW)
    assert claims.calls == [("task-1", {"decision": "approved", "paymentId": "PAY-000053",
                                        "reviewerId": "alex.mercer"})]
    assert result.resumed_state == "input-required" and result.resume_error is None


async def test_reject_resumes_the_customers_task_once(traced):
    claims = FakeClaims()
    result = await decide("PAY-000053", "alex.mercer", "reject", ledger=FakeLedger(),
                          gateway=FakeGateway(), claims=claims,
                          reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW)
    assert [c[1]["decision"] for c in claims.calls] == ["rejected"]
    assert result.resumed_state is not None


async def test_a_failed_release_does_not_resume_the_task(traced):
    claims = FakeClaims()
    result = await decide("PAY-000053", "alex.mercer", "approve", ledger=FakeLedger(),
                          gateway=FakeGateway(error=ExecutionRefused("denied")), claims=claims,
                          reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW)
    assert result.error and claims.calls == []


async def test_a_failed_reject_does_not_resume_the_task(traced):
    claims = FakeClaims()
    result = await decide("PAY-000053", "alex.mercer", "reject",
                          ledger=FakeLedger(reject_error=RuntimeError("down")),
                          gateway=FakeGateway(), claims=claims,
                          reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW)
    assert result.error and claims.calls == []


async def test_a_resume_failure_is_reported_and_leaves_the_decision_standing(traced):
    result = await decide("PAY-000053", "alex.mercer", "approve", ledger=FakeLedger(),
                          gateway=FakeGateway(), claims=FakeClaims(error=ResumeFailed("task gone")),
                          reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW)
    assert result.executed is not None and result.error is None
    assert result.resume_error == "task gone" and result.resumed_state is None


async def test_a_payment_with_no_task_id_resumes_nothing(traced):
    claims = FakeClaims()
    record = {**AWAITING_PAYMENT}
    record.pop("a2aTaskId")
    result = await decide("PAY-000053", "alex.mercer", "approve", ledger=FakeLedger(record=record),
                          gateway=FakeGateway(), claims=claims,
                          reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW)
    assert claims.calls == [] and result.resumed_state is None


# ---- M8 Task 3: the span link, the claim baggage, the wait histogram ----

from opentelemetry import baggage
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import approval_metrics


@pytest.fixture
def waits():
    reader = InMemoryMetricReader()
    approval_metrics.use_meter(MeterProvider(metric_readers=[reader]).get_meter("test"))

    def samples():
        data = reader.get_metrics_data()
        if not data or not data.resource_metrics:
            return []
        out = []
        for scope in data.resource_metrics[0].scope_metrics:
            for m in scope.metrics:
                if m.name == "northstar.approval.wait.duration":
                    out.extend(m.data.data_points)
        return out
    return samples


async def _go(decision="approve", record=None, gateway=None, claims=None):
    return await decide(
        "PAY-000053", "alex.mercer", decision,
        ledger=FakeLedger(record=record), gateway=gateway or FakeGateway(),
        claims=claims or FakeClaims(), reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW)


async def test_the_decision_span_links_to_the_refusal_span(traced):
    await _go()
    (span,) = traced.get_finished_spans()
    (link,) = span.links
    assert format(link.context.trace_id, "032x") == AWAITING_PAYMENT["refusal"]["refusedTraceId"]
    assert format(link.context.span_id, "016x") == AWAITING_PAYMENT["refusal"]["refusedSpanId"]


async def test_no_link_when_the_refusal_ids_are_empty(traced):
    record = dict(AWAITING_PAYMENT)
    record["refusal"] = {**AWAITING_PAYMENT["refusal"], "refusedTraceId": "", "refusedSpanId": ""}
    await _go(record=record)
    (span,) = traced.get_finished_spans()
    assert list(span.links) == []


async def test_the_claim_id_is_baggage_for_the_gateway_call_and_the_resume(traced):
    seen = {}

    class G(FakeGateway):
        async def execute_payment(self, *a):
            seen["gateway"] = baggage.get_baggage("northstar.claim.id")
            return await super().execute_payment(*a)

    class C(FakeClaims):
        async def resume(self, *a):
            seen["resume"] = baggage.get_baggage("northstar.claim.id")
            return await super().resume(*a)

    await _go(gateway=G(), claims=C())
    assert seen == {"gateway": "CLM-1842", "resume": "CLM-1842"}
    assert baggage.get_baggage("northstar.claim.id") is None  # detached


async def test_one_histogram_sample_per_successful_approval(traced, waits):
    await _go("approve")
    (point,) = waits()
    assert point.count == 1 and point.sum == 5.0
    assert dict(point.attributes) == {
        "northstar.review.decision": "approve",
        "northstar.policy.autonomy.level": "assisted",
    }


async def test_a_reject_records_decision_rejected(traced, waits):
    await _go("reject")
    (point,) = waits()
    assert dict(point.attributes) == {
        "northstar.review.decision": "reject",
        "northstar.policy.autonomy.level": "assisted",
    }


async def test_a_refused_execute_records_no_sample(traced, waits):
    result = await _go("approve", gateway=FakeGateway(error=ExecutionRefused("no")))
    assert result.error and waits() == []


# ---- fix wave: a customer is never stranded by a lost resume ----

from claims import TaskNotWaiting


async def test_a_decision_already_on_the_ledger_resends_the_resume_and_moves_no_money(traced, waits):
    """The release went through but the customer was never told (the gateway's
    answer was lost, claims-agent was unreachable). The retry heals it."""
    claims = FakeClaims()
    gateway = FakeGateway()
    ledger = FakeLedger(record={**AWAITING_PAYMENT, "status": "executed"})
    result = await decide("PAY-000053", "alex.mercer", "approve", ledger=ledger, gateway=gateway,
                          claims=claims, reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW)
    assert gateway.calls == [], "a healed resume must never release money a second time"
    assert [c[0] for c in ledger.calls] == ["get"], ledger.calls
    assert claims.calls == [("task-1", {"decision": "approved", "paymentId": "PAY-000053",
                                        "reviewerId": "alex.mercer"})]
    assert result.resent_only is True and result.error is None
    assert result.resumed_state == "input-required"
    assert waits() == [], "no second wait sample: the human's wait was measured by the first decision"
    span, = traced.get_finished_spans()
    assert span.attributes["northstar.review.resume_only"] is True
    assert "northstar.review.wait.duration" not in span.attributes


async def test_a_reject_already_on_the_ledger_resends_the_resume_too(traced):
    claims = FakeClaims()
    ledger = FakeLedger(record={**AWAITING_PAYMENT, "status": "rejected"})
    result = await decide("PAY-000053", "alex.mercer", "reject", ledger=ledger, gateway=FakeGateway(),
                          claims=claims, reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW)
    assert [c[1]["decision"] for c in claims.calls] == ["rejected"]
    assert result.resent_only and ledger.calls == [("get", "PAY-000053")]


async def test_a_decision_the_ledger_contradicts_is_still_a_409_and_tells_nobody(traced):
    claims = FakeClaims()
    ledger = FakeLedger(record={**AWAITING_PAYMENT, "status": "rejected"})
    with pytest.raises(PaymentNotAwaitingReview):
        await decide("PAY-000053", "alex.mercer", "approve", ledger=ledger, gateway=FakeGateway(),
                     claims=claims, reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW)
    assert claims.calls == []


async def test_a_task_that_is_no_longer_waiting_stays_a_409(traced):
    ledger = FakeLedger(record={**AWAITING_PAYMENT, "status": "executed"})
    with pytest.raises(PaymentNotAwaitingReview):
        await decide("PAY-000053", "alex.mercer", "approve", ledger=ledger, gateway=FakeGateway(),
                     claims=FakeClaims(error=TaskNotWaiting("task is 'completed', not waiting")),
                     reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW)


async def test_a_resend_that_fails_for_another_reason_is_reported_not_swallowed(traced):
    ledger = FakeLedger(record={**AWAITING_PAYMENT, "status": "executed"})
    result = await decide("PAY-000053", "alex.mercer", "approve", ledger=ledger, gateway=FakeGateway(),
                          claims=FakeClaims(error=ResumeFailed("claims-agent unreachable")),
                          reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW)
    assert result.resume_error == "claims-agent unreachable" and result.resumed_state is None


async def test_a_decided_payment_with_no_task_is_still_a_409(traced):
    record = {**AWAITING_PAYMENT, "status": "executed"}
    record.pop("a2aTaskId")
    with pytest.raises(PaymentNotAwaitingReview):
        await decide("PAY-000053", "alex.mercer", "approve", ledger=FakeLedger(record=record),
                     gateway=FakeGateway(), claims=FakeClaims(),
                     reviewer_spiffe_id=REVIEWER_SPIFFE_ID, now=NOW)
