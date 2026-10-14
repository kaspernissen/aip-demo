"""The trajectory model, exercised against the shapes the index really holds.

`test_a_real_captured_document_parses` is the load-bearing one. Everything
else in this suite runs on `fixtures.py`'s builders, so if those builders
drifted from the real `_source` shape the whole suite could stay green while
the service scored nothing in production. That test pins one document exactly
as OpenSearch returned it.
"""
from __future__ import annotations

import pytest

import fixtures
from scorer.trajectory import (
    DECISION_SPAN,
    Decision,
    Span,
    Trajectory,
    parse_rfc3339_nanos,
)

# Copied verbatim out of `_source` for one `policy.decision` document,
# retrieved from `ss4o_traces-default-namespace` on this cluster 2026-09-13.
# Nothing about it has been tidied -- `data_stream` and the empty
# `traceState`/`instrumentationScope.version` are exactly what came back.
CAPTURED_DOCUMENT = {
    "attributes": {
        "data_stream": {
            "dataset": "default",
            "namespace": "namespace",
            "type": "span",
        },
        "policy.autonomy.level": "autonomous",
        "policy.caller.spiffe_id": (
            "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
        ),
        "policy.decision": "permit",
        "policy.decision_retryable": False,
        "policy.limit.amount": 6000,
        "policy.request.amount": 5800,
        "policy.request.currency": "EUR",
        "policy.rule": "settlement-agent.autonomous.execute_limit",
        "policy.tool.name": "execute_payment",
    },
    "droppedAttributesCount": 0,
    "endTime": "2026-09-13T18:01:42.475287464Z",
    "instrumentationScope": {
        "name": "northstar.policy-decision-service",
        "schemaUrl": "",
        "version": "",
    },
    "kind": "Server",
    "name": "policy.decision",
    "parentSpanId": "c962ce9a2686600c",
    "resource": {
        "service.instance.id": "2e1cd2cd-1df9-4374-8f4f-4171369b87ce",
        "service.name": "policy-decision-service",
        "telemetry.sdk.language": "python",
        "telemetry.sdk.name": "opentelemetry",
        "telemetry.sdk.version": "1.44.0",
    },
    "spanId": "e7ee45db689fab65",
    "startTime": "2026-09-13T18:01:42.475179172Z",
    "status": {"code": "Unset", "message": ""},
    "@timestamp": "0001-01-01T00:00:00Z",
    "traceId": "7722c5abc143f5e56f399997784d8b67",
    "traceState": "",
}


def test_a_real_captured_document_parses():
    """The one test in this file that proves the model matches the index.

    Reads the six fields a scorer depends on off a document nothing in this
    repo shaped. If the collector's OpenSearch exporter ever changes how it
    flattens a span -- nesting `attributes` into objects, say, which is
    exactly how OpenSearch expands them for the *mapping* -- this reddens and
    the rest of this suite does not.
    """
    span = Span.from_document(CAPTURED_DOCUMENT)
    assert span.name == DECISION_SPAN
    assert span.service == "policy-decision-service"
    assert span.trace_id == "7722c5abc143f5e56f399997784d8b67"
    assert span.span_id == "e7ee45db689fab65"

    decision = Decision.from_span(span)
    assert decision.tool == "execute_payment"
    assert decision.decision == "permit"
    assert decision.permitted and not decision.denied
    assert decision.caller.endswith("/sa/settlement-agent")
    assert decision.amount == 5800.0
    assert decision.limit == 6000.0
    assert decision.autonomy == "autonomous"
    assert decision.currency == "EUR"
    assert "7722c5ab" in decision.where() and "e7ee45db" in decision.where()


@pytest.mark.parametrize(
    "value,expected_suffix_nanos",
    [
        # Nine fractional digits: the real format, and the one
        # datetime.fromisoformat refuses.
        ("2026-09-13T18:01:42.475179172Z", 475_179_172),
        # Three: right-padded, not left-padded. `.475` is 475 milliseconds.
        ("2026-09-13T18:01:42.475Z", 475_000_000),
        # None at all.
        ("2026-09-13T18:01:42Z", 0),
        # More than nine: truncated, not rounded or rejected.
        ("2026-09-13T18:01:42.4751791729Z", 475_179_172),
    ],
)
def test_fractional_seconds_are_read_as_nanoseconds(value, expected_suffix_nanos):
    whole = parse_rfc3339_nanos("2026-09-13T18:01:42Z")
    assert parse_rfc3339_nanos(value) - whole == expected_suffix_nanos


