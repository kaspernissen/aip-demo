"""Asserts the A2A hop is now correctly traced end to end into
settlement-agent, and that the MCP hop below it now joins the same trace.

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

**Task 7: the MCP-hop gap is now closed.** settlement-agent's MCP client
(`mcp_tools.py`) now injects `params._meta` (traceparent + baggage) via the
lower-level `ClientSession.send_request()` API — `mcp==1.9.4`'s high-level
`call_tool()` has no way to set `_meta` at all (see
`agents/settlement-agent/NOTES.md` §10). FastMCP 4.0.3's server-side
extraction (already proven working in Task 3/A14a) joins the trace, so
`test_all_four_services_are_in_one_trace`, `test_mcp_tool_calls_are_present`
and `test_settlement_agent_span_precedes_the_payment` below are no longer
`xfail(strict=True)` — they assert real, confirmed-by-capture behaviour.
(The `northstar.claim.id` *attribute* still does not cross this hop — a
separate, confirmed FastMCP gap; see `tests/e2e/test_claim_correlation.py`
and `agents/settlement-agent/NOTES.md` §11.)

**Net effect after all fix rounds**: trace context now propagates all the way
from an A2A client into settlement-agent, through the MCP hop into
payments-mcp, and on into payments-api — one trace, no orphans beyond the
expected synthetic root. The settlement reply's actual JSON payload, and the
served agent card's parity with its committed contract, are both under test.
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
    # Fix round (final-review.md SF-9): this was `timeout=60`, sized for the
    # deterministic stub this test predates M1.2's model call. Measured
    # settlement latency is 2.6-7.9s (Jaeger, 10 model-driven runs), so 60s
    # was never close to firing -- but this call runs inside a
    # module-scoped fixture, so a timeout here reports all of this file's
    # tests as ERROR with a traceback rather than one named failure. Aligned
    # on the same reasoned value test_settlement_llm_trace.py already uses:
    # a settlement is now three sequential model turns plus two MCP round
    # trips, not one hardcoded function call, so a 529-triggered backoff on
    # a slow uplink deserves real headroom.
    response = httpx.post(f"{SETTLEMENT_URL}/", json=payload, headers=headers, timeout=180)
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
    # A20/A20a (Task 7 update): now that settlement-agent's MCP client injects
    # params._meta, payments-mcp and payments-api join this trace too, on
    # their own independent BatchSpanProcessor export timers. Before Task 7,
    # ENTRY_SPAN appearing was sufficient (the trace legitimately stopped at
    # settlement-agent); now the fixture must wait for the whole chain to
    # land and hold steady via the harness's own `services` argument, exactly
    # the race test_claims_chain_trace.py's fixture comment already
    # anticipated ("exactly the shape Task 7's MCP fix will add").
    trace = client.wait_for_trace_id(
        trace_id,
        operation=ENTRY_SPAN,
        timeout=90,
        services={"settlement-agent", "payments-mcp", "payments-api"},
    )
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
    is instrumented and uses the stable HTTP semconv spelling (A13).

    Task 7 update: since the MCP hop now joins this trace (params._meta
    injection, correlation.py), the trace also legitimately contains
    payments-mcp's *own* outbound CLIENT span to payments-api — filtering to
    settlement-agent's own spans is what keeps this test's assertion (every
    one of *this* service's outbound calls targets payments-mcp) precise
    rather than accidentally also asserting something false about a
    different service's own outbound hop.

    **M1.2 Task 4 update — narrowed from `kind == "client"` to the httpx
    instrumentation's own scope.** Now that the settlement decision is a real
    model call, settlement-agent also emits pydantic-ai's `chat <model>`
    spans, and those are CLIENT-kind too. They carry `server.address =
    "api.anthropic.com"` and no `url.full` at all, so the old `kind ==
    "client" and service == "settlement-agent"` filter swept them in and the
    `"payments-mcp" in url.full` assertion failed against a
    correctly-behaving deployment (measured, M1.2 task-4-report.md). The fix
    is to say what this test always meant: the spans *this service's HTTP
    client instrumentation* produces.
    `otel.scope.name == "opentelemetry.instrumentation.httpx"` is exactly
    that, and measurably so — it is not a tolerance, and a genuinely
    misdirected outbound HTTP call would still fail here.

    **M1.2 Task 8 update — the model hop now has its own httpx-scoped span
    too.** `HTTPX2ClientInstrumentor` (`bootstrap.py`) closed the gap noted
    above: `anthropic==1.4.0`'s `httpx2` traffic to `api.anthropic.com` now
    also produces a CLIENT span under `otel.scope.name ==
    "opentelemetry.instrumentation.httpx"`, nested under the `chat` span,
    with its own `url.full` (`https://api.anthropic.com/v1/messages...`).
    That is a real, separate outbound target for this service and belongs in
    this trace — it is asserted for and pinned by
    `tests/e2e/test_settlement_llm_trace.py::test_anthropic_client_span_exists_and_is_parented_to_chat`.
    It is excluded here (`server.address != "api.anthropic.com"`) because
    this test's job is specifically the MCP transport's own spans, not every
    outbound hop this service makes."""
    client_spans = [
        s
        for s in settlement_trace.trace.spans
        if s.service == "settlement-agent"
        and s.attrs.get("otel.scope.name") == "opentelemetry.instrumentation.httpx"
        and s.attrs.get("server.address") != "api.anthropic.com"
    ]
    assert client_spans, "no outbound CLIENT spans found for the MCP transport"
    for span in client_spans:
        target = span.attrs.get("url.full") or ""
        assert "payments-mcp" in target, f"unexpected outbound target: {span.attrs}"
        # Stable HTTP semconv spelling (A13): OTEL_SEMCONV_STABILITY_OPT_IN=http
        # is set on the deployment manifest specifically so these come out
        # this way rather than the legacy http.method/http.url/http.status_code.
        assert "http.request.method" in span.attrs
        assert "http.method" not in span.attrs


def test_all_four_services_are_in_one_trace(settlement_trace):
    """Task 7 flips this from xfail(strict=True): settlement-agent's MCP
    client (mcp_tools.py) now injects params._meta (traceparent + baggage)
    via the lower-level ClientSession.send_request() API, and FastMCP 4.0.3's
    server-side extraction (already proven in NOTES.md/A14a) joins the trace.
    Confirmed by capture: settlement-agent, payments-mcp and payments-api all
    land in the one trace this test originates."""
    services = settlement_trace.trace.services()
    assert {"settlement-agent", "payments-mcp", "payments-api"} <= services, (
        f"expected one trace spanning the chain; got services: {sorted(services)}"
    )


def test_mcp_tool_calls_are_present(settlement_trace):
    """Task 7 flips this from xfail(strict=True) — same fix as
    test_all_four_services_are_in_one_trace above."""
    operations = {s.operation for s in settlement_trace.trace.spans}
    assert "tools/call propose_payment" in operations
    assert "tools/call execute_payment" in operations


def test_settlement_agent_span_precedes_the_payment(settlement_trace):
    """Task 7 flips this from xfail(strict=True) — same fix as
    test_all_four_services_are_in_one_trace above."""
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
