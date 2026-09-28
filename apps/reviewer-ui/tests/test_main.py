"""main.py: the FastAPI routes, exercised end-to-end in-process
(httpx.ASGITransport -- no real socket, no real payments-api or
senior-settlement-agent) against fakes substituted for the module-level
ledger/approval clients and the identity lookup.

`os.environ.setdefault` for AGENT_CARD_PATH-style config isn't needed here --
reviewer-ui has no agent card, it is an A2A *client* only (main.py's own
module docstring).
"""
from __future__ import annotations

import httpx
import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

import main

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
        "refusedAt": "2026-09-10T10:00:00Z",
    },
}


class FakeLedger:
    def __init__(self, listing=None, record=None, get_error=None):
        self._listing = listing if listing is not None else [REFUSED_PAYMENT]
        self._record = record if record is not None else dict(REFUSED_PAYMENT)
        self._get_error = get_error
        # The span that was current at the moment each read was made. In
        # production this is what `HTTPXClientInstrumentor` hangs its CLIENT
        # span (and the injected `traceparent`) off, so a read made with no
        # current span puts itself and payments-api's own server span in a
        # trace of their own.
        self.span_context_at_list: object | None = None
        self.span_context_at_get: object | None = None

    async def list_refused(self):
        self.span_context_at_list = otel_trace.get_current_span().get_span_context()
        return self._listing

    async def get(self, payment_id):
        self.span_context_at_get = otel_trace.get_current_span().get_span_context()
        if self._get_error is not None:
            raise self._get_error
        return self._record


class FakeApprover:
    def __init__(self, result=None, error=None):
        self._result = result or {
            "paymentId": "PAY-000053", "amount": 6200.0, "currency": "EUR",
            "status": "executed",
        }
        self._error = error

    async def execute_approved(self, payload):
        if self._error is not None:
            raise self._error
        return self._result


