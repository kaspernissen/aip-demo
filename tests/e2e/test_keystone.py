"""Proves the keystone: an agent identity cannot change the objects that define
its own authority, even when RBAC would let it.

Needs the live stage-7 cluster and nothing else -- like
`test_gateway_is_the_only_route.py` it spends no model tokens: it is a handful
of `kubectl --as=...` calls against the real API server.

**The two lines, and why both are tested.** RBAC already denies every agent
ServiceAccount a write to the autonomy flag, so on a correct cluster an agent's
patch is an RBAC `Forbidden`. That is the first line, and
`test_rbac_alone_refuses_the_agent` pins it. It is NOT the keystone: the
keystone is the second line, the one that holds when the first is wrong. So
`test_kyverno_refuses_the_agent_even_when_rbac_allows_it` makes RBAC wrong
(applying `deploy/demo/keystone-overgrant.yaml`, the demo fixture that models
the mistake) and asserts the refusal now comes from Kyverno. It asserts the
POLICY NAME in the message, so an RBAC `Forbidden` cannot pass for it -- the
one substitution that would make this file green on a cluster with no Kyverno.

**Every test that mutates the cluster restores it in a `finally`**: the
over-grant is deleted, and the flag is put back to `assisted` by a human
context whether or not the agent's patch (wrongly) landed. A test run that
leaves a standing over-grant would be the very thing the ring exists to say
never exists.

**Present on a stage-7 cluster, or these tests FAIL -- never skip.** The same
defect `test_gateway_is_the_only_route.py`'s `policy_present` fixture documents:
a fixture that skips when the policy is missing turns "the policy is no longer
applied" -- the one event this file exists to catch -- into a green run.
`keystone_armed` therefore separates *not a stage-7 cluster* (no Kyverno CRD at
all: skip) from *Kyverno installed but the policy absent or not Ready* (the
regression: fail, loudly). Verified by mutation: deleting the live policy turns
these tests red, not skipped.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
import requests

from tests.harness.jaeger import JaegerClient, Trace
from tests.harness.kube import absent_or_fail

KUBE_CONTEXT = "kind-northstar"
REPO_ROOT = Path(__file__).resolve().parents[2]
OVERGRANT = REPO_ROOT / "deploy" / "demo" / "keystone-overgrant.yaml"

POLICY = "agents-cannot-edit-their-own-authority"
POLICY_CRD = "validatingpolicies.policies.kyverno.io"

NAMESPACE = "northstar-platform"
AGENTS_NAMESPACE = "northstar-agents"
# Built, not quoted: test_no_legacy_names.py reads a quoted `policy.<x>` string as a
# retired telemetry name, and an API group has the same shape.
LINKERD_GROUP = "policy" + ".linkerd.io"
FLAG = "featureflag/northstar-autonomy"
AGENT = "system:serviceaccount:northstar-agents:settlement-agent"

# The one path Backstage's own write patches.
THEN_PATH = "/spec/flagSpec/flags/autonomy-level/targeting/if/1"
PATCH_AUTONOMOUS = json.dumps(
    [{"op": "replace", "path": THEN_PATH, "value": "autonomous"}]
)
PATCH_ASSISTED = json.dumps(
    [{"op": "replace", "path": THEN_PATH, "value": "assisted"}]
)


# MEASURED on the live stage-7 cluster (Kyverno chart 3.9.1 / app v1.19.1),
# not assumed: Kyverno's admission controller exports OTLP traces as the
# service below, and a denied admission produces a span with this operation
# name -- `VALIDATE <operation> <group>/<version>, Kind=<kind>`. The policy name
# is not an attribute of its own; it is inside `admission.response.result.message`.
KYVERNO_SERVICE = "kyverno-admission-controller"
DENIAL_OPERATION = "VALIDATE UPDATE core.openfeature.dev/v1beta1, Kind=FeatureFlag"


def _kubectl(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _as_agent(*args: str) -> subprocess.CompletedProcess:
    return _kubectl("--as", AGENT, *args)


def _flag_then_branch() -> str:
    proc = _kubectl(
        "-n", NAMESPACE, "get", FLAG,
        "-o", "jsonpath={.spec.flagSpec.flags.autonomy-level.targeting.if[1]}",
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _restore_flag() -> None:
    """Put the flag back as a human, whatever an agent's patch did."""
    _kubectl("-n", NAMESPACE, "patch", FLAG, "--type=json", "-p", PATCH_ASSISTED)


