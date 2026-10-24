"""Reach probes: what to run in a pod, and how to read what came back.

M12 moved the reach guarantees from Cilium to Linkerd (ingress, by workload
identity) and core NetworkPolicy (egress). Both change what a refusal looks
like, so the oracles changed. They come from the measurements in
`docs/superpowers/plans/2026-10-21-northstar-m12-linkerd.md` (M1-M6), and every
decision the e2e files make is a pure function here.

**Ingress refused** = HTTP 403 (a matched route or a route-less Server, wrong
identity) or a proxy 404 (no route matched on a Server that has routes), an
empty body, no `l5d-proxy-error` header, AND the destination proxy's
`inbound_http_authz_deny_total` for that Server/route/caller rising by at
least one across the call. The counter is what attributes the refusal to
Linkerd's policy rather than to an application that happens to answer 403.
Never TCP connect: an unmeshed or meshed caller can always open the socket,
the proxy accepts it and answers above TCP.

**Egress refused**: from a *meshed* pod a TCP connect "succeeds" for every
address (the pod's own outbound proxy accepts it locally), so the verdict is
read above TCP: a TLS handshake plus an HTTP request. Allowed = a status line
comes back. Refused = reset or timeout. From an *unmeshed* pod the kernel
drops the packet and the connect itself times out.

**Nothing here goes through `kubectl port-forward`** (M3): a port-forward
reaches the app on loopback and bypasses the proxy, so it cannot show a
refusal. Probes run inside a pod (`kubectl exec`); metrics are read through
the API server's pod proxy (`kubectl get --raw .../pods/<pod>:4191/proxy/metrics`).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

IDENTITY_SUFFIX = "serviceaccount.identity.linkerd.cluster.local"
PROXY_ADMIN_PORT = 4191
DENY_METRIC = "inbound_http_authz_deny_total"

# Runs INSIDE a pod: python3 -c "$PROBE_SRC" <host> <port> <method> <path> <timeout> <tls|plain|tcp>
# Prints exactly one JSON line. `stage` says how far it got, which is the
# whole point of probing above TCP: "connect" means the socket never opened,
# "tls" means the handshake failed, "request" means the request got no answer.
PROBE_SRC = r"""
import http.client, json, socket, ssl, sys
host, port, method, path, timeout, mode = (
    sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], float(sys.argv[5]), sys.argv[6])
out = {"status": None, "headers": {}, "body_len": None, "error": None, "stage": "connect"}
try:
    sock = socket.create_connection((host, port), timeout=timeout)
    if mode == "tcp":
        # connect-only: the oracle for an UNMESHED pod, where the kernel drops
        sock.close()
        out["stage"] = "connected"
        print(json.dumps(out))
        sys.exit(0)
    if mode == "tls":
        out["stage"] = "tls"
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
    out["stage"] = "request"
    if mode == "tls":
        c = http.client.HTTPSConnection(host, port, timeout=timeout)
    else:
        c = http.client.HTTPConnection(host, port, timeout=timeout)
    c.sock = sock
    body = b"{}" if method == "POST" else None
    c.request(method, path, body=body,
              headers={"content-type": "application/json", "connection": "close"})
    r = c.getresponse()
    data = r.read()
    out["status"] = r.status
    out["headers"] = {k.lower(): v for k, v in r.getheaders()}
    out["body_len"] = len(data)
    out["stage"] = "done"
except Exception as exc:
    out["error"] = type(exc).__name__
print(json.dumps(out))
"""


class ProbeError(ValueError):
    """The probe printed nothing a verdict can be read from."""


def parse_probe_output(stdout: str) -> dict:
    """The probe's last JSON line, or ProbeError (never a guess)."""
    lines = [ln for ln in (stdout or "").strip().splitlines() if ln.strip()]
    if not lines:
        raise ProbeError("probe produced no output")
    try:
        result = json.loads(lines[-1])
    except ValueError as exc:
        raise ProbeError(f"probe produced unreadable output: {lines[-1]!r}") from exc
    if not isinstance(result, dict) or "status" not in result or "error" not in result:
        raise ProbeError(f"probe output has the wrong shape: {result!r}")
    return result


