"""The five-minute demo flow, run as the commands themselves.

`scripts/demo` is what the speaker types on stage (spec section 7,
`docs/superpowers/specs/2026-10-12-northstar-m7-bounded-design.md`):

    guard -> claim 6200 -> autonomy autonomous -> claim 6200 -> autonomy assisted
          -> keystone -> evidence -> reset

This file runs exactly that sequence, **by invoking the driver as a subprocess**
-- not by importing its functions -- so a regression in the shim, the argument
parsing, the exit status or the printed output reddens here. Each test is one
step, they share state through the cluster (the flag, the run's claims in a
private `DEMO_STATE_DIR`), and they are meant to be read and run **in file
order**; a step that fails makes the later ones fail for a stated reason
(`flow.require`) rather than for a confusing one.

**What each step asserts is what the slides say about it**, so a slide cannot
drift from the cluster without this file going red:

* `guard` -- no identity is HTTP 401 at the gateway; the direct road to
  payments-mcp is DROPPED (not refused, not a 401); settlement-agent's SVID
  lists the tools.
* `claim 6200` under `assisted` -- REFUSED, the gateway's own span answers
  HTTP 200 with the tool call marked ERROR, the decision is a `deny` under
  `settlement-agent.assisted.execute_limit` against a limit of 6000, and the
  `payments-api` **execute** span is absent. (Not *every* payments-api span:
  the proposal and the refusal record are there, and the spec's shorthand "no
  payments-api span" was corrected to this by running it.)
* `autonomy autonomous`, then the same claim -- EXECUTED, a `permit` under
  `settlement-agent.autonomous.execute_limit`, the execute span present, and
  **no pod restarted**: start times, UIDs and restart counts of every pod in
  the agents and platform namespaces are compared across the flip, here and
  independently of the driver's own check.
* `keystone` -- RBAC `Forbidden`, then Kyverno `DENIED` naming the policy, the
  over-grant gone, the flag still `assisted`.
* `evidence` -- for this run's two claims, from OpenSearch: who decided, rule,
  limit, caller identity, outcome.
* `reset` -- the resting state: flag `assisted`, over-grant absent.

One more property is asserted that the driver does not itself print: that a
refusal is **HTTP 200 carrying a JSON-RPC error** (`-32001`, with the rule in
`data`), probed straight at the gateway. That is the slide's "HTTP 200 means
no" and it was measured, not assumed.

**Gated behind `RUN_BROWSER_TESTS=1`** -- the repo's single cost gate, which
`scripts/smoke-test.sh` sets unconditionally -- although no browser is started:
the two `claim` steps are real model calls (same reason `test_evidence_view.py`
and `test_settlement_llm_trace.py` carry it). The one thing that makes this
file more than a cost question: the second claim **executes a real EUR 6,200
payment** under settlement-agent's own identity, with the flag flipped, which a
bare `pytest tests/` must never do.

**The resting state is restored whatever happens.** The module's finalizer runs
`demo reset`, so a red step cannot leave the cluster at `autonomous` or with the
over-grant standing -- the one thing the keystone exists to say never exists.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.harness import contract, live

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO = REPO_ROOT / "scripts" / "demo"

AMOUNT = 6200

# A claim is three sequential model turns plus a ~10s trace-quiet wait; 180s
# for the model call matches every other call site, and the driver's own
# internal timeouts are all below this.
STEP_TIMEOUT_S = 300

POLICY = "agents-cannot-edit-their-own-authority"
OVERGRANT_NAME = "keystone-demo-overgrant"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason=(
        "spends two settlements' worth of model tokens and executes a real "
        "EUR 6,200 payment under settlement-agent's identity with the flag "
        "flipped; gated behind the same RUN_BROWSER_TESTS=1 flag "
        "scripts/smoke-test.sh sets unconditionally (no browser is started -- "
        "test_evidence_view.py rides the same flag for the same reason)"
    ),
)


class Flow:
    """The state the ordered steps share, and nothing else."""

    def __init__(self, state_dir: Path):
        self.env = {**os.environ, "DEMO_STATE_DIR": str(state_dir)}
        # Never colour: the assertions read words, and `DEMO_COLOR` is the only
        # thing that would force it on a pipe.
        self.env.pop("DEMO_COLOR", None)
        self.results: dict[str, subprocess.CompletedProcess] = {}
        self.pods_before_flip: dict | None = None
        self.refused_claim: str | None = None
        self.executed_claim: str | None = None

    def run(self, key: str, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [str(DEMO), *args],
            capture_output=True,
            text=True,
            timeout=STEP_TIMEOUT_S,
            env=self.env,
            cwd=REPO_ROOT,
        )
        result.stdout = ANSI.sub("", result.stdout)
        result.stderr = ANSI.sub("", result.stderr)
        self.results[key] = result
        return result

    def ok(self, key: str, *args: str) -> str:
        """Run a step that must exit 0, returning its output; on failure the
        assertion carries the driver's own output so the cause is on screen."""
        result = self.run(key, *args)
        assert result.returncode == 0, (
            f"`demo {' '.join(args)}` exited {result.returncode}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
        return result.stdout

    def require(self, *keys: str) -> None:
        missing = [k for k in keys if k not in self.results]
        assert not missing, (
            f"this step needs {missing} to have run first: the demo flow is an "
            "ordered sequence and an earlier step failed or was deselected"
        )


def _pods() -> dict[str, tuple[str, str, int]]:
    """uid, start time and restart count of every pod in the two namespaces the
    flip could disturb -- read here, not through the driver, so the claim 'no
    pod restarted' is not graded by the thing making it."""
    pods: dict[str, tuple[str, str, int]] = {}
    for ns in ("northstar-agents", "northstar-platform"):
        result = live.kubectl("-n", ns, "get", "pods", "-o", "json", timeout=30)
        assert result.returncode == 0, result.stderr
        for pod in json.loads(result.stdout)["items"]:
            statuses = pod.get("status", {}).get("containerStatuses", [])
            pods[f"{ns}/{pod['metadata']['name']}"] = (
                pod["metadata"]["uid"],
                pod["status"]["startTime"],
                sum(cs.get("restartCount", 0) for cs in statuses),
            )
    assert pods, "no pods found -- is this really a live cluster?"
    return pods


def _overgrant_present() -> bool:
    result = live.kubectl(
        "-n", "northstar-platform", "get", "role,rolebinding", OVERGRANT_NAME,
        "--ignore-not-found", "-o", "name", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return bool(result.stdout.strip())


@pytest.fixture(scope="module")
def flow(tmp_path_factory):
    """The shared state, reset before and after: a leaked `autonomous` from a
    crashed earlier run must not make the first claim execute."""
    reachable = live.kubectl("cluster-info", timeout=30)
    assert reachable.returncode == 0, (
        f"needs the live stage-7 cluster: cannot reach {live.KUBE_CONTEXT} "
        f"({reachable.stderr.strip()}); run ./scripts/setup-kind.sh --stage 7"
    )
    state = Flow(tmp_path_factory.mktemp("demo-state"))
    state.ok("reset-before", "reset")
    try:
        yield state
    finally:
        # Whatever happened above: flag assisted, over-grant absent.
        state.run("reset-after", "reset")


def test_1_guard_no_identity_is_401_the_direct_road_is_dropped_and_the_svid_lists_tools(flow):
    out = flow.ok("guard", "guard")
    assert out.count("[401]") == 2, (
        "both the tools/list and the tools/call with no identity must answer 401:\n" + out
    )
    assert "[REACHED]" in out, "the reach control must answer, or the drop proves an outage:\n" + out
    assert "[DROPPED]" in out and "never arrived" in out, out
    assert "[200]" in out, out
    # The tool names are read off the gateway's own tools/list for the minted
    # SVID, not typed into the driver.
    for tool in ("propose_payment", "execute_payment", "refuse_payment", "get_payment"):
        assert tool in out, f"{tool} missing from the tools settlement-agent may see:\n{out}"
    # A token is never printed: a JWT is three base64url segments, the first
    # of which always begins `eyJ`.
    assert "eyJ" not in out and "Bearer " not in out, "a bearer credential reached the output"


def test_2_claim_6200_under_assisted_is_refused_and_no_money_moved(flow):
    flow.require("guard")
    out = flow.ok("claim-refused", "claim", str(AMOUNT), "--expect", "refused")
    assert "[REFUSED]" in out, out
    assert "[DENY]" in out, out
    assert "settlement-agent.assisted.execute_limit" in out, out
    assert "limit      6000  (requested 6200)" in out, out
    # The gateway's own span: HTTP 200, the tool call marked ERROR.
    assert re.search(r"tools/call execute_payment\s+HTTP 200\s+ERROR", out), out
    assert "mcpGuardrails rejected" in out, out
    # The money hop is the absent one -- and only that one.
    assert re.search(r"\[ABSENT\]\s+POST /payments/\{paymentId\}/execute", out), out
    assert "present  POST /payments/propose" in out, out
    assert "http://localhost:16686/trace/" in out, "no Jaeger link was printed:\n" + out


def test_2b_a_refusal_is_http_200_carrying_a_jsonrpc_error(flow):
    """The slide's "HTTP 200 means no", measured straight at the gateway, with
    no model in the loop: propose, then execute over the limit."""
    flow.require("claim-refused")
    token = live.mint_svid(live.GATEWAY_AUDIENCE)
    headers = {"authorization": f"Bearer {token}"}
    with live.gateway_port_forward() as gateway:
        proposed = live.call_tool_directly(
            gateway, "propose_payment",
            {"claim_id": "CLM-DEMO-FLOW-PROBE", "amount": AMOUNT, "currency": "EUR"},
            headers,
        )
        payment_id = proposed.tool_result["structuredContent"]["paymentId"]
        executed = live.call_tool_directly(
            gateway, "execute_payment",
            {"payment_id": payment_id, "amount": AMOUNT, "currency": "EUR"},
            headers,
        )
    assert executed.http_status == 200, executed
    assert executed.tool_result is None, executed
    assert executed.jsonrpc_error and executed.jsonrpc_error["code"] == -32001, executed
    refusal = executed.jsonrpc_error["data"]["data"]
    assert refusal["rule"] == "settlement-agent.assisted.execute_limit", refusal
    assert refusal["decision"] == "deny" and refusal["limit"] == 6000.0, refusal


def test_3_flipping_the_value_executes_the_same_claim_and_restarts_nothing(flow):
    flow.require("claim-refused")
    flow.pods_before_flip = _pods()
    flipped = flow.ok("flip-up", "autonomy", "autonomous")
    assert "[autonomous]" in flipped and "[NONE]" in flipped, flipped
    # flagd itself, independently of the driver's own print.
    assert contract.resolved_level() == contract.AUTONOMOUS

    out = flow.ok("claim-executed", "claim", str(AMOUNT), "--expect", "executed")
    assert "[EXECUTED]" in out and "[PERMIT]" in out, out
    assert "settlement-agent.autonomous.execute_limit" in out, out
    assert re.search(r"\[PRESENT\]\s+POST /payments/\{paymentId\}/execute", out), out
    assert "ABSENT" not in out, out

    back = flow.ok("flip-down", "autonomy", "assisted")
    assert "[assisted]" in back and "[NONE]" in back, back
    assert contract.resolved_level() == contract.ASSISTED

    # No pod restarted across the flip: same pods, same UIDs, same start
    # times, same restart counts.
    assert _pods() == flow.pods_before_flip, (
        "a pod changed across the autonomy flip -- something was redeployed "
        "when only a value changed"
    )


def test_4_keystone_rbac_forbids_then_kyverno_denies_and_the_grant_is_gone(flow):
    flow.require("flip-down")
    out = flow.ok("keystone", "keystone")
    assert "[Forbidden]" in out and "[RBAC says no]" in out, out
    assert "-> yes" in out, "the over-grant must make RBAC say yes before Kyverno is asked:\n" + out
    assert "[DENIED]" in out and POLICY in out, out
    # The API server's message is wrapped for the room, so compare it with the
    # line breaks folded away.
    flat = " ".join(out.split())
    assert "may not change the objects that define its own authority" in flat, out
    assert "(FeatureFlag/northstar-autonomy)" in flat, out
    assert "never a runtime decision by the agent" in flat, out
    assert "settlement-agent resolves: [assisted]" in out, out
    assert not _overgrant_present(), "the over-grant is still standing after `demo keystone`"
    assert contract.resolved_level() == contract.ASSISTED


def test_5_evidence_names_who_decided_for_both_of_this_runs_claims(flow):
    flow.require("claim-refused", "claim-executed", "keystone")
    out = flow.ok("evidence", "evidence")
    assert "for this run's 2 claim(s)" in out, out
    refused, executed = out.split("EUR 6,200")[1:3]
    for section in (refused, executed):
        assert "policy-decision-service" in section, section
        assert "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent" in section, section
        assert "human approval   none" in section, section
    assert "[DENY]" in refused and "[REFUSED]" in refused, refused
    assert "settlement-agent.assisted.execute_limit" in refused, refused
    assert "limit            6000   (requested 6200)" in refused, refused
    assert "autonomy level   assisted" in refused, refused
    assert "[PERMIT]" in executed and "[EXECUTED]" in executed, executed
    assert "settlement-agent.autonomous.execute_limit" in executed, executed
    assert "autonomy level   autonomous" in executed, executed


def test_6_reset_leaves_the_resting_state(flow):
    flow.require("evidence")
    flow.ok("reset", "reset")
    assert contract.resolved_level() == contract.ASSISTED
    assert contract.get_committed_then_branch() == contract.ASSISTED
    assert not _overgrant_present()
    # And reset is safe at any time: a second one changes nothing and says so.
    flow.ok("reset-again", "reset")
    assert _pods() == flow.pods_before_flip, "a pod changed during the demo flow"
    # The run's claims are gone, so a stale `evidence` has nothing to report.
    again = flow.run("evidence-after-reset", "evidence")
    assert again.returncode != 0 and "no claims in this run yet" in again.stderr, again.stderr
