"""Guards the Policy Decision Service wiring: the gateway policy that calls
it, and the PDS's own deployment.

**What this module exists to protect.** The gateway authenticates every MCP
`tools/call` and then decides whether to permit it. That decision is
configuration -- six keys in `deploy/platform/agentgateway/config.yaml` and
one Deployment -- and every one of those keys has a default, so an omission
is not a syntax error, it is a silent policy change.

**Why `failureMode: failClosed` is stated explicitly.** Measured against the
pinned agentgateway image (whose `--version` reports git_revision
fe6732474a96a0363dfb9822859af4e9bab360fa, the v1.5.0 tag), with the guardrail
pointed at a dead address so every `tools/call` takes the failure branch:
omitting `failureMode` rejects the call with `mcpGuardrails checkRequest
failed: ... Connection refused`, `failClosed` returns that same error byte
for byte, and only `failOpen` lets the call through to the tool -- confirmed
in the source, where `crates/agentgateway/src/mcp/guardrails/mod.rs` puts
`#[default]` on `FailClosed`. So an *absent* key already behaves as
`failClosed`; the risk this guards against is a deliberate one-word edit to
`failOpen`, not an accidental deletion. `failOpen` is one word away from
`failClosed`, and a fail-open gateway is indistinguishable from a governing
one until somebody reads a trace: the demo keeps working, the €6,200 goes
out, and nothing in the output, the pod list or the agent's reply says the
platform stopped governing. Requiring the value to be present and explicit
makes that intent reviewable in a diff rather than inherited from an
upstream default.

**Every assertion parses YAML or Bash structurally. None greps.** This repo's
manifests carry heavy explanatory comments, and those comments contain every
string a naive guard would search for -- `failClosed`, `policy-decision-service`,
`9100`, `tools/call`, `agentgateway`. A `"failClosed" in text` assertion would
stay green with the entire `mcpGuardrails` block deleted, because the comment
above it explains at length what `failClosed` does. So `yaml.safe_load`
(which discards comments before anything is asserted) does the reading here,
and the two assertions about `setup-kind.sh` go through that module's own
comment-stripping helper.

**No assertion reads only the first element of a list the schema allows to be
longer.** The gateway schema permits a list of binds, of listeners, of routes
and of guardrail processors. Reading only `binds[0].listeners[0].routes[0]`
would let a *second* listener with no `jwtAuth` leave the whole suite green.
The same hole is available here twice over: a second route with no
`mcpGuardrails` is an ungoverned way in, and a second *processor* --
fail-open, pointed elsewhere -- would sit alongside a correct one and satisfy
any check that stopped at index 0. So the rule below is not "there is a
processor and it is fail-closed" but "every processor that exists is
fail-closed", and not "a route is governed" but "every route is".

**What this module does NOT prove.** That a payment is actually refused. A
config that parses, validates against the pinned image and applies cleanly can
still permit €6,200 -- if the PDS is unreachable and the guardrail is
fail-open, if the ConfigMap on the cluster is not this file, if the pod is not
Ready. The live evidence for the refusal is in `tests/e2e/`. This module
asserts that the configuration says what the rest of the repo believes it
says.
"""

from pathlib import Path

import yaml

from tests.unit._setup_script import effective_script as _effective_script

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_CONFIG = REPO_ROOT / "deploy" / "platform" / "agentgateway" / "config.yaml"
PDS_MANIFEST = REPO_ROOT / "deploy" / "k8s" / "policy-decision-service.yaml"
CILIUM_POLICIES = REPO_ROOT / "deploy" / "platform" / "cilium-policies.yaml"
SPIRE_REGISTRATION = REPO_ROOT / "deploy" / "platform" / "spire" / "registration.yaml"
REBUILD_AND_DEPLOY = REPO_ROOT / "scripts" / "rebuild-and-deploy.sh"

APP = "policy-decision-service"
NAMESPACE = "northstar-platform"
PORT = 9100
GATEWAY_APP = "agentgateway"
NAMESPACE_LABEL = "k8s:io.kubernetes.pod.namespace"

