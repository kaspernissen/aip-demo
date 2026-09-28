"""SPIFFE workload identity for settlement-agent.

This module exists so identity is testable without a cluster. Everything that
needs SPIRE running is behind one seam, `_fetch_from_workload_api`, which
tests/test_svid.py replaces; the caching and failure behaviour above it is
exercised keyless and networkless like the rest of this service's suite.

What the identity is. The spiffe-csi-driver bind-mounts the spire-agent's
Workload API socket into this pod (deploy/k8s/settlement-agent.yaml), and the
agent's Kubernetes workload attestor identifies the calling container from the
kernel up -- namespace and service account, checked against the registration
entry in deploy/platform/spire/registration.yaml. So the JWT-SVID this module
returns is an *attested* identity: there is no key, token or secret in this
pod for it to be derived from, and one cannot be copied out and replayed
elsewhere, because the audience scopes it to a single verifier and the socket
only ever answers with the identity of whoever is actually calling it.

This uses a JWT-SVID rather than mTLS deliberately. `docs/architecture.md`
("Workload identity: what is real and what is deferred") records why, and
records mTLS as later hardening rather than leaving the weaker bearer-token
choice silent.
"""
from __future__ import annotations

import asyncio
import os
import time

from spiffe import WorkloadApiClient

# How much life a cached token must have left before it is served. A token
# handed out with less than this remaining could expire in flight -- between
# this process reading it from the cache and agentgateway checking `exp` --
# and the resulting 401 would be indistinguishable from a real identity
# failure. 30s is the margin used here.
_REFRESH_MARGIN_SECONDS = 30

# Default matches deploy/k8s/settlement-agent.yaml's SPIFFE_ENDPOINT_SOCKET,
# which in turn matches the path the `spiffe/spire` 0.30.2 chart's own
# Workload API consumers use (read from the chart's rendered output; see that
# manifest's comment for how, and for the two similar-looking paths in the
# chart's values that are NOT this one).
_DEFAULT_ENDPOINT_SOCKET = "unix:///spiffe-workload-api/spire-agent.sock"

# audience -> (token, expiry as epoch seconds).
#
# Module-level, and deliberately not an lru_cache: entries expire on a clock,
# not on a call count, and tests need to be able to clear it.
_cache: dict[str, tuple[str, float]] = {}


class SvidUnavailable(RuntimeError):
    """Raised when a JWT-SVID could not be obtained from the Workload API.

    Wraps every transport-level failure -- socket missing (the CSI volume is
    not mounted), connection refused (spire-agent is not running or has not
    yet attested), gRPC deadline, permission denied -- because to the caller
    they are one condition: this workload currently has no provable identity.
    The original exception is preserved as `__cause__`, since a missing socket
    and a permission error are different operational problems.

    It is NOT raised for a token that verifies and is then rejected by policy.
    That is a decision, and it arrives as an MCP/HTTP error from the gateway.
    """


async def _fetch_from_workload_api(audience: str) -> tuple[str, float]:
    """Mint one JWT-SVID for `audience`, returning (token, expiry).

    The seam tests replace. Expiry is epoch seconds, because that is what
    `spiffe==0.3.1`'s `JwtSvid.expiry` is (its own docstring: "Date and time
    in UTC specifying expiry date", typed `int`) -- not a datetime, and not a
    lifetime in seconds.

    `spiffe`'s client is synchronous (it is built on blocking grpcio), so the
    call is pushed to a worker thread rather than run inline: this coroutine
    is awaited from `mcp_tools._call`, on the event loop that is also serving
    this agent's A2A endpoint, and a blocking gRPC round trip there would
    stall every other request in flight.
    """
    socket_path = os.getenv("SPIFFE_ENDPOINT_SOCKET", _DEFAULT_ENDPOINT_SOCKET)

    def _fetch() -> tuple[str, float]:
        client = WorkloadApiClient(socket_path=socket_path)
        try:
            # `audience` is a Set[str] in this client's signature, not a
            # string; passing a bare string would be iterated into a set of
            # characters and SPIRE would mint a token for a nonsense audience
            # list. Wrapped here on purpose.
            svid = client.fetch_jwt_svid(audience={audience})
            return svid.token, float(svid.expiry)
        finally:
            # No context-manager protocol on this class (checked against the
            # installed 0.3.1: it has `close`, but no `__enter__`/`__exit__`),
            # so the gRPC channel is closed by hand or it leaks one channel
            # per fetch.
            client.close()

    return await asyncio.to_thread(_fetch)


async def fetch_jwt_svid(audience: str) -> str:
    """Return a JWT-SVID for `audience`, cached until 30s before it expires.

    Raises SvidUnavailable when the Workload API cannot be reached.
    """
    cached = _cache.get(audience)
    if cached is not None:
        _token, expiry = cached
        # Strictly greater than: a token with exactly the margin left has no
        # margin left. tests/test_svid.py pins this boundary directly.
        if expiry - time.time() > _REFRESH_MARGIN_SECONDS:
            return _token

    try:
        token, expiry = await _fetch_from_workload_api(audience)
    except SvidUnavailable:
        # Already the right type; re-wrapping would bury the original cause
        # one level deeper for no gain.
        raise
    except Exception as exc:  # noqa: BLE001 -- see SvidUnavailable's docstring
        raise SvidUnavailable(
            f"could not fetch a JWT-SVID for audience {audience!r} from the "
            f"SPIFFE Workload API: {exc}"
        ) from exc

    # Written only after a successful fetch, so a failure leaves the cache as
    # it was rather than storing a half-result the next call would serve.
    _cache[audience] = (token, expiry)
    return token
