"""Compare a span dump against the flattened GenAI semconv span definitions.

Each observed span is classified by its `gen_ai.operation.name` (falling back to
the span name prefix, then to `openinference.span.kind` / `traceloop.span.kind`
for libraries that do not set it):

  chat / text_completion / generate_content -> anthropic.gen_ai.client.inference
  invoke_agent                              -> gen_ai.invoke_agent.internal
  execute_tool                              -> gen_ai.execute_tool.internal

For each class it prints:
  present   spec attributes seen on at least one span of that class
  missing   spec attributes never seen (opt_in and "when available and non-zero"
            breakdown attributes are listed separately, since absence is allowed)
  extra     attributes seen that the spec does not define for that span (any prefix)

Usage: python compare.py SPEC_FLAT_JSON SPANS_JSON [SPANS_JSON ...]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

CLASS = {
    "chat": "anthropic.gen_ai.client.inference",
    "text_completion": "anthropic.gen_ai.client.inference",
    "generate_content": "anthropic.gen_ai.client.inference",
    "invoke_agent": "gen_ai.invoke_agent.internal",
    "execute_tool": "gen_ai.execute_tool.internal",
}
# Fallbacks for non-semconv libraries, so their spans still land in a class.
KIND_FALLBACK = {
    "LLM": "chat", "AGENT": "invoke_agent", "TOOL": "execute_tool",
    "llm": "chat", "agent": "invoke_agent", "tool": "execute_tool", "workflow": "invoke_agent",
}


def classify(span: dict) -> str | None:
    a = span["attributes"]
    op = a.get("gen_ai.operation.name")
    if op is None:
        first = span["name"].split(" ")[0]
        if first in CLASS:
            op = first
    if op is None:
        k = a.get("openinference.span.kind") or a.get("traceloop.span.kind")
        op = KIND_FALLBACK.get(k)
    if op is None and span["name"] in ("anthropic.chat", "chat"):
        op = "chat"
    return CLASS.get(op) if op else None


def main() -> None:
    spec = json.load(open(sys.argv[1]))
    seen: dict[str, set[str]] = defaultdict(set)
    names: dict[str, set[str]] = defaultdict(set)
    unclassified: set[str] = set()
    for path in sys.argv[2:]:
        for s in json.load(open(path)):
            cls = classify(s)
            if cls is None:
                unclassified.add(f"{s['name']} ({s['scope']})")
                continue
            names[cls].add(s["name"])
            seen[cls].update(s["attributes"])
            if s["status"] == "ERROR":
                seen[cls].add("__status_error__")
    report = {}
    for cls, attrs in seen.items():
        want = {k: v for k, v in spec[cls].items() if not k.startswith("__")}
        optional = {k for k, v in want.items()
                    if v == "opt_in" or "non-zero" in v}
        report[cls] = {
            "span_names": sorted(names[cls]),
            "present": sorted(k for k in want if k in attrs),
            "missing_required_or_recommended": sorted(
                f"{k} [{want[k]}]" for k in want if k not in attrs and k not in optional),
            "missing_optional": sorted(k for k in optional if k not in attrs),
            "extra": sorted(k for k in attrs if k not in want and not k.startswith("__")),
        }
    report["__unclassified_spans__"] = sorted(unclassified)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
