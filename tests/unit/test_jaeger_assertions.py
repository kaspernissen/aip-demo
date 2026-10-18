"""Unit tests for the trace-shape assertions that don't need a live cluster.

assert_single_root's failure message is the one assertion whose text is load-bearing:
a zero-roots trace is exactly the signature of broken context propagation (the CORS
traceparent trap and the MCP params._meta trap this harness exists to catch), so the
message has to name every span and its declared parent, not just say "found 0: []".

Trace.span/spans_named's `service` filter, and wait_for_trace_id's `services`
quiet-window completion check, are unit-tested here too, against a fake clock
and a scripted JaegerClient.get_trace — no live cluster needed, and it lets the
quiet-window timing be asserted precisely rather than by sleeping for real.
"""
from __future__ import annotations

import pytest

from tests.harness import jaeger as jaeger_module
from tests.harness.jaeger import JaegerClient, Trace, assert_single_root


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


# --- Trace.span()/spans_named() optional `service` filter --------------------


def test_span_without_service_still_raises_on_cross_service_ambiguity():
    """Two services, same operation name, no `service` filter given — still
    ambiguous, still raises."""
    trace = Trace(
        _raw_trace(
            [
                _raw_span("span1", "POST /", process_id="p1"),
                _raw_span("span2", "POST /", process_id="p2"),
            ]
        )
    )

    with pytest.raises(AssertionError, match="2 spans named 'POST /', expected one"):
        trace.span("POST /")


def test_span_service_filter_disambiguates_across_services():
    """Once two services each have a "POST /" span, filtering by service
    picks out the right one instead of raising."""
    trace = Trace(
        _raw_trace(
            [
                _raw_span("span1", "POST /", process_id="p1"),
                _raw_span("span2", "POST /", process_id="p2"),
            ]
        )
    )

    found = trace.span("POST /", service="svc-b")
    assert found.span_id == "span2"
    assert found.service == "svc-b"


def test_span_service_filter_still_raises_on_ambiguity_within_one_service():
    """Ambiguity *within* one service must still raise, even with a `service`
    filter given."""
    trace = Trace(
        _raw_trace(
            [
                _raw_span("span1", "POST /", process_id="p1"),
                _raw_span("span2", "POST /", process_id="p1"),
            ]
        )
    )

    with pytest.raises(AssertionError, match="2 spans named 'POST /'"):
        trace.span("POST /", service="svc-a")


def test_span_service_filter_raises_when_no_match_in_that_service():
    trace = Trace(_raw_trace([_raw_span("span1", "POST /", process_id="p1")]))

    with pytest.raises(AssertionError, match="no span named 'POST /' in service 'svc-b'"):
        trace.span("POST /", service="svc-b")


def test_spans_named_filters_by_service():
    trace = Trace(
        _raw_trace(
            [
                _raw_span("span1", "POST /", process_id="p1"),
                _raw_span("span2", "POST /", process_id="p2"),
            ]
        )
    )

    assert [s.span_id for s in trace.spans_named("POST /", service="svc-a")] == ["span1"]
    assert [s.span_id for s in trace.spans_named("POST /")] == ["span1", "span2"]


# --- wait_for_trace_id's `services` quiet-window completion check ------------


