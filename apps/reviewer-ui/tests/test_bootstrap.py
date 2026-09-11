"""bootstrap.py's one departure from every other service's OTel setup:
`_Level1CompatibleTraceContextPropagator`.

**What this pins, and why it is not decoration.** M3's final-review fix round
(R36) moved reviewer-ui's ledger reads inside owned spans so their outbound
CLIENT spans would have a parent -- and the reads *still* landed in traces of
their own, because payments-api discarded the `traceparent` reviewer-ui was
by then correctly injecting. Two pins, one release apart across the W3C Trace
Context Level 1/Level 2 boundary:

  * `opentelemetry-sdk==1.44.0` sets `TraceFlags.SAMPLED | RANDOM_TRACE_ID`
    (0x01 | 0x02), so the header reads `...-03`;
  * `go.opentelemetry.io/otel v1.41.0`'s traceparent extractor refuses any
    version-00 flag byte above 0x02 (`propagation/trace_context.go`:
    `if version == 0 && (h != "" || opts[0] > 2)`), returning an empty
    SpanContext -- which `otelhttp` turns into a brand-new trace root.

otel-go v1.42.0 widened that to `> 3`, but every release from v1.42.0 on
declares `go 1.25.0` and this repo builds payments-api on `golang:1.24-alpine`
with `GOTOOLCHAIN=local`, so taking the upstream fix is a toolchain bump
rather than a module bump. The workaround therefore lives on this one hop.

These tests exist so that workaround cannot rot silently in either direction:
if someone drops it, the flag byte goes back to `03` and this goes red; if
someone "simplifies" it into something that also rewrites the trace id, span
id or tracestate, that goes red too. The day payments-api reaches otel-go
>= v1.42.0, delete the workaround and this file together.
"""
from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from bootstrap import _Level1CompatibleTraceContextPropagator

# The trace-context propagator every other service in this repo registers, used
# here as the reference the subclass is compared against -- so these tests
# measure the difference rather than restating the header format.
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

PROPAGATOR = _Level1CompatibleTraceContextPropagator()


def _inject_in_a_real_span() -> tuple[dict, trace.SpanContext]:
    """Inject from inside a genuinely recording, sampled span of a real SDK
    TracerProvider -- not a hand-built SpanContext, since the flag byte under
    test is set by the SDK and a hand-built context would let this test pass
    while the real thing regressed."""
    tracer = TracerProvider().get_tracer("test")
    carrier: dict = {}
    with tracer.start_as_current_span("probe") as span:
        PROPAGATOR.inject(carrier)
        return carrier, span.get_span_context()


def test_the_installed_sdk_really_does_set_the_level_2_random_trace_id_bit():
    """The premise, asserted rather than assumed. If a future SDK stops
    setting 0x02, the workaround becomes unnecessary and this test is how that
    is noticed -- it goes red, and the next reader reads the docstring above
    and deletes both."""
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("probe") as span:
        flags = span.get_span_context().trace_flags
    assert flags & trace.TraceFlags.SAMPLED, (
        "the probe span is not sampled, so this file measures nothing"
    )
    assert flags & trace.TraceFlags.RANDOM_TRACE_ID, (
        "the installed opentelemetry-sdk no longer sets the Level 2 "
        "random-trace-id bit, so bootstrap.py's flag mask has nothing left to "
        f"do -- delete it and this file (measured flags: {flags:#04x})"
    )


def test_the_injected_traceparent_carries_only_the_sampling_bit():
    """The property payments-api's otel-go v1.41.0 extractor requires: a
    version-00 flag byte of 0x00 or 0x01, never 0x03."""
    carrier, _ = _inject_in_a_real_span()
    traceparent = carrier["traceparent"]
    version, trace_id, span_id, flags = traceparent.split("-")
    assert version == "00", traceparent
    assert flags == "01", (
        "reviewer-ui is injecting a traceparent payments-api's pinned "
        "otel-go v1.41.0 will DISCARD (it refuses any version-00 flag byte "
        f"above 0x02), so every ledger read would start a trace of its own: "
        f"{traceparent!r}"
    )
    assert len(trace_id) == 32 and len(span_id) == 16, traceparent


def test_nothing_but_the_flag_byte_differs_from_the_upstream_propagator():
    """The mask must be exactly a mask. Trace id and span id are the whole
    point of the header; rewriting either would 'fix' propagation by breaking
    correlation, which is the failure this fix round exists to end."""
    tracer = TracerProvider().get_tracer("test")
    ours: dict = {}
    upstream: dict = {}
    with tracer.start_as_current_span("probe"):
        PROPAGATOR.inject(ours)
        TraceContextTextMapPropagator().inject(upstream)

    our_parts = ours["traceparent"].split("-")
    upstream_parts = upstream["traceparent"].split("-")
    assert our_parts[:3] == upstream_parts[:3], (
        "the masked traceparent disagrees with the upstream propagator on "
        f"something other than the flag byte: {ours} vs {upstream}"
    )
    assert upstream_parts[3] == "03", (
        "the upstream propagator no longer emits 03 here, so this comparison "
        f"is no longer demonstrating the difference: {upstream}"
    )
    assert our_parts[3] == "01", ours
    assert ours.keys() == upstream.keys(), (
        f"the two propagators set different header sets: {ours} vs {upstream}"
    )


def test_injecting_with_no_active_span_writes_nothing():
    """Inherited behaviour, pinned because the override touches this path:
    with no span in context there is nothing to propagate, and a header
    naming the invalid span context would be worse than none."""
    carrier: dict = {}
    PROPAGATOR.inject(carrier)
    assert carrier == {}


def test_extraction_is_untouched_and_still_accepts_a_level_2_parent():
    """Only injection is masked. This service must keep accepting whatever a
    caller sends it, including the Level 2 flag byte its own SDK produces --
    the mask is about one downstream's pinned extractor, not about a position
    on the spec."""
    incoming = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-03"
    ctx = PROPAGATOR.extract({"traceparent": incoming})
    span_context = trace.get_current_span(ctx).get_span_context()
    assert span_context.is_valid
    assert trace.format_trace_id(span_context.trace_id) == (
        "4bf92f3577b34da6a3ce929d0e0e4736"
    )
    assert trace.format_span_id(span_context.span_id) == "00f067aa0ba902b7"
    assert span_context.trace_flags & trace.TraceFlags.SAMPLED
