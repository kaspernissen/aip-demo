"""Exit criterion 7: the model provider is reachable from one workload, not a namespace.

The criterion, verbatim: *"Direct egress from `northstar-agents` to the model
provider is **denied** by Cilium, with the gateway as the only path."*

**What was measured before `deploy/platform/cilium-policies.yaml` grew an
egress half.** Not inferred from the absence of a policy -- probed from inside
each of the three pods in `northstar-agents`:

    settlement-agent -> api.anthropic.com:443   reached
    settlement-agent -> example.com:443         reached
    claims-agent     -> api.anthropic.com:443   reached
    claims-agent     -> example.com:443         reached
    customer-ui      -> api.anthropic.com:443   reached

So the criterion was false in three places. Scoping the policy to
`settlement-agent` alone would have satisfied a test named for the criterion
while leaving `claims-agent` and `customer-ui` -- neither of
which holds an API key or makes a model call -- as unrestricted outbound
egress points sitting in the namespace the criterion names. All three are
policied, and this file asserts all three.

**The honest reading of "the gateway as the only path".** `settlement-agent`
*must* reach `api.anthropic.com:443`: that is where its own model call goes,
and agentgateway is an MCP proxy that does not carry model traffic. So the
criterion cannot mean "no pod in this namespace reaches the model provider"
without meaning "the demo does not work". It is discharged here as two
statements that are both testable and both true:

1. **One workload, not the namespace.** Exactly one endpoint in
   `northstar-agents` is admitted to the model provider, and it is the one
   whose job is the model call. The other two are denied -- which is the
   criterion's own sentence, and the thing that was false before.
2. **The gateway really is the only path to a tool.** On the egress side this
   time. `payments-mcp:8000` and `policy-decision-service:9100` appear in no
   allowlist, so `agentgateway:3000` is the only tool destination
   `settlement-agent` can open a socket to at all. The ingress half
   (`test_gateway_is_the_only_route.py`) says the same thing from the other
   end; the two now have to be relaxed independently for the choke point to
   widen.

**Why blocked probes and a BPF map dump, and not an assertion that the policy
object exists.** A `kubectl get cnp` assertion passes on a cluster where Cilium
is not enforcing anything -- it proves a YAML document was accepted by the API
server. Every assertion below either attempts the egress and requires it to
fail, or reads what the datapath is actually enforcing out of the BPF policy
map. Same standard, and the same `cilium-dbg bpf policy get` table parsing, as
the ingress half.

**Controls, because a blanket outage would otherwise read as a success.** The
denial assertions here are all satisfied by a pod with no network at all, so
each one is paired: `settlement-agent` must still reach the model provider, the
gateway and the collector; `claims-agent` must still reach its delegation peer
and the collector; and DNS must still resolve in every pod. That last one is
not decoration -- `toFQDNs` depends on Cilium's DNS proxy seeing the lookup, so
DNS and the model-provider allowance are one mechanism in two rules. If DNS
broke or bypassed the proxy, `toFQDNs` would silently match nothing and the
model call would be dropped with no error naming a policy.

**Why this is not behind `RUN_BROWSER_TESTS=1`.** Like the ingress half, it
spends no model tokens: it opens TCP connections, resolves names and reads a
BPF map. It needs the live stage-2 cluster and nothing else.

**Cost of a false green.** The ingress half's failure mode was invisible (the
demo keeps working when the policy stops being applied). This half's is worse
in one direction and better in another: losing the *deny* is invisible in the
same way, but losing an *allow* breaks the demo loudly -- except for the OTLP
rule, where the BatchSpanProcessor swallows the export failure and the
milestone loses its telemetry while every settlement still succeeds. That
asymmetry is why the collector allowance is asserted here as its own named
test rather than folded into a list.
"""

import json
import subprocess

import pytest

KUBE_CONTEXT = "kind-northstar"

AGENT_NS = "northstar-agents"

# The three endpoints in the namespace the criterion names. `container` is
# spelled out because `customer-ui`'s container has no interpreter -- see
# `_probe_nc`.
MODEL_AGENT = "settlement-agent"
OTHER_AGENTS = ("claims-agent", "customer-ui")
ALL_AGENTS = (MODEL_AGENT, *OTHER_AGENTS)

