"""identity.py: reviewer-ui's own workload SPIFFE ID.

Not a JWT-SVID for some audience (svid.py's job in the other two agents,
scoped to calling agentgateway) -- reviewer-ui calls no audience-gated
service. This reads the workload's own X.509 SVID purely to report a real,
measured `review.reviewer.spiffe_id` on the `review.decision` span, rather
than a string this process merely believes it was deployed with.

Same seam-replacement testing technique as agents/settlement-agent/svid.py:
`_fetch_from_workload_api` is the one function that touches the real SPIFFE
Workload API, and every test here replaces it.
"""
from __future__ import annotations

import pytest

import identity
from identity import IdentityUnavailable, workload_spiffe_id


@pytest.fixture(autouse=True)
def _clear_cache():
    identity._cached_spiffe_id = None
    yield
    identity._cached_spiffe_id = None


async def test_workload_spiffe_id_returns_what_the_workload_api_reports(monkeypatch):
    async def fake():
        return "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"

    monkeypatch.setattr(identity, "_fetch_from_workload_api", fake)
    assert (
        await workload_spiffe_id()
        == "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"
    )


async def test_workload_spiffe_id_is_cached_after_the_first_fetch(monkeypatch):
    calls = []

    async def fake():
        calls.append(1)
        return "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"

    monkeypatch.setattr(identity, "_fetch_from_workload_api", fake)
    await workload_spiffe_id()
    await workload_spiffe_id()
    assert len(calls) == 1


async def test_workload_api_failure_raises_identity_unavailable(monkeypatch):
    async def boom():
        raise OSError("no socket")

    monkeypatch.setattr(identity, "_fetch_from_workload_api", boom)
    with pytest.raises(IdentityUnavailable):
        await workload_spiffe_id()


async def test_a_failed_fetch_does_not_poison_the_cache(monkeypatch):
    async def boom():
        raise OSError("no socket")

    monkeypatch.setattr(identity, "_fetch_from_workload_api", boom)
    with pytest.raises(IdentityUnavailable):
        await workload_spiffe_id()

    async def fake():
        return "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"

    monkeypatch.setattr(identity, "_fetch_from_workload_api", fake)
    assert (
        await workload_spiffe_id()
        == "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"
    )


async def test_identity_unavailable_preserves_the_underlying_cause(monkeypatch):
    original = OSError("no socket")

    async def boom():
        raise original

    monkeypatch.setattr(identity, "_fetch_from_workload_api", boom)
    with pytest.raises(IdentityUnavailable) as excinfo:
        await workload_spiffe_id()
    assert excinfo.value.__cause__ is original
