"""Guards the stage-2 configuration: kind's default CNI stays, Linkerd is
pinned and installed before any workload, the mesh annotations are where the
design says, and proxy tracing is on and points at the collector.

Every assertion here exists because getting it wrong costs a cluster recreate
to find out.

Guards over raw script text are easy to make inert: a guard that greps for
`"linkerd" in text` can pass on a nearby comment alone even after the real
install behind it is gone. The same risk holds for `str.find` ordering checks
-- an explanatory comment naming a step can satisfy the ordering before the
real step actually runs -- and for version-pin checks that only ask whether a
version string appears *somewhere* in a file that mentions it more than once.

Two defences, applied together:

1. **`effective_script()`** (tests/unit/_setup_script.py) strips the shebang
   and every whole-line comment, so no assertion below can be satisfied by
   prose.
2. **Behavioural tests**: `_run_setup_kind` copies the real script into a
   throwaway tree with fake `kind`/`kubectl`/`docker`/`helm` on `PATH`, runs
   it, and asserts on *what it actually invoked*.

The YAML guards parse with `yaml.safe_load`, which discards comments before any
assertion runs.
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

from tests.unit._setup_script import effective_script as _effective_script

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE1 = REPO_ROOT / "deploy" / "kind" / "stage1.yaml"
STAGE2 = REPO_ROOT / "deploy" / "kind" / "stage2.yaml"
STAGE3 = REPO_ROOT / "deploy" / "kind" / "stage3.yaml"
LINKERD_VALUES = REPO_ROOT / "deploy" / "platform" / "linkerd" / "values.yaml"
LINKERD_CERTS_SCRIPT = REPO_ROOT / "scripts" / "linkerd-certs.sh"
SETUP_KIND = REPO_ROOT / "scripts" / "setup-kind.sh"

# Read off `helm search repo linkerd-edge --versions` and
# https://helm.linkerd.io/edge/index.yaml on 2026-10-21: the chart version is
# NOT the app version, and linkerd-crds / linkerd-control-plane share it.
PINNED_LINKERD_CHART_VERSION = "2026.10.1"
PINNED_LINKERD_APP_VERSION = "edge-26.10.1"
PINNED_GATEWAY_API_VERSION = "v1.5.1"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _docs(path: Path) -> list:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


# --- The kind configs: the default CNI stays -------------------------------


def test_no_kind_config_disables_the_default_cni():
    """kindnet is what enforces the egress NetworkPolicies (kind >= 0.24).
    Asserts against `yaml.dump` of the parsed documents, so a comment
    mentioning the flag cannot trip it either way."""
    for path in (STAGE1, STAGE2, STAGE3):
        assert "disableDefaultCNI" not in yaml.dump(_yaml(path)), (
            f"{path.name} must not disable the default CNI: Cilium is gone, "
            "and without kindnet nothing enforces NetworkPolicy"
        )


def test_stage2_is_structurally_stage1():
    """The registry wiring and all three host port mappings must survive
    byte-for-byte: 8080/16686/4318 are what the host-side tests and the demo
    talk to. Structural, not textual: it compares parsed documents."""
    assert _yaml(STAGE2) == _yaml(STAGE1)


def test_stage3_is_stage2_plus_exactly_the_reviewer_ui_port():
    s2, s3 = _yaml(STAGE2), _yaml(STAGE1)
    s3 = _yaml(STAGE3)
    ports2 = s2["nodes"][0]["extraPortMappings"]
    ports3 = s3["nodes"][0]["extraPortMappings"]
    assert ports3[: len(ports2)] == ports2
    assert [p["hostPort"] for p in ports3[len(ports2):]] == [8081]
    s3["nodes"][0]["extraPortMappings"] = ports2
    assert s3 == s2


def test_cilium_values_are_gone():
    assert not (REPO_ROOT / "deploy" / "platform" / "cilium-values.yaml").exists()


# --- Pins -------------------------------------------------------------------


def test_setup_kind_pins_the_linkerd_chart_version_exactly():
    """Comment-stripped, exactly one assignment each, so neither a comment nor
    a second stale assignment can satisfy it."""
    text = _effective_script()
    for name, want in (
        ("LINKERD_CHART_VERSION", PINNED_LINKERD_CHART_VERSION),
        ("LINKERD_APP_VERSION", PINNED_LINKERD_APP_VERSION),
        ("GATEWAY_API_VERSION", PINNED_GATEWAY_API_VERSION),
    ):
        found = re.findall(rf'^{name}="([^"]+)"', text, re.MULTILINE)
        assert found == [want], f"{name}: expected exactly [{want!r}], found {found}"
    assert text.count('--version "${LINKERD_CHART_VERSION}"') == 2, (
        "both linkerd-crds and linkerd-control-plane must install at the pinned "
        "chart version; an unpinned `helm install` floats"
    )
    assert "GATEWAY_API_MANIFEST=" in text and "${GATEWAY_API_VERSION}" in text


def test_the_pinned_versions_are_recorded_in_the_values_header():
    header = "\n".join(
        line for line in LINKERD_VALUES.read_text().splitlines()[:6] if line.startswith("#")
    )
    assert PINNED_LINKERD_CHART_VERSION in header
    assert PINNED_LINKERD_APP_VERSION in header


# --- The values: proxy tracing is on ---------------------------------------


def test_proxy_tracing_exports_to_the_collector_over_otlp():
    """The proxies send their spans to the collector over OTLP gRPC on 4317, and
    expect the collector's mesh identity. Parsed YAML, so a comment cannot
    satisfy it. The proxy has no sampler (it follows the incoming traceparent),
    so there is no sampling value to pin; the apps sample everything."""
    values = _yaml(LINKERD_VALUES)
    tracing = values["proxy"]["tracing"]
    assert tracing["enabled"] is True
    assert tracing["protocol"] == "opentelemetry"
    assert tracing["traceServiceName"] == "linkerd-proxy"
    assert tracing["collector"]["endpoint"] == "otel-collector.observability.svc.cluster.local:4317"
    assert tracing["collector"]["meshIdentity"] == {
        "serviceAccountName": "otel-collector",
        "namespace": "observability",
    }
    assert not any("sampl" in str(k).lower() for k in tracing), "the demo never samples"


def test_controller_tracing_stays_off():
    """linkerd2's Go controllers export with the OpenCensus agent exporter, not
    OTLP, and the collector has no OpenCensus receiver."""
    assert _yaml(LINKERD_VALUES)["controller"]["tracing"]["enabled"] is False


def test_identity_material_is_not_committed_in_the_values():
    text = yaml.dump(_yaml(LINKERD_VALUES))
    for key in ("identityTrustAnchorsPEM", "crtPEM", "keyPEM", "BEGIN"):
        assert key not in text, f"{key} must come from --set-file, not the committed values"


def test_identity_material_is_passed_with_set_file_from_the_ignored_dir():
    text = _effective_script()
    for key in (
        "identityTrustAnchorsPEM",
        "identity.issuer.tls.crtPEM",
        "identity.issuer.tls.keyPEM",
    ):
        assert f"--set-file {key}=" in text, f"{key} must be passed with --set-file"
    assert ".demo/linkerd-certs" in text
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".demo/linkerd-certs/ca.key"], cwd=REPO_ROOT
    )
    assert ignored.returncode == 0, ".demo/ must be git-ignored: the issuer key lives there"


def test_the_certificate_script_makes_p256_cas_with_the_right_constraints(tmp_path):
    """Runs the real script into a tmp dir and inspects the result with
    openssl: ECDSA P-256 on both, anchor CA 10 years, issuer a pathlen:0
    intermediate CA for one year signed by the anchor; and a second run reuses
    the files instead of rotating them."""
    out = tmp_path / "certs"
    subprocess.run(["bash", str(LINKERD_CERTS_SCRIPT), str(out)], check=True,
                   capture_output=True, timeout=60)

    def text(name):
        return subprocess.run(["openssl", "x509", "-in", str(out / name), "-noout", "-text"],
                              check=True, capture_output=True, text=True).stdout

    anchor, issuer = text("ca.crt"), text("issuer.crt")
    for t in (anchor, issuer):
        assert "prime256v1" in t and "ecdsa-with-SHA256" in t
        assert "CA:TRUE" in t
    assert "Subject: CN=root.linkerd.cluster.local" in anchor or "Subject: CN = root.linkerd.cluster.local" in anchor
    assert "identity.linkerd.cluster.local" in issuer
    assert "pathlen:0" in issuer and "pathlen" not in anchor
    verify = subprocess.run(["openssl", "verify", "-CAfile", str(out / "ca.crt"), str(out / "issuer.crt")],
                            capture_output=True, text=True)
    assert verify.returncode == 0, verify.stdout + verify.stderr
    before = (out / "issuer.key").read_bytes()
    subprocess.run(["bash", str(LINKERD_CERTS_SCRIPT), str(out)], check=True,
                   capture_output=True, timeout=60)
    assert (out / "issuer.key").read_bytes() == before, "second run must reuse, not rotate"


# --- The stage-2 dispatch in setup-kind.sh (text, comment-stripped) --------


def test_stage2_uses_the_stage2_kind_config():
    """Stage 2 must select its config by stage, never hardcode stage1.yaml.
    Comment-stripped, and
    `test_stage_two_creates_the_cluster_from_stage2_yaml_when_run` below
    proves the same thing behaviourally."""
    text = _effective_script()
    assert re.search(
        r'KIND_CONFIG="\$ROOT/deploy/kind/stage\$\{STAGE\}\.yaml"', text
    ), "the stage-2 path must select its kind config by stage"
    assert not re.search(r'KIND_CONFIG="[^"]*stage1\.yaml"', text), (
        "KIND_CONFIG must not be hardcoded to stage1.yaml"
    )
    assert 'kind create cluster --config "$KIND_CONFIG"' in text


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


def test_linkerd_is_ready_before_any_workload_is_deployed():
    """A pod admitted before the injector webhook is serving comes up WITHOUT
    its proxy and nothing says so. Computed over comment-stripped code, so an
    explanatory comment cannot satisfy the ordering while the real steps sit
    after the deploy. The order inside the install matters too: Gateway API
    CRDs, then linkerd-crds, then the control plane, then the Available wait."""
    text = _effective_script()
    gw = text.find('apply --server-side -f "${GATEWAY_API_MANIFEST}"')
    crds = text.find("upgrade --install linkerd-crds")
    cp = text.find("upgrade --install linkerd-control-plane")
    wait = text.find("-n linkerd wait")
    deploy = text.find("rebuild-and-deploy.sh")
    jaeger = text.find("jaeger.yaml")
    namespaces = text.find("deploy/k8s/namespaces.yaml")
    assert -1 not in (gw, crds, cp, wait, deploy, jaeger, namespaces)
    assert gw < crds < cp < wait, "Gateway API CRDs, linkerd-crds, control plane, then wait"
    assert "--for=condition=Available deployment --all" in text[wait:wait + 200]
    assert wait < namespaces, "the meshed namespaces must not exist before the injector does"
    assert wait < jaeger and wait < deploy
    assert "Linkerd ${LINKERD_APP_VERSION} is ready" in text


def test_namespaces_and_pods_are_meshed_where_the_design_says():
    """The three app namespaces by namespace annotation; agentgateway and the
    policy-decision-service by pod annotation; the collector by pod annotation,
    because the proxies export their spans to it over mTLS. Nothing else in
    observability, nothing in spire*, kyverno, flagd/OpenFeature, cert-manager, kube-system or linkerd."""
    namespaces = {d["metadata"]["name"]: d for d in _docs(REPO_ROOT / "deploy/k8s/namespaces.yaml")}
    meshed = {
        n for n, d in namespaces.items()
        if d["metadata"].get("annotations", {}).get("linkerd.io/inject") == "enabled"
    }
    assert meshed == {"northstar-agents", "northstar-tools", "northstar-systems"}

    for rel, kind in (
        ("deploy/platform/agentgateway/deployment.yaml", "Deployment"),
        ("deploy/k8s/policy-decision-service.yaml", "Deployment"),
        ("deploy/observability/collector.yaml", "Deployment"),
    ):
        deployments = [d for d in _docs(REPO_ROOT / rel) if d.get("kind") == kind]
        assert deployments, rel
        for d in deployments:
            ann = d["spec"]["template"]["metadata"].get("annotations", {})
            assert ann.get("linkerd.io/inject") == "enabled", f"{rel}: pod template not meshed"

    # And nothing else under deploy/ opts in: an annotation is invisible in a
    # green suite, so the whole tree is swept.
    offenders = []
    for path in sorted((REPO_ROOT / "deploy").rglob("*.yaml")):
        if path.name == "values.yaml" or "linkerd.io/inject" not in path.read_text():
            continue
        offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert sorted(offenders) == [
        "deploy/k8s/namespaces.yaml",
        "deploy/k8s/policy-decision-service.yaml",
        "deploy/observability/collector.yaml",
        "deploy/platform/agentgateway/deployment.yaml",
    ], offenders


def test_no_cilium_survives_in_effective_setup_code_except_the_refusal():
    """Comment-stripped. The only effective lines that may name Cilium are the
    existing-cluster check and its two refusal messages."""
    lines = [l for l in _effective_script().splitlines() if "cilium" in l.lower()]
    assert lines, "the existing-cluster refusal for a Cilium cluster is gone"
    for line in lines:
        assert (
            "get daemonset cilium" in line
            or "still runs Cilium" in line
            or "Until M12" in line
        ), f"unexpected Cilium reference in effective setup-kind.sh code: {line.strip()}"


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


def test_an_existing_cilium_cluster_is_not_silently_reused_for_stage_2():
    """A pre-M12 cluster was created with `disableDefaultCNI` and runs Cilium;
    the flag is create-time, so it cannot be converted and the script must not
    report success on it. Asserts on the `kubectl ... get daemonset cilium`
    probe and the `exit 1` that must follow it, in comment-stripped code, and
    `test_stage_two_refuses_an_existing_cilium_cluster_when_run` proves the
    behaviour directly."""
    text = _effective_script()
    probe = re.search(r"kubectl[^\n]*-n kube-system get daemonset cilium", text)
    assert probe, "the stage-2 path must probe for Cilium's DaemonSet in effective code"
    tail = text[probe.end():]
    assert "exit 1" in tail.split("fi")[0], "detecting Cilium and continuing is the silent-success outcome"


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
  port)
    echo "6443/tcp -> 127.0.0.1:41234"
    echo "30080/tcp -> 0.0.0.0:8080"
    [[ -z "${FAKE_NO_8081:-}" ]] && echo "30081/tcp -> 0.0.0.0:8081"
    exit 0 ;;
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
if [[ "$*" == *"get daemonset cilium"* ]]; then
  [[ -n "${FAKE_CILIUM_PRESENT:-}" ]] && exit 0
  exit 1
fi
exit 0
""",
    "helm": r"""#!/usr/bin/env bash
echo "helm $*" >> "$FAKE_LOG"
exit 0
""",
}


