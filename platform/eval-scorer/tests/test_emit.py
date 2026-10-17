"""What the SDK's `score()` really takes, and what this service really emits.

**The first test in this file pins the measured signature against a stale
assumption.** An earlier record described the signature as
`score(name, value, trace_id, span_id, label, explanation)`. The published
package takes only the first two positionally. Calling it the way that
record reads raises `TypeError`, so the record was not a harmless
approximation -- it was a call that does not work. This asserts the measured
signature so a future bump that moves it reddens here rather than in
production, which is the difference between a corrected record and a record
that goes stale again.
"""
from __future__ import annotations

import inspect

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opensearch_genai_observability_sdk_py import score as sdk_score

import fixtures
from scorer import emit
from scorer.scores import scores_for
from scorer.trajectory import ATTR_CLAIM_ID, Trajectory

# The tracer name the SDK resolves its own tracer under, read from
# `opensearch_genai_observability_sdk_py/score.py::_TRACER_NAME` at 0.2.8.
SDK_TRACER_NAME = "opensearch-genai-observability-sdk-py-scores"


def test_the_sdks_score_signature_is_the_measured_one_not_the_m1_record():
    parameters = inspect.signature(sdk_score).parameters
    assert list(parameters) == [
        "name",
        "value",
        "trace_id",
        "span_id",
        "label",
        "explanation",
        "response_id",
        "attributes",
    ], (
        "opensearch-genai-observability-sdk-py's `score()` parameter list has "
        f"changed: {list(parameters)}. scorer/emit.py calls it by keyword and "
        "depends on exactly this list."
    )
    assert parameters["name"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["value"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("trace_id", "span_id", "label", "explanation", "response_id", "attributes"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{name} is no longer keyword-only. name/value/trace_id/span_id/"
            "label/explanation are not all positional at 0.2.8; if that "
            "changes, scorer/emit.py's keyword call needs revisiting."
        )


def test_calling_score_the_way_the_m1_record_reads_is_a_type_error():
    """The record is not a harmless approximation -- it is a call that raises.

    Asserted rather than merely stated, because "the signature differs
    slightly" and "the documented call does not work" warrant different
    responses, and only the second justifies editing a shipped spec.
    """
    with pytest.raises(TypeError):
        sdk_score("n", 1.0, "trace", "span", "label", "explanation")


def test_the_sdk_truncates_an_explanation_at_500_characters(score_spans):
    """Measured from the source and asserted through a real span.

    This is why `scorer/scores.py`'s explanations put the count and the
    identity first and the enumeration last: what survives a truncation has to
    be the part that says how badly.
    """
    sdk_score(
        "truncation_probe",
        0.0,
        trace_id="598983e349a54b64bd0031d10b206bae",
        span_id="rootspan00000001",
        explanation="x" * 900,
    )
    span = score_spans.get_finished_spans()[-1]
    assert len(span.attributes["gen_ai.evaluation.explanation"]) == 500


@pytest.fixture(scope="session")
def score_spans():
    """A global TracerProvider writing into memory, installed exactly once.

    `trace.set_tracer_provider` is once-per-process -- a second call logs a
    warning and is ignored -- and the SDK's `score()` resolves its tracer off
    whatever is globally installed. So this is session-scoped and
    `emit.configure()` is deliberately never called anywhere in this suite:
    doing so would install an OTLP exporter and there would be no way to take
    it back out.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "eval-scorer"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


@pytest.fixture(autouse=True)
def _clear(score_spans):
    score_spans.clear()
    yield


def test_emit_produces_one_span_per_score_parented_to_the_anchor(score_spans):
    trajectory = fixtures.supervised()
    scores = scores_for(trajectory)
    anchor_span_id = emit.emit(trajectory, scores)

    anchor = trajectory.anchor()
    assert anchor is not None and anchor_span_id == anchor.span_id

    spans = score_spans.get_finished_spans()
    assert len(spans) == len(scores) == 5
    for span in spans:
        context = span.get_span_context()
        assert format(context.trace_id, "032x") == anchor.trace_id, (
            "a score span must join the trace it judges, or it is a judgment "
            "nobody can find beside the evidence"
        )
        assert span.parent is not None
        assert format(span.parent.span_id, "016x") == anchor.span_id


def test_every_score_lands_with_its_value_label_and_explanation(score_spans):
    trajectory = fixtures.retry_after_refusal()
    scores = scores_for(trajectory)
    emit.emit(trajectory, scores)

    by_name = {
        span.attributes["gen_ai.evaluation.name"]: span
        for span in score_spans.get_finished_spans()
    }
    assert set(by_name) == set(scores)
    for name, result in scores.items():
        span = by_name[name]
        assert span.name == f"evaluation {name}"
        assert span.attributes["gen_ai.operation.name"] == "evaluation"
        assert span.attributes["gen_ai.evaluation.score.value"] == result.value
        assert span.attributes["gen_ai.evaluation.score.label"] == result.label
        assert (
            span.attributes["gen_ai.evaluation.explanation"]
            == result.explanation[:500]
        )
        # The claim id is put back on deliberately: Gap 4 keeps it off every
        # `northstar.policy.decision` span these scores were computed from, so without
        # this the scores would be unfindable by the one key an operator has.
        assert span.attributes[ATTR_CLAIM_ID] == trajectory.claim_id

    violation = by_name["stopped_when_refused"]
    assert violation.attributes["gen_ai.evaluation.score.value"] == 0.0
    assert violation.attributes["gen_ai.evaluation.score.label"] == "retry-after-refusal"


def test_every_score_span_also_carries_the_semconv_result_event(score_spans):
    """The SDK emits a `gen_ai.evaluation.result` event alongside the
    attributes, per the OTel GenAI events convention. Asserted because it is
    what a convention-aware backend reads, and because it is the half a
    reader of `_source` in OpenSearch would never notice was missing."""
    emit.emit(fixtures.supervised(), scores_for(fixtures.supervised()))
    for span in score_spans.get_finished_spans():
        events = [e for e in span.events if e.name == "gen_ai.evaluation.result"]
        assert len(events) == 1, span.name
        assert events[0].attributes["gen_ai.evaluation.name"] == (
            span.attributes["gen_ai.evaluation.name"]
        )


def test_an_empty_trajectory_emits_nothing_rather_than_an_orphan(score_spans):
    """A score span with no parent is a judgment nobody can trace back to the
    thing it judged. Nothing is the honest answer."""
    trajectory = Trajectory.from_documents("CLM-NOTHING", [])
    assert emit.emit(trajectory, scores_for(trajectory)) is None
    assert score_spans.get_finished_spans() == ()


def test_an_unparseable_anchor_id_is_refused_rather_than_silently_orphaned(
    score_spans,
):
    """The SDK's own failure mode, closed here. **Found by measurement.**

    `score()` runs each id through `_internal.parse_hex` and, on failure, logs
    `score(): invalid span_id '...', emitting standalone span` at WARNING and
    emits the span with **no trace context at all** -- returning `None` either
    way, so a caller that does not check gets five score spans floating in
    traces of their own, unreachable from the evidence they judge. That is the
    worst possible failure for this service: the scores are still in
    OpenSearch, still queryable, still say "compliant", and are attached to
    nothing.

    The first draft of this suite hit exactly that, with placeholder span ids
    in the fixtures; `scorer/emit.py::_is_w3c_id` is the result.
    """
    from scorer.emit import UnattachableScore

    trajectory = Trajectory.from_documents(
        "CLM-BAD",
        [dict(fixtures.root(0.0), spanId="rootspan00000001")],
    )
    with pytest.raises(UnattachableScore):
        emit.emit(trajectory, scores_for(trajectory))
    assert score_spans.get_finished_spans() == (), (
        "nothing may be emitted once the anchor is known to be unattachable -- "
        "a partial emission would leave some scores in the trace and some "
        "outside it"
    )


@pytest.mark.parametrize(
    "trace_id,span_id",
    [
        # A span id of the wrong length parses as hex perfectly well and names
        # a span that does not exist. Length is checked as well as alphabet.
        ("598983e349a54b64bd0031d10b206bae", "abcd"),
        ("598983", "9007a5da00000001"),
        # All-zero ids are what an invalid SpanContext formats to; they must
        # not be treated as a real parent.
        ("0" * 32, "9007a5da00000001"),
        ("598983e349a54b64bd0031d10b206bae", "0" * 16),
        ("598983e349a54b64bd0031d10b206bae", ""),
    ],
)
def test_ids_that_are_not_w3c_ids_are_refused(score_spans, trace_id, span_id):
    from scorer.emit import UnattachableScore

    trajectory = Trajectory.from_documents(
        "CLM-BAD",
        [dict(fixtures.root(0.0), traceId=trace_id, spanId=span_id)],
    )
    with pytest.raises(UnattachableScore):
        emit.emit(trajectory, scores_for(trajectory))


def test_configure_installs_an_otlp_batch_exporter_without_touching_the_global(
    monkeypatch,
):
    """`configure()` is the one function here that cannot run for real in this
    suite -- it would take the process-wide TracerProvider and there is no way
    to give it back. So its provider is captured instead of installed, and
    what it was built with is asserted on the object itself."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    captured = {}
    monkeypatch.setattr(
        emit.trace, "set_tracer_provider", lambda p: captured.setdefault("provider", p)
    )
    monkeypatch.setenv("OTEL_SERVICE_NAME", "eval-scorer")
    emit.configure()

    provider = captured["provider"]
    assert isinstance(provider, TracerProvider)
    assert provider.resource.attributes["service.name"] == "eval-scorer"
    processors = provider._active_span_processor._span_processors
    assert len(processors) == 1
    assert isinstance(processors[0], BatchSpanProcessor)
    assert isinstance(processors[0].span_exporter, OTLPSpanExporter)
