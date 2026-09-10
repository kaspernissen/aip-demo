"""ledger.py: the direct HTTP client to payments-api.

No MCP hop here, deliberately -- GET /payments?status=refused and GET
/payments/{id} are plain REST routes on payments-api itself
(systems/payments-api/handlers.go), not MCP tools, and reviewer-ui has no
business near agentgateway or the Policy Decision Service (it holds no
EXECUTE_LIMITS row and must never be able to reach a place that would grant
one). This client never recomputes a figure it reads -- every dict it
returns is decoded straight from the ledger's own JSON body.
"""
from __future__ import annotations

import json

import httpx
import pytest

from ledger import LedgerClient, LedgerUnavailable, PaymentNotFound


def _client(handler, **kwargs):
    return LedgerClient(
        "http://payments-api:8080", transport=httpx.MockTransport(handler), **kwargs
    )


async def test_list_refused_calls_the_status_filtered_route():
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
            ],
        )

    payments = await _client(handler).list_refused()

    assert seen["url"] == "http://payments-api:8080/payments?status=refused"
    assert len(payments) == 1
    assert payments[0]["paymentId"] == "PAY-000053"
    assert payments[0]["refusal"]["rule"] == "settlement-agent.assisted.execute_limit"


async def test_list_refused_returns_an_empty_list_when_none_are_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    assert await _client(handler).list_refused() == []


async def test_list_refused_raises_ledger_unavailable_on_a_server_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(LedgerUnavailable):
        await _client(handler).list_refused()


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
                "status": "refused",
                "refusal": {
                    "rule": "settlement-agent.assisted.execute_limit",
                    "limit": 6000.0,
                    "requested": 6200.0,
                    "narrative": "narrative text",
                    "refusedTraceId": "331edba8cb21223e9de2f244360c42ef",
                    "refusedSpanId": "b7997877797c1b27",
                    "refusedAt": "2026-09-10T10:00:00Z",
                },
            },
        )

    payment = await _client(handler).get("PAY-000053")
    assert payment["status"] == "refused"
    assert payment["refusal"]["refusedAt"] == "2026-09-10T10:00:00Z"


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