def _run_setup_kind(stage, cluster_exists, cilium_present, tmp_path, host_port_8081=True):
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
    if cilium_present:
        env["FAKE_CILIUM_PRESENT"] = "1"
    if not host_port_8081:
        env["FAKE_NO_8081"] = "1"
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
        "stage2.yaml -- each stage selects its own kind config by name"
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
        f"`--stage 1` created the cluster from {Path(config).name!r}"
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
def test_stage_two_refuses_an_existing_cilium_cluster_when_run(tmp_path):
    """The behavioural counterpart to
    `test_an_existing_cilium_cluster_is_not_silently_reused_for_stage_2`: with
    an existing cluster whose Cilium DaemonSet is present, `--stage 2` must
    exit non-zero with the delete-and-recreate hint, and must not proceed."""
    rc, out, calls = _run_setup_kind("2", True, True, tmp_path)
    assert rc != 0, (
        "`--stage 2` over an existing Cilium cluster exited 0 -- that cluster "
        f"was created with disableDefaultCNI and cannot be converted. Output:\n{out}"
    )
    assert "still runs Cilium" in out, f"expected the refusal to name the reason; got:\n{out}"
    assert "kind delete cluster --name northstar" in out, "the refusal must carry the recreate hint"
    assert not [c for c in calls if c.startswith("kind create cluster")]
    assert not [c for c in calls if c.startswith("helm upgrade")], (
        "the script must refuse BEFORE installing anything"
    )


