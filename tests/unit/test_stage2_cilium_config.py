"""Guards the stage-2 configuration: the create-time CNI flag, the Cilium
values, and the ordering `setup-kind.sh --stage 2` must preserve.

Every assertion here exists because getting it wrong costs a cluster recreate
to find out.

Guards over raw script text are easy to make inert: a guard that greps for
`"stage2.yaml" in text`, `"kindnet" in text`, or `"northstar-platform" in
text` can pass on a nearby comment alone even after the real assignment,
check, or `kubectl apply` behind it is gone. The same risk holds for
`str.find` ordering checks -- an explanatory comment naming a step can
satisfy the ordering before the real step actually runs -- and for
version-pin checks that only ask whether a version string appears
*somewhere* in a file that mentions it more than once, so a half-done
version bump can leave one mention unedited and still pass.

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

**Why the values assertions are phrased as they are.** `deploy/platform/
cilium-values.yaml` is checked against `helm show values cilium/cilium
--version 1.20.1`:

1. `dnsPolicy` is a **string** in this chart (`dnsPolicy: ""`, line 291 of the
   rendered default values, documented as "DNS policy for Cilium agent pods"
   with a ref to the Kubernetes pod-DNS-policy docs) -- the Kubernetes pod DNS
   policy for the cilium-agent pods, nothing to do with Cilium's DNS proxy. A
   map there is a type error.
2. The DNS proxy toFQDNs depends on is gated by `l7Proxy`, which is already
   `true` by default (line 2449). The chart's `dnsProxy` block (line 4315) has
   **no `enabled` key** at all -- only tuning knobs (`socketLingerTimeout`,
   `dnsRejectResponseCode`, `enableDnsCompression`, `endpointMaxIpPerHostname`,
   `minTtl`, `preAllocateIdentities`, ...). So
   `test_values_do_not_carry_the_briefs_dnspolicy_type_error` pins the absence
   of both, rather than letting a later reader "restore" either block.

The values-file guards all parse the YAML with `yaml.safe_load`, which
discards comments before any assertion runs, so they carry none of the risk
described above.
"""
import ast
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
STAGE3 = REPO_ROOT / "deploy" / "kind" / "stage3.yaml"
CILIUM_VALUES = REPO_ROOT / "deploy" / "platform" / "cilium-values.yaml"
SETUP_KIND = REPO_ROOT / "scripts" / "setup-kind.sh"

