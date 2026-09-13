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

**Retired 2026-09-11 (M3): the trace-flags mask that used to live here.**
This paragraph used to read:

> **The trace-flags mask below is the second half of R36's fix, and it was
> not in the finding. Both halves were needed; neither alone works.** See
> `_Level1CompatibleTraceContextPropagator`.

That class was a `TraceContextTextMapPropagator` subclass which masked the
injected flag byte down to `TraceFlags.SAMPLED`. It existed because
`opentelemetry-sdk` 1.44.0 emits W3C Trace Context **Level 2**'s
`RANDOM_TRACE_ID` bit alongside `SAMPLED` -- a `...-03` traceparent -- and
payments-api's then-pinned `go.opentelemetry.io/otel v1.41.0` **discarded**
any version-00 traceparent with a flag byte above `0x02`
(`propagation/trace_context.go:107`: `if version == 0 && (h != "" || opts[0]
> 2)`), so `otelhttp` minted a fresh trace root for every ledger read. The
full account is `docs/upstream-findings.md` Finding 11.

Its removal condition was stated on the class itself -- *"Remove this the
moment payments-api is on otel-go >= v1.42.0"* -- and payments-api is now on
**v1.46.0** (commit `chore(m3): payments-api to otel-go v1.46.0 and Go
1.27.1`), whose same line reads `opts[0] > 3` and whose extractor masks to
`FlagsSampled | FlagsRandom`. Measured on the live cluster the day the mask
came out, two requests to `payments-api` differing only in the flag byte:

  * `00-df5aa13c6d534c0f9c36ef5c187d014d-31966bf1692149b0-01` ->
    `GET /payments` SERVER span `da5830097b2aa97e`, parent `31966bf1692149b0`;
  * `00-9b855ee5e0a442619dc8d48118324644-51665f1dbec74c0b-03` ->
    `GET /payments` SERVER span `ca981a384c75fc22`, parent `51665f1dbec74c0b`.

The second of those produced **no span at all** before the bump. So the
service now registers the stock upstream propagator like every other service
in this repo, and reviewer-ui's outbound header goes out as `...-03`,
natively accepted.
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

    # M4 Task 2, before HTTPXClientInstrumentor below rather than after it:
    # that instrumentor resolves a meter once, inside `_instrument`
    # (`get_meter(...)`, opentelemetry/instrumentation/httpx/__init__.py:775
    # and three sibling call sites), so a provider installed afterwards is
    # reached only via the API proxy's rebinding path. See
    # agents/settlement-agent/bootstrap.py for the full note on that ordering.
    _configure_metrics(resource)

    # W3C trace-context + baggage, explicitly, matching every other service
    # in this repo (already OTel Python's own default; registered explicitly
    # rather than left implicit, per this repo's own convention -- see
    # agents/settlement-agent/bootstrap.py's identical comment).
    #
    # 2026-09-11 (M3): this block used to say "The ONE departure from every
    # other service's copy of this block" and register
    # `_Level1CompatibleTraceContextPropagator()` in the first slot. There is
    # no departure any more -- this is now byte-for-byte the block every other
    # service runs. The module docstring records what the departure was and
    # which bump retired it.
    set_global_textmap(
        CompositePropagator(
            [TraceContextTextMapPropagator(), W3CBaggagePropagator()]
        )
    )

    # Emits CLIENT spans for every outbound httpx call this process makes
    # (ledger.py's calls to payments-api, approval.py's A2A call to
    # senior-settlement-agent) and injects trace context into those outbound
    # headers.
    HTTPXClientInstrumentor().instrument()


def _metric_reader() -> PeriodicExportingMetricReader:
    """The single reader this service's metrics leave through.

    Split out so a test can build one under a controlled environment; see
    `agents/settlement-agent/bootstrap.py`'s copy for why that split is needed.
    """
    return PeriodicExportingMetricReader(OTLPMetricExporter())


def _configure_metrics(resource: Resource) -> None:
    """Install a MeterProvider (M4 Task 2). Endpoint and protocol come from the
    standard `OTEL_EXPORTER_OTLP_*` variables, as for traces.

    Identical in shape to every other Python service here.
    **`agents/settlement-agent/bootstrap.py`'s copy carries the full
    rationale** -- why no `views=`, why no temporality preference, and what the
    export interval does and does not drop. This docstring stays a pointer
    rather than a second copy that can drift from it;
    `tests/unit/test_metrics_wiring.py` asserts the five copies stay identical.

    **Why this service gets one even though it runs no model.** M4 §6 asks
    which GenAI metrics the frameworks emit, and an answer of "none" is only
    worth publishing if it was measured. Without a provider here, reviewer-ui
    emitting nothing would say nothing about any framework -- it would only say
    this process had nowhere to put a measurement. The absence is the finding,
    so the absence has to be real.
    """
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[_metric_reader()])
    )


configure()
