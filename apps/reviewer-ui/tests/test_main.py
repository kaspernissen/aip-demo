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

    async def list_refused(self):
        return self._listing

    async def get(self, payment_id):
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
