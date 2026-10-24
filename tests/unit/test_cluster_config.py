"""Guards the one kind cluster and how the laptop reaches it.

Every page and API the browser, `scripts/demo` and the tests open is a NodePort
Service published on a fixed localhost port by `deploy/kind/cluster.yaml`.
These checks keep the three places that name those ports in agreement -- the
kind config, the NodePort Services, and `scripts/preflight.sh`'s list -- and
keep the mesh-protected services off the host entirely.

Host port mappings are fixed when the cluster is created, so a mistake here
costs a cluster recreate to find out.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.unit._setup_script import KIND_CONFIG, effective_script, run_setup_kind

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO_ROOT / "scripts" / "preflight.sh"
PROMETHEUS_VALUES = REPO_ROOT / "deploy" / "observability" / "prometheus" / "values.yaml"
LINKERD_POLICIES = REPO_ROOT / "deploy" / "platform" / "reach" / "linkerd-policies.yaml"

# The localhost ports the demo, the tests, the browser build (customer-ui's
# NEXT_PUBLIC_* defaults), the collector's CORS origin and Backstage's baseUrl
# all name. Changing one means changing all of those too.
EXPECTED_HOST_PORTS = {8080, 8081, 19001, 16686, 4318, 9090, 18001, 7007}

_bash_only = pytest.mark.skipif(sys.platform == "win32", reason="bash-only harness")


def _mappings() -> list[dict]:
    config = yaml.safe_load(KIND_CONFIG.read_text())
    nodes = config["nodes"]
    assert len(nodes) == 1, "one node: the mappings below are on the control plane"
    return nodes[0]["extraPortMappings"]


def _host_ports() -> list[int]:
    return [m["hostPort"] for m in _mappings()]


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _nodeport_services() -> list[tuple[str, dict]]:
    """(where, service) for every NodePort Service the repo deploys: the
    manifests under deploy/, plus the Prometheus chart's server Service, which
    is configured in its Helm values rather than written as a manifest."""
    found = []
    for path in sorted((REPO_ROOT / "deploy").rglob("*.yaml")):
        if path.name == "values.yaml" or path.parent.name == "kind":
            continue
        for doc in _docs(path):
            if doc.get("kind") == "Service" and doc["spec"].get("type") == "NodePort":
                found.append((f"{path.relative_to(REPO_ROOT)}:{doc['metadata']['name']}", doc))
    server = yaml.safe_load(PROMETHEUS_VALUES.read_text())["server"]
    if server["service"].get("type") == "NodePort":
        found.append(("deploy/observability/prometheus/values.yaml:prometheus", {
            "metadata": {"name": "prometheus"},
            "spec": {"type": "NodePort", "selector": {"app.kubernetes.io/name": "prometheus"},
                     "ports": [{"port": server["service"]["servicePort"],
                                "nodePort": server["service"].get("nodePort")}]},
        }))
    return found


# --- The kind config ------------------------------------------------------------


def test_there_is_exactly_one_kind_config():
    assert sorted(p.name for p in KIND_CONFIG.parent.iterdir()) == ["cluster.yaml"]


def test_the_cluster_keeps_the_default_cni_and_the_registry_wiring():
    """kindnet is what enforces the egress NetworkPolicies, so the default CNI
    must never be disabled. Parsed, so a comment cannot trip it."""
    config = yaml.safe_load(KIND_CONFIG.read_text())
    assert config["name"] == "northstar"
    assert "disableDefaultCNI" not in yaml.dump(config)
    assert any('config_path = "/etc/containerd/certs.d"' in patch
               for patch in config["containerdConfigPatches"])


def test_the_host_ports_are_the_ones_everything_else_names():
    ports = _host_ports()
    assert len(ports) == len(set(ports)), f"a host port is mapped twice: {ports}"
    assert set(ports) == EXPECTED_HOST_PORTS


def test_every_mapping_is_tcp_onto_a_valid_nodeport_on_loopback_only():
    """kind's default listenAddress is 0.0.0.0, which would put the
    unauthenticated Backstage and reviewer page on the conference network."""
    for mapping in _mappings():
        assert mapping.get("protocol", "TCP") == "TCP", mapping
        assert mapping.get("listenAddress") == "127.0.0.1", mapping
        assert 30000 <= mapping["containerPort"] <= 32767, (
            f"{mapping}: kind publishes NodePorts, which Kubernetes allocates from 30000-32767"
        )


# --- NodePort Services and mappings agree, one to one ---------------------------


def test_every_nodeport_service_port_has_a_fixed_nodeport_and_a_mapping():
    """A NodePort Service with no mapping is unreachable from the laptop, and an
    unpinned nodePort is allocated at random, so it can never be mapped."""
    mapped = {m["containerPort"] for m in _mappings()}
    for where, service in _nodeport_services():
        for port in service["spec"]["ports"]:
            node_port = port.get("nodePort")
            assert node_port, f"{where}: port {port.get('port')} has no fixed nodePort"
            assert node_port in mapped, (
                f"{where}: nodePort {node_port} has no extraPortMapping in deploy/kind/cluster.yaml"
            )


def test_every_mapping_is_backed_by_exactly_one_nodeport_service():
    node_ports = [
        port["nodePort"]
        for _, service in _nodeport_services()
        for port in service["spec"]["ports"]
    ]
    for mapping in _mappings():
        assert node_ports.count(mapping["containerPort"]) == 1, (
            f"{mapping}: expected exactly one NodePort Service on {mapping['containerPort']}, "
            f"found {node_ports.count(mapping['containerPort'])}"
        )


def _host_reachable_servers() -> set[str]:
    """Linkerd Servers that admit a caller with no mesh identity on every path:
    a PathPrefix `/` route authorized for a NetworkAuthentication."""
    docs = _docs(LINKERD_POLICIES)
    network_auths = {
        (d["metadata"]["namespace"], d["metadata"]["name"])
        for d in docs if d["kind"] == "NetworkAuthentication"
    }
    open_routes = set()
    for d in docs:
        if d["kind"] != "AuthorizationPolicy" or d["spec"]["targetRef"]["kind"] != "HTTPRoute":
            continue
        refs = d["spec"]["requiredAuthenticationRefs"]
        if any(r["kind"] == "NetworkAuthentication"
               and (d["metadata"]["namespace"], r["name"]) in network_auths for r in refs):
            open_routes.add((d["metadata"]["namespace"], d["spec"]["targetRef"]["name"]))
    reachable = set()
    for d in docs:
        if d["kind"] != "HTTPRoute" or (d["metadata"]["namespace"], d["metadata"]["name"]) not in open_routes:
            continue
        catch_all = any(
            m.get("path") == {"type": "PathPrefix", "value": "/"} and "method" not in m
            for rule in d["spec"]["rules"] for m in rule["matches"]
        )
        if catch_all:
            for parent in d["spec"]["parentRefs"]:
                reachable.add(parent["name"])
    return reachable


def test_no_nodeport_leads_to_a_server_that_refuses_callers_without_identity():
    """payments-api, payments-mcp, settlement-agent and the PDS admit only named
    workload identities. A request through a NodePort arrives from the node
    with no identity, so a NodePort there would be refused by design (and
    opening it would undo the reach policy). Tests reach them through
    port-forwards instead (tests/harness/live.py)."""
    reachable = _host_reachable_servers()
    protected = {
        d["spec"]["podSelector"]["matchLabels"]["app"]: d["metadata"]["name"]
        for d in _docs(LINKERD_POLICIES)
        if d["kind"] == "Server" and d["metadata"]["name"] not in reachable
    }
    assert {"payments-api", "payments-mcp", "settlement-agent", "policy-decision-service"} <= set(protected)
    for where, service in _nodeport_services():
        app = service["spec"]["selector"].get("app")
        assert app not in protected, (
            f"{where} publishes {app}, whose Linkerd Server {protected[app]!r} "
            "refuses callers without a mesh identity"
        )


# --- preflight.sh checks exactly the published ports ----------------------------


def test_preflight_checks_every_host_port_and_nothing_else():
    text = PREFLIGHT.read_text()
    block = text.split("SURFACES=(", 1)[1].split("\n)", 1)[0]
    ports = [int(m) for m in re.findall(r'^\s*"(\d+)\|', block, re.M)]
    assert len(ports) == len(set(ports))
    assert set(ports) == set(_host_ports())


def test_preflight_is_valid_bash():
    subprocess.run(["bash", "-n", str(PREFLIGHT)], check=True)


# --- setup-kind.sh: one pass, one config, no port-forwards ----------------------


def test_setup_kind_has_no_stages_and_uses_the_one_config():
    text = effective_script()
    assert 'KIND_CONFIG="$ROOT/deploy/kind/cluster.yaml"' in text
    assert 'kind create cluster --config "$KIND_CONFIG"' in text
    assert not re.search(r"\bSTAGE\b", text) and "--stage" not in text


def test_setup_kind_starts_no_port_forward():
    """Only the cleanup of stale forwards may name port-forward."""
    for line in effective_script().splitlines():
        if "port-forward" in line:
            assert line.strip().startswith("pkill -f"), line


def test_setup_kind_ends_with_the_preflight():
    text = effective_script()
    assert '"$ROOT/scripts/preflight.sh"' in text
    assert text.index('"$ROOT/scripts/preflight.sh"') > text.index("rebuild-and-deploy.sh\" reviewer-ui")


def test_start_demo_is_gone_and_nothing_operator_facing_points_at_it_or_at_stages():
    assert not (REPO_ROOT / "scripts" / "start-demo.sh").exists()
    files = subprocess.run(
        # `:(glob)` so `docs/*.md` does not reach the dated plans and specs
        # under docs/superpowers/, which record history as it was.
        ["git", "ls-files", "README.md", ":(glob)docs/*.md", "scripts", "tests", "deploy"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    offenders = []
    for name in files:
        path = REPO_ROOT / name
        if path == Path(__file__) or not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        if "start-demo" in text or re.search(r"setup-kind\.sh --stage", text):
            offenders.append(name)
    assert not offenders, offenders


# --- Behavioural: the real script against fakes ---------------------------------


@_bash_only
def test_a_cold_run_creates_the_cluster_from_cluster_yaml(tmp_path):
    rc, out, calls = run_setup_kind(tmp_path)
    creates = [c for c in calls if c.startswith("kind create cluster")]
    assert len(creates) == 1, out
    assert creates[0].endswith("deploy/kind/cluster.yaml"), creates[0]
    # The stale-forward cleanup only ever names this repo's forwards.
    for call in (c for c in calls if c.startswith("pkill")):
        assert "northstar" in call, call


@_bash_only
def test_an_existing_cluster_with_every_host_port_is_reused(tmp_path):
    rc, out, calls = run_setup_kind(tmp_path, cluster_exists=True, mapped_ports=_host_ports())
    assert not any(c.startswith("kind create") for c in calls), out
    assert "Cluster already exists, skipping creation." in out
    assert any("cluster-info" in c for c in calls), "the run should continue to cluster-info"


@_bash_only
def test_an_existing_cluster_missing_a_host_port_is_refused(tmp_path):
    """Host ports are fixed at creation: an older cluster cannot gain one, so
    continuing would leave a UI silently unreachable."""
    ports = [p for p in _host_ports() if p != 7007]
    rc, out, calls = run_setup_kind(tmp_path, cluster_exists=True, mapped_ports=ports)
    assert rc != 0
    assert "7007" in out and "teardown.sh" in out, out
    assert not any(c.startswith("kind create") for c in calls)
    assert not any("cluster-info" in c for c in calls)


@_bash_only
def test_the_old_stage_flag_is_refused(tmp_path):
    rc, out, calls = run_setup_kind(tmp_path, args=("--stage", "7"))
    assert rc != 0 and "unknown argument" in out
    assert not calls, "nothing may run on an unknown argument"
