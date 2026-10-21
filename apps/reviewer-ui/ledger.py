"""The direct HTTP client to payments-api.

reviewer-ui reads and rejects on payments-api directly: `GET
/payments?status=awaiting_approval`, `GET /payments/{id}` and `POST
/payments/{id}/reject` are plain REST routes payments-api itself serves
(systems/payments-api/handlers.go), not MCP tools, and none of them moves
money. reviewer-ui **releases** money only through gateway.py, the governed
path where the Policy Decision Service decides. `deploy/platform/reach/`'s
reviewer-ui egress allowlist (network-policies.yaml) and payments-api's
per-route ingress policy (linkerd-policies.yaml) reflect exactly this:
payments-api (read, reject) and agentgateway (release), nothing else.

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


class PaymentNotAwaiting(RuntimeError):
    """payments-api's 409 on reject: the payment is not awaiting approval
    (already executed, rejected, or never refused)."""


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

    async def list_awaiting(self) -> list[dict[str, Any]]:
        """GET /payments?status=awaiting_approval -- every payment awaiting
        review, sorted by paymentId (payments-api's own store.list)."""
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout
        ) as client:
            try:
                response = await client.get(
                    f"{self._base_url}/payments", params={"status": "awaiting_approval"}
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                raise LedgerUnavailable(
                    f"could not list payments awaiting approval from payments-api: {exc}"
                ) from exc
            except ValueError as exc:  # json.JSONDecodeError is a ValueError
                raise LedgerUnavailable(
                    f"payments-api's response was not valid JSON: {exc}"
                ) from exc

    async def get(self, payment_id: str) -> dict[str, Any]:
        """GET /payments/{id} -- read back what the ledger actually
        recorded. Used at decision time (review.py's `decide`) so
        `northstar.review.wait.duration` and the release call are built from the
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

    async def reject(self, payment_id: str, reviewer_id: str) -> dict[str, Any]:
        """POST /payments/{id}/reject. 409 (not awaiting) is PaymentNotAwaiting."""
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            try:
                response = await client.post(f"{self._base_url}/payments/{payment_id}/reject",
                                             json={"reviewerId": reviewer_id})
            except httpx.HTTPError as exc:
                raise LedgerUnavailable(f"could not reach payments-api to reject {payment_id!r}: {exc}") from exc
        if response.status_code == 404:
            raise PaymentNotFound(f"payment {payment_id!r} not found")
        if response.status_code == 409:
            raise PaymentNotAwaiting(f"payment {payment_id!r} is not awaiting approval")
        try:
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LedgerUnavailable(f"payments-api rejected {payment_id!r} oddly: {exc}") from exc