# The service's own `DEFAULT_PORT`, and the port `README.md` publishes. Named
# here as a constant so the several places that must agree about it -- the
# guardrail `host`, the container port, the Service, both probes and the
# network policy -- are all checked against one value rather than against each
# other pairwise.
PDS_PORT = 9100


# --- Readers ---------------------------------------------------------------


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _gateway_config() -> dict:
    return yaml.safe_load(GATEWAY_CONFIG.read_text())


def _all_routes() -> list[tuple[str, dict]]:
    """Every route across every bind and every listener, with a path to each.

    Deliberately duplicated from `test_stage2_spire_config.py` rather than
    imported: that module's copy exists to enumerate *authentication*, and
    coupling the authorization guards to it would mean a future edit made for
    one could silently change the enumeration unit of the other.
    """
    config = _gateway_config()
    routes = []
    for bind_index, bind in enumerate(config.get("binds") or []):
        for listener_index, listener in enumerate(bind.get("listeners") or []):
            for route_index, route in enumerate(listener.get("routes") or []):
                routes.append(
                    (
                        f"binds[{bind_index}].listeners[{listener_index}]"
                        f".routes[{route_index}]",
                        route,
                    )
                )
    assert routes, "the gateway config declares no routes at all"
    return routes


def _route_policies() -> dict:
    """The `policies` block of the one route this gateway serves.

    Asserted to be exactly one route, because the assertions that read this
    helper are about a specific block's *contents*; the assertions about
    coverage ("every route is governed") enumerate `_all_routes()` instead and
    do not go through here. If a second route is ever added legitimately, this
    helper is the thing to change, and the coverage tests will keep holding in
    the meantime.
    """
    routes = _all_routes()
    assert len(routes) == 1, (
        f"expected one route on this gateway; found {len(routes)}: "
        f"{[where for where, _ in routes]}. The coverage assertions below "
        "still hold for any number, but the content assertions need to know "
        "which one they are reading."
    )
    return routes[0][1].get("policies") or {}


def _guardrail_processors() -> list[tuple[str, dict]]:
    """Every `mcpGuardrails` processor on every route, with a path to each."""
    processors = []
    for where, route in _all_routes():
        guardrails = (route.get("policies") or {}).get("mcpGuardrails") or {}
        for index, processor in enumerate(guardrails.get("processors") or []):
            processors.append((f"{where}.mcpGuardrails.processors[{index}]",
                               processor))
    assert processors, (
        "no mcpGuardrails processor anywhere in the gateway config -- with no "
        "processor the gateway permits every tool call it can authenticate, "
        "and this module exists to guard the refusal"
    )
    return processors


def _guardrail_processor() -> dict:
    """The single processor, for the content assertions."""
    processors = _guardrail_processors()
    assert len(processors) == 1, (
        f"expected exactly one guardrail processor; found {len(processors)}: "
        f"{[where for where, _ in processors]}. More than one is not wrong in "
        "itself, but each must be checked, so extend the content assertions "
        "rather than widening this."
    )
    return processors[0][1]


def _pds_object(kind: str) -> dict:
    hits = [d for d in _docs(PDS_MANIFEST) if d.get("kind") == kind]
    assert len(hits) == 1, (
        f"expected exactly one {kind} in {PDS_MANIFEST.name}; found "
        f"{len(hits)}"
    )
    return hits[0]


def _deployment() -> dict:
    return _pds_object("Deployment")


def _containers() -> list[dict]:
    """Every container in the pod spec, init containers included.

    The unit is `initContainers + containers`: a check widened only to
    `containers` would accept an env var or a probe that lives on an init
    container, i.e. somewhere unreachable at request time.
    """
    pod = _deployment()["spec"]["template"]["spec"]
    return list(pod.get("initContainers") or []) + list(pod.get("containers") or [])


# --- The guardrail: the six keys that make the refusal real -----------------


