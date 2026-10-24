"""The Weaver live-check wiring, asserted hermetically.

No cluster, no docker, no network: this reads the files that wire live-check in
and checks they agree with each other. What the live-check actually reports is
asserted by tests/e2e/test_registry_live_check.py against the running cluster.
"""
from __future__ import annotations

import re
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
]


def _overrides() -> list[dict]:
    return tomllib.loads(LIVE_CHECK_TOML.read_text())["live-check"]["finding_level_overrides"]


def test_findings_policy_fails_on_violation():
    assert tomllib.loads(LIVE_CHECK_TOML.read_text())["live-check"]["fail_on"] == "violation"


def _owned_override() -> dict:
    return next(o for o in _overrides() if o["level"] == "violation")


def test_owned_names_override_comes_first_and_the_catch_all_comes_last():
    """Overrides are first-match-wins and globs have no negation (measured on
    weaver v0.28.0), so the order IS the policy. The only override allowed
    before the owned one lowers Kubernetes pod labels (`northstar.io/...`) that
    the Linkerd proxy copies onto its resource."""
    overrides = _overrides()
    assert len(overrides) >= 3
    first, owned, last = overrides[0], overrides[1], overrides[-1]
    assert first == {"level": "information", "sample_names": ["northstar.io/*"]}, first
    assert owned["level"] == "violation" and owned.get("sample_names"), owned
    assert last["level"] == "information", last
    assert not last.get("sample_names") and not last.get("ids"), (
        "the last override must be the unscoped catch-all, or foreign names fail the gate"
    )


def test_undefined_enum_variant_is_raised_for_owned_names():
    """`undefined_enum_variant` is `information` by default; for our names an
    enum value outside the registry is a defect."""
    assert "undefined_enum_variant" in _owned_override()["ids"]


def test_the_harness_and_the_policy_name_the_same_owned_namespaces():
    policy = tuple(glob.removesuffix("*") for glob in _owned_override()["sample_names"])
    assert policy == livecheck.OWNED_PREFIXES
    lowered = tuple(glob.removesuffix("*") for glob in _overrides()[0]["sample_names"])
    assert lowered == livecheck.FOREIGN_LABEL_PREFIXES


def test_a_pod_label_on_the_proxy_resource_is_not_an_owned_violation():
    report = {"samples": [{"resource": {"attributes": [
        {"live_check_result": {"all_advice": [
            _finding("violation", "linkerd-proxy", attribute_key="northstar.io/plane"),
        ]}}]}}]}
    assert livecheck.owned_violations(report) == []


@pytest.mark.parametrize("config", COLLECTOR_CONFIGS, ids=lambda p: p.name)
def test_every_pipeline_sends_the_weaver_copy_and_none_samples_it(config):
    parsed = yaml.safe_load(config.read_text())
    pipelines = parsed["service"]["pipelines"]
    for name in ("traces", "metrics", "logs"):
        assert "otlp_grpc/weaver" in pipelines[name]["exporters"], name
    exporter = parsed["exporters"]["otlp_grpc/weaver"]
    assert exporter["endpoint"] == "weaver-live-check.observability.svc.cluster.local:4317"
    # Weaver's receiver rejects gzip, the exporter's default; with it every
    # batch is dropped as a permanent error and the report stays empty.
    assert exporter["compression"] == "none"
    assert exporter["sending_queue"] == {"enabled": True}
    assert exporter["retry_on_failure"] == {"enabled": True}
    for pipeline in pipelines.values():
        assert not any("sampl" in p for p in pipeline.get("processors", [])), pipeline


def test_the_manifest_pins_weaver_and_probes_the_admin_port():
    docs = list(yaml.safe_load_all(MANIFEST.read_text()))
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "otel/weaver:v0.28.0"
    assert container["resources"]["requests"]["memory"] and container["resources"]["limits"]["memory"]
    assert container["readinessProbe"]["httpGet"] == {"path": "/health", "port": 4320}
    args = container["args"]
    assert args[args.index("--output") + 1] == "http"
    assert args[args.index("--input-source") + 1] == "otlp"
    service = next(d for d in docs if d["kind"] == "Service")
    assert {p["port"] for p in service["spec"]["ports"]} == {4317, 4320}


def test_setup_kind_builds_both_configmaps_from_the_repo():
    setup = "\n".join(
        l for l in SETUP_KIND.read_text().splitlines() if not l.lstrip().startswith("#")
    )
    assert re.search(r"create configmap weaver-registry[^\n]*\\\n[^\n]*\n?[^\n]*telemetry/registry", setup)
    assert "--from-file=live-check.toml=\"$ROOT/telemetry/live-check.toml\"" in setup
    assert "weaver-live-check.yaml" in setup


