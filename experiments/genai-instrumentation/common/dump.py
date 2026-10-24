"""Turn finished spans into a JSON dump and a per-span-shape attribute inventory."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from opentelemetry.sdk.trace import ReadableSpan

_TRUNC = 400


def _val(v: Any) -> Any:
    if isinstance(v, str) and len(v) > _TRUNC:
        return v[:_TRUNC] + f"... [{len(v)} chars]"
    if isinstance(v, tuple):
        return [_val(x) for x in v]
    return v


def _event_attrs(attrs: Any, conv) -> dict[str, Any]:
    # Stack traces carry local file paths and add nothing to an attribute audit.
    return {k: ("<omitted>" if k == "exception.stacktrace" else conv(v))
            for k, v in (attrs or {}).items()}


def span_to_dict(s: ReadableSpan, *, truncate: bool = True) -> dict[str, Any]:
    conv = _val if truncate else (lambda v: list(v) if isinstance(v, tuple) else v)
    return {
        "name": s.name,
        "kind": s.kind.name,
        "scope": s.instrumentation_scope.name if s.instrumentation_scope else None,
        "scope_version": s.instrumentation_scope.version if s.instrumentation_scope else None,
        "span_id": format(s.context.span_id, "016x"),
        "parent_id": format(s.parent.span_id, "016x") if s.parent else None,
        "status": s.status.status_code.name,
        "status_description": s.status.description,
        "attributes": {k: conv(v) for k, v in sorted((s.attributes or {}).items())},
        "events": [
            {"name": e.name, "attributes": _event_attrs(e.attributes, conv)}
            for e in s.events
        ],
    }


def tree(spans: Iterable[ReadableSpan]) -> str:
    spans = list(spans)
    by_parent: dict[Any, list[ReadableSpan]] = {}
    ids = {s.context.span_id for s in spans}
    for s in spans:
        p = s.parent.span_id if s.parent and s.parent.span_id in ids else None
        by_parent.setdefault(p, []).append(s)
    lines: list[str] = []

    def walk(pid, depth):
        for s in sorted(by_parent.get(pid, []), key=lambda x: x.start_time):
            status = "" if s.status.status_code.name == "UNSET" else f"  [{s.status.status_code.name}]"
            lines.append(f"{'  ' * depth}- {s.name} ({s.kind.name}, {s.instrumentation_scope.name}){status}")
            walk(s.context.span_id, depth + 1)

    walk(None, 0)
    return "\n".join(lines)


def inventory(spans: Iterable[ReadableSpan]) -> dict[str, list[str]]:
    """span name -> sorted union of attribute keys seen on spans of that name."""
    inv: dict[str, set[str]] = {}
    for s in spans:
        inv.setdefault(s.name, set()).update((s.attributes or {}).keys())
        for e in s.events:
            inv[s.name].add(f"event:{e.name}")
    return {k: sorted(v) for k, v in sorted(inv.items())}


def write_metrics(out_dir: Path, data: Any) -> None:
    """metric name -> {unit, attribute key sets seen} for all three scenarios."""
    out: dict[str, Any] = {}
    if data is not None:
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    keys = sorted({k for p in m.data.data_points for k in (p.attributes or {})})
                    out[m.name] = {"scope": sm.scope.name, "unit": m.unit,
                                   "type": type(m.data).__name__, "attribute_keys": keys}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(out, indent=2))


def write(out_dir: Path, scenario: str, spans: list[ReadableSpan]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{scenario}.spans.json").write_text(
        json.dumps([span_to_dict(s) for s in spans], indent=2, default=str)
    )
    (out_dir / f"{scenario}.tree.txt").write_text(tree(spans) + "\n")
    (out_dir / f"{scenario}.inventory.json").write_text(json.dumps(inventory(spans), indent=2))
