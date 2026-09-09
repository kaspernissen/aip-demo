"""M1.2's final-review fix round (SF-2) added this exact pair of pin
assertions to `agents/settlement-agent/tests/test_pins.py`, closing the "no
service has any advisory gate" mechanism gap the final review named there —
but only for settlement-agent. `claims-agent` shipped the same
`pytest==9.0.3`/`pytest-asyncio==1.4.0` bump in its own `requirements.txt`
(clearing PYSEC-2026-1845) with nothing checking it stays there; this file
closes that carried gap (M2.1 Task 6, spec §11).

Asserted from the installed tree via `importlib.metadata`, never by parsing
`requirements.txt` — a pin file is not an installed tree. This is a tripwire
against this exact pair silently downgrading, not a general advisory
scanner.
"""
from __future__ import annotations

import importlib.metadata as md


def test_pytest_is_pinned_clear_of_pysec_2026_1845():
    assert md.version("pytest") == "9.0.3"


def test_pytest_asyncio_is_pinned_to_the_version_pytest_9_requires():
    assert md.version("pytest-asyncio") == "1.4.0"
