"""Anti-vacuity control: the same claim, refused then settled, live, with
only the contract changed.

**What this file is for.** Every other file in this suite proves a *fixed*
policy holds. This one proves the policy is not fixed at all -- that
flipping one live value changes a real outcome for a real identity, with
nothing else touched. If both halves below could pass while the flag were
silently ignored (the PDS hardcoding `"assisted"`, for instance), this file
would still print PASS and demonstrate nothing -- the same anti-vacuity
concern that this repo's policy-adherence scores guard against, restated
here for a feature flag instead of a score.

**One word in the contract, not one word in the repo:** the flip below is a
live `kubectl patch` of the deployed `FeatureFlag` object, through
`tests/harness/contract.py`. It never reads or writes `deploy/platform/
openfeature/featureflag.yaml`, so the tree this file leaves behind is
byte-identical to the one it started with, and the contract itself is left
in its shipped, refusing state (`assisted`/`assisted`) whether this file's
own assertions pass or raise -- the flip lives in a `try`/`finally`, not in
a final line that a failure upstream could skip.

**The three things this exact flip breaks, handled here rather than assumed
away.**

1. `test_supervised.py`'s criteria 1 and 6, and `test_policy_denial.py`'s
   criterion 1, all hardcode `settlement-agent.assisted.execute_limit` and
   require the junior identity to be *denied*. That is true again the
   instant this file's `finally` restores `assisted` -- which, by
   `pytest`'s own default alphabetical file collection, it always has by
   the time `test_supervised.py` runs (`test_policy_denial.py` sorts
   *before* this file; `test_supervised.py` sorts *after* it). But file
   order is an incidental property of two filenames, not a contract, and a
   crash between the flip and the restore would leak the wrong state into
   whatever ran next regardless of alphabetical luck. So the fix is not
   "rely on file order" -- it is `tests/harness/contract.py::assert_assisted`,
   called at the top of both files' `denial_run` fixture, which fails loudly
   and immediately, naming this file by name, if the live contract is ever
   not `assisted` when a denial-shaped fixture is about to drive one.
2. `platform/eval-scorer/scorer/contract.py`'s `CURRENT_LEVEL` is **not**
   touched by this file, on purpose, and that is a deliberate design choice,
   not an oversight. `CURRENT_LEVEL` must move in the same commit as any
   change to `deploy/platform/openfeature/featureflag.yaml`'s *committed*
   targeting branches -- a live-only patch through this file is not such a
   change, by construction (point above). The honest consequence, stated
   rather than hidden: if anything ever scored a decision made during this
   file's brief `AUTONOMOUS` window, the audit's independent `CURRENT_LEVEL`
   (still `"assisted"`, since the committed file never moved) would
   disagree with the live decision and report `level-does-not-match-contract`.
   That is the design working correctly -- the audit trusts the *published*
   contract, not a transient live patch -- not a defect this file needs to
   route around. Nothing in this repo's scoring path runs against this
   file's own traffic today (`test_policy_scores.py` sorts before this file
   and drives its own, separate claims), so the disagreement is never
   actually triggered, but it is recorded here so a future reader who wires
   scoring into this flow does not mistake the mismatch for a bug in either
   module.
3. This file itself must not be vacuous, which is the point a docstring
   cannot prove -- run `scripts/mutate-guard.sh` against it, and disbelieve
   the paragraph above until you have seen that evidence.

**Cost.** Two live settlements (one refused, one executed), the same one a
full run of `test_policy_denial.py` or `test_supervised.py` already spends
per file -- gated behind `RUN_BROWSER_TESTS=1` for the same reason. The
second test in this file starts no browser and spends no model tokens (a
`kubectl patch` and a handful of `kubectl get pods`), so it carries no such
gate, matching `test_gateway_config_drift.py`'s own stated reason for the
same choice.
"""
from __future__ import annotations

import json
import subprocess
import uuid

import pytest

from tests.harness import contract
from tests.harness.kube import absent_or_fail

from tests.e2e.test_policy_denial import (  # noqa: F401 -- reused, not reimplemented
    ATTR_DECISION,
    ATTR_POLICY_AMOUNT,
    ATTR_POLICY_LIMIT,
    ATTR_POLICY_RULE,
    EXECUTE_TOOL,
    _decisions,
    _drive_browser,
    browser_test,
    claims_agent_route,
)
from tests.harness.live import get_payment as _get_payment  # same ledger-read helper

