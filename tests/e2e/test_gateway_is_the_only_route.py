"""Proves the choke point is enforced by the mesh, not merely observed.

**The gap this closes.** `test_gateway_trace.py` asserts that every
`payments-mcp` span *in an observed trace* is parented to an agentgateway span.
That is a property of the traffic that happened. It says nothing about whether
other traffic is possible, and it passes perfectly on a cluster where any pod
can reach the tool directly.

Measured before any reach policy existed: `claims-agent`, holding no SVID and
with no reason to touch payments, POSTed an MCP `initialize` straight at
`payments-mcp:8000/mcp` and got **HTTP 200**. A choke point a workload can walk
around is a convention, not a control.

**What enforces it.** Ingress is Linkerd's, at the destination's
proxy: `payments-mcp:8000` and `policy-decision-service:9100` each have a
`Server` (`accessPolicy: deny`) and one `AuthorizationPolicy` naming
agentgateway's mesh identity
(`deploy/platform/reach/linkerd-policies.yaml`). Egress is core NetworkPolicy,
enforced by kindnet (`deploy/platform/reach/network-policies.yaml`). Both file
halves are asserted here; the egress half lives in `test_egress_policy.py`.

**The oracle.** An ingress refusal is an
HTTP 403 (or a proxy 404 when no route matched), an empty body, no
`l5d-proxy-error` header, AND the destination proxy's
`inbound_http_authz_deny_total` for that Server and caller rising by at least
one across the call. The counter is what says Linkerd's policy refused it
rather than an application. It is read through the API server pod proxy, never
through `kubectl port-forward`, which bypasses the proxy. **A TCP connect
is never the oracle for ingress**: the proxy accepts every connection and
answers above TCP. All decisions are pure functions in `tests/harness/reach.py`
with their own unit tests; this file only runs probes and applies them.

**Which callers.** A refusal can only be observed from a caller whose egress
reaches the destination, and every agent-plane workload's egress is
allowlisted (so `claims-agent` is stopped one layer earlier, see below). The
tests therefore create two throwaway pods, deleted afterwards, in
`northstar-agents`, labelled so that no NetworkPolicy selects them: one
UNMESHED (`linkerd.io/inject: disabled`: no proxy, no identity) and one MESHED
with a ServiceAccount of its own that no policy names (the wrong identity).
`claims-agent` itself is the prober for the egress half and for the control.

**Not behind `RUN_BROWSER_TESTS=1`**: it spends no model tokens.

**A skip is invisible in a green gate.** The fixture skips only on a cluster without the platform layer
(no policy-decision-service). On one that has it but lacks Linkerd or
the reach objects it FAILS, so the one event these tests exist to catch
(the policy silently no longer applied) cannot leave the suite green.
"""

import json

import pytest

from tests.harness import reach

KUBE_CONTEXT = "kind-northstar"
AGENT_NS = "northstar-agents"

PROTECTED_HOST = "payments-mcp.northstar-tools.svc.cluster.local"
PROTECTED_PORT = 8000
PDS_HOST = "policy-decision-service.northstar-platform.svc.cluster.local"
PDS_PORT = 9100
GATEWAY_IDENTITY = reach.client_id("agentgateway", "northstar-platform")

# Reachable from claims-agent and deliberately NOT the thing under test: the
# control, so a refusal below cannot be an outage. `settlement-agent:9002` is
# what claims-agent is *for* (NORTHSTAR_ALLOWED_AGENTS names it as its single
# delegation target) and it is authorized for claims-agent's identity.
# The control is `POST /`, never `GET /.well-known/agent-card.json`: Linkerd's
# default probe route opens that path to EVERY caller of a route-less Server
# so a GET there would pass for any identity and prove nothing.
CONTROL_HOST = "settlement-agent.northstar-agents.svc.cluster.local"
CONTROL_PORT = 9002
CONTROL_METHOD, CONTROL_PATH = "POST", "/"

PROBE_TIMEOUT = 8

WRONG_ID_SA = "reach-probe-wrongid"


