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

**`GET /healthz` produces no spans. `GET /` produces one, and this paragraph
used to deny it.** It read "`GET /` and `GET /healthz` therefore produce no
spans at all -- neither is owned telemetry this service has anything to say
about", and then said two clauses later that the outbound payments-api calls
get CLIENT spans -- which `GET /` makes one of. It was self-contradictory
when written and is corrected here rather than quietly reworded: at the M3
final-review fix round (controller ruling R36) `index()` gained an explicit
`review.queue` parent span, precisely because that CLIENT span had no parent
and was rooting a trace of its own. `GET /healthz` is unchanged and really
does produce nothing -- it makes no outbound call and owns no domain event.

HTTPXClientInstrumentor is still applied: it is what emits CLIENT spans for
the outbound calls ledger.py and approval.py make (to payments-api and to
senior-settlement-agent respectively), and -- the property that actually
matters for the demo -- it is what injects the `traceparent` header on every
one of them while one of this service's own spans is current, which is how
the senior agent's inbound server span, and payments-api's own server spans
for both ledger reads, end up in `review.decision`'s / `review.queue`'s trace
rather than starting unrelated ones.

**The trace-flags mask below is the second half of R36's fix, and it was not
in the finding. Both halves were needed; neither alone works.** See
`_Level1CompatibleTraceContextPropagator`.
"""
from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators import textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


class _Level1CompatibleTraceContextPropagator(TraceContextTextMapPropagator):
    """W3C trace-context, injected with the sampling bit only.

    **Why this exists at all -- measured on the live cluster, then confirmed
    in both upstreams' source.** With `review.queue`/`review.decision` in
    place as parents (the other half of R36's fix), reviewer-ui's outbound
    `traceparent` was still being *discarded* by payments-api, which went on
    minting a fresh trace root for every ledger read:

      * `opentelemetry-sdk==1.44.0` (this service's pin) sets **two** flag
        bits, not one -- `TraceFlags.SAMPLED` (0x01) and
        `TraceFlags.RANDOM_TRACE_ID` (0x02), the latter being W3C Trace
        Context **Level 2**'s new "the trace id was generated randomly" bit
        (`opentelemetry/trace/span.py`). So the header it emits reads
        `00-<trace>-<span>-03`, measured directly off a local repro of
        `ledger.py`'s own client against a capturing HTTP server.
      * `systems/payments-api` pins `go.opentelemetry.io/otel v1.41.0`, whose
        `propagation/trace_context.go` extractor refuses any version-00
        traceparent whose flag byte exceeds 0x02:
        `if version == 0 && (h != "" || opts[0] > 2) { return
        trace.SpanContext{} }`. An empty SpanContext means "no parent", so
        `otelhttp` starts a new root. Measured end to end through the
        repo-managed port-forward: a hand-injected `...-01` produced a
        correctly parented `GET /payments` span; the identical request with
        `...-03` produced no span in that trace id at all.

    **Upstream has already fixed this -- on the Go side, in a release this
    repo cannot take yet.** `otel-go` v1.42.0 widened exactly that line to
    `opts[0] > 3` (verified by reading v1.42.0, v1.44.0, v1.45.0 and v1.46.0
    in the local module cache; v1.41.0 is the last with `> 2`). Every one of
    those releases declares `go 1.25.0`, and this repo builds payments-api on
    `golang:1.24-alpine` with `GOTOOLCHAIN=local` against a host toolchain of
    go1.24.10 -- so adopting the fix is a Go *toolchain* bump, not a module
    bump, which is a machine-level change well outside this fix round.

    **So the workaround lives here, on the one hop that needs it**, and it is
    a workaround rather than a disagreement with either upstream: both are
    right, and the two pins are simply one minor release apart across the
    Level 1/Level 2 boundary. Masking to `TraceFlags.SAMPLED` emits a
    perfectly valid Level 1 traceparent that every participant in this system
    accepts, and loses only the advisory "this trace id is random" bit, which
    nothing here reads. Trace id, span id and tracestate are untouched, and
    nothing about the *local* span context changes -- this masks the wire
    format only.

    **Remove this the moment payments-api is on `otel-go` >= v1.42.0.** That
    is the whole exit condition; `tests/test_bootstrap.py` pins the behaviour
    so a silent regression is impossible, and the telemetry contract records
    the measurement.

    Implemented by delegating to the upstream propagator with a masked
    `SpanContext` rather than by re-formatting the header here, so the wire
    format stays upstream's and only the flag byte is ours.
    """

    def inject(
        self,
        carrier: textmap.CarrierT,
        context: Context | None = None,
        setter: textmap.Setter[textmap.CarrierT] = textmap.default_setter,
    ) -> None:
        span_context = trace.get_current_span(context).get_span_context()
        if span_context != trace.INVALID_SPAN_CONTEXT:
            masked = trace.TraceFlags(
                span_context.trace_flags & trace.TraceFlags.SAMPLED
            )
            if masked != span_context.trace_flags:
                context = trace.set_span_in_context(
                    trace.NonRecordingSpan(
                        trace.SpanContext(
                            trace_id=span_context.trace_id,
                            span_id=span_context.span_id,
                            is_remote=span_context.is_remote,
                            trace_flags=masked,
                            trace_state=span_context.trace_state,
                        )
                    ),
                    context,
                )
        super().inject(carrier, context=context, setter=setter)


def configure() -> None:
    service_name = os.environ.get("OTEL_SERVICE_NAME", "reviewer-ui")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # W3C trace-context + baggage, explicitly, matching every other service
    # in this repo (already OTel Python's own default; registered explicitly
    # rather than left implicit, per this repo's own convention -- see
    # agents/settlement-agent/bootstrap.py's identical comment).
    #
    # The ONE departure from every other service's copy of this block, and the
    # only one: the trace-context half is this module's Level-1-compatible
    # subclass rather than `TraceContextTextMapPropagator` itself. Extraction
    # is entirely unchanged (inherited); only the injected flag byte is
    # masked, and only because payments-api's pinned `otel-go` v1.41.0 drops
    # a traceparent carrying OTel Python 1.44's Level 2 random-trace-id bit.
    # The full measurement, both upstream citations and the removal condition
    # are in that class's docstring.
    set_global_textmap(
        CompositePropagator(
            [_Level1CompatibleTraceContextPropagator(), W3CBaggagePropagator()]
        )
    )

    # Emits CLIENT spans for every outbound httpx call this process makes
    # (ledger.py's calls to payments-api, approval.py's A2A call to
    # senior-settlement-agent) and injects trace context into those outbound
    # headers.
    HTTPXClientInstrumentor().instrument()


configure()
