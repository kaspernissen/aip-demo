"""`svid.py`'s caching and failure contract, with no cluster and no network.

`svid.py` exists so workload identity is testable without SPIRE running. The
one seam these tests replace is `svid._fetch_from_workload_api`, an async
callable taking an audience and returning `(token, expiry_epoch_seconds)` --
epoch seconds because that is what the pinned `spiffe==0.3.1` client's
`JwtSvid.expiry` actually is (`spiffe/svid/jwt_svid.py`: `expiry: int`,
"Date and time in UTC specifying expiry date"), not a datetime and not a
duration.

The helpers below are defined here rather than imported. There is no shared
fixture module in this service's suite (see the neighbouring
tests/test_mcp_tools.py, which defines its own fakes inline for the same
reason) and a fixture living in another test file would not be importable
anyway.

Branch coverage rather than plausible-edit coverage. `fetch_jwt_svid`'s
decision is a single comparison against a 30s margin, and the interesting
cases are the ones either side of it plus the boundary itself, not a list of
edits someone might make:

- comfortably inside the margin  -> `test_second_call_is_served_from_cache`
- comfortably outside it         -> `test_a_token_near_expiry_is_refetched`
- exactly on it                  -> `test_a_token_expiring_exactly_at_the_margin_is_refetched`
- already expired                -> `test_an_already_expired_token_is_refetched`
- a different cache key          -> `test_a_different_audience_is_fetched_separately`

Concrete regression checks, so each test names the change that breaks it:

- Delete the cache write in `fetch_jwt_svid` ->
  `test_second_call_is_served_from_cache` fails (two calls reach the API).
- Change the comparison from `>` to `>=` ->
  `test_a_token_expiring_exactly_at_the_margin_is_refetched` fails.
- Drop `_REFRESH_MARGIN_SECONDS` to 0 (serve any unexpired token) ->
  `test_a_token_near_expiry_is_refetched` fails.
- Key the cache on nothing (a single module-level slot) ->
  `test_a_different_audience_is_fetched_separately` fails.
- Remove the `except Exception -> SvidUnavailable` wrapper ->
  `test_workload_api_failure_raises_svid_unavailable` fails with the raw
  OSError.
"""
from __future__ import annotations

import time

import pytest

import svid
from svid import SvidUnavailable, fetch_jwt_svid


@pytest.fixture(autouse=True)
def _clear_the_module_cache():
    """The cache is module-level state, so it survives between tests in the
    same process. Clearing it before *and* after each test keeps these tests
    order-independent, and stops a cached token leaking into whatever runs
    next in the suite."""
    svid._cache.clear()
    yield
    svid._cache.clear()


def _fake_api(token: str, expires_in: float):
    """An async stand-in for `_fetch_from_workload_api` that always hands back
    the same token, expiring `expires_in` seconds from when it is called."""

    async def _fetch(audience: str) -> tuple[str, float]:
        return token, time.time() + expires_in

    return _fetch


def _counting_api(calls: list, token: str, expires_in: float):
    """Same, but records each call's audience in `calls` so a test can count
    how many times the Workload API was actually reached."""

    async def _fetch(audience: str) -> tuple[str, float]:
        calls.append(audience)
        return token, time.time() + expires_in

    return _fetch


async def test_fetch_returns_the_token_from_the_workload_api(monkeypatch):
    monkeypatch.setattr(svid, "_fetch_from_workload_api", _fake_api("token-abc", expires_in=600))
    assert await fetch_jwt_svid("agentgateway") == "token-abc"