# The one external name any workload here may reach, and the SDK default it
# comes from rather than a value this repo configures: the pinned `anthropic`
# client's `base_url = f"https://api.anthropic.com"`. No `ANTHROPIC_BASE_URL`
# override exists anywhere in the repo.
MODEL_HOST = "api.anthropic.com"
MODEL_PORT = 443

# Two external hosts that are emphatically not the model provider. Two rather
# than one, and one of them another model provider's API, so the assertion is
# "this is an allowlist" rather than "anthropic.com is special-cased".
FOREIGN_EXTERNAL = (("example.com", 443), ("api.openai.com", 443))

# The tool destinations that must be unreachable from the agent plane, so that
# agentgateway is the only route to a tool from this side too. Both are already
# protected by an *ingress* policy; their absence from every egress allowlist is
# the independent second statement.
DENIED_TOOLS = (
    ("payments-mcp.northstar-tools.svc.cluster.local", 8000),
    ("policy-decision-service.northstar-platform.svc.cluster.local", 9100),
    ("payments-api.northstar-systems.svc.cluster.local", 8080),
)

GATEWAY_HOST = "agentgateway.northstar-platform.svc.cluster.local"
GATEWAY_PORT = 3000
COLLECTOR_HOST = "otel-collector.observability.svc.cluster.local"
COLLECTOR_PORT = 4318
PEER_HOST = "settlement-agent.northstar-agents.svc.cluster.local"
PEER_PORT = 9002

# The egress allowlist each endpoint is supposed to have, as (workload label,
# port/proto) pairs the datapath should admit. Read off the manifest, and
# asserted as an equality below so that *adding* a destination fails here
# instead of quietly widening the policy. `k8s:app=` is the only label form
# compared: FQDN- and reserved-identity rows are handled separately, because
# they are not workloads.
EXPECTED_WORKLOAD_EGRESS = {
    "settlement-agent": {
        ("k8s:k8s-app=kube-dns", "53/TCP"),
        ("k8s:k8s-app=kube-dns", "53/UDP"),
        ("k8s:app=agentgateway", "3000/TCP"),
        ("k8s:app=otel-collector", "4318/TCP"),
    },
    "claims-agent": {
        ("k8s:k8s-app=kube-dns", "53/TCP"),
        ("k8s:k8s-app=kube-dns", "53/UDP"),
        ("k8s:app=settlement-agent", "9002/TCP"),
        # M8: the ledger read on resume (L7, GET /payments/{id} only); the
        # datapath shows the L4 pair, the method restriction is asserted by
        # the policy tests.
        ("k8s:app=payments-api", "8080/TCP"),
        ("k8s:app=otel-collector", "4318/TCP"),
    },
    "customer-ui": {
        ("k8s:k8s-app=kube-dns", "53/TCP"),
        ("k8s:k8s-app=kube-dns", "53/UDP"),
    },
}

EGRESS_POLICIES = {
    "settlement-agent-egress-allowlist": MODEL_AGENT,
    "claims-agent-egress-allowlist": "claims-agent",
    "customer-ui-egress-allowlist": "customer-ui",
}

# Same reasoning as the ingress half: long enough that a slow-but-allowed
# connection is not mistaken for a drop, short enough that a dozen probes do
# not dominate the suite. A Cilium drop presents as a connect timeout, not a
# refusal -- the packet is dropped, so there is no RST to become
# ECONNREFUSED, and a test that expected ECONNREFUSED here would fail on a
# working policy.
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


