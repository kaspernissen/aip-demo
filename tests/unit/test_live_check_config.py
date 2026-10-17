"""The Weaver live-check wiring (M9), asserted hermetically.

No cluster, no docker, no network: this reads the files that wire live-check in
and checks they agree with each other. What the live-check actually reports is
asserted by tests/e2e/test_registry_live_check.py against the running cluster.
"""
from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

from tests.harness import livecheck

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_CHECK_TOML = REPO_ROOT / "telemetry" / "live-check.toml"
MANIFEST = REPO_ROOT / "deploy" / "observability" / "weaver-live-check.yaml"
SETUP_KIND = REPO_ROOT / "scripts" / "setup-kind.sh"
COLLECTOR_CONFIGS = [
    REPO_ROOT / "deploy" / "observability" / "collector-config.yaml",
    REPO_ROOT / "deploy" / "observability" / "collector-config-dash0.yaml",
]


def _overrides() -> list[dict]:
    return tomllib.loads(LIVE_CHECK_TOML.read_text())["live-check"]["finding_level_overrides"]


def test_findings_policy_fails_on_violation():
    assert tomllib.loads(LIVE_CHECK_TOML.read_text())["live-check"]["fail_on"] == "violation"


def test_owned_names_override_comes_first_and_the_catch_all_comes_last():
    """Overrides are first-match-wins and globs have no negation (measured on
    weaver v0.28.0), so the order IS the policy."""
    overrides = _overrides()
    assert len(overrides) >= 2
    first, last = overrides[0], overrides[-1]
    assert first["level"] == "violation" and first.get("sample_names"), first
    assert last["level"] == "information", last
    assert not last.get("sample_names") and not last.get("ids"), (
        "the last override must be the unscoped catch-all, or foreign names fail the gate"
    )


def test_undefined_enum_variant_is_raised_for_owned_names():
    """`undefined_enum_variant` is `information` by default; for our names an
    enum value outside the registry is a defect."""
    assert "undefined_enum_variant" in _overrides()[0]["ids"]


def test_the_harness_and_the_policy_name_the_same_owned_namespaces():
    policy = tuple(glob.removesuffix("*") for glob in _overrides()[0]["sample_names"])
    assert policy == livecheck.OWNED_PREFIXES


@pytest.mark.parametrize("config", COLLECTOR_CONFIGS, ids=lambda p: p.name)
def test_every_pipeline_sends_the_weaver_copy_and_none_samples_it(config):
    parsed = yaml.safe_load(config.read_text())
    pipelines = parsed["service"]["pipelines"]
    for name in ("traces", "metrics", "logs"):
        assert "otlp_grpc/weaver" in pipelines[name]["exporters"], name
    # The copy goes to the MAIN traces pipeline, upstream of the OpenSearch
    # compat rewrite, so Weaver sees canonical names.
    assert "otlp_grpc/weaver" not in pipelines["traces/opensearch"]["exporters"]
    exporter = parsed["exporters"]["otlp_grpc/weaver"]
    assert exporter["endpoint"] == "weaver-live-check.observability.svc.cluster.local:4317"
    # Weaver's receiver rejects gzip, the exporter's default; with it every
    # batch is dropped as a permanent error and the report stays empty.
    assert exporter["compression"] == "none"
    assert exporter["sending_queue"] == {"enabled": True}
    assert exporter["retry_on_failure"] == {"enabled": True}
    for pipeline in pipelines.values():
        assert not any("sampl" in p for p in pipeline.get("processors", [])), pipeline


@pytest.mark.parametrize("config", COLLECTOR_CONFIGS, ids=lambda p: p.name)
def test_below_stage_4_the_weaver_exporter_is_stripped(config):
    """A pre-stage-4 cluster has no live-check pod; the collector must not name it."""
    setup = SETUP_KIND.read_text()
    expression = re.search(r"^STAGE4_ONLY_STRIP='([^']+)'$", setup, re.MULTILINE).group(1)
    stripped = subprocess.run(
        ["sed", expression, str(config)], capture_output=True, text=True, check=True
    ).stdout
    assert "weaver" not in stripped.lower()
    parsed = yaml.safe_load(stripped)
    for pipeline in parsed["service"]["pipelines"].values():
        for exporter in pipeline["exporters"]:
            assert exporter in parsed["exporters"], exporter


