"""The five-minute demo flow, run as the commands themselves.

`scripts/demo` is what the speaker types on stage (spec section 4,
`docs/superpowers/specs/2026-10-13-northstar-m8-human-in-the-loop-design.md`):

    guard -> claim 5800 (WAITS) -> approve last -> autonomy autonomous
          -> claim 5800 (settled by the agent) -> claim 6200 (WAITS)
          -> autonomy assisted -> keystone -> evidence -> reset

This file runs exactly that sequence, **by invoking the driver as a subprocess**
-- not by importing its functions -- so a regression in the shim, the argument
parsing, the exit status or the printed output reddens here. Each test is one
step, they share state through the cluster (the flag, the run's claims in a
private `DEMO_STATE_DIR`), and they are meant to be read and run **in file
order**; a step that fails makes the later ones fail for a stated reason
(`flow.require`) rather than for a confusing one. The browser half of step 2 is
`test_policy_denial.py`'s; this file drives the stage fallback (`approve last`).

**What each step asserts is what the slides say about it**, so a slide cannot
drift from the cluster without this file going red:

* `guard` -- no identity is HTTP 401 at the gateway; the direct road to
  payments-mcp is DROPPED (not refused, not a 401); settlement-agent's SVID
  lists the tools.
* `claim 5800` under `assisted` -- WAITING, the gateway's own span answers
  HTTP 200 with the tool call marked ERROR, the decision is a `deny` under
  `settlement-agent.assisted.execute_limit` against a limit of 0, and the
  `payments-api` **execute** span is absent. `approve last` -- SETTLED by a
  human, and the customer's A2A task reads `completed` (`tasks/get`).
* `autonomy autonomous`, then `claim 5800` -- settled by the agent, a `permit`
  under `settlement-agent.autonomous.execute_limit`, the execute span present,
  and **no pod restarted** across the flip. `claim 6200` -- WAITS: the limit
  the decision span carries is 6000.
* `keystone` -- RBAC `Forbidden`, then Kyverno `DENIED` naming the policy, the
  over-grant gone, the flag still `assisted`.
* `evidence` -- for this run's three claims, from Jaeger (no OpenSearch: the
  command is run with OPENSEARCH_URL pointing at a dead port and must still
  succeed): who decided, the rule, who approved (the stage reviewer for the
  first, none for the other two), the human wait in seconds for the approved
  one, and a Jaeger trace link per claim.
* `reset` -- the resting state: flag `assisted`, over-grant absent, and no
  approval open (the EUR 6,200 claim was rejected as `demo-reset`).

* **the browser path** (`test_7`) -- a claim filed the way the customer-ui files it
  (`live.submit_claim`, not `demo claim`), so the driver's state has never heard
  of it. `approve last` must still find it (it reads the reviewer-ui's queue),
  and `evidence` must still print the human-approval row for it: who approved and
  how long the human took.

One more property is asserted that the driver does not itself print: that a
no is **HTTP 200 carrying an `isError` tool result** (agentgateway v1.6.0;
the rule is in the `[policy-denial ...]` tag at the end of the text), probed
straight at the gateway.

**Gated behind `RUN_BROWSER_TESTS=1`** -- the repo's single cost gate, which
`scripts/smoke-test.sh` sets unconditionally -- although no browser is started:
the three `claim` steps are real model calls, and one **executes a real EUR
5,800 payment** under settlement-agent's own identity with the flag flipped,
which a bare `pytest tests/` must never do.

**The resting state is restored whatever happens.** The module's finalizer runs
`demo reset`, so a red step cannot leave the cluster at `autonomous`, with the
over-grant standing, or with a claim waiting.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest

from tests.harness import contract, live
from tests.harness.jaeger import JaegerClient

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO = REPO_ROOT / "scripts" / "demo"

live_jaeger = JaegerClient()

AMOUNT = 5800
OVER_AUTONOMOUS = 6200

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
        "EUR 5,800 payment under settlement-agent's identity with the flag "
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
        self.state_dir = state_dir

    def claims(self) -> list[dict]:
        """This run's claims, as the driver remembers them."""
        return json.loads((self.state_dir / "state.json").read_text())["claims"]

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
    # Egress is NetworkPolicy: the client's own proxy reports the drop as a
    # proxy error, so the verdict reads "[DROPPED (proxy error)]".
    assert "[DROPPED" in out and "dropped the packets" in out, out
    # Ingress is Linkerd: payments-api's proxy refuses reviewer-ui by identity.
    assert "[REFUSED 403" in out, out
    assert "[200]" in out, out
    # The tool names are read off the gateway's own tools/list for the minted
    # SVID, not typed into the driver.
    for tool in ("propose_payment", "execute_payment", "refuse_payment", "get_payment"):
        assert tool in out, f"{tool} missing from the tools settlement-agent may see:\n{out}"
    # A token is never printed: a JWT is three base64url segments, the first
    # of which always begins `eyJ`.
    assert "eyJ" not in out and "Bearer " not in out, "a bearer credential reached the output"


