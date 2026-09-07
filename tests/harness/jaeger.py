"""Jaeger query client and trace-shape assertions.

Trace shape is the contract this project rests on: if context propagation
breaks anywhere in the chain, every other test still passes while the demo
becomes worthless. These assertions are the guard against that.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
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
            for ref in raw_span.get("references", []):
                if ref.get("refType") == "CHILD_OF":
                    parent_id = ref.get("spanID")
            self.spans.append(
                Span(
                    operation=raw_span["operationName"],
                    service=process_names.get(raw_span.get("processID"), "unknown"),
                    span_id=raw_span["spanID"],
                    parent_id=parent_id,
                    kind=attrs.get("span.kind"),
                    attrs=attrs,
                )
            )

    @property
    def trace_id(self) -> str:
        return self._raw["traceID"]

    def span(self, operation: str) -> Span:
        matches = [s for s in self.spans if s.operation == operation]
        if not matches:
            raise AssertionError(
                f"no span named {operation!r}; present: {sorted(s.operation for s in self.spans)}"
            )
        if len(matches) > 1:
            raise AssertionError(f"{len(matches)} spans named {operation!r}, expected one")
        return matches[0]

    def spans_named(self, operation: str) -> list[Span]:
        return [s for s in self.spans if s.operation == operation]

    def has_span(self, operation: str) -> bool:
        return any(s.operation == operation for s in self.spans)

    def services(self) -> set[str]:
        return {s.service for s in self.spans}

    def roots(self) -> list[Span]:
        return [s for s in self.spans if s.parent_id is None]

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
        params: dict[str, Any] = {"service": service, "lookback": lookback, "limit": limit}
        if operation:
            params["operation"] = operation
        if tags:
            import json

            params["tags"] = json.dumps(tags)
        resp = requests.get(f"{self.base_url}/api/traces", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data") or []
        if not data:
            return None
        return Trace(data[0])

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


def assert_single_root(trace: Trace) -> None:
    roots = trace.roots()
    if len(roots) != 1:
        described = [f"{s.service}:{s.operation}" for s in roots]
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
