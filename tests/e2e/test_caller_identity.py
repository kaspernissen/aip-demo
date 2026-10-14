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
import time
import uuid

import httpx
import jwt as pyjwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa

# The helpers this file used to own -- the MCP caller, the SVID minting, the
# gateway port-forward, the outcome type -- live in `tests/harness/live.py` so
# the stage driver (`scripts/demo`) runs the very same code these tests assert
# on. The private names are kept as aliases because other e2e modules import
# them from here by those names.
from tests.harness import live
from tests.harness.jaeger import JaegerClient
from tests.harness.live import (  # noqa: F401 -- re-exported to sibling modules
    CALLER_SPIFFE_ID,
    GATEWAY_AUDIENCE,
    GATEWAY_NAMESPACE,
    KUBE_CONTEXT,
    MCP_PROTOCOL_VERSION,
    SPIRE_SERVER_POD,
    UNAUTHORIZED,
    Outcome as _Outcome,
    call_tool_directly,
    claims as _claims,
    json_body as _json_body,
    kubectl as _kubectl,
    post as _post,
)


# A different, equally real SPIFFE ID in the same trust domain. A token minted
# for this audience is a token minted for payments-mcp, not for the gateway --
# the replay this audience scoping exists to stop.
OTHER_AUDIENCE = "spiffe://northstar.local/ns/northstar-tools/sa/payments-mcp"

# What SPIRE actually mints as `iss`, rendered by the chart from the single
# `global.spire.jwtIssuer` value and matched exactly by the gateway's
# `jwtAuth.issuer`.
SPIRE_ISSUER = (
    "http://spire-spiffe-oidc-discovery-provider.northstar-platform.svc.cluster.local"
)

UNTRUSTED_ISSUER = "http://issuer.invalid"

# --- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
def gateway_url():
    """A port-forward to `svc/agentgateway`, owned and cleaned up by this file
    (see `tests.harness.live.gateway_port_forward` for how the ephemeral port
    is chosen and why it cannot shadow a repo-managed forward)."""
    with live.gateway_port_forward() as url:
        yield url


@pytest.fixture(scope="module")
def mint_svid():
    """Mint a real JWT-SVID for any (subject, audience) pair, via SPIRE itself
    (`tests.harness.live.mint_svid` -- why server-side minting is used is
    documented there)."""

    def _mint(audience: str, subject: str = CALLER_SPIFFE_ID) -> str:
        return live.mint_svid(audience, subject)

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
# M8: the route's `authorization` ceiling runs BEFORE the MCP body is parsed, and
# a JSON-RPC array carries no `mcp.methodName`, so its allow rule errors and the
# batch is answered `403 authorization failed` instead of the 400 above. Measured
# live, M8 Task 4. Both are a refusal before `mcpGuardrails` runs; the ledger and
# no-decision-span assertions below are what actually say "not a bypass".
BATCH_REJECTED_BY_CEILING = 403

# Over `settlement-agent`'s assisted allowance (0 since M8), so a batch that DID
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
    assert batched.status_code in (BATCH_REJECTED, BATCH_REJECTED_BY_CEILING), (
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
