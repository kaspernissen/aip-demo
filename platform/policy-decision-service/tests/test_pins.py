"""A `pytest`-pin tripwire, one of a matching set across
`agents/settlement-agent`, `agents/claims-agent`, `tools/payments-mcp` and
this, the fourth and last Python service, so no service is left without an
advisory gate on its own pinned versions.

No `pytest-asyncio` assertion here: this service pins no `pytest-asyncio` at
all (`requirements.txt`) because it has no async tests -- verified by
`grep -rn "async def test_\\|pytest.mark.asyncio" platform/policy-decision-service/tests/`
finding none, same check `tools/payments-mcp/tests/test_pins.py` documents for
itself. Asserting a plugin this service does not use would itself be invented
telemetry about this service's own dependency closure.

Asserted from the installed tree via `importlib.metadata`, never by parsing
`requirements.txt` -- a pin file is not an installed tree.

Three direct pins here besides `pytest`: `openfeature-sdk`,
`openfeature-provider-flagd` and `openfeature-hooks-opentelemetry`. Their own
transitive closure (cachebox, mmh3, openfeature-flagd-api,
openfeature-flagd-core, panzi-json-logic, pyyaml, semver) is not asserted
individually, for the same reason no other transitive closure in this file
is: a pin *this file's own code imports directly* earns a tripwire so
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
