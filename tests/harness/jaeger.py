"""Jaeger query client and trace-shape assertions.

Trace shape is the contract this project rests on: if context propagation
breaks anywhere in the chain, every other test still passes while the demo
becomes worthless. These assertions are the guard against that.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import requests


@dataclass(frozen=True)
class Span:
    operation: str
    service: str
    span_id: str
    parent_id: str | None
    kind: str | None
    attrs: dict[str, Any]
    trace_id: str = ""
    duration_s: float = 0.0
    # Span links, as the trace ids they point at (Jaeger renders a link as a
    # FOLLOWS_FROM reference; a parent is a CHILD_OF one).
    links: tuple["Link", ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Link:
    trace_id: str
    span_id: str


class Trace:
    def __init__(self, raw: dict[str, Any]):
        self._raw = raw
        process_names = {
            pid: proc.get("serviceName", "unknown")
            for pid, proc in raw.get("processes", {}).items()
        }
        self.spans: list[Span] = []
        for raw_span in raw.get("spans", []):
            attrs = {tag["key"]: tag["value"] for tag in raw_span.get("tags", [])}
            parent_id = None
            links = []
            for ref in raw_span.get("references", []):
                if ref.get("refType") == "CHILD_OF":
                    parent_id = ref.get("spanID")
                elif ref.get("refType") == "FOLLOWS_FROM":
                    links.append(Link(ref.get("traceID", ""), ref.get("spanID", "")))
            self.spans.append(
                Span(
                    operation=raw_span["operationName"],
                    service=process_names.get(raw_span.get("processID"), "unknown"),
                    span_id=raw_span["spanID"],
                    parent_id=parent_id,
                    kind=attrs.get("span.kind"),
                    attrs=attrs,
                    trace_id=raw_span.get("traceID", raw.get("traceID", "")),
                    duration_s=raw_span.get("duration", 0) / 1_000_000,
                    links=tuple(links),
                )
            )

    @property
    def trace_id(self) -> str:
        return self._raw["traceID"]

    def span(self, operation: str, service: str | None = None) -> Span:
        """Look up the single span named `operation`.

        Two services can each have a same-named span (e.g. both
        claims-agent and settlement-agent have their own "POST /" entry
        span), which makes `operation` alone ambiguous across the whole
        trace. The optional `service` filter disambiguates *across*
        services; ambiguity *within* one service still raises.
        """
        matches = self.spans_named(operation, service=service)
        if not matches:
            if service is None:
                raise AssertionError(
                    f"no span named {operation!r}; present: "
                    f"{sorted(s.operation for s in self.spans)}"
                )
            raise AssertionError(
                f"no span named {operation!r} in service {service!r}; present: "
                f"{sorted((s.service, s.operation) for s in self.spans)}"
            )
        if len(matches) > 1:
            scope = f" in service {service!r}" if service is not None else ""
            raise AssertionError(
                f"{len(matches)} spans named {operation!r}{scope}, expected one"
            )
        return matches[0]

    def spans_named(self, operation: str, service: str | None = None) -> list[Span]:
        return [
            s
            for s in self.spans
            if s.operation == operation and (service is None or s.service == service)
        ]

    def has_span(self, operation: str) -> bool:
        return any(s.operation == operation for s in self.spans)

    def services(self) -> set[str]:
        return {s.service for s in self.spans}

    def roots(self) -> list[Span]:
        return [s for s in self.spans if s.parent_id is None]

    def root(self) -> Span:
        """The one span with no parent; raises if there is not exactly one."""
        roots = self.roots()
        if len(roots) != 1:
            raise AssertionError(
                f"expected one root span, found {len(roots)}: "
                f"{[(s.service, s.operation) for s in roots]}"
            )
        return roots[0]

    def orphans(self) -> list[Span]:
        """Spans whose declared parent is not present in this trace."""
        ids = {s.span_id for s in self.spans}
        return [s for s in self.spans if s.parent_id is not None and s.parent_id not in ids]


class JaegerClient:
    def __init__(self, base_url: str = "http://localhost:16686"):
        self.base_url = base_url.rstrip("/")

    def services(self) -> list[str]:
        resp = requests.get(f"{self.base_url}/api/services", timeout=10)
        resp.raise_for_status()
        return resp.json().get("data") or []

    def find_trace(
        self,
        service: str,
        operation: str | None = None,
        tags: dict[str, str] | None = None,
        lookback: str = "1h",
        limit: int = 20,
    ) -> Trace | None:
        found = self.find_traces(service, operation, tags, lookback, limit)
        return found[0] if found else None

    def find_traces(
        self,
        service: str,
        operation: str | None = None,
        tags: dict[str, str] | None = None,
        lookback: str = "1h",
        limit: int = 20,
    ) -> list[Trace]:
        """Every trace matching the search, newest first (Jaeger's order)."""
        params: dict[str, Any] = {"service": service, "lookback": lookback, "limit": limit}
        if operation:
            params["operation"] = operation
        if tags:
            params["tags"] = json.dumps(tags)
        resp = requests.get(f"{self.base_url}/api/traces", params=params, timeout=15)
        resp.raise_for_status()
        return [Trace(t) for t in (resp.json().get("data") or [])]

    def wait_for_operation(
        self,
        service: str,
        operation: str,
        attrs: dict[str, Any] | None = None,
        timeout: float = 60.0,
        interval: float = 2.0,
        links_to: str | None = None,
    ) -> Trace:
        """The newest trace holding `operation` in `service` whose span carries
        every attribute in `attrs`; the trace is re-fetched whole by id, since a
        search result can be cut short. `links_to`, if given, keeps only traces
        where that operation's span links to that trace id (a claim id can be
        reused across runs; a link cannot). Fails on timeout, never returns None."""
        tags = {k: str(v) for k, v in (attrs or {}).items()}
        deadline = time.time() + timeout
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                for found in self.find_traces(service, operation, tags, limit=20):
                    if links_to is not None and not any(
                        link.trace_id == links_to
                        for span in found.spans_named(operation, service)
                        for link in span.links
                    ):
                        continue
                    return self.get_trace(found.trace_id)
            except (requests.RequestException, AssertionError) as exc:
                last_error = exc
            time.sleep(interval)
        detail = f" (last error: {last_error})" if last_error else ""
        raise AssertionError(
            f"no trace with {operation!r} in {service!r} carrying {attrs!r} "
            f"within {timeout}s{detail}"
        )

    def get_trace(self, trace_id: str) -> Trace:
        resp = requests.get(f"{self.base_url}/api/traces/{trace_id}", timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data") or []
        if not data:
            raise AssertionError(f"trace {trace_id} not found")
        return Trace(data[0])

    def wait_for_trace(
        self,
        service: str,
        operation: str | None = None,
        tags: dict[str, str] | None = None,
        timeout: float = 60.0,
        interval: float = 2.0,
    ) -> Trace:
        deadline = time.time() + timeout
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                trace = self.find_trace(service, operation=operation, tags=tags)
                if trace is not None:
                    return trace
            except requests.RequestException as exc:
                last_error = exc
            time.sleep(interval)
        detail = f" (last error: {last_error})" if last_error else ""
        raise AssertionError(
            f"no trace for service={service!r} operation={operation!r} "
            f"tags={tags!r} within {timeout}s{detail}"
        )

    # Independent processes' BatchSpanProcessors export on their own
    # timers, and every service in this repo — Python (bootstrap.py),
    # Go (payments-api), and the browser (customer-ui's telemetry.ts) alike —
    # uses the OTel SDK's default 5s schedule_delay_millis, unmodified. A
    # trace can look complete for an instant and then gain more spans
    # seconds later as a straggling batch lands. QUIET_WINDOW_S is the
    # minimum span-count-unchanged window `wait_for_trace_id` requires before
    # trusting a `services`-complete trace as actually complete. It must stay
    # comfortably longer than that 5s export delay: a 2s window and even a 6s
    # window both miss a batch landing just past the window edge once real
    # network/collector latency stacks on top of the timer itself; 10s holds
    # in timing simulation and in repeated live captures. If any service's
    # BatchSpanProcessor is ever retuned away from the SDK default, this
    # constant must move with it.
    QUIET_WINDOW_S = 10.0

    # What a claim that is waiting for a human adds to its trace for as long as
    # it waits: a2a-sdk's background consumer polls the task's event queue every
    # 0.5 s and each poll is a SERVER span (measured, M8 Task 2). They are counted
    # as spans (nothing is dropped from the trace) but not as *movement* when
    # `wait_for_trace_id` decides the trace has stopped growing.
    WAIT_POLLING_SPANS = frozenset({"a2a.server.events.event_queue.EventQueue.dequeue_event"})

    def wait_for_trace_id(
        self,
        trace_id: str,
        operation: str | None = None,
        timeout: float = 60.0,
        interval: float = 2.0,
        services: set[str] | None = None,
        steady_ignoring: frozenset[str] = frozenset(),
    ) -> Trace:
        """Poll for a trace by an ID the caller already knows (e.g. one it minted
        itself and sent as a `traceparent` header), rather than searching for it
        by service/operation/tags. Use this whenever the test is the request's
        origin: it removes any dependence on Jaeger's positional ordering or on
        attribute values staying unique across pod restarts.

        `services`, if given, additionally waits for the trace to look
        *complete*, not merely present — every named service must be
        represented, and the trace's total span count must then hold steady
        for `QUIET_WINDOW_S` (see above) before it is returned. This must
        never become a way to tolerate an absent span: on timeout this
        raises `AssertionError` naming exactly which services never showed
        up, rather than returning whatever trace was last fetched — a caller
        can only ever act on a trace this method itself judged complete.

        `steady_ignoring` names operations that do not count towards "the span
        count held steady" (use `WAIT_POLLING_SPANS` for a claim that is waiting
        for a human). The returned trace still contains them.
        """
        deadline = time.time() + timeout
        last_error: Exception | None = None
        last_trace: Trace | None = None
        last_span_count: int | None = None
        quiet_since: float | None = None
        while time.time() < deadline:
            try:
                trace = self.get_trace(trace_id)
                last_trace = trace
                if operation is not None and not trace.has_span(operation):
                    last_span_count = None
                    quiet_since = None
                elif services is None:
                    return trace
                else:
                    missing = services - trace.services()
                    if missing:
                        last_span_count = None
                        quiet_since = None
                    else:
                        now = time.time()
                        span_count = sum(
                            1 for sp in trace.spans if sp.operation not in steady_ignoring
                        )
                        if last_span_count is None or span_count != last_span_count:
                            last_span_count = span_count
                            quiet_since = now
                        elif now - quiet_since >= self.QUIET_WINDOW_S:
                            return trace
            except (requests.RequestException, AssertionError) as exc:
                last_error = exc
            time.sleep(interval)
        if last_trace is not None:
            if operation is not None and not last_trace.has_span(operation):
                raise AssertionError(
                    f"trace {trace_id!r} found but no span named {operation!r} "
                    f"within {timeout}s; present: "
                    f"{sorted(s.operation for s in last_trace.spans)}"
                )
            if services is not None:
                missing = services - last_trace.services()
                if missing:
                    raise AssertionError(
                        f"trace {trace_id!r} never gained services "
                        f"{sorted(missing)} within {timeout}s; present services: "
                        f"{sorted(last_trace.services())}"
                    )
                raise AssertionError(
                    f"trace {trace_id!r} had all of {sorted(services)} but its "
                    f"span count never held steady for {self.QUIET_WINDOW_S}s "
                    f"within {timeout}s total (last count: {len(last_trace.spans)})"
                )
        detail = f" (last error: {last_error})" if last_error else ""
        raise AssertionError(f"trace {trace_id!r} not found within {timeout}s{detail}")


def assert_single_root(trace: Trace) -> None:
    roots = trace.roots()
    if len(roots) != 1:
        if roots:
            described = [f"{s.service}:{s.operation}" for s in roots]
        else:
            # No roots at all is exactly the signature of broken context propagation:
            # list the full span inventory with declared parents so a reader can see
            # what was present and where each span thought it attached.
            described = [
                f"{s.service}:{s.operation} (parent={s.parent_id})" for s in trace.spans
            ]
        raise AssertionError(f"expected exactly one root span, found {len(roots)}: {described}")


def assert_no_orphans(trace: Trace) -> None:
    orphans = trace.orphans()
    if orphans:
        described = [f"{s.service}:{s.operation} (parent {s.parent_id})" for s in orphans]
        raise AssertionError(f"orphan spans present, context propagation is broken: {described}")


def assert_parent_child(trace: Trace, parent_op: str, child_op: str) -> None:
    parent = trace.span(parent_op)
    children = [s for s in trace.spans if s.parent_id == parent.span_id]
    if not any(s.operation == child_op for s in children):
        described = sorted(s.operation for s in children)
        raise AssertionError(
            f"{child_op!r} is not a child of {parent_op!r}; actual children: {described}"
        )


def assert_span_kind(trace: Trace, operation: str, kind: str) -> None:
    span = trace.span(operation)
    if span.kind != kind:
        raise AssertionError(f"{operation!r} has span.kind={span.kind!r}, expected {kind!r}")


def assert_attr_on_all_spans(
    trace: Trace, key: str, services: set[str] | None = None
) -> None:
    """Assert an attribute is present on every span, optionally limited to some services."""
    missing = [
        f"{s.service}:{s.operation}"
        for s in trace.spans
        if (services is None or s.service in services) and key not in s.attrs
    ]
    if missing:
        raise AssertionError(f"attribute {key!r} missing from spans: {sorted(missing)}")