def _proxy_error(result: dict) -> str | None:
    return (result.get("headers") or {}).get("l5d-proxy-error")


# --- ingress ---------------------------------------------------------------


def classify_ingress(result: dict) -> str:
    """One of: refused-403, refused-404, answered, proxy-error, no-response.

    `refused-*` is the shape of a Linkerd policy refusal (empty body, no
    `l5d-proxy-error`). An app that answers 403 with a body is `answered`,
    not a refusal: it is the application speaking, not the proxy.
    `proxy-error` is a request the proxy could not carry (a 5xx with
    `l5d-proxy-error`): neither reached nor refused, usually a broken
    egress rule. `no-response` is a transport failure.
    """
    status = result.get("status")
    if status is None:
        return "no-response"
    if _proxy_error(result):
        return "proxy-error"
    if status in (403, 404) and not result.get("body_len"):
        return f"refused-{status}"
    return "answered"


def grpc_refused(result: dict) -> bool:
    """gRPC PERMISSION_DENIED, in either form it can arrive.

    Measured at the PDS in the final review (M7: `PERMISSION_DENIED`). A refusal
    at the HTTP/2 layer is an HTTP 403, a gRPC-aware refusal is `grpc-status: 7`. Accept both, and
    require no `l5d-proxy-error` so a proxy failure is not mistaken for it.
    """
    if result.get("status") is None or _proxy_error(result):
        return False
    headers = result.get("headers") or {}
    return result["status"] == 403 or str(headers.get("grpc-status")) == "7"


# --- egress ----------------------------------------------------------------


def classify_egress(result: dict) -> tuple[str, str]:
    """(verdict, how). verdict is `reached` or `dropped`.

    `reached` = a status line came back (the TLS handshake, if any, completed
    and the server answered). A 502 with an empty body and no `l5d-proxy-error`
    is `("reached", "peer-proxy-502")`: the peer's inbound proxy answered for a
    port with no listener (M7), so the pod, not the port, was what the policy let in. `dropped` = anything else; `how` names it:
    `timeout`, `reset`, `proxy-error` (the pod's own proxy could not reach the
    peer, e.g. the node dropped its packets) or `other:<ExceptionName>`.
    """
    if (result.get("status") == 502 and not result.get("body_len")
            and not _proxy_error(result)):
        # M7: a meshed client got through to the peer POD's proxy, which found
        # nothing listening on the port it was asked for. Reached, deliberately:
        # the packets were not dropped, so egress to this pod is open on every port.
        return "reached", "peer-proxy-502"
    if result.get("status") is not None and not _proxy_error(result):
        return "reached", "answered"
    if result.get("status") is not None:
        return "dropped", "proxy-error"
    error = result.get("error") or "unknown"
    if error in ("TimeoutError", "timeout") or error.endswith("Timeout"):
        return "dropped", "timeout"
    if error in ("ConnectionResetError", "BrokenPipeError", "RemoteDisconnected",
                 "ConnectionAbortedError", "IncompleteRead"):
        return "dropped", "reset"
    return "dropped", f"other:{error}"


def classify_tcp(result: dict) -> str:
    """Connect-only probe (mode `tcp`), meaningful from an UNMESHED pod only:
    `connected`, `timeout` (the kernel dropped the SYN: NetworkPolicy), or
    `refused`/`other:<Name>`. From a meshed pod this says nothing, because the
    pod's own proxy accepts every connect (M1)."""
    if result.get("stage") == "connected" and not result.get("error"):
        return "connected"
    error = result.get("error") or "unknown"
    if error in ("TimeoutError", "timeout"):
        return "timeout"
    if error == "ConnectionRefusedError":
        return "refused"
    return f"other:{error}"


def egress_is_dropped(result: dict) -> bool:
    return classify_egress(result)[0] == "dropped"


# --- the deny counter --------------------------------------------------------

