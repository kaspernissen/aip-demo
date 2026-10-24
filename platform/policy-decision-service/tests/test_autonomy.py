"""`pds.autonomy.resolve_autonomy`: turning a flagd lookup into a policy
input, and nothing else.

Includes two behavioural proofs: that the port this file's client would be
built with cannot drift from
`deploy/platform/openfeature/featureflagsource.yaml`, and that the resolver
`FlagdProvider(host="localhost", port=8013)` actually constructs is the RPC
one -- an explicitly passed host and port are silently ignored if the
provider falls back to in-process or file resolution instead, which would
make the port assertion theatre.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from openfeature.contrib.provider.flagd import FlagdProvider
from openfeature.contrib.provider.flagd.resolvers import GrpcResolver
from openfeature.flag_evaluation import Reason

from pds.autonomy import (
    CONTEXT_KEY,
    FLAG_KEY,
    FLAGD_HOST,
    FLAGD_PORT,
    RESTRICTIVE_LEVEL,
    AutonomyResolution,
    resolve_autonomy,
)

SETTLEMENT_AGENT = "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"

# platform/policy-decision-service/tests/test_autonomy.py -> repo root is
# three parents up.
REPO_ROOT = Path(__file__).resolve().parents[3]
FEATUREFLAGSOURCE = (
    REPO_ROOT / "deploy" / "platform" / "openfeature" / "featureflagsource.yaml"
)
FEATUREFLAG = REPO_ROOT / "deploy" / "platform" / "openfeature" / "featureflag.yaml"


class _RaisingClient:
    """The flagd-outage double: `get_string_details` always raises, and the
    object carries no `.provider` attribute at all -- so a correct
    `resolve_autonomy` must not assume one is there either."""

    def __init__(self, exc: Exception):
        self._exc = exc

    def get_string_details(self, *args, **kwargs):
        raise self._exc


def _client_that_raises(exc: Exception) -> _RaisingClient:
    return _RaisingClient(exc)


class _FakeDetails:
    """The shape `resolve_autonomy` reads off a real
    `openfeature.flag_evaluation.FlagEvaluationDetails` -- `value`, `reason`,
    `variant`, `flag_metadata`, `error_code` -- built by hand so a test can
    hand back a *populated* `flag_metadata`, which the live sidecar never
    does today (see the two tests at the bottom of this file)."""

    def __init__(self, value, reason, variant=None, flag_metadata=None,
                 error_code=None):
        self.value = value
        self.reason = reason
        self.variant = variant
        self.flag_metadata = flag_metadata or {}
        self.error_code = error_code


class _FakeClient:
    """A client that returns a canned `_FakeDetails` and records the
    evaluation context it was called with, so a test can assert on both the
    extraction and the request shape without a live provider."""

    def __init__(self, details: _FakeDetails, provider_name: str | None = "FakeProvider"):
        self._details = details
        self.captured_context = None
        if provider_name is not None:
            self.provider = SimpleNamespace(
                get_metadata=lambda: SimpleNamespace(name=provider_name)
            )

    def get_string_details(self, flag_key, default_value, evaluation_context=None):
        self.captured_context = evaluation_context
        return self._details


def test_an_unreachable_provider_resolves_to_the_restrictive_level():
    """A flagd outage must never widen what is permitted."""
    resolution = resolve_autonomy(
        SETTLEMENT_AGENT,
        client=_client_that_raises(ConnectionError("flagd unreachable")),
    )
    assert resolution.level == "assisted"
    assert resolution.reason == "ERROR"
    assert resolution.error_type is not None


def test_the_unreachable_path_is_honest_about_what_it_could_not_learn():
    """The fields an outage cannot establish (`variant`, `version`) stay
    `None` rather than being invented."""
    resolution = resolve_autonomy(
        SETTLEMENT_AGENT,
        client=_client_that_raises(ConnectionError("flagd unreachable")),
    )
    assert resolution == AutonomyResolution(
        level=RESTRICTIVE_LEVEL,
        reason="ERROR",
        variant=None,
        version=None,
        error_type="ConnectionError",
    )


def test_a_successful_resolution_reports_every_field_it_can():
    """The positive case: every field populated from a response that --
    unlike the live sidecar today (see the bottom of this file) -- carries
    a `flag_metadata`, proving the extraction itself is correct independent
    of what this deployment's flagd happens to send back."""
    details = _FakeDetails(
        value="autonomous", reason=Reason.TARGETING_MATCH, variant="autonomous",
        flag_metadata={"version": "3", "flagSetId": "northstar-autonomy"},
    )
    client = _FakeClient(details, provider_name="FlagdProvider")

    resolution = resolve_autonomy(SETTLEMENT_AGENT, client=client)

    assert resolution == AutonomyResolution(
        level="autonomous",
        reason="TARGETING_MATCH",
        variant="autonomous",
        version="3",
        error_type=None,
    )


