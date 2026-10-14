"""Regression test for a requirements.txt dependency gap.

See main.py / bootstrap.py module docstrings and NOTES.md §2 for the full
writeup: pinning `a2a-sdk[telemetry]==0.3.7` plus a handful of
OTel/httpx/uvicorn packages is not enough, because `a2a.server.apps.
A2AStarletteApplication` (used in main.py) requires `starlette` and
`sse-starlette` to be importable, and neither the `telemetry` extra nor
those other pins declare them. settlement-agent's requirements.txt happens
to work anyway because `mcp==1.9.4` — a dependency claims-agent does not
have — pulls starlette in transitively; claims-agent has no such accidental
dependency.

Fixed by pinning explicit `starlette==1.6.0`/`sse-starlette==3.4.11` in
requirements.txt (not the `a2a-sdk[http-server]` extra — that also drags in
an unused `fastapi`).

**What actually fails, and why:** `main.py` imports
`opentelemetry.instrumentation.starlette` (required — see NOTES.md §4)
*before* `A2AStarletteApplication` is ever constructed, and that module
itself does `from starlette import applications` at import time. So
reverting requirements.txt to a bare pin set and reinstalling the venv
clean does **not** reach `A2AStarletteApplication`'s own `ImportError`
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

Not hermetic, deliberately: importing `main` runs
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
    'starlette' (see module docstring) if requirements.txt reverts to a bare
    pin set and this venv is reinstalled clean."""
    app = main.build_app()
    assert type(app).__name__ == "Starlette"


def test_the_card_declares_streaming():
    import json
    card = json.loads((REPO_ROOT / "contracts" / "agent-cards" / "claims-agent.json").read_text())
    assert card["capabilities"]["streaming"] is True