async def test_second_call_is_served_from_cache(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(svid, "_fetch_from_workload_api", _counting_api(calls, "token-abc", 600))
    await fetch_jwt_svid("agentgateway")
    await fetch_jwt_svid("agentgateway")
    assert len(calls) == 1, "the second call should not hit the Workload API"


async def test_a_token_near_expiry_is_refetched(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(svid, "_fetch_from_workload_api", _counting_api(calls, "token-abc", 10))
    await fetch_jwt_svid("agentgateway")
    await fetch_jwt_svid("agentgateway")
    assert len(calls) == 2, "a token expiring in 10s is inside the 30s refresh margin"


async def test_a_token_expiring_exactly_at_the_margin_is_refetched(monkeypatch):
    """The boundary itself, pinned so the comparison cannot silently flip from
    `>` to `>=`. A token whose remaining life *equals* the margin has no
    margin left, so it must be refetched.

    `expires_in` is set to the margin exactly and the clock is frozen, because
    with a real clock the microseconds spent between the two calls would push
    the token below the boundary and the test would pass for the wrong
    reason -- it would no longer be testing the boundary at all."""
    frozen = time.time()
    monkeypatch.setattr(svid.time, "time", lambda: frozen)
    calls: list[str] = []
    monkeypatch.setattr(
        svid,
        "_fetch_from_workload_api",
        _counting_api(calls, "token-abc", svid._REFRESH_MARGIN_SECONDS),
    )
    await fetch_jwt_svid("agentgateway")
    await fetch_jwt_svid("agentgateway")
    assert len(calls) == 2, (
        "remaining life exactly equal to the refresh margin is not more than "
        "the margin, so the cached token must not be served"
    )


async def test_an_already_expired_token_is_refetched(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(svid, "_fetch_from_workload_api", _counting_api(calls, "token-abc", -1))
    await fetch_jwt_svid("agentgateway")
    await fetch_jwt_svid("agentgateway")
    assert len(calls) == 2


async def test_a_different_audience_is_fetched_separately(monkeypatch):
    """The cache is keyed by audience. A JWT-SVID is only valid for the
    audience it was minted for, so serving audience B a token minted for
    audience A would produce a token the verifier rejects -- and the cache
    would keep producing it until expiry."""
    calls: list[str] = []
    monkeypatch.setattr(svid, "_fetch_from_workload_api", _counting_api(calls, "token-abc", 600))
    await fetch_jwt_svid("spiffe://northstar.local/ns/northstar-platform/sa/agentgateway")
    await fetch_jwt_svid("spiffe://northstar.local/ns/northstar-tools/sa/payments-mcp")
    assert calls == [
        "spiffe://northstar.local/ns/northstar-platform/sa/agentgateway",
        "spiffe://northstar.local/ns/northstar-tools/sa/payments-mcp",
    ]


async def test_workload_api_failure_raises_svid_unavailable(monkeypatch):
    async def boom(audience):
        raise OSError("no socket")

    monkeypatch.setattr(svid, "_fetch_from_workload_api", boom)
    with pytest.raises(SvidUnavailable):
        await fetch_jwt_svid("agentgateway")


async def test_a_failed_fetch_does_not_poison_the_cache(monkeypatch):
    """After a failure the next call must try again rather than serve a
    stale entry or a half-written one."""
    async def boom(audience):
        raise OSError("no socket")

    monkeypatch.setattr(svid, "_fetch_from_workload_api", boom)
    with pytest.raises(SvidUnavailable):
        await fetch_jwt_svid("agentgateway")
    assert svid._cache == {}

    calls: list[str] = []
    monkeypatch.setattr(svid, "_fetch_from_workload_api", _counting_api(calls, "token-abc", 600))
    assert await fetch_jwt_svid("agentgateway") == "token-abc"
    assert calls == ["agentgateway"]


async def test_svid_unavailable_preserves_the_underlying_cause(monkeypatch):
    """The operator needs to know *why* the Workload API was unreachable -- a
    missing socket and a permission error are different problems -- so the
    original exception must survive as __cause__ rather than being flattened
    into a generic message."""
    original = OSError("no socket")

    async def boom(audience):
        raise original

    monkeypatch.setattr(svid, "_fetch_from_workload_api", boom)
    with pytest.raises(SvidUnavailable) as excinfo:
        await fetch_jwt_svid("agentgateway")
    assert excinfo.value.__cause__ is original
