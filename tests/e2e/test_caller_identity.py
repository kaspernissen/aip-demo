"""Proves the JWT-SVID verification on Hop 3 has teeth: identity decides.

This platform's own line is *"the model can request; the platform decides if
it becomes an effect."* It knows who is calling — SPIRE issues
`settlement-agent` a JWT-SVID, agentgateway verifies it, and the verified
subject lands on the gateway's SERVER span as `jwt.sub`
(`tests/e2e/test_gateway_trace.py`). None of that, on its own, proves the
verification is load-bearing: a gateway that recorded `jwt.sub` from any token
it was handed, or that forwarded regardless, would satisfy every one of those
assertions. This file is the negative half.

**Why the wrong-audience case is the one that matters, and why "no token" is
not enough on its own.** A no-token refusal only proves `mode: strict` is in
force. It says nothing about whether the *contents* of a presented token are
checked, so a gateway that accepted any syntactically valid JWT would pass it.
The interesting property is the one `deploy/platform/agentgateway/config.yaml`
claims in prose — that scoping `audiences` to the gateway's own SPIFFE ID stops
a token minted for some *other* SPIFFE service in the same trust domain from
being replayed here. `wrong_audience_svid` below is exactly that token: minted
by the real SPIRE server, signed by the real JWT signing key SPIRE publishes in
its JWKS, carrying the real issuer and the real `settlement-agent` subject,
with the audience — and only the audience — pointing somewhere else.

**What this file deliberately does not claim.** It does not claim to exercise
agentgateway's `iss` *string comparison* in isolation. With the deployed
single-issuer `jwtAuth` config that check is not independently reachable, and
the reason is in the code rather than in this project's choices:
`validate_claims` (agentgateway v1.5.0, `crates/agentgateway/src/http/jwt.rs`)
looks the signing key up by the token header's `kid` and only then calls
`jsonwebtoken::decode`, which verifies the signature before validating `iss`
and `aud`. So a token bearing an untrusted issuer is refused for the reason it
*should* be refused — this gateway does not trust that issuer's keys — one or
two steps before the `iss` string is ever compared. Both crafted forms are
asserted below, and each names the layer that actually rejects it.

**Cost: no model call.** Unlike the other live-cluster files here, nothing in
this one drives `settlement-agent` or spends Anthropic tokens. It speaks MCP to
the gateway directly, which is also the *stronger* test — routing through the
agent would exercise the agent's error handling rather than the gateway's
verification. Consequently this file is **not** behind `RUN_BROWSER_TESTS=1`;
it needs the live `kind-northstar` cluster the same way
`tests/e2e/test_payments_api_trace.py` does, and like that file it fails rather
than skips when the cluster is not there.

**One test here is not about identity at all:**
`test_a_jsonrpc_batch_is_rejected_before_the_policy_hook_and_changes_nothing`
is the regression guard `docs/upstream-findings.md`'s OQ-U1 says it lacks. It
lives in this file because it reuses exactly this file's machinery — the
gateway port-forward, the SPIRE minting, `_post`/`_json_body`, and the
no-model-tokens, not-behind-`RUN_BROWSER_TESTS=1` property — and building a
second copy of that dance elsewhere is the thing this repo's own conventions
argue against. See that test's own docstring for what it asserts and why.

**Port-forward ownership.** The four forwards this repo manages —
19001/19002/18000/18080, started and verified by `scripts/setup-kind.sh` — are
none of this file's business and are never touched. The gateway has no
repo-managed forward, so `gateway_url` starts one of its own and asks
`kubectl` for an *ephemeral* local port (the bare `:3000` form), which cannot
collide with a repo-managed port by construction. It is terminated in teardown.

**Credential hygiene.** Every token here is minted at runtime and none is
committed, printed or logged. Failure messages carry claims (`sub`, `aud`,
`iss`) and never the token: `_claims()` decodes the payload without verifying
anything, precisely so an assertion can say *which identity* was presented
without putting a usable bearer credential into pytest output.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field

import httpx
import jwt as pyjwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.harness.jaeger import JaegerClient

KUBE_CONTEXT = "kind-northstar"
GATEWAY_NAMESPACE = "northstar-platform"
SPIRE_SERVER_POD = "spire-server-0"

# The audience settlement-agent's real SVIDs are minted for, and the single
# entry in the gateway's `jwtAuth.audiences` list. One string, both ends of the
# check -- `tests/unit/test_stage2_spire_config.py` pins that the configuration
# files agree on it; this file uses it as the *correct* value a wrong one is
# measured against.
GATEWAY_AUDIENCE = "spiffe://northstar.local/ns/northstar-platform/sa/agentgateway"

# A different, equally real SPIFFE ID in the same trust domain. A token minted
# for this audience is a token minted for payments-mcp, not for the gateway --
# the replay this audience scoping exists to stop.
OTHER_AUDIENCE = "spiffe://northstar.local/ns/northstar-tools/sa/payments-mcp"

CALLER_SPIFFE_ID = "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"

# What SPIRE actually mints as `iss`, rendered by the chart from the single
# `global.spire.jwtIssuer` value and matched exactly by the gateway's
# `jwtAuth.issuer`.
SPIRE_ISSUER = (
    "http://spire-spiffe-oidc-discovery-provider.northstar-platform.svc.cluster.local"
)

UNTRUSTED_ISSUER = "http://issuer.invalid"

MCP_PROTOCOL_VERSION = "2025-06-18"

# agentgateway v1.5.0 answers a jwtAuth rejection with this status and a
# `text/plain` body, measured -- see `_Outcome.refused`.
UNAUTHORIZED = 401


def _kubectl(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _claims(token: str) -> dict:
    """The token's payload, decoded and **not** verified.

    Verification is the gateway's job and is the thing under test; this exists
    only so an assertion message can name the identity that was presented
    without printing a usable credential.
    """
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


@dataclass
class _Outcome:
    """The normalised result of one direct MCP call through the gateway.

    `refused` means *the gateway rejected the caller's identity* — not merely
    "something non-2xx happened". That distinction is the whole point of this
    dataclass, and getting it wrong would make every test in this file pass
    vacuously: a bare `tools/call` with a perfectly valid token also returns a
    non-2xx status (measured: `400 mcp: session header is required for
    non-initialize requests`), so a `refused = not ok` definition would report
    the accepted path as refused too and the suite would go green on a gateway
    with its `jwtAuth` policy deleted.

    So `refused` is true for exactly two shapes, and both are handled because
    only one of them was measured:

    * **HTTP 401 with a `text/plain` body.** What agentgateway v1.5.0 actually
      does, in every rejection case measured here. `TokenError`'s
      `Display` text is the whole body (`crates/agentgateway/src/http/jwt.rs`),
      e.g. `authentication failure: no bearer token found`.
    * **A JSON-RPC error object riding on a non-2xx status.** A plausible
      shape for a JSON-RPC-framed rejection, but *not* what the deployed
      gateway produces for any identity rejection — the refusal never
      reaches JSON-RPC framing at all, because `jwtAuth` runs as an HTTP
      filter before the MCP backend is consulted. Handled anyway rather than
      dropped: if a later version moves the rejection into the MCP layer, these
      tests should keep asserting the property instead of silently inverting.
    """

    http_status: int
    body: str
    stage: str
    jsonrpc_error: dict | None = None
    tool_result: dict | None = None
    claims: dict = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        if self.http_status == UNAUTHORIZED:
            return True
        return self.jsonrpc_error is not None and self.http_status >= 400

    def __repr__(self) -> str:  # noqa: D105 -- assertion messages only
        return (
            f"<call {self.stage}: HTTP {self.http_status} body={self.body[:200]!r} "
            f"jsonrpc_error={self.jsonrpc_error!r} claims={self.claims!r}>"
        )


def _post(url: str, payload: dict, headers: dict) -> httpx.Response:
    return httpx.post(
        f"{url}/mcp",
        json=payload,
        headers={
            "content-type": "application/json",
            # Streamable HTTP requires both; the gateway answers `initialize`
            # as an SSE event stream, which `_json_body` unwraps.
            "accept": "application/json, text/event-stream",
            **headers,
        },
        timeout=30,
    )


def _json_body(response: httpx.Response) -> dict | None:
    """The JSON-RPC message out of either an `application/json` or an SSE body.

    agentgateway returns MCP responses as `text/event-stream` (measured), so a
    bare `response.json()` raises on the accepted path. Returns None when the
    body is not JSON-RPC at all, which is what a `text/plain` refusal is.
    """
    text = response.text
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[len("data:") :].strip())
            except json.JSONDecodeError:
                return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def call_tool_directly(
    gateway_url: str, tool: str, arguments: dict, headers: dict
) -> _Outcome:
    """Speak MCP to the gateway's `/mcp` and normalise the outcome.

    Deliberately performs the **full** streamable-HTTP sequence — `initialize`,
    then `tools/call` with the session id the gateway hands back — rather than
    firing a bare `tools/call`. Two reasons, and the second is the load-bearing
    one:

    1. A bare `tools/call` cannot succeed at all (no session header), so a test
       built on it could never distinguish "refused" from "accepted".
    2. `initialize` passes through the same route and therefore the same
       `jwtAuth` policy (measured: it 401s identically without a token). So an
       identity that is refused never gets a session, and an identity that is
       accepted goes on to really execute the tool. `refused` then means
       "could not open an MCP session through the gateway", and its negation is
       witnessed by a real tool result rather than by the absence of an error.
    """
    claims = {}
    authorization = headers.get("authorization") or headers.get("Authorization")
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        try:
            claims = _claims(token)
        except Exception:  # noqa: BLE001 -- a deliberately malformed token
            claims = {"<undecodable>": True}

    init = _post(
        gateway_url,
        {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "identity-probe", "version": "0"},
            },
        },
        headers,
    )
    init_body = _json_body(init)
    if init.status_code != 200 or init_body is None or "result" not in init_body:
        return _Outcome(
            http_status=init.status_code,
            body=init.text,
            stage="initialize",
            jsonrpc_error=(init_body or {}).get("error"),
            claims=claims,
        )

    session_id = init.headers.get("mcp-session-id")
    assert session_id, (
        "the gateway accepted `initialize` but returned no `mcp-session-id` "
        f"header, so this probe cannot continue: headers={dict(init.headers)!r}"
    )

    called = _post(
        gateway_url,
        {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        {**headers, "mcp-session-id": session_id},
    )
    body = _json_body(called)
    return _Outcome(
        http_status=called.status_code,
        body=called.text,
        stage="tools/call",
        jsonrpc_error=(body or {}).get("error"),
        tool_result=(body or {}).get("result"),
        claims=claims,
    )


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
def gateway_url():
    """A port-forward to `svc/agentgateway`, owned and cleaned up by this file.

    `:3000` (no local port) makes `kubectl` choose a free ephemeral port and
    print it, which is why this cannot shadow one of the four repo-managed
    forwards even by accident. The chosen port is parsed out of kubectl's own
    "Forwarding from 127.0.0.1:<port> -> 3000" line rather than guessed.
    """
    proc = subprocess.Popen(
        [
            "kubectl",
            "--context",
            KUBE_CONTEXT,
            "-n",
            GATEWAY_NAMESPACE,
            "port-forward",
            "svc/agentgateway",
            ":3000",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    port = None
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            match = re.search(r"Forwarding from 127\.0\.0\.1:(\d+) -> 3000", line)
            if match:
                port = int(match.group(1))
                break
        assert port, (
            "could not establish a port-forward to svc/agentgateway in "
            f"{GATEWAY_NAMESPACE} -- is the kind-northstar cluster up and the "
            "gateway deployed? (this file needs the live cluster, and fails "
            "rather than skipping when it is absent)"
        )
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def mint_svid():
    """Mint a real JWT-SVID for any (subject, audience) pair, via SPIRE itself.

    `spire-server jwt mint` is used rather than the Workload API on purpose:
    the Workload API only ever answers with the identity of whoever is calling
    it, so it *cannot* produce the wrong-audience-but-otherwise-genuine token
    this file needs. Minting server-side keeps everything else about the token
    real — same signing key, same `kid`, same `iss`, same subject SPIRE would
    attest for settlement-agent — so the only thing wrong with it is the one
    thing under test.
    """

    def _mint(audience: str, subject: str = CALLER_SPIFFE_ID) -> str:
        result = _kubectl(
            "-n",
            GATEWAY_NAMESPACE,
            "exec",
            SPIRE_SERVER_POD,
            "-c",
            "spire-server",
            "--",
            "/opt/spire/bin/spire-server",
            "jwt",
            "mint",
            "-spiffeID",
            subject,
            "-audience",
            audience,
            "-output",
            "json",
        )
        assert result.returncode == 0, (
            "`spire-server jwt mint` failed, so this file cannot build the "
            f"tokens it asserts on:\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
        # Deliberately not logged: the value is a live bearer credential.
        return json.loads(result.stdout)["svid"]["token"]

    return _mint


@pytest.fixture(scope="module")
def valid_svid(mint_svid):
    return mint_svid(GATEWAY_AUDIENCE)


@pytest.fixture(scope="module")
def wrong_audience_svid(mint_svid):
    return mint_svid(OTHER_AUDIENCE)


@pytest.fixture(scope="module")
def self_signed_tokens():
    """Two well-formed JWTs signed by a key SPIRE has never heard of.

    The keypair is generated in-process on every run and never leaves it, so
    nothing signable is committed anywhere. Both tokens carry the *correct*
    audience and the *correct* subject and differ from a genuine SVID only in
    who signed them and what `iss` says — which is what makes them a test of
    trust rather than a test of well-formedness.

    `own_kid` names a key id of its own; `spire_kid` reuses the `kid` SPIRE
    currently publishes, so the gateway's key lookup succeeds and the rejection
    has to come from signature verification instead. The two exist separately
    because they exercise two different rejection paths in
    `Jwt::validate_claims`, and a single token could only ever show one.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    claims = {
        "sub": CALLER_SPIFFE_ID,
        "aud": [GATEWAY_AUDIENCE],
        "iss": UNTRUSTED_ISSUER,
        "iat": now,
        "exp": now + 3600,
    }
    return {
        "own_kid": pyjwt.encode(
            claims, key, algorithm="RS256", headers={"kid": "untrusted-key"}
        ),
        "spire_kid": pyjwt.encode(
            claims, key, algorithm="RS256", headers={"kid": _spire_jwks_kid()}
        ),
    }


