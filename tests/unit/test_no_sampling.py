"""Guards a standing requirement this demo has never had to configure for:
100% of telemetry is recorded, always. Nothing samples today, but only by
*absence* of configuration -- no `OTEL_TRACES_SAMPLER` env var, no sampling
processor in the collector -- and absence is exactly the kind of thing a
changed library default or an added processor removes silently, with the
rest of the suite staying green because nothing here asserted the absence in
the first place.

Three independent ways telemetry could start being dropped, one assertion
each:

1. An `OTEL_TRACES_SAMPLER`/`OTEL_TRACES_SAMPLER_ARG` env var anywhere in
   `deploy/` would override the SDK's default (always-on) sampler.
2. A `tail_sampling` or `probabilistic_sampler` processor in the collector
   pipeline would drop spans after the SDK already sent them.
3. `deploy/platform/agentgateway/config.yaml`'s `frontendPolicies.tracing`
   must keep `randomSampling: true`. That config's own comment records why,
   measured against agentgateway v1.5.0's `TraceSampler::trace_sampled`
   (`crates/agentgateway/src/telemetry/log.rs:469-494`): a request that
   arrives *without* a `traceparent` is dropped by default ("If
   random_sampling is not set, default to NOT include it"). Without this
   setting, a header-less caller is silently untraced rather than visible.
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"
COLLECTOR_CONFIG = DEPLOY_DIR / "observability" / "collector-config.yaml"
GATEWAY_CONFIG = DEPLOY_DIR / "platform" / "agentgateway" / "config.yaml"


def test_no_otel_traces_sampler_env_var_anywhere_in_deploy():
    """The OTel SDK default is `parentbased_always_on` -- always record.
    Setting `OTEL_TRACES_SAMPLER` (or its `_ARG`) anywhere in `deploy/` would
    override that default, and nothing else in this repo would notice."""
    hits = [
        str(path.relative_to(REPO_ROOT))
        for path in DEPLOY_DIR.rglob("*")
        if path.is_file()
        and ("OTEL_TRACES_SAMPLER" in path.read_text(errors="ignore"))
    ]
    assert not hits, (
        "found OTEL_TRACES_SAMPLER (or _ARG) in: "
        f"{hits}. This demo's standing requirement is that 100% of telemetry "
        "is recorded, always -- remove the env var rather than tune it."
    )


def test_collector_config_declares_no_sampling_processor():
    """`tail_sampling` and `probabilistic_sampler` are the two processor
    names that would drop spans inside the collector after the SDK already
    sent them. Neither belongs in this pipeline."""
    config = yaml.safe_load(COLLECTOR_CONFIG.read_text())
    processors = set(config.get("processors") or {})
    sampling_processors = {"tail_sampling", "probabilistic_sampler"} & processors
    assert not sampling_processors, (
        f"{COLLECTOR_CONFIG.relative_to(REPO_ROOT)} declares a sampling "
        f"processor: {sampling_processors}. This demo's standing requirement "
        "is that 100% of telemetry is recorded, always -- remove it rather "
        "than tune its rate."
    )


def test_gateway_random_sampling_stays_on_for_headerless_callers():
    """Measured in agentgateway v1.5.0's `TraceSampler::trace_sampled`
    (`crates/agentgateway/src/telemetry/log.rs:469-494`): a request without an
    incoming `traceparent` is dropped by default. `randomSampling: true` is
    what makes a header-less caller visible instead of silently untraced --
    see this file's own comment for the full measurement."""
    config = yaml.safe_load(GATEWAY_CONFIG.read_text())
    tracing = (config.get("frontendPolicies") or {}).get("tracing") or {}
    assert tracing.get("randomSampling") is True, (
        f"{GATEWAY_CONFIG.relative_to(REPO_ROOT)}'s frontendPolicies.tracing."
        f"randomSampling is {tracing.get('randomSampling')!r}; it must be "
        "true. v1.5.0 drops a request with no incoming traceparent by "
        "default (TraceSampler::trace_sampled, log.rs:469-494) -- without "
        "this setting a header-less caller is silently untraced instead of "
        "visible."
    )
