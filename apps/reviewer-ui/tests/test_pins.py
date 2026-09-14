"""Advisory gate for reviewer-ui, following the pattern established in
agents/settlement-agent/tests/test_pins.py (M3 Task 1 Part A extended it to
every service that lacked one; this is a new service, so it gets one from
the start rather than being added later as debt).

Not a general vulnerability scanner -- a tripwire against this exact set of
versions silently drifting, so a change to requirements.txt that is not also
reflected in a reinstalled venv is caught here rather than surfacing as an
unrelated test failure somewhere else in the suite.
"""
from __future__ import annotations

import importlib.metadata as md


def test_fastapi_is_pinned_to_the_version_this_service_was_written_against():
    assert md.version("fastapi") == "0.141.1"


def test_uvicorn_is_pinned_to_the_version_the_rest_of_the_repo_uses():
    assert md.version("uvicorn") == "0.34.0"


def test_python_multipart_is_importable_for_form_parsing():
    # FastAPI's Form() raises its own AssertionError at request time if this
    # is missing -- caught here instead, at collection.
    assert md.version("python-multipart") == "0.0.32"


def test_httpx_is_pinned_to_the_version_the_rest_of_the_repo_uses():
    assert md.version("httpx") == "0.28.1"


def test_spiffe_is_pinned_to_the_version_settlement_agents_svid_py_uses():
    assert md.version("spiffe") == "0.3.1"


def test_pytest_is_pinned():
    assert md.version("pytest") == "9.1.1"


def test_pytest_asyncio_is_pinned():
    assert md.version("pytest-asyncio") == "1.4.0"


def test_opentelemetry_sdk_matches_the_rest_of_the_repo():
    assert md.version("opentelemetry-sdk") == "1.44.0"
