"""Asserts the single-trace property survives putting agentgateway in the path.

Chain under test, as of M2.1 Task 3: A2A client -> settlement-agent (model
decides) -> **agentgateway** -> payments-mcp -> payments-api.

M1.2 proved one trace spans that chain with settlement-agent talking straight
to payments-mcp. This file exists to answer one question about the interposed
proxy: is agentgateway a real node *inside* that one trace, or a fracture in
it? The M2 spike measured the answer with a hand-built client before anything
was deployed (`.superpowers/spikes/2026-09-09-agentgateway-spike.md`, a
git-ignored workspace report cited as provenance, not as a file you can open);
this file is the in-tree, runnable confirmation of it against the real agent.

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
  auto-injected `traceparent` HTTP header — measured against an echo server in
  the spike, e.g. `00-df60a027741941a16675b35edbcf0a32-5675a787e51b87e6-03`.
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

**Ruling 3 (this task's own): the fixture below is defined here, not
imported.** `test_settlement_llm_trace.py`'s `llm_settlement` fixture is
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

ENTRY_SPAN = "POST /"  # StarletteInstrumentor's ASGI server span (A18)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason=(
        "needs the live kind cluster and spends real model tokens; gated "
        "behind the same RUN_BROWSER_TESTS=1 flag scripts/smoke-test.sh sets "
        "unconditionally"
    ),
)

REQUESTED_AMOUNT = 6200


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

    A14: this fixture originates the request, so it mints its own trace and
    span id, sends them as a `traceparent` header, and looks the trace up by
    that exact id — never `find_trace`/`wait_for_trace`, which silently
    returned a stale trace twice during M1.1.

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

    Measured in the M2 spike before this was built: caller -> agentgateway
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
