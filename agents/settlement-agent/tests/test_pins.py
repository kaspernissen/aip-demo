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
