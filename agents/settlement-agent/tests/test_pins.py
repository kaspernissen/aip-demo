"""Task 7 moved `mcp` to 1.28.1 (clearing PYSEC-2026-1617/-1618/-3482/-3483),
deliberately isolated from the model-call work in Tasks 1-4 so a propagation
regression would have exactly one suspect.

`mcp_tools.py` still propagates trace context by hand-building a request and
calling the low-level `ClientSession.send_request`, unchanged: `mcp==1.28.1`
gained a merged `call_tool(meta=...)` convenience method
(docs/upstream-findings.md, Finding 4), but adopting it is explicitly left as
recorded, available future work, not taken here — this task verifies the
*existing* `send_request` path still propagates at the new version, rather
than replacing it. `mcp` staying below `2.0.0` also keeps PydanticAI's `[mcp]`
extra unsatisfiable against this pin (see requirements.txt), on purpose:
bumping to pick up the fix and adopting `MCPToolset` are independent
decisions, and only the former is this task's job.

Final-review fix round (SF-2): this file pinned `mcp` and `pydantic-ai-slim`
via `importlib.metadata` but not `pytest`/`pytest-asyncio`, so the pytest
advisory (PYSEC-2026-1845) that Task 7 cleared here could have silently
drifted back to `8.3.4` — exactly the "no service has any advisory gate"
mechanism gap the final review named — with nothing failing. The two
assertions below close that gap for this service; they are not a general
advisory scanner (that remains a separate, unbuilt piece of work), only a
tripwire against this exact pair silently downgrading.
"""
from __future__ import annotations

import importlib.metadata as md

import pytest


def test_mcp_is_still_pinned_to_the_version_mcp_tools_was_verified_against():
    assert md.version("mcp") == "1.28.1"


def test_pydantic_ai_is_importable_and_at_the_verified_version():
    assert md.version("pydantic-ai-slim") == "2.41.0"


def test_no_fastmcp_in_this_service():
    # fastmcp arriving here means the [mcp] extra crept in and dragged mcp 2.x.
    with pytest.raises(md.PackageNotFoundError):
        md.version("fastmcp-slim")


def test_pytest_is_still_pinned_to_the_version_that_cleared_pysec_2026_1845():
    assert md.version("pytest") == "9.0.3"


def test_pytest_asyncio_is_still_pinned_to_the_version_pytest_9_requires():
    assert md.version("pytest-asyncio") == "1.4.0"
