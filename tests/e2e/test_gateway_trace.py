"""Asserts the single-trace property survives putting agentgateway in the path.

Chain under test: A2A client -> settlement-agent (model decides) ->
**agentgateway** -> payments-mcp -> payments-api.

One trace already spans this chain when settlement-agent talks straight to
payments-mcp, with no gateway in the path. This file exists to answer one
question about the interposed proxy: is agentgateway a real node *inside*
that one trace, or a fracture in it? This file is the in-tree, runnable
confirmation of the answer, measured against the real agent.

**Why it survives, stated precisely, because two different mechanisms are easy
to conflate here:**

* This repo's MCP trace-context convention is SEP-414 — `traceparent` /
  `tracestate` / `baggage` as unprefixed keys inside the JSON-RPC
  `params._meta` object (`docs/telemetry-contract.md`'s "Conventions
  adopted"). `agents/settlement-agent/mcp_tools.py` does that injection by
  hand.
* agentgateway does **not** read that. Its `TraceParent::from_request`
  (v1.5.0, `crates/agentgateway/src/telemetry/trc.rs`) reads the HTTP
  `traceparent` **header** and nothing else; it never inspects the request
  body. Read in isolation that predicts a fractured trace.
* It survives anyway because `bootstrap.py` runs
  `HTTPXClientInstrumentor().instrument()` and the MCP streamable-HTTP
  transport is httpx-based, so every outbound MCP request *also* carries an
  auto-injected `traceparent` HTTP header — measured against an echo server,
  e.g. `00-df60a027741941a16675b35edbcf0a32-5675a787e51b87e6-03`.
  The gateway reads that header, and the `_meta` injection rides along
  untouched for payments-mcp's own FastMCP extraction to use.

So the two carriers are independent and both present. Nothing here should be
read as agentgateway supporting SEP-414 on ingress — it does not. A future MCP
client that carried context in `_meta` *alone* (a stdio transport, or a client
library that does not auto-instrument httpx) would fragment at this hop, and
these assertions would not catch it, because settlement-agent is not that
client.

**Cost gate.** Every test in this file shares one module-scoped live model run,
and the whole file is gated behind `RUN_BROWSER_TESTS=1` — the same convention
`test_browser_root_span.py` and `test_settlement_llm_trace.py` use, and the
only skip convention this suite has. `scripts/smoke-test.sh` sets that flag
unconditionally, so the gate always pays for this run.

**The fixture below is defined here, not imported.**
`test_settlement_llm_trace.py`'s `llm_settlement` fixture is
module-local to that file and not importable across modules; this file defines
its own in the same style (module-scoped, one live run, minted traceparent,
`wait_for_trace_id` never `find_trace`, 180s timeout for the reason that file
documents — three sequential model turns plus MCP round trips, now with a
proxy hop added).
"""
import json
import os
import uuid
from types import SimpleNamespace

import httpx
import pytest

from tests.harness.jaeger import JaegerClient

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
SETTLEMENT_URL = os.environ.get("SETTLEMENT_AGENT_URL", "http://localhost:19002")

ENTRY_SPAN = "POST /"  # StarletteInstrumentor's ASGI server span

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason=(
        "needs the live kind cluster and spends real model tokens; gated "
        "behind the same RUN_BROWSER_TESTS=1 flag scripts/smoke-test.sh sets "
        "unconditionally"
    ),
)

# This file is about trace *shape* through the interposed proxy — is
# agentgateway a node in the one trace, or a fracture in it — and the amount
# is incidental to that question, except that the PDS denies
# `execute_payment` above the €6,000 allowance for this caller
# (`platform/policy-decision-service/pds/rules.py`): at €6,200 the gateway
# never emits its `tools/call payments-mcp_execute_payment` CLIENT forward
# span and payments-mcp never emits `tools/call execute_payment` at all, so
# half of the propagation this file exists to prove would simply not be in
# the trace to assert on. €5,800 is under the allowance, so both tools traverse the full
# hop and every claim below is exercised end to end — which is the *stronger*
# choice, not the convenient one: the refusal path's own trace shape is owned
# by `tests/e2e/test_policy_denial.py` (criteria 1 and 3), at a level of
# detail this file has no business duplicating.
REQUESTED_AMOUNT = 5800