_METRIC_LINE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)\{(?P<labels>[^}]*)\}\s+(?P<value>[-+0-9.eE]+|NaN|\+Inf)\s*$"
)
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class DenyCounter:
    labels: tuple  # sorted (key, value) pairs, hashable
    value: float

    def label(self, key: str, default: str = "") -> str:
        return dict(self.labels).get(key, default)


def parse_deny_counters(metrics_text: str) -> list[DenyCounter]:
    """Every `inbound_http_authz_deny_total` sample in a proxy metrics page."""
    out = []
    for line in (metrics_text or "").splitlines():
        if not line.startswith(DENY_METRIC):
            continue
        m = _METRIC_LINE.match(line.strip())
        if not m or m["name"] != DENY_METRIC:
            continue
        labels = tuple(sorted((k, v) for k, v in _LABEL.findall(m["labels"])))
        out.append(DenyCounter(labels, float(m["value"])))
    return out


def client_id(service_account: str, namespace: str) -> str:
    return f"{service_account}.{namespace}.{IDENTITY_SUFFIX}"


def _matches(c: DenyCounter, want: dict) -> bool:
    return all(c.label(k) == v for k, v in want.items() if v is not None)


def deny_total(counters: list[DenyCounter], **want) -> float:
    """Sum of the samples whose labels match every given label (None = any)."""
    return sum(c.value for c in counters if _matches(c, want))


def deny_delta(before: list[DenyCounter], after: list[DenyCounter], **want) -> float:
    """How far the matching counter rose across a call (never negative:
    a restarted proxy resets its counters, which is not a refusal)."""
    return max(0.0, deny_total(after, **want) - deny_total(before, **want))


def refused_by(before: list[DenyCounter], after: list[DenyCounter],
               **want) -> tuple[str, str, float] | None:
    """(server, route, delta) for the sample that rose the most, or None.

    The Server name comes from `srv_name`; the route (or `authz_name` /
    `route_name`) is whatever the proxy labelled the refusal with. This is the
    "refused by" name the demo prints.
    """
    before_by = {c.labels: c.value for c in before}
    best = None
    for c in after:
        if not _matches(c, want):
            continue
        delta = c.value - before_by.get(c.labels, 0.0)
        if delta > 0 and (best is None or delta > best[2]):
            best = (c.label("srv_name"), c.label("route_name"), delta)
    return best


def refusal_line(status: int, by: tuple[str, str, float] | None) -> str:
    """The line the demo prints for an ingress refusal."""
    if by is None:
        return f"REFUSED {status} (no deny counter moved: not attributable to Linkerd)"
    server, route, delta = by
    name = f"{server}/{route}" if route else server
    return f"REFUSED {status} by {name} (deny counter +{int(delta)})"


def egress_line(verdict: str, how: str, meshed: bool) -> str:
    """The line the demo prints for an egress probe."""
    if verdict == "reached":
        return "REACHED"
    if how == "timeout":
        return "DROPPED (timeout)"
    if meshed and how in ("reset", "proxy-error"):
        return f"DROPPED (TLS {how})" if how == "reset" else "DROPPED (proxy error)"
    return f"DROPPED ({how})"


def metrics_proxy_path(namespace: str, pod: str) -> str:
    """The API-server pod-proxy path for a pod's linkerd-proxy metrics.

    Through the API server, not `kubectl port-forward` to the app port (M3).
    """
    return f"/api/v1/namespaces/{namespace}/pods/{pod}:{PROXY_ADMIN_PORT}/proxy/metrics"


# --- thin impure wrappers (the e2e files and scripts use these) -----------------


