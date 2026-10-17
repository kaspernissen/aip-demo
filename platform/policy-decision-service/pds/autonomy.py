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
from time import time_ns

from openfeature.evaluation_context import EvaluationContext
from opentelemetry.semconv.attributes.error_attributes import ERROR_TYPE
from opentelemetry.semconv._incubating.attributes.feature_flag_attributes import (
    FEATURE_FLAG_CONTEXT_ID,
    FEATURE_FLAG_KEY,
    FEATURE_FLAG_PROVIDER_NAME,
    FEATURE_FLAG_RESULT_REASON,
    FEATURE_FLAG_RESULT_VALUE,
    FEATURE_FLAG_RESULT_VARIANT,
    FEATURE_FLAG_SET_ID,
    FEATURE_FLAG_VERSION,
)

from pds import telemetry

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

    Seven fields, not five. `provider_name` and `set_id` join `level`,
    `reason`, `variant`, `version` and `error_type` because this service
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
        # The hook does NOT emit these (see the class docstring) -- read
        # ourselves, because §7's audit discipline depends on them.
        version=metadata.get("version"),
        error_type=error_type,
        provider_name=provider_name,
        set_id=metadata.get("flagSetId"),
    )


# The second of "two records per evaluation." Signal #1 is the
# `feature_flag.evaluation` SPAN EVENT `TracingHook` already puts on the
# active span the moment `resolve_autonomy` above calls
# `client.get_string_details` -- that is registered, not written by us, and
# `tests/test_autonomy.py::
# test_the_tracing_hook_puts_a_feature_flag_evaluation_event_on_the_active_span`
# proves it lands. Signal #2 is this function: a genuine LOG RECORD, sent
# through the logs pipeline `pds/telemetry.py`'s `configure()` wires,
# carrying the full seven-field set -- including `feature_flag.set.id` and
# `feature_flag.version`, which the hook declares constants for and never
# emits (its own `EventAttributes` class names them; `finally_after` never
# reads `flag_metadata` to fill them in).
#
# THE TENSION THIS DOES NOT PAPER OVER: `resolution.version` (and `.set_id`)
# measure `None` for every real evaluation against this deployment --
# `featureflag.yaml`'s `autonomy-level` flag defines no `flag_metadata`
# (measured `{}` coming back from the live sidecar). Per this repo's
# standing rule that a missing value is absent, never zero or invented (see
# `test_a_missing_value_is_absent_from_the_span_not_zero`), both are OMITTED
# from the attribute dict below when `None` -- exactly the convention
# `server.py` already applies to `ATTR_FLAG_VERSION` on the `northstar.policy.decision`
# span (`if resolution.version is not None: span.set_attribute(...)`). The
# same applies to `variant` and `provider_name`, which can also be `None` (an
# unreachable-provider resolution has neither), matching the hook's own
# conditional inclusion of `RESULT_VARIANT` and `PROVIDER_NAME`.
#
# WHAT A READER OF THIS LOG EVENT MUST KNOW: reading `feature_flag.version`
# off THIS log event will find it absent for every real evaluation today,
# same as the span. `scorer/contract.py` already routes the audit's
# *correctness* around this -- the scorer reads `northstar.policy.decision`'s own
# `feature_flag.version` span attribute (also absent, same reason) and
# resolves the CURRENT contract version from flagd directly; this log event
# is the human-readable record and the parity target, not a dependency of
# the score. A reader must not expect a populated `feature_flag.version` to
# exist here, and must not treat its absence as a bug in this function -- it
# is a fact about `featureflag.yaml`, not about the emission path.
FEATURE_FLAG_EVALUATION_EVENT = "feature_flag.evaluation"


def emit_evaluation_event(resolution: AutonomyResolution, caller: str | None) -> None:
    """Emit the semconv `feature_flag.evaluation` log-record event ourselves,
    with what the hook omits. Called once per decision, from `server.py`,
    immediately after `resolve_autonomy` -- this function performs no
    resolution of its own and takes no `client`, keeping `resolve_autonomy`
    itself free of the side effect (it is exercised without a logger
    installed by every test above this comment).

    `timestamp=time_ns()` is passed explicitly. MEASURED, not assumed: the
    installed SDK's own `LogRecord.__init__` (1.44.0) only defaults
    `observed_timestamp` to `time_ns()` when it is `None` -- `self.timestamp
    = timestamp` sets it verbatim, `None` included. Emitting one record
    against the live cluster with `timestamp` unset landed in
    `ss4o_logs-default-namespace` with `"@timestamp":
    "1970-01-01T00:00:00Z"` (confirmed by querying OpenSearch directly) --
    the OTLP exporter serialises an unset `timestamp` as zero. A Dashboards
    time-range query defaults to `@timestamp` and would never surface a
    record time-stamped at the epoch, which is a real loss dressed as a
    successful export -- the same silent-loss shape this repo's telemetry
    parity guard exists to catch, one field over."""
    attributes = {
        FEATURE_FLAG_KEY: FLAG_KEY,
        FEATURE_FLAG_RESULT_VALUE: resolution.level,
        FEATURE_FLAG_RESULT_REASON: resolution.reason,
    }
    if resolution.variant is not None:
        attributes[FEATURE_FLAG_RESULT_VARIANT] = resolution.variant
    if resolution.provider_name is not None:
        attributes[FEATURE_FLAG_PROVIDER_NAME] = resolution.provider_name
    if caller:
        attributes[FEATURE_FLAG_CONTEXT_ID] = caller
    if resolution.set_id is not None:
        attributes[FEATURE_FLAG_SET_ID] = resolution.set_id
    if resolution.version is not None:
        attributes[FEATURE_FLAG_VERSION] = resolution.version
    if resolution.error_type is not None:
        attributes[ERROR_TYPE] = resolution.error_type

    telemetry.event_logger().emit(
        event_name=FEATURE_FLAG_EVALUATION_EVENT,
        attributes=attributes,
        timestamp=time_ns(),
    )
