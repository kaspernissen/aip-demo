"""OpenTelemetry bootstrap for claims-agent.

Imported first in main.py. The a2a-sdk telemetry extra instruments the A2A
server's own internal call graph (see NOTES.md); nothing here creates A2A
spans by hand. HTTPXClientInstrumentor is added here (not in the brief's
original sketch) because `delegation.A2ADelegate` is httpx-based, and A13
requires measuring this service's own outbound HTTP spans rather than
assuming another service's spelling carries over. It also happens to be the
mechanism (per NOTES.md's capture) by which this agent's outbound call to
settlement-agent gets a `traceparent` header injected at all — question 1
in this task's brief.

A18: a2a-sdk 0.3.7's own telemetry does not extract inbound W3C trace context
(verified for settlement-agent in Task 4, reconfirmed here); StarletteInstrumentor
(applied to the built app in main.py, since the a2a-sdk's Starlette instance
doesn't exist until then) is what does that, using the global propagator
configured below.
"""
from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

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

    service_name = os.environ.get("OTEL_SERVICE_NAME", "claims-agent")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(ClaimIdSpanProcessor())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # W3C trace-context + baggage, explicitly, matching payments-api's Go side
    # and settlement-agent's bootstrap.py (A18). This is already OTel
    # Python's own default (OTEL_PROPAGATORS defaults to
    # "tracecontext,baggage"), but A18 asks for it registered explicitly
    # rather than left to an implicit default.
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )

    # Emits CLIENT spans for the outbound HTTP calls delegation.A2ADelegate
    # makes to settlement-agent, and injects trace context into those
    # outbound headers via the ordinary ambient-context mechanism. See
    # NOTES.md for the captured attribute spelling (A13) and confirmation of
    # whether the injected header actually reaches settlement-agent.
    HTTPXClientInstrumentor().instrument()


configure()