def _send(text: str, traceparent: str) -> dict:
    """Minimal A2A JSON-RPC `message/send`, same wire shape as
    `test_settlement_llm_trace.py`'s helper."""
    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": uuid.uuid4().hex,
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }
    response = httpx.post(
        f"{SETTLEMENT_URL}/", json=payload, headers={"traceparent": traceparent}, timeout=180
    )
    response.raise_for_status()
    return response.json()


@pytest.fixture(scope="module")
def gateway_settlement():
    """One live, LLM-driven settlement through the gateway, shared by this file.

    This fixture originates the request, so it mints its own trace and
    span id, sends them as a `traceparent` header, and looks the trace up by
    that exact id — never `find_trace`/`wait_for_trace`, which can silently
    return a stale trace.

    **`services=` deliberately omits `agentgateway`.** Naming it here would
    make the trace-completeness wait itself fail when the gateway is not in the
    path, surfacing as a fixture `ERROR` — which is not the failure this file
    is supposed to report. Leaving it out means a gateway-less run assembles a
    complete-looking trace and then fails in
    `test_mcp_calls_traverse_the_gateway_within_one_trace` with the message
    that actually names the problem. The cost of that choice is that gateway
    spans arriving *late* would fail this file rather than being waited for;
    that is covered by `JaegerClient.QUIET_WINDOW_S` (10s, comfortably past
    every exporter's batch delay in this system) and, if it ever were not,
    fails loudly rather than passing wrongly.

    **`policy-decision-service` is omitted for the same reason**, since
    `test_gateway_span_names_kinds_and_parentage_are_as_measured` asserts
    the PDS's `policy.decision` span as a child of the gateway's `tools/call`
    SERVER span. Naming the PDS here would turn a missing decision span into a
    fixture `ERROR` shared by all six tests; leaving it out means that one
    test fails by name, with a message that says which of the three expected
    children was absent. Same 10s quiet window covers the straggling-batch
    case, and the PDS uses the same default 5s BatchSpanProcessor delay as
    every other service here.
    """
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    traceparent = f"00-{trace_id}-{span_id}-01"
    claim_id = f"CLM-GW-{uuid.uuid4().hex[:8]}"

    result = _send(
        json.dumps({"claimId": claim_id, "amount": REQUESTED_AMOUNT, "currency": "EUR"}),
        traceparent,
    )
    assert "error" not in result, f"A2A call failed: {result}"
    reply = json.loads(result["result"]["parts"][0]["text"])
    assert reply["status"] == "executed", reply

    client = JaegerClient(JAEGER_URL)
    trace = client.wait_for_trace_id(
        trace_id,
        operation=ENTRY_SPAN,
        timeout=180,
        services={"settlement-agent", "payments-mcp", "payments-api"},
    )

    return SimpleNamespace(
        trace=trace,
        trace_id=trace_id,
        span_id=span_id,
        claim_id=claim_id,
        reply=reply,
        settled_amount=reply["amount"],
    )


def test_mcp_calls_traverse_the_gateway_within_one_trace(gateway_settlement):
    """The gateway is a real node in the trace, not a fracture.

    Measured directly: caller -> agentgateway
    "tools/call" -> agentgateway "tools/call payments-mcp_<tool>" ->
    payments-mcp "tools/call <tool>" -> payments-mcp "POST" -> payments-api.
    """
    trace = gateway_settlement.trace
    gateway_spans = [s for s in trace.spans if s.service == "agentgateway"]
    assert gateway_spans, (
        "no agentgateway spans in the trace -- is it in the path? "
        f"services present: {sorted(trace.services())}"
    )

    # every gateway span's parent must be in the same trace (no new root)
    span_ids = {s.span_id for s in trace.spans}
    for span in gateway_spans:
        assert span.parent_id, (span.operation, "gateway span is a root -- trace fragmented")
        assert span.parent_id in span_ids, (span.operation, span.parent_id)

    # payments-mcp's tool span must descend from a gateway span
    mcp_tool_spans = [
        s
        for s in trace.spans
        if s.service == "payments-mcp" and s.operation.startswith("tools/call")
    ]
    assert mcp_tool_spans
    gateway_ids = {s.span_id for s in gateway_spans}
    assert any(s.parent_id in gateway_ids for s in mcp_tool_spans), (
        "payments-mcp tool span does not descend from the gateway"
    )