# Resolved from `helm search repo cilium/cilium --versions`: newest stable 1.x,
# appVersion 1.20.1 (previous minor was 1.19.7).
PINNED_CILIUM_CHART_VERSION = "1.20.1"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _effective_script() -> str:
    """`setup-kind.sh` with the shebang and every whole-line comment removed.

    This is what makes the text assertions below non-inert: a comment cannot
    satisfy any of them, since it is stripped out before the regex runs.
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
    the flag.

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
    """Cilium's kube-proxy replacement is not required here — DNS-aware egress
    denial is ordinary CiliumNetworkPolicy and works with kube-proxy present —
    and enabling it on kind invites cgroup-v2 and kernel-version problems."""
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
        "tuning knobs only — so there is nothing to enable here"
    )


def test_hubble_is_off_and_the_operator_is_single_replica():
    values = _yaml(CILIUM_VALUES)
    assert values["hubble"]["enabled"] is False
    assert values["operator"]["replicas"] == 1


def test_the_pinned_chart_version_is_recorded_in_the_values_header():
    """Nothing floats: the version the script installs must be stated in the
    values file too, so the two cannot drift apart silently.

    The header mentions the version twice ("pinned to 1.20.1" and "appVersion
    1.20.1"), so this requires the pin sentence specifically, and requires
    *every* version-shaped string in the file to agree with it, so a
    half-done bump fails.
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
    """The single most load-bearing behaviour here: stage 2 must create the
    cluster from the config that carries `disableDefaultCNI`.

    Asserts on the assignment itself in comment-stripped code, with no
    disjunct a stray comment could satisfy, and
    `test_stage_two_creates_the_cluster_from_stage2_yaml_when_run` below
    proves the same thing behaviourally.
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
    refusal message does not claim stage 2 is unimplemented. The *behaviour*
    — that stage 2 dispatches and stages above it do not — is proven by
    `test_stages_above_the_implemented_one_still_refuse_to_run` and
    `test_an_unimplemented_stage_actually_exits_nonzero` below, not here."""
    text = SETUP_KIND.read_text()
    assert "implement stage 1 only" not in text, (
        "setup-kind.sh still claims only stage 1 is implemented"
    )
    assert "Cilium arrives at stage 2 via a cluster recreate" not in text, (
        "setup-kind.sh still points forward to a recreate that has happened"
    )


def test_stages_above_the_implemented_one_still_refuse_to_run():
    """Unimplemented stages must exit rather than silently building the last
    implemented cluster shape. Comment-stripped, so the threshold cannot be
    supplied by prose.

    The threshold here must track the stage gate in `setup-kind.sh` exactly:
    raising the gate without raising this test's expected value reddens this
    test, which is the intent -- the gate's threshold is a claim about what
    exists, and it should not be possible to change one without the other.
    """
    text = _effective_script()
    assert re.search(r'"\$\{STAGE\}" -gt 7', text), (
        "the stage gate must refuse stages above 7; expected an effective "
        '`"${STAGE}" -gt 7` test'
    )
    assert not re.search(r'"\$\{?STAGE\}?" -gt 6', text), (
        "the stage gate must not refuse stage 7, which is implemented"
    )


def test_cilium_is_ready_before_any_workload_is_deployed():
    """A pod scheduled before cilium-agent is ready comes up with no
    networking. That failure presents as a mysteriously broken demo, not as a
    failed install, so the ordering is asserted rather than assumed.

    Computed over comment-stripped code, so an explanatory comment naming
    `rollout status daemonset/cilium` cannot satisfy the ordering while the
    real waits sit after the deploy.
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
    `northstar-platform`, so stage 2 creates it here instead.

    Requires the manifest's own `name:` line in comment-stripped code, and
    that it be applied — a comment explaining why the namespace is created
    here must not be able to satisfy this on its own.
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

    Asserts on the `kubectl ... get daemonset kindnet` probe and on the
    `exit 1` that must follow it, in comment-stripped code so neither a
    comment nor the refusal message alone can satisfy it, and
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
    # stage3.yaml too, for the stage-6 cold-path test below. Copied
    # unconditionally rather than only when a stage-6 test runs -- it is a
    # static committed file, and copying it for stages that never read it
    # costs nothing.
    shutil.copy2(STAGE3, tree / "deploy" / "kind" / "stage3.yaml")
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
def test_stage_six_creates_the_cluster_from_stage3_yaml_when_cold(tmp_path):
    """Cold-path proof for the one part of `--stage 6` that a warm,
    already-running cluster cannot exercise: the create-time KIND_CONFIG
    selection. A warm cluster takes the "cluster already exists, skipping
    creation" branch, which never re-evaluates KIND_CONFIG at all (the same
    caveat stage 5's own header in `scripts/setup-kind.sh` states about
    itself), and with no git remote a cold rebuild is this repo's only
    recovery path if the real cluster is ever lost.

    Recreating the real cluster to exercise this branch live needs explicit
    operator permission each time, so this hermetic harness is the
    substitute: no real cluster, registry, or network is touched, and the
    assertion is on the literal `kind create cluster --config ...` invocation
    a genuinely cold `--stage 6` would issue.
    """
    _, out, calls = _run_setup_kind("6", False, False, tmp_path)
    creates = [c for c in calls if c.startswith("kind create cluster")]
    assert creates, f"`kind create cluster` was never invoked; output:\n{out}"
    assert len(creates) == 1, f"expected one create, got {creates}"
    config = creates[0].split("--config", 1)[1].strip()
    assert Path(config).name == "stage3.yaml", (
        f"`--stage 6` created the cluster from {Path(config).name!r}, not "
        "stage3.yaml. There is deliberately no stage6.yaml (Backstage needs "
        "no new host port, same reasoning stages 4 and 5 already established) "
        "-- if this reads anything else, a cold "
        "`--stage 6` either builds the wrong cluster shape or dies at the "
        "`[[ -f \"$KIND_CONFIG\" ]]` check on a file that does not exist."
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


def test_no_operator_facing_instruction_still_points_at_an_older_stage():
    """The current stage is the current cluster, so nothing a reader is told
    to run may name an older one. `scripts/smoke-test.sh` telling an operator
    whose cluster is unreachable to run an older `--stage N` would rebuild on
    a stale cluster shape -- reverting the CNI at worst, or omitting
    workloads a later check assumes are present -- so this asserts over a
    *set* of files rather than any one line.

    `CURRENT_STAGE` below is the single number each new stage has to raise.
    It is also the reason this test is parameter-free rather than
    parameterised: adding a stage must not change how many tests this module
    collects, since `docs/telemetry-contract.md` publishes that count.

    Two deliberate exclusions:

    * `docs/superpowers/` holds historical plan and spec documents, and the
      older `--stage N` in them is the correct, historical instruction for
      the milestone each one describes. Rewriting them would falsify a
      record rather than fix a pointer.
    * `docs/telemetry-contract.md` is checked for `--stage 1` but NOT for the
      later stages. That file's job is to state the command each published
      number was actually measured with, so naming the stage actually run is
      *required* there; "fixing" it to a later stage would attach provenance
      to numbers that do not have it.

    `deploy/kind/*.yaml` and `scripts/*.sh` are globs rather than hand-written
    lists, so a new kind config or script is covered the day it is written
    rather than depending on someone remembering to add it here.
    """
    CURRENT_STAGE = 7
    scripts_and_readme = [
        # A glob, not a hand-written list: every script this repo ships is
        # covered the day it is written, rather than depending on someone
        # remembering to add it here.
        *sorted((REPO_ROOT / "scripts").glob("*.sh")),
        REPO_ROOT / "README.md",
        *sorted((REPO_ROOT / "deploy" / "kind").glob("*.yaml")),
    ]
    assert any(
        path.parent.name == "kind" for path in scripts_and_readme
    ), (
        "deploy/kind/ matched no *.yaml at all, so this guard silently covers "
        "less than its docstring claims -- has the directory moved?"
    )
    # An empty or shrunken glob is how a guard silently stops guarding, so name
    # the scripts that must be in it. A rename is fine, but it has to be a
    # deliberate edit here rather than a silent drop.
    script_names = {path.name for path in scripts_and_readme if path.suffix == ".sh"}
    assert {
        "smoke-test.sh",
        "rebuild-and-deploy.sh",
        "setup-kind.sh",
        "start-demo.sh",
    } <= script_names, (
        "scripts/*.sh does not match every script this guard is meant to "
        f"cover; it matched {sorted(script_names)}. A renamed or moved script "
        "drops out of this guard silently, so a stale operator instruction in "
        "it could go unnoticed."
    )
    checked: list[tuple[Path, int]] = [
        (path, stage)
        for path in scripts_and_readme
        for stage in range(1, CURRENT_STAGE)
    ]
    checked.append((REPO_ROOT / "docs" / "telemetry-contract.md", 1))

    # `tests/` is included too: a skip or failure message is read by an
    # operator at the exact moment something is wrong, and a stale `--stage N`
    # in one is as dangerous as one in a script -- following a remedy printed
    # by a test that names an older stage can silently disable telemetry a
    # later stage depends on.
    #
    # The needle is narrower here than for scripts and README. Those files
    # are read top to bottom, so any `--stage N` in them is an instruction. A
    # test module is mostly prose *about* the stages -- "the ordering
    # `setup-kind.sh --stage 2` must preserve" is a correct sentence that must
    # stay correct forever -- and a guard that reddened on it would be a
    # guard people satisfy by rewriting true sentences. So only a fixed set
    # of instruction lead-ins is checked, immediately before the command --
    # what an operator is actually told to type or read as "the fix".
    #
    # Each file is parsed with `ast` rather than read as physical lines, and
    # searched over `ast.Constant` string values. CPython's tokenizer
    # concatenates adjacent string literals -- plain-to-plain, and
    # plain-to-f-string via `ast.JoinedStr`'s own `Constant` children --
    # before the parser ever produces a node, so `node.value` already reads
    # as one sentence regardless of how many source lines or literal
    # fragments an instruction was written across (e.g. "Re-run " /
    # "./scripts/setup-kind.sh --stage 4 to ..." as two adjacent literals),
    # and a `#` comment can never appear in it at all, since comments are not
    # AST nodes -- a correctness property no per-line text scan has.
    _INSTRUCTION_LEADINS = r"(?:(?:[Rr]e-?)?run|[Bb]ring it up with|[Tt]he fix is)"
    _STAGE_INSTRUCTION_RE = re.compile(
        _INSTRUCTION_LEADINS
        + r"\s+`?\.?/?scripts/setup-kind\.sh`?\s*--stage\s+(\d+)"
    )
    test_files = sorted((REPO_ROOT / "tests").glob("**/*.py"))
    assert len(test_files) >= 20, (
        "tests/**/*.py matched only "
        f"{len(test_files)} files, so this half of the guard is covering "
        "almost nothing -- has the suite moved?"
    )

    offenders = []
    for path in test_files:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            for match in _STAGE_INSTRUCTION_RE.finditer(node.value):
                stage = int(match.group(1))
                if stage >= CURRENT_STAGE:
                    continue
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} (stage "
                    f"{stage}): {match.group(0)!r}"
                )

    for path, stage in checked:
        if not path.exists():
            continue
        needle = f"setup-kind.sh --stage {stage}"
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if needle not in line:
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
            offenders.append(
                f"{path.relative_to(REPO_ROOT)}:{i + 1} (stage {stage}): "
                f"{line.strip()}"
            )
    assert not offenders, (
        f"these lines still instruct a reader to build a cluster older than "
        f"stage {CURRENT_STAGE}, which is the current one. `--stage 1` "
        "silently reverts the CNI; `--stage 2` omits workloads a later "
        "stage's prerequisite check then requires:\n" + "\n".join(offenders)
    )


@pytestmark_bash
def test_an_unimplemented_stage_actually_exits_nonzero():
    """Behavioural, and needs no fakes at all: the stage gate runs before the
    script loads `.env` or touches anything, so the real script can be invoked
    directly with no side effects whatsoever."""
    proc = subprocess.run(
        ["bash", str(SETUP_KIND), "--stage", "8"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=60,
    )
    assert proc.returncode != 0, "stage 8 must not be accepted"
    assert "not implemented yet" in proc.stdout + proc.stderr
