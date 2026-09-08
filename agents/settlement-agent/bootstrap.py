"""OpenTelemetry bootstrap for settlement-agent.

Imported first in main.py. The a2a-sdk telemetry extra instruments the A2A
server's own internal call graph (see NOTES.md); nothing here creates A2A
spans by hand. HTTPXClientInstrumentor is added here (not in the brief's
original sketch) because settlement-agent's MCP client
(`mcp.client.streamable_http`) is httpx-based under the hood, and A13
requires measuring this service's own outbound HTTP spans rather than
assuming payments-mcp's spelling carries over.

A18: a2a-sdk 0.3.7's own telemetry does not extract inbound W3C trace context
(verified empirically — see NOTES.md); StarletteInstrumentor (applied to the
built app in main.py, since the a2a-sdk's Starlette instance doesn't exist
until then) is what does that, using the global propagator configured below.
"""
from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.baggage.propagation import W3CBaggagePropagator

# N1 (final-review.md): ClaimIdSpanProcessor lives in correlation.py now, not
# here — it is the mechanism that implements that file's own correlation
# rule, and a reader told "correlation.py is the shared file" (A18) should
# find it there.
from correlation import ClaimIdSpanProcessor


def configure() -> None:
    if os.environ.get("DISABLE_OTEL_INSTRUMENTATION", "").lower() == "true":
        raise RuntimeError(
            "DISABLE_OTEL_INSTRUMENTATION is set to true. In the a2a-sdk it takes "
            "precedence over all other OTel configuration and would silently "
            "disable A2A tracing, which this project's tests depend on."
        )

    service_name = os.environ.get("OTEL_SERVICE_NAME", "settlement-agent")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(ClaimIdSpanProcessor())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # W3C trace-context + baggage, explicitly, matching payments-api's Go
    # side (systems/payments-api/telemetry.go:
    # propagation.NewCompositeTextMapPropagator(propagation.TraceContext{},
    # propagation.Baggage{})) — this is already OTel Python's own default
    # (OTEL_PROPAGATORS defaults to "tracecontext,baggage"), but A18 asks for
    # it registered explicitly, the same way the rest of the repo does, not
    # left to an implicit default.
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )

    # Emits CLIENT spans for the outbound HTTP calls the MCP streamable-http
    # transport makes to payments-mcp, and injects trace context into those
    # outbound headers. The measurement in NOTES.md §6 confirmed this
    # instrumentation defaults to the legacy HTTP attribute spelling, so
    # OTEL_SEMCONV_STABILITY_OPT_IN=http is set on the deployment manifest to
    # converge it onto the stable spelling.
    HTTPXClientInstrumentor().instrument()


configure()