def test_the_guardrail_is_fail_closed():
    """`failOpen` is one word away, and the difference is invisible when
    everything is healthy.

    The default (an absent `failureMode` key) already behaves exactly as
    `failClosed` does (see this module's docstring for the measurement), so
    the risk this test guards is not an accidental deletion; it is a
    deliberate one-word edit to `failOpen`, whose effect -- a gateway that
    keeps working and stops governing, with nothing in the pod list or the
    agent's reply to say so -- is strictly worse than a demo that breaks.

    The key is still required to be *present*, not merely not-`failOpen`. An
    explicit value is what makes the intent reviewable in a diff instead of
    inherited from an upstream default that could change under us in any
    release.

    The failure message names both accepted spellings because they are the
    validator's own: the pinned image rejects `fail_closed` with "unknown
    variant `fail_closed`, expected `failClosed` or `failOpen`".

    Enumerated over every processor: a second, fail-open processor alongside a
    correct one is a fail-open gateway.
    """
    for where, processor in _guardrail_processors():
        assert processor.get("failureMode") == "failClosed", (
            f"{where}.failureMode is {processor.get('failureMode')!r}; it must "
            "be 'failClosed', stated explicitly. 'failOpen' means a PDS outage "
            "permits every payment -- the demo would keep working and stop "
            "governing, with nothing in the pod list or the agent's reply to "
            "say so. An absent key currently behaves as 'failClosed' (measured "
            "against the pinned image), but is still rejected here: an "
            "inherited default is not a reviewable decision. (The pinned "
            "image accepts exactly 'failClosed' or 'failOpen'.)"
        )


def test_the_guardrail_runs_on_tools_call_at_the_request_phase():
    """Only `tools/call`, and only before the call happens.

    `request` is the phase at which a refusal is still a refusal rather than a
    regret: `response` would let the payment execute and then withhold the
    receipt. Measured against the pinned image's validator, which rejects a
    reconstructed `Request` with "unknown variant `Request`, expected one of
    `off`, `request`, `response`, `full`" -- so the lowercase spelling here is
    a measurement, not a style choice, and `off` is a live way to disable the
    guardrail while leaving the block in place.
    """
    for where, processor in _guardrail_processors():
        methods = processor.get("methods") or {}
        assert methods.get("tools/call") == "request", (
            f"{where}.methods['tools/call'] is "
            f"{methods.get('tools/call')!r}; it must be 'request'. 'off' "
            "disables the decision entirely, 'response' decides after the "
            "money has moved, and the pinned image rejects any other spelling."
        )


def test_the_guardrail_is_an_external_remote_processor():
    """`kind: remote` is what makes this the PDS's decision rather than an
    in-process one. A `kind` naming any built-in processor would leave the
    block valid, the gateway running, and `platform/policy-decision-service`
    entirely uninvolved in the refusal it exists to make."""
    for where, processor in _guardrail_processors():
        assert processor.get("kind") == "remote", (
            f"{where}.kind is {processor.get('kind')!r}; it must be 'remote' "
            "-- the ExtMCP gRPC hook the PDS serves"
        )


def test_the_guardrail_points_at_the_pds_service_on_its_own_port():
    """The `host` must name the PDS Service, and the port must be the one the
    PDS actually listens on.

    Both halves are derived from the manifest rather than compared against a
    literal, so a rename or a port change fails here instead of producing a
    gateway that fail-closes on every request because it is dialling nothing.
    That failure mode is worth naming: under `failureMode: failClosed`, a
    *typo in this string* refuses every payment, and the refusal reads as the
    PDS being down rather than as a misconfiguration.
    """
    service = _pds_object("Service")
    expected_host = (
        f"{service['metadata']['name']}."
        f"{service['metadata']['namespace']}.svc.cluster.local:{PDS_PORT}"
    )
    for where, processor in _guardrail_processors():
        assert processor.get("host") == expected_host, (
            f"{where}.host is {processor.get('host')!r}; the PDS Service in "
            f"{PDS_MANIFEST.name} is reachable at {expected_host!r}"
        )
        # The flattened `SimpleBackendReferenceWithPolicies` permits exactly
        # one of three arms. Two set at once is a config the gateway rejects,
        # and `name`/`backend` silently resolving elsewhere is how a correct
        # `host` gets ignored.
        for other in ("name", "port", "backend"):
            assert other not in processor, (
                f"{where} sets both `host` and `{other}`; the backend "
                "reference permits exactly one of `host`, `name`+`port` and "
                "`backend`"
            )


