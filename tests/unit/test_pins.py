"""M5-R33: the root `.venv` (`tests/requirements.txt`) had no pin guard.

Every per-service venv has one -- `agents/settlement-agent/tests/test_pins.py`
(M1.2's final-review fix round, SF-2, closing PYSEC-2026-1845), then
`agents/claims-agent` and `tools/payments-mcp` (M2.1 Task 6, spec §11),
`apps/reviewer-ui` (from its own creation) and
`platform/policy-decision-service` (M3 Task 1, Part A). The root venv --
the one `scripts/smoke-test.sh` and this repo's own CI-shaped runs invoke as
`.venv/bin/python -m pytest tests` -- was the seventh Python environment in
this repo and the only one with no tripwire of its own: `grep -rln
"tests/requirements.txt" --include="*.py" tests/ scripts/` finds nothing
that asserts against it.

This venv runs `tests/unit` AND `tests/e2e`, including the anti-vacuity e2e
(`tests/e2e/test_progressive_autonomy.py`) M5's headline claim rests on --
an unpinned drift here would move the evidence base for that claim without
anything noticing. Whole-branch review: found by a gate attempt failing,
the most expensive way to find it; all seven venvs happened to already agree
on `pytest 9.1.1` at the time this was written, which is exactly when a gap
like this is cheapest to close and hardest to notice on its own.

Asserted from the installed tree via `importlib.metadata`, never by parsing
`requirements.txt` -- a pin file is not an installed tree (same discipline
every other `test_pins.py` in this repo follows). Not a general vulnerability
scanner -- a tripwire against these exact versions silently drifting.

Following `platform/policy-decision-service/tests/test_pins.py`'s stated
rule for what earns a tripwire here: `pytest` (the PYSEC-2026-1845 pin every
sibling file carries first), plus the packages this harness's own test code
imports directly -- `mcp` (`tests/e2e/test_payments_mcp_trace.py`), `httpx`
and `requests` (most of `tests/e2e/`), `PyYAML` (`tests/harness/collector.py`,
most of `tests/unit/`), `anyio` (`tests/e2e/test_payments_mcp_trace.py`'s
`anyio.run`) and `playwright` (the browser tests gated behind
`RUN_BROWSER_TESTS=1`). No `pytest-asyncio` here, for the same reason
`tests/requirements.txt`'s own header gives: this tree's async calls all go
through synchronous `anyio.run()`, never `@pytest.mark.asyncio`.
"""
from __future__ import annotations

import importlib.metadata as md


def test_pytest_is_pinned_clear_of_pysec_2026_1845():
    assert md.version("pytest") == "9.1.1"


def test_mcp_is_pinned_to_the_version_this_harness_speaks():
    assert md.version("mcp") == "2.2.0"


def test_httpx_is_pinned_to_the_version_the_rest_of_the_repo_uses():
    assert md.version("httpx") == "0.28.1"


def test_requests_is_pinned_clear_of_its_own_advisory_pair():
    assert md.version("requests") == "2.34.2"


def test_pyyaml_is_pinned_to_the_version_every_stage_guard_parses_with():
    assert md.version("PyYAML") == "6.0.2"


def test_anyio_is_pinned_to_the_version_the_synchronous_mcp_calls_use():
    assert md.version("anyio") == "4.15.1"


def test_playwright_is_pinned_to_the_version_the_browser_tests_use():
    assert md.version("playwright") == "1.49.1"
