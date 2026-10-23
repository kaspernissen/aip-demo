"""OpenTelemetry bootstrap for claims-agent.

Imported first in main.py. The a2a-sdk telemetry extra instruments the A2A
server's own internal call graph (see NOTES.md); nothing here creates A2A
spans by hand. HTTPXClientInstrumentor is added here (a2a-sdk's own
instrumentation does not cover it) because `delegation.A2ADelegate` is
httpx-based, and this service's own outbound HTTP spans need to be measured
directly rather than assumed from another service's instrumentation. It also
happens to be the mechanism (see NOTES.md) by which this agent's outbound
call to settlement-agent gets a `traceparent` header injected at all.

a2a-sdk 0.3.7's own telemetry does not extract inbound W3C trace context;
StarletteInstrumentor (applied to the built app in main.py, since the
a2a-sdk's Starlette instance doesn't exist until then) is what does that,
using the global propagator configured below.

M13: claims-agent runs a model (extraction.py, the free-text intake), so this
file now does what settlement-agent's bootstrap does for its model:
`HTTPX2ClientInstrumentor` for the model's own outbound hop, and PydanticAI's
instrumentation with the same settings and the same content-capture switch.
"""
from __future__ import annotations

import os

from opentelemetry import metrics, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import (
    HTTPX2ClientInstrumentor,
    HTTPXClientInstrumentor,
)
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

# ClaimIdSpanProcessor lives in correlation.py, not here — it is the
# mechanism that implements that file's own correlation rule, and keeping it
# there matches settlement-agent's copy of this same bootstrap module.
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

    # Must run before HTTPXClientInstrumentor below, not after: that
    # instrumentor resolves a meter once, inside `_instrument`
    # (`get_meter(...)`, opentelemetry/instrumentation/httpx/__init__.py:775
    # and three sibling call sites), so a provider installed afterwards is
    # reached only via the API proxy's rebinding path. See
    # agents/settlement-agent/bootstrap.py for the full note on that ordering.
    _configure_metrics(resource)

    # W3C trace-context + baggage, explicitly, matching payments-api's Go side
    # and settlement-agent's bootstrap.py. This is already OTel Python's own
    # default (OTEL_PROPAGATORS defaults to "tracecontext,baggage"), but it is
    # registered explicitly here rather than left to an implicit default.
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )

    # Emits CLIENT spans for the outbound HTTP calls delegation.A2ADelegate
    # makes to settlement-agent, and injects trace context into those
    # outbound headers via the ordinary ambient-context mechanism. See
    # NOTES.md for the captured attribute spelling and confirmation that the
    # injected header actually reaches settlement-agent.
    HTTPXClientInstrumentor().instrument()
    # The intake model's hop to api.anthropic.com. anthropic==1.4.0 is built
    # on httpx2, a separate distribution HTTPXClientInstrumentor does not
    # cover; without this the model call is only pydantic-ai's `chat` span,
    # with no HTTP status, retry or wire latency under it. Same reasoning, in
    # full, in agents/settlement-agent/bootstrap.py (docs/telemetry-contract.md
    # Gap 7).
    HTTPX2ClientInstrumentor().instrument()

    _instrument_pydantic_ai()


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

    **Why this service had one even before it ran a model.** Whether
    `a2a-sdk` or the httpx instrumentation emit any GenAI metrics is only
    worth reporting as "none" if it was actually measured. Without a
    provider here, claims-agent emitting nothing would say nothing about
    those instrumentations -- it would only say this process had nowhere to
    put a measurement. The absence is only meaningful if it's real. Since
    M13 the intake model's PydanticAI histograms land here too, which is why
    this must run before `_instrument_pydantic_ai()` (see settlement-agent's
    copy for that ordering).
    """
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[_metric_reader()])
    )



def capture_content_enabled() -> bool:
    """Whether to record prompts, completions and tool arguments in spans.

    Defaults to ON, read from the same `NORTHSTAR_CAPTURE_CONTENT` variable
    as settlement-agent's identical function, so one switch covers both
    agents' model calls. For the intake that content is the customer's own
    description of the accident; docs/telemetry-contract.md's "Content
    capture" section carries the trade-off.
    """
    raw = os.environ.get("NORTHSTAR_CAPTURE_CONTENT")
    if raw is None:
        return True
    return raw.strip().lower() not in {"false", "0", "no", "off"}


def _instrument_pydantic_ai() -> None:
    """Turn on PydanticAI's own OTel instrumentation, exactly as
    agents/settlement-agent/bootstrap.py does (its docstring has the
    verification against pydantic-ai-slim 2.42.0): `Agent.instrument_all`,
    because `Agent(..., instrument=...)` does not exist at that version;
    `include_content` from the capture switch; `version=5` pinned.
    `tests/test_instrumentation_settings.py` holds the two copies equal.
    """
    from pydantic_ai import Agent
    from pydantic_ai.models.instrumented import InstrumentationSettings

    Agent.instrument_all(
        InstrumentationSettings(include_content=capture_content_enabled(), version=5)
    )


configure()