def test_the_guardrail_hands_the_pds_an_already_verified_caller():
    """`metadata: {caller: 'jwt.sub'}` is the whole identity contract.

    `pds/identity.py` reads `metadata_context['caller']` and nothing else --
    it accepts the `headers` half of the request and documents that it does not
    look at it. So without this key the PDS receives no caller at all, every
    execute lands on `rules.decide`'s `unknown_caller` branch, and the platform
    refuses everything: a demo that looks like it is governing while actually
    being blind. The value is a CEL expression over the token `jwtAuth` has
    already verified, which is what makes the subject attested rather than
    claimed.
    """
    for where, processor in _guardrail_processors():
        metadata = processor.get("metadata") or {}
        assert metadata.get("caller") == "jwt.sub", (
            f"{where}.metadata['caller'] is {metadata.get('caller')!r}; it "
            "must be 'jwt.sub'. pds/identity.py reads the caller from "
            "metadata_context['caller'] and from nowhere else, so without "
            "this every execute_payment is denied as an unknown caller."
        )


def test_the_guardrail_forwards_trace_context_to_the_pds():
    """`requestHeaders.allowed` is a strict allowlist, and this guard exists
    because getting it wrong is silent and looks like success.

    Measured on this cluster, three ways (see the config's own comment for the
    three runs). With `allowed: ["authorization"]`, the policy decides
    perfectly -- the €6,200 is refused, the reason reaches the caller, the PDS
    logs the denial -- and `traceparent` never reaches the hook, so
    `pds/telemetry.context_from_headers` extracts an empty context and every
    `northstar.policy.decision` span becomes **a new root trace of its own**. Nothing
    logs an error. Jaeger lists the service. The only symptom is that the
    observability claim -- the decision in the same trace as the gateway's
    `tools/call` and the model's reasoning -- is false.

    That is why this is a config assertion and not only an e2e one: an e2e
    trace assertion catches it, but only when someone spends a real settlement
    to run it.
    """
    for where, processor in _guardrail_processors():
        allowed = (processor.get("requestHeaders") or {}).get("allowed")
        assert allowed, (
            f"{where}.requestHeaders.allowed is {allowed!r}. The default's "
            "contents are not measured by this repo; name what the hook needs "
            "explicitly -- at minimum `traceparent`, or the decision span "
            "starts a trace of its own"
        )
        lowered = {str(header).lower() for header in allowed}
        assert "traceparent" in lowered, (
            f"{where}.requestHeaders.allowed is {allowed!r} and omits "
            "`traceparent`. This list is EXHAUSTIVE, not additive: without it "
            "the PDS receives no trace context and every northstar.policy.decision span "
            "becomes an orphan root -- while the policy itself keeps working "
            "perfectly, which is what makes this worth a guard."
        )
        assert "authorization" not in lowered, (
            f"{where}.requestHeaders.allowed forwards `authorization`. It is "
            "inert while `preserveToken` is unset, but an allowlist is "
            "exactly where a later `preserveToken` turns an inert entry into "
            "a live bearer credential handed to a second service that "
            "documents never reading it."
        )


def test_the_gateway_still_does_not_forward_the_bearer_token():
    """Asserted rather than trusted to stay absent.

    `metadata: {caller: 'jwt.sub'}` above delivers an already-verified subject.
    Setting `jwtAuth.preserveToken: true` as well would forward a live bearer
    credential to a second service for no gain -- pds/identity.py would not
    read it. It is also a plausible edit: it is the first thing anyone
    reaches for when a downstream service seems to be missing an identity.
    """
    for where, route in _all_routes():
        jwt_auth = (route.get("policies") or {}).get("jwtAuth") or {}
        assert not jwt_auth.get("preserveToken"), (
            f"{where}.jwtAuth.preserveToken is set. The PDS is handed a "
            "verified subject through the guardrail's `metadata` block and "
            "reads nothing from the forwarded headers, so this only widens "
            "where a usable credential travels."
        )


# --- Governance must not regress authentication ----------------------------


