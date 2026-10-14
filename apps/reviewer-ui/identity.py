"""reviewer-ui's own workload identity.

Two things are fetched from the SPIFFE Workload API, both for the same
attested identity. The workload's *X.509* SVID is read back for one fact:
`review.decision`'s `review.reviewer.spiffe_id` attribute needs this process's
real SPIFFE ID. And a *JWT*-SVID scoped to an *audience* (`fetch_jwt_svid`,
the same shape as agents/settlement-agent/svid.py) is what the reviewer-ui
presents to agentgateway to release an approved payment: the PDS permits that
identity under `human-approved.execute`, and only for a payment the ledger
shows awaiting approval.

The identity is real for the same reason settlement-agent's is (svid.py's own
docstring): the spiffe-csi-driver bind-mounts the spire-agent's Workload API
socket into this pod, and the agent's Kubernetes workload attestor derives the
identity from the kernel up -- namespace and ServiceAccount, checked against
`deploy/platform/spire/registration.yaml`'s template. There is nothing in
this pod to assemble a SPIFFE ID string from by hand; it is read from the
Workload API or not reported at all.

Same seam-replacement testing technique as svid.py: `_fetch_from_workload_api`
and `_fetch_jwt_from_workload_api` are the two functions that touch the real
Workload API, and tests/test_identity.py replaces them. The X.509 *spiffe_id*
string is stable for the pod's lifetime, so it is cached forever once fetched;
a JWT-SVID expires, so it is cached per audience until 30 s before expiry.
"""
from __future__ import annotations

import asyncio
import os
import time

from spiffe import WorkloadApiClient

# Matches deploy/k8s/reviewer-ui.yaml's SPIFFE_ENDPOINT_SOCKET, which in turn
# matches every other workload in this repo that mounts the csi.spiffe.io
# volume (see deploy/k8s/settlement-agent.yaml's own comment for how that
# path was read off the rendered spiffe/spire 0.30.2 chart).
_DEFAULT_ENDPOINT_SOCKET = "unix:///spiffe-workload-api/spire-agent.sock"

_cached_spiffe_id: str | None = None

_jwt_cache: dict[str, tuple[str, float]] = {}
_REFRESH_MARGIN_SECONDS = 30


class IdentityUnavailable(RuntimeError):
    """Raised when this workload's own SPIFFE ID could not be read from the
    Workload API.

    Wraps every transport-level failure the same way svid.py's
    SvidUnavailable does -- a missing socket, a connection refused, a gRPC
    deadline -- because to the caller they are one condition: this workload
    cannot currently prove its own identity, and `review.decision`'s
    `review.reviewer.spiffe_id` attribute must not be filled with a guess.
    """


async def _fetch_from_workload_api() -> str:
    """Read this workload's own X.509 SVID and return its SPIFFE ID as a
    string ("spiffe://trust-domain/ns/.../sa/...").

    Pushed to a worker thread for the same reason svid.py's own fetch is:
    `spiffe`'s client is synchronous (built on blocking grpcio), and this
    coroutine runs on the event loop that is also serving uvicorn's requests.
    """
    socket_path = os.getenv("SPIFFE_ENDPOINT_SOCKET", _DEFAULT_ENDPOINT_SOCKET)

    def _fetch() -> str:
        client = WorkloadApiClient(socket_path=socket_path)
        try:
            svid = client.fetch_x509_svid()
            return str(svid.spiffe_id)
        finally:
            # No context-manager protocol on this class (svid.py's own note,
            # verified against the same installed 0.3.1): close by hand or
            # leak one gRPC channel per fetch.
            client.close()

    return await asyncio.to_thread(_fetch)


async def workload_spiffe_id() -> str:
    """Return this workload's own SPIFFE ID, fetched once and cached for the
    life of the process.

    Raises IdentityUnavailable if the Workload API cannot be reached.
    """
    global _cached_spiffe_id
    if _cached_spiffe_id is not None:
        return _cached_spiffe_id

    try:
        spiffe_id = await _fetch_from_workload_api()
    except IdentityUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 -- see IdentityUnavailable's docstring
        raise IdentityUnavailable(
            f"could not read this workload's own SPIFFE ID from the SPIFFE "
            f"Workload API: {exc}"
        ) from exc

    # Written only after a successful fetch, so a failure leaves the cache
    # empty rather than storing a half-result the next call would serve.
    _cached_spiffe_id = spiffe_id
    return _cached_spiffe_id


async def _fetch_jwt_from_workload_api(audience: str) -> tuple[str, float]:
    socket_path = os.getenv("SPIFFE_ENDPOINT_SOCKET", _DEFAULT_ENDPOINT_SOCKET)

    def _fetch() -> tuple[str, float]:
        client = WorkloadApiClient(socket_path=socket_path)
        try:
            svid = client.fetch_jwt_svid(audience={audience})
            return svid.token, float(svid.expiry)
        finally:
            client.close()

    return await asyncio.to_thread(_fetch)


async def fetch_jwt_svid(audience: str) -> str:
    """A JWT-SVID for `audience`, cached until 30 s before it expires. The
    token is never logged or printed."""
    cached = _jwt_cache.get(audience)
    if cached is not None and cached[1] - time.time() > _REFRESH_MARGIN_SECONDS:
        return cached[0]
    try:
        token, expiry = await _fetch_jwt_from_workload_api(audience)
    except Exception as exc:  # noqa: BLE001 -- see IdentityUnavailable
        raise IdentityUnavailable(f"could not fetch a JWT-SVID for {audience!r}: {exc}") from exc
    _jwt_cache[audience] = (token, expiry)
    return token