def _pod(namespace: str, app: str) -> str:
    proc = reach.kubectl(
        KUBE_CONTEXT, "-n", namespace, "get", "pod", "-l", f"app={app},!reach-throwaway",
        "-o", "jsonpath={.items[0].metadata.name}",
    )
    assert proc.returncode == 0 and proc.stdout.strip(), (
        f"no {app} pod in {namespace} on a stage >= 2 cluster "
        f"({proc.stderr.strip() or 'no output'})"
    )
    return proc.stdout.strip()


def _image(deployment: str) -> str:
    proc = reach.kubectl(
        KUBE_CONTEXT, "-n", AGENT_NS, "get", "deployment", deployment,
        "-o", "jsonpath={.spec.template.spec.containers[0].image}",
    )
    assert proc.returncode == 0 and proc.stdout.strip(), proc.stderr
    return proc.stdout.strip()


@pytest.fixture(scope="module")
def reach_objects():
    """Stage >= 2 with Linkerd and every reach object, or FAIL (never skip)."""
    return reach.require_reach(KUBE_CONTEXT)


@pytest.fixture(scope="module")
def unmeshed_pod(reach_objects):
    """A pod with no proxy and no mesh identity, selected by no NetworkPolicy."""
    with reach.throwaway_pod(
        KUBE_CONTEXT, AGENT_NS, "reach-probe-unmeshed", _image("settlement-agent"),
        {"app": "reach-probe"}, meshed=False,
    ) as pod:
        yield pod.name


@pytest.fixture(scope="module")
def wrong_identity_pod(reach_objects):
    """A meshed pod whose identity (`reach-probe-wrongid.northstar-agents`) no policy names."""
    with reach.throwaway_pod(
        KUBE_CONTEXT, AGENT_NS, "reach-probe-wrongid", _image("settlement-agent"),
        {"app": "reach-probe"}, meshed=True, service_account=WRONG_ID_SA,
    ) as pod:
        yield pod.name


def _refusal(pod: str, host: str, port: int, method: str, path: str,
             dst_ns: str, dst_app: str, **counter_filter):
    """Probe from `pod` and return (classification, result, delta, refused_by).

    The delta is read across the call on the destination proxy's metrics, via
    the API server (never a port-forward).
    """
    dst_pod = _pod(dst_ns, dst_app)
    before = reach.read_deny_counters(KUBE_CONTEXT, dst_ns, dst_pod)
    result = reach.exec_probe(
        KUBE_CONTEXT, AGENT_NS, pod, "probe", host, port, method, path, PROBE_TIMEOUT)
    after = reach.read_deny_counters(KUBE_CONTEXT, dst_ns, dst_pod)
    return (
        reach.classify_ingress(result), result,
        reach.deny_delta(before, after, **counter_filter),
        reach.refused_by(before, after, **counter_filter),
    )


def test_an_unmeshed_pod_cannot_reach_the_tool_directly(unmeshed_pod):
    """The bypass that worked before any policy existed must now be refused.

    An unmeshed pod has no mesh identity, so no AuthorizationPolicy can admit
    it: payments-mcp's proxy answers 403 with an empty body and counts it with
    `tls="no_identity"`. The TCP connect succeeds (the proxy accepts it); that
    is exactly why the verdict is read from the HTTP answer and the counter.
    """
    kind, result, delta, by = _refusal(
        unmeshed_pod, PROTECTED_HOST, PROTECTED_PORT, "POST", "/mcp",
        "northstar-tools", "payments-mcp", srv_name="payments-mcp", tls="no_identity",
    )
    assert kind == "refused-403", (
        f"an unmeshed pod's POST to payments-mcp:8000/mcp was not refused 403: "
        f"{kind}, {result}. The gateway is not the only route into payments-mcp"
    )
    assert delta >= 1, (
        "payments-mcp's inbound_http_authz_deny_total{srv_name=payments-mcp, "
        f"tls=no_identity}} did not rise (delta {delta}), so the 403 did not come "
        "from Linkerd's policy"
    )