def kubectl(context: str, *args: str, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", context, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def read_deny_counters(context: str, namespace: str, pod: str) -> list[DenyCounter]:
    proc = kubectl(context, "get", "--raw", metrics_proxy_path(namespace, pod))
    if proc.returncode != 0:
        raise ProbeError(
            f"could not read {namespace}/{pod} proxy metrics through the API "
            f"server: {proc.stderr.strip()}"
        )
    return parse_deny_counters(proc.stdout)


def exec_probe(context: str, namespace: str, pod: str, container: str, host: str,
               port: int, method: str = "GET", path: str = "/", timeout: float = 8,
               tls: bool = False, mode: str | None = None) -> dict:
    proc = kubectl(
        context, "-n", namespace, "exec", pod, "-c", container, "--",
        "python3", "-c", PROBE_SRC, host, str(port), method, path, str(timeout),
        mode or ("tls" if tls else "plain"),
        timeout=int(timeout) * 2 + 45,
    )
    try:
        return parse_probe_output(proc.stdout)
    except ProbeError as exc:
        raise ProbeError(f"{exc}; exit {proc.returncode}; stderr: {proc.stderr.strip()}") from exc


# --- gRPC (the PDS) ---------------------------------------------------------

# Runs INSIDE a pod whose image carries grpcio (settlement-agent's does, via
# spiffe): a generic unary call, so no generated stubs are needed. Prints one
# JSON line {"grpc_code": <name or null>, "error": <exception name or null>}.
GRPC_PROBE_SRC = r"""
import json, sys
import grpc
target, method, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])
out = {"grpc_code": None, "error": None}
try:
    ch = grpc.insecure_channel(target)
    call = ch.unary_unary(method, request_serializer=lambda b: b,
                          response_deserializer=lambda b: b)
    call(b"", timeout=timeout)
    out["grpc_code"] = "OK"
except grpc.RpcError as exc:
    out["grpc_code"] = exc.code().name
except Exception as exc:
    out["error"] = type(exc).__name__
print(json.dumps(out))
"""

PDS_METHOD = "/agentgateway.dev.ext_mcp.ExtMcp/CheckRequest"


def classify_grpc(result: dict) -> str:
    """`refused` (PERMISSION_DENIED), `reached` (any other answer from the
    server, including an application error), `unreachable` (UNAVAILABLE or
    DEADLINE_EXCEEDED: nothing answered) or `error`.

    A client library maps an HTTP 403 at the HTTP/2 layer and a `grpc-status: 7`
    trailer to the same PERMISSION_DENIED, so one name covers both forms.
    Measured on the spike in the final review (plan M7).
    """
    code = result.get("grpc_code")
    if code is None:
        return "error"
    if code == "PERMISSION_DENIED":
        return "refused"
    if code in ("UNAVAILABLE", "DEADLINE_EXCEEDED"):
        return "unreachable"
    return "reached"


def exec_grpc_probe(context: str, namespace: str, pod: str, container: str,
                    target: str, timeout: float = 8,
                    method: str = PDS_METHOD) -> dict:
    proc = kubectl(
        context, "-n", namespace, "exec", pod, "-c", container, "--",
        "python3", "-c", GRPC_PROBE_SRC, target, method, str(timeout),
        timeout=int(timeout) * 2 + 45,
    )
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise ProbeError(
            f"unreadable gRPC probe output {proc.stdout!r}; exit {proc.returncode}; "
            f"stderr: {proc.stderr.strip()}"
        ) from exc


# --- the objects the cluster must hold ------------------------------------------

_RESOURCE_FOR_KIND = {
    "Server": "servers.policy.linkerd.io",
    "AuthorizationPolicy": "authorizationpolicies.policy.linkerd.io",
    "HTTPRoute": "httproutes.policy.linkerd.io",
    "MeshTLSAuthentication": "meshtlsauthentications.policy.linkerd.io",
    "NetworkAuthentication": "networkauthentications.policy.linkerd.io",
    "NetworkPolicy": "networkpolicies.networking.k8s.io",
}
REPO_ROOT = Path(__file__).resolve().parents[2]
REACH_FILES = (
    REPO_ROOT / "deploy" / "platform" / "reach" / "linkerd-policies.yaml",
    REPO_ROOT / "deploy" / "platform" / "reach" / "network-policies.yaml",
)


def repo_reach_documents(files=REACH_FILES) -> list[dict]:
    """Every object the repo's two reach files declare, as dicts."""
    import yaml

    docs = []
    for path in files:
        docs += [d for d in yaml.safe_load_all(path.read_text()) if d]
    return docs


def object_key(doc: dict) -> tuple[str, str, str]:
    """(kind, namespace, name): the identity of an object across repo and cluster."""
    return doc["kind"], doc["metadata"].get("namespace", ""), doc["metadata"]["name"]


def expected_objects(docs: list[dict] | None = None) -> set[tuple[str, str, str]]:
    return {object_key(d) for d in (docs if docs is not None else repo_reach_documents())}


def live_objects_query(kinds=None) -> list[str]:
    """The `kubectl get` resource list that returns every reach object."""
    return sorted({_RESOURCE_FOR_KIND[k] for k in (kinds or _RESOURCE_FOR_KIND)})


def missing_objects(expected: set, live_list_json: str) -> list[tuple[str, str, str]]:
    """Expected objects absent from a `kubectl get ... -o json` List."""
    items = json.loads(live_list_json).get("items", [])
    live = {object_key(i) for i in items}
    return sorted(expected - live)


def identities_authorized_for(target_kind: str, target_name: str, docs: list[dict],
                              namespace: str | None = None) -> set[str]:
    """Mesh identities that policies targeting a Server/HTTPRoute require.

    Works on the repo's documents and on a live `-o json` item list alike (same
    shape), so the e2e files ask the cluster what it enforces and the unit test
    asks the repo what it declares, with one function.
    """
    auths = {
        object_key(d): d for d in docs if d.get("kind") == "MeshTLSAuthentication"
    }
    found: set[str] = set()
    for d in docs:
        if d.get("kind") != "AuthorizationPolicy":
            continue
        ref = d["spec"].get("targetRef", {})
        if ref.get("kind") != target_kind or ref.get("name") != target_name:
            continue
        if namespace and d["metadata"].get("namespace") != namespace:
            continue
        for r in d["spec"].get("requiredAuthenticationRefs", []):
            if r.get("kind") != "MeshTLSAuthentication":
                continue
            key = ("MeshTLSAuthentication", d["metadata"].get("namespace", ""), r["name"])
            if key in auths:
                found |= set(auths[key]["spec"].get("identities", []))
    return found


def egress_drift(expected_doc: dict, live_doc: dict) -> list[str]:
    """Differences between a repo NetworkPolicy and the live one that matter:
    the pod it selects and the egress rules it allows. (policyTypes defaults and
    status are ignored.)"""
    problems = []
    for key in ("podSelector", "egress"):
        if expected_doc["spec"].get(key) != live_doc["spec"].get(key):
            problems.append(key)
    return problems


# --- throwaway pods ---------------------------------------------------------------


def throwaway_manifests(name: str, namespace: str, image: str, labels: dict,
                        meshed: bool, service_account: str | None = None,
                        never_ready: bool = False) -> list[dict]:
    """A sleeping pod (and its ServiceAccount) for the tests that need a caller
    the cluster does not already run: an unmeshed pod (`linkerd.io/inject:
    disabled`, so no proxy and no mesh identity), or a meshed one with an
    identity of its own that no policy names. Created and deleted by the test.

    `never_ready` gives the container a readiness probe that always fails, for a
    pod that carries a Service-selected label (`app=claims-agent`): it must
    never become an endpoint of the real Service and take a share of its
    traffic. Such a pod is waited on for `Running`, not `Ready`.
    """
    pod: dict = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": dict(labels),
            "annotations": {"linkerd.io/inject": "enabled" if meshed else "disabled"},
        },
        "spec": {
            "restartPolicy": "Never",
            "terminationGracePeriodSeconds": 0,
            "containers": [{
                "name": "probe",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "command": ["python3", "-c", "import time; time.sleep(3600)"],
                **({"readinessProbe": {"exec": {"command": ["false"]}, "periodSeconds": 5}}
                   if never_ready else {}),
            }],
        },
    }
    docs = []
    if service_account:
        pod["spec"]["serviceAccountName"] = service_account
        docs.append({"apiVersion": "v1", "kind": "ServiceAccount",
                     "metadata": {"name": service_account, "namespace": namespace}})
    docs.append(pod)
    return docs


