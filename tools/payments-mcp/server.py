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
def execute_payment(payment_id: str, amount: float, currency: str = "EUR") -> dict:
    """Execute a previously proposed payment.

    Money leaves the company. This action cannot be undone.

    Pass the amount and currency of the proposal you are executing. The call
    states what it is paying rather than naming an opaque id alone, so the
    payment system can confirm the instruction matches what it recorded.
    """
    return payments.execute(payment_id, amount, currency)


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        # Not destructive: this records a reason beside a payment that
        # already did not execute. It moves no money and reverses nothing,
        # the same argument propose_payment's annotation makes above -- and
        # the Policy Decision Service reads this tool as unlimited for
        # exactly that reason (platform/policy-decision-service/pds/rules.py,
        # UNLIMITED_TOOLS).
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
def refuse_payment(
    payment_id: str,
    rule: str,
    limit: float,
    requested: float,
    narrative: str,
    refused_trace_id: str,
    refused_span_id: str,
) -> dict:
    """Record why a payout was refused, beside the payout.

    Called by settlement-agent's own decide_settlement, through the same
    governed MCP path every other payments call in this repo takes -- not a
    new HTTP path from the agent to payments-api. Moves no money; the
    execute path stays exactly as guarded as it always was.
    """
    return payments.refuse(
        payment_id,
        rule=rule,
        limit=limit,
        requested=requested,
        narrative=narrative,
        refused_trace_id=refused_trace_id,
        refused_span_id=refused_span_id,
    )


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        # M3 Task 3 fix round (finding 1, R11): execute_approved reads the
        # ledger back through this tool before executing an approval, so it
        # has to be reachable through the same governed MCP path as every
        # other payments call. It moves nothing and reverses nothing -- the
        # Policy Decision Service reads this tool as unlimited for exactly
        # that reason (platform/policy-decision-service/pds/rules.py,
        # UNLIMITED_TOOLS).
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def get_payment(payment_id: str) -> dict:
    """Read back what the ledger recorded for a payment.

    Used by settlement-agent's execute_approved to confirm, before executing,
    that a refusal record actually exists for this payment and that its
    recorded amount matches what this approval asks to execute.
    """
    return payments.get(payment_id)


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )
