"""Asserts the single-trace property survives putting a language model in the path.

Chain under test: A2A client -> settlement-agent (**model decides**) ->
payments-mcp -> payments-api.

M1.1 proved one trace spans a *deterministic* chain. M1.2 replaced
settlement-agent's hardcoded decision with a real PydanticAI agent on Claude
that chooses and calls the MCP payment tools itself (`settlement.py`). The
only question this file exists to answer is whether the chain is still *one*
trace once a model sits in the middle of it — and, second, whether
`include_content=False` (bootstrap.py:91) genuinely redacts, so the
telemetry contract's content-capture claim is testable rather than
aspirational.

Everything asserted below was measured from a live capture first and only
then written down. Where this task's brief predicted an attribute name or a
trace shape and the measurement disagreed, the measurement won and the
difference is recorded — in the docstrings here and in
`.superpowers/sdd/2026-09-08-m1.2-intelligence/task-4-report.md`. Four such
differences are worth knowing before reading further:

1. **`assert_single_root`/`assert_no_orphans` cannot apply to this fixture.**
   The brief's sketch called both. An A2A-originated trace has *zero* roots
   and exactly one orphan by construction: the test mints its own
   `traceparent`, so the injected span never itself lands in Jaeger and
   settlement-agent's ASGI entry span (`POST /`) is parented to an ID no
   span in the trace owns. That is A17's known, legitimate orphan, pinned
   here by operation *and* `parent_id` exactly as
   `test_settlement_agent_trace.py` and `test_claim_correlation.py` already
   pin it — never by a count tolerance. The genuinely root-owning,
   zero-orphan form of this claim is `tests/e2e/test_browser_root_span.py`,
   whose real browser root now drives this same LLM path end to end.

2. **`Trace.services` is a method, not a property** (`tests/harness/jaeger.py`),
   so the brief's `{"payments-mcp", ...} <= trace.services` would have
   compared a set against a bound method. Called as `trace.services()` here.

3. **`include_content=False` redacts the messages but not
   `model_request_parameters`.** Measured: `gen_ai.input.messages` and
   `gen_ai.output.messages` carry message *shape* only — role,
   `parts[].type`, tool-call ids and tool names, no `content` key anywhere.
   But pydantic-ai 2.41.0 additionally sets a non-semconv
   `model_request_parameters` attribute on every `chat` span that contains
   the agent's system instructions verbatim plus the full tool JSON schemas.
   That is a real content-capture gap, pinned by
   `test_include_content_false_does_not_redact_model_request_parameters` so
   it cannot be forgotten, and left for the telemetry contract to state
   rather than papered over here.

4. **The model's own HTTP hop has no CLIENT span.** `anthropic==1.4.0`
   speaks `httpx2`, not `httpx`; `bootstrap.py` instruments
   `HTTPXClientInstrumentor` (`_module_name = "httpx"`), and the separate
   `HTTPX2ClientInstrumentor` in the same package is not applied. So the
   MCP hop gets httpx CLIENT spans and the `api.anthropic.com` request does
   not — pydantic-ai's own `chat` CLIENT span is the only span representing
   it. Recorded, pinned, not fixed here (`bootstrap.py` is out of scope for
   this task).

**Cost gate.** Every test in this file shares one module-scoped live model
run, and the whole file is gated behind `RUN_BROWSER_TESTS=1` — the same
convention `test_browser_root_span.py` uses, and the only skip convention
this suite has. That keeps a bare `pytest tests/` free of both a cluster
dependency and a token bill, while `scripts/smoke-test.sh` (which sets the
variable unconditionally) always runs it. Prose is asserted nowhere: the
model's wording varies run to run, so only structure, names and numbers are
checked.
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

ENTRY_SPAN = "POST /"  # StarletteInstrumentor's ASGI server span (A18)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason=(
        "needs the live kind cluster and spends real model tokens; gated "
        "behind the same RUN_BROWSER_TESTS=1 flag scripts/smoke-test.sh sets "
        "unconditionally"
    ),
)

# The amount the A2A request asks for. Nothing below asserts the model
# settled *this* number: M1 is ungoverned by design — the model can propose
# any amount and nothing stops it — so a test that failed on a different
# amount would be a guard rail smuggled into the suite. The amount actually
# recorded by the ledger is surfaced on the fixture (and in this file's
# failure messages) so a divergence is *reported*, never clamped.
REQUESTED_AMOUNT = 6200

# Measured, not predicted. The `gen_ai.usage.*` names below are the ones
# pydantic-ai 2.41.0 actually emitted in live capture; see the report for the
# complete measured inventory. Deliberately excluded from the assertion
# because they are conditional on what the model did, not on our wiring:
#   * gen_ai.usage.details.thinking_tokens          (only when it thought)
#   * gen_ai.usage.details.cache_creation_input_tokens
#   * gen_ai.usage.details.cache_read_input_tokens
# The brief guessed `gen_ai.usage.input_tokens` and that guess happened to be
# right — it is asserted here because it was measured, not because it was
# guessed.
CHAT_TOKEN_ATTRS = {"gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"}
AGENT_TOKEN_ATTRS = {
    "gen_ai.aggregated_usage.input_tokens",
    "gen_ai.aggregated_usage.output_tokens",
}

TOOL_NAMES = {"propose_payment", "execute_payment"}


def _send(text: str, traceparent: str) -> dict:
    """Minimal A2A JSON-RPC message/send, same wire-level shape as
    test_settlement_agent_trace.py's helper (the fixture style this task's
    brief asks to reuse rather than re-invent)."""
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
        f"{SETTLEMENT_URL}/", json=payload, headers={"traceparent": traceparent}, timeout=180
    )
    response.raise_for_status()
    return response.json()


@pytest.fixture(scope="module")
def llm_settlement():
    """One live, LLM-driven settlement, shared by every test in this file.

    A14: this test originates the request, so it mints its own trace and span
    ID, sends them as a `traceparent` header, and looks the trace up by that
    exact ID. Never `find_trace`/`wait_for_trace` — a search by operation
    name or service silently returned a 14-minute-old trace twice during
    M1.1, and by construction `wait_for_trace_id` cannot.
    """
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    traceparent = f"00-{trace_id}-{span_id}-01"
    claim_id = f"CLM-TEST-{uuid.uuid4().hex[:8]}"

    result = _send(
        json.dumps({"claimId": claim_id, "amount": REQUESTED_AMOUNT, "currency": "EUR"}),
        traceparent,
    )
    assert "error" not in result, f"A2A call failed: {result}"
    reply = json.loads(result["result"]["parts"][0]["text"])
    assert reply["status"] == "executed", reply
    assert reply["narrative"], "the model returned an empty narrative"

    client = JaegerClient(JAEGER_URL)
    # A20/A20a: wait for the whole chain to land *and hold steady* for the
    # harness's 10s quiet window — comfortably past every service's default
    # 5s BatchSpanProcessor delay. 2s and 6s windows both produced false
    # negatives during M1.1; 10s is the value that held. The three model
    # turns make this trace larger and slower to assemble than M1.1's, so
    # the overall budget is wider than the deterministic tests' too.
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

    This is the milestone's gate, stated at the boundary this fixture can
    actually own. Two halves:

      * the trace's only orphan is A17's known synthetic one — the entry span
        parented to the injected span ID that was never itself exported —
        pinned by operation *and* parent, so a real propagation defect
        anywhere else still fails here;
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
    `tools/call <name>` SERVER span in payments-mcp is a *direct child* of
    the matching `execute_tool <name>` span in settlement-agent. That is the
    strongest available statement that the model's tool choice is what
    caused the downstream call — `mcp_tools.py` injects `params._meta` from
    the context active inside pydantic-ai's tool span, so if the model's
    instrumentation and the MCP client ever stopped sharing a context, this
    parentage would break while a bare service-list check still passed.
    """
    trace = llm_settlement.trace
    services = trace.services()
    assert {"payments-mcp", "payments-api"} <= services, sorted(services)

    for name in sorted(TOOL_NAMES):
        tool_span = trace.span(f"execute_tool {name}", service="settlement-agent")
        mcp_span = trace.span(f"tools/call {name}", service="payments-mcp")
        assert mcp_span.parent_id == tool_span.span_id, (
            f"payments-mcp's {mcp_span.operation!r} span is parented to "
            f"{mcp_span.parent_id!r}, not to settlement-agent's "
            f"{tool_span.operation!r} span ({tool_span.span_id!r})"
        )