def _kubectl(*args: str, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _pod(app: str) -> str:
    proc = _kubectl(
        "-n", AGENT_NS, "get", "pod", "-l", f"app={app}",
        "-o", "jsonpath={.items[0].metadata.name}",
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        pytest.skip(
            f"needs the live stage-2 cluster: no {app} pod in {AGENT_NS} "
            f"({proc.stderr.strip() or 'no output'})"
        )
    return proc.stdout.strip()


def _probe(app: str, host: str, port: int) -> dict:
    """Open a TCP connection from inside `app`'s pod. Returns the probe's JSON.

    A bare TCP connect is deliberately the whole probe, for the same reason the
    ingress half gives: the question is reachability at the dataplane, and
    asking it with an HTTP request would conflate a Cilium drop with an
    application-level refusal -- the two failures this milestone spent effort
    keeping distinct. It also means the model-provider control costs no tokens
    and needs no API key: nothing is sent on the socket.
    """
    if app == "customer-ui":
        return _probe_nc(app, host, port)
    proc = _kubectl(
        "-n", AGENT_NS, "exec", _pod(app), "-c", app, "--",
        "python3", "-c", PROBE, host, str(port), str(CONNECT_TIMEOUT),
        timeout=CONNECT_TIMEOUT + 45,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        pytest.skip(f"could not exec the probe in {app}: {proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):  # pragma: no cover - diagnostic path
        pytest.fail(f"probe produced unreadable output: {proc.stdout!r}")


def _probe_nc(app: str, host: str, port: int) -> dict:
    """The same probe for `customer-ui`, which has no Python in it.

    `customer-ui` is nginx on Alpine serving a static export -- `python3` is
    absent and `command -v` finds only busybox `nc`. That is worth a second
    code path rather than a skip: this pod being the one with a shell and no
    interpreter is *why* it needed a policy of its own, so a probe that quietly
    skipped here would be missing on exactly the endpoint whose omission was
    the easiest outbound path in the namespace.
    """
    proc = _kubectl(
        "-n", AGENT_NS, "exec", _pod(app), "--",
        "sh", "-c",
        f'nc -z -w {CONNECT_TIMEOUT} {host} {port} && echo REACHED || echo BLOCKED',
        timeout=CONNECT_TIMEOUT + 45,
    )
    out = proc.stdout.strip().splitlines()
    if not out:
        pytest.skip(f"could not exec the probe in {app}: {proc.stderr.strip()}")
    if out[-1] == "REACHED":
        return {"reached": True, "error": None}
    if out[-1] == "BLOCKED":
        return {"reached": False, "error": "nc-nonzero"}
    pytest.fail(f"probe produced unreadable output: {proc.stdout!r}")


@pytest.fixture(scope="module")
def policy_present():
    """Present on a stage-2 cluster, or every test below FAILS -- never skips.

    Structured exactly like the ingress half's fixture of the same name, and
    for the reason recorded there: its first version skipped when the policy
    was missing, and mutation testing killed it -- deleting the live
    CiliumNetworkPolicy turned four tests into skips and left the suite green,
    so the one event the file exists to catch was invisible. Two situations
    that must not be conflated:

    * **Not a stage-2 cluster** (no Cilium agent at all) -- nothing to say, so
      skip.
    * **A stage-2 cluster with an egress policy missing** -- that is the
      regression, and it fails here, loudly, naming the policy.

    All three policies are checked, not just `settlement-agent`'s. Deleting
    `claims-agent-egress-allowlist` alone would restore precisely the gap this
    criterion closes -- an unrestricted route to the model provider from the
    namespace -- while `settlement-agent`'s policy stayed applied and the demo
    kept working.
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

    for name in sorted(EGRESS_POLICIES):
        proc = _kubectl(
            "-n", AGENT_NS, "get", "cnp", name, "-o", "jsonpath={.metadata.name}",
        )
        assert proc.returncode == 0 and proc.stdout.strip(), (
            f"Cilium is running but the {name} CiliumNetworkPolicy is NOT "
            f"applied, so {EGRESS_POLICIES[name]} has unrestricted outbound "
            "egress and can reach the model provider directly. Re-apply "
            "deploy/platform/cilium-policies.yaml (kubectl said: "
            f"{proc.stderr.strip() or 'no such policy'})"
        )
    return sorted(EGRESS_POLICIES)


@pytest.fixture(scope="module")
def datapath(policy_present):
    """What the BPF policy map is enforcing, per agent-plane endpoint.

    Parses `cilium-dbg bpf policy get`'s table rather than its `-o json`, for
    the reason the ingress half records: the JSON reports numeric security
    identities that would need a second lookup to become meaningful, while the
    table already resolves them to labels. A row's labels continue on following
    lines, which is why this accumulates into the current row.

    Keeps one extra column the ingress half discards -- `PROXY PORT`. That is
    how the DNS proxy shows up in the datapath, and the proxy is the mechanism
    `toFQDNs` depends on, so it is evidence rather than noise.
    """
    cil = _kubectl(
        "-n", "kube-system", "get", "pod", "-l", "k8s-app=cilium",
        "-o", "jsonpath={.items[0].metadata.name}",
    )
    if cil.returncode != 0 or not cil.stdout.strip():
        pytest.skip("no cilium agent pod; this is not a stage-2 cluster")
    agent = cil.stdout.strip()

    listing = _kubectl(
        "-n", "kube-system", "exec", agent, "-c", "cilium-agent",
        "--", "cilium-dbg", "endpoint", "list", "-o", "json",
    )
    if listing.returncode != 0:
        pytest.skip(f"could not list cilium endpoints: {listing.stderr.strip()}")
    endpoints = json.loads(listing.stdout)

    out = {}
    for app in ALL_AGENTS:
        endpoint_id = None
        for ep in endpoints:
            labels = (ep.get("status", {}).get("labels", {}) or {}).get(
                "security-relevant", []
            ) or []
            if any(f"app={app}" in label for label in labels):
                endpoint_id = ep["id"]
                break
        assert endpoint_id is not None, (
            f"Cilium has no endpoint for {app}, but its pod exists in "
            f"{AGENT_NS} -- an unmanaged endpoint is enforcing nothing"
        )

        dump = _kubectl(
            "-n", "kube-system", "exec", agent, "-c", "cilium-agent",
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
                    "port": fields[3],
                    "proxy_port": fields[4],
                }
                rows.append(current)
            elif current is not None:
                current["labels"].append(fields[0])
        out[app] = rows
    return out


def _egress(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["direction"] == "Egress" and r["verdict"] == "Allow"]


# --- The criterion's own sentence -------------------------------------------


@pytest.mark.parametrize("app", OTHER_AGENTS)
def test_an_agent_with_no_model_call_cannot_reach_the_model_provider(
    app, policy_present
):
    """§9.7: direct egress from `northstar-agents` to the model provider.

    This is the assertion the criterion names, and it is written against the
    two workloads that were measured reaching `api.anthropic.com:443` before
    the policy existed. Neither holds `ANTHROPIC_API_KEY` and neither makes a
    model call, so neither loses anything -- but before this, either could have
    been the exfiltration path out of the namespace, and the milestone's
    "second defence" would have been a claim about one pod.

    Parametrized over the workloads rather than asserted as a loop so that a
    failure names which pod is reachable.
    """
    result = _probe(app, MODEL_HOST, MODEL_PORT)
    assert not result["reached"], (
        f"{app} reached {MODEL_HOST}:{MODEL_PORT} directly, so direct egress "
        "from northstar-agents to the model provider is not denied and "
        "criterion 7 is false for this pod"
    )


@pytest.mark.parametrize("host,port", FOREIGN_EXTERNAL)
def test_the_model_agent_reaches_no_other_external_host(host, port, policy_present):
    """The allowance is an allowlist of one name, not "external is fine".

    `settlement-agent` has to be allowed out to the model provider, so the
    denial that carries weight for *this* pod is a different one: every other
    external destination. `api.openai.com` is in the list on purpose -- if the
    rule had been written as a pattern, or if the DNS proxy were handing back a
    blanket `reserved:world` allowance on 443, a second model provider is
    exactly what would slip through while `example.com` still looked blocked.
    """
    result = _probe(MODEL_AGENT, host, port)
    assert not result["reached"], (
        f"{MODEL_AGENT} reached {host}:{port}, so its egress allowance is not "
        f"scoped to {MODEL_HOST} and the toFQDNs rule is admitting more than "
        "the one name it lists"
    )


@pytest.mark.parametrize("host,port", DENIED_TOOLS)
def test_the_gateway_is_the_only_tool_destination_from_the_model_agent(
    host, port, policy_present
):
    """The criterion's second clause -- "with the gateway as the only path".

    Asserted from the egress side. `test_gateway_is_the_only_route.py` proves
    only agentgateway may open a connection *to* payments-mcp; the property
    here is that `settlement-agent` cannot open a connection to a tool at all,
    so the two have to be relaxed independently for the choke point to widen.

    **What this probe can and cannot attribute, measured rather than assumed.**
    Adding a `payments-mcp:8000` allowance to settlement-agent's egress policy
    and re-running this file left this test GREEN on that destination -- the
    ingress policy on payments-mcp was still dropping the connection, so the
    probe cannot tell which of the two halves refused it. That is defence in
    depth working exactly as intended, and it is also a limit on what this
    assertion evidences. Two things follow, and both are deliberate:

    * `payments-api.northstar-systems:8080` is in the parametrized list
      because it carries no CiliumNetworkPolicy of its own. A block there is
      attributable to this policy and nothing else, so it is the member of
      this list that actually measures the egress half.
    * The loss of the egress half on the two destinations that *do* have
      ingress policies is caught by
      `test_the_datapath_egress_allowlist_is_exactly_the_measured_inventory`,
      which failed on that same mutation. A behavioural probe alone would
      have missed it.
    """
    result = _probe(MODEL_AGENT, host, port)
    assert not result["reached"], (
        f"{MODEL_AGENT} reached {host}:{port} directly, bypassing "
        f"{GATEWAY_HOST}:{GATEWAY_PORT}; the gateway is not the only path out "
        "of the agent to a tool"
    )


# --- Controls: the denials above must not be an outage ----------------------


def test_the_model_agent_still_reaches_the_model_provider(policy_present):
    """Control, and the one that makes the whole file mean something.

    Without it, every denial above is satisfied by an agent plane with no
    network at all -- a green suite reporting a security property that is
    really a demo that cannot run. It is also the only end-to-end evidence
    that the `toFQDNs` rule and the L7 DNS rule are working *together*: the
    allowance is derived from what Cilium's DNS proxy saw resolve, so a
    connection succeeding here means the proxy is interposed, the lookup was
    permitted, and the resulting identity is admitted on 443.
    """
    result = _probe(MODEL_AGENT, MODEL_HOST, MODEL_PORT)
    assert result["reached"], (
        f"{MODEL_AGENT} cannot reach {MODEL_HOST}:{MODEL_PORT}, so every "
        "settlement will fail on the model call. The usual cause is the DNS "
        "rule: toFQDNs is populated from what Cilium's DNS proxy observes, so "
        "removing the `rules.dns` block from the DNS egress rule leaves this "
        "policy VALID and this connection dropped"
    )


@pytest.mark.parametrize(
    "host,port",
    [(GATEWAY_HOST, GATEWAY_PORT), (COLLECTOR_HOST, COLLECTOR_PORT)],
)
def test_the_model_agent_still_reaches_the_gateway_and_the_collector(
    host, port, policy_present
):
    """The two in-cluster destinations a settlement cannot happen without."""
    result = _probe(MODEL_AGENT, host, port)
    assert result["reached"], (
        f"{MODEL_AGENT} cannot reach {host}:{port} -- the egress allowlist is "
        "denying a destination the demo depends on"
    )


@pytest.mark.parametrize(
    "host,port", [(PEER_HOST, PEER_PORT), (COLLECTOR_HOST, COLLECTOR_PORT)]
)
def test_claims_agent_still_reaches_its_peer_and_the_collector(
    host, port, policy_present
):
    """Control for the claims-agent denial, on the two things it actually does.

    `NORTHSTAR_ALLOWED_AGENTS` names `settlement-agent:9002` as its single
    delegation target; losing it would break every claim, not just the model
    provider. This is also now the positive control
    `test_gateway_is_the_only_route.py` uses, for the reason recorded there.
    """
    result = _probe("claims-agent", host, port)
    assert result["reached"], (
        f"claims-agent cannot reach {host}:{port}, so the model-provider "
        "denial above proves an outage rather than a policy"
    )


def test_the_collector_is_reachable_from_every_agent_that_exports(policy_present):
    """Its own test because its failure is the one that looks like success.

    The OTLP exporter runs on a BatchSpanProcessor background thread and
    swallows export failures. Drop the collector rule and settlements keep
    succeeding, the UI keeps showing the right numbers, and every span from
    that service silently disappears -- in a milestone whose entire claim is
    that the decision is visible in one trace. Nothing else in this repo's
    suites would go red.
    """
    unreachable = [
        app
        for app in (MODEL_AGENT, "claims-agent")
        if not _probe(app, COLLECTOR_HOST, COLLECTOR_PORT)["reached"]
    ]
    assert not unreachable, (
        f"{unreachable} cannot reach {COLLECTOR_HOST}:{COLLECTOR_PORT}; their "
        "spans are being dropped on the floor while the demo appears to work"
    )


@pytest.mark.parametrize("app", ALL_AGENTS)
def test_dns_still_resolves_inside_every_agent_pod(app, policy_present):
    """DNS is a control here *and* half of the enforcement mechanism.

    Two things would break if the DNS rule were wrong, and only one of them is
    obvious. The obvious one: nothing resolves and every call fails. The
    non-obvious one: `toFQDNs` is populated from responses Cilium's DNS proxy
    observes, so DNS that works but bypasses the proxy leaves the model
    provider silently unreachable with no error mentioning a policy.

    The names resolved include the in-cluster ones on purpose. `ClusterFirst`
    DNS appends search domains, so those lookups also emit queries for
    suffixed variants, and a `rules.dns` block narrower than `matchPattern:
    "*"` would have the DNS proxy deny the ones that do not match -- breaking
    resolution through the search list while looking tighter than it is.
    """
    names = [MODEL_HOST, GATEWAY_HOST, COLLECTOR_HOST]
    if app == "customer-ui":
        proc = _kubectl(
            "-n", AGENT_NS, "exec", _pod(app), "--",
            "sh", "-c", f"nslookup {MODEL_HOST} >/dev/null 2>&1 && echo OK || echo FAIL",
            timeout=60,
        )
        assert proc.stdout.strip().splitlines()[-1:] == ["OK"], (
            f"{app} cannot resolve {MODEL_HOST}: {proc.stdout!r} {proc.stderr!r}"
        )
        return

    proc = _kubectl(
        "-n", AGENT_NS, "exec", _pod(app), "-c", app, "--",
        "python3", "-c", RESOLVE, *names,
        timeout=90,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        pytest.skip(f"could not exec the resolver in {app}: {proc.stderr.strip()}")
    resolved = json.loads(proc.stdout.strip().splitlines()[-1])
    failed = {n: v for n, v in resolved.items() if v.startswith("FAILED:")}
    assert not failed, (
        f"{app} could not resolve {sorted(failed)} -- the DNS egress rule is "
        f"wrong, and on {MODEL_AGENT} that also silently disables the toFQDNs "
        f"allowance. Full result: {resolved}"
    )


@pytest.mark.parametrize("app", ALL_AGENTS)
def test_every_agent_pod_is_still_ready(app, policy_present):
    """Nothing was severed, stated the way an operator would notice it.

    None of the egress policy documents carries an `ingress` block, so these
    endpoints stay default-*allow* for ingress and kubelet's `httpGet` probes
    -- dialled from the node, not from a pod -- are unaffected. Asserted rather
    than argued, because the symptom of getting it wrong is not a network error
    but a pod that never becomes Ready and a `rollout status` that hangs with
    nothing in its output mentioning networking.
    """
    proc = _kubectl(
        "-n", AGENT_NS, "get", "pod", "-l", f"app={app}",
        "-o", "jsonpath={.items[*].status.conditions[?(@.type=='Ready')].status}",
    )
    assert "True" in proc.stdout, (
        f"{app} is not Ready under the egress policy (got {proc.stdout!r})"
    )


# --- The datapath, not the manifest ----------------------------------------


def test_the_datapath_admits_the_model_provider_by_name_and_not_the_world(datapath):
    """The FQDN allowance is a per-name identity, not a hole punched to 443.

    Cilium implements `toFQDNs` by minting a CIDR identity from the addresses
    it saw the name resolve to, and that identity carries **both**
    `fqdn:api.anthropic.com` and `reserved:world` labels. So "assert no egress
    row mentions reserved:world" would be wrong -- it would fail on a correct
    policy. The assertion that actually distinguishes the two cases is that
    *every* world-bearing egress row is also FQDN-labelled for the one name,
    and is scoped to 443/TCP. A blanket `toEntities: world` allowance would
    produce a row with `reserved:world` and no `fqdn:` label, and would fail
    here -- which is the mistake this test exists to catch, because it would
    leave every behavioural probe in this file passing.
    """
    rows = _egress(datapath[MODEL_AGENT])
    world = [r for r in rows if "reserved:world" in r["labels"]]
    assert world, (
        "the datapath grants settlement-agent no external egress at all, so "
        f"{MODEL_HOST} is unreachable and every settlement fails on the model "
        f"call. Egress rows seen: {rows}"
    )
    for row in world:
        assert f"fqdn:{MODEL_HOST}" in row["labels"], (
            "an egress row admits reserved:world without being scoped to a "
            f"single FQDN -- that is a blanket external allowance: {row}"
        )
        assert row["port"] == f"{MODEL_PORT}/TCP", (
            f"the model-provider allowance is not scoped to {MODEL_PORT}/TCP: {row}"
        )


@pytest.mark.parametrize("app", OTHER_AGENTS)
def test_the_datapath_gives_the_other_agents_no_external_egress_at_all(app, datapath):
    """Read out of the BPF map, the stronger form of the criterion's sentence.

    The probe above shows one external host is unreachable from this pod. This
    shows the datapath admits *no* external identity from it -- no
    `reserved:world`, no `fqdn:`, no CIDR -- so the property does not depend on
    which host the probe happened to pick.
    """
    external = [
        r
        for r in _egress(datapath[app])
        if any(
            label.startswith(("reserved:world", "fqdn:", "cidr:"))
            for label in r["labels"]
        )
    ]
    assert not external, (
        f"the datapath admits external egress from {app}, which makes no model "
        f"call and holds no API key: {external}"
    )


@pytest.mark.parametrize("app", ALL_AGENTS)
def test_the_datapath_egress_allowlist_is_exactly_the_measured_inventory(
    app, datapath
):
    """Equality, not containment, so adding a destination fails here.

    Compares the set of (workload label, port/proto) pairs the BPF map admits
    against the inventory each policy was written from. Containment would let a
    later `toEndpoints` block widen the agent plane's reachability with nothing
    going red; this is the same set-equality shape the ingress half uses on its
    admitted identities, and for the same reason.

    FQDN and reserved identities are excluded here by construction -- they are
    not workloads, and the two tests above own them.
    """
    admitted = {
        (label, row["port"])
        for row in _egress(datapath[app])
        for label in row["labels"]
        if label.startswith(("k8s:app=", "k8s:k8s-app="))
    }
    assert admitted == EXPECTED_WORKLOAD_EGRESS[app], (
        f"{app}'s enforced egress allowlist is not the inventory its policy "
        f"was written from.\n  extra:   {sorted(admitted - EXPECTED_WORKLOAD_EGRESS[app])}"
        f"\n  missing: {sorted(EXPECTED_WORKLOAD_EGRESS[app] - admitted)}"
    )


def test_the_dns_proxy_is_interposed_on_the_model_agent_and_only_there(datapath):
    """The coupling between the two rules `toFQDNs` needs, seen in the datapath.

    `PROXY PORT` is how Cilium's DNS proxy appears in the BPF policy map: a
    non-`NONE` value on a 53 row means DNS from that endpoint is redirected
    through the proxy, which is the only way `toFQDNs` ever learns an address.
    Asserting it here is what turns "the model call works" from an observation
    into an explanation -- and it fails on the specific mistake of granting
    plain L4 DNS to `settlement-agent`, which leaves the policy VALID, DNS
    working, and the model provider silently unreachable.

    The negative half matters too. `claims-agent` and `customer-ui` are given
    L4-only DNS deliberately, because neither has a `toFQDNs` rule to feed;
    if the proxy showed up on them, the two documents would be doing the same
    job while claiming to differ, and the DNS proxy would be sitting in the
    path of the demo's entry service for no reason.
    """
    model_dns = [
        r for r in _egress(datapath[MODEL_AGENT]) if r["port"].startswith("53/")
    ]
    assert len(model_dns) == 2, (
        f"expected 53/TCP and 53/UDP egress rows for {MODEL_AGENT}, got "
        f"{[r['port'] for r in model_dns]}"
    )
    for row in model_dns:
        assert row["proxy_port"] != "NONE", (
            "DNS from settlement-agent is not redirected through Cilium's DNS "
            "proxy, so toFQDNs can never populate and the model provider is "
            f"unreachable however the policy reads: {row}"
        )

    for app in OTHER_AGENTS:
        for row in _egress(datapath[app]):
            if row["port"].startswith("53/"):
                assert row["proxy_port"] == "NONE", (
                    f"{app} has no toFQDNs rule, so interposing the DNS proxy "
                    f"on its lookups buys nothing: {row}"
                )


def test_the_agent_planes_ingress_is_exactly_what_the_milestones_intended(datapath):
    """Inbound A2A must be filtered exactly where intended, and nowhere else.

    This test once asserted the opposite: it was named
    `test_the_agent_plane_is_still_default_allow_for_ingress`, and its
    premise was that the egress policy touches egress only: `claims-agent`
    calls `settlement-agent:9002` and the browser calls `claims-agent:9001`,
    so an `ingress` block accidentally added to one of those egress
    documents would make that endpoint default-deny and break the chain
    from the far end. It asserted the absence of such a block where it is
    enforced -- as the catch-all `Allow Ingress ANY` row the datapath
    carries for an endpoint no ingress policy selects.

    **`settlement-agent-ingress-allowlist` exists on purpose.** Both
    settlement Deployments were reachable from anything in the mesh, so
    anything that could reach an A2A port could execute any proposed
    payment. That is the exact condition the old assertion required to
    remain true. Nothing caught the contradiction because this module needs
    a live Cilium dataplane and `cilium-dbg`, so it is not part of any
    cheap local loop.

    The repair is deliberately a STRENGTHENING rather than a narrowing, so the
    mutation the original was written to catch is still caught:

    * `claims-agent` and `customer-ui` must still carry the catch-all.
      Nothing has filtered their ingress, and an ingress block accidentally
      added to either egress document still fails here.
    * `settlement-agent` must now be default-DENY for ingress AND must admit
      `claims-agent` on 9002 specifically. The old test passed on
      "unfiltered"; this one fails both if the allowlist disappears (the
      catch-all returns) and if the allowlist stops naming its one real
      caller (the 9002 row goes). An accidental ingress block on the *egress*
      document would still be caught, because it would not produce a
      claims-agent-labelled row on 9002.

    Scope is unchanged on purpose: `reviewer-ui` has its own ingress document
    and its own coverage in tests/e2e/test_human_in_the_loop.py; widening this
    module's fixture to it is a separate change, not a bug fix.
    """
    for app in OTHER_AGENTS:
        catch_all = [
            r
            for r in datapath[app]
            if r["direction"] == "Ingress"
            and r["verdict"] == "Allow"
            and r["labels"] == ["ANY"]
            and r["port"] == "ANY"
        ]
        assert catch_all, (
            f"{app} is no longer default-allow for ingress, so something added "
            "an ingress block to an egress-only policy; inbound A2A and the "
            f"kubelet readiness probe are now filtered. Nothing asks "
            f"for that on {app}. Rows: {datapath[app]}"
        )

    ingress = [r for r in datapath[MODEL_AGENT] if r["direction"] == "Ingress"]
    catch_all = [
        r
        for r in ingress
        if r["verdict"] == "Allow" and r["labels"] == ["ANY"] and r["port"] == "ANY"
    ]
    assert not catch_all, (
        f"{MODEL_AGENT} is default-allow for ingress again, so "
        "settlement-agent-ingress-allowlist is no longer selecting it -- "
        "anything in the mesh that can reach 9002 can execute a proposed "
        f"payment. Rows: {ingress}"
    )
    from_claims = [
        r
        for r in ingress
        if r["verdict"] == "Allow"
        and r["port"] == "9002/TCP"
        and any(lbl == "k8s:app=claims-agent" for lbl in r["labels"])
    ]
    assert from_claims, (
        f"{MODEL_AGENT}'s ingress no longer admits claims-agent on 9002, so "
        "the A2A hop the whole demo runs through is filtered. An ingress "
        "policy that denies the one caller it exists to permit is worse than "
        f"no policy. Rows: {ingress}"
    )
