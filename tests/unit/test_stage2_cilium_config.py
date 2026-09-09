"""Guards the M2.1 Task 1 stage-2 configuration: the create-time CNI flag, the
Cilium values, and the ordering `setup-kind.sh --stage 2` must preserve.

Every assertion here exists because getting it wrong costs a cluster recreate
to find out, and a recreate is the one operation this milestone budgets exactly
once.

**Why the values assertions are phrased as they are.** The plan's own sketch of
`deploy/platform/cilium-values.yaml` was checked against `helm show values
cilium/cilium --version 1.20.1` before the cluster was touched, and was wrong in
two ways that a `helm install` would only have surfaced *after* the old cluster
was already gone:

1. It set `dnsPolicy: {enabled: true}`. In this chart `dnsPolicy` is a **string**
   (`dnsPolicy: ""`, line 291 of the rendered default values, documented as "DNS
   policy for Cilium agent pods" with a ref to the Kubernetes pod-DNS-policy
   docs) — the Kubernetes pod DNS policy for the cilium-agent pods, nothing to
   do with Cilium's DNS proxy. A map there is a type error.
2. It justified that block as "required for toFQDNs egress policy in M2.2". The
   DNS proxy toFQDNs depends on is gated by `l7Proxy`, which is already `true`
   by default (line 2449). The chart's `dnsProxy` block (line 4315) has **no
   `enabled` key** at all — only tuning knobs (`socketLingerTimeout`,
   `dnsRejectResponseCode`, `enableDnsCompression`, `endpointMaxIpPerHostname`,
   `minTtl`, `preAllocateIdentities`, ...). So M2.2 needs no values change here,
   and `test_values_do_not_carry_the_briefs_dnspolicy_type_error` pins the
   absence rather than letting a later reader "restore" the block from the plan.

**Why the ordering assertion exists.** Pods scheduled before Cilium's agent is
ready come up without networking, which presents as a broken demo rather than a
failed install. The script must wait on both the `cilium` DaemonSet and the
`cilium-operator` Deployment before it applies any workload, and
`test_cilium_is_ready_before_any_workload_is_deployed` checks the actual line
order in the script, not merely that the waits are present somewhere.
"""
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE1 = REPO_ROOT / "deploy" / "kind" / "stage1.yaml"
STAGE2 = REPO_ROOT / "deploy" / "kind" / "stage2.yaml"
CILIUM_VALUES = REPO_ROOT / "deploy" / "platform" / "cilium-values.yaml"
SETUP_KIND = REPO_ROOT / "scripts" / "setup-kind.sh"

# Resolved from `helm search repo cilium/cilium --versions`: newest stable 1.x,
# appVersion 1.20.1 (previous minor was 1.19.7).
PINNED_CILIUM_CHART_VERSION = "1.20.1"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


# --- The create-time CNI flag ----------------------------------------------


def test_stage2_disables_the_default_cni():
    """`networking.disableDefaultCNI` is create-time: this flag is the entire
    reason stage 2 needs its own kind config and a cluster recreate."""
    cfg = _yaml(STAGE2)
    assert cfg.get("networking", {}).get("disableDefaultCNI") is True, (
        "stage2.yaml must set networking.disableDefaultCNI: true — without it "
        "kind installs kindnet and Cilium ends up layered on a cluster that "
        "already has a CNI"
    )


def test_stage1_keeps_its_default_cni():
    """Stage 1 must stay bring-up-able without Cilium, so it must not acquire
    the flag. This is the direction the plan's own wording invited getting
    wrong: it described adding the flag "under `networking`" in a file that has
    no `networking` block at all."""
    cfg = _yaml(STAGE1)
    assert "disableDefaultCNI" not in yaml.dump(cfg), (
        "stage1.yaml must not disable the default CNI — stage 1 has no Cilium "
        "install, so a stage-1 cluster with no CNI would never become Ready"
    )


