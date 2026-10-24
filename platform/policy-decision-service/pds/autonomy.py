"""Resolving the autonomy level, and nothing else.

Deliberately free of gRPC and of the rule engine: `server.py` adapts the wire
to this, `rules.py` never learns a flag exists.

The client `server.py` builds is configured explicitly
(`host="localhost", port=8013`) rather than left to the library's own
default -- `FLAGD_HOST`/`FLAGD_PORT` below are that configuration, named once
so `server.py` and this module's own guard test
(`tests/test_autonomy.py::test_the_configured_port_matches_the_featureflagsource_resource`)
cite the same constant rather than two numbers that could drift apart. Port
8013 is not arbitrary: it is what
`deploy/platform/openfeature/featureflagsource.yaml` configures the Open
Feature Operator to render the flagd sidecar's gRPC evaluation listener onto,
measured off the running pod and asserted against that resource here rather
than merely believed.

This also requires proving the resolver is actually RPC, not in-process or
file resolution silently substituted underneath an unconsulted host/port
pair -- `openfeature-provider-flagd==0.5.2`'s
`FlagdProvider` picks its resolver from `FLAGD_RESOLVER` when no
`resolver_type` is passed (`openfeature/contrib/provider/flagd/config.py`),
defaulting to RPC only when that variable is unset. Nothing in
`deploy/k8s/policy-decision-service.yaml` sets it, and
`tests/test_autonomy.py` asserts the resulting resolver class is
`GrpcResolver`, not merely that construction succeeded.
"""
from __future__ import annotations

from dataclasses import dataclass

from openfeature.evaluation_context import EvaluationContext

FLAG_KEY = "autonomy-level"
CONTEXT_KEY = "spiffeId"

# Explicit, not the library default. `DEFAULT_PORT_RPC` in
# openfeature-provider-flagd's own `config.py` happens to already be 8013,
# but relying on that default is exactly the silent-drift risk explicit
# configuration guards against -- passing it here means a future upstream
# default change cannot move this service off the sidecar's actual port
# without this file changing too.
FLAGD_HOST = "localhost"
FLAGD_PORT = 8013

# The fail-closed position. Not a fallback in the ordinary sense: every path
# that cannot establish a level lands here, and the rule table's most
# restrictive allowance is keyed by it.
RESTRICTIVE_LEVEL = "assisted"


@dataclass(frozen=True)
class AutonomyResolution:
    """What a flag lookup told us -- or, on the fail-closed path, could not.

    `version` comes from `details.flag_metadata`, which `TracingHook` does
    not read. It measures `None` against the live sidecar today, because the
    `autonomy-level` flag defines no metadata; a missing field stays `None`
    rather than an invented value.
    """
    level: str
    reason: str
    variant: str | None = None
    version: str | None = None
    error_type: str | None = None


def resolve_autonomy(caller: str | None, client) -> AutonomyResolution:
    """The autonomy level for `caller`, resolved through `client`.

    `client` is an OpenFeature client (or, in tests, anything with the same
    `get_string_details` shape) -- this function never constructs one itself,
    so a test can hand it a double that raises and a fail-closed outage is
    exercised without a network.

    The `except` below is defence in depth, not the primary fail-closed
    mechanism: the real OpenFeature SDK already catches a provider's own
    exceptions internally and returns a `FlagEvaluationDetails` with
    `reason=ERROR` and `value=default_value` (measured against the live
    sidecar through a broken connection) rather than propagating. This
    function's `try` covers the client shape Step 1's test uses directly, and
    covers this function against a future SDK version that stops doing that.
    """
    try:
        details = client.get_string_details(
            FLAG_KEY, RESTRICTIVE_LEVEL,
            evaluation_context=EvaluationContext(
                targeting_key=caller or "", attributes={CONTEXT_KEY: caller or ""}
            ),
        )
    except Exception as exc:  # noqa: BLE001 -- see the module docstring
        return AutonomyResolution(
            level=RESTRICTIVE_LEVEL,
            reason="ERROR",
            error_type=type(exc).__name__,
        )

    metadata = details.flag_metadata or {}
    error_type = (
        str(details.error_code) if details.error_code is not None else None
    )
    # Fail-closed on the non-raising error path must be enforced HERE, not
    # merely observed to hold because of how
    # `openfeature-provider-flagd==0.5.2` currently behaves. The module
    # docstring traces four SDK paths that all set `value=default_value`
    # (i.e. `RESTRICTIVE_LEVEL`) whenever `error_code` is present -- that is a
    # property of the library today, not a guarantee this function enforces
    # itself. Forcing the level down here whenever `error_type` is set means
    # a future SDK version (or any other `client` shape a test hands in) that
    # ever returned a provider-chosen or contract-chosen value alongside an
    # error code cannot widen what is permitted through this function.
    level = RESTRICTIVE_LEVEL if error_type is not None else details.value
    return AutonomyResolution(
        level=level,
        reason=str(details.reason or "UNKNOWN"),
        variant=details.variant,
        # The hook does not read this (see the class docstring).
        version=metadata.get("version"),
        error_type=error_type,
    )

