"""HTTP client for the payments system of record.

Instrumentation note: no spans are created here by hand. The httpx
instrumentation registered in bootstrap.py emits CLIENT spans following HTTP
semantic conventions, and injects trace context into outbound headers.
"""
from __future__ import annotations

import httpx


class PaymentsClient:
    def __init__(
        self,
        base_url: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), transport=transport, timeout=timeout
        )

    def propose(self, claim_id: str, amount: float, currency: str = "EUR") -> dict:
        response = self._client.post(
            "/payments/propose",
            json={"claimId": claim_id, "amount": amount, "currency": currency},
        )
        response.raise_for_status()
        return response.json()

    def execute(self, payment_id: str) -> dict:
        response = self._client.post(f"/payments/{payment_id}/execute")
        response.raise_for_status()
        return response.json()

    def get(self, payment_id: str) -> dict:
        response = self._client.get(f"/payments/{payment_id}")
        response.raise_for_status()
        return response.json()