def test_2_claim_5800_under_assisted_waits_for_a_human_and_no_money_moved(flow):
    flow.require("guard")
    out = flow.ok("claim-waiting", "claim", str(AMOUNT), "--expect", "awaiting_approval")
    assert "[WAITING]" in out and "awaiting human approval" in out, out
    assert "[DENY]" in out, out
    assert "settlement-agent.assisted.execute_limit" in out, out
    assert "limit      0  (requested 5800" in out, out
    # The gateway's own span: HTTP 200, the tool call marked ERROR.
    assert re.search(r"tools/call execute_payment\s+HTTP 200\s+ERROR", out), out
    assert "mcpGuardrails rejected" in out, out
    assert "policy-denial" not in out, "the raw denial tag reached the room:\n" + out
    # The money hop is the absent one -- and only that one.
    assert re.search(r"\[ABSENT\]\s+POST /payments/\{paymentId\}/execute", out), out
    assert "present  POST /payments/propose" in out, out
    assert "scripts/demo approve" in out, out
    assert "http://localhost:16686/trace/" in out, "no Jaeger link was printed:\n" + out


def test_2a_a_human_approves_and_the_customers_task_completes(flow):
    flow.require("claim-waiting")
    (claim,) = flow.claims()
    out = flow.ok("approve", "approve", "last")
    assert "[SETTLED]" in out and "released by a human" in out, out
    assert "[completed]" in out, out
    # The customer's A2A task, asked directly, not through the driver's print.
    task = live.get_task(claim["task_id"])
    assert task["state"] == "completed", task
    assert task["reply"]["status"] == "executed", task
    # Nothing is left waiting.
    assert httpx.get(f"{live.REVIEWER_UI_URL}/api/awaiting", timeout=15).json() == []


def test_2b_a_refusal_is_http_200_carrying_an_is_error_tool_result(flow):
    """The slide's "HTTP 200 means no", measured straight at the gateway, with
    no model in the loop: propose, then execute over the limit."""
    flow.require("claim-waiting")
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
    assert executed.tool_result["isError"] is True, executed
    assert executed.denial.rule == "settlement-agent.assisted.execute_limit", executed
    assert executed.denial.limit == 0.0, executed


def test_3_the_dial_widens_the_band_and_the_human_stays(flow):
    flow.require("approve")
    flow.pods_before_flip = _pods()
    flipped = flow.ok("flip-up", "autonomy", "autonomous")
    assert "[autonomous]" in flipped and "[NONE]" in flipped, flipped
    # flagd itself, independently of the driver's own print.
    assert contract.resolved_level() == contract.AUTONOMOUS

    out = flow.ok("claim-settled", "claim", str(AMOUNT), "--expect", "executed")
    assert "[SETTLED]" in out and "[PERMIT]" in out, out
    assert "settlement-agent.autonomous.execute_limit" in out, out
    assert re.search(r"\[PRESENT\]\s+POST /payments/\{paymentId\}/execute", out), out
    assert "ABSENT" not in out, out

    over = flow.ok("claim-over", "claim", str(OVER_AUTONOMOUS), "--expect", "awaiting_approval")
    assert "[WAITING]" in over and "awaiting human approval" in over, over
    assert "settlement-agent.autonomous.execute_limit" in over, over
    # The limit is read off the decision span, not typed into the driver.
    assert re.search(r"limit\s+6000(\.0)?\s+\(requested 6200", over), over
    assert re.search(r"\[ABSENT\]\s+POST /payments/\{paymentId\}/execute", over), over

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


