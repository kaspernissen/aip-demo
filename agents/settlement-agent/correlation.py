"""Domain correlation.

northstar.claim.id is set once, here, at intake. It then propagates
automatically: A2A carries it in W3C baggage headers, MCP carries it in the
JSON-RPC params._meta bag. No function downstream takes a claim id parameter
purely for telemetry.
"""
from __future__ import annotations

from opentelemetry import baggage
from opentelemetry.context import Context

CLAIM_ID_KEY = "northstar.claim.id"


def set_claim_id(claim_id: str, ctx: Context | None = None) -> Context:
    """Return a new Context carrying the claim id in baggage."""
    return baggage.set_baggage(CLAIM_ID_KEY, claim_id, context=ctx)
