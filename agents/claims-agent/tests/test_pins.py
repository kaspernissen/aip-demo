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