def test_an_error_code_without_an_exception_still_reports_an_error_type():
    """The real SDK does not always raise on failure -- a provider that is
    merely not ready yet returns `FlagEvaluationDetails` with `error_code`
    set and no exception at all (measured against the live sidecar during
    its own startup window). `error_type` must still carry that, not stay
    `None` just because nothing was thrown.

    The fixture hands back `value="autonomous"` -- a provider-chosen or
    contract-chosen value that has not been overwritten to the default --
    *alongside* an `error_code`, rather than `value=RESTRICTIVE_LEVEL`, which
    would pass whether or not `resolve_autonomy` forces anything down and so
    prove nothing about this function's own fail-closed guarantee. Today's
    SDK always overwrites the value before this can happen (traced in the
    module docstring), but this function must not depend on that: if
    `error_code` is present, `resolve_autonomy` must force the level to
    `RESTRICTIVE_LEVEL` regardless of what `details.value` says.
    """
    from openfeature.exception import ErrorCode

    details = _FakeDetails(
        value="autonomous", reason=Reason.ERROR,
        error_code=ErrorCode.PROVIDER_NOT_READY,
    )
    resolution = resolve_autonomy(SETTLEMENT_AGENT, client=_FakeClient(details))
    assert resolution.level == RESTRICTIVE_LEVEL
    assert resolution.error_type == "PROVIDER_NOT_READY"


def test_the_evaluation_context_carries_the_caller_under_the_contracts_key():
    """`featureflag.yaml`'s targeting rule reads `var: spiffeId` -- derived
    from the resource here, not retyped, so the two cannot drift apart."""
    spec = yaml.safe_load(FEATUREFLAG.read_text())
    targeting_var = (
        spec["spec"]["flagSpec"]["flags"]["autonomy-level"]["targeting"]["if"][0]
        ["in"][0]["var"]
    )
    assert targeting_var == CONTEXT_KEY

    client = _FakeClient(_FakeDetails(value="assisted", reason=Reason.TARGETING_MATCH))
    resolve_autonomy(SETTLEMENT_AGENT, client=client)

    context = client.captured_context
    assert context.targeting_key == SETTLEMENT_AGENT
    assert context.attributes[CONTEXT_KEY] == SETTLEMENT_AGENT


def test_a_missing_caller_still_resolves_something():
    """No SPIFFE ID is a policy input the contract's targeting rule can see
    (an empty string, not an absent key), not a reason to skip the lookup."""
    client = _FakeClient(_FakeDetails(value="assisted", reason=Reason.DEFAULT))
    resolve_autonomy(None, client=client)

    context = client.captured_context
    assert context.targeting_key == ""
    assert context.attributes[CONTEXT_KEY] == ""


def test_the_flag_key_is_the_one_the_contract_defines():
    spec = yaml.safe_load(FEATUREFLAG.read_text())
    assert FLAG_KEY in spec["spec"]["flagSpec"]["flags"]


# --- the `feature_flag.evaluation` span event -------------------------------


