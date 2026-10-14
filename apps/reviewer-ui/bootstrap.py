"""OpenTelemetry bootstrap for reviewer-ui.

Imported first in main.py, matching every other Python service in this repo.

Deliberately does NOT instrument FastAPI/Starlette (no
opentelemetry-instrumentation-fastapi/-starlette anywhere in
requirements.txt, and none applied here). Every other service that wraps a
Starlette-based ASGI app auto-instruments it so the framework's own entry
span becomes the trace root; this service instead follows
apps/customer-ui's pattern of hand-created root spans that a reader can find
by name in the telemetry contract (`claim.submit` there; `review.decision`
and `review.queue` here, in review.py and main.py), rather than unnamed
framework-generated entry spans with the owned span nested one level inside
them.

**`GET /healthz` produces no spans; `GET /` produces one.** `index()` opens
an explicit `review.queue` parent span before its ledger read, because
`HTTPXClientInstrumentor`'s CLIENT span for that read has no parent
otherwise and roots a trace of its own. `GET /healthz` really does produce
nothing -- it makes no outbound call and owns no domain event.

HTTPXClientInstrumentor is still applied: it is what emits CLIENT spans for
the outbound calls ledger.py and gateway.py make (to payments-api and to
agentgateway respectively), and -- the property that actually
matters for the demo -- it is what injects the `traceparent` header on every
one of them while one of this service's own spans is current, which is how
the gateway's, the PDS's and payments-mcp's spans for the release, and
payments-api's own server spans for the ledger reads, end up in `review.decision`'s / `review.queue`'s trace
rather than starting unrelated ones.

This service registers the stock upstream `TraceContextTextMapPropagator`
like every other service in this repo, with no flag-byte masking. That
matters because `opentelemetry-sdk`'s Python implementation emits W3C Trace
Context **Level 2**'s `RANDOM_TRACE_ID` bit alongside `SAMPLED` -- a
`...-03` traceparent -- and an `go.opentelemetry.io/otel` version whose
`propagation/trace_context.go` rejects a flag byte above `0x02` will discard
that traceparent outright, minting a fresh trace root for every ledger read
instead of joining `payments-api`'s server span to this service's trace. See
`docs/upstream-findings.md` Finding 11 for the full account, and
`systems/payments-api`'s pinned otel-go version for the current guarantee.
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


def configure() -> None:
    service_name = os.environ.get("OTEL_SERVICE_NAME", "reviewer-ui")
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # Must run before HTTPXClientInstrumentor below, not after: that
    # instrumentor resolves a meter once, inside `_instrument`
    # (`get_meter(...)`, opentelemetry/instrumentation/httpx/__init__.py:775
    # and three sibling call sites), so a provider installed afterwards is
    # reached only via the API proxy's rebinding path. See
    # agents/settlement-agent/bootstrap.py for the full note on that ordering.
    _configure_metrics(resource)

    # W3C trace-context + baggage, explicitly, matching every other service
    # in this repo (already OTel Python's own default; registered explicitly
    # rather than left implicit, per this repo's own convention -- see
    # agents/settlement-agent/bootstrap.py's identical comment). No flag-byte
    # masking: this is byte-for-byte the block every other service runs. See
    # the module docstring for why that matters against payments-api.
    set_global_textmap(
        CompositePropagator(
            [TraceContextTextMapPropagator(), W3CBaggagePropagator()]
        )
    )

    # Emits CLIENT spans for every outbound httpx call this process makes
    # (ledger.py's calls to payments-api, gateway.py's MCP calls to
    # agentgateway) and injects trace context into those outbound
    # headers.
    HTTPXClientInstrumentor().instrument()


def _metric_reader() -> PeriodicExportingMetricReader:
    """The single reader this service's metrics leave through.

    Split out so a test can build one under a controlled environment; see
    `agents/settlement-agent/bootstrap.py`'s copy for why that split is needed.
    """
    return PeriodicExportingMetricReader(OTLPMetricExporter())


def _configure_metrics(resource: Resource) -> None:
    """Install a MeterProvider. Endpoint and protocol come from the
    standard `OTEL_EXPORTER_OTLP_*` variables, as for traces.

    Identical in shape to every other Python service here.
    **`agents/settlement-agent/bootstrap.py`'s copy carries the full
    rationale** -- why no `views=`, why no temporality preference, and what the
    export interval does and does not drop. This docstring stays a pointer
    rather than a second copy that can drift from it;
    `tests/unit/test_metrics_wiring.py` asserts the five copies stay identical.

    **Why this service gets one even though it runs no model.** A framework
    emitting no GenAI metrics is only a meaningful finding if it was measured:
    without a provider here, reviewer-ui emitting nothing would say nothing
    about any framework -- it would only say this process had nowhere to put
    a measurement. The absence is the finding, so the absence has to be real.
    """
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[_metric_reader()])
    )


configure()