def test_a_meshed_workload_with_the_wrong_identity_cannot_reach_the_tool_directly(
    wrong_identity_pod,
):
    """Meshed is not enough: the identity must be the gateway's.

    The pod presents a valid mTLS identity (`reach-probe-wrongid`) that no
    policy names. The refusal is counted against exactly that client_id, which
    is also what shows the authorization is by workload identity and not by
    namespace or by "is in the mesh".
    """
    cid = reach.client_id(WRONG_ID_SA, AGENT_NS)
    kind, result, delta, by = _refusal(
        wrong_identity_pod, PROTECTED_HOST, PROTECTED_PORT, "POST", "/mcp",
        "northstar-tools", "payments-mcp", srv_name="payments-mcp", client_id=cid,
    )
    assert kind == "refused-403", (
        f"a meshed pod with the wrong identity was not refused 403: {kind}, {result}"
    )
    assert delta >= 1, (
        f"no deny counter for client_id={cid} on payments-mcp rose ({delta}); "
        "the refusal is not attributable to the identity policy"
    )
    assert by is not None and by[0] == "payments-mcp", by


def test_the_pds_refuses_every_caller_but_the_gateway(wrong_identity_pod):
    """A caller that is not the gateway cannot ask the PDS anything.

    The PDS is fail-closed and the gateway's ExtMCP hook is its only client; a
    workload that could call it could probe the ruleset. gRPC refusal is
    PERMISSION_DENIED (an HTTP 403 at the HTTP/2 layer, or `grpc-status: 7`;
    a client library reports both as the same code). Expected result: `PERMISSION_DENIED`, counter
    `route_name="default"`, `tls="true"`, `client_id` present. If this fails,
    look at the code before the policy.
    """
    cid = reach.client_id(WRONG_ID_SA, AGENT_NS)
    dst_pod = _pod("northstar-platform", "policy-decision-service")
    before = reach.read_deny_counters(KUBE_CONTEXT, "northstar-platform", dst_pod)
    result = reach.exec_grpc_probe(
        KUBE_CONTEXT, AGENT_NS, wrong_identity_pod, "probe",
        f"{PDS_HOST}:{PDS_PORT}", PROBE_TIMEOUT,
    )
    after = reach.read_deny_counters(KUBE_CONTEXT, "northstar-platform", dst_pod)
    assert reach.classify_grpc(result) == "refused", (
        f"the PDS did not answer PERMISSION_DENIED to the wrong identity: {result}"
    )
    delta = reach.deny_delta(
        before, after, srv_name="policy-decision-service", client_id=cid)
    assert delta >= 1, (
        f"the PDS proxy's deny counter for client_id={cid} did not rise ({delta})"
    )


def test_claims_agent_cannot_reach_the_tool_directly_and_the_tool_never_hears_of_it(
    reach_objects,
):
    """claims-agent is stopped at its own egress, before payments-mcp's policy.

    `claims-agent-egress` does not list payments-mcp, so the node drops the
    packets the proxy sends and the request fails in claims-agent's own proxy
    (a 504 with `l5d-proxy-error`, a reset or a timeout). The destination's deny
    counter must NOT move: the request never arrived. That is what attributes
    this block to the egress half; the ingress half is the two tests above.
    """
    dst_pod = _pod("northstar-tools", "payments-mcp")
    before = reach.read_deny_counters(KUBE_CONTEXT, "northstar-tools", dst_pod)
    claims = _pod(AGENT_NS, "claims-agent")
    result = reach.exec_probe(
        KUBE_CONTEXT, AGENT_NS, claims, "claims-agent",
        PROTECTED_HOST, PROTECTED_PORT, "POST", "/mcp", PROBE_TIMEOUT)
    after = reach.read_deny_counters(KUBE_CONTEXT, "northstar-tools", dst_pod)
    verdict, how = reach.classify_egress(result)
    assert verdict == "dropped", (
        f"claims-agent reached payments-mcp:8000 directly ({result}), so the "
        "gateway is not the only route into payments-mcp"
    )
    assert reach.deny_delta(before, after, srv_name="payments-mcp") == 0, (
        "payments-mcp's proxy saw (and refused) claims-agent's request, so the "
        "egress NetworkPolicy no longer stops it: only the ingress half is left"
    )


