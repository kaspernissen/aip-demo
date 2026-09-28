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

    def execute(self, payment_id: str, amount: float, currency: str = "EUR") -> dict:
        # The execution states the amount it believes it is paying. payments-api
        # holds the proposal and is the party that can confirm the two agree.
        response = self._client.post(
            f"/payments/{payment_id}/execute",
            json={"amount": amount, "currency": currency},
        )
        response.raise_for_status()
        return response.json()

    def get(self, payment_id: str) -> dict:
        response = self._client.get(f"/payments/{payment_id}")
        response.raise_for_status()
        return response.json()

    def refuse(
        self,
        payment_id: str,
        *,
        rule: str,
        limit: float,
        requested: float,
        narrative: str,
        refused_trace_id: str,
        refused_span_id: str,
    ) -> dict:
        """Record why a payout was refused, beside the payout, via
        `POST /payments/{id}/refuse`. Every field is sent, always -- this
        client is the endpoint's only caller.

        `refused_span_id` travels beside `refused_trace_id`: a span link
        needs a real span id to survive the OpenTelemetry SDK's own validity
        check, not just a trace id.
        """
        response = self._client.post(
            f"/payments/{payment_id}/refuse",
            json={
                "rule": rule,
                "limit": limit,
                "requested": requested,
                "narrative": narrative,
                "refusedTraceId": refused_trace_id,
                "refusedSpanId": refused_span_id,
            },
        )
        response.raise_for_status()
        return response.json()
