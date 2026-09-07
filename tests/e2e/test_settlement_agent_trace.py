"""Asserts the A2A hop is now correctly traced end to end into
settlement-agent, and pins the one remaining confirmed propagation gap down
as xfail rather than asserting behaviour that empirically does not exist.

Chain under test: A2A client -> settlement-agent -> payments-mcp -> payments-api.

**Fix round 1 (A18).** Task 4's first pass found that `a2a-sdk` 0.3.7's own
telemetry never extracts inbound W3C trace context — an injected
`traceparent` header was silently ignored and every request started a fresh
root trace (full evidence in `agents/settlement-agent/NOTES.md`). A18 rules
that this is fixed with real OTel framework instrumentation
(`opentelemetry-instrumentation-starlette`, applied to the built app in
`main.py`) rather than hand-rolled spans. That fix is now verified by capture
below: an injected `traceparent` produces a server span
(`POST /`, from StarletteInstrumentor — a2a-sdk's own spans nest under it)
that is a genuine child of the injected span ID inside the injected trace,
per A14. Because the injected root span never itself lands in Jaeger, the
`POST /` entry span is a legitimate orphan by the harness's definition; per
A17 the exclusion below is pinned to exactly that one span (its operation
*and* its `parent_id`), not a count tolerance.

**One gap remains, deliberately.** settlement-agent's MCP client
(`mcp==1.9.4`'s high-level `ClientSession.call_tool()`, per this task's
explicit scope) still does not inject `params._meta`, and FastMCP only ever
extracts trace context from `params._meta` — never HTTP headers — so
payments-mcp's tool-call spans still do not join this trace, even though
settlement-agent's own outbound HTTP request to payments-mcp carries a
`traceparent` header (ordinary ambient-context injection from
`HTTPXClientInstrumentor`). Per the coordinator's ruling, that stays Task 7's
job (shared `correlation.py`, A6) because more than one service needs it.
The three tests below asserting cross-service joining remain
`xfail(strict=True)`, citing this one remaining gap precisely.

**Fix round 2 (A19 / review Finding 1).** The round-1 fixture only asserted
`"error" not in result` on the settlement reply — which tests nothing about
the payload. Restoring the brief's original three-`enqueue_event` executor
(the plan defect fixed in round 1, see `executor.py` and NOTES.md §8) would
still satisfy that assertion, because a2a-sdk's blocking `message/send`
returns the *first* enqueued `Message` regardless of which executor is
running, and that first message is always non-error text. The suite would
report green while silently returning narration instead of the documented
`{claimId, paymentId, amount, status, narrative}` JSON that Task 5 and
Task 7 consume. `test_message_send_returns_the_full_settlement_reply` below
replaces that vacuous check with one that parses and asserts the actual
payload, and was confirmed to fail against the brief's three-message
executor (see task-4-report.md's fix-round-2 section for the verbatim
failure). `test_agent_card_is_served` similarly now diffs the full served
card against `contracts/agent-cards/settlement-agent.json` rather than
checking two fields (review Finding 3), so served/contract drift is caught
here instead of shipping silently.

**Net effect after both fix rounds**: trace context now propagates *into*
settlement-agent from its A2A caller, but not *out* to payments-mcp. The
settlement reply's actual JSON payload, and the served agent card's parity
with its committed contract, are both now under test rather than assumed.
"""
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tests.harness.jaeger import JaegerClient

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
SETTLEMENT_URL = os.environ.get("SETTLEMENT_AGENT_URL", "http://localhost:19002")

ENTRY_SPAN = "POST /"  # StarletteInstrumentor's ASGI server span (A18)

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_CARD_CONTRACT = REPO_ROOT / "contracts" / "agent-cards" / "settlement-agent.json"


