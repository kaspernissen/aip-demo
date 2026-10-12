"""Proves the choke point is enforced by the dataplane, not merely observed.

**The gap this closes.** `test_gateway_trace.py` has a test called
`test_the_gateway_is_the_only_route_into_payments_mcp`, and its docstring is
scrupulous about what it actually asserts: every `payments-mcp` span *in an
observed trace* is parented to an agentgateway span. That is a property of the
traffic that happened. It says nothing about whether other traffic is
possible, and it passes perfectly on a cluster where any pod can reach the
tool directly.

Measured before `deploy/platform/cilium-policies.yaml` existed: `claims-agent`,
holding no SVID and with no reason to touch payments, POSTed an MCP
`initialize` straight at `payments-mcp:8000/mcp` and got **HTTP 200**. The
claim this demo rests on -- "the platform decides if it becomes an effect" --
therefore held only for a caller that chose to cooperate. A choke point a
workload can walk around is a convention, not a control.

**Why `claims-agent` is the prober.** It is a real workload already in the
cluster, in the same namespace as the agent that *is* allowed through, with no
SVID and no MCP client. If the policy were scoped by namespace instead of by
workload, this probe would wrongly succeed -- so the choice of prober is part
of the assertion.

**Why this is not behind `RUN_BROWSER_TESTS=1`.** Like
`test_caller_identity.py`, it spends no model tokens: it opens TCP connections
and reads status codes. It needs the live stage-2 cluster and nothing else.

**Cost of a false green.** If the policy silently stopped being applied, these
tests failing is the only signal -- the demo would keep working perfectly,
because the legitimate path is unaffected by the thing that broke.
"""

import json
import subprocess

import pytest

KUBE_CONTEXT = "kind-northstar"

PROTECTED_APP = "payments-mcp"
CALLER_APP = "agentgateway"

PROTECTED_HOST = "payments-mcp.northstar-tools.svc.cluster.local"
PROTECTED_PORT = 8000
# Reachable from the same pod and deliberately NOT covered by the policy, so a
# blocked result here would mean the cluster is broken rather than protected.
#
# Not `payments-api.northstar-systems.svc.cluster.local:8080`: `claims-agent`
# never had any business calling `payments-api` (it holds no MCP client and
# no payments URL; `deploy/k8s/claims-agent.yaml` configures exactly one
# peer), so that destination is absent from `claims-agent-egress-allowlist`
# by design and times out once egress allowlists exist on
# `northstar-agents`. Using it as the control would have meant either a
# permanently red control or -- much worse -- widening a security policy so
# a test's control could stay green.
#
# `settlement-agent:9002` is the replacement because it is what this pod is
# *for*: `NORTHSTAR_ALLOWED_AGENTS` names it as claims-agent's single
# delegation target, so it is both ignored by the payments-mcp ingress policy
# under test here and load-bearing for the demo. A blocked result on it is a
# real outage rather than a stale test fixture, which is exactly what a control
# should be.
CONTROL_HOST = "settlement-agent.northstar-agents.svc.cluster.local"
CONTROL_PORT = 9002

# Long enough that a slow-but-allowed connection is not mistaken for a drop,
# short enough that six probes do not dominate the suite. A Cilium drop
# presents as a connect timeout rather than a refusal, because the packet is
# dropped rather than rejected -- there is no RST to turn into ECONNREFUSED.
CONNECT_TIMEOUT = 8

PROBE = r"""
import json, socket, sys
host, port, timeout = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
try:
    s = socket.create_connection((host, port), timeout=timeout)
    s.close()
    print(json.dumps({"reached": True, "error": None}))
except Exception as exc:
    print(json.dumps({"reached": False, "error": type(exc).__name__}))
"""


