"""M6 Task 3, design §4: the live half of the write-grant proof.

`tests/unit/test_autonomy_writer_rbac.py` parses `deploy/k8s/backstage.yaml`
and pins the Role/RoleBinding/ServiceAccount/Secret objects it declares. That
is necessary but not sufficient -- "a Role that is too broad and a Role that
is correct look identical until something tries the extra verb" (this task's
own brief). Two live-cluster questions the parsed manifest cannot answer by
itself:

1. `resourceNames` scoping is enforced by the API server's authorizer, not by
   anything visible in the YAML alone -- a `resourceNames: [northstar-autonomy]`
   rule that the authorizer somehow ignored would still read correctly here as
   parsed text.
2. Whether this is really the ONLY grant in effect. A second, looser
   Role/RoleBinding applied by hand against the live cluster (not committed,
   so invisible to the unit test) would still leave the committed manifest
   reading narrow while `backstage-autonomy-writer` could do more than it
   claims.

`kubectl auth can-i --as=` is the standard the design and this task's own
brief both name for closing that gap: "Prove with `kubectl auth can-i
--as=<the card's SA>` that patching a different FeatureFlag is denied, that
`delete` is denied, and that Secrets are unreadable."

Not gated behind `RUN_BROWSER_TESTS=1`: like `test_egress_policy.py` and
`test_gateway_config_drift.py`, this spends no model tokens and drives no
browser -- it is a dozen `kubectl auth can-i` calls against the live API
server, over in well under a second each.
"""
from __future__ import annotations

import subprocess

import pytest

from tests.harness.kube import absent_or_fail

KUBE_CONTEXT = "kind-northstar"
NAMESPACE = "northstar-platform"
OTHER_NAMESPACE = "northstar-agents"

WRITER_SA = "system:serviceaccount:northstar-platform:backstage-autonomy-writer"
READ_ONLY_SA = "system:serviceaccount:northstar-platform:backstage"

FLAG = "featureflags.core.openfeature.dev/northstar-autonomy"
FLAG_GROUP_RESOURCE = "featureflags.core.openfeature.dev"
OTHER_FLAG = "featureflags.core.openfeature.dev/some-other-flag"


