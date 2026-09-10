"""The `policy.decision` span.

`docs/telemetry-contract.md` records this as Gap 3: no OpenTelemetry semantic
convention exists for a policy or autonomy decision, so these names are ours to
define. They are prefixed `policy.` and `northstar.` to make that provenance
obvious next to the `gen_ai.*` and `mcp.*` attributes emitted by frameworks --
a reader must be able to tell at a glance which attributes we invented.

Everything else here is the real OTel SDK doing the real work. We define
attribute *names*; we do not hand-roll span plumbing, and we invent nothing for
agentgateway, which emits its own `Guardrail` CLIENT span for the call that
reaches us (Task 1, question 6).
"""
from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

SPAN_NAME = "policy.decision"

# Attribute keys, defined here so the guard test and the contract cite one source.
ATTR_DECISION = "policy.decision"           # "permit" | "deny"
ATTR_RULE = "policy.rule"                   # the rule that fired
ATTR_CALLER = "policy.caller.spiffe_id"     # the attested caller
ATTR_TOOL = "policy.tool.name"              # the MCP tool requested
ATTR_AMOUNT = "policy.request.amount"       # what was asked for
ATTR_CURRENCY = "policy.request.currency"
ATTR_LIMIT = "policy.limit.amount"          # what the rule allows
ATTR_AUTONOMY = "policy.autonomy.level"     # the constant M5 turns into a flag
ATTR_RETRYABLE = "policy.decision.retryable"

# Every attribute this service sets, so a guard test can assert the set rather
# than re-listing it and drifting.
ATTRIBUTES = (
    ATTR_DECISION, ATTR_RULE, ATTR_CALLER, ATTR_TOOL, ATTR_AMOUNT,
    ATTR_CURRENCY, ATTR_LIMIT, ATTR_AUTONOMY, ATTR_RETRYABLE,
)

_TRACER_NAME = "northstar.policy-decision-service"


def configure() -> None:
    """Install the SDK. Endpoint and protocol come from the standard
    `OTEL_EXPORTER_OTLP_*` environment variables, as in every other service
    here, so the deployment manifest stays the only place they are named.
    """
    service_name = os.environ.get("OTEL_SERVICE_NAME", "policy-decision-service")
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # W3C trace-context + baggage, explicitly, matching the other three Python
    # services. This registration is what `context_from_headers` below reads
    # `traceparent` with, so it is load-bearing rather than decorative: without
    # it the decision span would start a new trace of its own.
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )


def tracer() -> trace.Tracer:
    """Resolved per call, not bound at import, so `configure()` may run after
    this module is imported (and so tests can install their own provider)."""
    return trace.get_tracer(_TRACER_NAME)


def context_from_headers(headers) -> Context:
    """The caller's trace context, extracted from the ExtMCP `headers`.

    Task 1 measured that `traceparent` *is* forwarded to the guardrail hook
    (run C: ten headers, `traceparent` among them), so the decision span can be
    a genuine child of the gateway's `tools/call` SERVER span rather than the
    root of an orphan trace. Exit criterion 3 depends on that.

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
