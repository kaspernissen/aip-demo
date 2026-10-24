"""Guards the Linkerd configuration: the chart is pinned and installed before
any workload, the mesh annotations are where the design says, and proxy
tracing is on and points at the collector.

Every assertion here exists because getting it wrong costs a cluster recreate
to find out. Assertions over `scripts/setup-kind.sh` read the comment-stripped
script (`effective_script()` in tests/unit/_setup_script.py), so prose cannot
satisfy them. The YAML guards parse with `yaml.safe_load`, which discards
comments before any assertion runs.
"""
import re
import subprocess
from pathlib import Path

import yaml

from tests.unit._setup_script import effective_script as _effective_script

REPO_ROOT = Path(__file__).resolve().parents[2]
LINKERD_VALUES = REPO_ROOT / "deploy" / "platform" / "linkerd" / "values.yaml"
LINKERD_CERTS_SCRIPT = REPO_ROOT / "scripts" / "linkerd-certs.sh"

# Read off `helm search repo linkerd-edge --versions` and
# https://helm.linkerd.io/edge/index.yaml: the chart version is
# NOT the app version, and linkerd-crds / linkerd-control-plane share it.
PINNED_LINKERD_CHART_VERSION = "2026.10.1"
PINNED_LINKERD_APP_VERSION = "edge-26.10.1"
PINNED_GATEWAY_API_VERSION = "v1.5.1"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _docs(path: Path) -> list:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def test_cilium_values_are_gone():
    assert not (REPO_ROOT / "deploy" / "platform" / "cilium-values.yaml").exists()


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


def test_setup_creates_the_northstar_platform_namespace():
    """`deploy/k8s/namespaces.yaml` creates four namespaces (observability,
    northstar-agents, northstar-tools, northstar-systems) and no
    `northstar-platform`, so setup-kind.sh creates it itself.

    Requires the manifest's own `name:` line in comment-stripped code, and
    that it be applied — a comment explaining why the namespace is created
    here must not be able to satisfy this on its own.
    """
    text = _effective_script()
    assert re.search(r"^\s*name: northstar-platform\s*$", text, re.MULTILINE), (
        "setup-kind.sh must create the northstar-platform namespace — "
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


def test_no_cilium_survives_in_effective_setup_code():
    """Cilium is gone; kindnet enforces NetworkPolicy and Linkerd meshes."""
    lines = [l for l in _effective_script().splitlines() if "cilium" in l.lower()]
    assert not lines, f"unexpected Cilium reference in setup-kind.sh: {lines}"
