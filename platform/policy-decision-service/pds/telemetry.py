"""The `northstar.policy.decision` span.

`docs/telemetry-contract.md` records this as Gap 3: no OpenTelemetry semantic
convention exists for a policy or autonomy decision, so these names are ours to
define. They are prefixed `northstar.policy.` to make that provenance
obvious next to the `gen_ai.*` and `mcp.*` attributes emitted by frameworks --
a reader must be able to tell at a glance which attributes we invented.

Everything else here is the real OTel SDK doing the real work. We define
attribute *names*; we do not hand-roll span plumbing, and we invent nothing for
agentgateway, which emits its own `Guardrail` CLIENT span for the call that
reaches us.
"""
from __future__ import annotations

import os

from opentelemetry import metrics, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.semconv._incubating.attributes.feature_flag_attributes import (
    FEATURE_FLAG_VERSION,
)

SPAN_NAME = "northstar.policy.decision"

# Attribute keys. The registry (telemetry/registry/policy.yaml) is the contract;
# these constants are the code's spelling of it.
ATTR_DECISION = "northstar.policy.decision"           # "permit" | "deny"
ATTR_RULE = "northstar.policy.rule"                   # the rule that fired
ATTR_CALLER = "northstar.policy.caller.spiffe_id"     # the attested caller
ATTR_TOOL = "northstar.policy.tool.name"              # the MCP tool requested
ATTR_AMOUNT = "northstar.policy.request.amount"       # what was asked for
ATTR_CURRENCY = "northstar.policy.request.currency"
ATTR_LIMIT = "northstar.policy.limit.amount"          # what the rule allows
ATTR_AUTONOMY = "northstar.policy.autonomy.level"     # resolved from flagd, not a constant
# The OpenFeature `reason` the level came back with (`TARGETING_MATCH`,
# `STATIC`, `DEFAULT`, `ERROR`, ...) -- alongside ATTR_AUTONOMY so a reader
# can tell "autonomous because the contract says so" from "assisted because
# we could not ask" without cross-referencing a second signal.
ATTR_AUTONOMY_REASON = "northstar.policy.autonomy.reason"
ATTR_RETRYABLE = "northstar.policy.retryable"
# What payments-api showed for the payment an `execute_payment` asked about: set
# on the human approver's decision (which needs `awaiting_approval`) AND on the
# settlement agent's (which needs `proposed`). `unreadable` when the ledger read
# failed. Not set on a tool that reads no ledger.
ATTR_LEDGER_STATUS = "northstar.policy.ledger.status"

# The one attribute on this span that is NOT ours to name. Unlike
# ATTR_AUTONOMY and its `northstar.policy.*` siblings -- which
# Gap 3 (module docstring) documents as invented because no convention exists
# for a policy decision itself -- a feature flag's ruleset version already
# has one: `feature_flag.version`, read from
# `opentelemetry.semconv._incubating.attributes.feature_flag_attributes`
# (still incubating in opentelemetry-semantic-conventions==0.65b0, not yet
# in the stable `opentelemetry.semconv.attributes` namespace -- checked by
# import, not assumed) rather than retyped as a string literal. The
# `feature_flag.evaluation` span event `TracingHook` writes does not carry it,
# so it goes on the decision span.
ATTR_FLAG_VERSION = FEATURE_FLAG_VERSION

_TRACER_NAME = "northstar.policy-decision-service"


def configure() -> None:
    """Install the SDK. Endpoint and protocol come from the standard
    `OTEL_EXPORTER_OTLP_*` environment variables, as in every other service
    here, so the deployment manifest stays the only place they are named.
    """
    service_name = os.environ.get("OTEL_SERVICE_NAME", "policy-decision-service")
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # This service installs no instrumentation library, so nothing here
    # resolves a meter before this line the way the httpx-instrumented
    # services do -- it is placed beside the TracerProvider anyway so all five
    # Python services read the same top to bottom.
    _configure_metrics(resource)

    # W3C trace-context + baggage, explicitly, matching the other four Python
    # services. This registration is what `context_from_headers` below reads
    # `traceparent` with, so it is load-bearing rather than decorative: without
    # it the decision span would start a new trace of its own.
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )


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

    **Why this service gets one when it authors no metric.** Gap 3 is closed by
    a *span*, not a counter, and nothing here records a measurement today. The
    provider exists so that stays a statement about this service rather than
    about its wiring -- and because §6 explicitly leaves the door open for our
    own components to author metrics "on the same terms their spans are
    authored under", which needs a provider to author them into. None is
    authored here: a decision counter would be derivable from the
    `northstar.policy.decision` spans, and inventing one
    to make a dashboard look fuller is exactly what §6 forbids.
    """
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[_metric_reader()])
    )


def tracer() -> trace.Tracer:
    """Resolved per call, not bound at import, so `configure()` may run after
    this module is imported (and so tests can install their own provider)."""
    return trace.get_tracer(_TRACER_NAME)


def context_from_headers(headers) -> Context:
    """The caller's trace context, extracted from the ExtMCP `headers`.

    `traceparent` is forwarded to the guardrail hook (measured: ten headers,
    `traceparent` among them), so the decision span can be a genuine child of
    the gateway's `tools/call` SERVER span rather than the root of an orphan
    trace.

    `McpHeader.value` is `bytes` because HTTP header values are not guaranteed
    to be UTF-8. `traceparent` is ASCII, so a value that will not decode cannot
    be one; `errors="replace"` turns that into a carrier the propagator simply
    fails to parse, which yields an empty context and a root span -- never an
    exception on the decision path.
    """
    carrier = {}
    for header in headers or ():
        key = header.key.lower()
        # Multi-value headers arrive as repeated entries. W3C allows exactly one
        # traceparent; on more than one the first wins, which is what a dict
        # built with setdefault gives us.
        carrier.setdefault(key, header.value.decode("utf-8", errors="replace"))
    return extract(carrier)
