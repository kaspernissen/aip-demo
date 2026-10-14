"""The claims-agent's one read of the ledger: what happened to this payment?

Used only when a task is resumed. The resume message is never trusted; the
customer is told what payments-api says. Plain REST, like reviewer-ui's
ledger.py: a read moves no money, so it does not need the governed MCP path.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_URL = os.environ.get(
    "PAYMENTS_API_URL", "http://payments-api.northstar-systems.svc.cluster.local:8080")


class LedgerReader:
    def __init__(self, base_url: str = DEFAULT_URL, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout

    async def get(self, payment_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            response = await client.get(f"{self._base_url}/payments/{payment_id}")
            response.raise_for_status()
            return response.json()
