"""OpenTelemetry bootstrap for reviewer-ui.

Imported first in main.py, matching every other Python service in this repo.

Deliberately does NOT instrument FastAPI/Starlette (no
opentelemetry-instrumentation-fastapi/-starlette anywhere in
requirements.txt, and none applied here). Every other service that wraps a
Starlette-based ASGI app auto-instruments it so the framework's own entry
span becomes the trace root; this service instead follows
apps/customer-ui's pattern of a single, hand-created root span
(`claim.submit` there; `review.decision` here, in review.py) that a reader
can find by name in the telemetry contract, rather than an unnamed
framework-generated entry span with the owned span nested one level inside
it. `GET /` and `GET /healthz` therefore produce no spans at all -- neither
is owned telemetry this service has anything to say about.

HTTPXClientInstrumentor is still applied: it is what emits CLIENT spans for
the outbound calls ledger.py and approval.py make (to payments-api and to
senior-settlement-agent respectively), and -- the property that actually
matters for the demo -- it is what injects the `traceparent` header on the
outbound call to senior-settlement-agent while `review.decision` is the
current span, which is how that call's own inbound server span ends up as a
child of `review.decision`'s trace rather than starting an unrelated one.
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


def configure() -> None:
    service_name = os.environ.get("OTEL_SERVICE_NAME", "reviewer-ui")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # W3C trace-context + baggage, explicitly, matching every other service
    # in this repo (already OTel Python's own default; registered explicitly
    # rather than left implicit, per this repo's own convention -- see
    # agents/settlement-agent/bootstrap.py's identical comment).
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )

    # Emits CLIENT spans for every outbound httpx call this process makes
    # (ledger.py's calls to payments-api, approval.py's A2A call to
    # senior-settlement-agent) and injects trace context into those outbound
    # headers.
    HTTPXClientInstrumentor().instrument()


configure()
