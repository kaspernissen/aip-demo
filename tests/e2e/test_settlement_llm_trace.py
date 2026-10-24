"""Asserts the single-trace property survives putting a language model in the path.

Chain under test: A2A client -> settlement-agent (**model decides**) ->
payments-mcp -> payments-api.

settlement-agent's decision of which payment tools to call is made by a real
PydanticAI agent on Claude, not hardcoded (`settlement.py`). The only
question this file exists to answer is whether the chain is still *one*
trace once a model sits in the middle of it — and, second, whether prompt/
completion capture (`NORTHSTAR_CAPTURE_CONTENT`, `bootstrap.py`) genuinely
behaves as configured, so the telemetry contract's content-capture claim is
testable rather than aspirational.

`include_content` is controlled by `NORTHSTAR_CAPTURE_CONTENT`, an env var
defaulting to ON (see `bootstrap.capture_content_enabled()` and
docs/telemetry-contract.md's "Content capture" section for why). A test that
only ever asserted absence would be toothless in the direction that matters
most — instead `test_content_capture_matches_the_deployed_mode` reads the
mode off the *running* deployment and asserts presence when ON, absence when
OFF, so it has teeth in both directions and can never pass by asserting
nothing.

Everything asserted below was measured from a live capture, not assumed.
Five points are worth knowing before reading further:

1. **`assert_single_root`/`assert_no_orphans` cannot apply to this fixture.**
   An A2A-originated trace has *zero* roots and exactly one orphan by
   construction: the test mints its own `traceparent`, so the injected span
   never itself lands in Jaeger and settlement-agent's ASGI entry span
   (`POST /`) is parented to an ID no span in the trace owns. That is a
   known, legitimate orphan, pinned here by operation *and* `parent_id`
   exactly as `test_settlement_agent_trace.py` and `test_claim_correlation.py`
   already pin it — never by a count tolerance. The genuinely root-owning,
   zero-orphan form of this claim is `tests/e2e/test_browser_root_span.py`,
   whose real browser root drives this same LLM path end to end.

2. **`Trace.services` is a method, not a property** (`tests/harness/jaeger.py`),
   so `{"payments-mcp", ...} <= trace.services` would compare a set against a
   bound method. Called as `trace.services()` here.

3. **`model_request_parameters` is governed by a different setting than
   `NORTHSTAR_CAPTURE_CONTENT`.** `gen_ai.input.messages` and
   `gen_ai.output.messages` carry message *shape* — role, `parts[].type`,
   tool-call ids and tool names — plus a `content` key exactly when capture
   is ON, absent when OFF (measured both directions). But pydantic-ai 2.41.0
   additionally sets a non-semconv `model_request_parameters` attribute on
   every `chat` span that contains the agent's system instructions verbatim
   plus the full tool JSON schemas, gated by the separate
   `include_model_request_parameters` setting (`bootstrap.py` leaves it at
   its library default, `True`, deliberately — no claim-specific data
   reaches it). Pinned unconditionally by
   `test_model_request_parameters_carries_instructions_regardless_of_capture_mode`
   so a change to that gating cannot be forgotten, and left for the
   telemetry contract to state rather than papered over here.

4. **The model's own HTTP hop has a CLIENT span.** `anthropic==1.4.0` speaks
   `httpx2`, not `httpx`; `bootstrap.py` instruments both
   `HTTPXClientInstrumentor` (`_module_name = "httpx"`, covers the MCP hop)
   and `HTTPX2ClientInstrumentor` (covers the `api.anthropic.com` request) —
   see `test_anthropic_client_span_exists_and_is_parented_to_chat` below,
   which asserts the span exists and is correctly parented.

5. **This file has one module-scoped fixture, `llm_settlement`,** carrying
   both the trace and the reply as attributes (`llm_settlement.trace`,
   `llm_settlement.reply`) — the same pattern every other test in this file
   uses, so a single live model run is shared across all of them rather than
   re-run per test.

**Cost gate — scoped to this file, not to the suite.** Every test in this
file shares one module-scoped live model run, and the whole file is gated
behind `RUN_BROWSER_TESTS=1` — the same convention `test_browser_root_span.py`
uses, and the only skip convention this suite has. That keeps *this file's*
model run out of a bare `pytest tests/`. It does not make a bare
`pytest tests/` free of a cluster dependency or a token bill for the suite
as a whole: `test_settlement_agent_trace.py` and `test_claims_chain_trace.py`
are deliberately ungated (by design: the default suite is supposed to prove
the backend chain against the live cluster, see `docs/telemetry-contract.md`'s
"What the default suite proves" section), and the settlement each of those
drives is itself a real three-turn model call — so a bare `pytest tests/`
today needs the live cluster and spends real tokens on two live
settlements, regardless of this file's own gate. Prose is asserted nowhere
in this file: the model's wording varies run to run, so only structure,
names and numbers are checked.
"""
import json
import os
import subprocess
import uuid
from types import SimpleNamespace