def send_message(text: str, traceparent: str | None = None) -> dict:
    """Minimal A2A JSON-RPC message/send call.

    Uses raw JSON-RPC rather than the SDK client so the test asserts the wire
    contract, not the SDK's convenience layer.
    """
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
    headers = {"traceparent": traceparent} if traceparent else None
    response = httpx.post(f"{SETTLEMENT_URL}/", json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()


def test_agent_card_is_served():
    response = httpx.get(f"{SETTLEMENT_URL}/.well-known/agent-card.json", timeout=15)
    response.raise_for_status()
    card = response.json()
    assert card["name"] == "settlement-agent"
    assert any(skill["id"] == "settle-claim" for skill in card["skills"])

    # Review Finding 3: the card is baked into the image at build time by
    # rebuild-and-deploy.sh's staging step, whose whole point is that
    # contracts/agent-cards/<component>.json is the single source of truth.
    # Checking only `name` and one skill id lets the served card silently
    # drift from the committed contract (e.g. an edited `description` or
    # `version` that never gets rebuilt) without failing anything. Diff the
    # whole document instead.
    contract = json.loads(AGENT_CARD_CONTRACT.read_text())
    assert card == contract, (
        "served agent card does not match the committed contract "
        f"({AGENT_CARD_CONTRACT}) — rebuild and redeploy settlement-agent"
    )


@pytest.fixture(scope="module")
def settlement_trace():
    # A14: this test originates the request, so it mints its own trace/span
    # ID and sends it as a `traceparent` header (an A2A/HTTP boundary, so a
    # header is the right carrier — contrast the MCP boundary below, which
    # never gets an HTTP-header workaround). Looked up by that exact ID
    # (wait_for_trace_id), never by search, per A14's rule that a search is
    # only used when the trace ID genuinely cannot be known up front — after
    # A18's fix, it now can be, for this hop.
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    traceparent = f"00-{trace_id}-{span_id}-01"

    claim_id = f"CLM-TEST-{uuid.uuid4().hex[:8]}"
    result = send_message(
        json.dumps({"claimId": claim_id, "amount": 6200, "currency": "EUR"}),
        traceparent=traceparent,
    )
    assert "error" not in result, f"A2A call failed: {result}"

    client = JaegerClient(JAEGER_URL)
    trace = client.wait_for_trace_id(trace_id, operation=ENTRY_SPAN, timeout=90)
    return SimpleNamespace(
        trace=trace, trace_id=trace_id, span_id=span_id, claim_id=claim_id, result=result
    )


def test_message_send_returns_the_full_settlement_reply(settlement_trace):
    """Plan defect (A19, review Finding 1): the brief's Step 8 executor
    sketch enqueued three Message events, assuming a blocking message/send
    caller would receive the last one. It doesn't — a2a-sdk 0.3.7's
    ResultAggregator.consume_and_break_on_interrupt returns as soon as it
    sees the FIRST Message-kind event (verified, NOTES.md §8), so the
    brief's version would have replied with "Assessed settlement: ..."
    narration text and silently dropped the promised JSON. `executor.py` now
    enqueues exactly one Message — the final JSON result. This test asserts
    that payload's substance, not merely that no error occurred: restoring
    the brief's three-message executor makes this test fail (verified, see
    task-4-report.md fix round 2), while the old `"error" not in result`
    assertion alone would not have caught it.
    """
    payload = json.loads(settlement_trace.result["result"]["parts"][0]["text"])
    assert payload["claimId"] == settlement_trace.claim_id
    assert payload["paymentId"].startswith("PAY-")
    assert payload["amount"] == 6200
    assert payload["currency"] == "EUR"
    assert payload["status"] == "executed"
    assert payload["narrative"], "narrative must be non-empty"


def test_settlement_agent_entry_span_is_parented_to_the_injected_context(settlement_trace):
    """The A18 fix, verified by capture, not by reasoning: StarletteInstrumentor
    extracts the injected `traceparent` and starts the entry span as a real
    child of it — not a new, disconnected root, which is what this same
    check proved *before* A18 (see NOTES.md for the before/after)."""
    trace = settlement_trace.trace
    entry = trace.span(ENTRY_SPAN)
    assert entry.parent_id == settlement_trace.span_id, (
        f"{ENTRY_SPAN!r} span's parent is {entry.parent_id!r}, expected the "
        f"injected span id {settlement_trace.span_id!r} — inbound trace "
        "context did not propagate"
    )
    # Jaeger's TraceID.String() drops a whole-number leading zero nibble when
    # the high 64 bits start with a zero hex digit (see
    # model.TraceID.String() in jaeger-idl), so the returned ID can be
    # shorter than 32 hex chars. Zero-pad it back out to 32 for an exact
    # comparison rather than stripping leading zeros from both sides (review
    # nit 10), which could mask a genuine mismatch that happened to leave
    # both operands with the same stripped form.
    assert trace.trace_id.lower().zfill(32) == settlement_trace.trace_id.lower()


def test_settlement_agent_trace_has_no_unexpected_orphans(settlement_trace):
    """A17: the injected span ID is never itself a real span in this trace,
    so the entry span (parented to it) is an unavoidable, legitimate orphan.
    Pinned to exactly that one span — operation *and* parent_id — so a
    genuine propagation defect anywhere else (a real internal hop dropping
    context) still fails this test, per A17's explicit rule against a count
    tolerance or a name allowlist."""
    trace = settlement_trace.trace
    orphans = trace.orphans()
    unexpected = [
        s
        for s in orphans
        if not (s.operation == ENTRY_SPAN and s.parent_id == settlement_trace.span_id)
    ]
    assert not unexpected, (
        "orphan spans present beyond the expected synthetic root, context "
        f"propagation is broken: {[(s.service, s.operation, s.parent_id) for s in unexpected]}"
    )


def test_settlement_agent_outbound_mcp_calls_are_traced(settlement_trace):
    """HTTPXClientInstrumentor (bootstrap.py, added for this task per A13)
    emits CLIENT spans for the MCP streamable-http transport's own HTTP
    calls to payments-mcp, nested inside the (now correctly externally
    rooted) A2A request span. This proves this agent's own outbound HTTP hop
    is instrumented and uses the stable HTTP semconv spelling (A13) — even
    though the far side does not yet join the trace (the one remaining gap)."""
    client_spans = [s for s in settlement_trace.trace.spans if s.kind == "client"]
    assert client_spans, "no outbound CLIENT spans found for the MCP transport"
    for span in client_spans:
        target = span.attrs.get("url.full") or ""
        assert "payments-mcp" in target, f"unexpected outbound target: {span.attrs}"
        # Stable HTTP semconv spelling (A13): OTEL_SEMCONV_STABILITY_OPT_IN=http
        # is set on the deployment manifest specifically so these come out
        # this way rather than the legacy http.method/http.url/http.status_code.
        assert "http.request.method" in span.attrs
        assert "http.method" not in span.attrs


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Confirmed, remaining gap (see module docstring / NOTES.md): "
        "settlement-agent's MCP client does not inject params._meta, so "
        "FastMCP's tool-call spans never join this trace. This one stays "
        "Task 7's job (shared correlation.py, A6) per the coordinator's "
        "ruling. Flip once client-side _meta injection lands, and remove "
        "this marker."
    ),
)
def test_all_four_services_are_in_one_trace(settlement_trace):
    services = settlement_trace.trace.services()
    assert {"settlement-agent", "payments-mcp", "payments-api"} <= services, (
        f"expected one trace spanning the chain; got services: {sorted(services)}"
    )


