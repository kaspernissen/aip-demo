"""Guards the model path: agents call the model through agentgateway's `llm` listener.

The agents hold no model key. `ANTHROPIC_BASE_URL` points them at the gateway's
llm listener, which verifies the same JWT-SVID as the MCP route, admits only the
two model agents' identities, holds the key and forwards to the provider. Every
property below is one that fails silently if it drifts: a listener with a
weaker `jwtAuth` than the MCP route is an anonymous way to spend the key, an
`authorization` that grew a third identity widens it without a test saying so,
and an agent manifest that gained a real key (or lost the base URL) would go
straight to the provider again, past the gateway.

`tests/unit/test_spire_config.py` walks `binds[].listeners[].routes[]` only, so
it never sees the top-level `llm:` block. These tests cover that block; the
agreement of its audience with the rest of the repo is asserted here, against
the same constants.

Every check parses YAML (comments are discarded before anything is asserted).
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_CONFIG = REPO_ROOT / "deploy" / "platform" / "agentgateway" / "config.yaml"
GATEWAY_DEPLOYMENT = REPO_ROOT / "deploy" / "platform" / "agentgateway" / "deployment.yaml"
SPIRE_VALUES = REPO_ROOT / "deploy" / "platform" / "spire" / "values.yaml"
AGENT_MANIFESTS = {
    "claims-agent": REPO_ROOT / "deploy" / "k8s" / "claims-agent.yaml",
    "settlement-agent": REPO_ROOT / "deploy" / "k8s" / "settlement-agent.yaml",
}

TRUST_DOMAIN = "northstar.local"
GATEWAY_SPIFFE_ID = f"spiffe://{TRUST_DOMAIN}/ns/northstar-platform/sa/agentgateway"
MODEL_AGENT_IDS = {
    f"spiffe://{TRUST_DOMAIN}/ns/northstar-agents/sa/claims-agent",
    f"spiffe://{TRUST_DOMAIN}/ns/northstar-agents/sa/settlement-agent",
}
GATEWAY_URL = "http://agentgateway.northstar-platform.svc.cluster.local:4000"
LLM_PORT = 4000
MODEL = "claude-sonnet-5"


def _docs(path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _llm():
    llm = yaml.safe_load(GATEWAY_CONFIG.read_text()).get("llm")
    assert llm, "the gateway config has no top-level `llm:` block"
    return llm


def _mcp_jwt_blocks():
    config = yaml.safe_load(GATEWAY_CONFIG.read_text())
    blocks = [
        route["policies"]["jwtAuth"]
        for bind in config["binds"]
        for listener in bind["listeners"]
        for route in listener["routes"]
    ]
    assert blocks
    return blocks


def _agent_containers(app):
    for d in _docs(AGENT_MANIFESTS[app]):
        if d["kind"] == "Deployment":
            return d["spec"]["template"]["spec"]["containers"]
    raise AssertionError(f"no Deployment in {AGENT_MANIFESTS[app]}")


def _env(app):
    """Every env entry of every container in the agent's pod, by name."""
    found = {}
    for container in _agent_containers(app):
        for entry in container.get("env") or []:
            found[entry["name"]] = entry
    return found


# --- the listener's authentication and authorization --------------------------


def test_the_llm_listener_is_on_port_4000():
    assert _llm()["port"] == LLM_PORT


def test_the_llm_jwt_policy_is_identical_to_the_mcp_routes():
    """Same issuer, audience and JWKS, and strict. Not 'similar': a model call
    must be as hard to forge as a tool call, and a different audience would be
    a token the agents are not minting."""
    llm_auth = _llm()["policies"]["jwtAuth"]
    for mcp_auth in _mcp_jwt_blocks():
        assert llm_auth == mcp_auth


def test_the_llm_jwt_policy_trusts_the_issuer_spire_mints_and_the_gateways_own_audience():
    issuer = yaml.safe_load(SPIRE_VALUES.read_text())["global"]["spire"]["jwtIssuer"]
    auth = _llm()["policies"]["jwtAuth"]
    assert auth["mode"] == "strict", "'optional' admits a call with no token at all"
    assert auth["issuer"] == issuer
    assert auth["audiences"] == [GATEWAY_SPIFFE_ID]
    assert auth["jwks"] == {"url": f"{issuer}/keys"}, "a jwks.file would go stale on rotation"


