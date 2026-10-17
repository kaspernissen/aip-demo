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


def _driver(stub_untold: bool = True):
    """The driver module, imported by path (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("northstar_demo_driver", SCRIPTS / "demo.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    # No unit test reaches the ledger: a decided-but-untold scan answers "none"
    # unless the test says otherwise.
    if stub_untold:
        module.untold_payments = lambda: []
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
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [{"paymentId": "PAY-000007", "claimId": "CLM-DEMO-AAA"}])
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
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [])
    monkeypatch.setattr(demo.live, "review", lambda *a: {"error": "payment is not awaiting approval"})
    assert demo.main(["reject", "CLM-DEMO-AAA"]) == 1


def test_approve_with_nothing_waiting_is_a_failure(monkeypatch, tmp_path):
    demo = _driver()
    monkeypatch.setattr(demo, "STATE_DIR", tmp_path)
    monkeypatch.setattr(demo, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [])
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
            span("d", "g", "policy-decision-service", "northstar.policy.decision", **{
                "northstar.policy.tool.name": "execute_payment", "northstar.policy.decision": "deny",
                "northstar.policy.rule": "x", "northstar.policy.limit.amount": 6000, "northstar.policy.request.amount": 6200,
                "northstar.policy.autonomy.level": "assisted"}),
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
    monkeypatch.setattr(driver.evidence, "evidence_for", lambda claim_id, window: next(rows))
    monkeypatch.setattr(driver.time, "sleep", lambda s: None)
    row = driver._settled({"id": "CLM-X", "status": "awaiting_approval"}, 0)
    assert row == complete


def test_evidence_that_never_arrives_is_a_failure_naming_what_is_missing(monkeypatch):
    driver = _driver()
    monkeypatch.setattr(driver, "EVIDENCE_WAIT_S", 0.0)
    monkeypatch.setattr(driver.evidence, "evidence_for", lambda claim_id, window: {"policy_decision": "deny"})
    monkeypatch.setattr(driver.time, "sleep", lambda s: None)
    try:
        driver._settled({"id": "CLM-X", "status": "awaiting_approval"}, 0)
    except driver.Fail as exc:
        assert "never became complete" in str(exc) and "rule" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an incomplete row was returned as if it were evidence")


# --- a claim filed in the customer-ui is not in this run's notes (C1) -------------

BROWSER_CLAIM = {
    "paymentId": "PAY-000011", "claimId": "CLM-BROWSER-1", "amount": 5800.0, "currency": "EUR",
    "status": "awaiting_approval", "a2aTaskId": "task-b1", "awaitingSince": "2026-10-14T10:00:00Z",
    "refusal": {"rule": "settlement-agent.assisted.execute_limit", "limit": 0.0,
                "refusedTraceId": "a" * 32, "refusedSpanId": "b" * 16},
}


def _isolated(monkeypatch, tmp_path):
    demo = _driver()
    monkeypatch.setattr(demo, "STATE_DIR", tmp_path)
    monkeypatch.setattr(demo, "STATE_FILE", tmp_path / "state.json")
    return demo


def _decided(monkeypatch, demo, **extra):
    sent = []
    reply = {"error": None, "elapsedMs": 4200, "resumedState": "input-required",
             "resumeError": None, **extra}
    monkeypatch.setattr(demo.live, "review", lambda pid, d, r: sent.append((pid, d, r)) or reply)
    return sent


def test_approve_last_finds_a_claim_the_customer_ui_filed_and_remembers_it(monkeypatch, tmp_path):
    demo = _isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [BROWSER_CLAIM])
    monkeypatch.setattr(demo.evidence, "claim_first_seen_ms", lambda claim_id: 1_700_000_000_000)
    sent = _decided(monkeypatch, demo)
    assert demo.main(["approve", "last"]) == 0
    assert sent == [("PAY-000011", "approve", demo.STAGE_REVIEWER)]
    state = demo.load_state()
    (claim,) = state["claims"]
    assert claim["id"] == "CLM-BROWSER-1" and claim["status"] == "executed"
    assert claim["decision"] == "approve" and claim["task_id"] == "task-b1"
    # The run's window opens where the record says the claim began, less a second.
    assert state["window_start_ms"] == 1_700_000_000_000 - 1000


def test_approve_by_claim_id_finds_it_in_the_reviewer_uis_queue(monkeypatch, tmp_path):
    demo = _isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [BROWSER_CLAIM])
    monkeypatch.setattr(demo.evidence, "claim_first_seen_ms", lambda claim_id: None)
    sent = _decided(monkeypatch, demo)
    assert demo.main(["reject", "CLM-BROWSER-1"]) == 0
    assert sent == [("PAY-000011", "reject", demo.STAGE_REVIEWER)]
    assert demo.load_state()["claims"][0]["status"] == "rejected"


def test_last_prefers_a_claim_that_is_in_both_the_notes_and_the_queue(monkeypatch, tmp_path):
    demo = _isolated(monkeypatch, tmp_path)
    demo.save_state({"run_id": "r", "window_start_ms": 5, "claims": [
        {"id": "CLM-DEMO-AAA", "payment_id": "PAY-000007", "status": "awaiting_approval",
         "amount": 6200.0, "level": "autonomous", "trace_id": "t"}]})
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [
        {**BROWSER_CLAIM, "paymentId": "PAY-000007", "claimId": "CLM-DEMO-AAA"}])
    sent = _decided(monkeypatch, demo)
    assert demo.main(["approve", "last"]) == 0
    assert sent[0][0] == "PAY-000007"
    assert demo.load_state()["window_start_ms"] == 5


def test_an_unknown_claim_id_names_what_it_did_find(monkeypatch, tmp_path, capsys):
    demo = _isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [BROWSER_CLAIM])
    assert demo.main(["approve", "CLM-NOPE"]) == 1
    assert "CLM-BROWSER-1" in capsys.readouterr().err


def test_a_decision_whose_customer_was_not_told_can_be_run_again_and_tells_them(monkeypatch, tmp_path):
    """The money moved but the customer's task was never resumed: the ledger no
    longer lists the payment as waiting, yet `approve last` still finds the claim
    in this run's notes and the reviewer-ui re-sends the resume."""
    demo = _isolated(monkeypatch, tmp_path)
    demo.save_state({"run_id": "r", "window_start_ms": 5, "claims": [
        {"id": "CLM-DEMO-AAA", "payment_id": "PAY-000007", "status": "awaiting_approval",
         "amount": 5800.0, "level": "assisted", "trace_id": "t"}]})
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [])
    _decided(monkeypatch, demo, resumeError="claims-agent unreachable")
    assert demo.main(["approve", "last"]) == 1
    assert demo.load_state()["claims"][0]["status"] == "awaiting_approval"
    sent = _decided(monkeypatch, demo, resentOnly=True)
    assert demo.main(["approve", "last"]) == 0
    assert sent == [("PAY-000007", "approve", demo.STAGE_REVIEWER)]
    assert demo.load_state()["claims"][0]["status"] == "executed"


