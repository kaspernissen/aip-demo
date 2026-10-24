"""A stdio MCP server standing in for payments-mcp: propose_payment and
execute_payment, with payments-mcp's argument names (agents/settlement-agent/
mcp_tools.py: claim_id/amount/currency, payment_id/amount/currency).

SCENARIO (env) selects what execute_payment does:
  happy       returns EXECUTED
  refused     returns an MCP error result carrying the PDS deny text, the way
              agentgateway's mcpGuardrails rejection reaches the agent
  tool_error  returns an MCP error result for a 409 (a malfunction, not policy)
"""
from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

DENY = (
    "mcp: mcpGuardrails rejected: requested 6200 EUR exceeds the 6000 EUR limit for "
    "this agent at autonomy level assisted [policy-denial "
    "rule=settlement-agent.assisted.execute_limit limit=6000.00 requested=6200.00]"
)

server = MCPServer("payments-mcp-stub")


@server.tool()
def propose_payment(amount: float, currency: str = "EUR", claim_id: str = "") -> dict:
    """Propose a payment against this claim. Returns the payment, including its paymentId."""
    return {"paymentId": "PAY-000001", "claimId": claim_id, "amount": amount,
            "currency": currency, "status": "PROPOSED"}


@server.tool()
def execute_payment(payment_id: str, amount: float, currency: str = "EUR") -> dict:
    """Execute a proposed payment. This moves money and cannot be undone."""
    scenario = os.environ.get("SCENARIO", "happy")
    if scenario == "refused":
        raise ToolError(DENY)
    if scenario == "tool_error":
        raise ToolError("payment PAY-000001 already executed (409)")
    return {"paymentId": payment_id, "claimId": "CLM-1842", "amount": amount,
            "currency": currency, "status": "EXECUTED"}


if __name__ == "__main__":
    server.run("stdio")
