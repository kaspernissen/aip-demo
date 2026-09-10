"""M1.2's final-review fix round (SF-2) added a `pytest`-pin tripwire to
`agents/settlement-agent/tests/test_pins.py`, closing the "no service has any
advisory gate" mechanism gap the final review named there -- but only for
settlement-agent. `agents/claims-agent` and `tools/payments-mcp` got their own
copies in M2.1 Task 6 (spec §11); `policy-decision-service` is the fourth and
last Python service and had none until this file (M3 Task 1, Part A).

No `pytest-asyncio` assertion here: this service pins no `pytest-asyncio` at
all (`requirements.txt`) because it has no async tests -- verified by
`grep -rn "async def test_\\|pytest.mark.asyncio" platform/policy-decision-service/tests/`
finding none, same check `tools/payments-mcp/tests/test_pins.py` documents for
itself. Asserting a plugin this service does not use would itself be invented
telemetry about this service's own dependency closure.

Asserted from the installed tree via `importlib.metadata`, never by parsing
`requirements.txt` -- a pin file is not an installed tree.
"""
from __future__ import annotations

import importlib.metadata as md


def test_pytest_is_pinned_clear_of_pysec_2026_1845():
    assert md.version("pytest") == "9.0.3"
