"""Exit criterion 7: the model provider is reachable from two workloads, not a namespace.

The criterion, verbatim: *"Direct egress from `northstar-agents` to the model
provider is **denied**, with the gateway as the only path."* (Enforced by
core NetworkPolicy, in the node.)

**Scope.** Three pods live in `northstar-agents` (settlement-agent,
claims-agent, customer-ui) plus reviewer-ui. Scoping a policy to
`settlement-agent` alone would satisfy a test named for the criterion while
leaving the others as unrestricted outbound egress points, so every workload in
the namespace carries an egress NetworkPolicy and this file asserts them.

**The honest reading of "the gateway as the only path".** `settlement-agent`
*must* reach `api.anthropic.com:443`: its own model call goes there, and
agentgateway is an MCP proxy that does not carry model traffic. `claims-agent` must too: it calls a model to read a claim the
customer wrote as free text. So the criterion is discharged as two testable
statements:

1. **Two workloads, not the namespace.** Only `settlement-agent` and
   `claims-agent` are admitted to the model provider, and only to Anthropic's published ranges
   (160.79.104.0/23 and 2607:6bc0::/48, TCP 443). NetworkPolicy has no names, so an address range is
   the honest equivalent and a name that resolves elsewhere is dropped.
2. **The gateway really is the only path to a tool**, from the egress side:
   `payments-mcp:8000`, `policy-decision-service:9100` and
   `payments-api:8080` appear in no agent's allowlist but the ones
   that need them.

**The oracle.** From a *meshed* pod a TCP connect to
ANY address "succeeds": the pod's own outbound proxy accepts it locally. So
nothing in this file asserts on a connect from a meshed pod. A meshed pod's
egress is read above TCP: a TLS handshake plus an HTTP request. Allowed = a
status line comes back; refused = reset or timeout (or the pod's own proxy
answering 504 with `l5d-proxy-error` when it cannot reach an in-cluster peer).
From an *unmeshed* pod nothing intercepts the connect, so the kernel's drop
shows as a connect timeout, and that is still the oracle there. The bypass
tests below use exactly such a pod, to prove the node enforces egress and not
the proxy. The decisions are pure functions in `tests/harness/reach.py`, unit
tested against recorded probe output.

**Controls, because a blanket outage would otherwise read as a success.** Each
denial is paired: both model agents must still reach the model provider;
`settlement-agent` must still reach the gateway and the collector; `claims-agent` must still reach its peer and the
collector; DNS must resolve in every pod; every pod must be Ready. Nothing here
is a `kubectl get networkpolicy` assertion on its own: a live-object comparison
(`test_the_live_egress_rules_are_the_ones_in_the_repo`) is there for drift, but
the verdicts are behaviour.

**Not behind `RUN_BROWSER_TESTS=1`**: it spends no model tokens. **A skip is
invisible in a green gate**: the fixture skips only on a cluster without the platform layer and FAILS on
one that has it but lacks Linkerd or the reach objects.
"""

import json

import pytest

from tests.harness import reach

KUBE_CONTEXT = "kind-northstar"

AGENT_NS = "northstar-agents"

# The agent whose tool calls go through the gateway.
MODEL_AGENT = "settlement-agent"
# Every workload here that calls the model provider: settlement-agent for the
# settlement, claims-agent to read a free-text claim.
MODEL_AGENTS = ("settlement-agent", "claims-agent")
OTHER_AGENTS = ("customer-ui",)
ALL_AGENTS = (*MODEL_AGENTS, *OTHER_AGENTS, "reviewer-ui")

# The one external service any workload here may reach: the SDK default
# (`base_url = "https://api.anthropic.com"`); no override exists in the repo.
MODEL_HOST = "api.anthropic.com"
MODEL_PORT = 443

# Not the model provider. Two, one of them another provider's API, so the
# assertion is "this is an allowlist" rather than "anthropic is special-cased".
FOREIGN_EXTERNAL = (("example.com", 443), ("api.openai.com", 443))

DENIED_TOOLS = (
    ("payments-mcp.northstar-tools.svc.cluster.local", 8000, "POST", "/mcp"),
    ("policy-decision-service.northstar-platform.svc.cluster.local", 9100, "POST", "/"),
    ("payments-api.northstar-systems.svc.cluster.local", 8080, "GET", "/healthz"),
)

