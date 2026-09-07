"""Unit tests for the trace-shape assertions that don't need a live cluster.

assert_single_root's failure message is the one assertion whose text is load-bearing:
a zero-roots trace is exactly the signature of broken context propagation (the CORS
traceparent trap and the MCP params._meta trap this harness exists to catch), so the
message has to name every span and its declared parent, not just say "found 0: []".
"""
from __future__ import annotations

import pytest

from tests.harness.jaeger import Trace, assert_single_root


def _raw_span(span_id: str, operation: str, parent_id: str | None = None, process_id: str = "p1"):
    references = []
    if parent_id is not None:
        references = [{"refType": "CHILD_OF", "spanID": parent_id}]
    return {
        "spanID": span_id,
        "operationName": operation,
        "references": references,
        "tags": [],
        "processID": process_id,
    }


def _raw_trace(spans: list[dict]) -> dict:
    return {
        "traceID": "test-trace",
        "spans": spans,
        "processes": {
            "p1": {"serviceName": "svc-a"},
            "p2": {"serviceName": "svc-b"},
        },
    }


def test_assert_single_root_zero_roots_lists_every_span_and_its_parent():
    """Neither span is a root; the message must name both spans and their parents."""
    trace = Trace(
        _raw_trace(
            [
                _raw_span("span1", "op.a", parent_id="missing-parent-1", process_id="p1"),
                _raw_span("span2", "op.b", parent_id="missing-parent-2", process_id="p2"),
            ]
        )
    )

    with pytest.raises(AssertionError) as exc_info:
        assert_single_root(trace)

    message = str(exc_info.value)
    assert "found 0" in message
    assert "svc-a:op.a (parent=missing-parent-1)" in message
    assert "svc-b:op.b (parent=missing-parent-2)" in message


def test_assert_single_root_multiple_roots_lists_the_root_spans():
    """Both spans are roots; the >1 case message is unchanged (lists just the roots)."""
    trace = Trace(
        _raw_trace(
            [
                _raw_span("span1", "op.a", process_id="p1"),
                _raw_span("span2", "op.b", process_id="p2"),
            ]
        )
    )

    with pytest.raises(AssertionError) as exc_info:
        assert_single_root(trace)

    message = str(exc_info.value)
    assert "found 2" in message
    assert "svc-a:op.a" in message
    assert "svc-b:op.b" in message


def test_assert_single_root_passes_with_exactly_one_root():
    trace = Trace(
        _raw_trace(
            [
                _raw_span("span1", "op.parent", process_id="p1"),
                _raw_span("span2", "op.child", parent_id="span1", process_id="p1"),
            ]
        )
    )

    assert_single_root(trace)  # must not raise