def test_the_guardrail_is_a_sibling_of_jwt_auth_on_the_same_route():
    """The two policies must sit on the same route, in one `policies` block.

    Not a style point. A guardrail on a route whose `jwtAuth` lives somewhere
    else is a policy deciding on an unauthenticated `jwt.sub` -- which, since
    CEL over an absent token yields nothing, means every caller is unknown and
    everything is refused. Asserted as co-location rather than as two separate
    presence checks, because two presence checks pass when the blocks are on
    two different routes.
    """
    policies = _route_policies()
    assert "jwtAuth" in policies and "mcpGuardrails" in policies, (
        "jwtAuth and mcpGuardrails must both sit in the route's own "
        f"`policies` block; found {sorted(policies)}"
    )


def test_the_jwt_auth_policy_is_still_present_on_every_route():
    """Governance must not regress authentication.

    The guardrail decides *what* a caller may do; `jwtAuth` is what
    establishes *who* they are. Losing the latter makes the former decide on a
    claim nobody checked. Enumerated over every route: a second, anonymous
    route would satisfy any check that reads only the first.
    """
    for where, route in _all_routes():
        assert (route.get("policies") or {}).get("jwtAuth"), (
            f"{where} carries no jwtAuth policy -- an unauthenticated way in, "
            "and a route on which the guardrail would decide on an "
            "unverified identity"
        )


def test_every_route_that_exists_is_governed_by_the_guardrail():
    """The inversion that keeps holding as the config grows: not "a route is
    governed" but "every route is". A second route added without an
    `mcpGuardrails` block would be authenticated, traced, and completely
    ungoverned -- and would satisfy every content assertion above, which reads
    the route that does have one."""
    ungoverned = [
        f"{where}: policies={sorted((route.get('policies') or {})) or 'none at all'}"
        for where, route in _all_routes()
        if not ((route.get("policies") or {}).get("mcpGuardrails") or {}).get(
            "processors"
        )
    ]
    assert not ungoverned, (
        "every route on this gateway must carry an mcpGuardrails processor; "
        "these do not, and are an ungoverned way in:\n  "
        + "\n  ".join(ungoverned)
    )


# --- The PDS's own deployment ----------------------------------------------


def test_the_pds_has_its_own_service_account():
    """Without a dedicated ServiceAccount, SPIRE mints
    `spiffe://northstar.local/ns/northstar-platform/sa/default` -- an identity
    that verifies perfectly and identifies nothing.

    Both halves are checked: the object must exist, and the pod must actually
    use it. A ServiceAccount nothing references is an unused object, and a
    `serviceAccountName` naming one that does not exist keeps the pod out of
    Running altogether.
    """
    assert _deployment()["spec"]["template"]["spec"]["serviceAccountName"] == APP
    account = _pds_object("ServiceAccount")
    assert account["metadata"]["name"] == APP
    assert account["metadata"]["namespace"] == NAMESPACE


def test_the_pds_probes_are_tcp_socket_checks_on_its_grpc_port():
    """A TCP-socket probe, not a gRPC health check.

    `grpcio-health-checking` is deliberately outside this service's pinned
    dependency closure, so an `httpGet` or a `grpc` probe here would either
    fail every check or require a dependency that does not exist in the
    image.

    The probe is load-bearing under fail-closed in a way it would not be
    otherwise: a pod that never becomes Ready does not degrade this demo, it
    stops it -- and it stops it with a refusal indistinguishable, in the
    agent's reply, from a policy denial.
    """
    for container in _containers():
        for probe_name in ("readinessProbe", "livenessProbe"):
            probe = container.get(probe_name)
            assert probe, (
                f"container {container.get('name')!r} has no {probe_name}; "
                "under failureMode: failClosed an unready or wedged PDS "
                "refuses every payment, so both probes are wanted"
            )
            assert "grpc" not in probe and "httpGet" not in probe, (
                f"{probe_name} must be a tcpSocket check; a gRPC health "
                "check needs grpcio-health-checking, which is outside this "
                "service's pinned dependency closure"
            )
            assert (probe.get("tcpSocket") or {}).get("port") == PDS_PORT, (
                f"{probe_name} must probe TCP {PDS_PORT}; got {probe!r}"
            )