class throwaway_pod:
    """Context manager: apply the manifests, wait for Ready, delete on exit."""

    def __init__(self, context, namespace, name, image, labels, meshed,
                 service_account=None, never_ready=False):
        self.context, self.namespace, self.name = context, namespace, name
        self.service_account = service_account
        self.never_ready = never_ready
        self.docs = throwaway_manifests(
            name, namespace, image, labels, meshed, service_account, never_ready)

    def __enter__(self):
        payload = json.dumps({"apiVersion": "v1", "kind": "List", "items": self.docs})
        proc = subprocess.run(
            ["kubectl", "--context", self.context, "apply", "-f", "-"],
            input=payload, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise ProbeError(f"could not create throwaway pod {self.name}: {proc.stderr.strip()}")
        condition = ("--for=jsonpath={.status.phase}=Running" if self.never_ready
                     else "--for=condition=Ready")
        wait = kubectl(self.context, "-n", self.namespace, "wait", condition,
                       f"pod/{self.name}", "--timeout=120s", timeout=150)
        if wait.returncode != 0:
            self.__exit__()
            raise ProbeError(f"throwaway pod {self.name} never became Ready: {wait.stderr.strip()}")
        return self

    def __exit__(self, *exc):
        kubectl(self.context, "-n", self.namespace, "delete", "pod", self.name,
                "--ignore-not-found", "--wait=false", "--grace-period=0", "--force")
        if self.service_account:
            kubectl(self.context, "-n", self.namespace, "delete", "serviceaccount",
                    self.service_account, "--ignore-not-found")
        return False


# --- fixtures' shared gate: stage and the reach objects ---------------------------


def require_reach(context: str):
    """Skip below stage 2; FAIL on a stage >= 2 cluster without Linkerd or the
    reach objects. A skip is invisible in a green gate, so every way of being
    stage 2 and broken is a failure.

    Stage 2 is detected by the Policy Decision Service Deployment, which stage 2
    installs and stage 1 does not. kubectl failures other than NotFound (an
    unreachable API server, a wrong context) are failures too, never skips.
    """
    import pytest

    from tests.harness.kube import absent_or_fail

    pds = kubectl(context, "-n", "northstar-platform", "get", "deployment",
                  "policy-decision-service", "-o", "name")
    if not absent_or_fail(pds, "deployment/policy-decision-service"):
        pytest.skip("below stage 2 (no policy-decision-service); run ./scripts/setup-kind.sh --stage 7")
    ns = kubectl(context, "get", "namespace", "linkerd", "-o", "name")
    assert absent_or_fail(ns, "namespace/linkerd"), (
        "this is a stage >= 2 cluster and the `linkerd` namespace is missing, so "
        "nothing enforces ingress by identity and every reach test here would "
        "prove nothing. Run ./scripts/setup-kind.sh --stage 7 (it creates the "
        "cluster with kindnet and installs Linkerd; a cluster that still runs "
        "Cilium must be deleted and recreated)")
    crd = kubectl(context, "get", "crd", "servers.policy.linkerd.io", "-o", "name")
    assert absent_or_fail(crd, "crd/servers.policy.linkerd.io"), (
        "the Linkerd policy CRDs (servers.policy.linkerd.io) are not installed")
    listing = kubectl(context, "get", ",".join(live_objects_query()), "-A", "-o", "json")
    assert listing.returncode == 0, (
        f"could not list the reach objects: {listing.stderr.strip()}")
    missing = missing_objects(expected_objects(), listing.stdout)
    assert not missing, (
        "the reach objects are NOT all applied, so the refusals below are not "
        f"enforced. Missing: {missing}. Re-apply deploy/platform/reach/ "
        "(./scripts/setup-kind.sh --stage 7)")
    return json.loads(listing.stdout)["items"]
