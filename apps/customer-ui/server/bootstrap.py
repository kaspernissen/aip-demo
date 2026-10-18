"""OpenTelemetry bootstrap for the customer-ui server.

Imported first in main.py, matching every other Python service in this repo.

Same shape as apps/reviewer-ui/bootstrap.py: a TracerProvider and a
MeterProvider, both exporting OTLP/HTTP to the endpoint named by the standard
OTEL_EXPORTER_OTLP_* variables, W3C trace-context + baggage registered
explicitly, nothing sampled and nothing dropped. Unlike reviewer-ui this
service instruments Starlette (main.py): its three routes are not domain
events with a name worth owning, they are the relay's plumbing, and the
framework's own server span is what shows the push arriving. The push token
travels in a header only; the instrumentor records no request headers, so no
span attribute carries it (tests/test_relay.py asserts that).

There is no HTTP client here: the server never calls out, so no httpx
instrumentation.
"""
from __future__ import annotations

import os

from opentelemetry import metrics, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


def configure() -> None:
    service_name = os.environ.get("OTEL_SERVICE_NAME", "customer-ui-server")
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(
        MeterProvider(
            resource=resource,
            metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
        )
    )
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )


configure()
