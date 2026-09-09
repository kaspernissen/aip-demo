"""M1.2's final-review fix round (SF-2) added a `pytest`-pin tripwire to
`agents/settlement-agent/tests/test_pins.py`, closing the "no service has
any advisory gate" mechanism gap the final review named there — but only for
settlement-agent. `payments-mcp` shipped the same `pytest==9.0.3` bump in its
own `requirements.txt` (clearing PYSEC-2026-1845) with nothing checking it
stays there; this file closes that carried gap (M2.1 Task 6, spec §11).

No `pytest-asyncio` assertion here: this service pins no `pytest-asyncio` at
all (`requirements.txt`) because its async calls all go through synchronous
`anyio.run()`/`asyncio.run()` wrappers, not `@pytest.mark.asyncio` — verified
by `grep -rn "async def test_\\|pytest.mark.asyncio" tools/payments-mcp/tests/`
finding none. Asserting a plugin this service does not use would itself be
invented telemetry about this service's own dependency closure.

Asserted from the installed tree via `importlib.metadata`, never by parsing
`requirements.txt` — a pin file is not an installed tree.
"""
from __future__ import annotations

import importlib.metadata as md


def test_pytest_is_pinned_clear_of_pysec_2026_1845():
    assert md.version("pytest") == "9.0.3"