def test_the_container_port_the_service_and_the_guardrail_all_name_one_port():
    """One number, four places. The PDS listens on 9100 (`pds/server.py`'s
    `DEFAULT_PORT`, and its README publishes it); the container port, the
    Service port and targetPort, and the guardrail `host` must all agree. A
    mismatch here does not fail to start -- it fail-closes every request, and
    reads as a PDS outage rather than as a wrong number."""
    ports = {
        port.get("containerPort")
        for container in _containers()
        for port in container.get("ports") or []
    }
    assert ports == {PDS_PORT}, (
        f"the PDS container must expose exactly {PDS_PORT}; got {sorted(ports)}"
    )
    service_ports = _pds_object("Service")["spec"]["ports"]
    assert [(p.get("port"), p.get("targetPort")) for p in service_ports] == [
        (PDS_PORT, PDS_PORT)
    ], f"the PDS Service must expose {PDS_PORT} -> {PDS_PORT}; got {service_ports}"


def test_the_pds_exports_its_span_to_the_same_collector_as_everything_else():
    """The `northstar.policy.decision` span has to land in the *same trace* as the
    gateway's `tools/call` and the model's reasoning -- that is the milestone's
    observability claim, and it is the only reason a reader can tell a policy
    refusal from an outage. `pds/telemetry.py` reads the standard
    `OTEL_EXPORTER_OTLP_*` names and nothing of ours, so this manifest is the
    only place the endpoint is named; with the variables absent the SDK
    defaults to localhost and every decision span is dropped silently."""
    for container in _containers():
        env = {e["name"]: e.get("value") for e in container.get("env") or []}
        assert env.get("OTEL_SERVICE_NAME") == APP, (
            f"OTEL_SERVICE_NAME is {env.get('OTEL_SERVICE_NAME')!r}; the "
            f"trace, the contract and the docs all name {APP!r}"
        )
        endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or ""
        assert endpoint == (
            "http://otel-collector.observability.svc.cluster.local:4318"
        ), (
            f"OTEL_EXPORTER_OTLP_ENDPOINT is {endpoint!r}; the decision span "
            "must reach the same collector as every other service's, or it "
            "cannot appear in the same trace"
        )
        assert env.get("OTEL_EXPORTER_OTLP_PROTOCOL") == "http/protobuf", (
            "the PDS exports OTLP/HTTP on 4318 like every Python service "
            f"here; got {env.get('OTEL_EXPORTER_OTLP_PROTOCOL')!r}"
        )


def test_the_pds_image_follows_the_registry_convention():
    """`localhost:5001/northstar/<component>:dev`, so
    `scripts/rebuild-and-deploy.sh` rebuilds this component like the others,
    with `imagePullPolicy: Always` because the tag never changes and a cached
    layer would otherwise outlive the edit."""
    for container in _containers():
        assert container.get("image") == f"localhost:5001/northstar/{APP}:dev", (
            f"got {container.get('image')!r}"
        )
        assert container.get("imagePullPolicy") == "Always", (
            "the :dev tag is reused on every build, so a rebuilt image is "
            "ignored without Always"
        )


# --- Reachability: the PDS is not a service anything may call ---------------


def _pds_network_policy() -> dict:
    hits = [
        d
        for d in _docs(CILIUM_POLICIES)
        if d.get("kind") == "CiliumNetworkPolicy"
        and d.get("spec", {}).get("endpointSelector", {}).get("matchLabels", {}).get(
            "app"
        )
        == APP
    ]
    assert len(hits) == 1, (
        f"expected exactly one CiliumNetworkPolicy selecting app={APP}; found "
        f"{len(hits)}: {[d.get('metadata', {}).get('name') for d in hits]}"
    )
    return hits[0]


def test_the_pds_is_default_deny_for_ingress():
    """A policy service anything can call is a policy service anything can
    impersonate answers from. The deny is implicit in Cilium: an endpoint
    selected by a policy carrying an `ingress` section is default-deny for
    ingress and the rules are the allowlist -- so deleting the `ingress` key
    does not loosen this slightly, it removes protection entirely while
    leaving an object that still applies and still reports valid."""
    policy = _pds_network_policy()
    assert policy["apiVersion"] == "cilium.io/v2"
    assert policy["metadata"]["namespace"] == NAMESPACE, (
        "a CiliumNetworkPolicy is namespaced and its endpointSelector only "
        "matches endpoints in its own namespace; elsewhere it would apply "
        "cleanly and protect nothing"
    )
    assert policy["spec"].get("ingress"), (
        "the ingress block is what makes the PDS default-deny; without it "
        "this policy selects the endpoint and permits everything"
    )