GATEWAY_HOST = "agentgateway.northstar-platform.svc.cluster.local"
GATEWAY_PORT = 3000
COLLECTOR_HOST = "otel-collector.observability.svc.cluster.local"
COLLECTOR_PORT = 4318
PEER_HOST = "settlement-agent.northstar-agents.svc.cluster.local"
PEER_PORT = 9002
PUSH_HOST = "customer-ui.northstar-agents.svc.cluster.local"
PUSH_PORT = 8080

# Long enough that a slow-but-allowed connection is not mistaken for a drop,
# short enough that a few dozen probes do not dominate the suite.
PROBE_TIMEOUT = 8

WRONG_ID_SA = "reach-probe-wrongid"

# Carried by the throwaway pod that borrows a real workload's `app` label (the
# bypass pod), and by nothing else, so `_pod()` never selects it by accident.
THROWAWAY_LABEL = "reach-throwaway"

RESOLVE = r"""
import json, socket, sys
out = {}
for name in sys.argv[1:]:
    try:
        out[name] = socket.gethostbyname(name)
    except Exception as exc:
        out[name] = "FAILED:" + type(exc).__name__
print(json.dumps(out))
"""


def _pod(app: str, namespace: str = AGENT_NS) -> str:
    proc = reach.kubectl(
        KUBE_CONTEXT, "-n", namespace, "get", "pod", "-l", f"app={app},!{THROWAWAY_LABEL}",
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


def _http(app: str, host: str, port: int, method: str = "GET", path: str = "/",
          tls: bool = False) -> dict:
    """An HTTP(S) request from inside `app`'s pod, through its own proxy."""
    return reach.exec_probe(
        KUBE_CONTEXT, AGENT_NS, _pod(app), app, host, port, method, path,
        PROBE_TIMEOUT, tls)


@pytest.fixture(scope="module")
def reach_objects():
    """Stage >= 2 with Linkerd and every reach object, or FAIL (never skip)."""
    return reach.require_reach(KUBE_CONTEXT)


@pytest.fixture(scope="module")
def bypass_pod(reach_objects):
    """An UNMESHED pod labelled `app=claims-agent`, created and deleted here.

    No `linkerd-proxy`, so nothing in the pod intercepts a connect: what stops
    it is the node, enforcing the NetworkPolicy that selects `app=claims-agent`.
    It runs the settlement-agent image only for `python3` (its command is
    replaced by a sleep), so it carries none of claims-agent's credentials.
    """
    with reach.throwaway_pod(
        KUBE_CONTEXT, AGENT_NS, "reach-bypass-claims", _image("settlement-agent"),
        {"app": "claims-agent", THROWAWAY_LABEL: "true"}, meshed=False,
        # Labelled like claims-agent, so it must never join the claims-agent
        # Service's endpoints and take a share of real traffic.
        never_ready=True,
    ) as pod:
        yield pod.name


@pytest.fixture(scope="module")
def wrong_identity_pod(reach_objects):
    """A meshed pod with a service account no policy names, selected by no NetworkPolicy."""
    with reach.throwaway_pod(
        KUBE_CONTEXT, AGENT_NS, "reach-probe-wrongid-egress", _image("settlement-agent"),
        {"app": "reach-probe"}, meshed=True, service_account=WRONG_ID_SA,
    ) as pod:
        yield pod.name


# --- The criterion's own sentence -------------------------------------------


@pytest.mark.parametrize("app", OTHER_AGENTS)
def test_an_agent_with_no_model_call_cannot_reach_the_model_provider(app, reach_objects):
    """§9.7: direct egress from `northstar-agents` to the model provider.

    Written against the workload that makes no model call. It holds no `ANTHROPIC_API_KEY`,
    so it loses nothing. A meshed pod's connect succeeds
    regardless, so the request is a TLS handshake plus an HTTP request and
    the verdict is whether a status line came back.
    """
    result = _http(app, MODEL_HOST, MODEL_PORT, "HEAD", "/", tls=True)
    verdict, how = reach.classify_egress(result)
    assert verdict == "dropped", (
        f"{app} completed a TLS handshake and an HTTP request to "
        f"{MODEL_HOST}:{MODEL_PORT} ({result}), so direct egress from "
        "northstar-agents to the model provider is not denied and criterion 7 "
        "is false for this pod"
    )


@pytest.mark.parametrize("app", MODEL_AGENTS)
@pytest.mark.parametrize("host,port", FOREIGN_EXTERNAL)
def test_a_model_agent_reaches_no_other_external_host(app, host, port, reach_objects):
    """The allowance is one address range, not "external is fine".

    `api.openai.com` is in the list on purpose: if the rule had been written as
    a blanket 443 allowance, a second model provider is exactly what would slip
    through while `example.com` still looked blocked.
    """
    result = _http(app, host, port, "HEAD", "/", tls=True)
    assert reach.egress_is_dropped(result), (
        f"{app} reached {host}:{port} ({result}), so its egress "
        f"allowance is wider than Anthropic's published range for {MODEL_HOST}"
    )


@pytest.mark.parametrize("host,port,method,path", DENIED_TOOLS)
def test_the_gateway_is_the_only_tool_destination_from_the_model_agent(
    host, port, method, path, reach_objects
):
    """The criterion's second clause, from the egress side.

    `settlement-agent` has no allowance to a tool at all. Any HTTP answer here,
    including a Linkerd 403, would mean the packets got to the tool's proxy, so
    the egress policy no longer stops it and only the ingress half is left: a
    403 FAILS this test on purpose. Only a failed request (the pod's own proxy
    answering 504 with `l5d-proxy-error`, a reset, a timeout) passes.
    """
    result = _http(MODEL_AGENT, host, port, method, path)
    assert reach.egress_is_dropped(result), (
        f"{MODEL_AGENT} got an answer from {host}:{port} ({result}), bypassing "
        f"{GATEWAY_HOST}:{GATEWAY_PORT}; the gateway is not the only path out "
        "of the agent to a tool"
    )


# --- The bypass: the node enforces egress, not the proxy ---------------------


@pytest.mark.parametrize(
    "host,why",
    [
        ("example.com", "an arbitrary external host"),
        ("1.1.1.1", "an external address outside Anthropic's range"),
    ],
)
def test_a_pod_that_skips_the_proxy_is_still_dropped_by_the_node(
    host, why, bypass_pod
):
    """`linkerd.io/inject: disabled` is not a way out of the egress policy.

    The pod is labelled `app=claims-agent` and has no proxy, so a connect is
    answered by the kernel or not at all. A timeout is the oracle here (and
    only here: from a meshed pod it would prove nothing). Control below: the
    same pod CAN connect to the one peer claims-agent is allowed.
    """
    result = reach.exec_probe(
        KUBE_CONTEXT, AGENT_NS, bypass_pod, "probe", host, 443,
        timeout=PROBE_TIMEOUT, mode="tcp")
    assert reach.classify_tcp(result) == "timeout", (
        f"an unmeshed pod labelled app=claims-agent connected to {host}:443 "
        f"({why}): got {reach.classify_tcp(result)} ({result}). The node is not "
        "enforcing egress; only the proxy was, and a pod can skip the proxy"
    )


def test_the_bypass_pod_is_dropped_on_the_tool_too_and_the_control_connects(bypass_pod):
    """Direct to payments-mcp: timeout. Control: the allowed peer connects.

    The control matters: without it a bypass pod with no network at all passes.
    DNS must also work from it (the policy allows kube-dns on 53), or the
    external-host timeout above would be a resolution failure, not a drop.
    """
    denied = reach.exec_probe(
        KUBE_CONTEXT, AGENT_NS, bypass_pod, "probe",
        "payments-mcp.northstar-tools.svc.cluster.local", 8000,
        timeout=PROBE_TIMEOUT, mode="tcp")
    assert reach.classify_tcp(denied) == "timeout", (
        f"the unmeshed claims-agent-labelled pod connected to payments-mcp:8000: {denied}"
    )
    control = reach.exec_probe(
        KUBE_CONTEXT, AGENT_NS, bypass_pod, "probe", PEER_HOST, PEER_PORT,
        timeout=PROBE_TIMEOUT, mode="tcp")
    assert reach.classify_tcp(control) == "connected", (
        f"the bypass pod cannot connect to its allowed peer {PEER_HOST}:{PEER_PORT} "
        f"either ({control}); the drops above prove an outage, not a policy"
    )
    proc = reach.kubectl(
        KUBE_CONTEXT, "-n", AGENT_NS, "exec", bypass_pod, "-c", "probe", "--",
        "python3", "-c", RESOLVE, "example.com", timeout=60)
    assert json.loads(proc.stdout.strip().splitlines()[-1])["example.com"][:7] != "FAILED:", proc.stdout


# --- Controls: the denials above must not be an outage ----------------------


@pytest.mark.parametrize("app", MODEL_AGENTS)
def test_a_model_agent_still_reaches_the_model_provider(app, reach_objects):
    """Control, and the one that makes the whole file mean something.

    Without it every denial above is satisfied by an agent plane with no
    network at all. A TLS handshake and an HTTP status line from
    `api.anthropic.com` (any status: an unauthenticated HEAD on `/` is a 4xx)
    prove the address range, DNS and the proxy are all working together.
    """
    result = _http(app, MODEL_HOST, MODEL_PORT, "HEAD", "/", tls=True)
    assert reach.classify_egress(result) == ("reached", "answered"), (
        f"{app} cannot complete a request to {MODEL_HOST}:{MODEL_PORT} "
        f"({result}), so every model call from it will fail. If "
        "Anthropic's published ranges moved, the ipBlock in "
        "deploy/platform/reach/network-policies.yaml is stale"
    )


@pytest.mark.parametrize(
    "host,port",
    [(GATEWAY_HOST, GATEWAY_PORT), (COLLECTOR_HOST, COLLECTOR_PORT)],
)
def test_the_model_agent_still_reaches_the_gateway_and_the_collector(
    host, port, reach_objects
):
    """The two in-cluster destinations a settlement cannot happen without.

    Both are meshed, so the allowance is the service port AND the peer proxy's
    4143. Any HTTP answer counts, a 404 or 405
    included: the question is whether something answered.
    """
    result = _http(MODEL_AGENT, host, port, "GET", "/")
    assert reach.classify_egress(result) == ("reached", "answered"), (
        f"{MODEL_AGENT} cannot reach {host}:{port} ({result}) -- the egress "
        "allowlist is denying a destination the demo depends on"
    )


@pytest.mark.parametrize(
    "host,port,method,path",
    [(PEER_HOST, PEER_PORT, "POST", "/"),
     (COLLECTOR_HOST, COLLECTOR_PORT, "GET", "/")],
)
def test_claims_agent_still_reaches_its_peer_and_the_collector(
    host, port, method, path, reach_objects
):
    """Control for the claims-agent denials, on the two things it actually does.

    `NORTHSTAR_ALLOWED_AGENTS` names `settlement-agent:9002` as its single
    delegation target; losing it would break every claim.

    The peer is probed with `POST /`, never the agent card: Linkerd's default
    probe route opens `GET /.well-known/agent-card.json` to every caller,
    so it would pass for any identity. A POST needs claims-agent's identity;
    any app answer (an A2A JSON-RPC error body included) is "reached".
    """
    result = _http("claims-agent", host, port, method, path)
    assert reach.classify_egress(result)[0] == "reached", (
        f"claims-agent cannot reach {host}:{port} ({result}), so the "
        "model-provider denial above proves an outage rather than a policy"
    )


def test_the_collector_is_reachable_from_every_agent_that_exports(reach_objects):
    """Its own test because its failure is the one that looks like success.

    The OTLP exporter runs on a BatchSpanProcessor background thread and
    swallows export failures. Drop the collector rule and settlements keep
    succeeding while every span from that service silently disappears.
    """
    unreachable = [
        app for app in ALL_AGENTS
        if not reach.classify_egress(
            _http(app, COLLECTOR_HOST, COLLECTOR_PORT, "GET", "/")
        )[0] == "reached"
    ]
    assert not unreachable, (
        f"{unreachable} cannot reach {COLLECTOR_HOST}:{COLLECTOR_PORT}; their "
        "spans are being dropped on the floor while the demo appears to work"
    )


@pytest.mark.parametrize("app", ALL_AGENTS)
def test_dns_still_resolves_inside_every_agent_pod(app, reach_objects):
    """DNS is a control: the policy allows kube-dns on 53 UDP and TCP, and an
    egress policy that forgot it would break every lookup, including the
    in-cluster ones (`ClusterFirst` search domains emit several queries per
    name)."""
    names = [MODEL_HOST, GATEWAY_HOST, COLLECTOR_HOST]
    proc = reach.kubectl(
        KUBE_CONTEXT, "-n", AGENT_NS, "exec", _pod(app), "-c", app, "--",
        "python3", "-c", RESOLVE, *names, timeout=90)
    assert proc.stdout.strip(), (
        f"could not exec the resolver in {app} (exit {proc.returncode}): {proc.stderr.strip()}")
    resolved = json.loads(proc.stdout.strip().splitlines()[-1])
    failed = {n: v for n, v in resolved.items() if v.startswith("FAILED:")}
    assert not failed, (
        f"{app} could not resolve {sorted(failed)} -- the DNS egress rule is "
        f"wrong. Full result: {resolved}"
    )


@pytest.mark.parametrize("app", ALL_AGENTS)
def test_every_agent_pod_is_still_ready(app, reach_objects):
    """Nothing was severed, stated the way an operator would notice it.

    A pod that cannot reach the Linkerd control plane (8080, 8086, 8090) cannot
    get its identity and never becomes Ready, with nothing in the output
    mentioning networking.
    """
    proc = reach.kubectl(
        KUBE_CONTEXT, "-n", AGENT_NS, "get", "pod", "-l", f"app={app}",
        "-o", "jsonpath={.items[*].status.conditions[?(@.type=='Ready')].status}")
    assert "True" in proc.stdout, (
        f"{app} is not Ready under the egress policy (got {proc.stdout!r})")


# --- the customer's push receiver --------------------------------------


def test_claims_agent_reaches_the_push_receiver_and_the_server_answers(reach_objects):
    """POST /a2a/push with no token is answered by the SERVER (401), not refused.

    A 401 can only come from customer-ui's own handler, so the push route's
    AuthorizationPolicy admitted claims-agent's identity and the egress rule
    admitted the connection (service port 8080 and the proxy's 4143).
    """
    result = _http("claims-agent", PUSH_HOST, PUSH_PORT, "POST", "/a2a/push")
    assert result["status"] == 401, (
        "claims-agent's POST to customer-ui:8080/a2a/push (no token) was not "
        f"answered 401 by the server: {result}. A 403 means the push route's "
        "policy does not admit claims-agent; a proxy error means the egress "
        "rule is wrong. Either way the push would never be delivered"
    )


def test_an_identity_that_is_not_claims_agent_cannot_push(wrong_identity_pod):
    """Only claims-agent's identity may POST /a2a/push: the wrong one is a counted 403.

    reviewer-ui is stopped earlier, by its own egress allowlist (asserted
    next), so the wrong caller here is the throwaway meshed pod. The refusal names the route `customer-ui-push`.
    """
    cid = reach.client_id(WRONG_ID_SA, AGENT_NS)
    dst = _pod("customer-ui")
    before = reach.read_deny_counters(KUBE_CONTEXT, AGENT_NS, dst)
    result = reach.exec_probe(
        KUBE_CONTEXT, AGENT_NS, wrong_identity_pod, "probe",
        PUSH_HOST, PUSH_PORT, "POST", "/a2a/push", PROBE_TIMEOUT)
    after = reach.read_deny_counters(KUBE_CONTEXT, AGENT_NS, dst)
    assert reach.classify_ingress(result) == "refused-403", (
        f"the wrong identity's forged push was not refused 403: {result}")
    by = reach.refused_by(before, after, srv_name="customer-ui", client_id=cid)
    assert by is not None and by[:2] == ("customer-ui", "customer-ui-push"), (
        f"the deny counter did not name Server customer-ui / route "
        f"customer-ui-push for client_id={cid}: {by}")


def test_reviewer_ui_has_no_egress_to_the_push_receiver(reach_objects):
    """reviewer-ui may not reach customer-ui at all (egress), so it cannot push."""
    result = _http("reviewer-ui", PUSH_HOST, PUSH_PORT, "POST", "/a2a/push")
    assert reach.egress_is_dropped(result), (
        f"reviewer-ui got an answer from customer-ui:8080 ({result}); its "
        "egress allowlist is wider than payments-api, claims-agent, the "
        "gateway and the collector")


def test_claims_agent_may_use_the_rest_of_customer_ui_and_that_is_documented(reach_objects):
    """Wider than Cilium's POST-only rule, on purpose and stated.

    Under Cilium claims-agent's egress allowed `POST /a2a/push` only. Linkerd
    restricts the PUSH path by caller; every other path is open to every caller
    (the catch-all route in linkerd-policies.yaml is what keeps the browser and
    the readiness probe working). So `GET /` from claims-agent is answered, not
    refused. This pins the documented widening: if it flips to a refusal, the
    catch-all went away and the page and its probe need re-checking.
    """
    result = _http("claims-agent", PUSH_HOST, PUSH_PORT, "GET", "/")
    assert reach.classify_ingress(result) == "answered", (
        f"claims-agent's GET / on customer-ui was not simply answered: {result}")


def _customer_ui_pod_ip() -> str:
    proc = reach.kubectl(
        KUBE_CONTEXT, "-n", AGENT_NS, "get", "pod", "-l", "app=customer-ui",
        "-o", "jsonpath={.items[0].status.podIP}")
    pod_ip = proc.stdout.strip()
    assert pod_ip, f"could not read the customer-ui pod IP: {proc.stderr.strip()}"
    return pod_ip


def test_an_unmeshed_claims_agent_cannot_reach_another_customer_ui_port(bypass_pod):
    """The NetworkPolicy port rule genuinely applies to UNMESHED traffic.

    The pod IP is probed, not the Service (which exposes only 8080). The bypass
    pod has no proxy, so the kernel sees the real destination port: a connect to
    customer-ui's pod IP on 9090 is outside the rule (8080 and 4143) and times
    out. Control: the same pod connects to the allowed port 8080. This is the
    only place the egress port rule can be shown to bind; for a MESHED source it
    does not (next test).
    """
    pod_ip = _customer_ui_pod_ip()
    denied = reach.exec_probe(
        KUBE_CONTEXT, AGENT_NS, bypass_pod, "probe", pod_ip, 9090,
        timeout=PROBE_TIMEOUT, mode="tcp")
    assert reach.classify_tcp(denied) == "timeout", (
        f"an unmeshed claims-agent-labelled pod connected to customer-ui pod "
        f"{pod_ip}:9090 ({denied}): the egress port rule does not bind")
    control = reach.exec_probe(
        KUBE_CONTEXT, AGENT_NS, bypass_pod, "probe", pod_ip, PUSH_PORT,
        timeout=PROBE_TIMEOUT, mode="tcp")
    assert reach.classify_tcp(control) == "connected", (
        f"the bypass pod cannot connect to customer-ui {pod_ip}:{PUSH_PORT} either "
        f"({control}); the drop above proves an outage, not a port rule")


def test_a_meshed_claims_agent_reaches_every_port_of_customer_ui_documented_limitation(
    reach_objects,
):
    """DOCUMENTS A LIMITATION, it does not bless it.

    Between meshed pods, egress is decided per pod, not per port: claims-agent
    is allowed to reach customer-ui's proxy (4143), and the client proxy tunnels
    through 4143 to ANY port the peer pod listens on. Port 9090 has nothing
    listening, so what comes back is the peer's inbound proxy answering 502 with
    an empty body and no `l5d-proxy-error`: the pod IS reachable on a port the
    rule never listed. Which ports actually answer is decided by the peer's
    Linkerd Servers. If this flips to a drop, the egress model changed (a CNI
    that sees through the mesh, or the rule lost 4143) and the docs that state
    this limitation need rewriting.
    """
    result = _http("claims-agent", _customer_ui_pod_ip(), 9090)
    assert reach.classify_egress(result) == ("reached", "peer-proxy-502"), (
        f"claims-agent's request to customer-ui pod:9090 was not the peer proxy's "
        f"502 ({result}); M7 measured that a meshed source reaches every port "
        "of a peer it may tunnel to")


# --- The objects, not just the behaviour ----------------------------------------


def test_the_live_egress_rules_are_the_ones_in_the_repo(reach_objects):
    """Drift check: every NetworkPolicy in the repo is live with the same selector and rules.

    Behaviour is asserted above; this catches the policy someone edited in the
    cluster to make a probe pass (or an `allow` added next to the ones tested).
    """
    live = {reach.object_key(i): i for i in reach_objects}
    problems = {}
    for doc in reach.repo_reach_documents():
        if doc["kind"] != "NetworkPolicy":
            continue
        key = reach.object_key(doc)
        drift = reach.egress_drift(doc, live[key])
        if drift:
            problems[key[2]] = drift
    assert not problems, f"live NetworkPolicies differ from the repo: {problems}"
