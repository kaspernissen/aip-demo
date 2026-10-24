"""Domain correlation: the claim id, carried in W3C baggage.

northstar.claim.id is set once, at intake (claims-agent). It then propagates by
itself: A2A carries it in the `baggage` header, MCP in the JSON-RPC
params._meta bag. No function downstream takes a claim id parameter just for
telemetry.

Each service copies it from baggage onto every span it starts with the upstream
`BaggageSpanProcessor` (opentelemetry-processor-baggage), restricted to this one
key. Baggage can carry other entries, set by anyone upstream, and copying them
all onto spans would export whatever they hold; so the predicate allows
`northstar.claim.id` and nothing else.
"""
from __future__ import annotations

from opentelemetry import baggage
from opentelemetry.context import Context
from opentelemetry.processor.baggage import BaggageSpanProcessor

CLAIM_ID_KEY = "northstar.claim.id"


def set_claim_id(claim_id: str, ctx: Context | None = None) -> Context:
    """Return a new Context carrying the claim id in baggage."""
    return baggage.set_baggage(CLAIM_ID_KEY, claim_id, context=ctx)


def is_claim_id(key: str) -> bool:
    """The only baggage key copied onto spans."""
    return key == CLAIM_ID_KEY


def claim_id_span_processor() -> BaggageSpanProcessor:
    """Copies northstar.claim.id, and only it, from baggage onto each span."""
    return BaggageSpanProcessor(is_claim_id)