class _FakeClock:
    """A controllable clock so the quiet-window logic can be asserted exactly,
    without sleeping in real time. `sleep()` advances the fake clock instead
    of blocking."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _install_fake_clock(monkeypatch) -> _FakeClock:
    clock = _FakeClock()
    monkeypatch.setattr(jaeger_module.time, "time", clock.time)
    monkeypatch.setattr(jaeger_module.time, "sleep", clock.sleep)
    return clock


def _script_get_trace(monkeypatch, client: JaegerClient, schedule: list[tuple[float, dict]]):
    """Makes client.get_trace(trace_id) return the Trace for the latest
    schedule entry whose elapsed-time threshold has been reached, keyed off
    the fake clock installed by _install_fake_clock. `schedule` must be
    sorted ascending by threshold and cover t=0.
    """
    def fake_get_trace(trace_id: str) -> Trace:
        now = jaeger_module.time.time()
        raw = schedule[0][1]
        for threshold, candidate in schedule:
            if now >= threshold:
                raw = candidate
        return Trace(raw)

    monkeypatch.setattr(client, "get_trace", fake_get_trace)


def test_wait_for_trace_id_without_services_keeps_original_immediate_return(monkeypatch):
    """With no `services` argument, returns as soon as `operation` is present
    — no quiet window applied."""
    clock = _install_fake_clock(monkeypatch)
    client = JaegerClient("http://fake")
    trace_raw = _raw_trace([_raw_span("span1", "POST /", process_id="p1")])
    _script_get_trace(monkeypatch, client, [(0.0, trace_raw)])

    result = client.wait_for_trace_id("trace-x", operation="POST /", timeout=5, interval=1)

    assert result.trace_id == "test-trace"
    assert clock.now == 0.0  # returned on the very first poll, no sleeping


def test_wait_for_trace_id_services_waits_for_quiet_window_past_export_delay(monkeypatch):
    """Once every named service is present, the trace must hold its span
    count steady for QUIET_WINDOW_S (10s, > the 5s BatchSpanProcessor default
    export delay) before being returned — not merely appear once."""
    clock = _install_fake_clock(monkeypatch)
    client = JaegerClient("http://fake")

    two_span_trace = _raw_trace(
        [
            _raw_span("span1", "POST /", process_id="p1"),
            _raw_span("span2", "POST /", parent_id="span1", process_id="p2"),
        ]
    )
    # Both services (svc-a, svc-b) are present from t=0 onward; nothing ever
    # changes again. The quiet window should still make the call wait until
    # t >= QUIET_WINDOW_S before returning.
    _script_get_trace(monkeypatch, client, [(0.0, two_span_trace)])

    result = client.wait_for_trace_id(
        "trace-x", timeout=60, interval=1, services={"svc-a", "svc-b"}
    )

    assert result.services() == {"svc-a", "svc-b"}
    # The quiet window is pinned at 10s, comfortably over the 5s
    # BatchSpanProcessor default export delay — hardcoded here (not read back
    # from JaegerClient.QUIET_WINDOW_S) so that shrinking the constant back
    # down would fail this test rather than silently passing against itself.
    assert clock.now >= 10.0


def test_wait_for_trace_id_services_resets_quiet_window_on_late_arrival(monkeypatch):
    """A third service's spans land late, after the trace first looked
    complete. A quiet window shorter than the export delay would declare
    victory before that late span arrives; the harness's window must reset
    and wait for the *new* count to hold steady."""
    clock = _install_fake_clock(monkeypatch)
    client = JaegerClient("http://fake")

    two_service_trace = _raw_trace(
        [
            _raw_span("span1", "POST /", process_id="p1"),
            _raw_span("span2", "POST /", parent_id="span1", process_id="p2"),
        ]
    )
    three_service_trace = _raw_trace(
        [
            _raw_span("span1", "POST /", process_id="p1"),
            _raw_span("span2", "POST /", parent_id="span1", process_id="p2"),
            _raw_span("span3", "tools/call x", parent_id="span2", process_id="p3"),
        ]
    )
    three_service_trace["processes"]["p3"] = {"serviceName": "svc-c"}

    # svc-c's span lands at t=6 — inside a naive 2s-or-6s quiet window that
    # would already have declared the two-service trace "stable", but still
    # short of this harness's 10s window.
    _script_get_trace(
        monkeypatch,
        client,
        [(0.0, two_service_trace), (6.0, three_service_trace)],
    )

    result = client.wait_for_trace_id(
        "trace-x", timeout=60, interval=1, services={"svc-a", "svc-b", "svc-c"}
    )

    assert result.services() == {"svc-a", "svc-b", "svc-c"}
    # Must not have returned before the late span's own quiet window elapsed:
    # t=6 (arrival) + 10s (pinned window) = 16, not merely after t=10.
    # Hardcoded (not JaegerClient.QUIET_WINDOW_S) so shrinking the constant
    # can't make this assertion silently pass against itself — verified by
    # temporarily setting QUIET_WINDOW_S=2.0 and confirming this test then
    # fails (it returns as early as t=6, long before t=16).
    assert clock.now >= 16.0


def test_wait_for_trace_id_services_timeout_names_the_missing_service(monkeypatch):
    """Waiting longer is fine, but the timeout must fail loudly and name
    exactly what never showed up — never silently accept a partial trace as
    complete."""
    clock = _install_fake_clock(monkeypatch)
    client = JaegerClient("http://fake")

    only_svc_a = _raw_trace([_raw_span("span1", "POST /", process_id="p1")])
    _script_get_trace(monkeypatch, client, [(0.0, only_svc_a)])

    with pytest.raises(AssertionError, match=r"never gained services.*'svc-b'"):
        client.wait_for_trace_id(
            "trace-x", timeout=5, interval=1, services={"svc-a", "svc-b"}
        )


# --- span links, the root, and wait_for_operation (M8 Task 3) ----------------


def _linked_raw_trace():
    root = _raw_span("b1", "northstar.review.decision")
    root["traceID"] = "bbbb"
    root["duration"] = 2_500_000
    root["references"] = [{"refType": "FOLLOWS_FROM", "traceID": "aaaa", "spanID": "a1"}]
    child = _raw_span("b2", "POST /payments/{paymentId}/execute", parent_id="b1")
    return _raw_trace([root, child])


def test_a_span_link_is_read_as_a_link_and_not_as_a_parent():
    trace = Trace(_linked_raw_trace())
    root = trace.root()
    assert root.operation == "northstar.review.decision" and root.parent_id is None
    assert [(l.trace_id, l.span_id) for l in root.links] == [("aaaa", "a1")]
    assert root.duration_s == 2.5


def test_root_raises_unless_exactly_one():
    with pytest.raises(AssertionError):
        Trace(_raw_trace([_raw_span("x", "a"), _raw_span("y", "b")])).root()


def test_wait_for_operation_returns_the_whole_trace_by_id(monkeypatch):
    client = JaegerClient("http://jaeger.invalid")
    seen = {}

    def fake_find(service, operation=None, tags=None, lookback="1h", limit=20):
        seen.update(service=service, operation=operation, tags=tags)
        return [Trace(_linked_raw_trace())]

    monkeypatch.setattr(client, "find_traces", fake_find)
    monkeypatch.setattr(client, "get_trace", lambda tid: Trace(_linked_raw_trace()))
    trace = client.wait_for_operation("reviewer-ui", "northstar.review.decision",
                                      attrs={"northstar.review.payment.id": "PAY-1"}, timeout=1)
    assert trace.root().operation == "northstar.review.decision"
    assert seen == {"service": "reviewer-ui", "operation": "northstar.review.decision",
                    "tags": {"northstar.review.payment.id": "PAY-1"}}


def test_wait_for_operation_fails_when_nothing_matches(monkeypatch):
    client = JaegerClient("http://jaeger.invalid")
    monkeypatch.setattr(client, "find_traces", lambda *a, **k: [])
    _install_fake_clock(monkeypatch)
    with pytest.raises(AssertionError, match="no trace with"):
        client.wait_for_operation("reviewer-ui", "northstar.review.decision", timeout=3)


def test_wait_for_operation_links_to_skips_traces_that_link_elsewhere(monkeypatch):
    client = JaegerClient("http://jaeger.invalid")
    other = _linked_raw_trace()
    other["traceID"] = "other-trace"
    other["spans"][0]["references"][0]["traceID"] = "zzzz"
    monkeypatch.setattr(client, "find_traces", lambda *a, **k: [Trace(other), Trace(_linked_raw_trace())])
    fetched = []
    monkeypatch.setattr(client, "get_trace", lambda tid: fetched.append(tid) or Trace(_linked_raw_trace()))
    client.wait_for_operation("svc-a", "northstar.review.decision", timeout=1, links_to="aaaa")
    assert fetched == ["test-trace"]


# --- the model-provider retry predicate (M8 gate run 4) ----------------------


def test_only_a_payment_less_model_connection_failure_counts_as_upstream():
    from tests.harness import live
    down = {"state": "failed", "reply": {"paymentId": None,
            "error": "settlement-agent returned an error: ModelAPIError: Connection error."}}
    assert live.is_upstream_model_failure(down)
    assert not live.is_upstream_model_failure({**down, "state": "input-required"})
    assert not live.is_upstream_model_failure(
        {"state": "failed", "reply": {"paymentId": "PAY-000001", "error": "Connection error"}})
    assert not live.is_upstream_model_failure(
        {"state": "failed", "reply": {"paymentId": None, "error": "amount must not be negative"}})


def test_otlp_links_keep_the_link_attributes_the_v1_shape_drops():
    from tests.harness.jaeger import otlp_links

    payload = {"resourceSpans": [{"scopeSpans": [{"spans": [
        {"spanId": "b2", "name": "northstar.a2a.resume", "links": [{
            "traceId": "aaaa", "spanId": "a1",
            "attributes": [{"key": "northstar.a2a.link.reason", "value": {"stringValue": "resume"}}]}]},
        {"spanId": "b3", "name": "no links"},
    ]}]}]}
    assert otlp_links(payload) == {"b2": [
        {"trace_id": "aaaa", "span_id": "a1", "attrs": {"northstar.a2a.link.reason": "resume"}}]}