LEGACY_RULE = "policy.rule"


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
            _finding("violation", "GET", attribute_key=LEGACY_RULE),
            _finding("violation", "kyverno.admission", attribute_key="admission.request.kind"),
            _finding("information", "northstar.policy.decision", attribute_key="northstar.policy.rule"),
        ]}}]}}]}
    owned = livecheck.owned_violations(report)
    assert [f["context"]["attribute_key"] for f in owned] == ["northstar.policy.rulee", LEGACY_RULE]


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


# --- the harness's report() and restart detection, with kubectl and HTTP faked ---


def _fake_cluster(monkeypatch, tmp_path, identity):
    import contextlib

    calls: list[tuple[str, str]] = []

    @contextlib.contextmanager
    def fake_forward():
        yield "http://fake"

    def fake_request(base, method, path, timeout=30.0):
        calls.append((method, path))
        if (method, path) == ("GET", "/report"):
            return 200, b'{"samples": [], "statistics": {}}'
        return 200, b"{}"

    monkeypatch.setattr(livecheck, "_forward", fake_forward)
    monkeypatch.setattr(livecheck, "_request", fake_request)
    monkeypatch.setattr(livecheck, "_pod_identity", lambda: dict(identity))
    monkeypatch.setattr(livecheck, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(livecheck, "healthy", lambda: True)
    monkeypatch.setattr(livecheck.time, "sleep", lambda s: None)
    return calls


def test_report_stops_reads_then_shuts_down_in_that_order(monkeypatch, tmp_path):
    """A stopped receiver left running makes the collector log `Exporting failed`
    for every batch, which other e2e guards count as loss."""
    import json

    identity = {"uid": "u1", "restartCount": 0}
    calls = _fake_cluster(monkeypatch, tmp_path, identity)
    (tmp_path / "state.json").write_text(json.dumps(identity))
    livecheck.report()
    assert calls == [("POST", "/stop"), ("GET", "/report"), ("POST", "/shutdown")]


def test_report_fails_when_the_pod_restarted_since_restart(monkeypatch, tmp_path):
    import json

    calls = _fake_cluster(monkeypatch, tmp_path, {"uid": "u1", "restartCount": 1})
    (tmp_path / "state.json").write_text(json.dumps({"uid": "u1", "restartCount": 0}))
    with pytest.raises(AssertionError, match="restarted during the run"):
        livecheck.report()
    assert calls == []


def test_report_fails_when_restart_never_recorded_the_pod(monkeypatch, tmp_path):
    _fake_cluster(monkeypatch, tmp_path, {"uid": "u1", "restartCount": 0})
    with pytest.raises(AssertionError, match="never recorded the pod"):
        livecheck.report()


def test_a_silent_kubectl_fails_the_forward_instead_of_hanging(monkeypatch):
    import subprocess as sp
    import time

    real_popen = sp.Popen

    def silent(cmd, **kwargs):
        return real_popen(["sleep", "60"], **kwargs)

    monkeypatch.setattr(livecheck.subprocess, "Popen", silent)
    monkeypatch.setattr(livecheck, "FORWARD_TIMEOUT_S", 1.0)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="unreachable"):
        with livecheck._forward():
            pass
    assert time.monotonic() - started < 10


def _span_report(*names: str) -> dict:
    return {"samples": [{"span": {"name": n}} for n in names]}


def test_legacy_span_names_lists_only_the_retired_namespaces():
    report = _span_report(
        "northstar.policy.decision", "northstar.review.decision", "GET /health",
        "policy" + ".decision", "review" + ".queue", "claim" + ".submit", "settlement" + ".execute",
    )
    assert livecheck.legacy_span_names(report) == [
        "claim" + ".submit", "policy" + ".decision", "review" + ".queue", "settlement" + ".execute",
    ]


def test_legacy_span_names_is_empty_for_a_clean_report():
    assert livecheck.legacy_span_names(_span_report("northstar.policy.decision", "kyverno.x")) == []


def test_the_weaver_limit_covers_a_measured_idle_day_between_recycles():
    """Idle growth was measured at about 220 MiB/hour (see the manifest comment);
    the pod is recycled at every setup-kind run and every gate, so the limit must
    cover well over the 8 hours of a demo day, not the old 768Mi (about 3.5 h)."""
    deployment = next(d for d in yaml.safe_load_all(MANIFEST.read_text()) if d["kind"] == "Deployment")
    limit = deployment["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"]
    assert limit.endswith("Gi") and int(limit[:-2]) * 1024 >= 220 * 12, limit
