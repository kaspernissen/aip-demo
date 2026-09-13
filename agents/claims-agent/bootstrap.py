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

from opentelemetry import metrics, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
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
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(ClaimIdSpanProcessor())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # M4 Task 2, before HTTPXClientInstrumentor below rather than after it:
    # that instrumentor resolves a meter once, inside `_instrument`
    # (`get_meter(...)`, opentelemetry/instrumentation/httpx/__init__.py:775
    # and three sibling call sites), so a provider installed afterwards is
    # reached only via the API proxy's rebinding path. See
    # agents/settlement-agent/bootstrap.py for the full note on that ordering.
    _configure_metrics(resource)

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


def _metric_reader() -> PeriodicExportingMetricReader:
    """The single reader this service's metrics leave through.

    Split out so a test can build one under a controlled environment; see
    `agents/settlement-agent/bootstrap.py`'s copy for why that split is needed.
    """
    return PeriodicExportingMetricReader(OTLPMetricExporter())


def _configure_metrics(resource: Resource) -> None:
    """Install a MeterProvider (M4 Task 2). Endpoint and protocol come from the
    standard `OTEL_EXPORTER_OTLP_*` variables, as for traces.

    Identical in shape to every other Python service here.
    **`agents/settlement-agent/bootstrap.py`'s copy carries the full
    rationale** -- why no `views=`, why no temporality preference, and what the
    export interval does and does not drop. This docstring stays a pointer
    rather than a second copy that can drift from it;
    `tests/unit/test_metrics_wiring.py` asserts the five copies stay identical.

    **Why this service gets one even though it runs no model.** M4 §6 asks
    which GenAI metrics the frameworks emit, and an answer of "none" is only
    worth publishing if it was measured. Without a provider here, claims-agent
    emitting nothing would say nothing about `a2a-sdk` or about the httpx
    instrumentation -- it would only say this process had nowhere to put a
    measurement. The absence is the finding, so the absence has to be real.
    """
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[_metric_reader()])
    )


configure()
