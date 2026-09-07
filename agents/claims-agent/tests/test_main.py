"""Regression test for a requirements.txt defect in the brief (A19).

See main.py / bootstrap.py module docstrings and NOTES.md §2 for the full
writeup: the brief's Step 4/6 requirements.txt pins `a2a-sdk[telemetry]==0.3.7`
plus a handful of OTel/httpx/uvicorn packages, but `a2a.server.apps.
A2AStarletteApplication` (used in main.py, per the brief's own Step 6 sketch)
requires `starlette` and `sse-starlette` to be importable. Neither the
`telemetry` extra nor any of the brief's other pins declare them. Task 4's
settlement-agent requirements.txt happened to work anyway because
`mcp==1.9.4` — a dependency claims-agent does not have — pulls starlette in
transitively; claims-agent has no such accidental dependency.

Fixed by pinning explicit `starlette==1.6.0`/`sse-starlette==3.4.11` in
requirements.txt (not the `a2a-sdk[http-server]` extra — that also drags in
an unused `fastapi`, review finding N2).

**What actually fails, and why (review finding S1 — corrected here after an
earlier version of this docstring got it wrong):** `main.py` imports
`opentelemetry.instrumentation.starlette` (the A18 instrumentation, required
— NOTES.md §4) *before* `A2AStarletteApplication` is ever constructed, and
that module itself does `from starlette import applications` at import time.
So reverting requirements.txt to the brief's original pin set and
reinstalling the venv clean does **not** reach
`A2AStarletteApplication`'s own `ImportError`
("Packages `starlette` and `sse-starlette` are required to use the
`A2AStarletteApplication`...") — it fails earlier, at collection, importing
`main` itself:

    tests/test_main.py:30: in <module>   import main
    main.py:12: in <module>              from opentelemetry.instrumentation.starlette import StarletteInstrumentor
    E   ModuleNotFoundError: No module named 'starlette'

Both failure modes are real and were verified directly (a bare
`A2AStarletteApplication(...).build()` call, bypassing `main.py`'s own import
order, does raise the `A2AStarletteApplication` `ImportError` quoted above)
— this test only ever exercises the first one, because `import main` never
gets past its own `StarletteInstrumentor` import without `starlette` already
being installed.

Review finding N3 — not hermetic, deliberately: importing `main` runs
`bootstrap.configure()` as a module-level side effect (a global
`TracerProvider`/`BatchSpanProcessor` pointed at whatever
`OTEL_EXPORTER_OTLP_ENDPOINT` resolves to, plus a global
`HTTPXClientInstrumentor().instrument()`) — there is no way to call
`main.build_app()` without that running first, since `build_app` is exactly
what this test needs to exercise. No test in this suite emits a span, so no
assertion is currently order-sensitive to it; verified by running
`test_main.py` both standalone and interleaved with `test_delegation.py`
(`pytest tests/test_main.py tests/test_delegation.py -v`), same results
either order. Left as-is rather than restructured (e.g. isolating
`bootstrap.configure()` behind a fixture) — that would be a larger change to
production module structure to harden a coupling review flagged as an
observation, not a failure.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault(
    "AGENT_CARD_PATH", str(REPO_ROOT / "contracts" / "agent-cards" / "claims-agent.json")
)

import main  # noqa: E402


def test_build_app_constructs_a_starlette_app_without_import_error():
    """Fails at collection with ModuleNotFoundError: No module named
    'starlette' (see module docstring) if requirements.txt reverts to the
    brief's original pin set and this venv is reinstalled clean."""
    app = main.build_app()
    assert type(app).__name__ == "Starlette"