import httpx
import pytest

from tests.harness import contract, live
from tests.harness.jaeger import JaegerClient

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")

ENTRY_SPAN = "POST /"  # StarletteInstrumentor's ASGI server span

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason=(
        "needs the live kind cluster and spends real model tokens; gated "
        "behind the same RUN_BROWSER_TESTS=1 flag scripts/smoke-test.sh sets "
        "unconditionally"
    ),
)

# The amount the A2A request asks for. Nothing below asserts the model
# settled *this* number: the model chooses what to propose and this file does
# not police it — so a test that failed on a different amount would be a guard
# rail smuggled into the suite. The amount actually recorded by the ledger is
# surfaced on the fixture (and in this file's failure messages) so a divergence
# is *reported*, never clamped.
#
# Nothing in *this repo's agent* clamps the amount — the model is free to
# propose whatever it likes. The ceiling lives one hop away, in the platform:
# it denies `execute_payment` above the €6,000 allowance for this caller
# (`platform/policy-decision-service/pds/rules.py`), so an amount above that
# comes back awaiting approval and the fixture's own `status == "executed"` assertion
# fails before any test body runs.
#
# €5,800 rather than a refusal, deliberately. Every one of this file's nine
# tests is about the *model's* instrumentation — the `gen_ai.*` vocabulary,
# content capture, message shape, token usage, the `anthropic` client's own
# httpx span — and none of them is about governance or the threshold. The
# amount is incidental to all nine, so re-pointing under the allowance keeps
# each one exercising the full happy path it was written against, including
# the `execute_payment` half of `test_the_model_called_both_payment_tools`
# and `test_mcp_hop_still_carries_the_trace`, which have no spans to assert on
# at all when the execution is denied. The refusal's own model-side shape is
# owned by `tests/e2e/test_policy_denial.py` (criterion 2: the narration and
# the no-retry count), which asserts it at the right level of detail;
# duplicating it here would give this project two places to maintain the same
# claim.
REQUESTED_AMOUNT = 5800

# Measured, not predicted. The `gen_ai.usage.*` names below are the ones
# pydantic-ai 2.41.0 actually emitted in live capture. Deliberately excluded
# from the assertion because they are conditional on what the model did, not
# on our wiring:
#   * gen_ai.usage.details.thinking_tokens          (only when it thought)
#   * gen_ai.usage.details.cache_creation_input_tokens
#   * gen_ai.usage.details.cache_read_input_tokens
CHAT_TOKEN_ATTRS = {"gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"}
AGENT_TOKEN_ATTRS = {
    "gen_ai.aggregated_usage.input_tokens",
    "gen_ai.aggregated_usage.output_tokens",
}

TOOL_NAMES = {"propose_payment", "execute_payment"}


def _send(text: str, traceparent: str) -> dict:
    """Minimal A2A JSON-RPC message/send, same wire-level shape as
    test_settlement_agent_trace.py's helper."""
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
    # Generous timeout: a settlement is now three sequential model turns plus
    # two MCP round trips, not one hardcoded function call.
    response = httpx.post(
        f"{live.settlement_agent_url()}/", json=payload, headers={"traceparent": traceparent}, timeout=180
    )
    response.raise_for_status()
    return response.json()


