"""The keystone policy and its over-grant fixture, guarded hermetically.

`deploy/platform/kyverno/keystone-policy.yaml` is the fourth ring: an agent
identity cannot change the objects that define its own authority. Everything
here parses the YAML (`yaml.safe_load`), so no assertion can be satisfied by a
comment; the behavioural proof that Kyverno actually refuses is
`tests/e2e/test_keystone.py`, which needs the live stage-7 cluster.

Why these particular properties, each of which is a way the ring quietly stops
being one:

* matched by PREFIX -- a list of service accounts means an agent added
  tomorrow is not covered, and nothing goes red;
* all five object groups -- dropping one (say RBAC) leaves a bypass that looks
  like a policy;
* `failurePolicy: Fail` -- `Ignore` turns "Kyverno is down" into "agents may
  edit their own authority";
* `Deny` -- `Audit` or `Warn` records a violation and lets it through;
* the identity match is a `matchCondition`, so the API server filters before
  calling Kyverno -- without it, `failurePolicy: Fail` takes every ConfigMap
  write in the cluster down with Kyverno;
* the over-grant fixture is never applied by `setup-kind.sh` -- Northstar does
  not ship a standing over-grant, the beat makes the mistake on stage.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = REPO_ROOT / "deploy" / "platform" / "kyverno" / "keystone-policy.yaml"
OVERGRANT = REPO_ROOT / "deploy" / "demo" / "keystone-overgrant.yaml"
SETUP_KIND = REPO_ROOT / "scripts" / "setup-kind.sh"
START_DEMO = REPO_ROOT / "scripts" / "start-demo.sh"


def _policy() -> dict:
    assert POLICY.exists(), f"{POLICY} does not exist"
    docs = [d for d in yaml.safe_load_all(POLICY.read_text()) if d]
    assert len(docs) == 1, "the keystone is exactly one ValidatingPolicy"
    return docs[0]


def _rule_pairs(policy: dict) -> set[tuple[str, str]]:
    return {
        (group, resource)
        for rule in policy["spec"]["matchConstraints"]["resourceRules"]
        for group in rule["apiGroups"]
        for resource in rule["resources"]
    }


def test_it_is_the_current_validating_policy_type_not_the_deprecated_cluster_policy():
    policy = _policy()
    assert policy["apiVersion"] == "policies.kyverno.io/v1"
    assert policy["kind"] == "ValidatingPolicy"
    assert policy["metadata"]["name"] == "agents-cannot-edit-their-own-authority"


def test_it_denies_and_fails_closed():
    spec = _policy()["spec"]
    assert spec["validationActions"] == ["Deny"], (
        "Audit or Warn would record the violation and let it through"
    )
    assert spec["failurePolicy"] == "Fail", (
        "failurePolicy Ignore would turn a Kyverno outage into agents being "
        "allowed to edit their own authority; the gateway is failClosed and "
        "so is this"
    )
    assert spec["evaluation"]["admission"]["enabled"] is True


def test_it_covers_create_update_and_delete_on_every_rule():
    for rule in _policy()["spec"]["matchConstraints"]["resourceRules"]:
        assert set(rule["operations"]) == {"CREATE", "UPDATE", "DELETE"}, rule


def test_it_covers_the_five_object_groups():
    pairs = _rule_pairs(_policy())
    expected = {
        # 1. the autonomy contract
        ("core.openfeature.dev", "featureflags"),
        ("core.openfeature.dev", "featureflagsources"),
        # 2. the gateway's rules
        ("", "configmaps"),
        # 3. the Reach ring
        ("cilium.io", "ciliumnetworkpolicies"),
        # 4. RBAC
        ("rbac.authorization.k8s.io", "roles"),
        ("rbac.authorization.k8s.io", "rolebindings"),
        ("rbac.authorization.k8s.io", "clusterroles"),
        ("rbac.authorization.k8s.io", "clusterrolebindings"),
        # 5. Kyverno's own constraint, and the bypass
        ("policies.kyverno.io", "validatingpolicies"),
        ("policies.kyverno.io", "policyexceptions"),
    }
    assert expected <= pairs, f"the policy no longer covers {sorted(expected - pairs)}"


def test_the_agent_identity_is_a_prefix_match_on_both_agent_namespaces():
    spec = _policy()["spec"]
    conditions = spec["matchConditions"]
    assert len(conditions) == 1
    expression = conditions[0]["expression"]
    for namespace in ("northstar-agents", "northstar-tools"):
        assert (
            f"startsWith('system:serviceaccount:{namespace}:')" in expression
        ), f"{namespace} is not matched by prefix"
    # A list of exact names is the defect this guards: a new agent would need
    # a policy edit.
    assert "settlement-agent" not in expression
    assert "claims-agent" not in expression
    # The humans and the one deliberate writer must not be matched.
    assert "northstar-platform" not in expression
    assert "backstage" not in expression


def test_the_message_names_the_identity_the_object_and_the_remedy():
    spec = _policy()["spec"]
    expression = spec["validations"][0]["messageExpression"]
    assert "DENIED: agent identity" in expression
    assert "request.userInfo.username" in expression
    assert "variables.kind" in expression and "variables.name" in expression
    assert "reviewed commit, never a runtime decision by the agent" in expression


def test_the_validation_can_actually_deny():
    """A validation that is constant `true` would pass every test above."""
    spec = _policy()["spec"]
    assert spec["validations"][0]["expression"] == "!variables.isProtected"
    names = [v["name"] for v in spec["variables"]]
    assert {"target", "kind", "name", "isProtected"} <= set(names)


def test_configmaps_are_narrowed_to_the_gateways_own():
    """The resource rule matches every ConfigMap; the variable must narrow it,
    or an agent could not write any ConfigMap in its own namespace."""
    spec = _policy()["spec"]
    is_protected = next(
        v["expression"] for v in spec["variables"] if v["name"] == "isProtected"
    )
    assert "agentgateway-config" in is_protected
    assert "northstar-platform" in is_protected


def test_the_overgrant_is_the_one_mistake_the_beat_models():
    docs = [d for d in yaml.safe_load_all(OVERGRANT.read_text()) if d]
    kinds = {d["kind"] for d in docs}
    assert kinds == {"Role", "RoleBinding"}
    role = next(d for d in docs if d["kind"] == "Role")
    binding = next(d for d in docs if d["kind"] == "RoleBinding")
    assert role["metadata"]["namespace"] == "northstar-platform"
    rule = role["rules"][0]
    assert rule["resources"] == ["featureflags"]
    assert rule["resourceNames"] == ["northstar-autonomy"]
    assert "patch" in rule["verbs"]
    assert "delete" not in rule["verbs"] and "*" not in rule["verbs"]
    subject = binding["subjects"][0]
    assert (subject["namespace"], subject["name"]) == (
        "northstar-agents",
        "settlement-agent",
    )


def test_the_overgrant_says_it_is_a_demo_fixture_modelling_a_mistake():
    header = OVERGRANT.read_text().split("apiVersion:")[0]
    assert "DEMO FIXTURE" in header and "MISTAKE" in header


def _effective(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def test_setup_kind_never_applies_the_overgrant():
    """Comment-stripped: the script's own prose names the fixture on purpose."""
    effective = _effective(SETUP_KIND)
    assert "keystone-overgrant" not in effective
    assert "deploy/demo" not in effective


