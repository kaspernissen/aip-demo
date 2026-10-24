"""Nothing the collector receives is dropped on the way out.

The deployed collector logs `Exporting failed` when a backend rejects data,
and that log is the only evidence: Jaeger simply shows fewer spans. This file
drives one trace through the gateway (a permitted `propose_payment` and an
`execute_payment` above the assisted allowance, so one decision span of each
outcome), checks the decision spans reached Jaeger readable, and reads the
collector's log for the whole run window -- never a `--tail`, which has
reported "no errors" while rejections sat further back.

It speaks MCP to the gateway directly with a SPIRE-minted JWT-SVID, so it
needs no model call and no browser, and it supplies its own `traceparent` so
the trace id is known before the call is made.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid

import httpx
import pytest

from tests.e2e.test_caller_identity import (
    CALLER_SPIFFE_ID,
    GATEWAY_AUDIENCE,
    GATEWAY_NAMESPACE,
    KUBE_CONTEXT,
    MCP_PROTOCOL_VERSION,
    SPIRE_SERVER_POD,
    _json_body,
)
from tests.harness import live
from tests.harness.jaeger import JaegerClient

OBSERVABILITY_NAMESPACE = "observability"
COLLECTOR_DEPLOYMENT = "deploy/otel-collector"

DECISION_SPAN = "northstar.policy.decision"
PDS_SERVICE = "policy-decision-service"

# The decision span's retryable flag.
ATTR_RETRYABLE_CANONICAL = "northstar.policy.retryable"
ATTR_DECISION = "northstar.policy.decision"

# €6,200 against settlement-agent's assisted execute allowance (€0 since M8; it was €6,000) -- the
# same numbers `test_policy_denial.py`'s denial scenario uses, so this file
# and the demo exercise the same rule.
DENIED_AMOUNT = 6200

# Services whose spans must all have landed before the trace is compared.
# `propose_payment` really executes, so payments-api is in the trace too.
EXPECTED_SERVICES = {"agentgateway", "payments-mcp", "payments-api", PDS_SERVICE}

def _kubectl(*args: str, stdin: str | None = None, timeout: int = 90):
    return live.kubectl(*args, stdin=stdin, timeout=timeout)


@pytest.fixture(scope="module")
def gateway_url():
    """An ephemeral port-forward to `svc/agentgateway`, owned by this file.

    Same shape as `tests/e2e/test_caller_identity.py::gateway_url`: `:3000`
    with no local port makes kubectl choose a free one and print it, so this
    cannot shadow a fixed port even by accident, and the
    port is parsed out of kubectl's own line rather than guessed.
    """
    proc = subprocess.Popen(
        [
            "kubectl", "--context", KUBE_CONTEXT, "-n", GATEWAY_NAMESPACE,
            "port-forward", "svc/agentgateway", ":3000",
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
            f"{GATEWAY_NAMESPACE} -- is the kind-northstar cluster up?"
        )
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def svid():
    """A real JWT-SVID for settlement-agent, minted by SPIRE at runtime.

    Never logged: it is a live bearer credential.
    """
    result = _kubectl(
        "-n", GATEWAY_NAMESPACE, "exec", SPIRE_SERVER_POD, "-c", "spire-server",
        "--", "/opt/spire/bin/spire-server", "jwt", "mint",
        "-spiffeID", CALLER_SPIFFE_ID, "-audience", GATEWAY_AUDIENCE,
        "-output", "json",
    )
    assert result.returncode == 0, (
        "`spire-server jwt mint` failed, so this file cannot drive the gateway:"
        f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)["svid"]["token"]


class _Run:
    def __init__(self, trace_id, trace, started_at):
        self.trace_id = trace_id
        self.trace = trace
        self.started_at = started_at


@pytest.fixture(scope="module")
def driven_run(gateway_url, svid):
    """One driven trace: a permitted proposal and a denied execution.

    Returns the completed Jaeger trace plus the wall-clock instant the run
    began, which the log test below uses to size its `--since` window.
    """
    started_at = time.time()
    trace_id = uuid.uuid4().hex
    traceparent = f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01"
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "authorization": f"Bearer {svid}",
        "traceparent": traceparent,
    }

    def post(payload: dict, extra: dict) -> httpx.Response:
        return httpx.post(
            f"{gateway_url}/mcp", json=payload, headers={**headers, **extra},
            timeout=30,
        )

    init = post(
        {
            "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "collector-export-probe", "version": "0"},
            },
        },
        {},
    )
    session_id = init.headers.get("mcp-session-id")
    assert init.status_code == 200 and session_id, (
        "the gateway did not open an MCP session, so this file cannot drive a "
        f"decision: HTTP {init.status_code} body={init.text[:300]!r}"
    )
    session = {"mcp-session-id": session_id}

    claim_id = f"CLM-EXPORT-{uuid.uuid4().hex[:8]}"
    proposed = post(
        {
            "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "tools/call",
            "params": {
                "name": "propose_payment",
                "arguments": {
                    "claim_id": claim_id, "amount": DENIED_AMOUNT, "currency": "EUR",
                },
            },
        },
        session,
    )
    body = _json_body(proposed) or {}
    payment_id = ((body.get("result") or {}).get("structuredContent") or {}).get(
        "paymentId"
    )
    assert payment_id, (
        f"the proposal did not return a paymentId, so the execute call below "
        f"cannot reach the policy decision: {proposed.text[:300]!r}"
    )

    executed = post(
        {
            "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "tools/call",
            "params": {
                "name": "execute_payment",
                "arguments": {
                    "payment_id": payment_id, "amount": DENIED_AMOUNT,
                    "currency": "EUR",
                },
            },
        },
        session,
    )
    executed_body = _json_body(executed) or {}
    # agentgateway v1.6.0 answers a policy deny as HTTP 200 with an `isError`
    # tool result whose text ends in the PDS's `[policy-denial ...]` tag.
    result = executed_body.get("result") or {}
    result_text = " ".join(
        c.get("text", "") for c in result.get("content") or [])
    assert result.get("isError") is True and live.parse_deny(result_text), (
        f"€{DENIED_AMOUNT} above the assisted allowance was not refused, so this "
        "run did not produce the denial half of the decision pair: "
        f"{executed.text[:300]!r}"
    )

    # `services=` makes this wait for the trace to look *complete* and then
    # hold steady for the harness's quiet window, so the span set compared
    # below is the final one rather than a snapshot mid-export.
    trace = JaegerClient().wait_for_trace_id(
        trace_id, services=EXPECTED_SERVICES, timeout=120.0
    )
    return _Run(trace_id, trace, started_at)


def test_both_decision_spans_reach_jaeger_readable(driven_run):
    decisions = driven_run.trace.spans_named(DECISION_SPAN)
    assert {d.attrs.get(ATTR_DECISION) for d in decisions} == {"permit", "deny"}, (
        f"expected one permit and one deny {DECISION_SPAN} span in Jaeger: "
        f"{[d.attrs for d in decisions]!r}"
    )
    for decision in decisions:
        assert ATTR_RETRYABLE_CANONICAL in decision.attrs, (
            f"Jaeger's {DECISION_SPAN} span has lost {ATTR_RETRYABLE_CANONICAL!r}: "
            f"{sorted(decision.attrs)}"
        )


def test_the_collector_rejected_nothing_during_the_run(driven_run):
    """Asserts on `Exporting failed`, the exporterhelper message that carries
    `rejected_items`, from any exporter, over every log line since the run
    began. The failure names the rejecting component ids."""
    age_seconds = max(int(time.time() - driven_run.started_at) + 30, 60)
    result = _kubectl(
        "-n", OBSERVABILITY_NAMESPACE, "logs", COLLECTOR_DEPLOYMENT,
        f"--since={age_seconds}s", timeout=120,
    )
    assert result.returncode == 0, (
        f"could not read the collector log:\nstderr:\n{result.stderr}"
    )
    lines = result.stdout.splitlines()
    rejections = [line for line in lines if "Exporting failed" in line]
    components = sorted(
        set(re.findall(r'"otelcol\.component\.id":\s*"([^"]+)"',
                       "\n".join(rejections)))
    )
    assert not rejections, (
        f"the collector rejected data during this run -- {len(rejections)} "
        f"`Exporting failed` events in the {age_seconds}s window "
        f"({len(lines)} lines read in full, not tailed), from {components or 'unparsed components'}.\n"
        + "\n".join(rejections[:5])
    )
