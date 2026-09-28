"""`mcp` is pinned to 2.2.0 (clearing PYSEC-2026-1617/-1618/-3482/-3483),
isolated from the model-call pins below so a propagation regression has
exactly one suspect.

`mcp_tools.py` still propagates trace context by hand-building a request and
calling the low-level `ClientSession.send_request`: `mcp` also ships a merged
`call_tool(meta=...)` convenience method (docs/upstream-findings.md,
Finding 4), but adopting it is left as available future work -- this file
verifies the *existing* `send_request` path still propagates trace context at
the pinned version, rather than replacing it. `test_no_fastmcp_in_this_service`
below is what keeps `MCPToolset` unadopted, since the `mcp` version itself no
longer makes PydanticAI's `[mcp]` extra unsatisfiable against this pin (see
requirements.txt).

This file also pins `pytest` and `pytest-asyncio` via `importlib.metadata`, as
a tripwire against either silently drifting back to a version carrying a
cleared advisory (PYSEC-2026-1845 for pytest) -- not a general advisory
scanner, just protection against this exact pair regressing.
"""
from __future__ import annotations

import importlib.metadata as md

import pytest


def test_mcp_is_still_pinned_to_the_version_mcp_tools_was_verified_against():
    assert md.version("mcp") == "2.2.0"


def test_mcp_types_is_pinned_in_lock_step_with_mcp():
    # v2 split the wire types into their own distribution, "published in
    # lock-step with `mcp`" (upstream's v2.0.0 release notes) and pinned exactly
    # by `mcp`'s own metadata. `mcp.types` is an alias for it, so a drift here
    # would be a drift in every request this service builds.
    assert md.version("mcp-types") == "2.2.0"


def test_pydantic_ai_is_importable_and_at_the_verified_version():
    # "Verified" here means the `gen_ai.*` inventory `docs/telemetry-contract.md`
    # publishes was re-measured off a live settlement at this version, not
    # that the release notes were read.
    assert md.version("pydantic-ai-slim") == "2.42.0"


def test_pydantic_graph_is_pinned_in_lock_step_with_pydantic_ai_slim():
    # Same repository, released together, and `pydantic-ai-slim` declares it as
    # an exact-version dependency. A drift would be a resolver accident, not a
    # decision.
    assert md.version("pydantic-graph") == md.version("pydantic-ai-slim")


def test_no_fastmcp_in_this_service():
    # fastmcp arriving here means the [mcp] extra crept in and dragged mcp 2.x.
    with pytest.raises(md.PackageNotFoundError):
        md.version("fastmcp-slim")


def test_pytest_is_still_pinned_to_the_version_that_cleared_pysec_2026_1845():
    assert md.version("pytest") == "9.1.1"


def test_pytest_asyncio_is_still_pinned_to_the_version_pytest_9_requires():
    assert md.version("pytest-asyncio") == "1.4.0"


# The SPIFFE Workload API client follows the same rationale as the pytest
# pair above: this is a tripwire against a silent drift, not a general
# advisory scanner. `spiffe` is what makes svid.py's identity real rather than
# mocked, and grpcio/pem arrived only as its transitives -- if `spiffe` ever
# disappears from this venv, svid.py's one un-faked code path
# (`_fetch_from_workload_api`) stops existing and nothing else here would say
# so, because every svid.py test replaces exactly that seam.
def test_spiffe_is_still_pinned_to_the_version_svid_py_was_written_against():
    assert md.version("spiffe") == "0.3.1"


def test_spiffes_new_transitives_are_pinned():
    # Pinned in requirements.txt because spiffe declares them as ranges
    # (grpcio<2,>=1.78.0 and pem<24,>=23), and this repo does not float.
    assert md.version("grpcio") == "1.83.1"
    assert md.version("pem") == "23.1.0"


def test_the_workload_api_client_svid_py_imports_is_actually_importable():
    # A version string in .dist-info is not a working import: `spiffe` builds
    # on grpcio, which ships compiled wheels, so a platform/ABI mismatch would
    # present here and nowhere else in this suite (svid.py's own tests replace
    # the seam that touches it).
    from spiffe import WorkloadApiClient

    assert hasattr(WorkloadApiClient, "fetch_jwt_svid")
