"""The raw A2A JSON-RPC client to senior-settlement-agent.

Deliberately not a2a-sdk's own client, for the same reason
agents/claims-agent/delegation.py's A2ADelegate gives (its own module
docstring): a2a-sdk does ship a client with its own telemetry, but its typed
response objects and error handling don't match the plain
dict-payload-in/dict-result-out contract this module tests against. This is
the same technique, duplicated per this repo's own established convention
(correlation.py's ClaimIdSpanProcessor is triplicated the same way, one copy
per service) rather than imported across a service boundary -- each service
ships its own image, built from its own requirements.txt.

The payload this sends is exactly what
agents/settlement-agent/executor.py's `_validate_approval` reads:
`paymentId`, `amount`, `currency`, `approval.reviewerId`, `refusedTraceId`,
`refusedSpanId`. review.py builds it from the ledger record it just read
back, never from anything the listing page rendered earlier.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx


class ApprovalClient:
    def __init__(
        self,
        base_url: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 120.0,
    ):
        self._base_url = base_url.rstrip("/") + "/"
        self._transport = transport
        self._timeout = timeout

    async def execute_approved(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST an approval to senior-settlement-agent's A2A endpoint and
        return its reply, decoded.

        Raises on a JSON-RPC error (RuntimeError, carrying the agent's own
        message -- this is the shape an UnverifiedApproval takes once it
        crosses the A2A boundary: settlement-agent's executor lets it
        propagate as an unhandled exception, and a2a-sdk turns that into a
        JSON-RPC error carrying str(exc)) and on any transport failure
        (httpx's own exceptions, via raise_for_status/connection errors).
        Never returns a success shape on either kind of failure -- the
        honesty rule this whole flow exists to keep: no page in this service
        may show "executed" unless the ledger's own record said so.
        """
        request = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": uuid.uuid4().hex,
                    "parts": [{"kind": "text", "text": json.dumps(payload)}],
                }
            },
        }

        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout
        ) as client:
            response = await client.post(self._base_url, json=request)
            response.raise_for_status()
            body = response.json()

        if "error" in body:
            raise RuntimeError(
                f"senior-settlement-agent returned an error: "
                f"{body['error'].get('message')}"
            )

        return _extract_json_result(body["result"])


def _extract_json_result(result: dict) -> dict:
    """Pull the last text part out of an A2A result and parse it as JSON.

    Same technique as agents/claims-agent/delegation.py's
    `_extract_json_result` -- settlement-agent's executor enqueues exactly
    one Message event per reply (its own comment explains why: a2a-sdk 0.3.7's
    blocking message/send handler drops anything enqueued after the first),
    so the last text part is also the only one.
    """
    parts = result.get("parts")
    if parts is None:
        history = result.get("status", {}).get("message", {})
        parts = history.get("parts", [])

    texts = [p.get("text") for p in parts or [] if p.get("kind") == "text" and p.get("text")]
    for text in reversed(texts):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    raise RuntimeError(f"senior-settlement-agent returned no JSON payload: {result!r}")