def test_a_409_from_the_reviewer_ui_is_a_failed_run_not_a_traceback(monkeypatch, tmp_path, capsys):
    demo = _isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [BROWSER_CLAIM])
    monkeypatch.setattr(demo.evidence, "claim_first_seen_ms", lambda claim_id: None)

    def conflict(*a):
        request = demo.httpx.Request("POST", "http://reviewer-ui/api/review/PAY-000011")
        raise demo.httpx.HTTPStatusError("409", request=request, response=demo.httpx.Response(409, request=request))
    monkeypatch.setattr(demo.live, "review", conflict)
    assert demo.main(["approve", "last"]) == 1
    assert "FAIL" in capsys.readouterr().err


def test_evidence_also_reports_claims_a_human_filed_in_the_browser(monkeypatch, tmp_path, capsys):
    demo = _isolated(monkeypatch, tmp_path)
    demo.save_state({"run_id": "r", "window_start_ms": 7, "claims": []})
    monkeypatch.setattr(demo.evidence, "claim_ids_since", lambda window, **kw: ["CLM-BROWSER-1", "CLM-BROWSER-FAILED"])
    row = {
        "policy_decision": "deny", "rule": "settlement-agent.assisted.execute_limit", "limit": 0.0,
        "requested": 5800.0, "autonomy_level": "assisted", "caller_identity": "spiffe://x/settlement-agent",
        "money_hops": [demo.PROPOSE_HOP, demo.EXECUTE_HOP], "executing_identity": "spiffe://x/reviewer-ui",
        "reviewer": "kasper.on.stage", "approved_by": "kasper.on.stage", "human_wait_s": 41.0,
    }
    monkeypatch.setattr(demo.evidence, "evidence_for",
                        lambda cid, window: row if cid == "CLM-BROWSER-1" else {"policy_decision": None})
    def no_opensearch(*a, **k):
        raise AssertionError("`evidence` asked OpenSearch; the demo reads Jaeger")
    monkeypatch.setattr(demo.live, "opensearch", no_opensearch)
    monkeypatch.setattr(demo.live, "search", no_opensearch)
    row["link_trace_id"] = "c" * 32
    assert demo.main(["evidence"]) == 0
    printed = capsys.readouterr().out
    assert "CLM-BROWSER-1" in printed and "CLM-BROWSER-FAILED" not in printed
    assert f"/trace/{'c' * 32}" in printed
    assert "OpenSearch" not in printed and "waiting for Jaeger" not in printed
    assert "approved by" in printed and "kasper.on.stage, after 41 s" in printed
    claims = demo._claims_seen_in_jaeger([], 7)
    assert claims[0]["status"] == "executed" and claims[0]["decision"] == "approve"


def test_guard_opens_the_runs_window_so_a_browser_only_run_has_evidence(monkeypatch, tmp_path):
    demo = _isolated(monkeypatch, tmp_path)
    state = demo.start_run({})
    assert state["claims"] == [] and state["window_start_ms"] > 0
    assert demo.start_run(state) == state, "an open window must be kept, not restarted"


