"""Regression test for a requirements.txt defect in the brief (A19).

See main.py / bootstrap.py module docstrings and NOTES.md for the full
writeup: the brief's Step 4/6 requirements.txt pins `a2a-sdk[telemetry]==0.3.7`
plus a handful of OTel/httpx/uvicorn packages, but `a2a.server.apps.
A2AStarletteApplication` (used in main.py, per the brief's own Step 6 sketch)
requires `starlette` and `sse-starlette` to be importable. Neither the
`telemetry` extra nor any of the brief's other pins declare them. Task 4's
settlement-agent requirements.txt happened to work anyway because
`mcp==1.9.4` — a dependency claims-agent does not have — pulls starlette in
transitively; claims-agent has no such accidental dependency; a clean install
of the brief's pin set raises:

    ImportError: Packages `starlette` and `sse-starlette` are required to use
    the `A2AStarletteApplication`. It can be added as a part of `a2a-sdk`
    optional dependencies, `a2a-sdk[http-server]`.

verified directly against `A2AStarletteApplication(...)`. Fixed by pinning
`a2a-sdk[telemetry,http-server]==0.3.7` plus explicit `starlette==1.6.0`/
`sse-starlette==3.4.11` in requirements.txt.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault(
    "AGENT_CARD_PATH", str(REPO_ROOT / "contracts" / "agent-cards" / "claims-agent.json")
)

import main  # noqa: E402


def test_build_app_constructs_a_starlette_app_without_import_error():
    """Fails with the ImportError quoted above if requirements.txt reverts to
    the brief's original pin set and this venv is reinstalled clean."""
    app = main.build_app()
    assert type(app).__name__ == "Starlette"
