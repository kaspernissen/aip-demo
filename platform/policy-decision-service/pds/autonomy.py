"""Resolving the autonomy level, and nothing else.

Deliberately free of gRPC and of the rule engine: `server.py` adapts the wire
to this, `rules.py` never learns a flag exists.

Ruling M5-R4: the client `server.py` builds is configured explicitly
(`host="localhost", port=8013`) rather than left to the library's own
default -- `FLAGD_HOST`/`FLAGD_PORT` below are that configuration, named once
so `server.py` and this module's own guard test
(`tests/test_autonomy.py::test_the_configured_port_matches_the_featureflagsource_resource`)
cite the same constant rather than two numbers that could drift apart. Port
8013 is not arbitrary: it is what
`deploy/platform/openfeature/featureflagsource.yaml` configures the Open
Feature Operator to render the flagd sidecar's gRPC evaluation listener onto,
measured off the running pod in Task 2 and asserted against that resource
here rather than merely believed.

Ruling M5-R4 also requires proving the resolver is actually RPC, not
in-process or file resolution silently substituted underneath an
unconsulted host/port pair -- `openfeature-provider-flagd==0.5.2`'s
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

# M5-R4: explicit, not the library default. `DEFAULT_PORT_RPC` in
# openfeature-provider-flagd's own `config.py` happens to already be 8013,
# but relying on that default is exactly the silent-drift risk the ruling
# names -- passing it here means a future upstream default change cannot
# move this service off the sidecar's actual port without this file
# changing too.
FLAGD_HOST = "localhost"
FLAGD_PORT = 8013

# The fail-closed position. Not a fallback in the ordinary sense: every path
# that cannot establish a level lands here, and the rule table's most
# restrictive allowance is keyed by it.
RESTRICTIVE_LEVEL = "assisted"


@dataclass(frozen=True)
class AutonomyResolution:
    """What a flag lookup told us -- or, on the fail-closed path, could not.

    Ruling M5-R5: seven fields, not five. `provider_name` and `set_id` join
    `level`, `reason`, `variant`, `version` and `error_type` because Task 4
    emits all seven on a semantic-convention log record and must not invent
    two of them at the call site. `provider_name` comes off the client's own
    provider metadata (always present -- `FlagdProvider.get_metadata()`
    returns the literal `Metadata(name="FlagdProvider")`, measured in
    `openfeature/contrib/provider/flagd/provider.py`, so this field is never
    None for a real client). `set_id` and `version` come from
    `details.flag_metadata`, which the OpenFeature OTel hook
    (`openfeature-hooks-opentelemetry`) declares constants for
    (`feature_flag.set.id`, `feature_flag.version`) but never reads or emits
    -- confirmed by reading `TracingHook.finally_after`, which builds its
    span event from `flag_key`/`value`/`reason`/`variant`/`context.id`/
    `provider.name` only. If a field genuinely is not available, it carries
    `None` rather than an invented value; see `tests/test_autonomy.py` for
    what was actually measured coming back from the live sidecar (both
    `None`, for a reason logged there, not assumed here).
    """
    level: str
    reason: str
    variant: str | None = None
    version: str | None = None
    error_type: str | None = None
    provider_name: str | None = None
    set_id: str | None = None


def _provider_name(client) -> str | None:
    """Best-effort, never load-bearing for the decision itself. A `client`
    with no `.provider` (the fail-closed test's double, or any other stand-in
    that only implements `get_string_details`) yields `None` honestly rather
    than raising -- this is diagnostic metadata, not a policy input."""
    try:
        return client.provider.get_metadata().name
    except Exception:  # noqa: BLE001 -- diagnostic only, see the docstring
        return None


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
    provider_name = _provider_name(client)
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
            provider_name=provider_name,
        )

    metadata = details.flag_metadata or {}
    error_type = (
        str(details.error_code) if details.error_code is not None else None
    )
    return AutonomyResolution(
        level=details.value,
        reason=str(details.reason or "UNKNOWN"),
        variant=details.variant,
        # The hook does NOT emit these (see the class docstring) -- read
        # ourselves, because §7's audit discipline depends on them.
        version=metadata.get("version"),
        error_type=error_type,
        provider_name=provider_name,
        set_id=metadata.get("flagSetId"),
    )