def _wait_until_agent_can_patch(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        proc = _as_agent("-n", NAMESPACE, "auth", "can-i", "patch", FLAG)
        if proc.stdout.strip() == "yes":
            return
        time.sleep(1)
    pytest.fail(
        "the over-grant was applied but the authorizer never admitted the "
        "agent's patch, so a refusal below would prove nothing about Kyverno"
    )


@pytest.fixture(scope="module")
def keystone_armed():
    """A stage-7 cluster with the keystone Ready, or the tests FAIL.

    See the module docstring: skipping on a missing policy would let the exact
    regression this file exists to catch leave the suite green.
    """
    crd = _kubectl("get", "crd", POLICY_CRD, "-o", "name")
    if not absent_or_fail(crd, f"the {POLICY_CRD} CRD"):
        pytest.skip(
            "not a stage-7 cluster (Kyverno's ValidatingPolicy CRD is absent); "
            "run ./scripts/setup-kind.sh --stage 7"
        )
    policy = _kubectl(
        "get", "validatingpolicy", POLICY,
        "-o", "jsonpath={.status.conditionStatus.ready}",
    )
    assert policy.returncode == 0 and policy.stdout.strip() == "true", (
        "Kyverno is installed but the keystone policy is NOT applied or NOT "
        "Ready, so an agent that is over-granted can edit the objects that "
        "define its own authority and nothing says no. Re-apply "
        "deploy/platform/kyverno/keystone-policy.yaml "
        f"(kubectl said: {policy.stderr.strip() or policy.stdout.strip() or 'no output'})"
    )
    stale = _kubectl(
        "-n", NAMESPACE, "get", "role,rolebinding", "keystone-demo-overgrant",
        "--ignore-not-found", "-o", "name",
    )
    assert stale.returncode == 0 and not stale.stdout.strip(), (
        "the keystone demo over-grant is already applied; it is a standing "
        "grant for settlement-agent to patch the autonomy flag. Remove it: "
        "kubectl delete -f deploy/demo/keystone-overgrant.yaml"
    )
    return POLICY


def test_rbac_alone_refuses_the_agent(keystone_armed):
    """The first line: with no over-grant, the agent's patch is RBAC Forbidden.

    Not the keystone, and asserted not to be: the message must not carry the
    policy's name, or this test would be passing on Kyverno and the next one
    would be proving nothing new.
    """
    before = _flag_then_branch()
    try:
        proc = _as_agent(
            "-n", NAMESPACE, "patch", FLAG, "--type=json", "-p", PATCH_AUTONOMOUS
        )
    finally:
        if _flag_then_branch() != before:
            _restore_flag()
    assert proc.returncode != 0, (
        "the agent patched the autonomy flag with no over-grant at all, so "
        "RBAC is wider than it should be"
    )
    assert "Forbidden" in proc.stderr, proc.stderr
    assert POLICY not in proc.stderr, (
        "the refusal already names the keystone with no over-grant applied; "
        f"RBAC is not the first line any more: {proc.stderr}"
    )


def test_kyverno_refuses_the_agent_even_when_rbac_allows_it(keystone_armed):
    """The second line: over-grant the agent, and Kyverno still says no."""
    try:
        applied = _kubectl("apply", "-f", str(OVERGRANT))
        assert applied.returncode == 0, applied.stderr
        _wait_until_agent_can_patch()
        proc = _as_agent(
            "-n", NAMESPACE, "patch", FLAG, "--type=json", "-p", PATCH_AUTONOMOUS
        )
        landed = proc.returncode == 0
    finally:
        # Both, in this order: take the flag back as a human (an agent patch
        # that wrongly succeeded must not outlive the test), then the grant.
        _restore_flag()
        _kubectl("delete", "-f", str(OVERGRANT), "--ignore-not-found")

    assert not landed, (
        "the over-granted agent changed the autonomy flag. The keystone did "
        "not fire: an agent can edit the contract that bounds it."
    )
    assert POLICY in proc.stderr, (
        f"the patch was refused, but not by the keystone ('{POLICY}' is not "
        f"in the message, so this is RBAC or something else): {proc.stderr}"
    )
    assert "DENIED: agent identity" in proc.stderr, proc.stderr
    assert AGENT in proc.stderr, "the message must name the agent identity"
    assert "featureflag" in proc.stderr.lower() or "FeatureFlag" in proc.stderr
    assert "reviewed commit" in proc.stderr, proc.stderr
    assert _flag_then_branch() == "assisted"


def test_a_human_context_can_still_change_the_flag_and_it_is_reverted(
    keystone_armed,
):
    """Control: the policy is about WHO, not about the flag being untouchable.

    Without this, the denials above would also be produced by a policy that
    refuses everyone -- including Backstage's flip in beat 7, which would then
    fail on stage. The current kubectl context is a human (cluster-admin).
    """
    try:
        proc = _kubectl(
            "-n", NAMESPACE, "patch", FLAG, "--type=json", "-p", PATCH_AUTONOMOUS
        )
        changed = _flag_then_branch()
    finally:
        _restore_flag()
    assert proc.returncode == 0, (
        "a human could not patch the autonomy flag; the keystone is refusing "
        f"everyone, not just agents: {proc.stderr}"
    )
    assert changed == "autonomous"
    assert _flag_then_branch() == "assisted", "the flag was not restored"


def test_an_agent_cannot_create_a_policy_exception(keystone_armed):
    """An exception is a bypass, so creating one is an edit to the constraint.

    As with the flag, RBAC denies this to an agent already, so the agent is
    over-granted `create` on policyexceptions for the duration of the test and
    the refusal must name the policy. Everything created here is removed in
    the `finally`.
    """
    role = "keystone-test-exception-grant"
    exception = {
        "apiVersion": "policies.kyverno.io/v1",
        "kind": "PolicyException",
        "metadata": {"name": "agent-self-exemption", "namespace": AGENTS_NAMESPACE},
        "spec": {
            "policyRefs": [{"name": POLICY, "kind": "ValidatingPolicy"}],
            "matchConditions": [{"name": "everything", "expression": "true"}],
        },
    }
    grant = {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "Role",
                "metadata": {"name": role, "namespace": AGENTS_NAMESPACE},
                "rules": [{
                    "apiGroups": ["policies.kyverno.io"],
                    "resources": ["policyexceptions"],
                    "verbs": ["create", "get", "delete"],
                }],
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {"name": role, "namespace": AGENTS_NAMESPACE},
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "Role",
                    "name": role,
                },
                "subjects": [{
                    "kind": "ServiceAccount",
                    "name": "settlement-agent",
                    "namespace": AGENTS_NAMESPACE,
                }],
            },
        ],
    }
    created = None
    try:
        applied = subprocess.run(
            ["kubectl", "--context", KUBE_CONTEXT, "apply", "-f", "-"],
            input=json.dumps(grant), capture_output=True, text=True, timeout=60,
        )
        assert applied.returncode == 0, applied.stderr
        deadline = time.time() + 30
        while time.time() < deadline:
            can = _as_agent(
                "-n", AGENTS_NAMESPACE, "auth", "can-i", "create",
                "policyexceptions.policies.kyverno.io",
            )
            if can.stdout.strip() == "yes":
                break
            time.sleep(1)
        created = subprocess.run(
            ["kubectl", "--context", KUBE_CONTEXT, "--as", AGENT,
             "create", "-f", "-"],
            input=json.dumps(exception), capture_output=True, text=True,
            timeout=60,
        )
    finally:
        _kubectl(
            "-n", AGENTS_NAMESPACE, "delete", "policyexception",
            "agent-self-exemption", "--ignore-not-found",
        )
        _kubectl(
            "-n", AGENTS_NAMESPACE, "delete", "role,rolebinding", role,
            "--ignore-not-found",
        )
    assert created is not None and created.returncode != 0, (
        "an agent created a PolicyException against the keystone: it can "
        "exempt itself from the constraint"
    )
    assert POLICY in created.stderr and "DENIED" in created.stderr, created.stderr


