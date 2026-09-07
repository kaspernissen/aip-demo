"""Asserts the first agent-to-agent handoff — claims-agent delegating to
settlement-agent over A2A — is correctly traced, and pins down precisely how
far the trace reaches today.

Chain under test: A2A client -> claims-agent -> settlement-agent -> payments-mcp
-> payments-api.

**A14/A17.** This test originates the request, so it mints its own trace/span
ID and sends it as a `traceparent` header, then looks the trace up by that
exact ID (`wait_for_trace_id`), never by search. Because the injected root
span never itself lands in Jaeger, claims-agent's entry span is a legitimate
orphan by the harness's definition; per A17 the exclusion below is pinned to
exactly that one span (its operation *and* its `parent_id`), not a count
tolerance.

**One gap remains, deliberately — same one Task 4 found and deferred.**
settlement-agent's MCP client does not inject `params._meta`, and FastMCP only
ever extracts trace context from `params._meta`, never HTTP headers (see
`agents/settlement-agent/NOTES.md`). So even though this chain now propagates
correctly from the test client through claims-agent and into settlement-agent
(the new hop this task adds — see `agents/claims-agent/NOTES.md` for the
empirical proof), it still does not reach payments-mcp or payments-api. Per
the coordinator's ruling (A6/A18), that stays Task 7's job via the shared
`correlation.py`. The tests asserting the full four-service chain remain
`xfail(strict=True)`, citing this one remaining gap precisely — the same gap,
not a new one.
"""
import json
import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tests.harness.jaeger import JaegerClient

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
CLAIMS_URL = os.environ.get("CLAIMS_AGENT_URL", "http://localhost:19001")

ENTRY_SPAN = "POST /"  # StarletteInstrumentor's ASGI server span (A18)

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_CARD_CONTRACT = REPO_ROOT / "contracts" / "agent-cards" / "claims-agent.json"


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
    response = httpx.post(f"{CLAIMS_URL}/", json=payload, headers=headers, timeout=120)
    response.raise_for_status()
    return response.json()


def file_claim(claim_id: str, amount: float, traceparent: str | None = None) -> dict:
    return send_message(
        json.dumps(
            {
                "claimId": claim_id,
                "policyNumber": "POL-77120",
                "description": "Rear-end collision at a junction",
                "estimatedAmount": amount,
            }
        ),
        traceparent=traceparent,
    )


def test_agent_card_is_served():
    response = httpx.get(f"{CLAIMS_URL}/.well-known/agent-card.json", timeout=15)
    response.raise_for_status()
    card = response.json()

    # contracts/agent-cards/<component>.json is the single source of truth,
    # staged into the build context by rebuild-and-deploy.sh (A11). Diff the
    # whole document, not just a couple of fields, so a served/contract drift
    # fails here instead of shipping silently.
    contract = json.loads(AGENT_CARD_CONTRACT.read_text())
    assert card == contract, (
        "served agent card does not match the committed contract "
        f"({AGENT_CARD_CONTRACT}) — rebuild and redeploy claims-agent"
    )