def _kubectl(*args: str, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _pod(namespace: str, app: str) -> str:
    proc = _kubectl(
        "-n", namespace, "get", "pod", "-l", f"app={app}",
        "-o", "jsonpath={.items[0].metadata.name}",
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        pytest.skip(
            f"needs the live stage-2 cluster: no {app} pod in {namespace} "
            f"({proc.stderr.strip() or 'no output'})"
        )
    return proc.stdout.strip()


def _probe(namespace: str, app: str, container: str, host: str, port: int) -> dict:
    """Open a TCP connection from inside `app`'s pod. Returns the probe's JSON.

    A TCP connect is deliberately the whole probe. The question is reachability
    at the dataplane, and answering it with an HTTP request would conflate a
    Cilium drop with an application-level refusal -- two failures this file
    deliberately keeps distinct.
    """
    pod = _pod(namespace, app)
    proc = _kubectl(
        "-n", namespace, "exec", pod, "-c", container, "--",
        "python3", "-c", PROBE, host, str(port), str(CONNECT_TIMEOUT),
        timeout=CONNECT_TIMEOUT + 45,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        pytest.skip(f"could not exec the probe in {app}: {proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):  # pragma: no cover - diagnostic path
        pytest.fail(f"probe produced unreadable output: {proc.stdout!r}")


@pytest.fixture(scope="module")
def datapath_ingress(policy_present):
    """Ingress rows the Cilium datapath is actually enforcing for payments-mcp.

    Parses `cilium-dbg bpf policy get`'s table rather than its `-o json`,
    deliberately: the JSON reports numeric security identities that would need
    a second lookup to become meaningful, while the table already resolves them
    to labels. A row's labels continue on following lines, which is why this
    accumulates into the current row rather than treating each line as one.
    """
    cil = _kubectl(
        "-n", "kube-system", "get", "pod", "-l", "k8s-app=cilium",
        "-o", "jsonpath={.items[0].metadata.name}",
    )
    if cil.returncode != 0 or not cil.stdout.strip():
        pytest.skip("no cilium agent pod; this is not a stage-2 cluster")

    endpoints = _kubectl(
        "-n", "kube-system", "exec", cil.stdout.strip(), "-c", "cilium-agent",
        "--", "cilium-dbg", "endpoint", "list", "-o", "json",
    )
    if endpoints.returncode != 0:
        pytest.skip(f"could not list cilium endpoints: {endpoints.stderr.strip()}")

    endpoint_id = None
    for ep in json.loads(endpoints.stdout):
        labels = (ep.get("status", {}).get("labels", {}) or {}).get(
            "security-relevant", []
        ) or []
        if any(f"app={PROTECTED_APP}" in label for label in labels):
            endpoint_id = ep["id"]
            break
    if endpoint_id is None:
        pytest.skip(f"no cilium endpoint for {PROTECTED_APP}")

    dump = _kubectl(
        "-n", "kube-system", "exec", cil.stdout.strip(), "-c", "cilium-agent",
        "--", "cilium-dbg", "bpf", "policy", "get", str(endpoint_id),
    )
    if dump.returncode != 0:
        pytest.skip(f"could not read the datapath policy: {dump.stderr.strip()}")

    rows, current = [], None
    for line in dump.stdout.splitlines():
        if not line.strip() or line.lstrip().startswith("POLICY"):
            continue
        fields = line.split()
        if fields[0] in ("Allow", "Deny"):
            current = {
                "verdict": fields[0],
                "direction": fields[1],
                "labels": [fields[2]],
                "ports": " ".join(fields[3:]),
            }
            if current["direction"] == "Ingress":
                rows.append(current)
        elif current is not None:
            current["labels"].append(fields[0])
    return rows


@pytest.fixture(scope="module")
def policy_present():
    """Present on a stage-2 cluster, or the tests below FAIL -- never skip.

    **This fixture had the exact defect it guards against.** Its first version
    skipped when the policy was missing, on the reasoning that an unprovisioned
    cluster should not fail the suite. Mutation testing killed it: deleting the
    live CiliumNetworkPolicy turned all four tests into skips, and a skip does
    not fail a run. So the one event these tests exist to catch -- the policy
    silently no longer being applied -- would have left the suite green.

    The fix is to separate two situations the first version conflated:

    * **Not a stage-2 cluster** (no Cilium agent at all) -- nothing to say, so
      skip. `tests/unit/test_stage2_network_policy.py` still guards the
      manifest and the setup script without a cluster.
    * **A stage-2 cluster with the policy missing** -- that is the regression,
      and it fails here, loudly.
    """
    cilium = _kubectl(
        "-n", "kube-system", "get", "pod", "-l", "k8s-app=cilium",
        "-o", "jsonpath={.items[*].metadata.name}",
    )
    # `cilium.returncode != 0` must not be folded into this skip condition:
    # an unreachable API server -- or a wrong context, or an expired
    # credential -- would otherwise read as "not a stage-2 cluster" and skip
    # every assertion in this file, exactly the "skip where you should fail"
    # defect this repo guards against elsewhere. A label selector does not
    # 404: on a reachable cluster
    # with no Cilium the query succeeds and returns nothing, so EMPTINESS is
    # the honest discriminator and a non-zero exit is a failure.
    assert cilium.returncode == 0, (
        "`kubectl get pod -l k8s-app=cilium` failed, so this file cannot tell "
        "whether the cluster is below stage 2 or simply unreachable, and it "
        "must not guess -- a skip here would remove every assertion below from "
        f"a green run. Exit {cilium.returncode}; stderr: "
        f"{cilium.stderr.strip() or '(empty)'}"
    )
    if not cilium.stdout.strip():
        pytest.skip(
            "not a stage-2 cluster (no Cilium agent), so there is no dataplane "
            "to enforce anything; run ./scripts/setup-kind.sh --stage 7"
        )

    proc = _kubectl(
        "-n", "northstar-tools", "get", "cnp", "payments-mcp-gateway-only",
        "-o", "jsonpath={.metadata.name}",
    )
    assert proc.returncode == 0 and proc.stdout.strip(), (
        "Cilium is running but the payments-mcp-gateway-only "
        "CiliumNetworkPolicy is NOT applied, so any workload in the cluster "
        "can reach payments-mcp directly and the gateway is a convention "
        "rather than a control. Re-apply deploy/platform/cilium-policies.yaml "
        f"(kubectl said: {proc.stderr.strip() or 'no such policy'})"
    )
    return proc.stdout.strip()


def test_an_unprivileged_workload_cannot_reach_the_tool_directly(policy_present):
    """The bypass that worked before the policy existed must now fail.

    This is the assertion this demo's claim rests on. It is written against
    `claims-agent` rather than a purpose-built prober pod so that it exercises
    the policy exactly as a real neighbouring workload would.

    **Which layer this actually exercises.** `payments-mcp`'s *ingress* policy
    says "only the gateway may connect to this pod". `claims-agent`'s own
    egress allowlist (`claims-agent-egress-allowlist`) does not include
    `payments-mcp` at all, so this connection is dropped at **egress**, on
    the client side, before the ingress policy is ever consulted. The
    assertion still fails loudly if *either* layer breaks, which is what
    defence in depth looks like from the outside. What it does not do on its
    own is attribute the block to the ingress half.
    `test_the_datapath_allowlist_admits_the_gateway_and_no_other_workload`
    below pins that half specifically, by reading the enforced allowlist out
    of `payments-mcp`'s BPF policy map rather than probing it: adding a
    `payments-mcp:8000` egress allowance alone would leave this behavioural
    probe green, because the ingress policy would still be dropping the
    packet, and only the BPF-map assertion catches that.
    """
    result = _probe(
        "northstar-agents", "claims-agent", "claims-agent",
        PROTECTED_HOST, PROTECTED_PORT,
    )
    assert not result["reached"], (
        f"claims-agent reached {PROTECTED_HOST}:{PROTECTED_PORT} directly, so "
        "the gateway is not the only route into payments-mcp and the choke "
        "point is a convention rather than a control"
    )


def test_the_block_is_targeted_and_the_cluster_is_otherwise_healthy(policy_present):
    """Control: the same pod can still reach a service the policy ignores.

    Without this, the test above would also pass on a cluster whose networking
    is simply broken, or where `claims-agent` has no egress at all -- a green
    suite reporting a security property that is really an outage.
    """
    result = _probe(
        "northstar-agents", "claims-agent", "claims-agent",
        CONTROL_HOST, CONTROL_PORT,
    )
    assert result["reached"], (
        f"claims-agent could not reach {CONTROL_HOST}:{CONTROL_PORT} either, "
        "so the previous test proves an outage rather than a policy"
    )


def test_the_datapath_allowlist_admits_the_gateway_and_no_other_workload(
    datapath_ingress,
):
    """Reads the enforced allowlist out of the BPF map, not the manifest.

    This is the other half of the property -- the allowlist must actually
    *allow*, or a policy that drops everything would satisfy the bypass test
    while silently breaking the demo.

    It cannot be checked the same way as the bypass. The gateway runs a
    distroless image with no shell and no interpreter (`exec /bin/sh` fails
    with `stat /bin/sh: no such file or directory`), so there is nothing inside
    that pod to open a socket with. Rather than leave a permanently-skipping
    test that looks like coverage, this asks the dataplane what it is actually
    enforcing, which is a stronger question than "did one packet get through":
    it enumerates every identity the endpoint admits.

    Two assertions, and the second is the load-bearing one:

    1. The gateway is admitted on 8000/TCP -- pinned by ServiceAccount as well
       as app label, which is what the `fromEndpoints` selector compiles down
       to.
    2. **No other workload identity is admitted at all.** `reserved:` entries
       (host, remote-node, kube-apiserver) are infrastructure and are excluded
       by construction; any `k8s:app=` other than the gateway is a widened
       choke point and fails here.
    """
    rows = datapath_ingress

    gateway_rows = [
        r for r in rows
        if f"k8s:app={CALLER_APP}" in r["labels"] and "8000/TCP" in r["ports"]
    ]
    assert gateway_rows, (
        "the datapath does not admit agentgateway to payments-mcp on 8000/TCP; "
        "the policy is denying the one workload it exists to permit, and every "
        f"MCP tool call will fail. Rows seen: {rows}"
    )
    assert any(
        f"k8s:io.cilium.k8s.policy.serviceaccount={CALLER_APP}" in r["labels"]
        for r in gateway_rows
    ), (
        "the admitted identity is not pinned to the agentgateway "
        "ServiceAccount, so the allowance is weaker than the manifest implies"
    )

    admitted_workloads = {
        label
        for r in rows
        for label in r["labels"]
        if label.startswith("k8s:app=")
    }
    assert admitted_workloads == {f"k8s:app={CALLER_APP}"}, (
        "exactly one workload may reach payments-mcp; the datapath admits "
        f"{sorted(admitted_workloads)}"
    )


def test_the_protected_pod_is_ready(policy_present):
    """The readiness probe still passes under the policy.

    kubelet dials the `tcpSocket` probe from the node, not from a pod, so a
    policy without a `host` allowance drops it. The symptom is not a network
    error -- it is a pod that never becomes Ready and a `setup-kind.sh` that
    hangs on `rollout status`, with nothing in the output mentioning
    networking.
    """
    proc = _kubectl(
        "-n", "northstar-tools", "get", "pod", "-l", "app=payments-mcp",
        "-o", "jsonpath={.items[*].status.conditions[?(@.type=='Ready')].status}",
    )
    assert "True" in proc.stdout, (
        "payments-mcp is not Ready under the network policy; check that the "
        f"policy still admits `host` for the readiness probe (got {proc.stdout!r})"
    )
