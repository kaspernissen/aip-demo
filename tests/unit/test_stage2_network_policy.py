"""Guards the network policy that makes the choke point enforceable.

**What this exists to prevent.** The gateway authenticates every MCP
`tools/call`, and a test named
`test_the_gateway_is_the_only_route_into_payments_mcp` checks it -- but that
name is stronger than what the test asserts, and its own docstring says so
honestly: it checks that every `payments-mcp` span *in an observed trace* is
parented to an agentgateway span. That is a claim about traffic that
happened, not traffic that is possible.

The difference was measured, not theorised. Without
`deploy/platform/cilium-policies.yaml` in place, `claims-agent` -- which holds
no SVID and has no reason to touch payments -- can open an MCP `initialize`
directly against `payments-mcp.northstar-tools.svc.cluster.local:8000/mcp` and
receive **HTTP 200**. So "the platform decides if it becomes an effect" holds
only once this policy also constrains who can call at all, not merely for a
caller that chooses to cooperate.

**Why these assertions parse YAML instead of grepping it.** The policy file is
heavily commented, and those comments contain every string a naive guard would
search for -- `payments-mcp`, `agentgateway`, `8000`, `northstar-platform`.
A `"agentgateway" in text` assertion would stay green with the entire `spec`
deleted. This is the inert-guard class `test_stage2_cilium_config.py` had to
fix five times; it is cheaper to not create it. Every assertion below therefore
walks the parsed structure, and the one assertion about `setup-kind.sh` uses
that module's comment-stripping helper for the same reason.

**What this module does NOT prove.** Enforcement. A manifest that parses
correctly and is applied by the setup script can still fail to drop a packet.
That is `tests/e2e/test_gateway_is_the_only_route.py`'s job, which attempts the
bypass against the live cluster and requires it to fail.
"""

from pathlib import Path

import pytest
import yaml

from tests.unit.test_stage2_cilium_config import _effective_script

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = REPO_ROOT / "deploy" / "platform" / "cilium-policies.yaml"

PROTECTED_APP = "payments-mcp"
PROTECTED_NS = "northstar-tools"
PROTECTED_PORT = "8000"
CALLER_APP = "agentgateway"
CALLER_NS = "northstar-platform"
NAMESPACE_LABEL = "k8s:io.kubernetes.pod.namespace"


def _policies():
    """Every non-empty document in the policy file, parsed."""
    return [d for d in yaml.safe_load_all(POLICY.read_text()) if d]


def _gateway_only():
    """The single CiliumNetworkPolicy protecting payments-mcp."""
    hits = [
        d
        for d in _policies()
        if d.get("kind") == "CiliumNetworkPolicy"
        and d.get("spec", {}).get("endpointSelector", {}).get("matchLabels", {}).get("app")
        == PROTECTED_APP
    ]
    assert len(hits) == 1, (
        "expected exactly one CiliumNetworkPolicy selecting "
        f"app={PROTECTED_APP}; found {len(hits)}: "
        f"{[d.get('metadata', {}).get('name') for d in hits]}"
    )
    return hits[0]


# --- The policy itself ------------------------------------------------------


def test_the_policy_is_a_ciliumnetworkpolicy_in_the_tools_namespace():
    """It has to live beside what it protects, or it selects nothing.

    A CiliumNetworkPolicy is namespaced and its endpointSelector only matches
    endpoints in its own namespace. The same manifest in `default` would apply
    cleanly, report `VALID: True`, and protect nothing at all.
    """
    p = _gateway_only()
    assert p["apiVersion"] == "cilium.io/v2"
    assert p["metadata"]["namespace"] == PROTECTED_NS


def test_the_policy_default_denies_by_carrying_an_ingress_block():
    """The deny is implicit and comes from the block's mere presence.

    Cilium has no `deny all` verb here: an endpoint selected by a policy with
    an `ingress` section is default-deny for ingress, and the rules are the
    allowlist. Deleting the `ingress` key therefore does not loosen the policy
    slightly -- it removes protection entirely while leaving a policy object
    that still applies and still reports valid.
    """
    p = _gateway_only()
    assert p["spec"].get("ingress"), (
        "the ingress block is what makes payments-mcp default-deny; without it "
        "this policy selects the endpoint and permits everything"
    )


def test_only_the_gateway_may_reach_the_tool():
    """Exactly one workload is allowed in, and it is the gateway.

    Asserted as a set over every `fromEndpoints` selector in the policy, so
    adding a second allowed workload fails here rather than silently widening
    the choke point -- which is the whole property the milestone claims.
    """
    p = _gateway_only()
    allowed = set()
    for rule in p["spec"]["ingress"]:
        for sel in rule.get("fromEndpoints", []):
            labels = sel.get("matchLabels", {})
            allowed.add((labels.get(NAMESPACE_LABEL), labels.get("app")))

    assert allowed == {(CALLER_NS, CALLER_APP)}, (
        f"exactly one workload may reach {PROTECTED_APP}; got {sorted(allowed)}"
    )


def test_the_gateway_rule_is_scoped_by_namespace_as_well_as_app_label():
    """A bare `app: agentgateway` would be forgeable by any namespace.

    Pod labels are not a security boundary on their own -- anything that can
    create a pod can label it. Pinning the namespace too means the allowance
    names a place in the cluster, not just a string a workload chose for
    itself. Stated as its own test because dropping the namespace label leaves
    the policy valid, applied, and meaningfully weaker.
    """
    p = _gateway_only()
    selectors = [
        sel.get("matchLabels", {})
        for rule in p["spec"]["ingress"]
        for sel in rule.get("fromEndpoints", [])
    ]
    assert selectors, "no fromEndpoints selector at all"
    for labels in selectors:
        assert labels.get(NAMESPACE_LABEL) == CALLER_NS, (
            f"selector {labels} does not pin the namespace; a pod labelled "
            f"app={CALLER_APP} in any namespace would inherit the gateway's "
            "reachability"
        )


