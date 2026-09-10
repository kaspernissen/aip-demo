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
        import json

        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
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

    result = make_client(handler).execute("PAY-000001", 6200.0, "EUR")

    assert seen["url"] == "http://payments-api:8080/payments/PAY-000001/execute"
    # The amount is stated in the body, not inferred from the id. payments-api
    # holds the proposal and is the party that can confirm the two agree.
    assert seen["body"] == {"amount": 6200.0, "currency": "EUR"}
    assert result["status"] == "executed"


def test_http_errors_are_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "payment not found"})

    with pytest.raises(httpx.HTTPStatusError):
        make_client(handler).execute("PAY-999999", 6200.0, "EUR")


# ---------------------------------------------------------------------------
# M3 Task 3: recording a refusal, through the same client that already talks
# to payments-api -- not a new HTTP path, the plan's own words for it.
# ---------------------------------------------------------------------------


def test_refuse_posts_to_refuse_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "paymentId": "PAY-000001",
                "claimId": "CLM-1842",
                "amount": 6200,
                "currency": "EUR",
                "status": "refused",
                "refusal": {
                    "rule": "execute_limit",
                    "limit": 6000.0,
                    "requested": 6200.0,
                    "narrative": "narrative",
                    "refusedTraceId": "331edba8cb21223e9de2f244360c42ef",
                    "refusedSpanId": "b7997877797c1b27",
                    "refusedAt": "2026-09-10T00:00:00Z",
                },
            },
        )

    result = make_client(handler).refuse(
        "PAY-000001",
        rule="execute_limit",
        limit=6000.0,
        requested=6200.0,
        narrative="narrative",
        refused_trace_id="331edba8cb21223e9de2f244360c42ef",
        refused_span_id="b7997877797c1b27",
    )

    assert seen["method"] == "POST"
    assert seen["url"] == "http://payments-api:8080/payments/PAY-000001/refuse"
    assert seen["body"] == {
        "rule": "execute_limit",
        "limit": 6000.0,
        "requested": 6200.0,
        "narrative": "narrative",
        "refusedTraceId": "331edba8cb21223e9de2f244360c42ef",
        "refusedSpanId": "b7997877797c1b27",
    }
    assert result["status"] == "refused"