def _kubectl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _can_i(
    *args: str,
    as_user: str = WRITER_SA,
    namespace: str | None = NAMESPACE,
    all_namespaces: bool = False,
) -> bool:
    """`kubectl auth can-i <args> --as <as_user>`, parsed to a bool.

    Fails the test outright (never a silent False) if kubectl's own answer is
    neither `yes` nor `no` -- an unreachable API server or a malformed
    invocation must not read as "denied", the same discipline
    test_egress_policy.py's `policy_present` fixture argues for its own
    RBAC-adjacent check.
    """
    cmd = ["auth", "can-i", *args, "--as", as_user]
    if all_namespaces:
        cmd += ["-A"]
    elif namespace is not None:
        cmd += ["-n", namespace]
    proc = _kubectl(*cmd)
    lines = proc.stdout.strip().splitlines()
    last = lines[-1] if lines else ""
    if last not in ("yes", "no"):
        pytest.fail(
            f"`kubectl auth can-i {' '.join(args)} --as {as_user}` produced "
            f"neither yes nor no: stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    return last == "yes"


@pytest.fixture(scope="module", autouse=True)
def stage_6_with_the_write_grant_applied_or_a_reason():
    """Skip if this is not even a live cluster; skip (not fail) if the
    cluster is live but stage 6's Backstage Deployment does not exist -- that
    is an absence of stage 6, not a regression; FAIL, never skip, if
    Backstage is deployed but `deploy/k8s/backstage.yaml`'s write grant is
    missing -- the same "empty is a regression, unreachable is a skip"
    discipline `test_egress_policy.py::policy_present` and its own docstring
    name.

    Whole-branch review M4: this used to go straight from the reachability
    probe to asserting the Role exists, which made ANY live stage-1..5
    cluster hard-fail all 15 tests here with a message claiming a regression
    where there was only an absence of stage 6 -- the exact inversion of the
    "skip where you should fail" family this repo otherwise guards against.
    `test_backstage_image_matches_head.py:59-77` and
    `test_golden_path.py:150-166` both already get this right (check
    `deployment/backstage` first, skip if absent; fail on a missing Role only
    once Backstage itself is confirmed present) -- copied verbatim here.

    Three separate questions, deliberately not collapsed into one raw
    exit-code check (ruling M4-R14; `tests/unit/test_kube_helper.py` walks
    this file for exactly that collapse): "can I talk to this cluster at
    all" has no `NotFound` shape to read (`cluster-info` names no object),
    so it stays a raw-returncode reachability probe -- the same shape
    `test_progressive_autonomy.py::stage_5_or_a_reason` uses and is
    allow-listed for. "Does the Deployment exist" and "does the Role exist"
    both DO name an object, so each goes through `absent_or_fail` rather than
    a second raw check.
    """
    reachable = _kubectl("cluster-info", timeout=15)
    if reachable.returncode != 0:
        pytest.skip(
            f"needs the live cluster: cannot reach {KUBE_CONTEXT} "
            f"({reachable.stderr.strip() or 'no output'})"
        )

    deployment = _kubectl(
        "-n", NAMESPACE, "get", "deployment", "backstage", "-o", "name",
    )
    if not absent_or_fail(deployment, f"deployment/backstage in {NAMESPACE}"):
        pytest.skip(
            f"needs stage 6: deployment/backstage does not exist in "
            f"{NAMESPACE}. Run ./scripts/setup-kind.sh --stage 6."
        )

    role = _kubectl(
        "-n", NAMESPACE, "get", "role", "backstage-autonomy-writer", "-o", "name",
    )
    exists = absent_or_fail(role, f"role/backstage-autonomy-writer in {NAMESPACE}")
    assert exists, (
        "Backstage is deployed (stage 6) but "
        "role/backstage-autonomy-writer is missing -- this is a regression, "
        "not an absence of stage 6: run `kubectl apply -f deploy/k8s/backstage.yaml`"
    )


# --- The positive grant: exactly get/list/patch on the one named flag ------


def test_writer_can_get_the_named_flag():
    assert _can_i("get", FLAG), (
        f"{WRITER_SA} cannot GET {FLAG} -- the card's read path is broken"
    )


def test_writer_can_list_the_named_flag():
    assert _can_i("list", FLAG), f"{WRITER_SA} cannot LIST {FLAG}"


def test_writer_can_patch_the_named_flag():
    assert _can_i("patch", FLAG), (
        f"{WRITER_SA} cannot PATCH {FLAG} -- the card's write path is broken"
    )


# --- The negative half: everything design §4 says must be denied -----------


def test_writer_cannot_delete_the_named_flag():
    assert not _can_i("delete", FLAG), (
        f"{WRITER_SA} CAN delete {FLAG} -- design §4: 'No delete.'"
    )


def test_writer_cannot_create_featureflags():
    assert not _can_i("create", FLAG_GROUP_RESOURCE), (
        f"{WRITER_SA} can create {FLAG_GROUP_RESOURCE} objects -- not one of "
        "the three declared verbs (get/list/patch)"
    )


def test_writer_cannot_update_the_named_flag():
    """`update` (whole-object replace) is a different verb from `patch`, and
    design §4 asks for patch only -- this pins that the Role's own verb list
    was never written as (or silently widened to) `["get","list","patch",
    "update"]`."""
    assert not _can_i("update", FLAG), (
        f"{WRITER_SA} can `update` {FLAG} -- only `patch` is declared"
    )


def test_writer_cannot_patch_a_different_featureflag_name():
    """The exact scenario the task brief names: resourceNames scoping,
    proven against the live authorizer rather than assumed from the YAML."""
    assert not _can_i("patch", OTHER_FLAG), (
        f"{WRITER_SA} can patch {OTHER_FLAG} -- resourceNames is not scoping "
        "this Role to northstar-autonomy alone"
    )


def test_writer_cannot_get_a_different_featureflag_name():
    assert not _can_i("get", OTHER_FLAG), (
        f"{WRITER_SA} can read {OTHER_FLAG} -- resourceNames is not enforced "
        "on `get`"
    )


def test_writer_cannot_read_secrets():
    assert not _can_i("get", "secrets"), f"{WRITER_SA} can GET secrets"
    assert not _can_i("list", "secrets"), f"{WRITER_SA} can LIST secrets"


def test_writer_cannot_read_its_own_credential_secret_through_the_api():
    """The token that IS handed to this identity reaches it by being mounted
    into the Backstage pod (deploy/k8s/backstage.yaml), not by this
    ServiceAccount being able to read Secret objects through the API --
    those are different privileges (see that manifest's own comment on the
    point). This is the sharpest version of that claim: not even the one
    Secret whose contents this identity already holds is readable by it
    through the API server."""
    assert not _can_i("get", "secrets/backstage-autonomy-writer-token"), (
        f"{WRITER_SA} can read its own token Secret through the API -- it "
        "should only ever have that token because kubelet mounted it, never "
        "because RBAC lets it ask for Secrets"
    )


def test_writer_cannot_touch_deployments():
    assert not _can_i("get", "deployments"), f"{WRITER_SA} can read Deployments"
    assert not _can_i("patch", "deployments/backstage"), (
        f"{WRITER_SA} can patch the backstage Deployment itself"
    )


def test_writer_has_no_access_in_a_different_namespace():
    assert not _can_i(
        "patch", FLAG, namespace=OTHER_NAMESPACE,
    ), (
        f"{WRITER_SA} can patch {FLAG} in {OTHER_NAMESPACE} -- this Role must "
        f"be namespaced to {NAMESPACE} only"
    )


def test_writer_has_no_cluster_wide_access():
    assert not _can_i("patch", FLAG, all_namespaces=True), (
        f"{WRITER_SA} has cluster-wide access to {FLAG} -- this must be a "
        "namespaced Role, not effectively cluster-scoped"
    )


# --- Controls: M6-R5 must still hold -- the read-only SA is untouched ------


def test_the_read_only_serviceaccount_still_cannot_touch_featureflags_at_all():
    """M6-R5: the read-only ClusterRole must never grow a `get` on
    featureflags just because the write grant now exists on a sibling
    ServiceAccount. Re-asserts Task 1's own verified matrix so a future edit
    to `backstage-read-only` that "fixes" its read by widening it reddens
    here, not only in a task-1-era report nobody re-runs."""
    for verb in ("get", "list", "patch", "delete"):
        assert not _can_i(verb, FLAG, as_user=READ_ONLY_SA), (
            f"the READ-ONLY ServiceAccount {READ_ONLY_SA} can {verb} {FLAG} -- "
            "M6-R5 says this must stay 'no': the card's own separate "
            "ServiceAccount is the only one that touches this resource"
        )


def test_the_two_serviceaccounts_are_actually_different_principals():
    assert WRITER_SA != READ_ONLY_SA, (
        "the writer and read-only ServiceAccount names collapsed to the "
        "same principal -- design §4's whole point is that these are two"
    )
