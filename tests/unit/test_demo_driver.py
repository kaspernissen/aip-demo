"""`scripts/demo` says nothing about the platform that a file the driver does
not own does not also say.

The driver has to know a handful of facts -- the reach URLs, the keystone
policy's name, the over-grant's object names, the flag object and the path the
flip patches. Retyping a fact in a second place is how a slide ends up saying
something the cluster stopped doing, so each one is compared here with the file
that actually owns it (the beat scripts the gate also runs, the policy, the
fixture). A drift is a red unit test, not a wrong thing said on stage.

Also pinned: the subcommand set is the M8 flow's plus `reset` and the off-stage `ceiling`, the driver and `beat-reach.sh` run the one probe file, the
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


def test_the_subcommands_are_the_m8_flow_plus_reset_and_the_ceiling():
    out = subprocess.run([str(SCRIPTS / "demo"), "--help"], capture_output=True, text=True, timeout=30)
    for command in ("guard", "claim", "approve", "reject", "autonomy", "keystone", "evidence", "reset", "ceiling"):
        assert command in out.stdout, out.stdout


def test_approve_resolves_the_claim_to_its_payment_from_this_runs_state(monkeypatch, tmp_path):
    demo = _driver()
    monkeypatch.setattr(demo, "STATE_DIR", tmp_path)
    monkeypatch.setattr(demo, "STATE_FILE", tmp_path / "state.json")
    demo.save_state({"run_id": "r", "window_start_ms": 0, "claims": [
        {"id": "CLM-DEMO-AAA", "payment_id": "PAY-000007", "status": "awaiting_approval", "amount": 5800.0,
         "level": "assisted", "trace_id": "t"}]})
    sent = []
    monkeypatch.setattr(demo.live, "review", lambda pid, decision, reviewer: sent.append((pid, decision, reviewer))
                        or {"error": None, "executed": {"status": "executed"}, "elapsedMs": 4200,
                            "resumedState": "input-required", "resumeError": None, "rejected": None})
    assert demo.main(["approve", "last"]) == 0
    assert sent == [("PAY-000007", "approve", demo.STAGE_REVIEWER)]
    assert demo.load_state()["claims"][0]["status"] == "executed"


def test_a_refused_decision_fails_the_run(monkeypatch, tmp_path):
    """The reviewer-ui answers 200 with `error` set when the ledger refuses."""
    demo = _driver()
    monkeypatch.setattr(demo, "STATE_DIR", tmp_path)
    monkeypatch.setattr(demo, "STATE_FILE", tmp_path / "state.json")
    demo.save_state({"claims": [{"id": "CLM-DEMO-AAA", "payment_id": "PAY-000007",
                                 "status": "awaiting_approval", "amount": 5800.0}]})
    monkeypatch.setattr(demo.live, "review", lambda *a: {"error": "payment is not awaiting approval"})
    assert demo.main(["reject", "CLM-DEMO-AAA"]) == 1


def test_approve_with_nothing_waiting_is_a_failure(monkeypatch, tmp_path):
    demo = _driver()
    monkeypatch.setattr(demo, "STATE_DIR", tmp_path)
    monkeypatch.setattr(demo, "STATE_FILE", tmp_path / "state.json")
    assert demo.main(["approve", "last"]) == 1


def test_reset_rejects_every_open_approval_as_demo_reset(monkeypatch):
    demo = _driver()
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [{"paymentId": "PAY-000001"}, {"paymentId": "PAY-000002"}])
    sent = []
    monkeypatch.setattr(demo.live, "review", lambda pid, d, r: sent.append((pid, d, r)) or {"error": None})
    monkeypatch.setattr(demo, "delete_overgrant", lambda: None)
    monkeypatch.setattr(demo, "overgrant_present", lambda: False)
    monkeypatch.setattr(demo.contract, "set_targeted_branch", lambda level: None)
    monkeypatch.setattr(demo, "flag_level", lambda: "assisted")
    monkeypatch.setattr(demo, "STATE_FILE", Path("/nonexistent/state.json"))
    calls = iter([[{"paymentId": "PAY-000001"}, {"paymentId": "PAY-000002"}], []])
    monkeypatch.setattr(demo, "awaiting_payments", lambda: next(calls))
    assert demo.main(["reset"]) == 0
    assert sent == [("PAY-000001", "reject", "demo-reset"), ("PAY-000002", "reject", "demo-reset")]


def test_reset_fails_while_an_approval_is_still_open(monkeypatch):
    demo = _driver()
    monkeypatch.setattr(demo.live, "review", lambda pid, d, r: {"error": None})
    monkeypatch.setattr(demo, "delete_overgrant", lambda: None)
    monkeypatch.setattr(demo, "overgrant_present", lambda: False)
    monkeypatch.setattr(demo.contract, "set_targeted_branch", lambda level: None)
    monkeypatch.setattr(demo, "flag_level", lambda: "assisted")
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [{"paymentId": "PAY-000001"}])
    assert demo.main(["reset"]) == 1


def test_the_gateway_line_says_rule_and_limit_in_plain_words_not_the_raw_tag():
    demo = _driver()
    line = demo.plain_denial("assisted limit reached [policy-denial rule=assisted.execute limit=0.0 requested=5800.0]")
    assert "policy-denial" not in line and "[" not in line
    assert "assisted.execute" in line and "5,800" in line


def test_the_flow_never_calls_a_waiting_claim_refused():
    text = (SCRIPTS / "demo.py").read_text()
    assert "awaiting human approval" in text
    assert "A refusal is a success" not in text
    assert "word('REFUSED'" not in text


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
    row = driver._settled({"id": "CLM-X", "status": "awaiting_approval"}, 0)
    assert row == complete


def test_evidence_that_never_arrives_is_a_failure_naming_what_is_missing(monkeypatch):
    driver = _driver()
    monkeypatch.setattr(driver, "EVIDENCE_WAIT_S", 0.0)
    monkeypatch.setattr(driver.live, "evidence_for", lambda claim_id, window: {"policy_decision": "deny"})
    monkeypatch.setattr(driver.time, "sleep", lambda s: None)
    try:
        driver._settled({"id": "CLM-X", "status": "awaiting_approval"}, 0)
    except driver.Fail as exc:
        assert "never became complete" in str(exc) and "rule" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an incomplete row was returned as if it were evidence")