def test_no_prompt_or_completion_text_appears_in_any_span_attribute(llm_settlement):
    """`include_content=False`, asserted as a compliance property.

    This is what makes the telemetry contract's content-capture claim
    testable rather than aspirational. Verified able to fail: flipping
    `include_content=True` in `bootstrap.py` and redeploying turns this test
    RED (both message needles then appear inside `gen_ai.input.messages` /
    `gen_ai.output.messages`); the flip was reverted. See the task report.

    Every needle genuinely traverses the model:

      * the exact prompt `settlement.py` builds for this run, and the
        distinctive fragment ``is assessed as covered`` from it. Note the
        agent's *static* instructions say "has already **been** assessed as
        covered" — the wording differs by one word, so this fragment can only
        match the run prompt, never the instruction text that
        `model_request_parameters` is separately known to leak (see
        `test_include_content_false_does_not_redact_model_request_parameters`).
        That is why the whole prompt is a needle too: it removes any doubt
        that the needle is prompt-specific.
      * the head of the narrative the model actually returned, read back from
        the A2A reply — a string that exists only because the model produced
        it, and only in this run.

    Deliberately NOT used as a needle: the claim's free-text description.
    settlement-agent is only ever sent `{claimId, amount, currency}`, so the
    description never reaches the model at all — asserting its absence would
    pass trivially while proving nothing about redaction.
    """
    needles = [
        n
        for n in (
            llm_settlement.prompt,
            "is assessed as covered",
            llm_settlement.reply["narrative"][:40],
        )
        if n
    ]
    offenders = [
        (s.service, s.operation, k, needle)
        for needle in needles
        for s in llm_settlement.trace.spans
        for k, v in s.attrs.items()
        if isinstance(v, str) and needle in v
    ]
    assert not offenders, f"include_content=False leaked model content: {offenders}"


