"""The restraining config is shown, never retyped.

`scripts/demo config` prints, and the deck's "The guardrails are config" slide
shows, a few exact lines from five files. Both come from GUARDRAIL_CONFIG in
scripts/demo.py; these tests hold that list, and the slide, to the files, so a
moved or edited guardrail is a red test rather than a stale slide on stage.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SLIDES = REPO_ROOT / "talk" / "slides" / "30-act3a.html"


def _driver():
    spec = importlib.util.spec_from_file_location("northstar_demo_driver_gc", REPO_ROOT / "scripts" / "demo.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _file_lines(path: str) -> set[str]:
    return {line.strip() for line in (REPO_ROOT / path).read_text().splitlines()}


def test_every_guardrail_line_is_in_its_file():
    demo = _driver()
    for _, _, path, wanted in demo.GUARDRAIL_CONFIG:
        missing = [w for w in wanted if w not in _file_lines(path)]
        assert not missing, f"{path} no longer has {missing}: update GUARDRAIL_CONFIG and the slide"
