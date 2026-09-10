"""approval.py: the raw A2A JSON-RPC client to senior-settlement-agent.

Deliberately NOT a2a-sdk's own client, for the same reason
agents/claims-agent/delegation.py gives (its module docstring): a2a-sdk's
typed client and error handling don't match the plain dict-in/dict-out
contract this module tests against. This is the same technique, duplicated
per this repo's own convention (correlation.py's ClaimIdSpanProcessor is
triplicated the same way) rather than imported across a service boundary --
each service ships its own image from its own requirements.txt.
"""
from __future__ import annotations

import json

import httpx
import pytest

from approval import ApprovalClient


def _client(handler):
    return ApprovalClient(
        "http://senior-settlement-agent:9002", transport=httpx.MockTransport(handler)
    )


async def test_execute_approved_posts_a_jsonrpc_message_send_with_the_payload():
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
                    "parts": [
                        {
                            "kind": "text",
                            "text": json.dumps(
                                {
                                    "paymentId": "PAY-000053",
                                    "amount": 6200.0,
                                    "currency": "EUR",
                                    "status": "executed",
                                    "reviewerId": "alex.mercer",
                                }
                            ),
                        }
                    ],
                },
            },
        )

    payload = {
        "paymentId": "PAY-000053",
        "amount": 6200.0,
        "currency": "EUR",
        "approval": {"reviewerId": "alex.mercer"},
        "refusedTraceId": "331edba8cb21223e9de2f244360c42ef",
        "refusedSpanId": "b7997877797c1b27",
    }
    result = await _client(handler).execute_approved(payload)

    assert seen["url"] == "http://senior-settlement-agent:9002/"
    assert seen["body"]["method"] == "message/send"
    sent = json.loads(seen["body"]["params"]["message"]["parts"][0]["text"])
    assert sent == payload
    assert result == {
        "paymentId": "PAY-000053",
        "amount": 6200.0,
        "currency": "EUR",
        "status": "executed",
        "reviewerId": "alex.mercer",
    }


async def test_execute_approved_raises_on_a_jsonrpc_error():
    """This is the shape an UnverifiedApproval takes once it crosses the A2A
    boundary: settlement-agent's executor lets it propagate as an unhandled
    exception, and a2a-sdk turns that into a JSON-RPC error carrying
    str(exc) (agents/settlement-agent/executor.py's own module docstring)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "x",
                "error": {
                    "code": -32603,
                    "message": (
                        "payment PAY-000053: ledger status is 'proposed', not "
                        "'refused'"
                    ),
                },
            },
        )

    with pytest.raises(RuntimeError, match="ledger status is 'proposed'"):
        await _client(handler).execute_approved({"paymentId": "PAY-000053"})


async def test_execute_approved_raises_on_a_transport_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(httpx.HTTPError):
        await _client(handler).execute_approved({"paymentId": "PAY-000053"})