@pytest.fixture(scope="module")
def chain_trace():
    # A14: mint our own trace/span ID, send it as a traceparent header (an
    # A2A/HTTP boundary), and look it up by that exact ID — never by search.
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    traceparent = f"00-{trace_id}-{span_id}-01"

    claim_id = f"CLM-TEST-{uuid.uuid4().hex[:8]}"
    result = file_claim(claim_id, 6200, traceparent=traceparent)
    assert "error" not in result, f"claims-agent call failed: {result}"

    client = JaegerClient(JAEGER_URL)
    trace = client.wait_for_trace_id(trace_id, operation=ENTRY_SPAN, timeout=120)

    # Both claims-agent's and settlement-agent's own BatchSpanProcessors
    # default to a 5s scheduled export delay, and each is a *separate*
    # process exporting on its own timer, independently of the httpx CLIENT
    # span for the outbound call and of each other. By the time the HTTP
    # response comes back, settlement-agent has already finished its own
    # work in-process, but any of these spans may not have reached the
    # collector yet. `wait_for_trace_id`'s `operation` check is satisfied the
    # instant claims-agent's own "POST /" span lands, which races ahead of
    # the rest — observed directly across repeated captures: one showed only
    # {"claims-agent"}; another showed both services but with
    # settlement-agent's entry span an orphan because claims-agent's own
    # outbound httpx CLIENT span (its declared parent) hadn't landed yet.
    # Keep polling the same trace ID (never a fresh search — A14) until the
    # trace is fully assembled — settlement-agent present and no orphan
    # beyond the one synthetic root A17 expects — or we give up.
    def is_complete(t) -> bool:
        if "settlement-agent" not in t.services():
            return False
        return all(
            o.operation == ENTRY_SPAN and o.service == "claims-agent" and o.parent_id == span_id
            for o in t.orphans()
        )

    deadline = time.time() + 45
    while not is_complete(trace) and time.time() < deadline:
        time.sleep(2)
        trace = client.get_trace(trace_id)

    # Review finding S4: is_complete() proves claims-agent and
    # settlement-agent have both fully landed, but says nothing about a
    # *third* service's spans landing later still, on that service's own
    # independent BatchSpanProcessor timer — exactly the shape Task 7's MCP
    # fix will add (payments-mcp/payments-api joining this same trace once
    # params._meta injection lands). Snapshotting the instant is_complete()
    # first holds risks handing the two xfail(strict=True) tests below a
    # trace that is missing those not-yet-landed spans, which would keep them
    # xfailing forever instead of flipping to an unexpected pass the day that
    # gap actually closes — a silent false negative in the one signal
    # `strict=True` exists to surface. So: once is_complete() holds (or its
    # own deadline elapses), keep polling until the *total span count* has
    # gone unchanged for a continuous quiet window.
    #
    # Amendment A20a (round-2 re-review finding): that quiet window MUST be
    # strictly longer than the export interval it is waiting on, or it is not
    # a quiet window at all. Every service here (claims-agent,
    # settlement-agent, and payments-mcp/payments-api once Task 7 lands) uses
    # `BatchSpanProcessor(OTLPSpanExporter())` with no schedule_delay_millis
    # override (see each service's bootstrap.py), i.e. the OTel SDK's default
    # 5s export delay. Round 1's quiet window was a single 2s gap between two
    # polls — shorter than that 5s delay — so the common case of three
    # independent 5s timers would very often land a batch *after* a 2s gap
    # already looked quiet, and the loop would exit early anyway. Fixed: track
    # the wall-clock time of the last observed span-count change and only
    # accept the trace as stable once that change is more than
    # QUIET_WINDOW_S (10s) in the past — any span landing resets the clock.
    # 10s, not just-over-5s: a bare 1s margin over the nominal 5s export
    # delay is not enough once real network/collector latency is added on
    # top of the timer itself (a timing simulation built while implementing
    # this amendment demonstrated a 6s window missing a batch that landed at
    # 6.5s for exactly that reason). If the BatchSpanProcessor's schedule
    # delay is ever tuned away from the SDK default in any service's
    # bootstrap.py, QUIET_WINDOW_S must move with it.
    QUIET_WINDOW_S = 10  # comfortably > 5s BatchSpanProcessor default export delay (A20a)
    POLL_INTERVAL_S = 1
    stability_deadline = time.time() + 90
    previous_count = len(trace.spans)
    last_change = time.time()
    while (
        time.time() - last_change < QUIET_WINDOW_S and time.time() < stability_deadline
    ):
        time.sleep(POLL_INTERVAL_S)
        trace = client.get_trace(trace_id)
        count = len(trace.spans)
        if count != previous_count:
            previous_count = count
            last_change = time.time()

    return SimpleNamespace(
        trace=trace, trace_id=trace_id, span_id=span_id, claim_id=claim_id, result=result
    )


