"""OpenTelemetry bootstrap for settlement-agent.

Imported first in main.py. The a2a-sdk telemetry extra instruments the A2A
server's own internal call graph (see NOTES.md); nothing here creates A2A
spans by hand. The httpx instrumentors are added here (not in the brief's
original sketch) because settlement-agent's MCP client
(`mcp.client.streamable_http`) is httpx-based under the hood, and A13
requires measuring this service's own outbound HTTP spans rather than
assuming payments-mcp's spelling carries over. Since the `mcp` 2.x bump
(2026-09-11) that client is built on `httpx2` rather than `httpx`, so it is
`HTTPX2ClientInstrumentor` that covers the payments-mcp hop; see the comment
on that call below.

A18: a2a-sdk 0.3.7's own telemetry does not extract inbound W3C trace context
(verified empirically — see NOTES.md); StarletteInstrumentor (applied to the
built app in main.py, since the a2a-sdk's Starlette instance doesn't exist
until then) is what does that, using the global propagator configured below.
"""
from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import (
    HTTPX2ClientInstrumentor,
    HTTPXClientInstrumentor,
)
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.baggage.propagation import W3CBaggagePropagator

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

    service_name = os.environ.get("OTEL_SERVICE_NAME", "settlement-agent")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(ClaimIdSpanProcessor())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # W3C trace-context + baggage, explicitly, matching payments-api's Go
    # side (systems/payments-api/telemetry.go:
    # propagation.NewCompositeTextMapPropagator(propagation.TraceContext{},
    # propagation.Baggage{})) — this is already OTel Python's own default
    # (OTEL_PROPAGATORS defaults to "tracecontext,baggage"), but A18 asks for
    # it registered explicitly, the same way the rest of the repo does, not
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
    # **2026-09-11 (M3 remaining-bumps, step 1): this line stopped being only
    # about the model call.** This file's own header used to say the MCP client
    # "is httpx-based under the hood", and that was true of `mcp==1.28.1`.
    # `mcp==2.2.0`'s streamable-HTTP client is built on `httpx2`
    # (`mcp.client.streamable_http` imports `httpx2`, and
    # `streamable_http_client(http_client=...)` is typed
    # `httpx2.AsyncClient | None`), so THIS instrumentor is now what emits the
    # CLIENT span for the payments-mcp hop and injects `traceparent` into it.
    # Deleting it as "the anthropic one" would sever the money path's trace,
    # not just lose a model-call span.
    HTTPX2ClientInstrumentor().instrument()

    _instrument_pydantic_ai()


def capture_content_enabled() -> bool:
    """Whether to record prompts, completions and tool arguments in spans.

    Defaults to ON. Redacting is not a free safety win: with content off, a
    span still proves *that* a payment was executed and by which tool, but not
    why that amount or on what stated grounds. The trade-off is the subject of
    OQ-1 in docs/telemetry-contract.md.
    """
    raw = os.environ.get("NORTHSTAR_CAPTURE_CONTENT")
    if raw is None:
        return True
    return raw.strip().lower() not in {"false", "0", "no", "off"}


def _instrument_pydantic_ai() -> None:
    """Turn on PydanticAI's own OTel instrumentation.

    Verified against pydantic-ai-slim 2.42.0 (2.41.0 until 2026-09-11; every
    bullet below was re-checked against the installed 2.42.0 at the bump, and
    all four still hold):
      * `Agent(..., instrument=True)` does NOT exist at this version — it
        raises TypeError. `Agent.instrument_all()` is a staticmethod setting a
        process-wide default, which is what we want: one call, every agent.
      * `include_content` gates whether prompts, completions and tool
        arguments/results are recorded, or only message *shape* (role,
        parts[].type). This is now `NORTHSTAR_CAPTURE_CONTENT`, defaulting to
        ON — see `capture_content_enabled()` and
        docs/telemetry-contract.md's "Content capture" section for why the
        earlier hardcoded `False` was reversed.
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