KUBE_CONTEXT = "kind-northstar"
PLATFORM_NAMESPACE = "northstar-platform"
AGENTS_NAMESPACE = "northstar-agents"
# The clean, separate object that answers "is this at least a stage-5
# cluster" -- distinct from `featureflag/northstar-autonomy` and from the
# policy-decision-service pod this file's real target lives inside, the same
# "a different object than the thing under test" discipline
# `test_policy_scores.py::stage_4_or_a_reason` and
# `test_gateway_config_drift.py` both already hold themselves to.
OFO_NAMESPACE = "open-feature-operator-system"
OFO_DEPLOYMENT = "open-feature-operator-controller-manager"

AMOUNT = 5800.0  # waits at assisted (limit 0), settles at autonomous (limit 6,000)


def _kubectl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module", autouse=True)
def stage_5_or_a_reason():
    """Skip below stage 5; fail on a stage-5 cluster missing the one thing
    this file actually asserts on.

    The discriminator is the Open Feature Operator's own controller
    Deployment -- present the moment `./scripts/setup-kind.sh --stage 5` has
    run, regardless of whether the `FeatureFlag` object or the flagd sidecar
    this file drives are themselves healthy. A stage-5 cluster missing
    either of *those* is this file's own regression and must fail, never
    read as "not stage 5".
    """
    reachable = _kubectl("cluster-info", timeout=30)
    if reachable.returncode != 0:
        pytest.skip(
            f"needs the live cluster: cannot reach {KUBE_CONTEXT} "
            f"({reachable.stderr.strip() or 'no output'}); "
            "run ./scripts/setup-kind.sh --stage 7"
        )

    ofo = _kubectl(
        "-n", OFO_NAMESPACE, "get", f"deployment/{OFO_DEPLOYMENT}", "-o", "name",
        timeout=30,
    )
    if not absent_or_fail(ofo, f"deployment/{OFO_DEPLOYMENT} in {OFO_NAMESPACE}"):
        pytest.skip(
            "the Open Feature Operator is not installed, so this is "
            "coherently below stage 5. Bring it up with "
            "./scripts/setup-kind.sh --stage 7 to run these."
        )