@pytest.fixture(scope="module")
def llm_settlement():
    """One live, LLM-driven settlement, shared by every test in this file.

    This test originates the request, so it mints its own trace and span ID,
    sends them as a `traceparent` header, and looks the trace up by that
    exact ID. Never `find_trace`/`wait_for_trace` — a search by operation
    name or service can silently return a stale trace instead, and by
    construction `wait_for_trace_id` cannot.
    """
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    traceparent = f"00-{trace_id}-{span_id}-01"
    claim_id = f"CLM-TEST-{uuid.uuid4().hex[:8]}"

    # M8: the resting state (`assisted`) releases nothing, so a claim the agent
    # is to settle itself runs with the dial at `autonomous` -- always restored.
    with live.autonomy(contract.AUTONOMOUS):
        result = _send(
            json.dumps({"claimId": claim_id, "amount": REQUESTED_AMOUNT, "currency": "EUR"}),
            traceparent,
        )
    assert "error" not in result, f"A2A call failed: {result}"
    reply = json.loads(result["result"]["parts"][0]["text"])
    assert reply["status"] == "executed", reply
    assert reply["narrative"], "the model returned an empty narrative"

    client = JaegerClient(JAEGER_URL)
    # Wait for the whole chain to land *and hold steady* for the harness's
    # 10s quiet window — comfortably past every service's default 5s
    # BatchSpanProcessor delay. Shorter windows (2s, 6s) produced false
    # negatives; 10s is the value that holds. The three model turns make
    # this trace larger and slower to assemble than a deterministic chain's,
    # so the overall budget is wider than the deterministic tests' too.
    trace = client.wait_for_trace_id(
        trace_id,
        operation=ENTRY_SPAN,
        timeout=180,
        services={"settlement-agent", "payments-mcp", "payments-api"},
    )

    # The exact prompt settlement.py builds. Reconstructed here rather than
    # imported, because this suite must not depend on the agent's own
    # importable module — but kept character-for-character identical so the
    # redaction needle below is genuinely the string that crossed the wire to
    # the model.
    prompt = (
        f"Claim {claim_id} is assessed as covered for "
        f"{float(REQUESTED_AMOUNT):.0f} EUR. Settle it."
    )

    return SimpleNamespace(
        trace=trace,
        trace_id=trace_id,
        span_id=span_id,
        claim_id=claim_id,
        reply=reply,
        prompt=prompt,
        settled_amount=reply["amount"],
    )


def _chat_spans(trace):
    return [s for s in trace.spans if s.attrs.get("gen_ai.operation.name") == "chat"]


def _descends_from(trace, span, ancestor_id):
    by_id = {s.span_id: s for s in trace.spans}
    seen = set()
    current = span
    while current is not None and current.parent_id is not None:
        if current.parent_id == ancestor_id:
            return True
        if current.parent_id in seen:
            return False
        seen.add(current.parent_id)
        current = by_id.get(current.parent_id)
    return False


def test_gen_ai_spans_join_the_existing_trace(llm_settlement):
    """The model's spans land inside the caller's trace, not in a new root.

    Stated at the boundary this fixture can actually own. Two halves:

      * the trace's only orphan is the expected synthetic one — the entry
        span parented to the injected span ID that was never itself
        exported — pinned by operation *and* parent, so a real propagation
        defect anywhere else still fails here;
      * every span carrying a `gen_ai.*` attribute descends from that entry
        span, which is what "the model did not detach the trace" means
        concretely. A `gen_ai.*` span merely *present* in the trace would not
        prove it: instrumentation that lost the ambient context would still
        share a trace ID while hanging off nothing.
    """
    trace = llm_settlement.trace
    entry = trace.span(ENTRY_SPAN, service="settlement-agent")

    unexpected = [
        s
        for s in trace.orphans()
        if not (s.operation == ENTRY_SPAN and s.parent_id == llm_settlement.span_id)
    ]
    assert not unexpected, (
        "orphan spans present beyond the expected synthetic root, context "
        f"propagation is broken: {[(s.service, s.operation, s.parent_id) for s in unexpected]}"
    )

    gen_ai = [s for s in trace.spans if any(k.startswith("gen_ai.") for k in s.attrs)]
    assert gen_ai, (
        "no span carries any gen_ai.* attribute; operations seen: "
        f"{sorted({s.operation for s in trace.spans})}"
    )

    detached = [
        (s.service, s.operation)
        for s in gen_ai
        if s.span_id != entry.span_id and not _descends_from(trace, s, entry.span_id)
    ]
    assert not detached, (
        f"gen_ai spans that do not descend from {ENTRY_SPAN!r}: {detached} — "
        "the model's instrumentation started its own subtree instead of "
        "joining the request's"
    )


def test_the_model_called_both_payment_tools(llm_settlement):
    """The model, not our code, chose to propose and then execute.

    `gen_ai.tool.name` is measured on two kinds of span: pydantic-ai's
    `execute_tool <name>` INTERNAL spans in settlement-agent, and
    payments-mcp's own `tools/call <name>` SERVER spans. Asserting the
    settlement-agent side specifically is what pins the *decision* — the
    payments-mcp side is asserted separately below as the hop.
    """
    tool_names = {
        s.attrs["gen_ai.tool.name"]
        for s in llm_settlement.trace.spans
        if "gen_ai.tool.name" in s.attrs and s.service == "settlement-agent"
    }
    assert TOOL_NAMES <= tool_names, (
        f"settlement-agent's gen_ai.tool.name values were {sorted(tool_names)}; "
        f"expected both of {sorted(TOOL_NAMES)}. Settled amount was "
        f"{llm_settlement.settled_amount}."
    )