def test_the_block_is_targeted_and_the_cluster_is_otherwise_healthy(reach_objects):
    """Control: the same pod can still reach a service the policies allow.

    Without this the test above would also pass on a cluster whose networking
    is simply broken, or where `claims-agent` has no egress at all.
    """
    claims = _pod(AGENT_NS, "claims-agent")
    result = reach.exec_probe(
        KUBE_CONTEXT, AGENT_NS, claims, "claims-agent",
        CONTROL_HOST, CONTROL_PORT, CONTROL_METHOD, CONTROL_PATH, PROBE_TIMEOUT)
    assert reach.classify_egress(result)[0] == "reached", (
        f"claims-agent could not reach {CONTROL_HOST}:{CONTROL_PORT} either "
        f"({result}), so the previous test proves an outage rather than a policy"
    )
    # Any app answer counts (an A2A JSON-RPC error body is fine); a proxy 403 or
    # 404 would mean claims-agent's identity is NOT authorized at settlement-agent.
    assert reach.classify_ingress(result) == "answered", (
        f"settlement-agent's proxy refused claims-agent's POST /: {result}")


def test_the_wrong_identity_cannot_post_to_settlement_agent(wrong_identity_pod):
    """The refusal that pairs with the control above, on the same request.

    The control is claims-agent's `POST /`, answered. This is a meshed pod with
    an identity no policy names making the same `POST /`: a counted 403 against
    Server settlement-agent. (Never the agent-card path: the probe route opens
    that to everyone.)
    """
    cid = reach.client_id(WRONG_ID_SA, AGENT_NS)
    kind, result, delta, by = _refusal(
        wrong_identity_pod, CONTROL_HOST, CONTROL_PORT, CONTROL_METHOD, CONTROL_PATH,
        AGENT_NS, "settlement-agent", srv_name="settlement-agent", client_id=cid,
    )
    assert kind == "refused-403", (
        f"the wrong identity's POST / to settlement-agent:9002 was not refused 403: "
        f"{kind}, {result}")
    assert delta >= 1, (
        f"inbound_http_authz_deny_total{{srv_name=settlement-agent}} for client_id={cid} "
        f"did not rise ({delta}); the 403 is not attributable to the identity policy")


@pytest.mark.parametrize(
    "server,namespace",
    [("payments-mcp", "northstar-tools"), ("policy-decision-service", "northstar-platform")],
)
def test_the_cluster_admits_the_gateway_identity_and_nothing_else(
    server, namespace, reach_objects
):
    """Reads what the control plane holds, not what the repo declares.

    The gateway runs a distroless image with no shell, so nothing inside its
    pod can open a socket; rather than leave a permanently-skipping test, this
    asks the live AuthorizationPolicies which identities may reach each Server
    (a stronger question than "did one request get through": it enumerates
    them). Exactly one identity, agentgateway's own service account; the
    `default` account is never one of them.
    """
    allowed = reach.identities_authorized_for(
        "Server", server, reach_objects, namespace=namespace)
    assert allowed == {GATEWAY_IDENTITY}, (
        f"{server} must admit exactly {GATEWAY_IDENTITY}; the cluster admits "
        f"{sorted(allowed)}"
    )


@pytest.mark.parametrize(
    "app,namespace",
    [("payments-mcp", "northstar-tools"), ("policy-decision-service", "northstar-platform")],
)
def test_the_protected_pod_is_ready_and_meshed(app, namespace, reach_objects):
    """Both protected pods are Ready under their Servers, with a proxy.

    Their probes are `tcpSocket`, which the proxy accepts under
    `accessPolicy: deny`. The symptom of getting it wrong is not a network
    error but a pod that never becomes Ready, or a PDS that restarts and fails
    closed so every payment is refused for the wrong reason. A pod without a
    `linkerd-proxy` container is a pod the Server does not protect.
    """
    proc = reach.kubectl(
        KUBE_CONTEXT, "-n", namespace, "get", "pod", "-l", f"app={app}", "-o", "json")
    assert proc.returncode == 0, proc.stderr
    pod = json.loads(proc.stdout)["items"][0]
    ready = [c["status"] for c in pod["status"]["conditions"] if c["type"] == "Ready"]
    assert ready == ["True"], f"{app} is not Ready under its Server: {pod['status']}"
    names = {c["name"] for c in pod["spec"].get("containers", [])} | {
        c["name"] for c in pod["spec"].get("initContainers", [])}
    assert "linkerd-proxy" in names, f"{app} has no linkerd-proxy; its Server protects nothing"