def test_message_shape_is_recorded_without_content(llm_settlement):
    """The positive half of the redaction claim: shape is kept, content is not.

    A redaction test that only asserts absence would also pass if the
    instrumentation had emitted nothing at all — which would be a telemetry
    regression dressed up as a compliance win. Measured behaviour at
    pydantic-ai 2.41.0 with `include_content=False`: every `chat` span
    carries `gen_ai.input.messages` and `gen_ai.output.messages` as JSON
    arrays of `{"role": ..., "parts": [{"type": ...}]}`, with tool-call ids
    and tool names retained and no `content` key at any depth. Both
    properties are asserted here.

    The `invoke_agent` span's own `pydantic_ai.all_messages` — a non-semconv
    attribute holding the whole conversation, and therefore the single
    highest-risk surface in this trace — is checked the same way, because
    "the messages are redacted" is worth nothing if it only holds for the
    per-turn copies and not the full transcript.
    """
    chat_spans = _chat_spans(llm_settlement.trace)
    assert chat_spans, "no span with gen_ai.operation.name == 'chat' in the trace"

    def content_keys(node, path="") -> list[str]:
        found = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "content":
                    found.append(f"{path}.{key}")
                found += content_keys(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found += content_keys(value, f"{path}[{index}]")
        return found

    agent_span = llm_settlement.trace.span(
        "invoke_agent settlement_agent", service="settlement-agent"
    )
    surfaces = [
        (span, key)
        for span in chat_spans
        for key in ("gen_ai.input.messages", "gen_ai.output.messages")
    ] + [(agent_span, "pydantic_ai.all_messages")]

    for span, key in surfaces:
        assert key in span.attrs, f"{span.operation!r} has no {key}"
        messages = json.loads(span.attrs[key])
        assert messages, f"{span.operation!r}'s {key} is empty"
        for message in messages:
            assert "role" in message, message
            assert "parts" in message, message
            for part in message["parts"]:
                assert "type" in part, part
        leaks = content_keys(messages)
        assert not leaks, (
            f"{span.operation!r}'s {key} contains 'content' key(s) "
            f"{leaks} — include_content=False is not in effect"
        )


def test_token_usage_is_recorded(llm_settlement):
    """Token accounting, at the names actually emitted.

    Measured at pydantic-ai 2.41.0, not taken from this task's plan:
    `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` on every
    `chat` span, and `gen_ai.aggregated_usage.input_tokens` /
    `gen_ai.aggregated_usage.output_tokens` on the `invoke_agent` span. The
    `aggregated_usage` spelling is a pydantic-ai extension with no
    counterpart in the GenAI semantic conventions; it is asserted because it
    was measured. Conditional `gen_ai.usage.details.*` counters
    (thinking/cache tokens) are recorded in the report but not asserted —
    they depend on what the model did, not on our wiring.
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


def test_include_content_false_does_not_redact_model_request_parameters(llm_settlement):
    """A measured content-capture gap, pinned so it cannot be forgotten.

    `include_content=False` redacts `gen_ai.input.messages` and
    `gen_ai.output.messages` (asserted above). It does **not** touch
    `model_request_parameters`, a non-semconv attribute pydantic-ai 2.41.0
    sets on every `chat` span, which carries the agent's system instructions
    verbatim together with the full JSON schema and docstring of every
    registered tool.

    Nothing claimant-specific reaches it — the instructions are static text
    this repo wrote, and the schemas are our own function signatures — so
    this is not a data-subject leak. It is still content that a reader of
    `include_content=False` would reasonably expect to be absent, and for an
    insurer it is the operative policy text sitting in a telemetry backend.
    The gap is asserted as *present* on purpose, in the same spirit as
    `tests/e2e/test_claim_correlation.py`'s treatment of the FastMCP baggage
    gap: if a later pydantic-ai version starts redacting this, or if the
    instructions move, this test fails loudly and the telemetry contract gets
    updated deliberately instead of drifting.
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


def test_the_models_own_http_hop_has_no_client_span(llm_settlement):
    """Another measured gap: the request to api.anthropic.com is untraced.

    `anthropic==1.4.0` speaks `httpx2`. `bootstrap.py` applies
    `HTTPXClientInstrumentor`, whose `_module_name` is `"httpx"`; the
    separate `HTTPX2ClientInstrumentor` shipped in the same
    `opentelemetry-instrumentation-httpx` package is not applied. Measured
    consequence: settlement-agent's six MCP round trips each get an httpx
    CLIENT span, and the model's own HTTPS request gets none — pydantic-ai's
    `chat` CLIENT span (which does carry `server.address =
    "api.anthropic.com"`) is the only span representing that hop, so its
    status code, retries and latency-on-the-wire are invisible.

    Pinned as measured rather than fixed: `bootstrap.py` is explicitly out of
    scope for this task. Closing it is a one-line addition there, and this
    test turning RED is the signal that it happened.
    """
    trace = llm_settlement.trace
    httpx_spans = [
        s
        for s in trace.spans
        if s.attrs.get("otel.scope.name") == "opentelemetry.instrumentation.httpx"
    ]
    assert httpx_spans, "no httpx-instrumented CLIENT spans at all in this trace"

    to_model = [
        (s.service, s.operation, s.attrs.get("server.address"))
        for s in httpx_spans
        if "anthropic" in (s.attrs.get("server.address") or "")
        or "anthropic" in (s.attrs.get("url.full") or "")
    ]
    assert not to_model, (
        "an httpx CLIENT span for the model call now exists: "
        f"{to_model}. HTTPX2ClientInstrumentor was presumably added to "
        "bootstrap.py — good; re-measure and update "
        "docs/telemetry-contract.md, then retire this test"
    )

    # The positive half: pydantic-ai's own chat span is what names the hop.
    addresses = {s.attrs.get("server.address") for s in _chat_spans(trace)}
    assert addresses == {"api.anthropic.com"}, addresses
