"""The reach matrix: who may reach what, under Linkerd and NetworkPolicy.

Nothing here reads a policy's *shape*. The two files
under `deploy/platform/reach/` are evaluated, by a small reader that
implements only the semantics this repo uses, against a table of requests
(the REACH MATRIX below), and the table is the spec: every path the demo needs
is a row that says `allow`, and every path claimed to be closed is a
row that says `deny-403`, `deny-404-no-route` or `drop`.

**Two enforcement points, two kinds of row.**

* *Ingress* is Linkerd's, at the destination's proxy. A row is (caller,
  destination workload, port, method, path) and the outcome is one of
  `allow`, `deny-403` (a Server or route matched and the caller's mesh
  identity, or lack of one, is not authorized: what the proxy answers with an
  empty 403 and counts in `inbound_http_authz_deny_total`), or
  `deny-404-no-route` (the Server has routes and none matched the request).
* *Egress* is core NetworkPolicy, enforced in the node by kindnet. A row is
  (source workload, destination workload+port or CIDR+port) and the outcome
  is `allow` or `drop`.

**What the reader models, and from where it learns it.** Linkerd semantics:

* a port without a `Server` is open (the default inbound policy is
  `all-unauthenticated`); a `Server` denies whatever no policy authorizes;
* a Server with **no routes** has Linkerd's default `probe` route: `GET` on the
  pod's own `httpGet` probe paths is open to every caller, everything
  else needs an AuthorizationPolicy targeting the Server;
* a Server **with routes** authorizes per route only: a request matching no
  route is a 404 from the proxy, a matched route needs an
  AuthorizationPolicy targeting that HTTPRoute, and the kubelet probe is
  refused unless a route for its path is authorized for the node;
* route precedence follows Gateway API: Exact beats a regular expression
  beats a prefix, a longer prefix beats a shorter one, a route that names a
  method beats one that does not;
* a MeshTLSAuthentication admits exactly its listed identities (an unmeshed
  caller has none); a NetworkAuthentication admits by source CIDR and nothing
  else; a policy with both requires both;
* a meshed workload calling a meshed peer under an egress policy needs TCP
  4143, the peer proxy's inbound port (with the service port alone the
  call is a 504), and the Linkerd control plane on 8080, 8086 and 8090. And
  4143 is ALL it needs: the client proxy tunnels through 4143 and the
  peer's inbound proxy forwards to whatever port was asked for, so for meshed
  sources egress to a meshed peer is per POD, not per port. Port rules still
  bind an UNMESHED source (no proxy, so the kernel sees the real port). Which
  ports actually answer is the peer's Servers' job (ingress, per port and per
  identity).

Workloads, namespaces, service accounts and probes are read from the real
manifests under `deploy/k8s/` (and the gateway's), not retyped here, so the
matrix cannot drift from what actually runs.

**What this does not prove.** Enforcement. That is the e2e reach files'
job, against the live cluster. What it proves is that the two policy files say
what the table says, and that the table says what the demo claims.
"""
import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from tests.unit._setup_script import effective_script as _effective_script

REPO_ROOT = Path(__file__).resolve().parents[2]
REACH_DIR = REPO_ROOT / "deploy" / "platform" / "reach"
LINKERD_POLICIES = REACH_DIR / "linkerd-policies.yaml"
NETWORK_POLICIES = REACH_DIR / "network-policies.yaml"

LINKERD_GROUP = "policy.linkerd.io"
IDENTITY_SUFFIX = "serviceaccount.identity.linkerd.cluster.local"
PROXY_INBOUND_PORT = 4143
CONTROL_PLANE_PORTS = (8080, 8086, 8090)
ANTHROPIC_V4 = "160.79.104.0/23"
ANTHROPIC_V6 = "2607:6bc0::/48"


# --- The cluster the policies are evaluated against, read from manifests -----


@dataclass(frozen=True)
class Workload:
    name: str
    namespace: str
    labels: dict
    service_account: str
    meshed: bool
    ports: frozenset = frozenset()
    # {port: {httpGet path, ...}} -- the paths kubelet GETs, by container port.
    http_probes: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def identity(self):
        if not self.meshed:
            return None
        return f"{self.service_account}.{self.namespace}.{IDENTITY_SUFFIX}"


