"""Domain correlation.

northstar.claim.id is set once, here, at intake. It then propagates
automatically: A2A carries it in W3C baggage headers, MCP carries it in the
JSON-RPC params._meta bag. No function downstream takes a claim id parameter
purely for telemetry.

`ClaimIdSpanProcessor` lives here, alongside `CLAIM_ID_KEY` and
`set_claim_id`, because it is the mechanism that implements this file's own
correlation rule (copying the id from baggage onto a span), and this is the
one file each service's correlation code points to. The class is
byte-identical across services, one copy per service rather than a shared
dependency, matching `CLAIM_ID_KEY`/`set_claim_id` above.
"""
from __future__ import annotations

from opentelemetry import baggage
from opentelemetry.context import Context
from opentelemetry.sdk.trace import Span, SpanProcessor

CLAIM_ID_KEY = "northstar.claim.id"


def set_claim_id(claim_id: str, ctx: Context | None = None) -> Context:
    """Return a new Context carrying the claim id in baggage."""
    return baggage.set_baggage(CLAIM_ID_KEY, claim_id, context=ctx)


class ClaimIdSpanProcessor(SpanProcessor):
    """Copies northstar.claim.id from baggage onto every span in this process.

    Standard OTel enrichment: it adds a domain fact to spans this process
    produces. It does not invent a convention, and it never touches telemetry
    emitted by infrastructure we do not run in-process.
    """

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        claim_id = baggage.get_baggage(CLAIM_ID_KEY, parent_context)
        if claim_id is not None:
            span.set_attribute(CLAIM_ID_KEY, str(claim_id))

    def on_end(self, span) -> None:  # noqa: D102
        pass

    def shutdown(self) -> None:  # noqa: D102
        pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:  # noqa: D102
        return True
