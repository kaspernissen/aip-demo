import json

import httpx
import pytest

from delegation import A2ADelegate, allowed_agents_from_env


def test_allowed_agents_parses_the_env_format():
    agents = allowed_agents_from_env(
        "settlement-agent=http://settlement-agent:9002,policy-agent=http://policy-agent:9003"
    )

    assert agents == {
        "settlement-agent": "http://settlement-agent:9002",
        "policy-agent": "http://policy-agent:9003",
    }


def test_allowed_agents_is_empty_when_unset():
    assert allowed_agents_from_env("") == {}
    assert allowed_agents_from_env(None) == {}


async def test_send_posts_a_jsonrpc_message_send():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": seen["body"]["id"],
                "result": {
                    "kind": "message",
                    "role": "agent",
                    "messageId": "m1",
                    "parts": [{"kind": "text", "text": json.dumps({"status": "executed"})}],
                },
            },
        )

    delegate = A2ADelegate(
        "http://settlement-agent:9002",
        "settlement-agent",
        transport=httpx.MockTransport(handler),
    )
    result = await delegate.send({"claimId": "CLM-1842", "amount": 6200})

    assert seen["url"] == "http://settlement-agent:9002/"
    assert seen["body"]["method"] == "message/send"
    assert json.loads(seen["body"]["params"]["message"]["parts"][0]["text"]) == {
        "claimId": "CLM-1842",
        "amount": 6200,
    }
    assert result == {"status": "executed"}


async def test_send_raises_on_a_jsonrpc_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "x",
                "error": {"code": -32000, "message": "executor exploded"},
            },
        )

    delegate = A2ADelegate(
        "http://settlement-agent:9002",
        "settlement-agent",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RuntimeError, match="executor exploded"):
        await delegate.send({"claimId": "CLM-1842", "amount": 6200})