def test_message_send_returns_the_settlement_json(chain_trace):
    """Plan defect (A19, same class of bug Task 4 found in settlement-agent's
    executor, NOTES.md §8 there): a2a-sdk 0.3.7's blocking message/send
    handler returns as soon as it sees the FIRST Message-kind event enqueued,
    silently dropping anything enqueued after. The brief's Step 6 sketch for
    claims-agent's executor.py enqueues a "Claim ... received. Investigating."
    narration *before* the final JSON result, which would mean this test's
    caller only ever sees that narration text and never the settlement JSON
    this agent's documented interface promises. Fixed in executor.py: the
    narration is logged, not enqueued, and exactly one Message — the final
    JSON result — is enqueued. This test asserts the payload's substance, not
    merely that no error occurred, so restoring the brief's two-enqueue
    executor makes it fail (verified — see task-5-report.md)."""
    payload = json.loads(chain_trace.result["result"]["parts"][0]["text"])
    assert payload["claimId"] == chain_trace.claim_id
    assert payload["policyNumber"] == "POL-77120"
    assert payload["paymentId"].startswith("PAY-")
    assert payload["amount"] == 6200
    assert payload["currency"] == "EUR"
    assert payload["status"] == "executed"
    assert payload["narrative"], "narrative must be non-empty"


def test_claims_agent_entry_span_is_parented_to_the_injected_context(chain_trace):
    """A14: proves context *propagated* from the test client into claims-agent
    (StarletteInstrumentor, A18) — not merely that a matching span exists
    somewhere in the store.

    Both claims-agent and settlement-agent use the same StarletteInstrumentor
    setup (A18) and so both name their entry span "POST /" — `Trace.span()`
    matches by operation name alone and raises on more than one match, so
    this looks the claims-agent one up directly by filtering on `.service`
    rather than calling `trace.span(ENTRY_SPAN)`, which would be ambiguous
    the moment a second A2A hop joins the trace (exactly what this task adds)."""
    trace = chain_trace.trace
    entries = [
        s for s in trace.spans if s.operation == ENTRY_SPAN and s.service == "claims-agent"
    ]
    assert len(entries) == 1, (
        f"expected exactly one claims-agent {ENTRY_SPAN!r} span, found {len(entries)}"
    )
    entry = entries[0]
    assert entry.parent_id == chain_trace.span_id, (
        f"{ENTRY_SPAN!r} span's parent is {entry.parent_id!r}, expected the "
        f"injected span id {chain_trace.span_id!r} — inbound trace context "
        "did not propagate"
    )
    # Jaeger's TraceID.String() drops a whole-number leading zero nibble when
    # the high 64 bits start with a zero hex digit, so the returned ID can be
    # shorter than 32 hex chars. Zero-pad rather than strip leading zeros from
    # both sides, which could mask a genuine mismatch.
    assert trace.trace_id.lower().zfill(32) == chain_trace.trace_id.lower()


def test_claims_agent_delegates_to_settlement_agent_in_the_same_trace(chain_trace):
    """The new hop this task adds, and the one nobody had measured before this
    task (see module docstring / NOTES.md question 1): does claims-agent's
    outbound A2A call to settlement-agent carry the ambient trace context?
    HTTPXClientInstrumentor (bootstrap.py) instruments the httpx.AsyncClient
    delegation.A2ADelegate builds, injecting a traceparent header from
    whatever the ambient OTel context is at call time — which, thanks to
    A18's StarletteInstrumentor fix (this task and Task 4), is the real
    externally-injected context rather than a disconnected root. Verified by
    capture: settlement-agent's own entry span shows up as a genuine
    descendant of claims-agent's span in this same trace."""
    trace = chain_trace.trace
    assert "settlement-agent" in trace.services(), (
        f"settlement-agent never joined this trace; services present: "
        f"{sorted(trace.services())} — outbound A2A context propagation is broken"
    )

    by_id = {s.span_id: s for s in trace.spans}
    claims_ids = {s.span_id for s in trace.spans if s.service == "claims-agent"}
    settlement_spans = [s for s in trace.spans if s.service == "settlement-agent"]
    assert settlement_spans, "no settlement-agent spans in this trace"

    def ancestors(span):
        seen = set()
        current = span
        while current and current.parent_id and current.parent_id not in seen:
            seen.add(current.parent_id)
            yield current.parent_id
            current = by_id.get(current.parent_id)

    assert any(
        claims_ids & set(ancestors(s)) for s in settlement_spans
    ), "settlement-agent span does not descend from any claims-agent span"


