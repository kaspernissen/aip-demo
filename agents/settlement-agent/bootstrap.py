"""OpenTelemetry bootstrap for settlement-agent.

Imported first in main.py. The a2a-sdk telemetry extra instruments the A2A
server's own internal call graph (see NOTES.md); nothing here creates A2A
spans by hand. The httpx instrumentors are added here because
settlement-agent's MCP client (`mcp.client.streamable_http`) is httpx-based
under the hood, and this service's own outbound HTTP spans need to be
measured rather than assuming payments-mcp's spelling carries over. That
client is built on `httpx2` rather than `httpx`, so it is
`HTTPX2ClientInstrumentor` that covers the payments-mcp hop; see the comment
on that call below.

a2a-sdk 0.3.7's own telemetry does not extract inbound W3C trace context
(verified empirically — see NOTES.md); StarletteInstrumentor (applied to the
built app in main.py, since the a2a-sdk's Starlette instance doesn't exist
until then) is what does that, using the global propagator configured below.
"""
from __future__ import annotations

import os

from opentelemetry import metrics, trace
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
from opentelemetry.baggage.propagation import W3CBaggagePropagator

# ClaimIdSpanProcessor lives in correlation.py, the module shared with
# claims-agent that implements claim-id/trace correlation for both agents.
from correlation import ClaimIdSpanProcessor


def configure() -> None:
    if os.environ.get("DISABLE_OTEL_INSTRUMENTATION", "").lower() == "true":
        raise RuntimeError(
            "DISABLE_OTEL_INSTRUMENTATION is set to true. In the a2a-sdk it takes "
            "precedence over all other OTel configuration and would silently "
            "disable A2A tracing, which this project's tests depend on."
        )

    service_name = os.environ.get("OTEL_SERVICE_NAME", "settlement-agent")
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(ClaimIdSpanProcessor())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # **Ordering is load-bearing here, not stylistic**: this call
    # must precede `_instrument_pydantic_ai()` below and both httpx
    # instrumentors. Each of those resolves a meter once, at instrumentation
    # time -- PydanticAI in `InstrumentationSettings.__init__`
    # (`pydantic_ai/models/instrumented.py:146`: `meter_provider or
    # get_meter_provider()`, then `create_histogram` immediately after), the
    # httpx instrumentors in their own `_instrument` (`get_meter(...)` at
    # `opentelemetry/instrumentation/httpx/__init__.py:775` and three sibling
    # call sites). Resolving before a provider is set yields the API's proxy
    # meter; the proxy does rebind when `set_meter_provider` runs later, but
    # depending on that rebinding is depending on an implementation detail of
    # the API package rather than on an explicit ordering guarantee.
    _configure_metrics(resource)

    # W3C trace-context + baggage, explicitly, matching payments-api's Go
    # side (systems/payments-api/telemetry.go:
    # propagation.NewCompositeTextMapPropagator(propagation.TraceContext{},
    # propagation.Baggage{})) — this is already OTel Python's own default
    # (OTEL_PROPAGATORS defaults to "tracecontext,baggage"), registered
    # explicitly here, the same way the rest of the repo does, rather than
    # left to an implicit default.
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )

    # The measurement in NOTES.md §6 confirmed this instrumentation defaults to
    # the legacy HTTP attribute spelling, so OTEL_SEMCONV_STABILITY_OPT_IN=http
    # is set on the deployment manifest to converge it onto the stable
    # spelling. That applies to both instrumentors below.
    HTTPXClientInstrumentor().instrument()
    # anthropic==1.4.0 depends on httpx2, a distribution separate from
    # httpx. HTTPXClientInstrumentor's _module_name is "httpx", so without
    # this the model's outbound HTTP hop has no CLIENT span at all: the
    # request to api.anthropic.com is only ever represented by pydantic-ai's
    # own "chat" span, which carries no HTTP status, retry or wire-latency
    # detail. httpx2 is present in this venv because anthropic requires it
    # (agents/settlement-agent/requirements.txt pins httpx2==2.12.0), so
    # instrument() cannot raise ModuleNotFoundError here.
    #
    # This instrumentor covers more than the model call: the MCP client's
    # streamable-HTTP transport (`mcp.client.streamable_http` imports
    # `httpx2`, and `streamable_http_client(http_client=...)` is typed
    # `httpx2.AsyncClient | None`) is also built on `httpx2`, so this is what
    # emits the CLIENT span for the payments-mcp hop and injects
    # `traceparent` into it. Removing it as "the anthropic-only instrumentor"
    # would sever the money path's trace, not just drop a model-call span.
    HTTPX2ClientInstrumentor().instrument()

    _instrument_pydantic_ai()