def test_the_gateway_is_the_only_route_into_payments_mcp(gateway_settlement):
    """The reroute is real: nothing still reaches payments-mcp directly.

    The test above would pass if `PAYMENTS_MCP_URL` were reverted but the
    gateway still saw some other traffic in the same trace — "a gateway span
    exists" and "the agent's calls go through the gateway" are different
    claims. This asserts the stronger one: *every* payments-mcp span whose
    parent lives in another service is parented to an agentgateway span, and
    none to settlement-agent. payments-mcp's own internal children (its
    outbound `POST` to payments-api) are parented within payments-mcp and are
    not evidence either way, so they are excluded by construction.
    """
    trace = gateway_settlement.trace
    by_id = {s.span_id: s for s in trace.spans}
    crossings = [
        (s.operation, by_id[s.parent_id].service)
        for s in trace.spans
        if s.service == "payments-mcp"
        and s.parent_id in by_id
        and by_id[s.parent_id].service != "payments-mcp"
    ]
    assert crossings, (
        "no payments-mcp span has a parent in another service, so this trace "
        "cannot say anything about how payments-mcp is reached; payments-mcp "
        f"spans: {[s.operation for s in trace.spans if s.service == 'payments-mcp']}"
    )
    direct = [c for c in crossings if c[1] != "agentgateway"]
    assert not direct, (
        "payments-mcp is still being reached from outside the gateway "
        f"(operation, calling service): {direct} -- PAYMENTS_MCP_URL should "
        "point at agentgateway, not at payments-mcp"
    )


# --- The gateway's own telemetry, exactly as measured -----------------------
#
# Every name below was read off a live span in Jaeger, dumped in full before
# being written here. None was predicted from agentgateway's source or
# documentation, and none was invented:
# agentgateway is not our component, so the no-invented-telemetry rule applies
# to it without exception. These sets exist so that a version bump silently
# changing the gateway's span vocabulary fails loudly here instead of quietly
# rotting `docs/telemetry-contract.md`.
#
# **Two keys are deliberately excluded, and the exclusion was measured, not
# assumed.** `internal.span.format` and `span.kind` appear on agentgateway's
# spans but are not agentgateway's: in that same trace they appear on 20/20
# agentgateway spans *and* 52/52 settlement-agent, 6/6 payments-mcp and 2/2
# payments-api spans -- i.e. on every span from every service. They are
# Jaeger's own representation of the OTLP payload (`internal.span.format`) and
# of the span's OTel `SpanKind` (`span.kind`, which this harness surfaces as
# `Span.kind`), so attributing either to agentgateway would be wrong.
JAEGER_ADDED_TAGS = {"internal.span.format", "span.kind"}

# The gateway emits a *pair* of spans per MCP message: a SERVER span for the
# request it received, and a CLIENT span for the request it made to the
# upstream target. The CLIENT span's name carries the target and, for
# tools/call, the tool -- prefixed `<target>_<tool>`, which is agentgateway's
# own multiplexing convention (see `mcp-multiplex` in its examples/), not a
# tool name payments-mcp would recognise: payments-mcp's own span for the same
# call is named `tools/call propose_payment`.
GATEWAY_TOOLS_CALL_SERVER_ATTRS = {
    "duration",
    "gateway",
    "gen_ai.tool.name",
    "http.host",
    "http.method",
    "http.path",
    "http.status",
    "http.version",
    # agentgateway's own attribute, not ours and not Jaeger's: it
    # appears once the route carries a `jwtAuth` policy, and it carries the
    # verified token's `sub` claim -- which for a SPIRE-minted JWT-SVID is the
    # caller's SPIFFE ID. Measured on live SERVER spans; measured *absent* from
    # the CLIENT spans below, which is why only this set grew. Its value is
    # asserted separately (see the jwt.sub test), because a key that is present
    # but carrying the wrong identity is the failure this whole task exists to
    # rule out.
    "jwt.sub",
    "listener",
    "mcp.method.name",
    "mcp.resource.type",
    "mcp.session.id",
    "mcp.target",
    "network.protocol.version",
    "otel.scope.name",
    "protocol",
    "route",
    "span.id",
    "src.addr",
    "trace.id",
    "url.scheme",
}