def test_setup_kind_applies_the_policy_and_waits_for_it_to_be_ready():
    effective = _effective(SETUP_KIND)
    assert re.search(
        r'apply -f "\$ROOT/deploy/platform/kyverno/keystone-policy\.yaml"', effective
    )
    assert re.search(
        r"wait validatingpolicy/agents-cannot-edit-their-own-authority", effective
    )
    assert "conditionStatus.ready" in effective


VALUES = REPO_ROOT / "deploy" / "platform" / "kyverno" / "values.yaml"


def test_setup_kind_installs_kyverno_with_the_values_file():
    effective = _effective(SETUP_KIND)
    assert '-f "$ROOT/deploy/platform/kyverno/values.yaml"' in effective


def test_kyverno_tracing_points_the_admission_controller_at_the_collectors_grpc_port():
    tracing = yaml.safe_load(VALUES.read_text())["admissionController"]["tracing"]
    assert tracing["enabled"] is True
    assert tracing["address"] == "otel-collector.observability.svc.cluster.local"
    # Kyverno exports OTLP/gRPC; 4318 (HTTP) would silently drop every span.
    # A string, because the chart's values schema types it that way.
    assert tracing["port"] == "4317"


def test_kyverno_can_read_every_kind_the_policy_matches_or_it_never_becomes_ready():
    """Measured: without read access to the non-core kinds the policy is
    stored, its webhook is configured, and `status.conditionStatus.ready`
    stays false -- so setup-kind.sh's wait times out. Read-only, always."""
    values = yaml.safe_load(VALUES.read_text())
    policy_groups = {
        (group, resource)
        for rule in _policy()["spec"]["matchConstraints"]["resourceRules"]
        for group in rule["apiGroups"]
        for resource in rule["resources"]
        if group not in ("", "policies.kyverno.io")  # core and Kyverno's own are default
    }
    for controller in ("admissionController", "reportsController"):
        granted = set()
        for rule in values[controller]["rbac"]["clusterRole"]["extraResources"]:
            assert set(rule["verbs"]) == {"get", "list", "watch"}, (
                f"{controller} must gain no write verb: {rule}"
            )
            granted |= {(g, r) for g in rule["apiGroups"] for r in rule["resources"]}
        assert policy_groups <= granted, (
            f"{controller} cannot read {sorted(policy_groups - granted)}"
        )


def test_start_demos_preflight_checks_the_overgrant_is_absent():
    effective = _effective(START_DEMO)
    assert "keystone-demo-overgrant" in effective