def _find_denial_trace(started_at_s: float, timeout: float = 60.0) -> Trace:
    """The Kyverno trace for a denial made after `started_at_s`.

    Searched by service and operation and then filtered by start time and
    attributes, because there is no trace ID to wait for: kubectl does not
    propagate a `traceparent`, so the test cannot mint one (see the test below).
    """
    base = JaegerClient().base_url
    floor_us = int((started_at_s - 1) * 1_000_000)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{base}/api/traces",
                params={
                    "service": KYVERNO_SERVICE,
                    "operation": DENIAL_OPERATION,
                    "lookback": "1h",
                    "limit": 50,
                },
                timeout=15,
            )
            resp.raise_for_status()
            for raw in resp.json().get("data") or []:
                trace = Trace(raw)
                for span in trace.spans:
                    start = next(
                        s["startTime"] for s in raw["spans"]
                        if s["spanID"] == span.span_id
                    )
                    if (
                        span.operation == DENIAL_OPERATION
                        and start >= floor_us
                        and span.attrs.get("admission.request.user.name") == AGENT
                    ):
                        return trace
        except requests.RequestException:
            pass
        time.sleep(2)
    pytest.fail(
        f"no {KYVERNO_SERVICE!r} span {DENIAL_OPERATION!r} for {AGENT} reached "
        f"Jaeger within {timeout}s of the denial. Is Kyverno's tracing pointed "
        "at the collector's gRPC port (deploy/platform/kyverno/values.yaml)?"
    )