def test_mcp_hop_still_carries_the_trace(llm_settlement):
    """The model choosing the call did not detach the MCP boundary.

    Beyond the service list, this asserts the measured parentage: each
    `tools/call <name>` SERVER span in payments-mcp *descends from* the
    matching `execute_tool <name>` span in settlement-agent. That is the
    strongest available statement that the model's tool choice is what
    caused the downstream call — `mcp_tools.py` injects `params._meta` from
    the context active inside pydantic-ai's tool span, so if the model's
    instrumentation and the MCP client ever stopped sharing a context, this
    parentage would break while a bare service-list check still passed.

    This asserts ancestry, not direct parentage: `PAYMENTS_MCP_URL` points at
    agentgateway, so the measured chain has two proxy spans between the tool
    span and the MCP server span —
    `settlement-agent:execute_tool propose_payment` ->
    `settlement-agent:POST` (httpx CLIENT) -> `agentgateway:tools/call`
    (SERVER) -> `agentgateway:tools/call payments-mcp_propose_payment`
    (CLIENT) -> `payments-mcp:tools/call propose_payment` (SERVER). Ancestry
    is paired with a separate assertion that the immediate parent is an
    agentgateway span, so the total strictness does not drop: this test
    still fails if the causal chain from the model's tool choice to the MCP
    server span is broken anywhere, and it also fails if that chain stops
    running through the gateway.
    """
    trace = llm_settlement.trace
    services = trace.services()
    assert {"payments-mcp", "payments-api"} <= services, sorted(services)

    by_id = {s.span_id: s for s in trace.spans}
    for name in sorted(TOOL_NAMES):
        tool_span = trace.span(f"execute_tool {name}", service="settlement-agent")
        mcp_span = trace.span(f"tools/call {name}", service="payments-mcp")
        assert _descends_from(trace, mcp_span, tool_span.span_id), (
            f"payments-mcp's {mcp_span.operation!r} span does not descend "
            f"from settlement-agent's {tool_span.operation!r} span "
            f"({tool_span.span_id!r}); its own parent is "
            f"{mcp_span.parent_id!r}"
        )
        parent = by_id.get(mcp_span.parent_id)
        assert parent is not None and parent.service == "agentgateway", (
            f"payments-mcp's {mcp_span.operation!r} span is parented to "
            f"{(parent.service, parent.operation) if parent else mcp_span.parent_id!r}, "
            "not to an agentgateway span -- the MCP hop is no longer going "
            "through the interposed gateway (see deploy/k8s/settlement-agent.yaml's "
            "PAYMENTS_MCP_URL and tests/e2e/test_gateway_trace.py)"
        )