def _pod_uids_and_image_digests(namespace: str) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Every pod in `namespace`, identified by Kubernetes' own UID (assigned
    once per pod object and never reused) and the exact image digests its
    containers are actually running (`status.containerStatuses[*].imageID`,
    not `spec.containers[*].image`, which is only ever the tag this cluster's
    own registry mutates in place on every `docker push` -- the digest is the
    field that changes if and only if the running bytes changed).

    Keyed by pod name so a redeploy that *replaces* one pod with another of
    the same name (a `Recreate` strategy, or any rollout) is still visible: a
    changed UID under the same key, not a missing key.
    """
    result = _kubectl("-n", namespace, "get", "pods", "-o", "json")
    assert result.returncode == 0, (
        f"could not list pods in {namespace}: {result.stderr.strip()}"
    )
    data = json.loads(result.stdout)
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    for pod in data["items"]:
        name = pod["metadata"]["name"]
        uid = pod["metadata"]["uid"]
        images = tuple(
            sorted(
                cs.get("imageID", "")
                for cs in pod.get("status", {}).get("containerStatuses", [])
            )
        )
        out[name] = (uid, images)
    assert out, f"no pods found in {namespace} -- is this really a live cluster?"
    return out


# --- Step 1: the whole claim, in one test ------------------------------------


@browser_test
def test_the_same_claim_refuses_then_settles_when_only_the_contract_changes(
    claims_agent_route,
):
    """The whole claim in one test: submit the identical over-the-assisted-limit
    claim twice, under the junior identity, with nothing between the two runs
    but a live contract flip.

    Both halves assert against the PDS's own `northstar.policy.decision` span (not
    against `.reply`, which carries no `rule` -- `claimId`/`status`/`amount`/
    `paymentId`/`narrative` only, measured by grepping every existing
    `reply.get(...)` call in this suite), the same source
    `test_policy_denial.py`'s own criterion 1 reads the rule from.
    """
    contract.set_targeted_branch(contract.ASSISTED)
    try:
        refused = _drive_browser(f"CLM-AUTONOMY-{uuid.uuid4().hex[:8]}", AMOUNT)
        assert refused.reply.get("status") == "awaiting_approval", refused.reply
        refused_decisions = _decisions(refused.trace, EXECUTE_TOOL)
        assert len(refused_decisions) == 1, (
            f"expected exactly one northstar.policy.decision span for {EXECUTE_TOOL}, "
            f"got {len(refused_decisions)}"
        )
        refused_decision = refused_decisions[0]
        assert refused_decision.attrs.get(ATTR_DECISION) == "deny", refused_decision.attrs
        assert (
            refused_decision.attrs.get(ATTR_POLICY_RULE)
            == "settlement-agent.assisted.execute_limit"
        ), refused_decision.attrs
        assert refused_decision.attrs.get(ATTR_POLICY_LIMIT) == 0, refused_decision.attrs
        assert refused_decision.attrs.get(ATTR_POLICY_AMOUNT) == AMOUNT, refused_decision.attrs

        contract.set_targeted_branch(contract.AUTONOMOUS)

        settled = _drive_browser(f"CLM-AUTONOMY-{uuid.uuid4().hex[:8]}", AMOUNT)
        assert settled.reply.get("status") == "executed", settled.reply
        settled_decisions = _decisions(settled.trace, EXECUTE_TOOL)
        assert len(settled_decisions) == 1, (
            f"expected exactly one northstar.policy.decision span for {EXECUTE_TOOL}, "
            f"got {len(settled_decisions)}"
        )
        settled_decision = settled_decisions[0]
        assert settled_decision.attrs.get(ATTR_DECISION) == "permit", settled_decision.attrs
        assert (
            settled_decision.attrs.get(ATTR_POLICY_RULE)
            == "settlement-agent.autonomous.execute_limit"
        ), settled_decision.attrs
        assert settled_decision.attrs.get(ATTR_POLICY_LIMIT) == 6000, (
            settled_decision.attrs
        )
        assert settled_decision.attrs.get(ATTR_POLICY_AMOUNT) == AMOUNT, settled_decision.attrs

        # "A human was involved" would mean a reviewer-ui approval hop --
        # this settlement never drives reviewer-ui at all, so its absence
        # from the trace is the real, checkable form of "the junior executed
        # this on its own", not a restatement of the rule/decision assertions
        # above.
        assert "reviewer-ui" not in settled.trace.services(), (
            "a reviewer-ui span appeared in a settlement this test never "
            f"drove through reviewer-ui: {settled.trace.services()!r} -- a "
            "human was involved; that is not autonomy"
        )

        # The ledger is the primary evidence for every other file in this
        # suite; this one is no exception.
        payment_id = settled.reply.get("paymentId")
        assert payment_id, f"no paymentId in the executed reply: {settled.reply}"
        record = _get_payment(payment_id)
        assert record.get("status") == "executed", record
        assert record.get("amount") == AMOUNT, record
    finally:
        # Leave the contract in its refusing state whether the
        # assertions above passed or raised -- verified by
        # `set_targeted_branch` itself, not merely attempted.
        contract.set_targeted_branch(contract.ASSISTED)


# --- Step 2: prove the flip touched nothing else ----------------------------


def test_no_workload_was_redeployed_by_the_flip():
    """The claim is "we didn't change a prompt, a deployment, or an image" --
    asserted mechanically, against pod UIDs and image digests, rather than
    left as prose. No browser, no model call: this is a `kubectl patch` and
    two `kubectl get pods`, the same reason `test_gateway_config_drift.py`
    gives for running unconditionally rather than behind
    `RUN_BROWSER_TESTS=1`.
    """
    before = _pod_uids_and_image_digests(AGENTS_NAMESPACE)
    contract.set_targeted_branch(contract.AUTONOMOUS)
    try:
        after = _pod_uids_and_image_digests(AGENTS_NAMESPACE)
        assert before == after, (
            "something in northstar-agents restarted when only the contract "
            f"changed: before={before!r} after={after!r}"
        )
    finally:
        contract.set_targeted_branch(contract.ASSISTED)
