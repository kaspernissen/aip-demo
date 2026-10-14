"""identity.py: reviewer-ui's own workload identity.

Two seams, both replaced here: `_fetch_from_workload_api` (the X.509 SPIFFE
ID that `review.decision` reports) and `_fetch_jwt_from_workload_api` (the
JWT-SVID, scoped to an audience, that the reviewer-ui presents to
agentgateway to release an approved payment). Same technique as
agents/settlement-agent/tests/test_svid.py. No cluster, no network.
"""
from __future__ import annotations

import time

import pytest

import identity
from identity import IdentityUnavailable, workload_spiffe_id


@pytest.fixture(autouse=True)
def _clear_cache():
    identity._cached_spiffe_id = None
    identity._jwt_cache.clear()
    yield
    identity._cached_spiffe_id = None
    identity._jwt_cache.clear()


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


# --- the JWT-SVID -----------------------------------------------------------

AUDIENCE = "spiffe://northstar.local/ns/northstar-platform/sa/agentgateway"


def _jwt_seam(monkeypatch, *, expiry_in: float, calls: list, token="tok"):
    async def fake(audience):
        calls.append(audience)
        return f"{token}-{len(calls)}", time.time() + expiry_in

    monkeypatch.setattr(identity, "_fetch_jwt_from_workload_api", fake)


async def test_a_jwt_svid_is_served_from_cache_while_comfortably_valid(monkeypatch):
    calls: list = []
    _jwt_seam(monkeypatch, expiry_in=300, calls=calls)
    first = await identity.fetch_jwt_svid(AUDIENCE)
    second = await identity.fetch_jwt_svid(AUDIENCE)
    assert first == second == "tok-1"
    assert calls == [AUDIENCE]


async def test_a_jwt_svid_near_expiry_is_refetched(monkeypatch):
    calls: list = []
    _jwt_seam(monkeypatch, expiry_in=10, calls=calls)
    await identity.fetch_jwt_svid(AUDIENCE)
    await identity.fetch_jwt_svid(AUDIENCE)
    assert len(calls) == 2


async def test_a_different_audience_is_fetched_separately(monkeypatch):
    calls: list = []
    _jwt_seam(monkeypatch, expiry_in=300, calls=calls)
    await identity.fetch_jwt_svid(AUDIENCE)
    await identity.fetch_jwt_svid("spiffe://other")
    assert calls == [AUDIENCE, "spiffe://other"]


async def test_a_jwt_failure_raises_identity_unavailable_and_caches_nothing(monkeypatch):
    async def boom(audience):
        raise OSError("no socket")

    monkeypatch.setattr(identity, "_fetch_jwt_from_workload_api", boom)
    with pytest.raises(IdentityUnavailable):
        await identity.fetch_jwt_svid(AUDIENCE)
    assert identity._jwt_cache == {}
