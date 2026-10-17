"""Emitting the five scores as OTel spans, through the scoring SDK.

**Written against the measured signature, not an assumed one.** An earlier
record of the signature was
`score(name, value, trace_id, span_id, label, explanation)`. Read against the
package as actually published -- `opensearch-genai-observability-sdk-py`
0.2.8, `opensearch_genai_observability_sdk_py/score.py`, downloaded from PyPI
and unpacked on 2026-09-13 -- the real signature is:

    score(name, value=None, *, trace_id=None, span_id=None, label=None,
          explanation=None, response_id=None, attributes=None) -> None

The names and the order of the six that record lists are right; what it gets
wrong is that **only `name` and `value` are positional**. Everything after
them is keyword-only, and there are two further parameters it does not
mention. Calling it the way that record reads -- `score(n, v, tid, sid, lbl,
exp)` -- is a `TypeError`, not a subtly different span. This module is
written against the measured signature, and `tests/test_emit.py` asserts that
signature with `inspect.signature` so a future bump that moves it reddens
here rather than in production.

**What the SDK actually emits**, read from the same source rather than assumed:

  * one span named `evaluation <name>`, from a tracer named
    `opensearch-genai-observability-sdk-py-scores`;
  * span attributes `gen_ai.operation.name="evaluation"`,
    `gen_ai.evaluation.name`, `gen_ai.evaluation.score.value`,
    `gen_ai.evaluation.score.label`, `gen_ai.evaluation.explanation`, plus
    whatever `attributes=` carries, used as-is with no prefix added;
  * one span event, `gen_ai.evaluation.result`, carrying the same four
    evaluation keys, per the OTel GenAI events convention;
  * `explanation` **truncated to 500 characters**, silently. That is why the
    explanations in `scorer/scores.py` put the count and the identity first
    and the enumeration last: what survives a truncation is the part that
    says how badly.

It emits through `opentelemetry.trace.get_tracer(...)`, i.e. through whatever
TracerProvider is globally installed. So it rides this repo's existing OTLP
pipeline with no exporter of its own -- the property this service depends on,
because it is what makes the scores land in the same index as the spans they
judge, and reach the optional Dash0 export at no extra cost.

**Why every score is attached span-level rather than trace-level.** Passing a
`trace_id` with no `span_id` is the SDK's "trace-level" mode, and
`_build_parent_context` then derives the parent span id from *the lower 64
bits of the trace id* -- a convention no span in this system follows. The
score span would be parented to an id that does not exist, and would render as
an orphan in the waterfall. `Trajectory.anchor()` supplies a real, measured
span id instead.
"""
from __future__ import annotations

import logging
import os

from opensearch_genai_observability_sdk_py import score as sdk_score
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from scorer.scores import Score
from scorer.trajectory import ATTR_CLAIM_ID, Trajectory

logger = logging.getLogger(__name__)

_HEX = frozenset("0123456789abcdefABCDEF")


class UnattachableScore(ValueError):
    """The anchor's ids are not W3C ids, so a score span cannot join its trace.

    **Found by measurement, not by reading the source.** A unit test handed
    `score()` a readable placeholder span id and asserted the resulting span
    joined the evaluated trace. It did not: `score()` runs the id through
    `_internal.parse_hex`, and on failure logs
    `score(): invalid span_id '...', emitting standalone span` at WARNING and
    emits the span **with no trace context at all**. The call returns `None`
    either way, so a caller that does not check gets five score spans floating
    in their own traces, unreachable from the evidence they judge, with one
    log line as the only sign.

    That is the worst possible failure for this service -- the scores would
    still be in OpenSearch, still queryable, still say "compliant", and would
    no longer be attached to anything. So the ids are checked here before the
    SDK gets them, and an unparseable one is raised rather than warned about.
    """


def _is_w3c_id(value: str, length: int) -> bool:
    """A W3C trace/span id: exactly `length` hex characters, not all zero.

    Length is checked as well as the alphabet because `parse_hex` would
    happily accept `"ab"` as a span id and produce a span context this system
    has no span for -- valid to the SDK, attached to nothing here.
    """
    return (
        len(value) == length
        and all(character in _HEX for character in value)
        and int(value, 16) != 0
    )


def configure() -> None:
    """Install the SDK. Endpoint and protocol come from the standard
    `OTEL_EXPORTER_OTLP_*` environment variables, as in every other service
    here, so the deployment manifest stays the only place they are named.

    No MeterProvider, unlike the five services `tests/unit/
    test_metrics_wiring.py` enumerates. This service authors no metric: what
    it produces is a judgment per settlement, which is a span with a value on
    it, and a counter derived from those spans would be redundant with them.
    This repo's rule -- do not author a metric to make a dashboard look
    fuller -- applies here exactly as it applies to the PDS.

    No propagator registration either, for a reason worth naming rather than
    leaving as an omission: this service never continues an inbound trace. The
    score spans are deliberately parented to the *evaluated* trace, by trace
    id and span id read out of OpenSearch, not to whatever HTTP request asked
    for the scoring. A score that landed in the caller's trace would be a
    record of the audit having run, not of what it found.
    """
    service_name = os.environ.get("OTEL_SERVICE_NAME", "eval-scorer")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


def emit(trajectory: Trajectory, scores: dict[str, Score]) -> str | None:
    """Emit one score span per score, attached to the trajectory's anchor.

    Returns the span id the scores were attached to, or `None` when the
    trajectory held no span to attach to -- in which case nothing is emitted,
    because a score span with no parent is a judgment nobody can trace back to
    the thing it judged.
    """
    anchor = trajectory.anchor()
    if anchor is None:
        logger.warning(
            "claim %s: no spans, so no score span was emitted", trajectory.claim_id
        )
        return None

    if not _is_w3c_id(anchor.trace_id, 32) or not _is_w3c_id(anchor.span_id, 16):
        raise UnattachableScore(
            f"claim {trajectory.claim_id}: the anchor span carries ids the "
            f"SDK cannot parse (traceId={anchor.trace_id!r}, "
            f"spanId={anchor.span_id!r}). Emitting anyway would produce five "
            "standalone score spans in traces of their own, unreachable from "
            "the evidence they judge -- see UnattachableScore."
        )

    for name, result in scores.items():
        sdk_score(
            name,
            result.value,
            trace_id=anchor.trace_id,
            span_id=anchor.span_id,
            label=result.label,
            explanation=result.explanation,
            # Carried through verbatim by the SDK, with no prefix added
            # (`score.py`: "Keys are used as-is"). The claim id is put back on
            # deliberately: it is the key an operator has, and it is the one
            # thing Gap 4 keeps off every `northstar.policy.decision` span these scores
            # were computed from -- so a score span is findable by the filter
            # the spans it judges are not.
            attributes={ATTR_CLAIM_ID: trajectory.claim_id},
        )
    return anchor.span_id


def flush(timeout_millis: int = 10_000) -> None:
    """Push the score spans out before the request returns.

    A `BatchSpanProcessor` would otherwise hold them for up to its schedule
    delay, and this service's whole e2e contract is "ask for a score, then
    find it in the index" -- a caller that polls for a span still sitting in a
    batch queue cannot tell that from a span that was never emitted.
    """
    provider = trace.get_tracer_provider()
    force_flush = getattr(provider, "force_flush", None)
    if force_flush is not None:
        force_flush(timeout_millis)
