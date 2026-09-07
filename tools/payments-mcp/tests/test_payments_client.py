import httpx
import pytest

from payments_client import PaymentsClient


def make_client(handler) -> PaymentsClient:
    transport = httpx.MockTransport(handler)
    return PaymentsClient("http://payments-api:8080", transport=transport)


def test_propose_posts_to_propose_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "paymentId": "PAY-000001",
                "claimId": "CLM-1842",
                "amount": 6200,
                "currency": "EUR",
                "status": "proposed",
            },
        )

    result = make_client(handler).propose("CLM-1842", 6200, "EUR")

    assert seen["method"] == "POST"
    assert seen["url"] == "http://payments-api:8080/payments/propose"
    assert seen["body"] == {"claimId": "CLM-1842", "amount": 6200, "currency": "EUR"}
    assert result["paymentId"] == "PAY-000001"
    assert result["status"] == "proposed"


def test_execute_posts_to_execute_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "paymentId": "PAY-000001",
                "claimId": "CLM-1842",
                "amount": 6200,
                "currency": "EUR",
                "status": "executed",
            },
        )

    result = make_client(handler).execute("PAY-000001")

    assert seen["url"] == "http://payments-api:8080/payments/PAY-000001/execute"
    assert result["status"] == "executed"


def test_http_errors_are_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "payment not found"})

    with pytest.raises(httpx.HTTPStatusError):
        make_client(handler).execute("PAY-999999")