# The caller's attested identity, as SPIRE mints it from
# `deploy/platform/spire/registration.yaml`'s ID template: trust domain,
# namespace, service account. `tests/unit/test_stage2_spire_config.py` pins
# that the configuration renders this; this file pins that the gateway
# actually *observed* it on the wire.
SETTLEMENT_AGENT_SPIFFE_ID = (
    "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
)

GATEWAY_TOOLS_CALL_CLIENT_ATTRS = {
    "agentgateway.outbound.kind",
    "agentgateway.outbound.subtype",
    "gen_ai.tool.name",
    "mcp.method.name",
    "mcp.target",
    "otel.scope.name",
}


def _gateway_spans(trace):
    return [s for s in trace.spans if s.service == "agentgateway"]


def _keys(span):
    return set(span.attrs) - JAEGER_ADDED_TAGS


def test_gateway_span_names_kinds_and_parentage_are_as_measured(gateway_settlement):
    """Pins the measured span *shape* of the interposed hop.

    Measured, for each of the two tools the model calls: a SERVER span named
    exactly `tools/call` (the JSON-RPC method, with no tool name in it), whose
    MCP forward child is a CLIENT span named `tools/call payments-mcp_<tool>`,
    whose own child is payments-mcp's `tools/call <tool>` SERVER span. The
    tool name is not in the SERVER span's *name* — it is in that span's
    `gen_ai.tool.name` attribute, which is a real difference worth pinning:
    an operator filtering Jaeger by operation name sees two identically-named
    `tools/call` spans per settlement and has to read an attribute to tell
    them apart.

    **The forward span is not the SERVER span's only child.** The
    `mcpGuardrails` policy interposes an `agentgateway` `Guardrail` CLIENT
    span and the PDS's own `policy.decision` SERVER span as its siblings.
    The full measured child set is asserted below, with the reasoning
    inline.

    `otel.scope.name` is asserted across every gateway span because it is the
    only scope identifier the gateway sets — measured: it emits
    `otel.scope.name = "agentgateway"` and **no `otel.scope.version` at all**,
    unlike every other service in this trace (settlement-agent 49/52,
    payments-mcp 2/6, payments-api 2/2 carry a version; agentgateway 0/20).
    The version *is* available on the span's Resource
    (`service.version = "1.5.0"`), just not on the scope.
    """
    trace = gateway_settlement.trace
    gateway_spans = _gateway_spans(trace)
    assert gateway_spans, "no agentgateway spans in the trace -- is it in the path?"

    for span in gateway_spans:
        assert span.attrs.get("otel.scope.name") == "agentgateway", (
            span.operation,
            span.attrs.get("otel.scope.name"),
        )
        assert "otel.scope.version" not in span.attrs, (
            f"{span.operation!r} now carries otel.scope.version="
            f"{span.attrs['otel.scope.version']!r}; agentgateway v1.5.0 emitted "
            "none, so this measurement (and docs/telemetry-contract.md) is stale"
        )

    by_id = {s.span_id: s for s in trace.spans}
    servers = [s for s in gateway_spans if s.operation == "tools/call"]
    assert servers, (
        "no agentgateway span named exactly 'tools/call'; gateway operations "
        f"seen: {sorted({s.operation for s in gateway_spans})}"
    )
    for server in servers:
        assert server.kind == "server", (server.operation, server.kind)
        tool = server.attrs.get("gen_ai.tool.name")
        assert tool in {"propose_payment", "execute_payment"}, (
            f"agentgateway's 'tools/call' SERVER span carries "
            f"gen_ai.tool.name={tool!r}"
        )
        # The `mcpGuardrails` policy in
        # `deploy/platform/agentgateway/config.yaml` makes this SERVER
        # span's child set three deep, on *every* tools/call, propose and
        # execute alike, permit or deny -- not a single forward child. The
        # measured shape:
        #
        #   agentgateway `tools/call` (SERVER)
        #     |- agentgateway `Guardrail` (CLIENT)          -- the hook call
        #     |- policy-decision-service `policy.decision` (SERVER)
        #     `- agentgateway `tools/call payments-mcp_<tool>` (CLIENT)
        #
        # `policy.decision` is a *sibling* of `Guardrail`, not its child, and
        # that is the interesting part: agentgateway propagates the context it
        # received on the inbound request to the hook, not the context of its
        # own `Guardrail` CLIENT span. So the PDS's server span attaches to the
        # gateway's SERVER span, one level above the client span that actually
        # made the call. Asserted as measured — inventing a
        # `Guardrail -> policy.decision` edge would be inventing telemetry for
        # a third-party tool.
        #
        # Asserted as a sorted list of (service, operation, kind) triples, not
        # a set: a list pins multiplicity as well as membership (a second
        # `Guardrail` span, or a duplicated forward, still fails), while
        # sorting removes a dependence on Jaeger's arbitrary child ordering
        # that a plain list comparison would have. This is exact equality in
        # both directions, exactly as the single-child assertion it replaces
        # was — not a count tolerance and not a name allowlist. The forward
        # CLIENT span's `kind` is pinned by the triple, so it is not restated
        # below.
        children = [s for s in trace.spans if s.parent_id == server.span_id]
        measured = sorted((c.service, c.operation, c.kind) for c in children)
        expected = sorted(
            [
                ("agentgateway", "Guardrail", "client"),
                ("policy-decision-service", "policy.decision", "server"),
                ("agentgateway", f"tools/call payments-mcp_{tool}", "client"),
            ]
        )
        assert measured == expected, (
            f"agentgateway's 'tools/call' SERVER span for {tool!r} has children\n"
            f"  measured: {measured}\n"
            f"  expected: {expected}\n"
            "A missing `tools/call payments-mcp_<tool>` forward means the call "
            "was denied and never forwarded -- at EUR "
            f"{REQUESTED_AMOUNT} it must be permitted, so check the PDS's "
            "allowance. A missing `policy.decision` means either the "
            "mcpGuardrails hook is no longer on this route, or the PDS's own "
            "spans had not landed when the fixture judged the trace complete. "
            "A missing `Guardrail` means the gateway is not calling the hook "
            "at all."
        )
        client = next(
            c for c in children if c.operation == f"tools/call payments-mcp_{tool}"
        )

        mcp_children = [
            s
            for s in trace.spans
            if s.parent_id == client.span_id and s.service == "payments-mcp"
        ]
        assert [s.operation for s in mcp_children] == [f"tools/call {tool}"], (
            f"{client.operation!r}'s payments-mcp children are "
            f"{[s.operation for s in mcp_children]!r}, expected exactly "
            f"['tools/call {tool}']"
        )

        # The gateway is a hop in *this* trace, not a new one.
        assert by_id[client.parent_id].service == "agentgateway"
        assert server.parent_id in by_id, (server.operation, server.parent_id)
        assert by_id[server.parent_id].service == "settlement-agent", (
            f"agentgateway's 'tools/call' SERVER span is parented to "
            f"{by_id[server.parent_id].service!r}, not settlement-agent"
        )