def test_only_the_gateway_may_reach_the_pds_and_only_on_9100():
    """Exactly one workload, scoped by namespace as well as app label -- pod
    labels are not a security boundary on their own, and a Cilium
    `fromEndpoints` selector is not implicitly scoped to the policy's own
    namespace the way a core NetworkPolicy's `podSelector` is."""
    policy = _pds_network_policy()
    allowed = {
        (
            sel.get("matchLabels", {}).get(NAMESPACE_LABEL),
            sel.get("matchLabels", {}).get("app"),
        )
        for rule in policy["spec"]["ingress"]
        for sel in rule.get("fromEndpoints", [])
    }
    assert allowed == {(NAMESPACE, GATEWAY_APP)}, (
        f"exactly one workload may reach the PDS; got {sorted(allowed)}"
    )
    ports = {
        port.get("port")
        for rule in policy["spec"]["ingress"]
        if rule.get("fromEndpoints")
        for block in rule.get("toPorts", [])
        for port in block.get("ports", [])
    }
    assert ports == {str(PDS_PORT)}, (
        f"the gateway rule should be scoped to {PDS_PORT}; got {sorted(ports)}"
    )


def test_the_node_may_reach_the_pds_for_its_probes():
    """The guard whose absence would be actively misleading.

    kubelet dials the `tcpSocket` probe from the host, not from a pod. Drop
    the `host` entity and the probe is dropped, the pod never goes Ready, and
    because the guardrail is fail-closed **every payment is refused for the
    wrong reason** -- including the €5,800 control whose whole job is to prove
    the platform permits as well as refuses. The demo would show the €6,200
    denial and look correct. `remote-node` is asserted alongside it on the same
    reasoning the payments-mcp rule records: it costs nothing on a single-node
    cluster and is the difference between working and not on a multi-node one.
    """
    entities = {
        e
        for rule in _pds_network_policy()["spec"]["ingress"]
        for e in rule.get("fromEntities", [])
    }
    assert {"host", "remote-node"} <= entities, (
        "the PDS ingress policy must admit `host` and `remote-node`; got "
        f"{sorted(entities)}. Without `host`, kubelet's probe is dropped, "
        "the pod never becomes Ready, and fail-closed turns that into every "
        "payment refused."
    )


# --- Identity: the PDS is a registered workload ----------------------------