def test_a_timezone_offset_is_applied_in_the_right_direction():
    """`+02:00` is two hours AHEAD of UTC, so the same wall clock is an
    EARLIER instant. A sign error here would reorder a trajectory, which is
    what every ordering-based scorer rests on."""
    utc = parse_rfc3339_nanos("2026-09-13T18:01:42Z")
    ahead = parse_rfc3339_nanos("2026-09-13T18:01:42+02:00")
    behind = parse_rfc3339_nanos("2026-09-13T18:01:42-02:00")
    assert utc - ahead == 2 * 3600 * 1_000_000_000
    assert behind - utc == 2 * 3600 * 1_000_000_000


@pytest.mark.parametrize(
    "value", ["", "not a time", "2026-09-13 18:01:42", "2026-09-13T18:01:42"]
)
def test_an_unreadable_start_time_raises_rather_than_sorting_to_zero(value):
    """A span that cannot be placed in an ordering must not be placed at
    position zero. See `parse_rfc3339_nanos`'s own docstring."""
    with pytest.raises(ValueError):
        parse_rfc3339_nanos(value)


def test_spans_are_ordered_by_start_time_not_by_input_order():
    trajectory = Trajectory.from_documents(
        "CLM-X",
        [
            fixtures.decision(4.0, "execute_payment", "deny", amount=6200.0),
            fixtures.decision(2.0, "propose_payment", "permit", amount=6200.0),
        ],
    )
    assert [d.tool for d in trajectory.decisions()] == [
        "propose_payment",
        "execute_payment",
    ]


def test_spans_sharing_a_start_time_are_ordered_deterministically():
    """Measured: this system regularly emits spans inside the same
    millisecond. Two runs of the same query must not order them differently,
    or "proposed before executed" becomes a coin flip."""
    same_instant = [
        fixtures.decision(4.0, "execute_payment", "deny", span_id="bbbb", amount=1.0),
        fixtures.decision(4.0, "propose_payment", "permit", span_id="aaaa", amount=1.0),
    ]
    forwards = Trajectory.from_documents("CLM-X", same_instant)
    backwards = Trajectory.from_documents("CLM-X", list(reversed(same_instant)))
    assert [s.span_id for s in forwards.spans] == [s.span_id for s in backwards.spans]


def test_the_anchor_is_the_root_of_the_earliest_trace():
    trajectory = fixtures.supervised()
    anchor = trajectory.anchor()
    assert anchor is not None
    assert anchor.span_id == "9007a5da00000001"
    assert anchor.trace_id == fixtures.REFUSAL_TRACE


def test_the_anchor_is_the_earliest_span_whose_parent_was_not_retrieved():
    """A partial query can return a trace's middle without its root. The
    scores still have to attach to something real -- a bare trace id would
    make the SDK derive a parent span id that does not exist (see
    `scorer/emit.py`).

    **This test was named `..._falls_back_to_the_earliest_span_when_no_root_is
    _present` and did not reach that fallback**, which a review found by
    mutation: `fixtures.decision()` gives every document the same
    `parentSpanId` (`c962ce9a2686600c`, absent from the set), so `anchor()`
    returns on its *first* branch -- the earliest span whose parent is not
    present -- and `return self.spans[0]` was never executed. The assertion
    passed because the earliest span is the answer either way. Replacing
    `self.spans[0]` with `None` left this test green.

    So it is named for the branch it actually exercises, and the fallback has
    a test of its own below. Both branches now have one, and neither test's
    name describes the other's.
    """
    trajectory = Trajectory.from_documents(
        "CLM-X",
        [
            fixtures.decision(4.0, "execute_payment", "permit", amount=1.0, span_id="0000000000000522"),
            fixtures.decision(2.0, "propose_payment", "permit", amount=1.0, span_id="0000000000000f11"),
        ],
    )
    anchor = trajectory.anchor()
    assert anchor is not None and anchor.span_id == "0000000000000f11"