def test_gateway_span_attributes_are_exactly_what_was_measured(gateway_settlement):
    """Pins the gateway's attribute vocabulary on the two tools/call spans.

    Exact set equality in both directions, deliberately: an attribute
    *appearing* matters as much as one disappearing, because this project
    publishes agentgateway's vocabulary in `docs/telemetry-contract.md` and a
    published list that silently gained a member is the defect class this repo
    has already had to correct three times.

    **The finding this test makes non-optional: agentgateway v1.5.0 does not
    speak either HTTP semantic convention.** It emits `http.method`,
    `http.path`, `http.status`, `http.host` and `http.version` — not the
    stable spelling (`http.request.method`, `url.path`,
    `http.response.status_code`, `server.address`) that this project's Go
    service emits by default and that all three of its Python services are
    forced onto with `OTEL_SEMCONV_STABILITY_OPT_IN=http`, and not the OTel
    *legacy* spelling either: legacy is `http.status_code` and `http.url`,
    while the gateway emits `http.status` and no URL attribute at all. It is a
    third vocabulary. Two attributes are the exception and are correct stable
    semconv — `url.scheme` and `network.protocol.version` — which is exactly
    what the v1.5.0 source predicts, since those two are the only keys it
    routes through its own `semconv` module
    (`crates/agentgateway/src/telemetry/trc.rs`); everything else is a raw
    string key. Asserted here so the day this changes upstream, this project
    finds out from a red test rather than from a stale document.
    """
    trace = gateway_settlement.trace
    gateway_spans = _gateway_spans(trace)

    servers = [s for s in gateway_spans if s.operation == "tools/call"]
    clients = [
        s for s in gateway_spans if s.operation.startswith("tools/call payments-mcp_")
    ]
    assert servers and clients, (
        f"gateway operations seen: {sorted({s.operation for s in gateway_spans})}"
    )

    for span in servers:
        assert _keys(span) == GATEWAY_TOOLS_CALL_SERVER_ATTRS, (
            f"{span.operation!r} (SERVER) attribute keys have drifted from what "
            "was measured; update docs/telemetry-contract.md too.\n"
            f"  unexpected: {sorted(_keys(span) - GATEWAY_TOOLS_CALL_SERVER_ATTRS)}\n"
            f"  missing:    {sorted(GATEWAY_TOOLS_CALL_SERVER_ATTRS - _keys(span))}"
        )
        # Stated separately from the set equality above so the failure message
        # names the convention rather than just a set difference.
        assert "http.request.method" not in span.attrs, (
            "agentgateway now emits the stable HTTP semconv spelling; the "
            "third-vocabulary finding in docs/telemetry-contract.md is stale"
        )
        assert "http.status_code" not in span.attrs and "http.url" not in span.attrs, (
            "agentgateway now emits the OTel legacy HTTP spelling; the "
            "third-vocabulary finding in docs/telemetry-contract.md is stale"
        )

    for span in clients:
        assert _keys(span) == GATEWAY_TOOLS_CALL_CLIENT_ATTRS, (
            f"{span.operation!r} (CLIENT) attribute keys have drifted from what "
            "was measured; update docs/telemetry-contract.md too.\n"
            f"  unexpected: {sorted(_keys(span) - GATEWAY_TOOLS_CALL_CLIENT_ATTRS)}\n"
            f"  missing:    {sorted(GATEWAY_TOOLS_CALL_CLIENT_ATTRS - _keys(span))}"
        )


