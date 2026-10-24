"""bootstrap.py's OTel setup: the trace-context propagator it registers.

reviewer-ui registers the stock upstream `TraceContextTextMapPropagator`, with
no flag-byte masking, so it injects a plain, unmodified, Level 2 `-03`
traceparent, exactly like every other Python service here. This matters
because `systems/payments-api`'s pinned `go.opentelemetry.io/otel` version
must accept that flag byte rather than discard the traceparent outright --
see `docs/upstream-findings.md` Finding 11 for the full account. If someone
reintroduces a mask -- or an SDK downgrade stops setting `0x02` -- the two
tests below say so.

The cross-service half of the proof is not here and cannot be: that
payments-api *accepts* what this service sends is asserted on the live
cluster by `tests/e2e/test_supervised.py`'s criterion 5, which checks real
parentage across the `northstar.review.decision` -> reviewer-ui CLIENT `GET` ->
payments-api `GET /payments/{paymentId}` hop.
"""
from __future__ import annotations

from opentelemetry import trace
from opentelemetry.propagate import get_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

# Importing bootstrap runs its module-level `configure()`, which is what
# registers the global propagator this file inspects.
import bootstrap  # noqa: F401


def test_the_registered_trace_context_propagator_is_upstreams_own():
    """No subclass, no mask.

    `type(...) is` rather than `isinstance(...)`: a masking subclass would
    still *be* a `TraceContextTextMapPropagator` and would sail through an
    isinstance check, so that is precisely the assertion that would not catch
    it.
    """
    propagator = get_global_textmap()
    assert isinstance(propagator, CompositePropagator), propagator
    parts = propagator._propagators
    trace_context = [
        p for p in parts if isinstance(p, TraceContextTextMapPropagator)
    ]
    assert len(trace_context) == 1, (
        f"expected exactly one trace-context propagator, found: {parts}"
    )
    assert type(trace_context[0]) is TraceContextTextMapPropagator, (
        "reviewer-ui has a trace-context propagator that is a SUBCLASS of "
        "upstream's rather than upstream's own -- which is the shape a "
        f"flag-byte workaround has: {type(trace_context[0])!r}. See "
        "this module's docstring and docs/upstream-findings.md Finding 11 "
        "before adding another one."
    )


def test_the_injected_traceparent_carries_the_level_2_random_trace_id_bit():
    """Asserts the wanted state directly, not just the absence of a mask.

    Injected from inside a genuinely recording, sampled span of a real SDK
    TracerProvider -- not a hand-built SpanContext, since the flag byte under
    test is set by the SDK and a hand-built context would let this pass while
    the real thing regressed.
    """
    tracer = TracerProvider().get_tracer("test")
    carrier: dict = {}
    with tracer.start_as_current_span("probe") as span:
        get_global_textmap().inject(carrier)
        flags = span.get_span_context().trace_flags

    assert flags & trace.TraceFlags.SAMPLED, (
        "the probe span is not sampled, so this test measures nothing"
    )
    version, trace_id, span_id, wire_flags = carrier["traceparent"].split("-")
    assert version == "00", carrier["traceparent"]
    assert len(trace_id) == 32 and len(span_id) == 16, carrier["traceparent"]
    assert wire_flags == "03", (
        "reviewer-ui's injected traceparent no longer carries W3C Trace "
        "Context Level 2's random-trace-id bit (0x02) alongside SAMPLED. "
        "Either something is masking it again -- see this module's docstring "
        "-- or the installed opentelemetry-sdk stopped setting it: "
        f"{carrier['traceparent']!r}, span flags {flags:#04x}"
    )