def _deployed_capture_mode() -> bool:
    """Read the mode off the actual serving pod, not the Deployment spec.

    `kubectl apply` updates the Deployment's own spec the instant it runs,
    before the new ReplicaSet's pod exists or has finished starting —
    reading `deploy/settlement-agent` mid-rollout could report a mode the
    pod actually answering requests is not yet running. A Pod's spec is
    copied from the Deployment's template at that Pod's creation time and
    never changes afterward, so reading it from the Running pod reports what
    is *actually* serving, not what the manifest says it should eventually
    be.
    """
    pod_name = subprocess.run(
        ["kubectl", "-n", "northstar-agents", "get", "pod",
         "-l", "app=settlement-agent",
         "--field-selector=status.phase=Running",
         "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert pod_name, "no Running settlement-agent pod found"

    jsonpath = (
        "{.spec.containers[0].env"
        "[?(@.name=='NORTHSTAR_CAPTURE_CONTENT')].value}"
    )
    out = subprocess.run(
        ["kubectl", "-n", "northstar-agents", "get", "pod", pod_name,
         "-o", f"jsonpath={jsonpath}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return out.lower() not in {"false", "0", "no", "off"}


def test_content_capture_matches_the_deployed_mode(llm_settlement):
    """Proves the configured behaviour in whichever direction it is set.

    Reads `NORTHSTAR_CAPTURE_CONTENT` off the *running* settlement-agent
    deployment and asserts presence when ON, absence when OFF — so it can
    never pass by asserting nothing. Verified both directions live: with the
    deployment at `true`, the needles below are found inside
    `gen_ai.input.messages` / `gen_ai.output.messages`; flipped to `false`
    and redeployed, the same test asserts their absence and still passes.

    Needles must be things that genuinely traverse the model: a fragment of
    the prompt settlement.py builds, and the narrative the model returned.
    Do NOT use the claim's free-text description — settlement-agent is only
    ever sent {claimId, amount, currency}, so its absence would prove nothing.
    """
    trace = llm_settlement.trace
    reply = llm_settlement.reply
    # HAZARD: "is assessed as covered" — the run
    # prompt's exact wording (settlement.py:122) — sits one word away from
    # colliding with `model_request_parameters`'s static instructions text,
    # "has already **been** assessed as covered" (settlement.py's
    # `instructions=`). `model_request_parameters` is present regardless of
    # NORTHSTAR_CAPTURE_CONTENT (gated by the separate
    # `include_model_request_parameters`, always True — see
    # docs/telemetry-contract.md's "Content capture"), so in the OFF branch
    # it is the one span attribute in this trace still holding this fragment
    # of English. Confirmed empirically: "is assessed as covered" is NOT a
    # substring of "has already been assessed as covered" — "been" is the
    # entire margin. If a later prompt or instructions edit ever removes that
    # one word of difference, this needle starts matching
    # model_request_parameters even with capture OFF: a spurious OFF-mode
    # failure with nothing wrong with NORTHSTAR_CAPTURE_CONTENT itself. Watch
    # this margin whenever either string changes; it is not re-verified by
    # any assertion below.
    needles = [n for n in ("is assessed as covered", reply["narrative"][:40]) if n]
    hits = [
        (s.service, s.operation, k, needle)
        for needle in needles
        for s in trace.spans
        for k, v in s.attrs.items()
        if isinstance(v, str) and needle in v
    ]
    if _deployed_capture_mode():
        assert hits, (
            "capture is ON but no span carries any model content — "
            "content capture is not actually happening"
        )
    else:
        assert not hits, f"capture is OFF but model content leaked: {hits}"


def test_message_shape_is_recorded(llm_settlement):
    """Message *shape* is always present, mode-aware on whether content is too.

    Independent of `NORTHSTAR_CAPTURE_CONTENT`: every `chat` span carries
    `gen_ai.input.messages` and `gen_ai.output.messages` as JSON arrays of
    `{"role": ..., "parts": [{"type": ...}]}`, with tool-call ids and tool
    names retained — shape survives either way, which matters because a
    redaction test that only ever asserted absence would also pass if the
    instrumentation had emitted nothing at all (a telemetry regression
    dressed up as a compliance win).

    Whether a `content` key exists *inside* that shape is what actually
    depends on the deployed mode — but only for `text`/`thinking` parts.
    Measured live (this run had three chat turns): a `text` part carries
    `content` set to the model's prose, a `thinking` part carries `content`
    (empty string here, since the model did no visible extended thinking) —
    both present with capture ON, both keys absent with it OFF. A
    `tool_call` part carries `id`/`name`/`arguments` instead and has no
    `content` key at either setting — one whole chat turn in this run
    (`execute_payment`'s own call) produced an output message that was
    *only* a `tool_call` part, so asserting `content` on every single
    (span, key) surface regardless of what part types it holds would be
    wrong.

    The `invoke_agent` span's own `pydantic_ai.all_messages` — a non-semconv
    attribute holding the whole conversation, and therefore the single
    highest-risk surface in this trace — is checked the same way, because
    "content capture behaves as configured" is worth nothing if it only
    holds for the per-turn copies and not the full transcript.
    """
    chat_spans = _chat_spans(llm_settlement.trace)
    assert chat_spans, "no span with gen_ai.operation.name == 'chat' in the trace"

    def content_bearing_parts(node) -> list[dict]:
        """`text`/`thinking` parts — the only part types measured to carry a
        `content` key at all (with capture ON); `tool_call` parts never do,
        at either setting, so they are not evidence either way."""
        found = []
        if isinstance(node, dict):
            if node.get("type") in ("text", "thinking"):
                found.append(node)
            for value in node.values():
                found += content_bearing_parts(value)
        elif isinstance(node, list):
            for value in node:
                found += content_bearing_parts(value)
        return found

    agent_span = llm_settlement.trace.span(
        "invoke_agent settlement_agent", service="settlement-agent"
    )
    surfaces = [
        (span, key)
        for span in chat_spans
        for key in ("gen_ai.input.messages", "gen_ai.output.messages")
    ] + [(agent_span, "pydantic_ai.all_messages")]

    checked_any_content_bearing_part = False
    for span, key in surfaces:
        assert key in span.attrs, f"{span.operation!r} has no {key}"
        messages = json.loads(span.attrs[key])
        assert messages, f"{span.operation!r}'s {key} is empty"
        for message in messages:
            assert "role" in message, message
            assert "parts" in message, message
            for part in message["parts"]:
                assert "type" in part, part

        parts = content_bearing_parts(messages)
        if not parts:
            continue  # this surface's turn(s) were tool-call-only; no evidence here either way
        checked_any_content_bearing_part = True
        has_content = [p for p in parts if "content" in p]
        if _deployed_capture_mode():
            assert has_content == parts, (
                f"{span.operation!r}'s {key} has text/thinking part(s) with "
                f"no 'content' key — capture is ON but content is not "
                f"actually being recorded: {parts}"
            )
        else:
            assert not has_content, (
                f"{span.operation!r}'s {key} has 'content' key(s) on "
                f"text/thinking part(s) — capture is OFF but content is not "
                f"actually redacted: {has_content}"
            )
    assert checked_any_content_bearing_part, (
        "no chat span's messages contained a text/thinking part in this run "
        "— nothing to check the content-capture mode against"
    )


def test_tool_call_arguments_carries_the_settled_amount(llm_settlement):
    """Pins `gen_ai.tool.call.arguments` — the single strongest argument for
    defaulting capture ON.

    A span proving *that* €5,800 was executed says nothing about *why that
    amount* or on what stated grounds. `gen_ai.tool.call.arguments` on the
    `execute_tool propose_payment` span is what actually answers that: it is
    the literal amount and currency the model chose to propose.

    Measured (`pydantic_ai/capabilities/instrumentation.py`'s
    `_tool_span_attributes`, `InstrumentationNames.for_version(5)`):
    `gen_ai.tool.call.arguments` is `call.args_as_json_str()`, included only
    `if include_content` — the same `NORTHSTAR_CAPTURE_CONTENT` switch every
    other mode-aware assertion in this file reads, not a separate mechanism.
    So this test only runs its positive assertion when the deployment is ON;
    the OFF-mode absence of this same attribute is established by that
    shared source-level gating (see docs/telemetry-contract.md's "Content
    capture") rather than re-measured span-by-span here.
    """
    if not _deployed_capture_mode():
        pytest.skip(
            "gen_ai.tool.call.arguments is only expected present when capture is ON "
            "(see docs/telemetry-contract.md's include_content gating)"
        )

    span = llm_settlement.trace.span(
        "execute_tool propose_payment", service="settlement-agent"
    )
    raw = span.attrs.get("gen_ai.tool.call.arguments")
    assert raw, (
        f"{span.operation!r} has no gen_ai.tool.call.arguments even though "
        f"capture is ON; all its gen_ai.* keys: "
        f"{sorted(k for k in span.attrs if k.startswith('gen_ai.'))}"
    )
    arguments = json.loads(raw)
    assert arguments.get("amount") == llm_settlement.settled_amount, (
        f"gen_ai.tool.call.arguments={arguments!r} does not carry the "
        f"settled amount {llm_settlement.settled_amount!r}"
    )


def test_token_usage_is_recorded(llm_settlement):
    """Token accounting, at the names actually emitted.

    Measured at pydantic-ai 2.41.0: `gen_ai.usage.input_tokens` /
    `gen_ai.usage.output_tokens` on every `chat` span, and
    `gen_ai.aggregated_usage.input_tokens` /
    `gen_ai.aggregated_usage.output_tokens` on the `invoke_agent` span. The
    `aggregated_usage` spelling is a pydantic-ai extension with no
    counterpart in the GenAI semantic conventions; it is asserted because it
    was measured. Conditional `gen_ai.usage.details.*` counters
    (thinking/cache tokens) are not asserted — they depend on what the model
    did, not on our wiring.
    """
    trace = llm_settlement.trace
    emitted = sorted({k for s in trace.spans for k in s.attrs if k.startswith("gen_ai.")})

    for span in _chat_spans(trace):
        missing = CHAT_TOKEN_ATTRS - set(span.attrs)
        assert not missing, (
            f"{span.operation!r} is missing {sorted(missing)}; "
            f"all gen_ai.* keys emitted in this trace: {emitted}"
        )
        for key in sorted(CHAT_TOKEN_ATTRS):
            assert isinstance(span.attrs[key], int) and span.attrs[key] > 0, (
                f"{span.operation!r} has {key}={span.attrs[key]!r}"
            )

    agent_span = trace.span("invoke_agent settlement_agent", service="settlement-agent")
    missing = AGENT_TOKEN_ATTRS - set(agent_span.attrs)
    assert not missing, (
        f"invoke_agent span is missing {sorted(missing)}; "
        f"all gen_ai.* keys emitted in this trace: {emitted}"
    )


def test_model_request_parameters_carries_instructions_regardless_of_capture_mode(
    llm_settlement,
):
    """`model_request_parameters` is gated by a *different* setting entirely.

    `NORTHSTAR_CAPTURE_CONTENT` (`include_content`) is configurable,
    defaulting ON. `model_request_parameters` — a non-semconv attribute
    pydantic-ai 2.41.0 sets on every `chat` span, carrying the agent's system
    instructions verbatim together with the full JSON schema and docstring of
    every registered tool — is governed by the separate
    `include_model_request_parameters` setting, which stays at its library
    default (`True`) regardless of the capture-content mode. So this
    attribute's presence is asserted unconditionally here, in both directions
    of `NORTHSTAR_CAPTURE_CONTENT`; it is not part of the mode-aware
    assertions above.

    Nothing claimant-specific reaches it — the instructions are static text
    this repo wrote, and the schemas are our own function signatures — so
    this is not a data-subject leak. Those instructions and schemas contain
    no claim-specific data and are treated as an audit asset by design, not
    a gap — see docs/telemetry-contract.md. Pinned in the same spirit
    as `tests/e2e/test_claim_correlation.py`'s treatment of the FastMCP
    baggage gap: if a later pydantic-ai version starts gating this
    differently, or the instructions move, this test fails loudly and the
    telemetry contract gets updated deliberately instead of drifting.
    """
    chat_spans = _chat_spans(llm_settlement.trace)
    assert chat_spans, "no chat span in the trace"

    instruction_fragment = "You settle motor insurance claims for Northstar Insurance."
    for span in chat_spans:
        raw = span.attrs.get("model_request_parameters")
        assert isinstance(raw, str), (
            f"{span.operation!r} no longer carries model_request_parameters; "
            "the measured content-capture gap this test pins has changed — "
            "re-measure and update docs/telemetry-contract.md"
        )
        assert instruction_fragment in raw, (
            f"{span.operation!r}'s model_request_parameters no longer contains "
            "the agent's instructions; the measured gap has changed — "
            "re-measure and update docs/telemetry-contract.md"
        )
        for name in sorted(TOOL_NAMES):
            assert name in raw, f"{name!r} absent from model_request_parameters"


def test_anthropic_client_span_exists_and_is_parented_to_chat(llm_settlement):
    """The model's own HTTP hop has a CLIENT span, distinct from the MCP hop's.

    `anthropic==1.4.0` speaks `httpx2`, a distribution separate from
    `httpx`. `bootstrap.py` instruments both `HTTPXClientInstrumentor`
    (`_module_name` `"httpx"`, covers settlement-agent's MCP round trips)
    and `HTTPX2ClientInstrumentor` (covers the model's own HTTPS request to
    `api.anthropic.com`).

    Both `HTTPXClientInstrumentor` and `HTTPX2ClientInstrumentor` live in the
    same `opentelemetry.instrumentation.httpx` module and share its
    `get_tracer(__name__, ...)` call, so `otel.scope.name` is identical for
    both (`"opentelemetry.instrumentation.httpx"`) — the new span is
    identified by `server.address == "api.anthropic.com"`, not by scope name.

    Attribute names measured live, not recalled: this deployment sets
    `OTEL_SEMCONV_STABILITY_OPT_IN=http` (`deploy/k8s/settlement-agent.yaml`),
    and a full attribute dump of an existing MCP-bound httpx CLIENT span in
    this same trace confirmed the **stable** HTTP semconv spelling is what
    this instrumentation actually emits here: `http.request.method`,
    `http.response.status_code`, `server.address`, `server.port`,
    `url.full`, `network.peer.address`, `network.peer.port`,
    `network.protocol.version` — not the legacy `http.method` / `http.url` /
    `http.status_code`. The new anthropic-bound span is asserted on that same
    measured set.

    One CLIENT span is expected per model request (three, in this fixture's
    three-turn conversation) — but the exact turn count is not this test's
    concern, so it asserts "at least one, and every one well-formed and
    correctly parented" rather than a hardcoded turn count. A single turn can
    also produce *more* than one such span: `anthropic`'s own SDK retries
    (`DEFAULT_MAX_RETRIES = 2`) by re-entering the transport the instrumentor
    wraps, so a 429/529 that succeeds on retry is one CLIENT span per HTTP
    attempt, not per turn. The assertions below do not require every attempt
    to be `200` — only that every attempt is a well-formed span and that
    each turn got a `200` on *some* attempt — because a healthy,
    retried-then-succeeded settlement is precisely the scenario this span
    exists to make visible rather than to forbid.
    """
    trace = llm_settlement.trace
    httpx_spans = [
        s
        for s in trace.spans
        if s.attrs.get("otel.scope.name") == "opentelemetry.instrumentation.httpx"
    ]
    assert httpx_spans, "no httpx-instrumented CLIENT spans at all in this trace"

    to_model = [s for s in httpx_spans if s.attrs.get("server.address") == "api.anthropic.com"]
    assert to_model, (
        "no httpx CLIENT span targets api.anthropic.com; HTTPX2ClientInstrumentor "
        "is presumably not instrumented in bootstrap.py"
    )

    chat_spans = _chat_spans(trace)
    chat_span_ids = {s.span_id for s in chat_spans}
    assert chat_span_ids, "no chat span in the trace to parent the CLIENT span to"

    # Asserting `http.response.status_code == 200` on *every* anthropic-bound
    # span would be wrong. `anthropic` retries (`DEFAULT_MAX_RETRIES = 2`,
    # `anthropic/_constants.py`) by re-entering its own transport per
    # attempt, and the installed instrumentor wraps
    # `AsyncHTTPTransport.handle_async_request` -- a per-attempt wrap point,
    # not a per-logical-request one (`opentelemetry/instrumentation/httpx/
    # __init__.py:1295-1299`) -- so a retried call produces one CLIENT span
    # *per HTTP attempt*, each with its own status. A 429 or 529 that
    # succeeds on retry is exactly the signal this span exists to surface
    # (see docs/telemetry-contract.md's rationale for this gap), so asserting
    # every attempt is 200 would fail a *healthy* demo -- the settlement
    # completes and the amount is executed even though one attempt reported
    # `429`. The assertions below pin two properties instead: every attempt
    # is a well-formed CLIENT span (an int status code, correctly parented),
    # and each model turn -- attempts grouped by the chat span they are
    # parented to -- got a 200 on *some* attempt. A turn where every attempt
    # failed still fails this loudly; only a turn that failed then succeeded
    # is tolerated.
    by_chat_parent: dict[str, list] = {}
    for span in to_model:
        assert span.kind == "client", (span.operation, span.kind)
        assert span.attrs.get("http.request.method") == "POST", span.attrs
        status = span.attrs.get("http.response.status_code")
        assert isinstance(status, int), (
            f"CLIENT span {span.span_id!r} (parent {span.parent_id!r}) has no "
            f"integer http.response.status_code ({status!r}) -- a genuine "
            "connection-level failure, not a retried-then-succeeded attempt"
        )
        assert span.parent_id in chat_span_ids, (
            f"CLIENT span {span.span_id!r} (parent {span.parent_id!r}) is not "
            f"parented to any of the chat spans {sorted(chat_span_ids)!r}"
        )
        by_chat_parent.setdefault(span.parent_id, []).append(status)

    for parent_id, statuses in by_chat_parent.items():
        assert 200 in statuses, (
            f"chat span {parent_id!r}: none of its {len(statuses)} anthropic "
            f"CLIENT span attempt(s) succeeded (statuses={statuses}) -- every "
            "attempt for this model turn failed"
        )

    # pydantic-ai's own chat span also names the hop (it wraps the httpx
    # CLIENT span). Restricted to chat spans that actually carry a
    # server.address: an
    # errored turn can lack it entirely, and that is not the same failure as
    # a turn that reached the wrong host (the thing this assertion exists to
    # catch) -- exact set equality over the *full* `chat_spans` list turned
    # one into the other (`{None, "api.anthropic.com"}` != `{"api.anthropic.com"}`).
    addresses = {
        s.attrs.get("server.address") for s in chat_spans if s.attrs.get("server.address") is not None
    }
    assert addresses == {"api.anthropic.com"}, addresses
