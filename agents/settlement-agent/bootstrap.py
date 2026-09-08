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

    _instrument_pydantic_ai()


def _instrument_pydantic_ai() -> None:
    """Turn on PydanticAI's own OTel instrumentation.

    Verified against pydantic-ai-slim 2.41.0:
      * `Agent(..., instrument=True)` does NOT exist at this version — it
        raises TypeError. `Agent.instrument_all()` is a staticmethod setting a
        process-wide default, which is what we want: one call, every agent.
      * `include_content=False` keeps message *shape* in the spans
        (role, parts[].type) but drops prompts, completions and tool
        arguments/results. Recording everything a model was told is itself a
        compliance problem in insurance — see docs/telemetry-contract.md.
      * `version` is Literal[2,3,4,5,6], default 5. Pinned explicitly so a
        library default change cannot silently reshape our spans.
    """
    from pydantic_ai import Agent
    from pydantic_ai.models.instrumented import InstrumentationSettings

    Agent.instrument_all(
        InstrumentationSettings(include_content=False, version=5)
    )


configure()