def test_the_pds_has_a_spire_registration_entry_with_the_controllers_class():
    """The one silent-failure mode in `registration.yaml`: the chart renders
    its controller-manager with `watchClassless: false`, so a ClusterSPIFFEID
    carrying no `className` is ignored outright -- `kubectl apply` succeeds,
    the CR's status stays empty, and no entry is ever created.

    The PDS does not fetch an SVID today (its pod carries no Workload API
    mount, by design: it decides on the *caller's* identity, handed to it by
    the gateway). The entry exists so this pod's attested identity names it
    rather than `sa/default`, which is the only reason its dedicated
    ServiceAccount is worth having.
    """
    crs = {
        d["metadata"]["name"]: d["spec"]
        for d in _docs(SPIRE_REGISTRATION)
        if d.get("kind") == "ClusterSPIFFEID"
    }
    name = f"northstar-{APP}"
    assert name in crs, (
        f"no ClusterSPIFFEID/{name}; the registered identities are "
        f"{sorted(crs)}"
    )
    spec = crs[name]
    assert spec.get("className") == "northstar-platform-spire", (
        f"ClusterSPIFFEID/{name} must set className; with the chart's "
        "watchClassless: false a classless CR is never reconciled"
    )
    assert spec.get("fallback") is False, (
        "the chart ships a catch-all `default` CR with fallback: true that "
        "matches the same pod; a fallback CR applies only when no "
        "non-fallback one does"
    )
    squashed = [" ".join(s.split()) for s in spec.get("workloadSelectorTemplates") or []]
    assert "k8s:ns:{{ .PodMeta.Namespace }}" in squashed
    assert "k8s:sa:{{ .PodSpec.ServiceAccountName }}" in squashed, (
        "the service account must be an attestation constraint, not "
        "decoration in the ID string"
    )
    assert spec["podSelector"]["matchLabels"]["app"] == APP
    assert (
        spec["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
        == NAMESPACE
    )


# --- The scripts actually deploy it -----------------------------------------


def test_the_pds_is_a_registered_rebuildable_component():
    """A component outside `rebuild-and-deploy.sh`'s COMPONENTS list is a
    component that silently keeps running last week's image after someone
    edits its source -- and this component's source is the policy.

    Asserted over the parsed triple rather than the substring, so a mention in
    a comment (this file's own COMPONENTS block carries several) cannot satisfy
    it, and so the build context and namespace are checked too: a triple with
    the wrong context builds the wrong image, and one with the wrong namespace
    fails the rollout.
    """
    text = REBUILD_AND_DEPLOY.read_text()
    body = text.split("COMPONENTS=(", 1)[1].split(")", 1)[0]
    triples = [
        line.strip().strip('"')
        for line in body.splitlines()
        if line.strip().startswith('"')
    ]
    assert f"{APP}:platform/{APP}:{NAMESPACE}" in triples, (
        f"{APP} is not registered in rebuild-and-deploy.sh's COMPONENTS; "
        f"found {triples}"
    )


def test_stage_two_deploys_the_pds_before_the_gateway_that_calls_it():
    """Ordering, over the comment-stripped script so a comment explaining the
    ordering cannot satisfy it.

    The gateway is fail-closed, so from the moment it starts until the PDS is
    Ready every `tools/call` is refused -- and refused with the gateway's own
    outage message rather than a policy reason. Nothing between the two blocks
    drives a settlement today, so neither order strands a real request; this
    order means the window does not exist if a future step ever does, and it
    fails in the right place, at the PDS's own rollout, rather than presenting
    later as a gateway that refuses everything.

    Matched as a *line that names both the deploy script and the component*,
    rather than any line naming the component: the block's own success
    `echo` also names the component, and comment-stripping does not help
    there since an `echo` is code, so a looser match would be satisfied by
    output text that deploys nothing.
    """
    script = _effective_script()
    invocations = [
        line
        for line in script.splitlines()
        if "rebuild-and-deploy.sh" in line and APP in line
    ]
    assert invocations, (
        "setup-kind.sh --stage 2 must hand policy-decision-service to "
        "scripts/rebuild-and-deploy.sh; the gateway is fail-closed and "
        "refuses every payment without a PDS. (An echo naming the component "
        "does not count and is not looked at.)"
    )
    pds = script.find(invocations[0])
    gateway = script.find("agentgateway/deployment.yaml")
    assert gateway != -1
    assert pds < gateway, (
        "the PDS must be deployed before the gateway that calls it"
    )
    preceding = script[:pds]
    assert '"${STAGE}" -ge 2' in preceding, (
        "the PDS deploy must sit inside a stage-2 guard: northstar-platform "
        "does not exist at stage 1, so the apply would fail the whole script"
    )


def test_stage_two_restarts_the_gateway_after_updating_its_configmap():
    """The failure this prevents looks exactly like success.

    This config is mounted as a volume and agentgateway reads it once at
    startup with `-f`. `kubectl create configmap --dry-run | kubectl apply`
    updates the object and reports success while the running pod goes on
    serving the old, policy-free config -- so the €6,200 would be paid out with
    the committed file and the live ConfigMap in perfect agreement, and the
    obvious diagnostic (`kubectl get configmap -o yaml`) would show the new
    policy present and correct.
    """
    script = _effective_script()
    configmap = script.find("agentgateway-config")
    restart = script.find("rollout restart deployment/agentgateway")
    status = script.find("rollout status deployment/agentgateway")
    assert configmap != -1 and status != -1
    assert restart != -1, (
        "setup-kind.sh must restart the gateway after writing its ConfigMap: "
        "the config is a mounted volume read once at startup, so an updated "
        "ConfigMap alone leaves the old policy running"
    )
    assert configmap < restart < status, (
        "the restart must come after the ConfigMap is written and before the "
        f"rollout is waited on; got configmap={configmap}, restart={restart}, "
        f"status={status}"
    )
