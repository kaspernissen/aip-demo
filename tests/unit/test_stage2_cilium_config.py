"""Guards the M2.1 Task 1 stage-2 configuration: the create-time CNI flag, the
Cilium values, and the ordering `setup-kind.sh --stage 2` must preserve.

Every assertion here exists because getting it wrong costs a cluster recreate
to find out, and a recreate is the one operation this milestone budgets exactly
once.

**Fix round 1 -- the inert-guard class.** The first version of this module
grepped `scripts/setup-kind.sh`'s raw text. Mutation testing showed that three
of those guards stayed green while the behaviour they name was removed, because
the string each searched for also appears in a *comment* or in an error message:

- `KIND_CONFIG` reverted to `stage1.yaml` -- the guard passed on `"stage2.yaml"
  in text`, satisfied by the prose comment above the assignment.
- the kindnet reuse check replaced with `if false; then` -- the guard passed on
  `"kindnet" in text`, satisfied by the comment *and* the refusal message.
- the `northstar-platform` namespace apply deleted -- the guard passed on
  `"northstar-platform" in text`, satisfied by the comment that explains why it
  is created here. (This third one the review's own mutation set missed; it was
  found by auditing the whole class rather than only the two reported
  instances.)

A fourth was latent rather than live: the readiness-ordering guard used
`str.find` on raw text, so an ordinary explanatory comment naming
`rollout status daemonset/cilium` early in the file would satisfy the ordering
while the real waits sat after the deploy. A fifth was weak rather than inert:
the version-pin guard asked only whether `1.20.1` appeared *somewhere* in the
values file, and the header mentions it twice, so editing one mention and
forgetting the other -- precisely what a half-done version bump does -- left it
green.

Two defences, applied together:

1. **`_effective_script()`** strips the shebang and every whole-line comment,
   so no assertion below can be satisfied by prose. Trailing (end-of-line)
   comments are deliberately *not* stripped -- doing that safely needs a shell
   parser, since `#` is an ordinary character inside strings -- so the
   assertions are additionally written specifically enough that a trailing
   comment cannot supply them.
2. **Behavioural tests** for the load-bearing behaviours, which is stronger
   than any text assertion: `_run_setup_kind` copies the real script into a
   throwaway tree with fake `kind`/`kubectl`/`docker`/`helm` on `PATH`, runs
   it, and asserts on *what it actually invoked*. Those tests cannot be
   satisfied by any text anywhere, comment or not.

**Why the values assertions are phrased as they are.** The plan's own sketch of
`deploy/platform/cilium-values.yaml` was checked against `helm show values
cilium/cilium --version 1.20.1` before the cluster was touched, and was wrong in
two ways that a `helm install` would only have surfaced *after* the old cluster
was already gone:

1. It set `dnsPolicy: {enabled: true}`. In this chart `dnsPolicy` is a **string**
   (`dnsPolicy: ""`, line 291 of the rendered default values, documented as "DNS
   policy for Cilium agent pods" with a ref to the Kubernetes pod-DNS-policy
   docs) -- the Kubernetes pod DNS policy for the cilium-agent pods, nothing to
   do with Cilium's DNS proxy. A map there is a type error.
2. It justified that block as "required for toFQDNs egress policy in M2.2". The
   DNS proxy toFQDNs depends on is gated by `l7Proxy`, which is already `true`
   by default (line 2449). The chart's `dnsProxy` block (line 4315) has **no
   `enabled` key** at all -- only tuning knobs (`socketLingerTimeout`,
   `dnsRejectResponseCode`, `enableDnsCompression`, `endpointMaxIpPerHostname`,
   `minTtl`, `preAllocateIdentities`, ...). So M2.2 needs no values change here,
   and `test_values_do_not_carry_the_briefs_dnspolicy_type_error` pins the
   absence rather than letting a later reader "restore" the block from the plan.

The values-file guards were never part of the inert class: they all parse the
YAML with `yaml.safe_load`, which discards comments before any assertion runs.
"""
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
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


