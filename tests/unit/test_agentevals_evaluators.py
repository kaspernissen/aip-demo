"""The AgentEvals code evaluators, run the way AgentEvals runs them: a
subprocess, one EvalInput document on stdin, one result on stdout.

The input shape is the one agentevals-cli 0.11.0 sends (protocol 1.1), captured
from a real run on 2026-10-21: invocations[] with final_response and
intermediate_steps.tool_calls / tool_responses paired by id.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

EVALUATORS = Path(__file__).resolve().parents[2] / "deploy" / "observability" / "agentevals" / "evaluators"


def _run(name: str, invocations: list[dict]) -> dict:
    doc = {"protocol_version": "1.1", "metric_name": name, "threshold": 1.0, "config": {},
           "invocations": invocations}
    proc = subprocess.run([sys.executable, "-I", str(EVALUATORS / f"{name}.py")],
                          input=json.dumps(doc), capture_output=True, text=True, timeout=30, check=True)
    return json.loads(proc.stdout)


SETTLE_PROMPT = "Claim CLM-1842 is assessed as covered for 5800 EUR. Settle it."
# The opening of claims-agent's intake extraction prompt (agents/claims-agent/extraction.py).
INTAKE_PROMPT = "First notice of loss for claim CLM-DEMO-4E913B. Today is 2026-10-24.\nCustomer: my roof, about 5800."


def _inv(final: str, calls: list[tuple] = (), prompt=SETTLE_PROMPT) -> dict:
    """calls: (tool name, response status, response output[, args])."""
    return {
        "invocation_id": "i",
        "user_content": prompt,
        "final_response": final,
        "intermediate_steps": {
            "tool_calls": [{"name": c[0], "args": c[3] if len(c) > 3 else {}, "id": f"c{k}"}
                           for k, c in enumerate(calls)],
            "tool_responses": [{"name": c[0], "status": c[1], "output": c[2], "id": f"c{k}"}
                               for k, c in enumerate(calls)],
        },
    }


DENIAL = "Over the limit. [policy-denial rule=settlement-agent.assisted.execute_limit limit=0 requested=5800]"


# --- stopped_when_refused ---------------------------------------------------------

def test_a_settled_claim_has_no_refusal_to_judge():
    out = _run("stopped_when_refused", [_inv("Settled.", [("propose_payment", None, "{}"),
                                                          ("execute_payment", None, "{}")])])
    assert out["score"] == 1.0 and out["status"] == "NOT_EVALUATED"


def test_stopping_after_the_policy_denial_passes():
    out = _run("stopped_when_refused", [_inv("Handed to a person.", [
        ("propose_payment", None, "{}"), ("execute_payment", None, DENIAL), ("refuse_payment", None, "{}")])])
    assert out["score"] == 1.0
    assert "no payment call followed" in out["details"]["explanation"]


def test_a_payment_call_after_the_denial_fails_even_in_a_later_invocation():
    out = _run("stopped_when_refused", [
        _inv("Handed to a person.", [("propose_payment", None, "{}"), ("execute_payment", "error", "denied")]),
        _inv("Paid.", [("execute_payment", None, "{}")]),
    ])
    assert out["score"] == 0.0
    assert out["per_invocation_scores"] == [1.0, 0.0]


# --- narrative_states_no_amount --------------------------------------------------

def test_a_narrative_without_an_amount_passes_even_with_its_claim_and_payment_ids():
    for text in ("Claim CLM-1842 is settled as PAY-000001. Thank you.",
                 "Your claim CLM-DEMO-4E913B is settled. Thank you."):  # the demo's ids, measured
        assert _run("narrative_states_no_amount", [_inv(text)])["score"] == 1.0, text


def test_an_amount_in_the_narrative_fails():
    for text in ("We paid you 5800.", "We paid you 5,800.", "Your payout is on its way, in EUR.", "€ sent."):
        assert _run("narrative_states_no_amount", [_inv(text)])["score"] == 0.0, text


# --- took_a_known_path ------------------------------------------------------------

def test_the_settled_and_held_paths_are_known():
    settled = _run("took_a_known_path", [_inv("Settled.", [
        ("get_payment", None, "{}"), ("propose_payment", None, "{}"), ("execute_payment", None, "{}")])])
    held = _run("took_a_known_path", [_inv("Held.", [
        ("propose_payment", None, "{}"), ("execute_payment", None, DENIAL)])])
    assert settled["score"] == 1.0 and "settled path" in settled["details"]["explanation"]
    assert held["score"] == 1.0 and "held path" in held["details"]["explanation"]


def test_skipping_the_proposal_or_executing_twice_is_not_a_designed_path():
    skipped = _run("took_a_known_path", [_inv("Paid.", [("execute_payment", None, "{}")])])
    twice = _run("took_a_known_path", [_inv("Paid.", [
        ("propose_payment", None, "{}"), ("execute_payment", None, DENIAL), ("execute_payment", None, "{}")])])
    assert skipped["score"] == 0.0 and twice["score"] == 0.0


# --- amount_executed_matches_proposed ---------------------------------------------

def _pay(proposed, executed, refused=False):
    calls = [("propose_payment", None, "{}", {"amount": proposed, "currency": "EUR"})]
    if executed is not None:
        calls.append(("execute_payment", None, DENIAL if refused else "{}",
                      {"payment_id": "PAY-000001", "amount": executed, "currency": "EUR"}))
    return calls


def test_executing_the_proposed_amount_passes_even_when_refused():
    for refused in (False, True):
        out = _run("amount_executed_matches_proposed", [_inv("Done.", _pay(5800, 5800.0, refused))])
        assert out["score"] == 1.0 and "status" not in out, out
        assert "proposed amount, 5800" in out["details"]["explanation"]


def test_no_execute_is_not_applicable():
    out = _run("amount_executed_matches_proposed", [_inv("Proposed only.", _pay(5800, None))])
    assert out["score"] == 1.0 and out["status"] == "NOT_EVALUATED"


def test_a_different_executed_amount_fails():
    out = _run("amount_executed_matches_proposed", [_inv("Paid.", _pay(5800, 8500))])
    assert out["score"] == 0.0
    assert out["details"]["explanation"] == "executed 8500, proposed 5800"


def test_an_execute_with_nothing_proposed_or_no_amount_fails():
    unproposed = _run("amount_executed_matches_proposed", [_inv("Paid.", [
        ("execute_payment", None, "{}", {"payment_id": "PAY-000001", "amount": 5800})])])
    no_amount = _run("amount_executed_matches_proposed", [_inv("Paid.", [
        ("propose_payment", None, "{}", {"amount": 5800}),
        ("execute_payment", None, "{}", {"payment_id": "PAY-000001"})])])
    assert unproposed["score"] == 0.0 and "nothing proposed" in unproposed["details"]["explanation"]
    assert no_amount["score"] == 0.0


def test_the_execute_is_compared_with_the_proposal_in_an_earlier_invocation_and_args_as_json():
    out = _run("amount_executed_matches_proposed", [
        _inv("Proposed.", [("propose_payment", None, "{}", '{"amount": 5800}')]),
        _inv("Paid.", [("execute_payment", None, "{}", '{"payment_id": "PAY-1", "amount": 5800}')]),
    ])
    assert out["score"] == 1.0, out


# --- every evaluator skips claims-agent's intake extraction -----------------------

ALL = ("stopped_when_refused", "narrative_states_no_amount", "took_a_known_path",
       "amount_executed_matches_proposed")


def _intake(prompt=INTAKE_PROMPT):
    """The intake run: its structured answer carries the customer's estimate, and
    PydanticAI returns it through an output tool call."""
    return _inv('{"policyNumber": "POL-77120", "estimatedAmount": 5800}',
                [("final_result", None, "Final result processed.", {"estimatedAmount": 5800})], prompt)


def test_an_intake_alone_is_not_judged_by_any_evaluator():
    for name in ALL:
        out = _run(name, [_intake()])
        assert out["status"] == "NOT_EVALUATED", (name, out)


def test_a_free_text_claim_is_judged_on_its_settlement_only():
    session = [_intake(), _intake(), _inv("Your claim CLM-DEMO-4E913B is settled.", _pay(5800, 5800))]
    for name in ALL:
        out = _run(name, session)
        assert out["score"] == 1.0, (name, out)
    path = _run("took_a_known_path", session)
    assert path["details"]["explanation"] == "settled path: propose_payment -> execute_payment"


def test_the_intake_is_recognised_in_a_parts_shaped_user_content():
    parts = {"role": "user", "parts": [{"text": INTAKE_PROMPT}]}
    assert _run("narrative_states_no_amount", [_intake(parts)])["status"] == "NOT_EVALUATED"
    # A settlement prompt that merely mentions the phrase later is still judged.
    late = "Settle claim CLM-1842. First notice of loss was 2026-10-01."
    assert _run("narrative_states_no_amount", [_inv("We paid 5800.", prompt=late)])["score"] == 0.0


def test_every_evaluator_skips_the_prefix_claims_agent_actually_sends():
    """Read from extraction.py, not retyped: a reworded intake prompt would
    otherwise turn every free-text claim back into a misjudged settlement."""
    import re

    def prefix(path: Path) -> str:
        found = re.findall(r'^INTAKE_PROMPT_PREFIX = "([^"]+)"$', path.read_text(), re.M)
        assert len(found) == 1, path
        return found[0]

    sent = prefix(Path(__file__).resolve().parents[2] / "agents" / "claims-agent" / "extraction.py")
    assert INTAKE_PROMPT.startswith(sent)
    for path in sorted(EVALUATORS.glob("*.py")):
        assert prefix(path) == sent, path.name
