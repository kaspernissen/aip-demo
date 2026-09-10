"""The direct HTTP client to payments-api.

Deliberately not through payments-mcp/agentgateway/the Policy Decision
Service: `GET /payments?status=refused` and `GET /payments/{id}` are plain
REST routes payments-api itself serves (systems/payments-api/handlers.go),
not MCP tools, and reviewer-ui has no business anywhere near the governed MCP
path -- it holds no `EXECUTE_LIMITS` row and must never be able to reach a
place that would grant one. `deploy/platform/cilium-policies.yaml`'s
reviewer-ui egress allowlist reflects exactly this: payments-api and
senior-settlement-agent, nothing else.

Every dict this module returns is decoded straight from payments-api's own
JSON body -- no field is renamed, summed, rounded or otherwise recomputed.
That is the honesty rule review.py and render.py both depend on: the only
thing between the ledger and the page is JSON decoding.
"""
from __future__ import annotations

from typing import Any

import httpx


class LedgerUnavailable(RuntimeError):
    """payments-api could not be reached, or answered with something that
    isn't a valid payment record -- a connection failure, a 5xx, or a body
    that doesn't parse as JSON. Never conflated with PaymentNotFound: this
    means the ledger itself is not answering, not that it answered "no such
    payment"."""


class PaymentNotFound(RuntimeError):
    """payments-api's own 404 for a payment id that names nothing."""


class LedgerClient:
    def __init__(
        self,
        base_url: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout

    async def list_refused(self) -> list[dict[str, Any]]:
        """GET /payments?status=refused -- every payment awaiting review,
        sorted by paymentId (payments-api's own store.list)."""
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout
        ) as client:
            try:
                response = await client.get(
                    f"{self._base_url}/payments", params={"status": "refused"}
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                raise LedgerUnavailable(
                    f"could not list refused payments from payments-api: {exc}"
                ) from exc
            except ValueError as exc:  # json.JSONDecodeError is a ValueError
                raise LedgerUnavailable(
                    f"payments-api's response was not valid JSON: {exc}"
                ) from exc

    async def get(self, payment_id: str) -> dict[str, Any]:
        """GET /payments/{id} -- read back what the ledger actually
        recorded. Used at decision time (review.py's `decide`) so
        `review.elapsed_ms` and the approval payload are built from the
        current ledger state, not from whatever the listing page rendered
        when it loaded."""
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout
        ) as client:
            try:
                response = await client.get(f"{self._base_url}/payments/{payment_id}")
            except httpx.HTTPError as exc:
                raise LedgerUnavailable(
                    f"could not reach payments-api for payment {payment_id!r}: {exc}"
                ) from exc

            if response.status_code == 404:
                raise PaymentNotFound(f"payment {payment_id!r} not found")
            try:
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                raise LedgerUnavailable(
                    f"payments-api returned an unexpected status for payment "
                    f"{payment_id!r}: {exc}"
                ) from exc
            except ValueError as exc:
                raise LedgerUnavailable(
                    f"payments-api's response for payment {payment_id!r} was "
                    f"not valid JSON: {exc}"
                ) from exc
