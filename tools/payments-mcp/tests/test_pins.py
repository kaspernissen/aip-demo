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


def test_opentelemetry_sdk_matches_the_rest_of_the_repo():
    """2026-09-11 (M3). This service sat at `opentelemetry-sdk==1.29.0` while
    its four Python siblings were on 1.44.0, and nothing anywhere said so.

    `docs/upstream-findings.md` Finding 11 recorded that pin as load-bearing
    by accident: 1.29.0 derives the injected flag byte from the sampling
    decision alone and emits `...-01` unconditionally, whereas 1.44.0 inherits
    W3C Trace Context Level 2's `RANDOM_TRACE_ID` bit from an extracted remote
    parent -- and `payments-api`'s then-pinned otel-go v1.41.0 discarded any
    version-00 traceparent above `0x02` outright. Bumping this line was
    therefore a trace-fragmentation risk that surfaced only in an expensive
    cluster-dependent e2e assertion about a missing span.

    payments-api is on otel-go v1.46.0 now, whose extractor accepts `-03`, so
    the bump was taken and this assertion is what keeps the five services
    agreeing about it. The same shape as
    `apps/reviewer-ui/tests/test_pins.py::test_opentelemetry_sdk_matches_the_rest_of_the_repo`.
    """
    assert md.version("opentelemetry-sdk") == "1.44.0"
