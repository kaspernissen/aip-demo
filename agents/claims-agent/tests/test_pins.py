"""Mirrors the pin-tripwire pair in
`agents/settlement-agent/tests/test_pins.py`: `claims-agent` ships the same
`pytest==9.0.3`/`pytest-asyncio==1.4.0` bump in its own `requirements.txt`
(clearing PYSEC-2026-1845), and this file is what checks it stays there.

Asserted from the installed tree via `importlib.metadata`, never by parsing
`requirements.txt` — a pin file is not an installed tree. This is a tripwire
against this exact pair silently downgrading, not a general advisory
scanner.
"""
from __future__ import annotations

import importlib.metadata as md


def test_pytest_is_pinned_clear_of_pysec_2026_1845():
    assert md.version("pytest") == "9.1.1"


def test_pytest_asyncio_is_pinned_to_the_version_pytest_9_requires():
    assert md.version("pytest-asyncio") == "1.4.0"


# M13: the intake model. The same PydanticAI as settlement-agent's, not a
# newer one: a version refresh is its own milestone, and two agents on two
# versions would publish two `gen_ai.*` inventories under one contract.
def test_pydantic_ai_is_at_settlement_agents_version():
    assert md.version("pydantic-ai-slim") == "2.42.0"
    assert md.version("pydantic-graph") == md.version("pydantic-ai-slim")
    assert md.version("anthropic") == "1.4.0"


def test_every_package_both_agents_pin_is_pinned_to_the_same_version():
    """A file comparison, the one place this suite reads pin files: the claim
    is about two files agreeing, not about the installed tree."""
    from pathlib import Path

    def pins(path: Path) -> dict[str, str]:
        out = {}
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if "==" in line:
                name, version = line.split("==")
                out[name.split("[")[0].lower()] = version
        return out

    agents = Path(__file__).resolve().parents[2]
    here, there = pins(agents / "claims-agent/requirements.txt"), pins(agents / "settlement-agent/requirements.txt")
    assert "pydantic-ai-slim" in here and "httpx2" in here
    assert {k: here[k] for k in here if k in there} == {k: there[k] for k in here if k in there}