def _spire_jwks_kid() -> str:
    """The `kid` SPIRE currently publishes, read live from its own JWKS.

    Read rather than hardcoded because SPIRE rotates its JWT signing key on
    its own schedule; a pinned value here would turn a routine rotation into a
    test that quietly stops exercising the path it names.
    """
    result = _kubectl(
        "-n",
        GATEWAY_NAMESPACE,
        "exec",
        SPIRE_SERVER_POD,
        "-c",
        "spire-server",
        "--",
        "/opt/spire/bin/spire-server",
        "jwt",
        "mint",
        "-spiffeID",
        CALLER_SPIFFE_ID,
        "-audience",
        "spiffe://northstar.local/kid-probe",
        "-output",
        "json",
    )
    assert result.returncode == 0, (
        f"could not mint a token to read SPIRE's current `kid` from:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    token = json.loads(result.stdout)["svid"]["token"]
    header = token.split(".")[0]
    header += "=" * (-len(header) % 4)
    kid = json.loads(base64.urlsafe_b64decode(header)).get("kid")
    assert kid, "SPIRE minted a JWT-SVID with no `kid` in its header"
    return kid


# --- the accepted path, which every refusal below is measured against -------


def test_a_valid_svid_is_accepted_and_the_tool_really_runs(gateway_url, valid_svid):
    """The must-stay-green control, and it is not optional.

    Without it every refusal assertion in this file is satisfiable by a broken
    probe — a typo in the path, the wrong port, a gateway that is down, a
    `jwtAuth` policy that rejects *everything*. All of those refuse all four
    tokens, and this project has shipped that exact defect class before: a
    guard whose green and red both mean the same thing.

    So this asserts the positive: a genuine SPIRE-minted JWT-SVID for the
    gateway's own audience opens an MCP session **and** executes
    `propose_payment` through the whole interposed path, gateway to
    payments-mcp to payments-api.

    payments-api's store is in-memory and every probe adds a row, so
    nothing here asserts a record count or a payment id — only that *this*
    call's own result names the claim it was given and came back `proposed`.
    """
    claim_id = f"CLM-IDENTITY-{uuid.uuid4().hex[:8]}"
    result = call_tool_directly(
        gateway_url,
        "propose_payment",
        {"claim_id": claim_id, "amount": 100, "currency": "EUR"},
        headers={"authorization": f"Bearer {valid_svid}"},
    )

    assert not result.refused, (
        "the gateway refused a genuine SPIRE-minted JWT-SVID for its own "
        f"audience, so every refusal assertion in this file is meaningless "
        f"until this is fixed: {result!r}"
    )
    assert result.http_status == 200, result
    assert result.tool_result is not None, result
    assert result.tool_result.get("isError") is False, result
    structured = result.tool_result.get("structuredContent") or {}
    assert structured.get("claimId") == claim_id, result
    assert structured.get("status") == "proposed", result

    # The token that opened this session is the caller's own attested identity,
    # not the gateway's and not `.../sa/default`.
    assert result.claims.get("sub") == CALLER_SPIFFE_ID, result.claims
    assert result.claims.get("iss") == SPIRE_ISSUER, result.claims


# --- the five refusals (4 functions; the untrusted-issuer one is
# parametrised x2, for own_kid and spire_kid) ---------------------------------


def test_gateway_refuses_a_call_with_no_svid_at_all(gateway_url):
    """Identity has teeth: without a JWT-SVID the gateway does not forward.

    This is the property the policy decision service's decisions rest on — a
    PDS that trusts the caller identity is only as good as the gateway's
    verification of it.

    It is `mode: strict` that makes this true, and `strict` is a deliberate
    override: agentgateway v1.5.0's `Mode` enum marks `Optional` as its
    `#[default]`, documented as "allows requests without a JWT". Left at the
    default this test would fail and the platform would know the identity only
    of callers that volunteered one.
    """
    result = call_tool_directly(
        gateway_url,
        "propose_payment",
        {"claim_id": "CLM-IDENTITY-TEST", "amount": 100},
        headers={},  # no token at all
    )
    assert result.refused, f"gateway forwarded an unauthenticated call: {result!r}"
    assert result.tool_result is None, (
        f"the gateway refused but a tool result came back anyway: {result!r}"
    )


def test_gateway_refuses_a_genuine_svid_minted_for_another_audience(
    gateway_url, wrong_audience_svid
):
    """The audience check, isolated — this file's core claim.

    Everything about this token is real: SPIRE minted it, SPIRE's current JWT
    signing key signed it, its `kid` is the one SPIRE publishes in the JWKS the
    gateway fetches, its `iss` is the issuer the gateway is configured with, and
    its `sub` is settlement-agent's own attested SPIFFE ID. Exactly one field
    differs from the token the agent really sends: `aud` names payments-mcp's
    SPIFFE ID instead of the gateway's.

    So a refusal here cannot be explained by malformedness, by an untrusted
    signer, or by an expired token — the only remaining explanation is the
    `audiences` list in `deploy/platform/agentgateway/config.yaml`. That is
    what makes this the test of the property that file claims in prose: a token
    issued for one SPIFFE service in this trust domain cannot be replayed at
    another.

    The claims are asserted before the call, not merely commented, because a
    minting change that quietly produced the *right* audience would otherwise
    turn this into a second copy of the accepted-path test wearing a refusal's
    name.
    """
    claims = _claims(wrong_audience_svid)
    assert claims.get("aud") == [OTHER_AUDIENCE], (
        "this test is only meaningful if the token's audience is the wrong "
        f"one; SPIRE minted aud={claims.get('aud')!r}"
    )
    assert claims.get("iss") == SPIRE_ISSUER, (
        "the wrong-audience token must be right about everything else, "
        f"including the issuer; got iss={claims.get('iss')!r}"
    )
    assert claims.get("sub") == CALLER_SPIFFE_ID, (
        f"expected the caller's own subject; got sub={claims.get('sub')!r}"
    )

    result = call_tool_directly(
        gateway_url,
        "propose_payment",
        {"claim_id": "CLM-IDENTITY-TEST", "amount": 100},
        headers={"authorization": f"Bearer {wrong_audience_svid}"},
    )
    assert result.refused, (
        "the gateway accepted a SPIRE-minted token whose audience names a "
        "different SPIFFE service -- `jwtAuth.audiences` is not being enforced, "
        f"so any token from anywhere in this trust domain is usable here: "
        f"{result!r}"
    )
    assert result.tool_result is None, (
        f"the gateway refused but a tool result came back anyway: {result!r}"
    )


@pytest.mark.parametrize(
    "variant",
    [
        # The realistic forgery: an attacker mints their own token. Refused at
        # key lookup -- `TokenError::UnknownKeyId`.
        "own_kid",
        # The same forgery wearing SPIRE's key id, so key lookup succeeds and
        # the rejection must come from signature verification instead.
        "spire_kid",
    ],
)
def test_gateway_refuses_a_well_formed_token_from_an_untrusted_issuer(
    gateway_url, self_signed_tokens, variant
):
    """A structurally perfect JWT that SPIRE did not sign is refused.

    Both variants are well-formed RS256 JWTs with an unexpired `exp`, the
    correct `aud`, the correct `sub`, and an `iss` the gateway is not
    configured for. They are parametrised rather than merged because they
    reach two different rejection points in `Jwt::validate_claims`
    (agentgateway v1.5.0, `crates/agentgateway/src/http/jwt.rs`) and a single
    token could only ever demonstrate one of them.

    **The honest scope of this test, stated so a reader does not over-read
    it.** It proves the wrong-issuer token is refused. It does *not* prove the
    `iss` string comparison fired: that code path validates the signature
    before it validates claims, and looks the key up by `kid` before either, so
    with one configured issuer an untrusted `iss` is always caught earlier — by
    key trust, which is the check that actually matters. The `iss` comparison
    becomes independently reachable only in a multi-issuer (`providers`)
    configuration, which this project does not deploy.
    """
    token = self_signed_tokens[variant]
    claims = _claims(token)
    assert claims.get("iss") == UNTRUSTED_ISSUER, claims
    assert claims.get("aud") == [GATEWAY_AUDIENCE], (
        "this token must be right about the audience, so that a refusal is "
        f"attributable to the issuer/signature and not to `aud`: {claims!r}"
    )

    result = call_tool_directly(
        gateway_url,
        "propose_payment",
        {"claim_id": "CLM-IDENTITY-TEST", "amount": 100},
        headers={"authorization": f"Bearer {token}"},
    )
    assert result.refused, (
        f"gateway accepted a token signed by a key SPIRE never issued "
        f"({variant}): {result!r}"
    )
    assert result.tool_result is None, (
        f"the gateway refused but a tool result came back anyway: {result!r}"
    )


def test_gateway_refuses_a_bearer_value_that_is_not_a_jwt(gateway_url):
    """The degenerate case, kept because it discriminates a real mutation.

    `mode` has three values in agentgateway v1.5.0, and they fail these tests
    in different combinations — which is what makes keeping both this test and
    the no-token one above worthwhile rather than redundant:

    * `strict` (deployed) — refuses a missing token *and* an invalid one. All
      of this file's refusal tests pass.
    * `optional` (the enum's own `#[default]`) — refuses an invalid token but
      permits a missing one. Only the no-token test above goes red.
    * `permissive` — permits both. This test goes red too, and it is the only
      one of the two that does.

    Both non-strict modes turn the no-token test red, so on its own that test
    says only "mode is no longer strict". It takes this one to say *which* way,
    and that matters: `optional` still checks every token it is handed, while
    `permissive` checks none.
    """
    result = call_tool_directly(
        gateway_url,
        "propose_payment",
        {"claim_id": "CLM-IDENTITY-TEST", "amount": 100},
        headers={"authorization": "Bearer not-a-real-svid"},
    )
    assert result.refused, f"gateway accepted a forged token: {result!r}"
    assert result.tool_result is None, (
        f"the gateway refused but a tool result came back anyway: {result!r}"
    )


# --- OQ-U1's regression guard ------------------------------------------------
#
# `docs/upstream-findings.md`'s OQ-U1 closes a policy-bypass shape that would
# otherwise go unruled-out: whether agentgateway v1.5.0's
# `mcpGuardrails` evaluates every message inside a batched JSON-RPC array. It
# does not have to -- the gateway rejects the array at deserialization, before
# the hook runs at all. That write-up names its own gap in as many words:
# "the day agentgateway accepts a batch on this transport, the original
# question reopens in full, and nothing in this repository's test suite would
# notice."
#
# This is that notice. It asserts exactly what was measured and nothing more,
# and it costs no model tokens -- it speaks MCP to the gateway directly, the
# same reason nothing else in this file is behind `RUN_BROWSER_TESTS=1`. If a
# future gateway version starts accepting arrays, this goes red and the
# question is reopened deliberately rather than discovered later.

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
PAYMENTS_API_URL = os.environ.get("PAYMENTS_API_URL", "http://localhost:18080")

# The status agentgateway v1.5.0 answers a JSON-RPC array with, measured
# directly against the live gateway.
BATCH_REJECTED = 400

# Over `settlement-agent`'s assisted allowance of 6,000, so a batch that DID
# reach the guardrail would produce a `policy.decision` span -- and one that
# bypassed it would execute a real over-limit payment. The probe cannot
# produce a false negative by asking for something that was never decidable.
BATCH_PROBE_AMOUNT = 6200.0


def _open_session(gateway_url: str, token: str) -> str:
    """`initialize` through the gateway with a real SVID, returning the
    `mcp-session-id` it hands back. `call_tool_directly` does this internally
    and then throws the session away; the batch probe needs to POST a second,
    hand-built body on the *same* session."""
    init = _post(
        gateway_url,
        {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "oq-u1-batch-probe", "version": "0"},
            },
        },
        {"authorization": f"Bearer {token}"},
    )
    assert init.status_code == 200, (
        f"could not open an MCP session for the batch probe: HTTP "
        f"{init.status_code}: {init.text[:300]}"
    )
    session_id = init.headers.get("mcp-session-id")
    assert session_id, f"no mcp-session-id: headers={dict(init.headers)!r}"
    return session_id