@pytest.fixture
def traced(monkeypatch):
    """Capture main.py's own spans in memory, the same way tests/test_review.py
    captures review.py's."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr(main.trace, "get_tracer", lambda name: tracer)
    return exporter


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(main, "ledger_client", FakeLedger())
    monkeypatch.setattr(main, "approval_client", FakeApprover())

    async def fake_spiffe_id():
        return "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"

    monkeypatch.setattr(main.identity, "workload_spiffe_id", fake_spiffe_id)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz_is_ok(client):
    response = await client.get("/healthz")
    assert response.status_code == 200


async def test_index_lists_the_refused_payment(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "PAY-000053" in response.text
    assert "6200" in response.text


async def test_index_reports_a_ledger_outage_honestly(monkeypatch):
    class BrokenLedger:
        async def list_refused(self):
            raise main.ledger.LedgerUnavailable("connection refused")

    monkeypatch.setattr(main, "ledger_client", BrokenLedger())
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/")
    assert response.status_code == 502
    assert "PAY-" not in response.text


async def test_index_makes_its_ledger_read_inside_an_owned_review_queue_span(
    monkeypatch, traced
):
    """The listing read must have a parent span, or its CLIENT span roots a
    trace of its own and payments-api's server span for the read lands in a
    second, unrelated one.

    The span context captured *inside* `list_refused` is the assertion that
    matters: it is exactly what `HTTPXClientInstrumentor` reads to parent its
    CLIENT span and to build the `traceparent` it injects.
    """
    ledger = FakeLedger()
    monkeypatch.setattr(main, "ledger_client", ledger)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/")
    assert response.status_code == 200

    spans = traced.get_finished_spans()
    assert [s.name for s in spans] == ["review.queue"]
    span = spans[0]
    assert span.kind == SpanKind.SERVER
    # No attributes, deliberately -- see main.py's own comment on the name.
    assert dict(span.attributes) == {}
    assert ledger.span_context_at_list is not None
    assert ledger.span_context_at_list.is_valid, (
        "the ledger listing ran with no valid span current, so its outbound "
        "CLIENT span would root a trace of its own"
    )
    assert ledger.span_context_at_list.span_id == span.context.span_id, (
        "the ledger listing did not run inside the review.queue span"
    )


async def test_a_ledger_outage_ends_the_review_queue_span_error(monkeypatch, traced):
    """The page renders a 502; the span must say so too, rather than ending
    Unset beside it."""

    class BrokenLedger:
        async def list_refused(self):
            raise main.ledger.LedgerUnavailable("connection refused")

    monkeypatch.setattr(main, "ledger_client", BrokenLedger())
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/")
    assert response.status_code == 502

    span = traced.get_finished_spans()[0]
    assert span.name == "review.queue"
    assert span.status.status_code == StatusCode.ERROR
    assert "connection refused" in (span.status.description or "")
    assert [event.name for event in span.events] == ["exception"]


async def test_healthz_produces_no_span_at_all(traced):
    """bootstrap.py's own claim: the probe endpoint makes no outbound call
    and owns no domain event, so it is not telemetry this service invents."""
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/healthz")
    assert response.status_code == 200
    assert traced.get_finished_spans() == ()


async def test_approve_executes_and_shows_the_ledgers_result(client):
    response = await client.post(
        "/review/PAY-000053",
        data={"reviewer_id": "alex.mercer", "decision": "approve"},
    )
    assert response.status_code == 200
    assert "executed" in response.text
    assert "alex.mercer" in response.text


async def test_reject_never_calls_the_approver(client, monkeypatch):
    calls = []

    class WatchedApprover(FakeApprover):
        async def execute_approved(self, payload):
            calls.append(payload)
            return await super().execute_approved(payload)

    monkeypatch.setattr(main, "approval_client", WatchedApprover())

    response = await client.post(
        "/review/PAY-000053",
        data={"reviewer_id": "alex.mercer", "decision": "reject"},
    )
    assert response.status_code == 200
    assert calls == []


async def test_a_failed_approval_is_shown_honestly_not_as_a_success(monkeypatch):
    monkeypatch.setattr(main, "ledger_client", FakeLedger())
    monkeypatch.setattr(
        main, "approval_client",
        FakeApprover(error=RuntimeError(
            "senior-settlement-agent returned an error: UnverifiedApproval: "
            "no refusal record covers this payment"
        )),
    )

    async def fake_spiffe_id():
        return "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"

    monkeypatch.setattr(main.identity, "workload_spiffe_id", fake_spiffe_id)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.post(
            "/review/PAY-000053",
            data={"reviewer_id": "alex.mercer", "decision": "approve"},
        )
    assert response.status_code == 200
    assert "status executed" not in response.text.lower()
    assert "was not executed" in response.text.lower() or "failed" in response.text.lower()


async def test_an_empty_reviewer_name_is_rejected(client):
    response = await client.post(
        "/review/PAY-000053", data={"reviewer_id": "  ", "decision": "approve"},
    )
    assert response.status_code == 400


async def test_an_unknown_payment_id_is_a_404(monkeypatch):
    monkeypatch.setattr(
        main, "ledger_client",
        FakeLedger(get_error=main.ledger.PaymentNotFound("payment not found")),
    )
    monkeypatch.setattr(main, "approval_client", FakeApprover())

    async def fake_spiffe_id():
        return "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"

    monkeypatch.setattr(main.identity, "workload_spiffe_id", fake_spiffe_id)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.post(
            "/review/PAY-999999",
            data={"reviewer_id": "alex.mercer", "decision": "approve"},
        )
    assert response.status_code == 404


async def test_a_payment_no_longer_refused_is_a_409(monkeypatch):
    monkeypatch.setattr(
        main, "ledger_client",
        FakeLedger(record={**REFUSED_PAYMENT, "status": "executed"}),
    )
    monkeypatch.setattr(main, "approval_client", FakeApprover())

    async def fake_spiffe_id():
        return "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"

    monkeypatch.setattr(main.identity, "workload_spiffe_id", fake_spiffe_id)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.post(
            "/review/PAY-000053",
            data={"reviewer_id": "alex.mercer", "decision": "approve"},
        )
    assert response.status_code == 409