def test_the_denial_reaches_jaeger_as_a_standalone_kyverno_trace(keystone_armed):
    """Which refusals the trace can see: this one, but not inside the claim's trace.

    Kyverno's span carries the denial -- the identity, the object, `allowed =
    False` and the message naming the policy. It is a trace of its OWN:
    `kubectl` does not propagate a `traceparent`, and Kyverno only continues a
    trace the caller starts, so the span is a root with no agent span above or
    beside it. That is asserted rather than papered over: if the API server is
    ever given trace propagation to webhooks this assertion goes red, and the
    deck's "standalone" claim must change with it.
    """
    started = time.time()
    try:
        assert _kubectl("apply", "-f", str(OVERGRANT)).returncode == 0
        _wait_until_agent_can_patch()
        proc = _as_agent(
            "-n", NAMESPACE, "patch", FLAG, "--type=json", "-p", PATCH_AUTONOMOUS
        )
    finally:
        _restore_flag()
        _kubectl("delete", "-f", str(OVERGRANT), "--ignore-not-found")
    assert proc.returncode != 0 and POLICY in proc.stderr, proc.stderr

    trace = _find_denial_trace(started)
    denial = next(s for s in trace.spans if s.operation == DENIAL_OPERATION)
    assert denial.attrs["admission.response.allowed"] is False
    assert denial.attrs["admission.request.operation"] == "UPDATE"
    assert denial.attrs["admission.request.kind.kind"] == "FeatureFlag"
    assert denial.attrs["admission.request.name"] == "northstar-autonomy"
    assert denial.attrs["admission.request.namespace"] == NAMESPACE
    assert POLICY in denial.attrs["admission.response.result.message"]
    # Standalone: one service, one root, no parent from anything else.
    assert trace.services() == {KYVERNO_SERVICE}, trace.services()
    assert len(trace.roots()) == 1 and trace.roots()[0].parent_id is None


# --- Coverage beyond the flag: every ring's configuration, and identity ------
#
# The slide says "the agent can never edit what constrains it", so the policy
# has to hold for each ring's configuration and for the ways an agent could
# ACQUIRE another identity, not just for the one object beat 7 flips. Each case
# below over-grants the agent the one verb it needs (a temporary
# ClusterRole/ClusterRoleBinding, removed in the `finally`), has it make the
# request, and asserts the refusal names the policy -- so RBAC, which would
# otherwise have refused first, cannot pass for Kyverno.

GRANT_NAME = "keystone-test-grant"