def test_the_manifest_pins_weaver_and_probes_the_admin_port():
    docs = list(yaml.safe_load_all(MANIFEST.read_text()))
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "otel/weaver:v0.28.0"
    assert container["readinessProbe"]["httpGet"] == {"path": "/health", "port": 4320}
    args = container["args"]
    assert args[args.index("--output") + 1] == "http"
    assert args[args.index("--input-source") + 1] == "otlp"
    service = next(d for d in docs if d["kind"] == "Service")
    assert {p["port"] for p in service["spec"]["ports"]} == {4317, 4320}


def test_setup_kind_builds_both_configmaps_from_the_repo_at_stage_4_and_up():
    setup = "\n".join(
        l for l in SETUP_KIND.read_text().splitlines() if not l.lstrip().startswith("#")
    )
    assert re.search(r"create configmap weaver-registry[^\n]*\\\n[^\n]*\n?[^\n]*telemetry/registry", setup)
    assert "--from-file=live-check.toml=\"$ROOT/telemetry/live-check.toml\"" in setup
    assert "weaver-live-check.yaml" in setup


def _finding(level: str, signal_name: str, **context: str) -> dict:
    return {"id": "missing_attribute", "level": level, "signal_name": signal_name,
            "signal_type": "span", "message": "m", "context": context}


def test_owned_violations_judges_the_attribute_key_not_the_span_it_sits_on():
    """Mirrors the override: a foreign attribute on an owned span is not ours; a
    misspelled owned attribute on a foreign span is."""
    report = {"samples": [{"span": {"name": "x", "attributes": [
        {"live_check_result": {"all_advice": [
            _finding("violation", "northstar.policy.decision", attribute_key="northstar.policy.rulee"),
            _finding("violation", "northstar.policy.decision", attribute_key="kyverno.thing"),
            _finding("violation", "GET", attribute_key="policy.rule"),
            _finding("violation", "kyverno.admission", attribute_key="admission.request.kind"),
            _finding("information", "northstar.policy.decision", attribute_key="northstar.policy.rule"),
        ]}}]}}]}
    owned = livecheck.owned_violations(report)
    assert [f["context"]["attribute_key"] for f in owned] == ["northstar.policy.rulee", "policy.rule"]


def test_a_metric_finding_is_judged_by_the_signal_name():
    report = {"samples": [{"metric": {"name": "northstar.approval.wait.duraton",
              "live_check_result": {"all_advice": [
                  {"id": "missing_metric", "level": "violation", "message": "m",
                   "signal_type": "metric", "signal_name": "northstar.approval.wait.duraton",
                   "context": {}}]}}}]}
    assert len(livecheck.owned_violations(report)) == 1


def test_an_empty_report_has_no_samples():
    assert livecheck.sample_counts({"samples": [], "statistics": {"total_entities_by_type": {}}}) == {}
    assert livecheck.owned_violations({"samples": [], "statistics": {}}) == []


def _effective_gate() -> str:
    gate = REPO_ROOT / "scripts" / "smoke-test.sh"
    return "\n".join(l for l in gate.read_text().splitlines() if not l.lstrip().startswith("#"))


def test_the_gate_restarts_live_check_before_the_e2e_run_and_judges_it_after():
    """Order is the design: an empty report, then all the traffic, then the verdict."""
    gate = _effective_gate()
    registry = gate.index("scripts/check-registry.sh")
    restart = gate.index("tests.harness.livecheck restart")
    main_run = gate.index('-m pytest "$ROOT/tests" -v')
    verdict = gate.index('tests/e2e/test_registry_live_check.py" -v')
    assert registry < restart < main_run < verdict
    assert '--ignore="$ROOT/tests/e2e/test_registry_live_check.py"' in gate
    assert "m9-live-check-report.json" in gate
