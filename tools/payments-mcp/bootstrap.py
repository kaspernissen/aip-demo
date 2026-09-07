"""OpenTelemetry bootstrap.

Imported before fastmcp as a matter of convention (see NOTES.md): on the
installed fastmcp==4.0.3, `fastmcp.telemetry.get_tracer()` resolves
`trace.get_tracer_provider()` fresh on every span creation rather than
binding a tracer at import time, so import order relative to fastmcp is no
longer load-bearing for that version (verified empirically — see NOTES.md).
Kept first regardless, for clarity and because it costs nothing.

FastMCP's own instrumentation is controlled by `FASTMCP_TELEMETRY_MODE`
(default: "native"), set explicitly in the deployment manifest.
"""
from __future__ import annotations

import os

from opentelemetry import baggage, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

CLAIM_ID_KEY = "northstar.claim.id"


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


def configure() -> None:
    service_name = os.environ.get("OTEL_SERVICE_NAME", "payments-mcp")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(ClaimIdSpanProcessor())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # W3C trace-context + baggage, explicitly, matching the other two Python
    # services (A18) and payments-api's Go side. This service had no explicit
    # registration before this task; FastMCP's own params._meta extraction
    # (fastmcp/telemetry.py) is independent of this global propagator (it
    # hand-builds its own carrier), but the outbound HTTPXClientInstrumentor
    # call to payments-api below relies on this propagator to inject
    # traceparent/baggage headers from the ambient context.
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )

    # Emits CLIENT spans for calls to payments-api and injects trace context
    # into outbound HTTP headers. OTEL_SEMCONV_STABILITY_OPT_IN=http (set in
    # the deployment manifest) is required for these spans to use the stable
    # HTTP attribute names; see NOTES.md for the empirical before/after.
    HTTPXClientInstrumentor().instrument()


configure()