def test_a_jsonrpc_batch_is_rejected_before_the_policy_hook_and_changes_nothing(
    gateway_url, valid_svid
):
    """OQ-U1's closure, turned into a guard (`docs/upstream-findings.md`).

    A JSON-RPC **array** of two `tools/call` messages -- a benign
    `propose_payment` followed by an over-limit `execute_payment` -- is the
    shape of a policy bypass: if the gateway unpacked the array but handed
    `mcpGuardrails` only the first message, the second would reach
    `payments-mcp` with no decision made about it.

    Three assertions, one per thing that was measured:

    1. **HTTP 400.** The gateway refuses the payload at deserialization
       (`mcp: fail to deserialize request body: data did not match any
       variant of untagged enum JsonRpcMessage`), one layer above the point
       where a method name exists to be matched against `methods:
       {"tools/call": request}`.
    2. **No decision spans.** Zero `policy.decision` spans in the trace the
       request carries, read from Jaeger by an id this test mints itself, so
       there is no search and no ambiguity. A rejection that still consulted
       the policy would be a different (and much more interesting) finding.
    3. **The ledger is untouched.** The payment proposed as bait is still
       `proposed` afterwards.

    A control is built in: the bait payment is created by a *single*
    `propose_payment` through the same session with the same token, which
    must succeed. Without it a 400 could equally mean "this probe built a
    malformed request", and every assertion below would be satisfied by a
    gateway that was simply down.
    """
    session_id = _open_session(gateway_url, valid_svid)
    headers = {
        "authorization": f"Bearer {valid_svid}",
        "mcp-session-id": session_id,
    }

    # The control, and the bait. A single message on this session must work.
    claim_id = f"CLM-BATCH-{uuid.uuid4().hex[:8]}"
    proposed = _post(
        gateway_url,
        {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "tools/call",
            "params": {
                "name": "propose_payment",
                "arguments": {
                    "claim_id": claim_id,
                    "amount": BATCH_PROBE_AMOUNT,
                    "currency": "EUR",
                },
            },
        },
        headers,
    )
    assert proposed.status_code == 200, (
        "the single-message control failed, so a 400 on the batch below would "
        f"prove nothing about batching: HTTP {proposed.status_code}: "
        f"{proposed.text[:300]}"
    )
    control_body = _json_body(proposed) or {}
    structured = ((control_body.get("result") or {}).get("structuredContent")) or {}
    payment_id = structured.get("paymentId")
    assert payment_id, f"the control proposal recorded no payment: {control_body!r}"
    assert structured.get("status") == "proposed", control_body

    # A trace id this test mints, so the trace is fetched by id rather than
    # searched for.
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    batch = [
        {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "tools/call",
            "params": {
                "name": "propose_payment",
                "arguments": {
                    "claim_id": f"{claim_id}-B",
                    "amount": 100,
                    "currency": "EUR",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "tools/call",
            "params": {
                "name": "execute_payment",
                "arguments": {
                    "payment_id": payment_id,
                    "amount": BATCH_PROBE_AMOUNT,
                    "currency": "EUR",
                },
            },
        },
    ]
    batched = httpx.post(
        f"{gateway_url}/mcp",
        json=batch,
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "traceparent": f"00-{trace_id}-{span_id}-01",
            **headers,
        },
        timeout=30,
    )

    # 1. Rejected outright.
    assert batched.status_code == BATCH_REJECTED, (
        "agentgateway no longer rejects a JSON-RPC array outright -- OQ-U1 "
        "(docs/upstream-findings.md) is REOPENED, and whether `mcpGuardrails` "
        "evaluates every message in a batch is an open policy-bypass question "
        f"again. HTTP {batched.status_code}: {batched.text[:500]}"
    )

    # 3. The ledger, first, because it is the primary evidence and it needs no
    # export delay to settle.
    record = requests.get(
        f"{PAYMENTS_API_URL}/payments/{payment_id}", timeout=10
    )
    assert record.status_code == 200, (
        f"could not read {payment_id} back from the ledger: HTTP "
        f"{record.status_code}: {record.text[:300]}"
    )
    assert record.json().get("status") == "proposed", (
        "A BATCHED OVER-LIMIT execute_payment MOVED MONEY: the ledger's own "
        f"record for {payment_id} reads "
        f"{record.json().get('status')!r}: {record.json()}"
    )

    # 2. No decision spans. The trace may be empty (Jaeger 404s on an id it
    # has never seen) or may hold the gateway's single `POST /*` span; both
    # are consistent with "the guardrail never ran", and only a
    # `policy.decision` span would contradict it. Waiting out one full
    # BatchSpanProcessor cycle first, so "absent" is not merely "not exported
    # yet" -- the PDS uses the SDK default 5s schedule delay.
    time.sleep(JaegerClient.QUIET_WINDOW_S)
    try:
        trace = JaegerClient(JAEGER_URL).get_trace(trace_id)
    except AssertionError:
        decisions = []
    else:
        decisions = trace.spans_named("policy.decision")
    assert decisions == [], (
        "the batched request produced policy.decision span(s), so the "
        "guardrail DID see the array -- OQ-U1's measured answer ('rejected at "
        "deserialization, before the hook runs') no longer holds: "
        f"{[(s.service, s.attrs) for s in decisions]}"
    )