def test_the_gateway_carries_no_northstar_claim_id(gateway_settlement):
    """The enrichment boundary, asserted rather than only asserted-in-prose.

    `docs/telemetry-contract.md`'s "Enrichment boundary" section states that
    agentgateway's own spans are correlated by trace ID, not enriched.
    Measured, now that the gateway is in the path: it holds.
    `ClaimIdSpanProcessor` runs only
    inside this project's own Python processes and nothing reaches into
    agentgateway's telemetry to add anything, so all 20 of its spans in the
    measured trace carry no `northstar.claim.id`.

    This is the *desired* behaviour, not a gap to close — the negative form is
    the point. If a future task ever starts injecting this project's domain
    attributes into third-party infrastructure telemetry, this test fails and
    that becomes a deliberate decision instead of a quiet one.
    """
    gateway_spans = _gateway_spans(gateway_settlement.trace)
    # Without this, the whole test passes vacuously on a trace containing no
    # gateway spans at all -- i.e. it would report green on precisely the
    # regression the rest of this file exists to catch. Verified: against a
    # pre-reroute trace this assertion is what fails, not the one below.
    assert gateway_spans, (
        "no agentgateway spans in the trace, so this test's negative claim "
        "would hold vacuously -- is the gateway in the path?"
    )
    offenders = [
        (s.operation, s.attrs["northstar.claim.id"])
        for s in gateway_spans
        if "northstar.claim.id" in s.attrs
    ]
    assert not offenders, (
        "agentgateway spans now carry northstar.claim.id: "
        f"{offenders} -- either baggage started crossing into the gateway's "
        "own telemetry, or this project started enriching infrastructure it "
        "does not run in-process. Both need a deliberate decision and a "
        "docs/telemetry-contract.md update, not a silent pass."
    )