def test_the_llm_authorization_admits_exactly_the_two_model_agents():
    rules = _llm()["policies"]["authorization"]["rules"]
    assert len(rules) == 1 and set(rules[0]) == {"allow"}, rules
    expression = rules[0]["allow"]
    # One membership test on the verified subject, over exactly two literals.
    assert expression.startswith("jwt.sub in [") and expression.endswith("]"), expression
    listed = {
        part.strip().strip('"')
        for part in expression[len("jwt.sub in ["):-1].split(",")
    }
    assert listed == MODEL_AGENT_IDS, listed


def test_no_other_policy_is_attached_to_the_llm_listener():
    """A policy not named here is a decision no test describes."""
    assert set(_llm()["policies"]) == {"jwtAuth", "authorization"}


def test_the_model_allowlist_is_exactly_one_model_and_the_key_is_a_mounted_file():
    models = _llm()["models"]
    assert [m["name"] for m in models] == [MODEL]
    for model in models:
        assert model["provider"] == "anthropic"
        key = model["params"]["apiKey"]
        assert set(key) == {"file"}, "the key must be read from the mounted Secret, never inlined"
        assert key["file"] == "/etc/agentgateway-model/ANTHROPIC_API_KEY"


# --- the gateway's deployment carries the port and the key --------------------


def _gateway_pod_and_service():
    docs = _docs(GATEWAY_DEPLOYMENT)
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    service = next(d for d in docs if d["kind"] == "Service")
    return deployment["spec"]["template"]["spec"], service


def test_the_gateway_exposes_port_4000_on_the_container_and_the_service():
    pod, service = _gateway_pod_and_service()
    container_ports = {p["containerPort"] for c in pod["containers"] for p in c.get("ports", [])}
    assert LLM_PORT in container_ports
    assert LLM_PORT in {p["port"] for p in service["spec"]["ports"]}


def test_the_gateway_mounts_the_model_secret_where_the_config_reads_it():
    pod, _ = _gateway_pod_and_service()
    secrets = [v for v in pod["volumes"] if v.get("secret", {}).get("secretName") == "northstar-model"]
    assert len(secrets) == 1
    mounts = [
        m for c in pod["containers"] for m in c.get("volumeMounts", [])
        if m["name"] == secrets[0]["name"]
    ]
    assert [m["mountPath"] for m in mounts] == ["/etc/agentgateway-model"]
    assert all(m.get("readOnly") for m in mounts)


# --- the agents point at the gateway and hold no key --------------------------


def test_each_agent_sends_its_model_calls_to_the_gateway():
    for app in AGENT_MANIFESTS:
        env = _env(app)
        assert env["ANTHROPIC_BASE_URL"].get("value") == GATEWAY_URL, app


def test_no_agent_holds_a_model_key():
    """The key is a placeholder the gateway overwrites: a literal value, never a
    secretKeyRef, and no `envFrom` that could pull one in either."""
    for app in AGENT_MANIFESTS:
        key = _env(app)["ANTHROPIC_API_KEY"]
        assert key.get("value") == "held-by-agentgateway" and "valueFrom" not in key, app
        for container in _agent_containers(app):
            assert "envFrom" not in container, app
            for entry in container.get("env") or []:
                secret_ref = (entry.get("valueFrom") or {}).get("secretKeyRef") or {}
                assert secret_ref.get("name") != "northstar-model", (app, entry["name"])


def test_each_agent_asks_for_a_token_scoped_to_the_gateways_own_identity():
    """The audience both agents mint for is the one the llm jwtAuth checks, and
    both have the Workload API socket (the CSI volume) to mint it from."""
    for app in AGENT_MANIFESTS:
        env = _env(app)
        assert env["NORTHSTAR_GATEWAY_AUDIENCE"].get("value") == GATEWAY_SPIFFE_ID, app
        assert "SPIFFE_ENDPOINT_SOCKET" in env, app
        for d in _docs(AGENT_MANIFESTS[app]):
            if d["kind"] == "Deployment":
                pod = d["spec"]["template"]["spec"]
                assert any(v.get("csi", {}).get("driver") == "csi.spiffe.io" for v in pod["volumes"]), app
    assert _llm()["policies"]["jwtAuth"]["audiences"] == [GATEWAY_SPIFFE_ID]
