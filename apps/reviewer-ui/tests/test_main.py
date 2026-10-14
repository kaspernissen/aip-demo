"""main.py: the FastAPI routes, exercised end-to-end in-process
(httpx.ASGITransport -- no real socket, no real payments-api or
agentgateway) against fakes substituted for the module-level ledger/gateway
clients and the identity lookup.
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

AWAITING_PAYMENT = {
    "paymentId": "PAY-000053",
    "claimId": "CLM-1842",
    "amount": 5800.0,
    "currency": "EUR",
    "status": "awaiting_approval",
    "awaitingSince": "2026-09-10T10:00:00Z",
    "refusal": {
        "rule": "settlement-agent.assisted.execute_limit",
        "limit": 0.0,
        "requested": 5800.0,
        "narrative": "I'm unable to complete this payment myself.",
        "refusedTraceId": "331edba8cb21223e9de2f244360c42ef",
        "refusedSpanId": "b7997877797c1b27",
    },
}


class FakeLedger:
    def __init__(self, listing=None, record=None, get_error=None):
        self._listing = listing if listing is not None else [AWAITING_PAYMENT]
        self._record = record if record is not None else dict(AWAITING_PAYMENT)
        self._get_error = get_error
        # The span that was current at the moment each read was made. In
        # production this is what `HTTPXClientInstrumentor` hangs its CLIENT
        # span (and the injected `traceparent`) off, so a read made with no
        # current span puts itself and payments-api's own server span in a
        # trace of their own.
        self.span_context_at_list: object | None = None
        self.span_context_at_get: object | None = None

    async def list_awaiting(self):
        self.span_context_at_list = otel_trace.get_current_span().get_span_context()
        return self._listing

    async def get(self, payment_id):
        self.span_context_at_get = otel_trace.get_current_span().get_span_context()
        if self._get_error is not None:
            raise self._get_error
        return self._record

    async def reject(self, payment_id, reviewer_id):
        self.rejected = (payment_id, reviewer_id)
        return {"paymentId": payment_id, "status": "rejected",
                "rejection": {"reviewerId": reviewer_id, "rejectedAt": "2026-09-10T10:00:05Z"}}


class FakeGateway:
    def __init__(self, result=None, error=None):
        self.calls = []
        self._result = result or {
            "paymentId": "PAY-000053", "amount": 5800.0, "currency": "EUR",
            "status": "executed",
        }
        self._error = error

    async def execute_payment(self, payment_id, amount, currency):
        self.calls.append((payment_id, amount, currency))
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
    monkeypatch.setattr(main, "gateway_client", FakeGateway())

    async def fake_spiffe_id():
        return "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"

    monkeypatch.setattr(main.identity, "workload_spiffe_id", fake_spiffe_id)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz_is_ok(client):
    response = await client.get("/healthz")
    assert response.status_code == 200


async def test_index_lists_the_payment_awaiting_approval(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "PAY-000053" in response.text
    assert "5800" in response.text


async def test_index_reports_a_ledger_outage_honestly(monkeypatch):
    class BrokenLedger:
        async def list_awaiting(self):
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

    The span context captured *inside* `list_awaiting` is the assertion that
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
        async def list_awaiting(self):
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


async def test_approve_releases_through_the_gateway_and_shows_the_ledgers_result(client, monkeypatch):
    gateway = FakeGateway()
    monkeypatch.setattr(main, "gateway_client", gateway)
    response = await client.post(
        "/review/PAY-000053",
        data={"reviewer_id": "alex.mercer", "decision": "approve"},
    )
    assert response.status_code == 200
    assert "Review recorded" in response.text
    assert "executed" in response.text
    assert "alex.mercer" in response.text
    assert gateway.calls == [("PAY-000053", 5800.0, "EUR")]


async def test_reject_never_calls_the_gateway(client, monkeypatch):
    gateway = FakeGateway()
    monkeypatch.setattr(main, "gateway_client", gateway)

    response = await client.post(
        "/review/PAY-000053",
        data={"reviewer_id": "alex.mercer", "decision": "reject"},
    )
    assert response.status_code == 200
    assert gateway.calls == []
    assert "Rejected by alex.mercer" in response.text


async def test_a_refused_release_is_shown_honestly_not_as_a_success(client, monkeypatch):
    monkeypatch.setattr(
        main, "gateway_client",
        FakeGateway(error=main.gateway.ExecutionRefused(
            "the ledger shows 'rejected' [policy-denial rule=human-approved.execute "
            "limit=none requested=5800.00]"
        )),
    )
    response = await client.post(
        "/review/PAY-000053",
        data={"reviewer_id": "alex.mercer", "decision": "approve"},
    )
    assert response.status_code == 200
    assert "status executed" not in response.text.lower()
    assert "paid by the platform" not in response.text.lower()
    assert "did not happen" in response.text.lower()


async def test_api_awaiting_lists_the_queue(client):
    response = await client.get("/api/awaiting")
    assert response.status_code == 200
    assert response.json()[0]["paymentId"] == "PAY-000053"


async def test_api_review_approve_returns_the_released_record_and_level(client):
    response = await client.post(
        "/api/review/PAY-000053", json={"reviewerId": "alex.mercer", "decision": "approve"})
    assert response.status_code == 200
    body = response.json()
    assert body["executed"]["status"] == "executed"
    assert body["rejected"] is None and body["error"] is None
    assert body["autonomyLevel"] == "assisted"
    assert body["reviewerId"] == "alex.mercer"


async def test_api_review_reject_returns_the_rejection(client):
    response = await client.post(
        "/api/review/PAY-000053", json={"reviewerId": "alex.mercer", "decision": "reject"})
    body = response.json()
    assert body["rejected"]["status"] == "rejected"
    assert body["executed"] is None


async def test_api_review_maps_errors_to_json_statuses(monkeypatch):
    async def fake_spiffe_id():
        return "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"

    monkeypatch.setattr(main.identity, "workload_spiffe_id", fake_spiffe_id)
    monkeypatch.setattr(main, "gateway_client", FakeGateway())
    transport = httpx.ASGITransport(app=main.app)
    cases = [
        (main.ledger.PaymentNotFound("nope"), 404),
        (main.ledger.LedgerUnavailable("down"), 502),
    ]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        for error, status in cases:
            monkeypatch.setattr(main, "ledger_client", FakeLedger(get_error=error))
            response = await c.post(
                "/api/review/PAY-000053", json={"reviewerId": "alex.mercer", "decision": "approve"})
            assert response.status_code == status
            assert "error" in response.json()
        monkeypatch.setattr(main, "ledger_client", FakeLedger(record={**AWAITING_PAYMENT, "status": "rejected"}))
        response = await c.post(
            "/api/review/PAY-000053", json={"reviewerId": "alex.mercer", "decision": "approve"})
        assert response.status_code == 409
        response = await c.post(
            "/api/review/PAY-000053", json={"reviewerId": "  ", "decision": "approve"})
        assert response.status_code == 400


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
    monkeypatch.setattr(main, "gateway_client", FakeGateway())

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


async def test_a_payment_no_longer_awaiting_is_a_409(monkeypatch):
    monkeypatch.setattr(
        main, "ledger_client",
        FakeLedger(record={**AWAITING_PAYMENT, "status": "executed"}),
    )
    monkeypatch.setattr(main, "gateway_client", FakeGateway())

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