def test_the_gateway_records_the_callers_attested_spiffe_id_on_its_server_spans(
    gateway_settlement,
):
    """The identity is *in the trace*, not only in a 401.

    This is the assertion that makes workload identity observable rather than
    merely enforced. The gateway verifies a SPIRE-minted JWT-SVID and records
    the verified `sub` claim as `jwt.sub` on the SERVER span for the request it
    checked — so an operator reading this trace can see which attested
    workload asked for the payment, from the same span tree that shows the
    payment being proposed. Nothing in this project sets that attribute:
    agentgateway derives it from the token it validated.

    Three things are pinned, and the third is the one that matters:

    1. `jwt.sub` is present on every gateway SERVER span. Its presence at all
       depends on the route carrying a `jwtAuth` policy — without that
       policy, no span in this trace would carry this attribute at all.
    2. It is absent from the CLIENT spans. Measured, and consistent with the
       gateway's own behaviour on the outbound leg: it does not forward the
       credential (`if !self.preserve_token { self.location.remove(req)?; }` in
       `crates/agentgateway/src/http/jwt.rs`), so there is no verified token on
       that side to attribute anything to. Recorded here so the day the
       gateway starts propagating identity onward, this project finds out.
    3. The value is the *caller's* SPIFFE ID, not the audience and not
       `.../sa/default`. That distinction is the whole point: a token whose
       subject were the gateway's own ID, or an identity minted for the
       `default` service account because the pod never got its own, would both
       verify successfully and prove nothing about who called. `.../sa/default`
       is not hypothetical — it is the identity this pod actually attested as
       before it was given a ServiceAccount of its own.
    """
    gateway_spans = _gateway_spans(gateway_settlement.trace)
    servers = [s for s in gateway_spans if s.kind == "server"]
    clients = [s for s in gateway_spans if s.kind == "client"]
    assert servers, (
        "no agentgateway SERVER spans in the trace, so every claim below would "
        "hold vacuously -- is the gateway in the path?"
    )

    missing = [s.operation for s in servers if "jwt.sub" not in s.attrs]
    assert not missing, (
        f"{len(missing)} of {len(servers)} agentgateway SERVER spans carry no "
        f"jwt.sub ({sorted(set(missing))}) -- either the route lost its "
        "jwtAuth policy, or the gateway stopped recording the verified "
        "subject. Identity is then unobservable in the trace even if it is "
        "still enforced."
    )

    subjects = {s.attrs["jwt.sub"] for s in servers}
    assert subjects == {SETTLEMENT_AGENT_SPIFFE_ID}, (
        f"agentgateway attributed these calls to {sorted(subjects)}, expected "
        f"exactly {{{SETTLEMENT_AGENT_SPIFFE_ID!r}}}. A subject ending "
        "'/sa/default' means the calling pod is running as the default "
        "ServiceAccount and its SVID names an identity SPIRE never "
        "constrained on; a subject naming the gateway itself means the "
        "audience was minted as the subject."
    )

    leaked = [(s.operation, s.attrs["jwt.sub"]) for s in clients if "jwt.sub" in s.attrs]
    assert not leaked, (
        f"agentgateway CLIENT spans now carry jwt.sub: {leaked}. Measured "
        "absent, consistent with the gateway stripping the "
        "credential on the outbound leg; if it now forwards identity upstream, "
        "GATEWAY_TOOLS_CALL_CLIENT_ATTRS and docs/telemetry-contract.md are "
        "both stale and payments-mcp can start checking who called it."
    )