CASES = {
    "pod-as-reviewer-ui": dict(
        # The identity edit: a pod running as another ServiceAccount is issued
        # that ServiceAccount's SVID, with no RBAC object touched at all. The
        # target is reviewer-ui since M8: an agent that could run a pod as it
        # could forge a human approval (the PDS permits that identity to
        # release a payment awaiting approval).
        rules=[{"apiGroups": [""], "resources": ["pods"], "verbs": ["create"]}],
        manifest={
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "keystone-test-pod", "namespace": AGENTS_NAMESPACE},
            "spec": {
                "serviceAccountName": "reviewer-ui",
                "containers": [{
                    "name": "pause",
                    "image": "registry.k8s.io/pause:3.10",
                }],
            },
        },
        cleanup=["-n", AGENTS_NAMESPACE, "delete", "pod", "keystone-test-pod",
                 "--ignore-not-found", "--wait=false"],
        names="Pod/keystone-test-pod",
    ),
    "network-policy": dict(
        # Reach ring, egress half (M12): core NetworkPolicy, enforced by the
        # node. An agent that could add one could add an allow next to the
        # allowlist that keeps it off the model provider and the tools.
        rules=[{"apiGroups": ["networking.k8s.io"],
                "resources": ["networkpolicies"], "verbs": ["create"]}],
        manifest={
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "keystone-test-netpol",
                         "namespace": AGENTS_NAMESPACE},
            "spec": {
                "podSelector": {"matchLabels": {"keystone-test": "never"}},
                "policyTypes": ["Egress"],
                "egress": [{}],
            },
        },
        cleanup=["-n", AGENTS_NAMESPACE, "delete", "networkpolicy",
                 "keystone-test-netpol", "--ignore-not-found"],
        names="NetworkPolicy/keystone-test-netpol",
    ),
    "linkerd-authorization-policy": dict(
        # Reach ring, ingress half (M12): who may call the money path is a
        # Linkerd AuthorizationPolicy. Every policy.linkerd.io kind is covered
        # (Server, HTTPRoute, the *Authentication kinds), this is the one that
        # would admit a caller.
        rules=[{"apiGroups": [LINKERD_GROUP],
                "resources": ["authorizationpolicies"], "verbs": ["create"]}],
        manifest={
            "apiVersion": "policy.linkerd.io/v1alpha1",
            "kind": "AuthorizationPolicy",
            "metadata": {"name": "keystone-test-authz",
                         "namespace": "northstar-tools"},
            "spec": {
                "targetRef": {"group": LINKERD_GROUP, "kind": "Server",
                              "name": "payments-mcp"},
                "requiredAuthenticationRefs": [{
                    "group": LINKERD_GROUP, "kind": "NetworkAuthentication",
                    "name": "keystone-never"}],
            },
        },
        cleanup=["-n", "northstar-tools", "delete", "authorizationpolicy",
                 "keystone-test-authz", "--ignore-not-found"],
        names="AuthorizationPolicy/keystone-test-authz",
    ),
    "spire-clusterspiffeid": dict(
        # Identity ring: who is issued which SVID.
        rules=[{"apiGroups": ["spire.spiffe.io"],
                "resources": ["clusterspiffeids"], "verbs": ["create"]}],
        manifest={
            "apiVersion": "spire.spiffe.io/v1alpha1",
            "kind": "ClusterSPIFFEID",
            "metadata": {"name": "keystone-test-spiffeid"},
            "spec": {
                "spiffeIDTemplate": "spiffe://northstar.local/keystone-test",
                "podSelector": {"matchLabels": {"keystone-test": "never"}},
            },
        },
        cleanup=["delete", "clusterspiffeid", "keystone-test-spiffeid",
                 "--ignore-not-found"],
        names="ClusterSPIFFEID/keystone-test-spiffeid",
    ),
    "kyverno-legacy-cluster-policy": dict(
        # Every kind in Kyverno's own groups, not just the two the first
        # version of the policy named.
        rules=[{"apiGroups": ["kyverno.io"], "resources": ["clusterpolicies"],
                "verbs": ["create"]}],
        manifest={
            "apiVersion": "kyverno.io/v1",
            "kind": "ClusterPolicy",
            "metadata": {"name": "keystone-test-clusterpolicy"},
            "spec": {"rules": [{
                "name": "noop",
                "match": {"any": [{"resources": {"kinds": ["ConfigMap"],
                                                 "names": ["keystone-never"]}}]},
                "validate": {"message": "never", "deny": {}},
            }]},
        },
        cleanup=["delete", "clusterpolicy", "keystone-test-clusterpolicy",
                 "--ignore-not-found"],
        names="ClusterPolicy/keystone-test-clusterpolicy",
    ),
}


def _apply_json(manifest: dict, *, as_user: str | None = None, verb: str = "create"):
    cmd = ["kubectl", "--context", KUBE_CONTEXT]
    if as_user:
        cmd += ["--as", as_user]
    cmd += [verb, "-f", "-"]
    return subprocess.run(
        cmd, input=json.dumps(manifest), capture_output=True, text=True, timeout=60
    )


