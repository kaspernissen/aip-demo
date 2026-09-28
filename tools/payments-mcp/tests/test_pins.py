"""This file is `payments-mcp`'s own `pytest`-pin tripwire, mirroring
`agents/settlement-agent/tests/test_pins.py`: `payments-mcp` pins
`pytest==9.1.1` in its own `requirements.txt` (clearing PYSEC-2026-1845), and
nothing else checks that the pin stays in place.

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
    assert md.version("pytest") == "9.1.1"


def test_opentelemetry_sdk_matches_the_rest_of_the_repo():
    """This service's `opentelemetry-sdk` pin must match its four Python
    siblings (`docs/upstream-findings.md` Finding 11 documents why the
    version matters for trace continuity): 1.44.0 inherits W3C Trace Context
    Level 2's `RANDOM_TRACE_ID` bit from an extracted remote parent, where an
    older SDK derives the injected flag byte from the sampling decision alone
    and emits `...-01` unconditionally -- and `payments-api`'s otel-go
    extractor must in turn accept a version-00 traceparent with that bit set
    (currently v1.46.0), or the two services fall out of the same trace. This
    assertion is what keeps the five services agreeing about it. The same
    shape as
    `apps/reviewer-ui/tests/test_pins.py::test_opentelemetry_sdk_matches_the_rest_of_the_repo`.
    """
    assert md.version("opentelemetry-sdk") == "1.44.0"