@pytestmark_bash
def test_stage_two_proceeds_when_the_existing_cluster_has_no_cilium(tmp_path):
    """The guard must refuse a *Cilium* cluster specifically, not every
    existing cluster: a stage-1 cluster upgraded to stage 2 has the same CNI
    and is fine. Same inputs as above except the Cilium probe fails."""
    _, out, _ = _run_setup_kind("2", True, False, tmp_path)
    assert "still runs Cilium" not in out, f"the guard fired against a cluster with no Cilium:\n{out}"
    assert "Cluster already exists, skipping creation." in out


@pytestmark_bash
@pytest.mark.parametrize("stage", ["3", "7"])
def test_stage_three_plus_refuses_an_existing_cluster_without_host_port_8081(stage, tmp_path):
    """A stage-1/2 cluster has no 8081 mapping (create-time), so upgrading it to
    stage >= 3 must refuse with the delete-and-recreate hint, before installing."""
    rc, out, calls = _run_setup_kind(stage, True, False, tmp_path, host_port_8081=False)
    assert rc != 0, f"exited 0 over a cluster with no host port 8081. Output:\n{out}"
    assert "8081" in out and "kind delete cluster --name northstar" in out, out
    assert not [c for c in calls if c.startswith("kind create cluster")]
    assert not [c for c in calls if c.startswith("helm upgrade")], (
        "the script must refuse BEFORE installing anything")


@pytestmark_bash
def test_stage_three_proceeds_when_the_existing_cluster_has_host_port_8081(tmp_path):
    rc, out, _ = _run_setup_kind("3", True, False, tmp_path, host_port_8081=True)
    assert "no host port 8081" not in out, out
    assert "Cluster already exists, skipping creation." in out


@pytestmark_bash
def test_stage_two_does_not_need_host_port_8081(tmp_path):
    _, out, _ = _run_setup_kind("2", True, False, tmp_path, host_port_8081=False)
    assert "no host port 8081" not in out, out
    assert "Cluster already exists, skipping creation." in out


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
