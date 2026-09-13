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
    service_name = os.environ.get("OTEL_SERVICE_NAME", "payments-mcp")
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

    **This service is the one that decides M4 §6's `gen_ai.execute_tool.duration`
    row.** Tool execution happens here, under FastMCP, so this is the process in
    which that metric would be recorded if FastMCP recorded it. Without a
    provider here, "FastMCP emits no `execute_tool` metric" would be a claim
    about our own wiring rather than about FastMCP -- exactly the false negative
    the inventory must not contain.
    """
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[_metric_reader()])
    )


configure()
