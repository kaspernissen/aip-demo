"""Payments MCP server.

This is an adapter over payments-api, not a system of record. It owns no state.
"""
from __future__ import annotations

import bootstrap  # noqa: F401  imported first for clarity; see bootstrap.py and NOTES.md

import os

from fastmcp import FastMCP

from payments_client import PaymentsClient

mcp = FastMCP("payments-mcp")
payments = PaymentsClient(os.environ.get("PAYMENTS_API_URL", "http://payments-api:8080"))


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
def propose_payment(claim_id: str, amount: float, currency: str = "EUR") -> dict:
    """Propose a payment for a claim. Records an intent; moves no money."""
    return payments.propose(claim_id, amount, currency)


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
def execute_payment(payment_id: str) -> dict:
    """Execute a previously proposed payment.

    Money leaves the company. This action cannot be undone.
    """
    return payments.execute(payment_id)


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )
