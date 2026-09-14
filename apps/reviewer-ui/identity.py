"""reviewer-ui's own workload identity.

Not `svid.py`'s job (the two settlement agents' module, fetching a
*JWT*-SVID scoped to an *audience* so agentgateway can verify it on a
`tools/call`) -- reviewer-ui makes no MCP call and holds no
`EXECUTE_LIMITS` row, so there is no audience to mint one for. What this
module fetches instead is the workload's own *X.509* SVID, purely to read
back the one fact `review.decision`'s `review.reviewer.spiffe_id` attribute
needs: this process's real, attested SPIFFE ID.

The identity is real for the same reason settlement-agent's is (svid.py's own
docstring): the spiffe-csi-driver bind-mounts the spire-agent's Workload API
socket into this pod, and the agent's Kubernetes workload attestor derives the
identity from the kernel up -- namespace and ServiceAccount, checked against
`deploy/platform/spire/registration.yaml`'s template. There is nothing in
this pod to assemble a SPIFFE ID string from by hand; it is read from the
Workload API or not reported at all.

Same seam-replacement testing technique as svid.py: `_fetch_from_workload_api`
is the one function that touches the real Workload API, and tests/test_identity.py
replaces it. Unlike svid.py's JWT cache there is no expiry to track here --
an X.509 SVID rotates its certificate material periodically, but the
*spiffe_id* string it carries is stable for the pod's lifetime, so this
caches the string forever once fetched rather than re-checking a clock.
"""
from __future__ import annotations

import asyncio
import os

from spiffe import WorkloadApiClient

# Matches deploy/k8s/reviewer-ui.yaml's SPIFFE_ENDPOINT_SOCKET, which in turn
# matches every other workload in this repo that mounts the csi.spiffe.io
# volume (see deploy/k8s/settlement-agent.yaml's own comment for how that
# path was read off the rendered spiffe/spire 0.30.2 chart).
_DEFAULT_ENDPOINT_SOCKET = "unix:///spiffe-workload-api/spire-agent.sock"

_cached_spiffe_id: str | None = None


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
