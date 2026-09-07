"""Proves the trace-assertion harness works, using a synthetic span.

This test intentionally does not touch application code. It exists so that
every later task has a trustworthy way to assert on trace shape.
"""
import os
import uuid

import pytest
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind

from tests.harness.jaeger import (
    JaegerClient,
    assert_no_orphans,
    assert_parent_child,
    assert_single_root,
    assert_span_kind,
)

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")


@pytest.fixture(scope="module")
def synthetic_trace():
    """Emit a two-span trace through the real collector, then read it back."""
    marker = uuid.uuid4().hex
    service_name = f"harness-probe-{marker}"

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{OTLP_ENDPOINT}/v1/traces"))
    )
    tracer = provider.get_tracer("harness-probe")

    with tracer.start_as_current_span("probe.parent", kind=SpanKind.SERVER):
        with tracer.start_as_current_span("probe.child", kind=SpanKind.CLIENT):
            pass

    provider.force_flush()
    provider.shutdown()

    client = JaegerClient(JAEGER_URL)
    return client.wait_for_trace(service=service_name, operation="probe.parent", timeout=90)


def test_harness_reads_back_a_synthetic_trace(synthetic_trace):
    assert len(synthetic_trace.spans) == 2


def test_harness_detects_parent_child(synthetic_trace):
    assert_parent_child(synthetic_trace, "probe.parent", "probe.child")


def test_harness_detects_span_kinds(synthetic_trace):
    assert_span_kind(synthetic_trace, "probe.parent", "server")
    assert_span_kind(synthetic_trace, "probe.child", "client")


def test_harness_detects_single_root_and_no_orphans(synthetic_trace):
    assert_single_root(synthetic_trace)
    assert_no_orphans(synthetic_trace)