def _docs(path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _inject_namespaces():
    return {
        d["metadata"]["name"]
        for d in _docs(REPO_ROOT / "deploy" / "k8s" / "namespaces.yaml")
        if d["kind"] == "Namespace"
        and d["metadata"].get("annotations", {}).get("linkerd.io/inject") == "enabled"
    }


def _load_workloads():
    inject = _inject_namespaces()
    paths = sorted((REPO_ROOT / "deploy" / "k8s").glob("*.yaml")) + [
        REPO_ROOT / "deploy" / "platform" / "agentgateway" / "deployment.yaml"
    ]
    found = {}
    for path in paths:
        for d in _docs(path):
            if d.get("kind") != "Deployment":
                continue
            ns = d["metadata"]["namespace"]
            template = d["spec"]["template"]
            pod = template["spec"]
            annotations = template["metadata"].get("annotations", {})
            ports, probes = set(), {}
            for c in pod["containers"]:
                for p in c.get("ports", []):
                    ports.add(p["containerPort"])
                for key in ("readinessProbe", "livenessProbe"):
                    probe = c.get(key, {}).get("httpGet")
                    if probe:
                        probes.setdefault(probe["port"], set()).add(probe["path"])
            name = d["metadata"]["name"]
            found[name] = Workload(
                name=name,
                namespace=ns,
                labels=dict(template["metadata"]["labels"]),
                service_account=pod.get("serviceAccountName", "default"),
                meshed=(
                    annotations.get("linkerd.io/inject") == "enabled" or ns in inject
                ),
                ports=frozenset(ports),
                http_probes=probes,
            )
    return found


WORKLOADS = _load_workloads()

# Pods the policies talk about that are not read from deploy/k8s/: CoreDNS, the
# Linkerd control plane, the collector. CoreDNS and the control plane are not
# meshed. The collector is (deploy/observability/collector.yaml), because the
# Linkerd proxies export their spans to it over mTLS; its entry is checked
# against that manifest by test_the_collector_entry_matches_its_manifest.
INFRA = {
    "kube-dns": Workload(
        "kube-dns", "kube-system", {"k8s-app": "kube-dns"}, "coredns", False,
        frozenset({53}),
    ),
    "linkerd-control-plane": Workload(
        "linkerd-control-plane", "linkerd",
        {"linkerd.io/control-plane-component": "destination"}, "linkerd-destination",
        False, frozenset(CONTROL_PLANE_PORTS),
    ),
    "otel-collector": Workload(
        "otel-collector", "observability", {"app": "otel-collector"}, "otel-collector",
        True, frozenset({4317, 4318}),
    ),
}
ALL_PODS = {**WORKLOADS, **INFRA}


@dataclass(frozen=True)
class Caller:
    identity: str | None  # None: no mesh identity (an unmeshed peer)
    ip: str


POD_IP = "10.244.0.50"
NODE_IP = "172.18.0.1"  # kubelet and the host-published NodePort come from here


def caller_for(name: str) -> Caller:
    if name == "unmeshed":  # a pod that skips the mesh, e.g. a pod made for the bypass test
        return Caller(None, POD_IP)
    if name == "kubelet":
        return Caller(None, NODE_IP)
    if name == "browser":  # via the kind NodePort: no identity, node source address
        return Caller(None, NODE_IP)
    return Caller(WORKLOADS[name].identity, POD_IP)


# --- Linkerd: the reader -------------------------------------------------------


def _linkerd_docs():
    return _docs(LINKERD_POLICIES)


def _by_kind(kind):
    return [d for d in _linkerd_docs() if d["kind"] == kind]


def _labels_match(selector, labels):
    assert set(selector) <= {"matchLabels"}, (
        f"the reader implements matchLabels only; got {selector}"
    )
    return all(labels.get(k) == v for k, v in selector.get("matchLabels", {}).items())


def _server_for(workload: Workload, port: int):
    hits = [
        s
        for s in _by_kind("Server")
        if s["metadata"]["namespace"] == workload.namespace
        and s["spec"]["port"] == port
        and _labels_match(s["spec"]["podSelector"], workload.labels)
    ]
    assert len(hits) <= 1, (
        f"two Servers select {workload.name}:{port}: "
        f"{[h['metadata']['name'] for h in hits]}"
    )
    return hits[0] if hits else None


def _routes_of(server):
    ns, name = server["metadata"]["namespace"], server["metadata"]["name"]
    return [
        r
        for r in _by_kind("HTTPRoute")
        if r["metadata"]["namespace"] == ns
        and any(
            p.get("kind", "Server") == "Server" and p["name"] == name
            for p in r["spec"]["parentRefs"]
        )
    ]


def _path_rank(path_match, path):
    """Return a precedence tuple if the path matches, else None."""
    kind = path_match.get("type", "PathPrefix")
    value = path_match.get("value", "/")
    if kind == "Exact":
        return (3, len(value)) if path == value else None
    if kind == "RegularExpression":
        return (2, 0) if re.search(value, path) else None
    if kind == "PathPrefix":
        prefix = value.rstrip("/")
        if path == value or path == prefix or path.startswith(prefix + "/"):
            return (1, len(prefix))
        return None
    raise AssertionError(f"unmodelled path match type {kind!r}")


def _best_route(routes, method, path):
    best, best_rank = None, None
    for route in routes:
        for rule in route["spec"].get("rules", []):
            for m in rule.get("matches", [{}]):
                if m.get("method") and m["method"] != method:
                    continue
                if m.get("headers") or m.get("queryParams"):
                    raise AssertionError("the reader does not model header/query matches")
                ranked = _path_rank(m.get("path", {}), path)
                if ranked is None:
                    continue
                rank = (*ranked, 1 if m.get("method") else 0)
                if best_rank is None or rank > best_rank:
                    best, best_rank = route, rank
                elif rank == best_rank and best is not route:
                    raise AssertionError(
                        f"routes {best['metadata']['name']} and "
                        f"{route['metadata']['name']} tie for {method} {path}"
                    )
    return best


def _authn_admits(ref, caller: Caller, namespace: str) -> bool:
    ns = ref.get("namespace", namespace)
    kind = ref["kind"]
    target = next(
        (
            d
            for d in _by_kind(kind)
            if d["metadata"]["name"] == ref["name"] and d["metadata"]["namespace"] == ns
        ),
        None,
    )
    assert target is not None, f"AuthorizationPolicy refers to missing {kind} {ns}/{ref['name']}"
    if kind == "MeshTLSAuthentication":
        assert "identityRefs" not in target["spec"], "the reader models `identities` only"
        return caller.identity is not None and caller.identity in target["spec"]["identities"]
    if kind == "NetworkAuthentication":
        ip = ipaddress.ip_address(caller.ip)
        for net in target["spec"]["networks"]:
            if ip in ipaddress.ip_network(net["cidr"]) and not any(
                ip in ipaddress.ip_network(x) for x in net.get("except", [])
            ):
                return True
        return False
    raise AssertionError(f"unmodelled authentication kind {kind}")


def _policies_targeting(kind, name, namespace):
    return [
        p
        for p in _by_kind("AuthorizationPolicy")
        if p["metadata"]["namespace"] == namespace
        and p["spec"]["targetRef"]["kind"] == kind
        and p["spec"]["targetRef"]["name"] == name
    ]


def _authorized(kind, name, namespace, caller: Caller) -> bool:
    """Any policy targeting the object whose authentications ALL admit the
    caller authorizes it (identity and network refs combine as AND)."""
    for policy in _policies_targeting(kind, name, namespace):
        refs = policy["spec"]["requiredAuthenticationRefs"]
        if refs and all(_authn_admits(r, caller, namespace) for r in refs):
            return True
    return False


def ingress_outcome(caller_name, dst, port, method, path):
    caller = caller_for(caller_name)
    workload = WORKLOADS[dst]
    path = path.split("?", 1)[0]  # route matching sees the path, never the query
    server = _server_for(workload, port)
    if server is None:
        return "allow"
    ns, sname = server["metadata"]["namespace"], server["metadata"]["name"]
    routes = _routes_of(server)
    if not routes:
        # Linkerd's default probe route: the pod's httpGet probe paths,
        # GET only, open to every caller.
        if method == "GET" and path in workload.http_probes.get(port, set()):
            return "allow"
        return "allow" if _authorized("Server", sname, ns, caller) else "deny-403"
    route = _best_route(routes, method, path)
    if route is None:
        return "deny-404-no-route"
    if _authorized("HTTPRoute", route["metadata"]["name"], ns, caller):
        return "allow"
    return "deny-403"


# --- NetworkPolicy: the reader -------------------------------------------------


def _network_policies():
    return [d for d in _docs(NETWORK_POLICIES) if d["kind"] == "NetworkPolicy"]


def _ns_labels(namespace):
    return {"kubernetes.io/metadata.name": namespace}  # set by the API server on every namespace


def _peer_matches(peer, dst, dst_ip, policy_ns):
    if "ipBlock" in peer:
        if dst_ip is None:
            return False
        block = peer["ipBlock"]
        ip = ipaddress.ip_address(dst_ip)
        net = ipaddress.ip_network(block["cidr"])
        return (ip.version == net.version and ip in net) and not any(
            ip.version == ipaddress.ip_network(x).version and ip in ipaddress.ip_network(x)
            for x in block.get("except", [])
        )
    if dst is None:
        return False
    ns_sel, pod_sel = peer.get("namespaceSelector"), peer.get("podSelector")
    if ns_sel is None and pod_sel is None:
        return False
    if ns_sel is not None and not _labels_match(ns_sel, _ns_labels(dst.namespace)):
        return False
    if ns_sel is None and dst.namespace != policy_ns:  # podSelector alone: own namespace
        return False
    if pod_sel is not None and not _labels_match(pod_sel, dst.labels):
        return False
    return True


def _np_egress_allows(src: Workload, dst, dst_ip, port, proto="TCP") -> bool:
    selecting = [
        p
        for p in _network_policies()
        if p["metadata"]["namespace"] == src.namespace
        and "Egress" in p["spec"].get("policyTypes", [])
        and _labels_match(p["spec"]["podSelector"], src.labels)
    ]
    if not selecting:
        return True  # no policy selects the pod: unrestricted
    for policy in selecting:
        for rule in policy["spec"].get("egress", []):
            ports = rule.get("ports")
            if ports is not None and not any(
                p.get("protocol", "TCP") == proto and p["port"] == port for p in ports
            ):
                continue
            peers = rule.get("to")
            if peers is None or any(
                _peer_matches(p, dst, dst_ip, policy["metadata"]["namespace"]) for p in peers
            ):
                return True
    return False


def egress_outcome(src_name, dst_name=None, port=None, *, ip=None, proto="TCP",
                   unmeshed=False):
    """`unmeshed=True` evaluates the same pod's labels as a pod with no proxy
    (the e2e bypass pod): then the port rules bind exactly as written."""
    src = WORKLOADS[src_name]
    if ip is not None:
        return "allow" if _np_egress_allows(src, None, ip, port, proto) else "drop"
    dst = ALL_PODS[dst_name]
    if src.meshed and dst.meshed and not unmeshed:
        # A meshed peer is reached through its proxy's inbound port,
        # and a rule that admits 4143 admits EVERY port of that peer.
        return "allow" if _np_egress_allows(src, dst, None, PROXY_INBOUND_PORT, "TCP") else "drop"
    return "allow" if _np_egress_allows(src, dst, None, port, proto) else "drop"


# --- THE REACH MATRIX ---------------------------------------------------------
#
# (caller, destination workload, port, method, path) -> outcome.
# Rows marked "Cilium:" reproduce a rule of the earlier Cilium policy; the rest say why they exist.

MCP = ("POST", "/mcp")
PDS_CHECK = ("POST", "/envoy.service.ext_mcp.v1.ExtMcp/Check")
CARD = ("GET", "/.well-known/agent-card.json")
PID = "/payments/PAY-000123"

INGRESS = [
    # payments-mcp:8000 -- agentgateway only (Cilium: payments-mcp-gateway-only).
    ("agentgateway", "payments-mcp", 8000, *MCP, "allow"),
    ("claims-agent", "payments-mcp", 8000, *MCP, "deny-403"),
    ("settlement-agent", "payments-mcp", 8000, *MCP, "deny-403"),
    ("reviewer-ui", "payments-mcp", 8000, *MCP, "deny-403"),
    ("policy-decision-service", "payments-mcp", 8000, *MCP, "deny-403"),
    ("unmeshed", "payments-mcp", 8000, *MCP, "deny-403"),
    # policy-decision-service:9100 -- agentgateway only (Cilium: ...-gateway-only).
    ("agentgateway", "policy-decision-service", 9100, *PDS_CHECK, "allow"),
    ("settlement-agent", "policy-decision-service", 9100, *PDS_CHECK, "deny-403"),
    ("claims-agent", "policy-decision-service", 9100, *PDS_CHECK, "deny-403"),
    ("payments-mcp", "policy-decision-service", 9100, *PDS_CHECK, "deny-403"),
    ("unmeshed", "policy-decision-service", 9100, *PDS_CHECK, "deny-403"),
    # settlement-agent:9002 -- claims-agent only (Cilium: settlement-agent-ingress-allowlist).
    ("claims-agent", "settlement-agent", 9002, "POST", "/", "allow"),
    ("claims-agent", "settlement-agent", 9002, *CARD, "allow"),
    ("reviewer-ui", "settlement-agent", 9002, "POST", "/", "deny-403"),
    ("customer-ui", "settlement-agent", 9002, "POST", "/", "deny-403"),
    ("unmeshed", "settlement-agent", 9002, "POST", "/", "deny-403"),
    # The default probe route is open to everyone, by Linkerd's design:
    # kubelet needs it, and the path is the public agent card, a read.
    ("kubelet", "settlement-agent", 9002, *CARD, "allow"),
    ("reviewer-ui", "settlement-agent", 9002, *CARD, "allow"),
    ("kubelet", "settlement-agent", 9002, "GET", "/tasks", "deny-403"),
    # payments-api:8080 -- one route per (method, path) family, by caller identity.
    # These rules sit on payments-api's ingress. Callers per client file: tools/payments-mcp/payments_client.py,
    # apps/reviewer-ui/ledger.py, agents/claims-agent/ledger.py, pds/ledger.py.
    ("payments-mcp", "payments-api", 8080, "POST", "/payments/propose", "allow"),
    ("payments-mcp", "payments-api", 8080, "POST", PID + "/execute", "allow"),
    ("payments-mcp", "payments-api", 8080, "POST", PID + "/refuse", "allow"),
    ("payments-mcp", "payments-api", 8080, "GET", PID, "allow"),
    ("reviewer-ui", "payments-api", 8080, "GET", "/payments", "allow"),
    ("reviewer-ui", "payments-api", 8080, "GET", "/payments?status=awaiting_approval", "allow"),
    ("reviewer-ui", "payments-api", 8080, "GET", PID, "allow"),
    ("reviewer-ui", "payments-api", 8080, "POST", PID + "/reject", "allow"),
    ("claims-agent", "payments-api", 8080, "GET", PID, "allow"),
    ("policy-decision-service", "payments-api", 8080, "GET", PID, "allow"),
    ("kubelet", "payments-api", 8080, "GET", "/healthz", "allow"),
    # ... and every other combination is closed.
    ("reviewer-ui", "payments-api", 8080, "POST", PID + "/execute", "deny-403"),
    ("reviewer-ui", "payments-api", 8080, "POST", PID + "/refuse", "deny-403"),
    ("reviewer-ui", "payments-api", 8080, "POST", "/payments/propose", "deny-403"),
    ("claims-agent", "payments-api", 8080, "POST", PID + "/execute", "deny-403"),
    ("claims-agent", "payments-api", 8080, "POST", PID + "/reject", "deny-403"),
    ("claims-agent", "payments-api", 8080, "GET", "/payments", "deny-403"),
    ("policy-decision-service", "payments-api", 8080, "POST", PID + "/execute", "deny-403"),
    ("policy-decision-service", "payments-api", 8080, "GET", "/payments", "deny-403"),
    ("payments-mcp", "payments-api", 8080, "GET", "/payments", "deny-403"),
    ("payments-mcp", "payments-api", 8080, "POST", PID + "/reject", "deny-403"),
    ("settlement-agent", "payments-api", 8080, "GET", PID, "deny-403"),
    ("settlement-agent", "payments-api", 8080, "POST", PID + "/execute", "deny-403"),
    ("agentgateway", "payments-api", 8080, "POST", PID + "/execute", "deny-403"),
    ("unmeshed", "payments-api", 8080, "POST", PID + "/execute", "deny-403"),
    ("unmeshed", "payments-api", 8080, "GET", PID, "deny-403"),
    ("kubelet", "payments-api", 8080, "POST", PID + "/execute", "deny-403"),
    # The id shape is payments-api's own (PAY- and six digits): anything else
    # matches no route at all, and a path nothing serves is a 404 from the proxy.
    ("payments-mcp", "payments-api", 8080, "GET", "/payments/PAY-12", "deny-404-no-route"),
    ("payments-mcp", "payments-api", 8080, "GET", "/payments/PAY-000123/extra", "deny-404-no-route"),
    ("payments-mcp", "payments-api", 8080, "GET", "/metrics", "deny-404-no-route"),
    ("payments-mcp", "payments-api", 8080, "DELETE", PID, "deny-404-no-route"),
    # customer-ui:8080 -- only claims-agent may POST /a2a/push; everything else
    # (the browser, the NodePort, the readiness probe) is unchanged.
    ("claims-agent", "customer-ui", 8080, "POST", "/a2a/push", "allow"),
    ("reviewer-ui", "customer-ui", 8080, "POST", "/a2a/push", "deny-403"),
    ("settlement-agent", "customer-ui", 8080, "POST", "/a2a/push", "deny-403"),
    ("unmeshed", "customer-ui", 8080, "POST", "/a2a/push", "deny-403"),
    ("browser", "customer-ui", 8080, "POST", "/a2a/push", "deny-403"),
    ("browser", "customer-ui", 8080, "GET", "/", "allow"),
    ("browser", "customer-ui", 8080, "GET", "/_next/static/app.js", "allow"),
    ("kubelet", "customer-ui", 8080, "GET", "/healthz", "allow"),
    ("reviewer-ui", "customer-ui", 8080, "GET", "/", "allow"),
    # No Server, no restriction (Cilium put no ingress policy on these).
    ("reviewer-ui", "claims-agent", 9001, "POST", "/", "allow"),
    ("browser", "claims-agent", 9001, "POST", "/", "allow"),
    ("settlement-agent", "agentgateway", 3000, *MCP, "allow"),
    ("reviewer-ui", "agentgateway", 3000, *MCP, "allow"),
    ("unmeshed", "agentgateway", 3000, *MCP, "allow"),
    ("kubelet", "agentgateway", 15021, "GET", "/healthz/ready", "allow"),
    ("claims-agent", "reviewer-ui", 8080, "GET", "/", "allow"),
]


@pytest.mark.parametrize(
    "caller,dst,port,method,path,expected",
    INGRESS,
    ids=[f"{r[0]}->{r[1]}:{r[2]} {r[3]} {r[4]} = {r[5]}" for r in INGRESS],
)
def test_ingress_reach_matrix(caller, dst, port, method, path, expected):
    assert ingress_outcome(caller, dst, port, method, path) == expected


ANTHROPIC_HOST = "160.79.104.10"
OTHER_ANTHROPIC_HOST = "160.79.105.200"
EXAMPLE_COM = "93.184.215.14"

# (source, destination workload, port) or (source, None, port, ip) -> allow | drop
EGRESS = [
    # settlement-agent (Cilium: settlement-agent-egress-allowlist).
    ("settlement-agent", "kube-dns", 53, None, "allow"),
    ("settlement-agent", "agentgateway", 3000, None, "allow"),
    ("settlement-agent", "agentgateway", 4000, None, "allow"),  # the llm listener
    ("settlement-agent", "otel-collector", 4318, None, "allow"),
    ("settlement-agent", "linkerd-control-plane", 8080, None, "allow"),
    ("settlement-agent", "linkerd-control-plane", 8086, None, "allow"),
    ("settlement-agent", "linkerd-control-plane", 8090, None, "allow"),
    ("settlement-agent", None, 443, ANTHROPIC_HOST, "drop"),
    ("settlement-agent", None, 443, OTHER_ANTHROPIC_HOST, "drop"),
    ("settlement-agent", None, 443, "2607:6bc0::10", "drop"),
    ("settlement-agent", None, 443, EXAMPLE_COM, "drop"),
    ("settlement-agent", None, 443, "160.79.106.1", "drop"),
    ("settlement-agent", None, 80, ANTHROPIC_HOST, "drop"),
    ("settlement-agent", None, 443, "2607:6bc1::10", "drop"),
    ("settlement-agent", "payments-mcp", 8000, None, "drop"),  # the gateway is the only route
    ("settlement-agent", "payments-api", 8080, None, "drop"),
    ("settlement-agent", "policy-decision-service", 9100, None, "drop"),
    ("settlement-agent", "claims-agent", 9001, None, "drop"),
    # The collector is meshed, so a meshed source reaches it through 4143 like
    # any meshed peer (per pod). The proxy's own 4317 export is checked apart, in
    # test_every_egress_restricted_proxy_may_export_its_spans_on_4317.
    ("settlement-agent", "otel-collector", 4317, None, "allow"),
    # claims-agent (Cilium: claims-agent-egress-allowlist).
    ("claims-agent", "kube-dns", 53, None, "allow"),
    ("claims-agent", "settlement-agent", 9002, None, "allow"),
    ("claims-agent", "payments-api", 8080, None, "allow"),
    ("claims-agent", "customer-ui", 8080, None, "allow"),
    ("claims-agent", "otel-collector", 4318, None, "allow"),
    ("claims-agent", "linkerd-control-plane", 8086, None, "allow"),
    # The intake model: claims-agent's only way to it is the gateway's llm
    # listener. The provider's published ranges and the rest of the internet are
    # dropped, so the model key and the model's address are not the pod's to use.
    ("claims-agent", "agentgateway", 4000, None, "allow"),
    ("claims-agent", None, 443, ANTHROPIC_HOST, "drop"),
    ("claims-agent", None, 443, OTHER_ANTHROPIC_HOST, "drop"),
    ("claims-agent", None, 443, "2607:6bc0::10", "drop"),
    ("claims-agent", None, 443, EXAMPLE_COM, "drop"),
    ("claims-agent", None, 443, "160.79.106.1", "drop"),
    ("claims-agent", None, 80, ANTHROPIC_HOST, "drop"),
    ("claims-agent", None, 443, "2607:6bc1::10", "drop"),
    ("claims-agent", "payments-mcp", 8000, None, "drop"),  # direct bypass of the gateway
    # The rule that reaches agentgateway:4000 also carries 4143, and a meshed
    # source reaches EVERY port of the peer through 4143 (per pod, not per port).
    # So the MCP port is open to claims-agent at L4. What keeps it out of the
    # tools is the gateway itself (its MCP route's jwtAuth and the PDS), never
    # this NetworkPolicy. An unmeshed claims-agent is held to 4000 only, below.
    ("claims-agent", "agentgateway", 3000, None, "allow"),
    ("claims-agent", "reviewer-ui", 8080, None, "drop"),
    # Egress to a meshed peer is per pod. 4143 is allowed to these peers, so
    # every port of them is allowed for a meshed claims-agent; the peer's Servers
    # decide what answers. These rows document the limitation, they do not bless it.
    ("claims-agent", "customer-ui", 9090, None, "allow"),
    ("claims-agent", "settlement-agent", 9999, None, "allow"),
    ("claims-agent", "payments-api", 9999, None, "allow"),
    # customer-ui (Cilium: customer-ui-egress-allowlist).
    ("customer-ui", "kube-dns", 53, None, "allow"),
    ("customer-ui", "otel-collector", 4318, None, "allow"),
    ("customer-ui", "linkerd-control-plane", 8090, None, "allow"),
    ("customer-ui", None, 443, ANTHROPIC_HOST, "drop"),
    ("customer-ui", None, 443, EXAMPLE_COM, "drop"),
    ("customer-ui", "claims-agent", 9001, None, "drop"),
    ("customer-ui", "settlement-agent", 9002, None, "drop"),
    ("customer-ui", "payments-api", 8080, None, "drop"),
    # reviewer-ui (Cilium: reviewer-ui-egress-allowlist).
    ("reviewer-ui", "kube-dns", 53, None, "allow"),
    ("reviewer-ui", "payments-api", 8080, None, "allow"),
    ("reviewer-ui", "claims-agent", 9001, None, "allow"),
    ("reviewer-ui", "agentgateway", 3000, None, "allow"),
    ("reviewer-ui", "otel-collector", 4318, None, "allow"),
    ("reviewer-ui", "linkerd-control-plane", 8080, None, "allow"),
    ("reviewer-ui", None, 443, ANTHROPIC_HOST, "drop"),
    ("reviewer-ui", None, 443, EXAMPLE_COM, "drop"),
    ("reviewer-ui", "payments-mcp", 8000, None, "drop"),
    ("reviewer-ui", "policy-decision-service", 9100, None, "drop"),
    ("reviewer-ui", "settlement-agent", 9002, None, "drop"),
]


@pytest.mark.parametrize(
    "src,dst,port,ip,expected",
    EGRESS,
    ids=[f"{r[0]}->{r[1] or r[3]}:{r[2]} = {r[4]}" for r in EGRESS],
)
def test_egress_reach_matrix(src, dst, port, ip, expected):
    assert egress_outcome(src, dst, port, ip=ip) == expected


@pytest.mark.parametrize("proto", ["UDP", "TCP"])
@pytest.mark.parametrize("src", ["settlement-agent", "claims-agent", "customer-ui", "reviewer-ui"])
def test_dns_is_open_on_udp_and_tcp(src, proto):
    assert egress_outcome(src, "kube-dns", 53, proto=proto) == "allow"


@pytest.mark.parametrize(
    "src,dst,port",
    [("claims-agent", "customer-ui", 9090), ("claims-agent", "settlement-agent", 9999),
     ("claims-agent", "payments-api", 9999), ("claims-agent", "agentgateway", 3000)],
)
def test_port_rules_bind_an_unmeshed_source_but_not_a_meshed_one(src, dst, port):
    """The same labels, with and without a proxy: a pod that skips the proxy is
    held to the ports written in the rule; a meshed pod is held to 4143 only."""
    assert egress_outcome(src, dst, port) == "allow"
    assert egress_outcome(src, dst, port, unmeshed=True) == "drop"
    assert egress_outcome(src, dst, 4143, unmeshed=True) == "allow"


def test_a_meshed_peer_is_reached_on_4143_alone():
    """No rule lists port 9090 or 9999 for these peers, yet a meshed source gets
    through: 4143 is the whole gate."""
    for dst in ("customer-ui", "settlement-agent", "payments-api"):
        listed = {
            p["port"]
            for pol in _network_policies()
            if pol["metadata"]["name"] == "claims-agent-egress"
            for rule in pol["spec"]["egress"]
            for peer in rule.get("to", [])
            if _peer_matches(peer, ALL_PODS[dst], None, "northstar-agents")
            for p in rule["ports"]
        }
        assert 4143 in listed and 9999 not in listed
        assert egress_outcome("claims-agent", dst, 9999) == "allow"


@pytest.mark.parametrize("src", ["settlement-agent", "claims-agent", "customer-ui", "reviewer-ui"])
def test_the_meshed_peer_needs_the_proxy_port_not_just_the_service_port(src):
    """Allowing only a meshed peer's service port yields a 504. For every
    allowed meshed peer, both ports must be allowed."""
    for dst in ("agentgateway", "settlement-agent", "payments-api", "claims-agent", "customer-ui"):
        for port in WORKLOADS[dst].ports:
            direct = _np_egress_allows(WORKLOADS[src], WORKLOADS[dst], None, port)
            if direct and port != PROXY_INBOUND_PORT:
                assert _np_egress_allows(WORKLOADS[src], WORKLOADS[dst], None, PROXY_INBOUND_PORT), (
                    f"{src} may reach {dst}:{port} but not {dst}:4143; through the "
                    "mesh that is a 504"
                )


# --- Structure the matrix cannot see -----------------------------------------

EGRESS_SOURCES = ["settlement-agent", "claims-agent", "customer-ui", "reviewer-ui"]


def test_the_agent_plane_workloads_have_the_expected_servers():
    """Parity with the earlier Cilium policy: exactly the protected ports get a Server."""
    servers = {
        (s["metadata"]["namespace"], s["spec"]["podSelector"]["matchLabels"]["app"], s["spec"]["port"])
        for s in _by_kind("Server")
    }
    assert servers == {
        ("northstar-tools", "payments-mcp", 8000),
        ("northstar-platform", "policy-decision-service", 9100),
        ("northstar-agents", "settlement-agent", 9002),
        ("northstar-systems", "payments-api", 8080),
        ("northstar-agents", "customer-ui", 8080),
    }


def test_every_servers_port_is_a_container_port_of_the_workload_it_selects():
    """A Server on a port the pod does not declare selects nothing useful: the
    default policy would keep applying and the refusals would silently not
    happen. The port must be one of the workload's `containerPort`s, read from
    its Deployment."""
    for s in _by_kind("Server"):
        app = s["spec"]["podSelector"]["matchLabels"]["app"]
        ns = s["metadata"]["namespace"]
        workload = WORKLOADS[app]
        assert workload.namespace == ns, (s["metadata"]["name"], workload.namespace, ns)
        assert s["spec"]["port"] in workload.ports, (
            f"Server {s['metadata']['name']} names port {s['spec']['port']}, but "
            f"{app} declares containerPorts {sorted(workload.ports)}"
        )


def test_every_server_denies_what_no_policy_authorizes():
    for s in _by_kind("Server"):
        assert s["apiVersion"] == "policy.linkerd.io/v1beta3"
        assert s["spec"].get("accessPolicy") == "deny", s["metadata"]["name"]


def test_pds_server_speaks_grpc():
    pds = next(s for s in _by_kind("Server") if s["spec"]["port"] == 9100)
    assert pds["spec"]["proxyProtocol"] == "gRPC"


def test_a_server_with_routes_has_no_server_level_policy():
    """A Server-level policy beside routes is a second, blanket way in that the
    per-route table would not show."""
    for s in _by_kind("Server"):
        if _routes_of(s):
            assert not _policies_targeting("Server", s["metadata"]["name"], s["metadata"]["namespace"]), (
                f"{s['metadata']['name']} has routes AND a Server-level AuthorizationPolicy"
            )


def test_every_authorization_policy_targets_a_real_server_or_route():
    servers = {(s["metadata"]["namespace"], s["metadata"]["name"]) for s in _by_kind("Server")}
    routes = {(r["metadata"]["namespace"], r["metadata"]["name"]) for r in _by_kind("HTTPRoute")}
    for p in _by_kind("AuthorizationPolicy"):
        assert p["apiVersion"] == "policy.linkerd.io/v1alpha1"
        ref = p["spec"]["targetRef"]
        assert ref["group"] == LINKERD_GROUP
        key = (p["metadata"]["namespace"], ref["name"])
        assert key in (servers if ref["kind"] == "Server" else routes), (p["metadata"]["name"], ref)
        assert p["spec"]["requiredAuthenticationRefs"], f"{p['metadata']['name']} authorizes nobody"


def test_every_http_route_has_a_policy_or_is_unreachable_by_design():
    for r in _by_kind("HTTPRoute"):
        assert r["apiVersion"] == "policy.linkerd.io/v1beta3"
        assert _policies_targeting("HTTPRoute", r["metadata"]["name"], r["metadata"]["namespace"]), (
            f"HTTPRoute {r['metadata']['name']} has no AuthorizationPolicy: it admits nobody"
        )


def test_regular_expressions_are_anchored():
    """An unanchored `PAY-[0-9]{6}` also matches `/x/PAY-000001/y`."""
    for r in _by_kind("HTTPRoute"):
        for rule in r["spec"]["rules"]:
            for m in rule["matches"]:
                p = m.get("path", {})
                if p.get("type") == "RegularExpression":
                    assert p["value"].startswith("^") and p["value"].endswith("$"), p["value"]


def test_no_policy_names_the_default_service_account_as_a_caller():
    for a in _by_kind("MeshTLSAuthentication"):
        for ident in a["spec"]["identities"]:
            assert not ident.startswith("default."), (
                f"{a['metadata']['name']} admits {ident}: two workloads share `default`"
            )


def test_every_named_identity_is_a_real_workload_identity():
    real = {w.identity for w in WORKLOADS.values() if w.meshed}
    for a in _by_kind("MeshTLSAuthentication"):
        for ident in a["spec"]["identities"]:
            assert ident in real, f"{a['metadata']['name']} names {ident}, which no meshed workload has"


@pytest.mark.parametrize(
    "name,namespace",
    [
        ("claims-agent", "northstar-agents"),
        ("customer-ui", "northstar-agents"),
        ("payments-mcp", "northstar-tools"),
        ("payments-api", "northstar-systems"),
    ],
)
def test_ruling_3_the_workload_has_its_own_service_account(name, namespace):
    assert WORKLOADS[name].service_account == name, (
        f"{name} runs as {WORKLOADS[name].service_account!r}; a policy cannot tell it from its neighbours"
    )
    declared = [
        d
        for path in (REPO_ROOT / "deploy" / "k8s").glob("*.yaml")
        for d in _docs(path)
        if d.get("kind") == "ServiceAccount"
        and d["metadata"]["name"] == name
        and d["metadata"]["namespace"] == namespace
    ]
    assert len(declared) == 1, f"no ServiceAccount {namespace}/{name} is declared beside the Deployment"


def test_no_meshed_workload_that_policy_depends_on_runs_as_default():
    for name in ("agentgateway", "policy-decision-service", "settlement-agent", "claims-agent",
                 "customer-ui", "reviewer-ui", "payments-mcp", "payments-api"):
        assert WORKLOADS[name].service_account != "default", name
        assert WORKLOADS[name].meshed, f"{name} is not meshed: its identity is not checkable"


@pytest.mark.parametrize("dst", sorted(WORKLOADS))
def test_a_server_with_routes_lets_kubelet_probe_the_pod(dst):
    """The failure that does not look like a policy failure: a Server with
    routes refuses the kubelet probe unless a route authorizes it, the pod
    never goes Ready, and (for the PDS) fail-closed refuses every payment."""
    w = WORKLOADS[dst]
    for port, paths in w.http_probes.items():
        server = _server_for(w, port)
        if server is None:
            continue
        for path in paths:
            assert ingress_outcome("kubelet", dst, port, "GET", path) == "allow", (
                f"kubelet cannot probe {dst}:{port}{path}"
            )


@pytest.mark.parametrize("src", EGRESS_SOURCES)
def test_an_egress_restricted_pod_still_reaches_what_makes_it_work(src):
    """DNS, the Linkerd control plane (without it the proxy never starts the
    pod's traffic) and the collector (whose absence is silent: the demo works
    and every span vanishes)."""
    for port in CONTROL_PLANE_PORTS:
        assert egress_outcome(src, "linkerd-control-plane", port) == "allow", port
    assert egress_outcome(src, "otel-collector", 4318) == "allow"
    assert egress_outcome(src, "kube-dns", 53) == "allow"


@pytest.mark.parametrize("src", EGRESS_SOURCES)
def test_every_egress_restricted_proxy_may_export_its_spans_on_4317(src):
    """The Linkerd proxy sends its spans to the collector itself, not through an
    outbound proxy, so the kernel sees the real port, 4317, and the 4143 rule
    does not cover it. Without it every proxy span from this pod is lost and
    nothing fails."""
    collector = ALL_PODS["otel-collector"]
    assert _np_egress_allows(WORKLOADS[src], collector, None, 4317, "TCP"), src
    # The app's export, which the source proxy now sends to the collector's
    # proxy on 4143, and the plain port a pod outside the mesh would use.
    assert _np_egress_allows(WORKLOADS[src], collector, None, PROXY_INBOUND_PORT, "TCP"), src
    assert egress_outcome(src, "otel-collector", 4318, unmeshed=True) == "allow", src


def test_the_collector_entry_matches_its_manifest():
    """INFRA's collector is typed here, so it is held to the manifest: meshed by
    pod annotation, its own service account (the identity the proxies expect,
    deploy/platform/linkerd/values.yaml), its label and its two OTLP ports."""
    docs = _docs(REPO_ROOT / "deploy" / "observability" / "collector.yaml")
    dep = next(d for d in docs if d["kind"] == "Deployment")
    template = dep["spec"]["template"]
    entry = INFRA["otel-collector"]
    assert template["metadata"]["annotations"]["linkerd.io/inject"] == "enabled"
    assert entry.meshed is True
    assert template["spec"]["serviceAccountName"] == entry.service_account
    assert any(d["kind"] == "ServiceAccount" and d["metadata"]["name"] == entry.service_account
               for d in docs)
    assert template["metadata"]["labels"] == entry.labels
    ports = {p["containerPort"] for c in template["spec"]["containers"] for p in c["ports"]}
    assert ports == set(entry.ports)
    values = yaml.safe_load(
        (REPO_ROOT / "deploy" / "platform" / "linkerd" / "values.yaml").read_text())
    identity = values["proxy"]["tracing"]["collector"]["meshIdentity"]
    assert identity == {"serviceAccountName": entry.service_account,
                        "namespace": entry.namespace}


MODEL_AGENTS = {"settlement-agent", "claims-agent"}


def test_no_agent_plane_pod_reaches_the_model_provider_directly():
    """The requirement: direct egress from `northstar-agents` to the model
    provider is denied. The two agents that call a model do so through the
    gateway's llm listener, so no pod in the namespace, them included, may reach
    the published ranges on 443."""
    checked = 0
    for src in WORKLOADS:
        if WORKLOADS[src].namespace != "northstar-agents" or src not in EGRESS_SOURCES:
            continue
        for ip in (ANTHROPIC_HOST, OTHER_ANTHROPIC_HOST, "2607:6bc0::10"):
            assert egress_outcome(src, port=443, ip=ip) == "drop", (src, ip)
        checked += 1
    assert checked >= len(MODEL_AGENTS)


@pytest.mark.parametrize("src", sorted(MODEL_AGENTS))
def test_a_model_agent_reaches_the_model_only_through_the_gateways_llm_listener(src):
    assert egress_outcome(src, "agentgateway", 4000) == "allow"
    # Unmeshed, the rule's listed ports bind: 4000 is written in it.
    assert egress_outcome(src, "agentgateway", 4000, unmeshed=True) == "allow"


@pytest.mark.parametrize("src", sorted(set(EGRESS_SOURCES) - MODEL_AGENTS))
def test_only_the_model_agents_may_reach_the_gateways_llm_listener(src):
    """customer-ui has no rule to the gateway at all; reviewer-ui's rule lists
    3000 and 4143. For an unmeshed reviewer-ui, 4000 is not written, so a pod
    that skipped its proxy could not use the model path."""
    assert egress_outcome(src, "agentgateway", 4000, unmeshed=True) == "drop", src


@pytest.mark.parametrize("src", sorted(MODEL_AGENTS))
def test_a_model_agent_reaches_nothing_else_on_the_internet(src):
    for ip in (EXAMPLE_COM, "1.1.1.1", "8.8.8.8", "160.79.106.1", "2607:6bc1::10"):
        assert egress_outcome(src, port=443, ip=ip) == "drop", (src, ip)


def test_no_egress_policy_names_an_address_range():
    """There is no direct path to the provider any more, so no document carries
    an ipBlock at all: a range rule is exactly how a direct path would return."""
    for p in _network_policies():
        for rule in p["spec"].get("egress", []):
            for peer in rule.get("to", []):
                assert "ipBlock" not in peer, (p["metadata"]["name"], peer)
    text = NETWORK_POLICIES.read_text()
    non_comment = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert ANTHROPIC_V4 not in non_comment and ANTHROPIC_V6 not in non_comment


def test_network_policies_are_egress_only_and_select_one_workload():
    policies = _network_policies()
    assert {p["spec"]["podSelector"]["matchLabels"]["app"] for p in policies} == set(EGRESS_SOURCES)
    for p in policies:
        assert p["spec"]["policyTypes"] == ["Egress"], p["metadata"]["name"]
        assert p["metadata"]["namespace"] == "northstar-agents"
        assert p["spec"]["podSelector"]["matchLabels"], "an empty selector is the whole namespace"


def test_every_document_says_what_it_enforces():
    """One comment per document, in the style of the Cilium file this replaces."""
    for path in (LINKERD_POLICIES, NETWORK_POLICIES):
        chunks = [c for c in re.split(r"(?m)^---\s*$", path.read_text()) if "kind:" in c]
        assert chunks
        for chunk in chunks:
            head = chunk.split("apiVersion:", 1)[0]
            assert len([l for l in head.splitlines() if l.lstrip().startswith("#")]) >= 2, (
                f"{path.name}: a document with no comment above it:\n{chunk[:200]}"
            )


# --- Applied by the setup script, and Cilium's file is gone --------------------


def test_cilium_policies_file_is_deleted():
    assert not (REPO_ROOT / "deploy" / "platform" / "cilium-policies.yaml").exists()


def test_setup_applies_the_reach_directory_after_the_namespaces_exist():
    script = _effective_script()
    assert "cilium-policies" not in script
    apply = re.search(r"apply[^\n]*deploy/platform/reach", script)
    assert apply, "setup-kind.sh does not apply deploy/platform/reach/"
    assert script.index("deploy/k8s/namespaces.yaml") < apply.start()
    assert script.index("name: northstar-platform") < apply.start(), (
        "the PDS Server lives in northstar-platform; the namespace must exist first"
    )
    # Linkerd's CRDs must be installed before the Servers can apply.
    assert script.index("linkerd-control-plane") < apply.start()
