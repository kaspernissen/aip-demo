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

M5 Task 3 (Step 3) adds three direct pins here: `openfeature-sdk`,
`openfeature-provider-flagd` and `openfeature-hooks-opentelemetry`. Their own
transitive closure (cachebox, mmh3, openfeature-flagd-api,
openfeature-flagd-core, panzi-json-logic, pyyaml, semver) is not asserted
individually, for the same reason the pre-M5 transitive closure above it
never was: a pin *this file's own code imports directly* earns a tripwire so
a floating reinstall cannot silently change behaviour underneath it; a
dependency of a dependency does not, or every requirements.txt in this repo
would need one test per transitive package.
"""
from __future__ import annotations

import importlib.metadata as md


def test_pytest_is_pinned_clear_of_pysec_2026_1845():
    assert md.version("pytest") == "9.1.1"


def test_the_openfeature_sdk_is_pinned():
    assert md.version("openfeature-sdk") == "0.10.0"


def test_the_flagd_provider_is_pinned():
    assert md.version("openfeature-provider-flagd") == "0.5.2"


def test_the_opentelemetry_hook_is_pinned():
    assert md.version("openfeature-hooks-opentelemetry") == "0.3.1"
