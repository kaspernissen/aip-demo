"""`scripts/demo` says nothing about the platform that a file the driver does
not own does not also say.

The driver has to know a handful of facts -- the reach URLs, the keystone
policy's name, the over-grant's object names, the flag object and the path the
flip patches. Retyping a fact in a second place is how a slide ends up saying
something the cluster stopped doing, so each one is compared here with the file
that actually owns it (the beat scripts the gate also runs, the policy, the
fixture). A drift is a red unit test, not a wrong thing said on stage.

Also pinned: the subcommand set is exactly the five-minute flow's plus `reset`
(spec section 7.3), the driver and `beat-reach.sh` run the one probe file, the
shim is executable, and the run-state directory is git-ignored so a rehearsal's
claim ids can never be committed.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _driver():
    """The driver module, imported by path (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("northstar_demo_driver", SCRIPTS / "demo.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _shell_var(path: Path, name: str) -> str:
    match = re.search(rf'^{name}="([^"]*)"', path.read_text(), re.MULTILINE)
    assert match, f"{path.name} no longer defines {name}="
    return match.group(1)


def test_the_reach_urls_are_the_ones_beat_reach_probes():
    driver = _driver()
    beat = SCRIPTS / "beat-reach.sh"
    assert driver.CONTROL_URL == _shell_var(beat, "CONTROL_URL")
    assert driver.DIRECT_URL == _shell_var(beat, "DIRECT_URL")
    assert f"CONNECT_TIMEOUT={driver.CONNECT_TIMEOUT_S}\n" in beat.read_text()


def test_the_driver_and_beat_reach_run_one_probe_file():
    driver = _driver()
    assert driver.REACH_PROBE == SCRIPTS / "reach-probe.py"
    beat = (SCRIPTS / "beat-reach.sh").read_text()
    assert "reach-probe.py" in beat, "beat-reach.sh must read the shared probe, not carry its own copy"
    assert "urllib.request" not in beat, "a second copy of the probe has crept back into beat-reach.sh"
    probe = (SCRIPTS / "reach-probe.py").read_text()
    for outcome in ("REACHED", "DROPPED", "REFUSED"):
        assert outcome in probe
        # The driver branches on exactly these prefixes.
        assert outcome in (SCRIPTS / "demo.py").read_text()


def test_the_keystone_names_are_the_ones_the_policy_and_the_beat_script_use():
    driver = _driver()
    beat = SCRIPTS / "beat-keystone.sh"
    assert driver.AGENT == _shell_var(beat, "AGENT")
    assert driver.POLICY == _shell_var(beat, "POLICY")
    assert driver.FLAG == _shell_var(beat, "FLAG")
    assert driver.PLATFORM_NS == _shell_var(beat, "NS")
    policy = yaml.safe_load((REPO_ROOT / "deploy/platform/kyverno/keystone-policy.yaml").read_text())
    assert policy["metadata"]["name"] == driver.POLICY


def test_the_overgrant_objects_are_the_ones_the_fixture_defines():
    driver = _driver()
    docs = [d for d in yaml.safe_load_all(driver.OVERGRANT.read_text()) if d]
    assert {d["kind"].lower() for d in docs} == set(driver.OVERGRANT_OBJECTS)
    assert {d["metadata"]["name"] for d in docs} == {driver.OVERGRANT_NAME}


def test_the_flip_patches_the_path_backstage_patches():
    """One path, one definition: `tests/harness/contract.py`'s, which the beat
    script's own copy and Backstage's `k8sClient.ts` are already compared
    with elsewhere. The driver imports it rather than restating it."""
    from tests.harness import contract

    text = (SCRIPTS / "demo.py").read_text()
    assert "contract.TARGETING_THEN_PATH" in text
    assert "targeting/if/1" not in text, "the driver restated the flag path instead of importing it"
    assert contract.TARGETING_THEN_PATH in (SCRIPTS / "beat-keystone.sh").read_text().replace("'", "")


def test_the_subcommands_are_exactly_the_five_minute_flow_plus_reset():
    result = subprocess.run(
        [str(SCRIPTS / "demo"), "--help"], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    match = re.search(r"\{([a-z,]+)\}", result.stdout)
    assert match, result.stdout
    assert match.group(1).split(",") == ["guard", "claim", "autonomy", "keystone", "evidence", "reset", "ceiling"]


def test_the_shim_is_executable_and_runs_under_the_repo_venv():
    shim = SCRIPTS / "demo"
    assert os.access(shim, os.X_OK)
    assert ".venv/bin/python" in shim.read_text()


def test_the_run_state_directory_is_git_ignored():
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".demo/state.json"], cwd=REPO_ROOT, timeout=30
    )
    assert result.returncode == 0, ".demo/ must be in .gitignore"


def test_a_refused_claim_that_executes_would_fail_the_run():
    """The exit-status contract, on the pure part: `claim` raises `Fail` when
    the reply, the policy decision and the payments-api span disagree. Exercised
    with a fake trace so the property does not need a model call."""
    driver = _driver()
    from tests.harness.jaeger import Trace

    def span(sid, parent, service, operation, **attrs):
        return {
            "spanID": sid,
            "processID": service,
            "operationName": operation,
            "references": [{"refType": "CHILD_OF", "spanID": parent}] if parent else [],
            "tags": [{"key": k, "value": v} for k, v in attrs.items()],
        }

    raw = {
        "traceID": "t",
        "processes": {n: {"serviceName": n} for n in ("agentgateway", "policy-decision-service", "payments-api")},
        "spans": [
            span("g", None, "agentgateway", "tools/call", **{"gen_ai.tool.name": "execute_payment", "http.response.status_code": 200}),
            span("r", "g", "agentgateway", "Guardrail"),
            span("d", "g", "policy-decision-service", "policy.decision", **{
                "policy.tool.name": "execute_payment", "policy.decision": "deny",
                "policy.rule": "x", "policy.limit.amount": 6000, "policy.request.amount": 6200,
                "policy.autonomy.level": "assisted"}),
            span("e", "g", "payments-api", "POST /payments/{paymentId}/execute"),
        ],
    }
    facts = driver.render_span_tree(Trace(raw), executed_expected=False)
    # A deny with an execute span present is exactly what `cmd_claim` turns
    # into a failure; the facts it compares are these two.
    assert facts["verdict"] == "deny" and facts["executed_span_present"] is True


def test_evidence_waits_for_ingest_lag_then_settles_and_never_returns_a_partial_row(monkeypatch):
    """OpenSearch is near-real-time, so a claim's row can be incomplete for a
    few seconds after the claim returns. `evidence` waits, bounded; and the row
    it finally prints is a complete one, never the partial one it saw first."""
    driver = _driver()
    complete = {
        "policy_decision": "deny", "rule": "r", "caller_identity": "c", "money_hops": ["h"],
    }
    rows = iter([{}, {"policy_decision": "deny"}, complete])
    monkeypatch.setattr(driver.live, "evidence_for", lambda claim_id, window: next(rows))
    monkeypatch.setattr(driver.time, "sleep", lambda s: None)
    row = driver._settled({"id": "CLM-X", "status": "refused"}, 0)
    assert row == complete


def test_evidence_that_never_arrives_is_a_failure_naming_what_is_missing(monkeypatch):
    driver = _driver()
    monkeypatch.setattr(driver, "EVIDENCE_WAIT_S", 0.0)
    monkeypatch.setattr(driver.live, "evidence_for", lambda claim_id, window: {"policy_decision": "deny"})
    monkeypatch.setattr(driver.time, "sleep", lambda s: None)
    try:
        driver._settled({"id": "CLM-X", "status": "refused"}, 0)
    except driver.Fail as exc:
        assert "never became complete" in str(exc) and "rule" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an incomplete row was returned as if it were evidence")
