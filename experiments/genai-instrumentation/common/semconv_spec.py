"""Flatten the GenAI semconv span definitions (weaver `definition/2` format) into
{span type: {attribute: requirement level}}.

Input: a local copy of `model/gen-ai/spans.yaml` from
github.com/open-telemetry/semantic-conventions-genai (no release yet; record the
commit you read). Fetch with:

  gh api repos/open-telemetry/semantic-conventions-genai/contents/model/gen-ai/spans.yaml \
      -H 'Accept: application/vnd.github.raw' > spans.yaml

Usage: python semconv_spec.py spans.yaml [span-type ...]
"""
from __future__ import annotations

import json
import sys

import yaml


def _rl(a: dict) -> str | None:
    if "requirement_level" not in a:
        return None  # inherit the level set by the group / base span
    rl = a["requirement_level"]
    if isinstance(rl, dict):
        k, v = next(iter(rl.items()))
        return f"{k}: {' '.join(str(v).split())}"
    return str(rl)


def flatten(doc: dict) -> dict[str, dict[str, str]]:
    groups = {g["id"]: g for g in doc.get("attribute_groups", [])}

    def expand(attrs: list[dict]) -> dict[str, str]:
        out: dict[str, str] = {}
        for a in attrs or []:
            if "ref_group" in a:
                out.update(expand(groups[a["ref_group"]].get("attributes", [])))
            else:
                key = a.get("ref") or a.get("id")
                level = _rl(a)
                if level is not None or key not in out:
                    out[key] = level or "recommended"
        return out

    spans: dict[str, dict[str, str]] = {}
    for s in doc.get("spans", []):
        spans[s["type"]] = {"__kind__": s.get("kind", ""), **expand(s.get("attributes", []))}
    for r in doc.get("span_refinements", []):
        base = dict(spans.get(r.get("ref") or "", {}))
        for a in r.get("attributes", []) or []:
            if "ref_group" in a:
                base.update(expand(groups[a["ref_group"]].get("attributes", [])))
            elif _rl(a) is not None:
                base[a.get("ref") or a.get("id")] = _rl(a)
            else:
                base.setdefault(a.get("ref") or a.get("id"), "recommended")
        spans[r["id"]] = base
    return spans


if __name__ == "__main__":
    flat = flatten(yaml.safe_load(open(sys.argv[1])))
    wanted = sys.argv[2:] or list(flat)
    print(json.dumps({k: flat[k] for k in wanted if k in flat}, indent=2))