def test_claims_agent_trace_has_no_unexpected_orphans(chain_trace):
    """A17: the injected span ID is never itself a real span in this trace, so
    claims-agent's entry span (parented to it) is an unavoidable, legitimate
    orphan. Pinned to exactly that one span — operation *and* parent_id — so a
    genuine propagation defect anywhere else (settlement-agent's own entry
    span losing its parent, for instance) still fails this test.

    `assert_single_root` (the harness's usual check) cannot be used here — it
    would never hold for a synthetic root by construction — but nothing
    replaced it (review finding N4). `trace.roots() == []` is the positive
    complement: every span actually stored in this trace has *some* parent,
    and the only span without one (the injected root) never lands in Jaeger
    at all. Together with the orphan-exclusion assertion below, this pins
    "every span has a parent, and the only external one is the injected
    root" precisely, rather than leaving that half of A17's claim unchecked."""
    trace = chain_trace.trace
    assert trace.roots() == [], (
        f"expected no real root spans (the trace's root is the synthetic, "
        f"never-collected injected span); found: {trace.roots()}"
    )
    orphans = trace.orphans()
    unexpected = [
        s
        for s in orphans
        if not (s.operation == ENTRY_SPAN and s.service == "claims-agent" and s.parent_id == chain_trace.span_id)
    ]
    assert not unexpected, (
        "orphan spans present beyond the expected synthetic root, context "
        f"propagation is broken: {[(s.service, s.operation, s.parent_id) for s in unexpected]}"
    )


def test_settlement_failure_reaches_the_caller_with_its_real_reason():
    """Quality bar from this task's brief: a settlement failure must reach
    claims-agent's own caller with the underlying reason intact — not
    flattened into a success, not swallowed. A negative estimatedAmount makes
    settlement-agent's decide_settlement raise
    ValueError("amount must not be negative"); a2a-sdk's request handler turns
    that unhandled exception into a JSON-RPC error carrying str(exc)
    (verified in Task 4's settlement-agent). delegation.A2ADelegate.send()
    re-raises that as RuntimeError("settlement-agent returned an error: ...");
    executor.py does not catch it, so claims-agent's own a2a-sdk request
    handler does the same thing one hop further out. This proves the whole
    round trip, not just delegation.py's unit test."""
    claim_id = f"CLM-TEST-{uuid.uuid4().hex[:8]}"
    result = file_claim(claim_id, -100)
    assert "error" in result, f"expected a JSON-RPC error, got: {result}"
    message = result["error"].get("message", "")
    assert "amount must not be negative" in message, (
        f"real failure reason was not preserved end to end: {message!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Confirmed, remaining gap (see module docstring / "
        "agents/settlement-agent/NOTES.md): settlement-agent's MCP client "
        "does not inject params._meta, so FastMCP's tool-call spans never "
        "join this trace. This stays Task 7's job (shared correlation.py, "
        "A6) per the coordinator's ruling. Flip once client-side _meta "
        "injection lands, and remove this marker."
    ),
)
def test_all_four_backend_services_are_in_one_trace(chain_trace):
    expected = {"claims-agent", "settlement-agent", "payments-mcp", "payments-api"}
    services = chain_trace.trace.services()
    assert expected <= services, (
        f"expected one trace across the whole chain; missing: {sorted(expected - services)}"
    )


@pytest.mark.xfail(
    strict=True,
    reason="Same remaining gap as test_all_four_backend_services_are_in_one_trace.",
)
def test_payment_was_executed_with_no_governance(chain_trace):
    """M1's deliberate horror: nothing stopped the payment. Would be a
    real assertion once the MCP hop joins this trace (Task 7); until then,
    payments-api's span lives in a disconnected trace of its own."""
    operations = {s.operation for s in chain_trace.trace.spans}
    assert "tools/call execute_payment" in operations
    api_span = next(
        s for s in chain_trace.trace.spans
        if s.service == "payments-api" and "execute" in s.operation
    )
    assert api_span.attrs.get("http.response.status_code") == 200
