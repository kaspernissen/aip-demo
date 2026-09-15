"""M6 Task 3: the write grant, pinned the same way Task 1's read-only grant
is (tests/unit/test_stage6_backstage_config.py). Design (docs/superpowers/
specs/2026-09-15-northstar-m6-golden-path-design.md §4) makes every clause
below a named, testable requirement, not an incidental property of
`deploy/k8s/backstage.yaml`:

- namespaced `Role`, verbs `get`/`list`/`patch` only -- no `delete`, no
  `create`, no wildcard verb;
- `resourceNames: [northstar-autonomy]` -- not `*`;
- a SEPARATE ServiceAccount from `backstage` (Task 1's read-only identity),
  bound by its own RoleBinding;
- no access to Secrets anywhere in this Role (M6-R5: the read-only
  ClusterRole must also never grow one, so both are checked);
- the RoleBinding actually binds the write Role to the write ServiceAccount,
  not to `backstage`.

Parsed with `yaml.safe_load_all`, never grepped -- same discipline
test_stage6_backstage_config.py states and for the same reason: a manifest is
data, and "no delete verb anywhere" is a claim about the parsed RBAC object,
not about which strings happen to appear in the file.

This module cannot see what the live API server actually enforces --
`resourceNames` scoping RBAC's own `list`/`watch` semantics, in particular,
is a live-cluster question. That half is
`tests/e2e/test_autonomy_writer_live_rbac.py`, which runs
`kubectl auth can-i --as=` against the real object.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKSTAGE_MANIFEST = REPO_ROOT / "deploy" / "k8s" / "backstage.yaml"

WRITE_ROLE_NAME = "backstage-autonomy-writer"
WRITE_SA_NAME = "backstage-autonomy-writer"
READ_ONLY_SA_NAME = "backstage"

ALLOWED_VERBS = {"get", "list", "patch"}
ALLOWED_API_GROUP = "core.openfeature.dev"
ALLOWED_RESOURCE = "featureflags"
ALLOWED_RESOURCE_NAME = "northstar-autonomy"


def _load_docs() -> list[dict]:
    return [
        doc for doc in yaml.safe_load_all(BACKSTAGE_MANIFEST.read_text()) if doc
    ]


def _docs_of_kind(kind: str, name: str) -> list[dict]:
    return [
        doc
        for doc in _load_docs()
        if doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name
    ]


def _the_write_role() -> dict:
    roles = _docs_of_kind("Role", WRITE_ROLE_NAME)
    assert len(roles) == 1, (
        f"expected exactly one namespaced Role named {WRITE_ROLE_NAME!r} in "
        f"{BACKSTAGE_MANIFEST}; found {len(roles)}"
    )
    return roles[0]


def test_the_write_role_is_namespaced_not_a_clusterrole():
    """Design §4: this grant is scoped to one namespace, unlike the read-only
    grant's ClusterRole (which needs cluster scope for a different reason --
    the catalog spans four namespaces). A ClusterRole with the same
    resourceNames would still be narrower than `*`, but it would also make
    this Role bindable in every namespace at once via a ClusterRoleBinding,
    which nothing here needs."""
    assert _docs_of_kind("ClusterRole", WRITE_ROLE_NAME) == [], (
        f"{WRITE_ROLE_NAME!r} must be a namespaced Role, not a ClusterRole -- "
        "found one anyway"
    )
    role = _the_write_role()
    assert role.get("metadata", {}).get("namespace") == "northstar-platform", (
        f"the write Role is not namespaced to northstar-platform: {role}"
    )


def test_the_write_role_grants_only_get_list_patch():
    role = _the_write_role()
    offending = [
        (rule.get("resources"), rule.get("verbs"))
        for rule in role.get("rules", [])
        if not set(rule.get("verbs", [])) <= ALLOWED_VERBS
    ]
    assert not offending, (
        f"{WRITE_ROLE_NAME} grants a verb outside {sorted(ALLOWED_VERBS)}: "
        f"{offending}. Design §4: 'get/list/patch ... No delete.'"
    )


def test_the_write_role_never_grants_delete_or_create_specifically():
    """Belt and suspenders on the verb the design calls out by name twice."""
    role = _the_write_role()
    all_verbs = {v for rule in role.get("rules", []) for v in rule.get("verbs", [])}
    assert "delete" not in all_verbs, f"delete is granted: {role['rules']}"
    assert "create" not in all_verbs, f"create is granted: {role['rules']}"
    assert "update" not in all_verbs, (
        "update is granted -- design §4 says patch only, and update would "
        f"let a caller replace the whole object: {role['rules']}"
    )


def test_the_write_role_uses_no_wildcard_anywhere():
    role = _the_write_role()
    offending = [
        rule
        for rule in role.get("rules", [])
        if "*" in rule.get("verbs", [])
        or "*" in rule.get("resources", [])
        or "*" in rule.get("apiGroups", [])
        or "*" in rule.get("resourceNames", [])
    ]
    assert not offending, (
        f"{WRITE_ROLE_NAME} uses a wildcard somewhere: {offending}. Design "
        "§4, verbatim: 'not `*`, not cluster-wide, not other verbs.'"
    )


def test_the_write_role_is_scoped_to_exactly_the_one_named_flag():
    role = _the_write_role()
    rules = role.get("rules", [])
    assert rules, f"{WRITE_ROLE_NAME} grants no rules at all"
    for rule in rules:
        assert rule.get("apiGroups") == [ALLOWED_API_GROUP], (
            f"a rule targets apiGroups {rule.get('apiGroups')!r}, not only "
            f"{[ALLOWED_API_GROUP]!r}: {rule}"
        )
        assert rule.get("resources") == [ALLOWED_RESOURCE], (
            f"a rule targets resources {rule.get('resources')!r}, not only "
            f"{[ALLOWED_RESOURCE]!r}: {rule}"
        )
        assert rule.get("resourceNames") == [ALLOWED_RESOURCE_NAME], (
            f"a rule's resourceNames is {rule.get('resourceNames')!r}, not "
            f"exactly {[ALLOWED_RESOURCE_NAME]!r} -- design §4: "
            "'resourceNames: [northstar-autonomy] -- not `*`'"
        )


def test_neither_backstage_role_grants_any_access_to_secrets():
    """M6-R5's own warning: the temptation an implementer might have is to
    widen the READ-ONLY ClusterRole to give the card its `get`. This checks
    both Roles in this file, not only the new one, so that mistake reddens
    here regardless of which object it lands in."""
    offending = []
    for kind, name in (("ClusterRole", "backstage-read-only"), ("Role", WRITE_ROLE_NAME)):
        for doc in _docs_of_kind(kind, name):
            for rule in doc.get("rules", []):
                if "secrets" in rule.get("resources", []):
                    offending.append((kind, name, rule))
    assert not offending, f"a Backstage RBAC object grants Secrets access: {offending}"


def test_the_write_serviceaccount_is_separate_from_the_read_only_one():
    write_sas = _docs_of_kind("ServiceAccount", WRITE_SA_NAME)
    assert len(write_sas) == 1, (
        f"expected exactly one ServiceAccount named {WRITE_SA_NAME!r}; found "
        f"{len(write_sas)}"
    )
    assert WRITE_SA_NAME != READ_ONLY_SA_NAME, (
        "the write ServiceAccount's name must differ from the read-only "
        "one -- design §4's whole point is that these are two identities"
    )


def test_the_rolebinding_binds_the_write_role_to_the_write_serviceaccount_only():
    bindings = _docs_of_kind("RoleBinding", WRITE_ROLE_NAME)
    assert len(bindings) == 1, (
        f"expected exactly one RoleBinding named {WRITE_ROLE_NAME!r}; found "
        f"{len(bindings)}"
    )
    binding = bindings[0]
    assert binding.get("roleRef", {}).get("kind") == "Role", (
        f"roleRef.kind is {binding.get('roleRef', {}).get('kind')!r}, not "
        "'Role' -- a ClusterRole roleRef here would silently widen scope"
    )
    assert binding.get("roleRef", {}).get("name") == WRITE_ROLE_NAME, binding
    subjects = binding.get("subjects", [])
    assert subjects == [
        {
            "kind": "ServiceAccount",
            "name": WRITE_SA_NAME,
            "namespace": "northstar-platform",
        }
    ], (
        f"{WRITE_ROLE_NAME}'s RoleBinding subjects are not exactly the write "
        f"ServiceAccount: {subjects}"
    )


def test_the_deployment_mounts_the_writer_token_read_only():
    """The mechanism, not just the RBAC: the writer credential has to reach
    the pod somehow, and it must not do so in a way that grants the pod
    itself broader Secret access than the Roles above already forbid."""
    docs = _load_docs()
    deployment = next(
        d
        for d in docs
        if d.get("kind") == "Deployment"
        and d.get("metadata", {}).get("name") == "backstage"
    )
    pod_spec = deployment["spec"]["template"]["spec"]
    volumes = {v["name"]: v for v in pod_spec.get("volumes", [])}
    assert "autonomy-writer-token" in volumes, (
        "no volume named autonomy-writer-token on the backstage Deployment "
        f"-- the card's backend has no way to reach the write credential. "
        f"volumes present: {sorted(volumes)}"
    )
    secret_vol = volumes["autonomy-writer-token"].get("secret", {})
    assert secret_vol.get("secretName") == "backstage-autonomy-writer-token", (
        f"autonomy-writer-token volume does not source the writer's own "
        f"token Secret: {secret_vol}"
    )

    containers = pod_spec["containers"]
    assert len(containers) == 1
    mounts = {m["name"]: m for m in containers[0].get("volumeMounts", [])}
    assert "autonomy-writer-token" in mounts, (
        f"the backstage container does not mount autonomy-writer-token: "
        f"{sorted(mounts)}"
    )
    assert mounts["autonomy-writer-token"].get("readOnly") is True, (
        f"the writer token volume is not mounted read-only: "
        f"{mounts['autonomy-writer-token']}"
    )

    # The pod's OWN identity is still the read-only ServiceAccount -- Task 1's
    # invariant, unweakened by this task's write grant.
    assert pod_spec.get("serviceAccountName") == READ_ONLY_SA_NAME, (
        "the backstage Deployment's own serviceAccountName changed away "
        f"from {READ_ONLY_SA_NAME!r}: {pod_spec.get('serviceAccountName')!r}. "
        "The write identity must reach this pod via the mounted token "
        "Secret, not by becoming the pod's own ServiceAccount."
    )


def test_the_writer_token_secret_is_bound_to_the_write_serviceaccount_by_annotation():
    secrets = _docs_of_kind("Secret", "backstage-autonomy-writer-token")
    assert len(secrets) == 1, (
        f"expected exactly one Secret named backstage-autonomy-writer-token; "
        f"found {len(secrets)}"
    )
    secret = secrets[0]
    assert secret.get("type") == "kubernetes.io/service-account-token", (
        f"the writer token Secret's type is {secret.get('type')!r}, not "
        "'kubernetes.io/service-account-token' -- without this the API "
        "server's own controller never populates its token/ca.crt/namespace"
    )
    annotations = secret.get("metadata", {}).get("annotations", {})
    assert annotations.get("kubernetes.io/service-account.name") == WRITE_SA_NAME, (
        f"the writer token Secret's kubernetes.io/service-account.name "
        f"annotation is {annotations.get('kubernetes.io/service-account.name')!r}, "
        f"not {WRITE_SA_NAME!r} -- it would mint a token for the wrong "
        "identity, or none at all"
    )