def test_stage2_is_stage1_plus_exactly_the_networking_block():
    """The registry wiring and all three host port mappings must survive the
    recreate byte-for-byte: the shared `kind-registry` container is wired in by
    `containerdConfigPatches`, and 8080/16686/4318 are what the host-side tests
    and the demo itself talk to."""
    s1, s2 = _yaml(STAGE1), _yaml(STAGE2)
    networking = s2.pop("networking", None)
    assert networking == {"disableDefaultCNI": True}, (
        "stage 2 should differ from stage 1 by the disableDefaultCNI flag and "
        f"nothing else in `networking`; found {networking!r}. In particular no "
        "podSubnet (kind's default 10.244.0.0/16 is what makes ipam.mode: "
        "kubernetes coherent) and no kubeProxyMode (kube-proxy stays in place)."
    )
    assert s2 == s1, (
        "stage2.yaml must otherwise be stage1.yaml exactly — the "
        "containerdConfigPatches registry wiring and every extraPortMapping "
        "included"
    )


# --- The Cilium values -----------------------------------------------------


def test_kube_proxy_replacement_stays_off():
    """The spec is explicit that Cilium's kube-proxy replacement is not
    required — M2.2's DNS-aware egress denial is ordinary CiliumNetworkPolicy
    and works with kube-proxy present — and enabling it on kind invites
    cgroup-v2 and kernel-version problems."""
    values = _yaml(CILIUM_VALUES)
    assert values["kubeProxyReplacement"] in (False, "false"), (
        "kubeProxyReplacement must stay off; got "
        f"{values.get('kubeProxyReplacement')!r}"
    )


def test_ipam_mode_is_kubernetes():
    """Deliberate deviation from the chart default `cluster-pool`: in
    `kubernetes` mode Cilium allocates out of the node podCIDRs kind carves
    from its default 10.244.0.0/16, so pod addressing matches what kind
    reports."""
    assert _yaml(CILIUM_VALUES)["ipam"]["mode"] == "kubernetes"


def test_values_do_not_carry_the_briefs_dnspolicy_type_error():
    """See the module docstring: `dnsPolicy` is a string in this chart, and the
    toFQDNs support it was supposed to provide is gated by `l7Proxy` (default
    true) instead. Nothing belongs here."""
    values = _yaml(CILIUM_VALUES)
    assert "dnsPolicy" not in values, (
        "`dnsPolicy` in cilium/cilium is a *string* (the Kubernetes pod DNS "
        "policy for cilium-agent pods, default \"\"), not a {enabled: bool} "
        "map. It is also not what toFQDNs needs — that is `l7Proxy`, already "
        "true by default. Setting it here is a schema error with no upside."
    )
    assert "dnsProxy" not in values, (
        "the chart's `dnsProxy` block has no `enabled` key — its children are "
        "tuning knobs only — so there is nothing to enable here for M2.2"
    )


def test_hubble_is_off_and_the_operator_is_single_replica():
    values = _yaml(CILIUM_VALUES)
    assert values["hubble"]["enabled"] is False
    assert values["operator"]["replicas"] == 1


def test_the_pinned_chart_version_is_recorded_in_the_values_header():
    """Nothing floats: the version the script installs must be stated in the
    values file too, so the two cannot drift apart silently."""
    assert PINNED_CILIUM_CHART_VERSION in CILIUM_VALUES.read_text(), (
        f"cilium-values.yaml must record the pinned chart version "
        f"{PINNED_CILIUM_CHART_VERSION} in its header comment"
    )


# --- The stage-2 dispatch in setup-kind.sh ---------------------------------


def _setup_kind_text() -> str:
    return SETUP_KIND.read_text()


def test_setup_kind_pins_the_cilium_chart_version_exactly():
    text = _setup_kind_text()
    match = re.search(r'CILIUM_CHART_VERSION="([^"]+)"', text)
    assert match, "setup-kind.sh must pin CILIUM_CHART_VERSION"
    assert match.group(1) == PINNED_CILIUM_CHART_VERSION, (
        f"setup-kind.sh pins Cilium {match.group(1)}, this guard and "
        f"cilium-values.yaml say {PINNED_CILIUM_CHART_VERSION}"
    )
    assert "--version \"${CILIUM_CHART_VERSION}\"" in text, (
        "the helm install must pass the pinned version explicitly; an "
        "unpinned `helm install` floats to whatever the repo cache holds"
    )