def test_the_gateway_rule_is_scoped_to_the_mcp_port():
    """Allowing the gateway everywhere on the pod is broader than intended."""
    p = _gateway_only()
    ports = {
        port.get("port")
        for rule in p["spec"]["ingress"]
        if rule.get("fromEndpoints")
        for block in rule.get("toPorts", [])
        for port in block.get("ports", [])
    }
    assert ports == {PROTECTED_PORT}, (
        f"the gateway rule should be scoped to {PROTECTED_PORT}; got {sorted(ports)}"
    )


def test_the_node_may_still_reach_the_tool_for_its_readiness_probe():
    """The failure this prevents does not look like a policy failure.

    `deploy/k8s/payments-mcp.yaml` uses a `tcpSocket` readinessProbe, and
    kubelet dials it from the node rather than from a pod. Without a `host`
    allowance the probe is dropped, the pod never becomes Ready, and
    `setup-kind.sh` hangs on `rollout status` with no mention of networking
    anywhere in the output -- a very expensive thing to debug on a stage.
    """
    p = _gateway_only()
    entities = {
        e for rule in p["spec"]["ingress"] for e in rule.get("fromEntities", [])
    }
    assert "host" in entities, (
        "kubelet's readiness probe originates on the node; without `host` the "
        f"{PROTECTED_APP} pod never becomes Ready"
    )


def test_the_policy_does_not_reach_beyond_payments_mcp():
    """Scope check: this file protects one tool, not the whole namespace.

    An `endpointSelector: {}` matches *every* endpoint in the namespace, which
    would apply default-deny to anything later added to `northstar-tools` and
    break it in a way that looks unrelated to this file.
    """
    for p in _policies():
        if p.get("kind") != "CiliumNetworkPolicy":
            continue
        selector = p["spec"].get("endpointSelector", {})
        assert selector.get("matchLabels"), (
            f"{p['metadata']['name']} has an empty endpointSelector, which "
            "selects every endpoint in the namespace"
        )


# --- The setup script applies it --------------------------------------------


def test_stage_two_applies_the_policy():
    """A manifest nothing applies protects nothing.

    Uses the comment-stripped script so the assertion cannot be satisfied by
    the block comment that explains the apply.
    """
    script = _effective_script()
    assert "cilium-policies.yaml" in script, (
        "setup-kind.sh --stage 2 must apply deploy/platform/cilium-policies.yaml"
    )


def test_stage_one_does_not_apply_the_policy():
    """Stage 1 has no Cilium, so the CRD does not exist and the apply fails.

    Asserted by locating the reference inside a `STAGE -ge 2` guard rather than
    by running the script, which the cilium module already covers behaviourally.
    """
    script = _effective_script()
    idx = script.index("cilium-policies.yaml")
    preceding = script[:idx]
    assert '"${STAGE}" -ge 2' in preceding, (
        "the policy apply must sit inside a stage-2 guard; stage 1 has no "
        "cilium.io/v2 CRD and the apply would fail the whole script"
    )


@pytest.mark.parametrize("stray", ["kind: NetworkPolicy"])
def test_no_stray_core_networkpolicy_shadows_the_cilium_one(stray):
    """Two policy engines on one endpoint is a debugging trap.

    Kubernetes core NetworkPolicy and CiliumNetworkPolicy compose by union of
    allows, so a core policy added here later would quietly widen the choke
    point while this file still looks correct.
    """
    assert stray not in POLICY.read_text(), (
        "keep this file Cilium-only; a core NetworkPolicy alongside it changes "
        "the effective allowlist in a way none of the assertions above check"
    )


# --- reviewer-ui's egress to payments-api is L7 (M8 review I1) --------------


def test_reviewer_ui_may_reach_payments_api_only_for_list_get_and_reject():
    """payments-api has no caller authentication, so an L4 allow would let the
    reviewer-ui POST /payments/{id}/execute and skip the gateway and the PDS.
    The rule must be L7 and must name exactly the routes ledger.py calls."""
    policy = next(
        d for d in _policies()
        if d["metadata"]["name"] == "reviewer-ui-egress-allowlist"
    )
    to_payments = [
        rule for rule in policy["spec"]["egress"]
        if any(
            e.get("matchLabels", {}).get("app") == "payments-api"
            for e in rule.get("toEndpoints", [])
        )
    ]
    assert len(to_payments) == 1, to_payments
    ports = to_payments[0]["toPorts"]
    assert len(ports) == 1 and ports[0]["ports"] == [{"port": "8080", "protocol": "TCP"}]
    http = ports[0].get("rules", {}).get("http")
    assert http, "the reviewer-ui's payments-api egress is L4 only: it could execute directly"
    assert {(r["method"], r["path"]) for r in http} == {
        ("GET", "/payments(\\?.*)?"),
        ("GET", "/payments/PAY-[0-9]{6}"),
        ("POST", "/payments/PAY-[0-9]{6}/reject"),
    }
    assert not any("execute" in r["path"] or "refuse" in r["path"] for r in http)
