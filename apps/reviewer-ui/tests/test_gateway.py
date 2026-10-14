import json

import httpx
import pytest

import gateway


def _sse(body: dict) -> str:
    return f"event: message\ndata: {json.dumps(body)}\n\n"


def _transport(tool_result: dict, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append((payload.get("method"), dict(request.headers), payload))
        if payload["method"] == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "s1", "content-type": "text/event-stream"},
                                  text=_sse({"jsonrpc": "2.0", "id": payload["id"], "result": {"capabilities": {}}}))
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=_sse({"jsonrpc": "2.0", "id": payload["id"], "result": tool_result}))
    return httpx.MockTransport(handler)


@pytest.fixture
def svid(monkeypatch):
    async def fake(audience):
        assert audience == "spiffe://northstar.local/ns/northstar-platform/sa/agentgateway"
        return "header.payload.sig"
    monkeypatch.setattr(gateway, "fetch_jwt_svid", fake)


async def test_execute_presents_the_svid_and_returns_the_ledger_record(svid):
    seen = []
    client = gateway.GatewayClient("http://gw/mcp", transport=_transport(
        {"isError": False, "structuredContent": {"result": {"paymentId": "PAY-000001", "status": "executed"}}}, seen))
    record = await client.execute_payment("PAY-000001", 5800.0, "EUR")
    assert record == {"paymentId": "PAY-000001", "status": "executed"}
    assert [m for m, _, _ in seen] == ["initialize", "notifications/initialized", "tools/call"]
    assert all(h["authorization"] == "Bearer header.payload.sig" for _, h, _ in seen)
    assert seen[2][1]["mcp-session-id"] == "s1"
    call = seen[2][2]["params"]
    assert call["name"] == "execute_payment"
    assert call["arguments"] == {"payment_id": "PAY-000001", "amount": 5800.0, "currency": "EUR"}


async def test_a_policy_denial_is_execution_refused_with_its_text(svid):
    text = "the ledger shows 'executed' [policy-denial rule=human-approved.execute limit=none requested=5800.00]"
    client = gateway.GatewayClient("http://gw/mcp", transport=_transport(
        {"isError": True, "content": [{"type": "text", "text": text}]}, []))
    with pytest.raises(gateway.ExecutionRefused) as raised:
        await client.execute_payment("PAY-000001", 5800.0, "EUR")
    assert str(raised.value) == text


async def test_an_http_failure_is_gateway_unavailable(svid):
    client = gateway.GatewayClient("http://gw/mcp", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="authentication failure")))
    with pytest.raises(gateway.GatewayUnavailable):
        await client.execute_payment("PAY-000001", 5800.0, "EUR")


async def test_a_json_rpc_error_is_gateway_unavailable(svid):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "s1"},
                                  json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32603, "message": "boom"}})
    client = gateway.GatewayClient("http://gw/mcp", transport=httpx.MockTransport(handler))
    with pytest.raises(gateway.GatewayUnavailable):
        await client.execute_payment("PAY-000001", 5800.0, "EUR")


def _custom_transport(call_text: str):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "s1", "content-type": "text/event-stream"},
                                  text=_sse({"jsonrpc": "2.0", "id": payload["id"], "result": {}}))
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=call_text.replace("__ID__", payload["id"]))
    return httpx.MockTransport(handler)


async def test_a_leading_notification_before_the_response_is_skipped(svid):
    notification = _sse({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}})
    other = _sse({"jsonrpc": "2.0", "id": "someone-else", "result": {"isError": True, "content": []}})
    answer = _sse({"jsonrpc": "2.0", "id": "__ID__", "result": {
        "isError": False, "structuredContent": {"result": {"paymentId": "PAY-000001", "status": "executed"}}}})
    client = gateway.GatewayClient("http://gw/mcp", transport=_custom_transport(notification + other + answer))
    record = await client.execute_payment("PAY-000001", 5800.0, "EUR")
    assert record["status"] == "executed"


async def test_a_body_with_neither_result_nor_error_is_gateway_unavailable(svid):
    client = gateway.GatewayClient("http://gw/mcp", transport=_custom_transport(
        _sse({"jsonrpc": "2.0", "id": "__ID__"})))
    with pytest.raises(gateway.GatewayUnavailable):
        await client.execute_payment("PAY-000001", 5800.0, "EUR")


async def test_a_stream_with_no_matching_id_is_gateway_unavailable(svid):
    client = gateway.GatewayClient("http://gw/mcp", transport=_custom_transport(
        _sse({"jsonrpc": "2.0", "id": "not-mine", "result": {"isError": False}})))
    with pytest.raises(gateway.GatewayUnavailable):
        await client.execute_payment("PAY-000001", 5800.0, "EUR")
