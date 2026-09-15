"""M6 Task 1: Backstage's ServiceAccount is READ-ONLY. Design (docs/superpowers/
specs/2026-09-15-northstar-m6-golden-path-design.md §4) makes this a named
requirement, not an incidental property -- the write grant that lets a later
task PATCH `featureflags.core.openfeature.dev` is a SEPARATE ServiceAccount,
deliberately, so the two grants stay independently reviewable. This module
pins the boundary on the ServiceAccount Task 1 actually ships:
`deploy/k8s/backstage.yaml`'s `backstage-read-only` ClusterRole must never
carry a write verb, a wildcard, or any access to Secrets -- in this task or
any later edit to this file.

Parsed with `yaml.safe_load_all`, never by grepping the raw text: a manifest
is data, and asserting on parsed rules is what makes "no write verb anywhere"
a real claim about the RBAC object rather than a claim about which strings
happen to appear in the file.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKSTAGE_MANIFEST = REPO_ROOT / "deploy" / "k8s" / "backstage.yaml"

# Design §4: "get`/`list` on featureflags ... Not `*`, not cluster-wide, not
# other verbs" is Task 3's write grant. This task's read-only grant gets the
# weaker, symmetrical version of the same rule: only these three verbs, ever.
ALLOWED_VERBS = {"get", "list", "watch"}


def _load_docs() -> list[dict]:
    return [
        doc
        for doc in yaml.safe_load_all(BACKSTAGE_MANIFEST.read_text())
        if doc
    ]


def _backstage_cluster_roles() -> list[dict]:
    return [
        doc
        for doc in _load_docs()
        if doc.get("kind") == "ClusterRole"
        and doc.get("metadata", {}).get("name") == "backstage-read-only"
    ]


def test_the_manifest_defines_exactly_one_read_only_cluster_role():
    roles = _backstage_cluster_roles()
    assert len(roles) == 1, (
        "expected exactly one ClusterRole named 'backstage-read-only' in "
        f"{BACKSTAGE_MANIFEST}; found {len(roles)}. If Task 3's write grant "
        "has been folded into this same object, split it back out -- design "
        "§4 requires two separate ServiceAccounts precisely so the two "
        "grants can be reviewed and revoked independently."
    )


def test_no_rule_grants_a_verb_outside_get_list_watch():
    role = _backstage_cluster_roles()[0]
    rules = role.get("rules", [])
    # Whole-branch review M5: this iterated `role.get("rules", [])` with no
    # non-emptiness assertion, so a blanked ClusterRole (`rules: []`) passed
    # vacuously -- and a rule with no `verbs` key at all passed too, since
    # `set() <= ALLOWED_VERBS` is True. The sibling file already has this
    # line (`test_autonomy_writer_rbac.py`'s
    # `test_the_write_role_is_scoped_to_exactly_the_one_named_flag`); it was
    # never copied over here, so the read-only Role had a negative assertion
    # with no positive control at all.
    assert rules, "deploy/k8s/backstage.yaml's backstage-read-only ClusterRole grants no rules at all"
    offending = [
        (rule.get("resources"), rule.get("verbs"))
        for rule in rules
        if not set(rule.get("verbs", [])) <= ALLOWED_VERBS
    ]
    assert not offending, (
        "deploy/k8s/backstage.yaml's backstage-read-only ClusterRole grants "
        f"a verb outside {sorted(ALLOWED_VERBS)}: {offending}. This "
        "ServiceAccount is Task 1's read-only grant -- no create/update/"
        "patch/delete belongs here at all; that is the second, separate "
        "ServiceAccount design §4 assigns to the FeatureFlag-write task."
    )


def test_no_rule_uses_a_wildcard_verb_resource_or_api_group():
    role = _backstage_cluster_roles()[0]
    rules = role.get("rules", [])
    assert rules, "deploy/k8s/backstage.yaml's backstage-read-only ClusterRole grants no rules at all"
    offending = [
        rule
        for rule in rules
        if "*" in rule.get("verbs", [])
        or "*" in rule.get("resources", [])
        or "*" in rule.get("apiGroups", [])
    ]
    assert not offending, (
        "deploy/k8s/backstage.yaml's backstage-read-only ClusterRole uses a "
        f"wildcard somewhere: {offending}. Design §4's own wording for the "
        "sibling write grant -- 'not `*`, not cluster-wide[-in-scope], not "
        "other verbs' -- applies here with equal force: every verb, "
        "resource and apiGroup must be named explicitly."
    )


def test_no_rule_grants_any_access_to_secrets():
    role = _backstage_cluster_roles()[0]
    rules = role.get("rules", [])
    assert rules, "deploy/k8s/backstage.yaml's backstage-read-only ClusterRole grants no rules at all"
    offending = [
        rule
        for rule in rules
        if "secrets" in rule.get("resources", [])
    ]
    assert not offending, (
        "deploy/k8s/backstage.yaml's backstage-read-only ClusterRole grants "
        f"access to Secrets: {offending}. Design §4: 'no access to Secrets "
        "... beyond what the read-only Kubernetes plugin needs' -- the "
        "plugin needs none, so this resource must never appear here."
    )


def test_the_backstage_deployment_uses_the_read_only_service_account():
    docs = _load_docs()
    deployment = next(
        d for d in docs if d.get("kind") == "Deployment"
        and d.get("metadata", {}).get("name") == "backstage"
    )
    sa_name = deployment["spec"]["template"]["spec"]["serviceAccountName"]
    assert sa_name == "backstage", (
        "deploy/k8s/backstage.yaml's Deployment must run as the 'backstage' "
        f"ServiceAccount (found {sa_name!r}) -- the one this module's other "
        "tests bind the read-only ClusterRole to, via the "
        "ClusterRoleBinding's subjects list."
    )
    binding = next(
        d for d in docs if d.get("kind") == "ClusterRoleBinding"
        and d.get("metadata", {}).get("name") == "backstage-read-only"
    )
    subjects = binding.get("subjects", [])
    assert any(
        s.get("kind") == "ServiceAccount"
        and s.get("name") == "backstage"
        and s.get("namespace") == "northstar-platform"
        for s in subjects
    ), (
        "the backstage-read-only ClusterRoleBinding's subjects do not name "
        f"the backstage ServiceAccount in northstar-platform: {subjects}"
    )