def test_the_tracing_hook_puts_a_feature_flag_evaluation_event_on_the_active_span():
    """`server.py`'s comment claims `TracingHook` "puts a `feature_flag.
    evaluation` event on whatever span is active" -- this guard verifies it
    independently against a real `OpenFeatureClient` and an in-memory
    exporter, since this repo's standing rule forbids asserting a claim that
    was never measured. `test_servicer.py`'s `_FailClosedFlagClient` cannot
    stand in for this guard: it implements `get_string_details` itself and
    never runs a hook at all, by design (its own docstring says so).

    This is the guard: a real `OpenFeatureClient`, a real `TracingHook`, and
    `InMemoryProvider` (in-process, no network -- the point under test is the
    hook, not flagd) evaluated inside a real active span through
    `resolve_autonomy` itself, so the call shape matches production exactly.
    A domain-scoped client keeps this isolated from the default-domain client
    `pds.server` installs at import.
    """
    from openfeature import api
    from openfeature.contrib.hook.opentelemetry import TracingHook
    from openfeature.provider.in_memory_provider import InMemoryFlag, InMemoryProvider
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = tracer_provider.get_tracer("test_autonomy")

    domain = "test-tracing-hook-event"
    api.set_provider(InMemoryProvider({
        FLAG_KEY: InMemoryFlag(
            default_variant=RESTRICTIVE_LEVEL,
            variants={RESTRICTIVE_LEVEL: RESTRICTIVE_LEVEL, "autonomous": "autonomous"},
        ),
    }), domain=domain)
    client = api.get_client(domain=domain)
    client.add_hooks([TracingHook()])

    with tracer.start_as_current_span("northstar.policy.decision"):
        resolve_autonomy(SETTLEMENT_AGENT, client=client)

    span, = exporter.get_finished_spans()
    events = [e for e in span.events if e.name == "feature_flag.evaluation"]
    assert len(events) == 1, (
        f"expected exactly one feature_flag.evaluation event, got "
        f"{[e.name for e in span.events]}"
    )
    attrs = dict(events[0].attributes)
    assert attrs["feature_flag.key"] == FLAG_KEY
    assert attrs["feature_flag.result.value"] == f'"{RESTRICTIVE_LEVEL}"'
    assert attrs["feature_flag.result.reason"] == "static"
    assert attrs["feature_flag.result.variant"] == RESTRICTIVE_LEVEL
    assert attrs["feature_flag.context.id"] == SETTLEMENT_AGENT
    assert "feature_flag.provider.name" in attrs


# --- the flagd client's host and port match the deployed sidecar ------------


def test_the_configured_port_matches_the_featureflagsource_resource():
    """The port `server.py`'s client is built against
    (`FLAGD_HOST`/`FLAGD_PORT`) and the port the Open Feature Operator
    actually configures the sidecar to answer on must be the same number by
    construction, not by two authors remembering to agree."""
    source = yaml.safe_load(FEATUREFLAGSOURCE.read_text())
    assert source["spec"]["port"] == FLAGD_PORT
    assert FLAGD_HOST == "localhost", (
        "the sidecar is native (`initContainers`, not a separate pod/Service) "
        "-- anything other than localhost cannot reach it"
    )


def test_the_provider_resolves_over_rpc_not_in_process():
    """A behavioural proof, not just a constructor call that didn't raise.

    `FlagdProvider.setup_resolver` (openfeature-provider-flagd==0.5.2) picks
    `GrpcResolver` only when `self.config.resolver is ResolverType.RPC`; an
    unset `FLAGD_RESOLVER` environment variable is what makes that the
    default (`config.py`'s `DEFAULT_RESOLVER_TYPE`), and
    `deploy/k8s/policy-decision-service.yaml` sets no such variable -- so
    this asserts today's actual, not assumed, behaviour, and will go RED if
    either fact stops being true.
    """
    provider = FlagdProvider(host=FLAGD_HOST, port=FLAGD_PORT)
    assert isinstance(provider.resolver, GrpcResolver), (
        f"expected the RPC resolver, got {type(provider.resolver).__name__} "
        "-- an explicitly passed host/port is silently ignored if the "
        "provider resolves in-process or from a file instead"
    )
    assert provider.config.host == FLAGD_HOST
    assert provider.config.port == FLAGD_PORT