@pytest.mark.xfail(
    strict=True,
    reason="Same remaining gap as test_all_four_services_are_in_one_trace.",
)
def test_mcp_tool_calls_are_present(settlement_trace):
    operations = {s.operation for s in settlement_trace.trace.spans}
    assert "tools/call propose_payment" in operations
    assert "tools/call execute_payment" in operations


@pytest.mark.xfail(
    strict=True,
    reason="Same remaining gap as test_all_four_services_are_in_one_trace.",
)
def test_settlement_agent_span_precedes_the_payment(settlement_trace):
    trace = settlement_trace.trace
    agent_spans = [s for s in trace.spans if s.service == "settlement-agent"]
    api_spans = [s for s in trace.spans if s.service == "payments-api"]
    assert agent_spans, "no settlement-agent spans"
    assert api_spans, "no payments-api spans"
    agent_ids = {s.span_id for s in agent_spans}
    by_id = {s.span_id: s for s in trace.spans}

    def ancestors(span):
        seen = set()
        current = span
        while current and current.parent_id and current.parent_id not in seen:
            seen.add(current.parent_id)
            yield current.parent_id
            current = by_id.get(current.parent_id)

    assert any(
        agent_ids & set(ancestors(api_span)) for api_span in api_spans
    ), "payments-api span does not descend from settlement-agent"