def test_stage2_uses_the_stage2_kind_config():
    text = _setup_kind_text()
    assert "deploy/kind/stage${STAGE}.yaml" in text or "stage2.yaml" in text, (
        "the stage-2 path must create the cluster from stage2.yaml, not "
        "stage1.yaml"
    )


def test_stage2_is_no_longer_the_not_implemented_stub():
    """The M1.x message this replaces read: "M1.1 and M1.2 implement stage 1
    only; Cilium arrives at stage 2 via a cluster recreate." That recreate has
    now happened, so that sentence must not survive to tell a reader stage 2 is
    unimplemented."""
    text = _setup_kind_text()
    assert "implement stage 1 only" not in text, (
        "setup-kind.sh still claims only stage 1 is implemented"
    )
    assert "Cilium arrives at stage 2 via a cluster recreate" not in text, (
        "setup-kind.sh still points forward to a recreate that has happened"
    )


def test_stages_above_two_still_refuse_to_run():
    assert re.search(r'\$\{?STAGE\}?" -gt 2', _setup_kind_text()), (
        "stages 3-6 are not implemented and must still exit with a clear "
        "message rather than silently building a stage-2 cluster"
    )


def test_cilium_is_ready_before_any_workload_is_deployed():
    """A pod scheduled before cilium-agent is ready comes up with no
    networking. That failure presents as a mysteriously broken demo, not as a
    failed install, so the ordering is asserted rather than assumed."""
    text = _setup_kind_text()
    ds_wait = text.find("rollout status daemonset/cilium")
    op_wait = text.find("rollout status deployment/cilium-operator")
    deploy = text.find("rebuild-and-deploy.sh")
    jaeger = text.find("jaeger.yaml")
    assert ds_wait != -1, "must wait for the cilium DaemonSet rollout"
    assert op_wait != -1, "must wait for the cilium-operator Deployment rollout"
    assert deploy != -1 and jaeger != -1
    assert ds_wait < jaeger and op_wait < jaeger, (
        "both Cilium readiness waits must come before the observability "
        "manifests are applied"
    )
    assert ds_wait < deploy and op_wait < deploy, (
        "both Cilium readiness waits must come before stage 1's workloads are "
        "built and deployed"
    )


def test_stage2_creates_the_northstar_platform_namespace():
    """`deploy/k8s/namespaces.yaml` creates four namespaces (observability,
    northstar-agents, northstar-tools, northstar-systems) and no
    `northstar-platform`; M2.1 Tasks 2 and 4 both need it, so stage 2 creates
    it. Asserted here so it cannot quietly disappear."""
    assert "northstar-platform" in _setup_kind_text(), (
        "stage 2 must create the northstar-platform namespace — "
        "deploy/k8s/namespaces.yaml does not"
    )


def test_the_model_key_set_empty_reporting_survives():
    """The one signal that tells an operator the model credential is missing.
    README.md depends on it, and it must never start printing the value."""
    text = _setup_kind_text()
    assert "ANTHROPIC_API_KEY: SET" in text
    assert "ANTHROPIC_API_KEY: EMPTY" in text


def test_an_existing_cluster_is_not_silently_reused_for_stage_2():
    """`disableDefaultCNI` is create-time, so re-running `--stage 2` against a
    cluster that already exists cannot apply it. Without a guard the script
    would happily install Cilium on top of kindnet and report success — the
    single worst outcome available here, because it looks like it worked."""
    text = _setup_kind_text()
    assert "kindnet" in text, (
        "the stage-2 path must detect an existing cluster still running the "
        "default CNI (kindnet) and refuse, rather than layering Cilium on it"
    )
