"""The M1.2 dependency bump must not move `mcp`.

`mcp_tools.py` propagates trace context by hand-building a request and calling
the low-level `ClientSession.send_request`, because `mcp==1.9.4`'s `call_tool`
has no `_meta` parameter (docs/upstream-findings.md, Finding 4). Anything that
drags `mcp` to 2.x silently changes that call path in the same milestone that
introduces a language model, which is exactly what M1.2 must not do.
"""
from __future__ import annotations

import importlib.metadata as md

import pytest


def test_mcp_is_still_pinned_to_the_version_mcp_tools_was_verified_against():
    assert md.version("mcp") == "1.9.4"


def test_pydantic_ai_is_importable_and_at_the_verified_version():
    assert md.version("pydantic-ai-slim") == "2.41.0"


def test_no_fastmcp_in_this_service():
    # fastmcp arriving here means the [mcp] extra crept in and dragged mcp 2.x.
    with pytest.raises(md.PackageNotFoundError):
        md.version("fastmcp-slim")