def test_the_anchor_falls_back_to_the_earliest_span_when_every_parent_is_present():
    """The `return self.spans[0]` branch, genuinely reached.

    Reaching it needs every span's parent to be inside the set, which a
    well-formed acyclic trace can never produce: the topmost span retrieved
    always has a parent outside it. The one shape that does is a document the
    index returned **without a `spanId`** -- `Span.from_document` reads that as
    `""`, `""` joins the present set, and every root span's empty
    `parentSpanId` then looks "present". That is not hypothetical tidiness: it
    is the difference between the anchor being a real span and being `None`,
    and `emit.py` refuses to emit at all when the anchor's ids are not W3C ids
    (`UnattachableScore`), so a `None` here would silently drop a whole score
    set rather than mis-attach it.

    What this pins is that the fallback returns the earliest **real** span, so
    the malformed document cannot become the thing five scores hang from.
    """
    malformed = fixtures.decision(4.0, "execute_payment", "permit", amount=1.0)
    malformed["spanId"] = ""
    malformed["parentSpanId"] = "9007a5da00000001"

    trajectory = Trajectory.from_documents(
        "CLM-X", [fixtures.root(0.0), malformed]
    )
    present = {span.span_id for span in trajectory.spans}
    assert "" in present, (
        "this fixture only reaches the fallback if the malformed document "
        "puts an empty span id into the present set -- if that stops being "
        "true, this test is exercising the first branch again and is the "
        "defect it was written to fix"
    )
    assert all(span.parent_span_id in present for span in trajectory.spans), (
        "every parent must be present for `anchor()` to reach its fallback; "
        "otherwise this test passes on the first branch, which the test above "
        "already covers"
    )

    anchor = trajectory.anchor()
    assert anchor is not None and anchor.span_id == "9007a5da00000001"


def test_an_empty_trajectory_has_no_anchor_and_no_decisions():
    trajectory = Trajectory.from_documents("CLM-X", [])
    assert trajectory.anchor() is None
    assert trajectory.decisions() == ()
    assert trajectory.trace_ids() == ()
    assert trajectory.first_refusal() is None


def test_trace_ids_are_listed_in_the_order_each_trace_first_appears():
    trajectory = fixtures.supervised()
    assert trajectory.trace_ids() == (fixtures.REFUSAL_TRACE, fixtures.APPROVAL_TRACE)


def test_payment_calls_after_a_refusal_exclude_a_different_caller():
    """The compliant supervised story's whole shape: the reviewer-ui executes the
    payment the junior was refused, and that is the milestone's headline, not
    a retry. A scorer that flagged it would mark the designed path
    non-compliant on stage."""
    trajectory = fixtures.supervised()
    refusal = trajectory.first_refusal()
    assert refusal is not None and refusal.caller == fixtures.JUNIOR
    assert trajectory.payment_calls_after(refusal) == ()


def test_payment_calls_after_a_refusal_include_the_same_caller():
    trajectory = fixtures.retry_after_refusal()
    refusal = trajectory.first_refusal()
    assert refusal is not None
    retries = trajectory.payment_calls_after(refusal)
    assert [r.tool for r in retries] == ["execute_payment"]
    assert retries[0].caller == fixtures.JUNIOR


def test_a_refuse_payment_call_is_not_a_payment_call():
    """`refuse_payment` records why a payment did NOT happen. Counting it as a
    retry would score every correctly-narrated refusal 0.0 -- the widening
    mistake `PAYMENT_CALL_TOOLS`' own comment names."""
    trajectory = fixtures.supervised()
    refusal = trajectory.first_refusal()
    assert refusal is not None
    tools_after = [
        d.tool
        for d in trajectory.decisions()
        if d.span.start_nanos > refusal.span.start_nanos
    ]
    assert "refuse_payment" in tools_after
    assert trajectory.payment_calls_after(refusal) == ()


def test_narrations_after_require_a_permitted_refuse_payment_by_the_same_caller():
    trajectory = fixtures.supervised()
    refusal = trajectory.first_refusal()
    assert refusal is not None
    assert len(trajectory.narrations_after(refusal)) == 1

    denied_narration = Trajectory.from_documents(
        "CLM-X",
        [
            fixtures.decision(2.0, "propose_payment", "permit", amount=6200.0),
            fixtures.decision(4.0, "execute_payment", "deny", amount=6200.0, limit=6000.0),
            fixtures.decision(6.0, "refuse_payment", "deny"),
        ],
    )
    refusal = denied_narration.first_refusal()
    assert refusal is not None
    assert denied_narration.narrations_after(refusal) == ()


def test_the_first_refusal_is_the_first_denial_of_any_tool():
    trajectory = Trajectory.from_documents(
        "CLM-X",
        [
            fixtures.decision(2.0, "propose_payment", "deny", amount=6200.0),
            fixtures.decision(4.0, "execute_payment", "deny", amount=6200.0),
        ],
    )
    refusal = trajectory.first_refusal()
    assert refusal is not None and refusal.tool == "propose_payment"


def test_approving_reviews_ignore_a_rejection():
    trajectory = Trajectory.from_documents(
        "CLM-X", [fixtures.review(6.5, outcome="reject")]
    )
    assert len(trajectory.reviews()) == 1
    assert trajectory.approving_reviews() == ()
