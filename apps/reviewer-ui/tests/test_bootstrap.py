"""bootstrap.py's OTel setup -- and the record of the departure it used to have.

**Retired 2026-09-11 (M3).** Until this date this file pinned
`_Level1CompatibleTraceContextPropagator`, a `TraceContextTextMapPropagator`
subclass in `bootstrap.py` that masked reviewer-ui's *injected* flag byte down
to `TraceFlags.SAMPLED`. Its five tests were:

  * `test_the_installed_sdk_really_does_set_the_level_2_random_trace_id_bit`
  * `test_the_injected_traceparent_carries_only_the_sampling_bit`
  * `test_nothing_but_the_flag_byte_differs_from_the_upstream_propagator`
  * `test_injecting_with_no_active_span_writes_nothing`
  * `test_extraction_is_untouched_and_still_accepts_a_level_2_parent`

and its docstring closed *"The day payments-api reaches otel-go >= v1.42.0,
delete the workaround and this file together."* The first three existed only
to pin the workaround and its premise; they are gone with it. The last two
pinned *inherited* behaviour that the override happened to touch, so with no
override there is nothing left for them to guard that upstream does not guard
itself.

What retired them: `systems/payments-api` moved from `go.opentelemetry.io/otel
v1.41.0` -- whose `propagation/trace_context.go:107` reads `if version == 0 &&
(h != "" || opts[0] > 2)`, discarding a Level 2 `-03` traceparent outright --
to **v1.46.0**, whose same line reads `opts[0] > 3` and which masks the
extracted flags to `FlagsSampled | FlagsRandom`. `docs/upstream-findings.md`
Finding 11 is the full account and stays standing.

The file itself does not go, because the *positive* claim is now worth
pinning and was not pinnable before: reviewer-ui injects a plain,
unmodified, Level 2 `-03` traceparent, exactly like every other Python
service here. If someone reintroduces a mask -- or an SDK downgrade stops
setting `0x02` -- the two tests below say so.

The cross-service half of the proof is not here and cannot be: that
payments-api *accepts* what this service sends is asserted on the live
cluster by `tests/e2e/test_supervised.py`'s criterion 5, which checks real
parentage across the `review.decision` -> reviewer-ui CLIENT `GET` ->
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
    """No subclass, no mask, no departure.

    `type(...) is` rather than `isinstance(...)`: the retired workaround *was*
    a `TraceContextTextMapPropagator` and would sail through an isinstance
    check, so that is precisely the assertion that would not have caught it.
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
        "upstream's rather than upstream's own -- which is the shape the "
        f"retired flag-byte workaround had: {type(trace_context[0])!r}. See "
        "this module's docstring and docs/upstream-findings.md Finding 11 "
        "before adding another one."
    )


def test_the_injected_traceparent_carries_the_level_2_random_trace_id_bit():
    """The thing the workaround used to prevent, asserted as the wanted state.

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
