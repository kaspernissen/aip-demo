"""ledger.py: the direct HTTP client to payments-api.

No MCP hop here, deliberately -- GET /payments?status=awaiting_approval, GET
/payments/{id} and POST /payments/{id}/reject are plain REST routes on
payments-api itself (systems/payments-api/handlers.go), not MCP tools, and
none of them moves money. Money is released only through gateway.py. This
client never recomputes a figure it reads -- every dict it returns is decoded
straight from the ledger's own JSON body.
"""
from __future__ import annotations

import json

import httpx
import pytest

from ledger import LedgerClient, LedgerUnavailable, PaymentNotAwaiting, PaymentNotFound


def _client(handler, **kwargs):
    return LedgerClient(
        "http://payments-api:8080", transport=httpx.MockTransport(handler), **kwargs
    )


async def test_list_awaiting_calls_the_status_filtered_route():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json=[
                {
                    "paymentId": "PAY-000053",
                    "claimId": "CLM-1842",
                    "amount": 6200.0,
                    "currency": "EUR",
                    "status": "awaiting_approval",
                    "refusal": {
                        "rule": "settlement-agent.assisted.execute_limit",
                        "limit": 6000.0,
                        "requested": 6200.0,
                        "narrative": "I'm unable to complete this payment myself.",
                        "refusedTraceId": "331edba8cb21223e9de2f244360c42ef",
                        "refusedSpanId": "b7997877797c1b27",
                    },
                    "awaitingSince": "2026-09-10T10:00:00Z",
                }
            ],
        )

    payments = await _client(handler).list_awaiting()

    assert seen["url"] == "http://payments-api:8080/payments?status=awaiting_approval"
    assert len(payments) == 1
    assert payments[0]["paymentId"] == "PAY-000053"
    assert payments[0]["refusal"]["rule"] == "settlement-agent.assisted.execute_limit"


async def test_list_awaiting_returns_an_empty_list_when_none_are_waiting():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    assert await _client(handler).list_awaiting() == []


async def test_list_awaiting_raises_ledger_unavailable_on_a_server_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(LedgerUnavailable):
        await _client(handler).list_awaiting()


async def test_get_returns_the_decoded_payment():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://payments-api:8080/payments/PAY-000053"
        return httpx.Response(
            200,
            json={
                "paymentId": "PAY-000053",
                "claimId": "CLM-1842",
                "amount": 6200.0,
                "currency": "EUR",
                "status": "awaiting_approval",
                "refusal": {
                    "rule": "settlement-agent.assisted.execute_limit",
                    "limit": 6000.0,
                    "requested": 6200.0,
                    "narrative": "narrative text",
                    "refusedTraceId": "331edba8cb21223e9de2f244360c42ef",
                    "refusedSpanId": "b7997877797c1b27",
                },
                "awaitingSince": "2026-09-10T10:00:00Z",
            },
        )

    payment = await _client(handler).get("PAY-000053")
    assert payment["status"] == "awaiting_approval"
    assert payment["awaitingSince"] == "2026-09-10T10:00:00Z"


async def test_get_raises_payment_not_found_on_a_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "payment not found"})

    with pytest.raises(PaymentNotFound):
        await _client(handler).get("PAY-999999")


async def test_get_raises_ledger_unavailable_when_the_connection_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(LedgerUnavailable):
        await _client(handler).get("PAY-000053")


async def test_a_malformed_json_body_is_ledger_unavailable_not_a_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with pytest.raises(LedgerUnavailable):
        await _client(handler).get("PAY-000053")


async def test_reject_posts_the_reviewer_and_returns_the_record():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"paymentId": "PAY-000053", "status": "rejected",
                                         "rejection": {"reviewerId": "alex.mercer", "rejectedAt": "2026-09-10T10:00:05Z"}})

    record = await _client(handler).reject("PAY-000053", "alex.mercer")
    assert seen["method"] == "POST"
    assert seen["url"] == "http://payments-api:8080/payments/PAY-000053/reject"
    assert seen["body"] == {"reviewerId": "alex.mercer"}
    assert record["status"] == "rejected"


async def test_reject_maps_404_and_409_and_failures():
    def status(code):
        return lambda request: httpx.Response(code, json={"error": "x"})

    with pytest.raises(PaymentNotFound):
        await _client(status(404)).reject("PAY-1", "a")
    with pytest.raises(PaymentNotAwaiting):
        await _client(status(409)).reject("PAY-1", "a")
    with pytest.raises(LedgerUnavailable):
        await _client(status(500)).reject("PAY-1", "a")

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LedgerUnavailable):
        await _client(refuse).reject("PAY-1", "a")