def test_5_evidence_names_who_decided_and_who_approved_for_each_claim(flow):
    flow.require("claim-waiting", "claim-settled", "claim-over", "keystone")
    # No request to OpenSearch: a dead address must change nothing.
    flow.env["OPENSEARCH_URL"] = "http://127.0.0.1:9"
    out = flow.ok("evidence", "evidence")
    assert "for this run's 3 claim(s)" in out, out
    assert "OpenSearch" not in out, out
    ids = [c["id"] for c in flow.claims()]
    assert len(ids) == 3, ids
    # One section per claim, in the order they were made: a section starts at
    # the claim's own header line (`CLM-DEMO-XXXXXX   EUR 5,800`).
    parts = re.split(r"^(CLM-DEMO-\w+)   EUR [\d,]+$", out, flags=re.M)
    assert parts[1::2] == ids, (parts[1::2], ids)
    sections = [body.split("Every line above")[0] for body in parts[2::2]]
    approved, settled, waiting = sections
    for section in sections:
        assert "policy-decision-service" in section, section
        assert re.search(r"^\s*trace\s+http://localhost:16686/trace/[0-9a-f]{32}\s*$", section, re.M), section
        assert "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent" in section, section
    # 1: agent asked, a human approved.
    assert "[SENT TO A HUMAN]" in approved and "[SETTLED]" in approved, approved
    assert "settlement-agent.assisted.execute_limit" in approved, approved
    assert "autonomy level   assisted" in approved, approved
    assert re.search(r"approved by\s+kasper\.on\.stage, after \d+ s", approved), approved
    assert re.search(r"limit\s+0\s", approved), approved
    # The approval's own trace is the one linked: it holds the review span.
    link = re.search(r"trace\s+http://localhost:16686/trace/([0-9a-f]{32})", approved).group(1)
    review = live_jaeger.get_trace(link).spans_named("northstar.review.decision", "reviewer-ui")
    assert review and review[0].attrs["northstar.review.reviewer.id"] == "kasper.on.stage", link
    assert review[0].attrs["northstar.review.wait.duration"] > 0, review[0].attrs
    # 2: the agent settled it alone, inside the widened band.
    assert "[PERMIT]" in settled and "[SETTLED]" in settled, settled
    assert "settlement-agent.autonomous.execute_limit" in settled, settled
    assert "autonomy level   autonomous" in settled, settled
    assert re.search(r"approved by\s+none\s*$", settled.strip(), re.M), settled
    # 3: over the widened band, still waiting.
    assert "[SENT TO A HUMAN]" in waiting and "[AWAITING HUMAN APPROVAL]" in waiting, waiting
    assert re.search(r"limit\s+6000(\.0)?\s+\(requested 6200", waiting), waiting
    assert re.search(r"approved by\s+none\s*$", waiting.strip(), re.M), waiting


def test_6_reset_rejects_the_open_approval_and_leaves_the_resting_state(flow):
    flow.require("evidence")
    waiting = httpx.get(f"{live.REVIEWER_UI_URL}/api/awaiting", timeout=15).json()
    assert len(waiting) == 1, f"step 3's EUR 6,200 claim should be the one open approval: {waiting}"
    out = flow.ok("reset", "reset")
    assert "open approvals" in out and "(rejected 1)" in out, out
    assert contract.resolved_level() == contract.ASSISTED
    assert contract.get_committed_then_branch() == contract.ASSISTED
    assert not _overgrant_present()
    assert httpx.get(f"{live.REVIEWER_UI_URL}/api/awaiting", timeout=15).json() == []
    # And reset is safe at any time: a second one changes nothing and says so.
    again_reset = flow.ok("reset-again", "reset")
    assert "(rejected 0)" in again_reset, again_reset
    assert _pods() == flow.pods_before_flip, "a pod changed during the demo flow"
    # The run's claims are gone, so a stale `evidence` has nothing to report.
    again = flow.run("evidence-after-reset", "evidence")
    assert again.returncode != 0 and "no claims in this run yet" in again.stderr, again.stderr


def test_7_a_claim_filed_in_the_browser_is_approved_and_evidenced_like_any_other(tmp_path):
    """The on-stage path (spec section 4, step 2) files the claim in the
    customer-ui, which `scripts/demo` knows nothing about. The harness's own
    `submit_claim` is that path without the browser. A fresh state directory
    makes the driver as ignorant of it as it is on stage."""
    reachable = live.kubectl("cluster-info", timeout=30)
    assert reachable.returncode == 0, reachable.stderr
    browser = Flow(tmp_path)
    claim_id = f"CLM-BROWSER-{uuid.uuid4().hex[:6].upper()}"
    try:
        with live.claims_agent_route() as url:
            run = live.submit_claim(claim_id, AMOUNT, claims_agent_url=url, timeout=170)
        assert run["reply"]["status"] == "awaiting_approval", run
        assert not (tmp_path / "state.json").exists(), "the driver must not know this claim yet"

        out = browser.ok("browser-approve", "approve", "last")
        assert claim_id in out and "[SETTLED]" in out and "released by a human" in out, out
        assert "[completed]" in out, out
        # Adopted into the run's state, with the window opened where the claim began.
        (adopted,) = browser.claims()
        assert adopted["id"] == claim_id and adopted["status"] == "executed", adopted
        task = live.get_task(run["task_id"])
        assert task["state"] == "completed" and task["reply"]["status"] == "executed", task

        evidence = browser.ok("browser-evidence", "evidence")
        assert claim_id in evidence, evidence
        assert "[SENT TO A HUMAN]" in evidence and "[SETTLED]" in evidence, evidence
        # The human-approval row: who approved, and how long the human took.
        assert re.search(r"approved by\s+kasper\.on\.stage, after \d+ s", evidence), evidence
    finally:
        browser.run("browser-reset", "reset")
