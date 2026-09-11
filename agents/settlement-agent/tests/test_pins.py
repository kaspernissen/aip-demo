"""Task 7 moved `mcp` to 1.28.1 (clearing PYSEC-2026-1617/-1618/-3482/-3483),
deliberately isolated from the model-call work in Tasks 1-4 so a propagation
regression would have exactly one suspect.

`mcp_tools.py` still propagates trace context by hand-building a request and
calling the low-level `ClientSession.send_request`, unchanged: `mcp==1.28.1`
gained a merged `call_tool(meta=...)` convenience method
(docs/upstream-findings.md, Finding 4), but adopting it is explicitly left as
recorded, available future work, not taken here — this task verifies the
*existing* `send_request` path still propagates at the new version, rather
than replacing it.

**2026-09-11 (M3 remaining-bumps, step 1): `mcp` is now 2.2.0**, and one
sentence that used to close the paragraph above is dead. It read: "`mcp`
staying below `2.0.0` also keeps PydanticAI's `[mcp]` extra unsatisfiable
against this pin (see requirements.txt), on purpose: bumping to pick up the
fix and adopting `MCPToolset` are independent decisions, and only the former
is this task's job." The two decisions are still independent and `MCPToolset`
is still not adopted — but the version number no longer enforces that, so
`test_no_fastmcp_in_this_service` below is the whole of the enforcement now.
The `send_request` path is still the one `_call` takes, still hand-building
`params._meta`, and it is still what this file exists to pin.

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
    assert md.version("mcp") == "2.2.0"


def test_mcp_types_is_pinned_in_lock_step_with_mcp():
    # v2 split the wire types into their own distribution, "published in
    # lock-step with `mcp`" (upstream's v2.0.0 release notes) and pinned exactly
    # by `mcp`'s own metadata. `mcp.types` is an alias for it, so a drift here
    # would be a drift in every request this service builds.
    assert md.version("mcp-types") == "2.2.0"


def test_pydantic_ai_is_importable_and_at_the_verified_version():
    # 2026-09-11 (M3 remaining-bumps, step 2): 2.41.0 -> 2.42.0. "Verified"
    # here means the `gen_ai.*` inventory `docs/telemetry-contract.md`
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
    assert md.version("pytest") == "9.0.3"


def test_pytest_asyncio_is_still_pinned_to_the_version_pytest_9_requires():
    assert md.version("pytest-asyncio") == "1.4.0"


# M2.1 Task 4 adds the SPIFFE Workload API client. Same rationale as the pytest
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