def _over_grant_cluster_wide(
    rules: list[dict], can_i: list[str] | None = None
) -> None:
    grant = {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
             "metadata": {"name": GRANT_NAME}, "rules": rules},
            {"apiVersion": "rbac.authorization.k8s.io/v1",
             "kind": "ClusterRoleBinding", "metadata": {"name": GRANT_NAME},
             "roleRef": {"apiGroup": "rbac.authorization.k8s.io",
                         "kind": "ClusterRole", "name": GRANT_NAME},
             "subjects": [{"kind": "ServiceAccount", "name": "settlement-agent",
                           "namespace": AGENTS_NAMESPACE}]},
        ],
    }
    applied = _apply_json(grant, verb="apply")
    assert applied.returncode == 0, applied.stderr
    resource = rules[0]["resources"][0]
    group = rules[0]["apiGroups"][0]
    target = f"{resource}.{group}" if group else resource
    check = can_i or [rules[0]["verbs"][0], target, "-A"]
    deadline = time.time() + 30
    while time.time() < deadline:
        can = _as_agent("auth", "can-i", *check)
        if can.stdout.strip() == "yes":
            return
        time.sleep(1)
    pytest.fail(f"the temporary grant on {target} never took effect")


def _remove_cluster_wide_grant() -> None:
    _kubectl("delete", "clusterrolebinding,clusterrole", GRANT_NAME,
             "--ignore-not-found")


@pytest.mark.parametrize("case", sorted(CASES))
def test_an_over_granted_agent_is_refused_by_kyverno_on_every_ring(
    keystone_armed, case
):
    spec = CASES[case]
    created = None
    try:
        _over_grant_cluster_wide(spec["rules"])
        created = _apply_json(spec["manifest"], as_user=AGENT)
    finally:
        _kubectl(*spec["cleanup"])
        _remove_cluster_wide_grant()
    assert created is not None and created.returncode != 0, (
        f"an over-granted agent created {spec['names']}: the keystone does "
        "not cover this object"
    )
    assert POLICY in created.stderr and "DENIED" in created.stderr, (
        f"refused, but not by the keystone: {created.stderr}"
    )
    assert spec["names"] in created.stderr, created.stderr


def test_an_over_granted_agent_cannot_mint_another_serviceaccounts_token(
    keystone_armed,
):
    """`serviceaccounts/token`: minting a token for reviewer-ui is
    acquiring its identity by a shorter route than creating a pod -- and that
    identity is the one that releases human-approved payments (M8)."""
    try:
        _over_grant_cluster_wide([{
            "apiGroups": [""], "resources": ["serviceaccounts/token"],
            "verbs": ["create"],
        }], can_i=["create", "serviceaccounts", "--subresource=token",
                   "-n", AGENTS_NAMESPACE])
        proc = _as_agent(
            "-n", AGENTS_NAMESPACE, "create", "token", "reviewer-ui"
        )
    finally:
        _remove_cluster_wide_grant()
    assert proc.returncode != 0, (
        "an over-granted agent minted a token for reviewer-ui: "
        "serviceaccounts/token is not covered"
    )
    assert POLICY in proc.stderr and "DENIED" in proc.stderr, proc.stderr


def test_an_over_granted_agent_cannot_patch_its_namespaces_mesh_annotation(
    keystone_armed,
):
    """The mesh is switched on by an annotation on the Namespace
    (`linkerd.io/inject: enabled`). An agent that could patch it could have the
    next pod in its own namespace start with no proxy and no mesh identity,
    outside every Linkerd policy. `namespaces` is a keystone ring for exactly
    that reason; this asserts the UPDATE, not just the CREATE, is refused."""
    try:
        # `get` as well: `kubectl annotate` reads the object before it patches,
        # and without it RBAC refuses the read and Kyverno never sees the UPDATE.
        _over_grant_cluster_wide(
            [{"apiGroups": [""], "resources": ["namespaces"],
              "verbs": ["get", "patch"]}],
            can_i=["patch", "namespaces", "-A"],
        )
        proc = _as_agent(
            "annotate", "namespace", AGENTS_NAMESPACE,
            "linkerd.io/inject=disabled", "--overwrite",
        )
    finally:
        # Never leave the namespace unmeshed, whatever the refusal did.
        _kubectl("annotate", "namespace", AGENTS_NAMESPACE,
                 "linkerd.io/inject=enabled", "--overwrite")
        _remove_cluster_wide_grant()
    assert proc.returncode != 0, (
        "an over-granted agent changed its namespace's linkerd.io/inject "
        "annotation: `namespaces` is not covered on UPDATE"
    )
    assert POLICY in proc.stderr and "DENIED" in proc.stderr, proc.stderr
