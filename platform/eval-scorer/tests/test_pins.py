"""The advisory-pin tripwire every Python service in this repo carries.

`agents/settlement-agent/tests/test_pins.py` is the original, closing the "no
service has any advisory gate" mechanism gap; `agents/claims-agent`,
`tools/payments-mcp`, `platform/policy-decision-service` and
`apps/reviewer-ui` each have their own. eval-scorer is the sixth Python
service and this is its copy.

No `pytest-asyncio` assertion: this service pins none, because it has no async
tests -- verified by
`grep -rn "async def test_\\|pytest.mark.asyncio" platform/eval-scorer/tests/`
finding none, the same check `tools/payments-mcp/tests/test_pins.py` documents
for itself. Asserting a plugin this service does not use would be invented
telemetry about its own dependency closure.

Asserted from the installed tree via `importlib.metadata`, never by parsing
`requirements.txt` -- a pin file is not an installed tree.
"""
from __future__ import annotations

import importlib.metadata as md


def test_pytest_is_pinned_clear_of_pysec_2026_1845():
    assert md.version("pytest") == "9.1.1"


def test_the_scoring_sdk_is_the_version_this_services_call_shape_was_read_from():
    """`scorer/emit.py` is written against a signature and an emission shape
    read out of 0.2.8's own source, and `tests/test_emit.py` asserts both. The
    pin is named here as well so a bump is a deliberate act with a test to run
    afterwards, rather than something that arrives with a lockfile refresh.
    """
    assert md.version("opensearch-genai-observability-sdk-py") == "0.2.8"


def test_the_otel_sdk_matches_every_other_python_service_in_this_repo():
    """1.44.0 across settlement-agent, claims-agent, reviewer-ui,
    policy-decision-service and this one. A second OTel line in one repo is
    how two services start disagreeing about what a span looks like."""
    assert md.version("opentelemetry-sdk") == "1.44.0"
    assert md.version("opentelemetry-api") == "1.44.0"