def _effective_script() -> str:
    """`setup-kind.sh` with the shebang and every whole-line comment removed.

    This is what makes the text assertions below non-inert: a comment can no
    longer satisfy any of them. See the module docstring for the three guards
    that were green against broken input before this existed.
    """
    return "\n".join(
        line
        for line in SETUP_KIND.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


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
    no `networking` block at all.

    Asserts against `yaml.dump` of the *parsed* document, so a comment
    mentioning the flag cannot trip it either way.
    """
    cfg = _yaml(STAGE1)
    assert "disableDefaultCNI" not in yaml.dump(cfg), (
        "stage1.yaml must not disable the default CNI — stage 1 has no Cilium "
        "install, so a stage-1 cluster with no CNI would never become Ready"
    )


def test_stage2_is_stage1_plus_exactly_the_networking_block():
    """The registry wiring and all three host port mappings must survive the
    recreate byte-for-byte: the shared `kind-registry` container is wired in by
    `containerdConfigPatches`, and 8080/16686/4318 are what the host-side tests
    and the demo itself talk to.

    Structural, not textual — it compares parsed documents, and mutation
    testing turns it RED on all four independent mutations thrown at it
    (missing flag, added `podSubnet`, changed `hostPort`, dropped registry
    wiring).
    """
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
    values file too, so the two cannot drift apart silently.

    Fix round 1: this used to ask only whether `1.20.1` appeared *somewhere* in
    the file. The header mentions the version twice ("pinned to 1.20.1" and
    "appVersion 1.20.1"), so editing one and forgetting the other left the
    guard green. It now requires the pin sentence specifically, and requires
    *every* version-shaped string in the file to agree with it, so a half-done
    bump fails.
    """
    text = CILIUM_VALUES.read_text()
    assert re.search(rf"pinned to {re.escape(PINNED_CILIUM_CHART_VERSION)}\b", text), (
        f"cilium-values.yaml's header must state 'pinned to "
        f"{PINNED_CILIUM_CHART_VERSION}'"
    )
    # `(?<![\d.])...(?![\d.])` keeps dotted quads out: the header's
    # `10.244.0.0/16` (kind's default podSubnet) is not a chart version.
    found = set(re.findall(r"(?<![\d.])\d+\.\d+\.\d+(?![\d.])", text))
    assert found == {PINNED_CILIUM_CHART_VERSION}, (
        f"every version-shaped string in cilium-values.yaml must be "
        f"{PINNED_CILIUM_CHART_VERSION}; found {sorted(found)} — a partial "
        f"version bump is the failure mode this catches"
    )


# --- The stage-2 dispatch in setup-kind.sh (text, comment-stripped) --------


def test_setup_kind_pins_the_cilium_chart_version_exactly():
    """Asserted against comment-stripped effective code, and requires exactly
    one assignment, so neither a comment nor a second stale assignment can
    satisfy it."""
    text = _effective_script()
    assignments = re.findall(r'CILIUM_CHART_VERSION="([^"]+)"', text)
    assert len(assignments) == 1, (
        f"expected exactly one effective CILIUM_CHART_VERSION assignment, "
        f"found {len(assignments)}: {assignments}"
    )
    assert assignments[0] == PINNED_CILIUM_CHART_VERSION, (
        f"setup-kind.sh pins Cilium {assignments[0]}, this guard and "
        f"cilium-values.yaml say {PINNED_CILIUM_CHART_VERSION}"
    )
    assert '--version "${CILIUM_CHART_VERSION}"' in text, (
        "the helm install must pass the pinned version explicitly; an "
        "unpinned `helm install` floats to whatever the repo cache holds"
    )


def test_stage2_uses_the_stage2_kind_config():
    """The task's single most load-bearing behaviour: stage 2 must create the
    cluster from the config that carries `disableDefaultCNI`.

    Fix round 1 (review Finding 1, HIGH): this guard used to read
    `"deploy/kind/stage${STAGE}.yaml" in text or "stage2.yaml" in text`, and
    the `or` disjunct was satisfied by a prose comment — so reverting
    `KIND_CONFIG` to `stage1.yaml` left it green. It now asserts on the
    assignment itself in comment-stripped code, with no disjunct, and
    `test_stage_two_creates_the_cluster_from_stage2_yaml_when_run` below proves
    the same thing behaviourally.
    """
    text = _effective_script()
    assert re.search(
        r'KIND_CONFIG="\$ROOT/deploy/kind/stage\$\{STAGE\}\.yaml"', text
    ), (
        "the stage-2 path must select its kind config by stage — expected an "
        "effective assignment of KIND_CONFIG to "
        '"$ROOT/deploy/kind/stage${STAGE}.yaml"; a hardcoded stage1.yaml here '
        "would build a kindnet cluster and then install Cilium on top of it"
    )
    assert not re.search(r'KIND_CONFIG="[^"]*stage1\.yaml"', text), (
        "KIND_CONFIG must not be hardcoded to stage1.yaml"
    )
    assert 'kind create cluster --config "$KIND_CONFIG"' in text, (
        "the cluster must actually be created from KIND_CONFIG"
    )


def test_stage2_is_no_longer_the_not_implemented_stub():
    """A prose-staleness guard, and deliberately nothing more: it pins that the
    M1.x refusal message no longer claims stage 2 is unimplemented. The
    *behaviour* — that stage 2 dispatches and stages above 2 do not — is proven
    by `test_stages_above_two_still_refuse_to_run` and
    `test_an_unimplemented_stage_actually_exits_nonzero` below, not here."""
    text = SETUP_KIND.read_text()
    assert "implement stage 1 only" not in text, (
        "setup-kind.sh still claims only stage 1 is implemented"
    )
    assert "Cilium arrives at stage 2 via a cluster recreate" not in text, (
        "setup-kind.sh still points forward to a recreate that has happened"
    )


def test_stages_above_two_still_refuse_to_run():
    """Stages 3-6 are not implemented and must exit rather than silently
    building a stage-2 cluster. Comment-stripped, so the threshold cannot be
    supplied by prose."""
    text = _effective_script()
    assert re.search(r'"\$\{STAGE\}" -gt 2', text), (
        "the stage gate must refuse stages above 2; expected an effective "
        '`"${STAGE}" -gt 2` test'
    )
    assert not re.search(r'"\$\{?STAGE\}?" -gt 1', text), (
        "the stage gate must no longer refuse stage 2"
    )


def test_cilium_is_ready_before_any_workload_is_deployed():
    """A pod scheduled before cilium-agent is ready comes up with no
    networking. That failure presents as a mysteriously broken demo, not as a
    failed install, so the ordering is asserted rather than assumed.

    Fix round 1: the ordering used to be computed over raw text, which made it
    latently inert — an ordinary explanatory comment naming
    `rollout status daemonset/cilium` early in the file would satisfy the
    ordering while the real waits sat after the deploy. Demonstrated with a
    mutation; now computed over comment-stripped code.
    """
    text = _effective_script()
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
    it.

    Fix round 1: this used to be `"northstar-platform" in _setup_kind_text()`,
    which the comment explaining *why* the namespace is created here satisfied
    all by itself — so deleting the `kubectl apply` left the guard green. This
    instance was not in the review's mutation set; it was found by auditing the
    whole class. It now requires the manifest's own `name:` line in
    comment-stripped code, and that it be applied.
    """
    text = _effective_script()
    assert re.search(r"^\s*name: northstar-platform\s*$", text, re.MULTILINE), (
        "stage 2 must create the northstar-platform namespace — "
        "deploy/k8s/namespaces.yaml does not — and the manifest's own "
        "`name: northstar-platform` line must be present in effective code, "
        "not only in a comment explaining the intent"
    )
    assert "kind: Namespace" in text, (
        "the northstar-platform namespace must be applied as a Namespace "
        "manifest"
    )
    assert "kubectl apply -f - <<'EOF'" in text, (
        "the namespace manifest must actually be piped to `kubectl apply`"
    )


def test_the_model_key_set_empty_reporting_survives():
    """The one signal that tells an operator the model credential is missing.
    README.md depends on it, and it must never start printing the value.
    Comment-stripped, so the strings must come from real `echo` statements."""
    text = _effective_script()
    assert "ANTHROPIC_API_KEY: SET" in text
    assert "ANTHROPIC_API_KEY: EMPTY" in text
    # The only place the value itself may be interpolated is the --from-literal
    # handed to kubectl. Anywhere else means it reaches output.
    remainder = text.replace('ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}"', "")
    assert "${ANTHROPIC_API_KEY}" not in remainder, (
        "the key's value must never be interpolated anywhere but the "
        "--from-literal handed to kubectl — anywhere else risks printing it"
    )


def test_an_existing_cluster_is_not_silently_reused_for_stage_2():
    """`disableDefaultCNI` is create-time, so re-running `--stage 2` against a
    cluster that already exists cannot apply it. Without a guard the script
    would happily install Cilium on top of kindnet and report success — the
    single worst outcome available here, because it looks like it worked.

    Fix round 1 (review Finding 2, MEDIUM): this used to be
    `assert "kindnet" in _setup_kind_text()`, which stayed green when the
    check's condition was replaced with `if false; then` — the word survives in
    both the comment and the refusal message, and the refusal message is
    effective code, so comment-stripping alone would not have fixed it. It now
    asserts on the `kubectl ... get daemonset kindnet` probe and on the `exit 1`
    that must follow it, and
    `test_stage_two_refuses_an_existing_kindnet_cluster_when_run` below proves
    the behaviour directly.
    """
    text = _effective_script()
    probe = re.search(r"kubectl[^\n]*-n kube-system get daemonset kindnet", text)
    assert probe, (
        "the stage-2 path must probe for the default CNI's DaemonSet with "
        "`kubectl -n kube-system get daemonset kindnet` in effective code — "
        "the word 'kindnet' appearing in a comment or an error message proves "
        "nothing about whether the check runs"
    )
    tail = text[probe.end():]
    assert "exit 1" in tail.split("fi")[0], (
        "the kindnet probe must lead to `exit 1` — detecting the wrong CNI and "
        "continuing is the silent-success outcome this guard exists to prevent"
    )


# --- Behavioural tests: run the real script with fakes on PATH ------------
#
# Stronger than any text assertion, because nothing written in the file --
# comment, string literal or error message -- can satisfy them. Each builds a
# throwaway tree containing the real `setup-kind.sh`, runs it with fake
# `kind`/`kubectl`/`docker`/`helm` ahead of it on PATH, and asserts on what it
# actually invoked.
#
# The fake `kubectl` fails on `cluster-info`, which is the first thing the
# script does after the cluster-creation step, so every run stops a few lines
# past the behaviour under test. Nothing here touches a real cluster, a real
# registry, Docker, the network, or the repo's own `.env`.

FAKES = {
    # `docker inspect` succeeds and reports the registry as already running, so
    # the script takes its "already running" branch and never runs a container.
    "docker": r"""#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  inspect) echo "true"; exit 0 ;;
esac
exit 0
""",
    "kind": r"""#!/usr/bin/env bash
echo "kind $*" >> "$FAKE_LOG"
if [[ "$1 $2" == "get clusters" ]]; then
  [[ -n "${FAKE_CLUSTER_EXISTS:-}" ]] && echo northstar
  exit 0
fi
if [[ "$1 $2" == "get nodes" ]]; then echo northstar-control-plane; exit 0; fi
exit 0
""",
    "kubectl": r"""#!/usr/bin/env bash
echo "kubectl $*" >> "$FAKE_LOG"
for arg in "$@"; do
  if [[ "$arg" == "cluster-info" ]]; then exit 1; fi
done
if [[ "$*" == *"get daemonset kindnet"* ]]; then
  [[ -n "${FAKE_KINDNET_PRESENT:-}" ]] && exit 0
  exit 1
fi
exit 0
""",
    "helm": r"""#!/usr/bin/env bash
echo "helm $*" >> "$FAKE_LOG"
exit 0
""",
}


def _run_setup_kind(stage, cluster_exists, kindnet_present, tmp_path):
    """Copy the real script into a throwaway tree and run it against fakes.

    Returns (returncode, combined_output, invocations).
    """
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    (tree / "deploy" / "kind").mkdir(parents=True)
    (tree / "deploy" / "platform").mkdir(parents=True)
    shutil.copy2(SETUP_KIND, tree / "scripts" / "setup-kind.sh")
    shutil.copy2(STAGE1, tree / "deploy" / "kind" / "stage1.yaml")
    shutil.copy2(STAGE2, tree / "deploy" / "kind" / "stage2.yaml")
    shutil.copy2(CILIUM_VALUES, tree / "deploy" / "platform" / "cilium-values.yaml")
    # A fake .env with only the one variable the script requires. The repo's
    # real .env is never read, and no secret is involved.
    (tree / ".env").write_text("OPENTELEMETRY_COLLECTOR_CONTAINER_IMAGE=fake/collector:0\n")

    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    for name, body in FAKES.items():
        p = bindir / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    log = tmp_path / "invocations.log"
    log.write_text("")
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["FAKE_LOG"] = str(log)
    if cluster_exists:
        env["FAKE_CLUSTER_EXISTS"] = "1"
    if kindnet_present:
        env["FAKE_KINDNET_PRESENT"] = "1"
    # Never let a real credential reach the subprocess.
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("DASH0_AUTH_TOKEN", None)

    proc = subprocess.run(
        ["bash", str(tree / "scripts" / "setup-kind.sh"), "--stage", stage],
        capture_output=True, text=True, env=env, timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr, log.read_text().splitlines()


pytestmark_bash = pytest.mark.skipif(sys.platform == "win32", reason="bash-only harness")


@pytestmark_bash
def test_stage_two_creates_the_cluster_from_stage2_yaml_when_run(tmp_path):
    """The behavioural counterpart to `test_stage2_uses_the_stage2_kind_config`,
    and the one no comment can satisfy: run `--stage 2` with no existing
    cluster and assert the config `kind create cluster` was actually handed is
    stage2.yaml."""
    _, out, calls = _run_setup_kind("2", False, False, tmp_path)
    creates = [c for c in calls if c.startswith("kind create cluster")]
    assert creates, f"`kind create cluster` was never invoked; output:\n{out}"
    assert len(creates) == 1, f"expected one create, got {creates}"
    config = creates[0].split("--config", 1)[1].strip()
    assert Path(config).name == "stage2.yaml", (
        f"`--stage 2` created the cluster from {Path(config).name!r}, not "
        "stage2.yaml — only stage2.yaml carries disableDefaultCNI, so this "
        "would have built a kindnet cluster and then installed Cilium on top"
    )


@pytestmark_bash
def test_stage_one_creates_the_cluster_from_stage1_yaml_when_run(tmp_path):
    """The same selection, the other way round: stage 1 must still get
    stage1.yaml, so the by-stage config lookup is genuinely parameterised
    rather than hardcoded to whichever file this task cared about."""
    _, out, calls = _run_setup_kind("1", False, False, tmp_path)
    creates = [c for c in calls if c.startswith("kind create cluster")]
    assert creates, f"`kind create cluster` was never invoked; output:\n{out}"
    config = creates[0].split("--config", 1)[1].strip()
    assert Path(config).name == "stage1.yaml", (
        f"`--stage 1` created the cluster from {Path(config).name!r}; stage 1 "
        "must keep its default CNI"
    )


@pytestmark_bash
def test_stage_two_refuses_an_existing_kindnet_cluster_when_run(tmp_path):
    """The behavioural counterpart to
    `test_an_existing_cluster_is_not_silently_reused_for_stage_2`, and the only
    assertion here that could not have been satisfied by the refusal message
    alone: with an existing cluster whose kindnet DaemonSet is present,
    `--stage 2` must exit non-zero and must not proceed."""
    rc, out, calls = _run_setup_kind("2", True, True, tmp_path)
    assert rc != 0, (
        "`--stage 2` over an existing kindnet cluster exited 0 — the "
        "create-time disableDefaultCNI flag cannot be applied to an existing "
        f"cluster, so this is the silent-success failure mode. Output:\n{out}"
    )
    assert "still running the default CNI" in out, (
        f"expected the refusal to name the reason; got:\n{out}"
    )
    assert not [c for c in calls if c.startswith("kind create cluster")], (
        "the script must not create a cluster on this path"
    )
    assert not [c for c in calls if c.startswith("helm upgrade")], (
        "the script must refuse BEFORE installing Cilium — installing it on "
        "top of a live kindnet is exactly what this guard prevents"
    )


@pytestmark_bash
def test_stage_two_proceeds_when_the_existing_cluster_has_no_kindnet(tmp_path):
    """The guard must refuse a *kindnet* cluster specifically, not every
    existing cluster — otherwise re-running `--stage 2` against the Cilium
    cluster it just built would fail, and the guard would be a nuisance rather
    than a safety net. Same inputs as the test above except the kindnet probe
    fails."""
    _, out, _ = _run_setup_kind("2", True, False, tmp_path)
    assert "still running the default CNI" not in out, (
        f"the guard fired against a cluster with no kindnet DaemonSet:\n{out}"
    )
    assert "Cluster already exists, skipping creation." in out, (
        f"expected the script to reuse the existing Cilium cluster; got:\n{out}"
    )


def test_no_operator_facing_instruction_still_points_at_stage_1():
    """Fix round 1 (review Findings 3, 4 and 7, treated as one class): stage 2
    is the current cluster, so nothing a reader is told to run may say
    `--stage 1`.

    This had teeth. `scripts/smoke-test.sh` told an operator whose cluster was
    unreachable to run `--stage 1`; following that after a cluster loss rebuilds
    on kindnet, silently reverting the CNI this milestone's whole trace
    verification is about — and the new kindnet guard would then refuse, but
    only after a second delete. Four sites carried the same instruction
    (`smoke-test.sh` twice, `rebuild-and-deploy.sh`, `README.md`), which is why
    this asserts over a *set* of files rather than the one line the review
    cited.

    `docs/superpowers/` is deliberately excluded: those are the M1.1/M1.2 plan
    and spec documents, and `--stage 1` is the correct, historical instruction
    for the milestone each of them describes. Rewriting them would falsify a
    record rather than fix a pointer.
    """
    checked = [
        REPO_ROOT / "scripts" / "smoke-test.sh",
        REPO_ROOT / "scripts" / "rebuild-and-deploy.sh",
        REPO_ROOT / "scripts" / "setup-kind.sh",
        REPO_ROOT / "README.md",
        REPO_ROOT / "docs" / "telemetry-contract.md",
    ]
    offenders = []
    for path in checked:
        if not path.exists():
            continue
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if "setup-kind.sh --stage 1" not in line:
                continue
            # A passage that documents the correction is allowed to quote the
            # old instruction; a line that *issues* it is not. Whole-line
            # comments are prose, and so is a quotation introduced by
            # "previously read" — which, since the quoted command wraps, may
            # sit a line or two above the match rather than on it.
            if line.lstrip().startswith("#"):
                continue
            window = "\n".join(lines[max(0, i - 2): i + 1])
            if "previously read" in window:
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{i + 1}: {line.strip()}")
    assert not offenders, (
        "these lines still instruct a reader to build a stage-1 (kindnet) "
        "cluster; stage 2 is the current cluster and `--stage 1` silently "
        "reverts the CNI:\n" + "\n".join(offenders)
    )


@pytestmark_bash
def test_an_unimplemented_stage_actually_exits_nonzero():
    """Behavioural, and needs no fakes at all: the stage gate runs before the
    script loads `.env` or touches anything, so the real script can be invoked
    directly with no side effects whatsoever."""
    proc = subprocess.run(
        ["bash", str(SETUP_KIND), "--stage", "7"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=60,
    )
    assert proc.returncode != 0, "stage 7 must not be accepted"
    assert "not implemented yet" in proc.stdout + proc.stderr
