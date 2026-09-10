"""M3 Task 3 Step 4: `execute_approved` -- the human-approved execution path.

No model call, ever: the reasoning happened in the refused run, a human
reviewed *that*, and this function's only job is to carry an approved
instruction to the payment system under an identity the policy permits. These
tests never construct a FunctionModel and never override `settlement_agent`'s
model, which is itself part of what they prove -- if `execute_approved` ever
grew a call into the agent, a test relying on `defer_model_check=True` to get
this far without an API key would start failing on model resolution instead.

The span-link half is the measured half (see settlement.py's `_refusal_link`
docstring for what was checked against the installed SDK and why): a link
built from a real, valid SpanContext is what actually survives
`Span._new_links`, so these tests assert on `span.links` read back from a
real `TracerProvider` + `InMemorySpanExporter`, not on what `_refusal_link`'s
return value merely looks like before it reaches a span.
"""
from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import settlement
from settlement import _refusal_link, execute_approved

REFUSED_TRACE_ID = "331edba8cb21223e9de2f244360c42ef"
REFUSED_SPAN_ID = "b7997877797c1b27"


@pytest.fixture
def traced(monkeypatch):
    """A real SDK TracerProvider + InMemorySpanExporter, wired to
    `execute_approved` by monkeypatching `settlement.trace.get_tracer`
    directly, per test.

    Deliberately NOT `opentelemetry.trace.set_tracer_provider`: that is a
    `sync.Once` (checked against the installed SDK) -- it takes effect the
    first process-wide call and silently no-ops after. Importing `bootstrap`
    anywhere in this same pytest session (test_instrumentation_settings.py
    does, for `capture_content_enabled`) runs `configure()` at module import
    time, which claims that Once during collection, before any fixture here
    could run. A global swap would then be racing the whole test session by
    file layout, not something this module can control the outcome of.
    Monkeypatching the one call site `execute_approved` actually makes is
    unaffected by whichever module wins that race.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr(settlement.trace, "get_tracer", lambda name: tracer)
    return exporter


class FakePayments:
    def __init__(self):
        self.calls: list[tuple] = []

    async def propose(self, *args, **kwargs):
        self.calls.append(("propose", args, kwargs))
        raise AssertionError("execute_approved must never propose")

    async def execute(self, payment_id, amount, currency="EUR"):
        self.calls.append(("execute", payment_id, amount, currency))
        return {
            "paymentId": payment_id,
            "claimId": "CLM-1842",
            "amount": amount,
            "currency": currency,
            "status": "executed",
        }


async def test_execute_approved_calls_execute_directly_with_no_propose(traced):
    payments = FakePayments()
    await execute_approved(
        "PAY-000001", 6200.0, "EUR", {"reviewerId": "alex.mercer"},
        payments, REFUSED_TRACE_ID, REFUSED_SPAN_ID,
    )

    assert [c[0] for c in payments.calls] == ["execute"]
    assert ("execute", "PAY-000001", 6200.0, "EUR") in payments.calls


async def test_execute_approved_returns_the_ledgers_own_record(traced):
    payments = FakePayments()
    result = await execute_approved(
        "PAY-000001", 6200.0, "EUR", {"reviewerId": "alex.mercer"},
        payments, REFUSED_TRACE_ID, REFUSED_SPAN_ID,
    )

    assert result == {
        "paymentId": "PAY-000001",
        "claimId": "CLM-1842",
        "amount": 6200.0,
        "currency": "EUR",
        "status": "executed",
    }


async def test_execute_approved_span_carries_the_payment_and_reviewer(traced):
    exporter = traced
    payments = FakePayments()
    await execute_approved(
        "PAY-000001", 6200.0, "EUR", {"reviewerId": "alex.mercer"},
        payments, REFUSED_TRACE_ID, REFUSED_SPAN_ID,
    )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "settlement.execute_approved"
    assert span.attributes["northstar.payment.id"] == "PAY-000001"
    assert span.attributes["northstar.approval.reviewer_id"] == "alex.mercer"


async def test_execute_approved_span_links_to_the_real_refusal_span(traced):
    """The measured proof: a link built from the refusal's real trace and
    span ids survives to the exported span, not merely to `_refusal_link`'s
    return value."""
    exporter = traced
    payments = FakePayments()
    await execute_approved(
        "PAY-000001", 6200.0, "EUR", {"reviewerId": "alex.mercer"},
        payments, REFUSED_TRACE_ID, REFUSED_SPAN_ID,
    )

    span = exporter.get_finished_spans()[0]
    assert len(span.links) == 1
    link = span.links[0]
    assert link.context.is_valid
    assert format(link.context.trace_id, "032x") == REFUSED_TRACE_ID
    assert format(link.context.span_id, "016x") == REFUSED_SPAN_ID


async def test_execute_approved_carries_no_link_when_there_are_no_refusal_ids(traced):
    """No trace/span id (e.g. a refusal recorded before this field existed):
    no link, rather than one that would only be silently dropped."""
    exporter = traced
    payments = FakePayments()
    await execute_approved(
        "PAY-000001", 6200.0, "EUR", {"reviewerId": "alex.mercer"},
        payments, "", "",
    )

    span = exporter.get_finished_spans()[0]
    assert span.links == ()


async def test_a_malformed_approval_payload_fails_loudly_not_silently_executing():
    """No `reviewerId` at all: this must raise, never default to executing."""
    payments = FakePayments()
    with pytest.raises(KeyError):
        await execute_approved(
            "PAY-000001", 6200.0, "EUR", {}, payments,
            REFUSED_TRACE_ID, REFUSED_SPAN_ID,
        )
    assert payments.calls == [], "a malformed approval must not reach the payment system"


# ---------------------------------------------------------------------------
# `_refusal_link` in isolation -- the SDK-level measurement, pinned as a unit
# test independent of the span-export path above.
# ---------------------------------------------------------------------------


def test_refusal_link_is_a_real_valid_link_when_both_ids_are_present():
    links = _refusal_link(REFUSED_TRACE_ID, REFUSED_SPAN_ID)
    assert len(links) == 1
    assert links[0].context.is_valid
    assert format(links[0].context.trace_id, "032x") == REFUSED_TRACE_ID
    assert format(links[0].context.span_id, "016x") == REFUSED_SPAN_ID


@pytest.mark.parametrize("trace_id,span_id", [
    ("", ""),
    (REFUSED_TRACE_ID, ""),
    ("", REFUSED_SPAN_ID),
])
def test_refusal_link_is_empty_without_both_ids(trace_id, span_id):
    assert _refusal_link(trace_id, span_id) == []