def _metric_reader() -> PeriodicExportingMetricReader:
    """The single reader this service's metrics leave through.

    Split out of `_configure_metrics` so a test can build one under a
    controlled environment. The reader `configure()` installs is constructed at
    import, before any test can set `OTEL_EXPORTER_OTLP_ENDPOINT`, so reading
    its endpoint back could only ever show the ambient default -- see
    `tests/test_telemetry.py::
    test_the_metrics_endpoint_comes_from_the_standard_otel_env_var`.
    """
    return PeriodicExportingMetricReader(OTLPMetricExporter())


def _configure_metrics(resource: Resource) -> None:
    """Install a MeterProvider. Endpoint and protocol come from the standard
    `OTEL_EXPORTER_OTLP_*` variables, as for traces, so the deployment manifest
    stays the only place either is named.

    `resource` is the TracerProvider's own, passed in rather than rebuilt: a
    metric whose `service.name` disagreed with the spans from the same process
    would not join to them, and joining metrics to their spans depends on that
    agreement.

    **Without this, `metrics.get_meter_provider()` returns the API's
    `_ProxyMeterProvider`** -- an object that forwards to a real provider if one
    is ever set and silently discards every measurement if one is not.
    PydanticAI creates its histograms against whatever provider is installed
    when `InstrumentationSettings` is constructed, so with none installed,
    "the framework emits nothing" and "we installed nothing" look identical --
    installing a real provider here is what makes agent-health metrics
    observable at all.

    **Nothing is configured beyond the reader, deliberately.** This demo
    records 100% of its telemetry, metrics included:

      * No `views=`. A `View` carrying `DropAggregation` is the metrics-side
        tail sampler, and one narrowing `attribute_keys` discards series just
        as effectively. `tests/test_telemetry.py::test_no_view_drops_an_instrument`
        asserts the absence rather than trusting it, the argument
        `tests/unit/test_no_sampling.py` already makes for traces.
      * No temporality preference. Measured on the installed
        `opentelemetry-exporter-otlp-proto-http==1.44.0`:
        `OTLPMetricExporter()._preferred_temporality` is
        `AggregationTemporality.CUMULATIVE` (`2`) for all six instrument kinds.
        Cumulative is also what Prometheus's OTLP receiver stores directly, so
        preferring DELTA would hand it increments it has to re-total.
      * Export interval left at the SDK default, 60 000 ms
        (`OTEL_METRIC_EXPORT_INTERVAL`; read off a constructed reader,
        `_export_interval_millis == 60000.0`). That is a flush cadence, not a
        filter -- measurements between flushes are aggregated, never dropped.
    """
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[_metric_reader()])
    )


def capture_content_enabled() -> bool:
    """Whether to record prompts, completions and tool arguments in spans.

    Defaults to ON. Redacting is not a free safety win: with content off, a
    span still proves *that* a payment was executed and by which tool, but not
    why that amount or on what stated grounds. The trade-off is discussed in
    docs/telemetry-contract.md's "Content capture" section.
    """
    raw = os.environ.get("NORTHSTAR_CAPTURE_CONTENT")
    if raw is None:
        return True
    return raw.strip().lower() not in {"false", "0", "no", "off"}


def _instrument_pydantic_ai() -> None:
    """Turn on PydanticAI's own OTel instrumentation.

    Verified against pydantic-ai-slim 2.42.0:
      * `Agent(..., instrument=True)` does NOT exist at this version — it
        raises TypeError. `Agent.instrument_all()` is a staticmethod setting a
        process-wide default, which is what we want: one call, every agent.
      * `include_content` gates whether prompts, completions and tool
        arguments/results are recorded, or only message *shape* (role,
        parts[].type). This is controlled by `NORTHSTAR_CAPTURE_CONTENT`,
        defaulting to ON — see `capture_content_enabled()` and
        docs/telemetry-contract.md's "Content capture" section for the
        trade-off.
      * `include_model_request_parameters` is left at its library default
        (`True`), deliberately — it carries the agent's static system
        instructions and tool JSON schemas, which contain no claim-specific
        data and are an audit asset, not a redaction target.
      * `version` is Literal[2,3,4,5,6], default 5. Pinned explicitly so a
        library default change cannot silently reshape our spans.
    """
    from pydantic_ai import Agent
    from pydantic_ai.models.instrumented import InstrumentationSettings

    Agent.instrument_all(
        InstrumentationSettings(include_content=capture_content_enabled(), version=5)
    )


configure()
