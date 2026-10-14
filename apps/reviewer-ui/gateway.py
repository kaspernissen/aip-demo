"""The reviewer-ui's own call to execute_payment, through agentgateway.

The same governed path the settlement-agent uses: a JWT-SVID in the
Authorization header (identity.fetch_jwt_svid), jwtAuth at the gateway, the
PDS's decision on the tools/call. The PDS permits this caller only under
`human-approved.execute`, and only when it reads the ledger status
`awaiting_approval` itself.

Raw streamable-HTTP JSON-RPC over httpx (initialize, notifications/initialized,
tools/call), the technique tests/harness/live.py::call_tool_directly already
uses, rather than a new dependency on the `mcp` SDK. agentgateway answers as
`text/event-stream`, so the body is read from its `data:` line.

`traceparent` is injected by HTTPXClientInstrumentor (bootstrap.py) and also
put into `params._meta` (SEP-414), exactly as the settlement-agent does, so the
gateway, the PDS and payments-mcp join review.decision's trace.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx
from opentelemetry import propagate

from identity import fetch_jwt_svid

GATEWAY_AUDIENCE = os.getenv(
    "NORTHSTAR_GATEWAY_AUDIENCE",
    "spiffe://northstar.local/ns/northstar-platform/sa/agentgateway",
)
MCP_PROTOCOL_VERSION = "2025-06-18"


class ExecutionRefused(RuntimeError):
    """The platform refused the release (an isError tool result). The text is
    shown to the reviewer as it came back."""


class GatewayUnavailable(RuntimeError):
    """The gateway did not answer with an MCP result at all."""


def _message(response: httpx.Response) -> dict[str, Any]:
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    return response.json()


class GatewayClient:
    def __init__(self, mcp_url: str, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 30.0):
        self._url = mcp_url
        self._transport = transport
        self._timeout = timeout

    async def execute_payment(self, payment_id: str, amount: float, currency: str) -> dict[str, Any]:
        token = await fetch_jwt_svid(GATEWAY_AUDIENCE)
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        }
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
                init = await client.post(self._url, headers=headers, json={
                    "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "initialize",
                    "params": {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
                               "clientInfo": {"name": "reviewer-ui", "version": "1"}}})
                init.raise_for_status()
                session = {**headers, "mcp-session-id": init.headers["mcp-session-id"]}
                await client.post(self._url, headers=session, json={
                    "jsonrpc": "2.0", "method": "notifications/initialized"})
                meta: dict[str, str] = {}
                propagate.inject(meta)
                called = await client.post(self._url, headers=session, json={
                    "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "tools/call",
                    "params": {"name": "execute_payment", "_meta": meta,
                               "arguments": {"payment_id": payment_id, "amount": amount,
                                             "currency": currency}}})
                called.raise_for_status()
                body = _message(called)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise GatewayUnavailable(f"agentgateway did not complete execute_payment: {exc}") from exc

        if "error" in body:
            raise GatewayUnavailable(f"agentgateway returned a JSON-RPC error: {body['error']}")
        result = body.get("result") or {}
        if result.get("isError"):
            text = " ".join(c.get("text", "") for c in result.get("content") or [])
            raise ExecutionRefused(text)
        structured = result.get("structuredContent") or {}
        return structured.get("result", structured)