def test_guard_stamps_the_run_before_it_touches_the_cluster(monkeypatch, tmp_path):
    """`guard` is the first command of the flow, so it opens the run's window: a
    run whose only claim is filed in the browser still has one for `evidence`."""
    import pytest
    demo = _isolated(monkeypatch, tmp_path)

    def unreachable():
        raise RuntimeError("no cluster in a unit test")
    monkeypatch.setattr(demo.live, "gateway_port_forward", unreachable)
    with pytest.raises(RuntimeError):
        demo.main(["guard"])
    state = demo.load_state()
    assert state["claims"] == [] and state["window_start_ms"] > 0


# --- a decided claim whose customer was never told -----------------------------

UNTOLD = {**BROWSER_CLAIM, "paymentId": "PAY-000021", "claimId": "CLM-BROWSER-LOST",
          "a2aTaskId": "task-lost", "status": "executed"}


def test_approve_last_heals_a_claim_decided_in_the_browser_whose_resume_was_lost(monkeypatch, tmp_path):
    demo = _isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [])
    monkeypatch.setattr(demo, "untold_payments", lambda: [UNTOLD])
    monkeypatch.setattr(demo.evidence, "claim_first_seen_ms", lambda claim_id: None)
    sent = _decided(monkeypatch, demo, resentOnly=True)
    assert demo.main(["approve", "last"]) == 0
    assert sent == [("PAY-000021", "approve", demo.STAGE_REVIEWER)]
    (claim,) = demo.load_state()["claims"]
    assert claim["id"] == "CLM-BROWSER-LOST" and claim["status"] == "executed"
    assert claim["task_id"] == "task-lost"


def test_approve_by_claim_id_finds_a_decided_untold_claim(monkeypatch, tmp_path):
    demo = _isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [])
    monkeypatch.setattr(demo, "untold_payments", lambda: [UNTOLD])
    monkeypatch.setattr(demo.evidence, "claim_first_seen_ms", lambda claim_id: None)
    sent = _decided(monkeypatch, demo, resentOnly=True)
    assert demo.main(["approve", "CLM-BROWSER-LOST"]) == 0
    assert sent == [("PAY-000021", "approve", demo.STAGE_REVIEWER)]


def test_only_the_decision_the_ledger_holds_can_be_resent(monkeypatch, tmp_path, capsys):
    """Rejecting an executed payment would be a 409 at best; the driver says
    which decision the ledger holds instead of asking the reviewer-ui."""
    demo = _isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [])
    monkeypatch.setattr(demo, "untold_payments", lambda: [UNTOLD])
    monkeypatch.setattr(demo.evidence, "claim_first_seen_ms", lambda claim_id: None)
    sent = _decided(monkeypatch, demo)
    assert demo.main(["reject", "last"]) == 1
    assert sent == []
    assert "already executed on the ledger" in capsys.readouterr().err


def test_a_waiting_claim_in_the_queue_wins_over_a_decided_untold_one(monkeypatch, tmp_path):
    demo = _isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(demo, "awaiting_payments", lambda: [BROWSER_CLAIM])
    monkeypatch.setattr(demo, "untold_payments", lambda: [UNTOLD])
    monkeypatch.setattr(demo.evidence, "claim_first_seen_ms", lambda claim_id: None)
    sent = _decided(monkeypatch, demo)
    assert demo.main(["approve", "last"]) == 0
    assert sent[0][0] == "PAY-000011"


def test_untold_payments_keeps_only_decided_payments_whose_task_still_waits(monkeypatch):
    demo = _driver(stub_untold=False)
    ledger = {"executed": [
        {"paymentId": "PAY-000003", "a2aTaskId": "t3", "status": "executed"},
        {"paymentId": "PAY-000002", "status": "executed"},                       # no A2A task
        {"paymentId": "PAY-000001", "a2aTaskId": "t1", "status": "executed"}],
        "rejected": [{"paymentId": "PAY-000004", "a2aTaskId": "t4", "status": "rejected"}]}
    states = {"t1": "completed", "t3": "input-required", "t4": "input-required"}
    monkeypatch.setattr(demo.live, "list_payments", lambda status: ledger[status])
    monkeypatch.setattr(demo.live, "get_task", lambda task_id: {"state": states[task_id]})
    assert [p["paymentId"] for p in demo.untold_payments()] == ["PAY-000004", "PAY-000003"]


def test_a_claim_filed_in_the_skew_margin_before_the_run_is_not_this_runs(monkeypatch, tmp_path):
    """The window opens 30 s early; discovery must still start at the real run
    start, or the previous test file's claims join `evidence`."""
    demo = _isolated(monkeypatch, tmp_path)
    state = demo.start_run({})
    assert state["window_start_ms"] <= state["run_started_ms"] - 29_000
    seen = {}
    monkeypatch.setattr(demo.evidence, "claim_ids_since",
                        lambda window, filed_after_ms=None: seen.update(w=window, f=filed_after_ms) or [])
    demo.save_state(state)
    demo.main(["evidence"])  # no claims: exits 1, after having asked
    assert seen == {"w": state["window_start_ms"], "f": state["run_started_ms"]}
    adopted = demo.start_run({}, 5_000)
    assert adopted["run_started_ms"] == adopted["window_start_ms"] == 5_000
