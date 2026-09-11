"""Asserts payments-api emits the HTTP server spans the chain depends on.

The fixture originates its own request, so it looks its trace up by an ID it
already minted (via a synthetic `traceparent` header) rather than searching
for it by service/operation/tags. Payment IDs are deterministic and
sequential *starting from 1 on every pod restart* (by design — they appear on
conference slides), so any attribute that embeds a payment ID (e.g.
`url.path`) necessarily recurs across pod lifetimes in Jaeger's long-lived
in-memory store. A search-based lookup (`find_trace`/`wait_for_trace`) has no
reliable way to distinguish "the trace this test just produced" from "an
older trace from a previous pod lifetime that happens to share the same
path" once `nextID` has cycled — which routine `rebuild-and-deploy.sh`
restarts guarantee it will. Looking the trace up by the exact ID this test
generated and sent removes that dependency entirely.
"""
import os
import uuid

import pytest
import requests

from tests.harness.jaeger import JaegerClient, assert_span_kind

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
PAYMENTS_API_URL = os.environ.get("PAYMENTS_API_URL", "http://localhost:18080")


@pytest.fixture(scope="module")
def executed_payment():
    claim_id = f"CLM-TEST-{uuid.uuid4().hex[:8]}"

    proposed = requests.post(
        f"{PAYMENTS_API_URL}/payments/propose",
        json={"claimId": claim_id, "amount": 6200, "currency": "EUR"},
        timeout=10,
    )
    proposed.raise_for_status()
    payment_id = proposed.json()["paymentId"]

    # Mint our own W3C trace context and send it on the execute call, so we
    # can look this exact call's trace up by ID afterwards instead of
    # searching for it.
    trace_id = uuid.uuid4().hex  # 32 hex chars, non-zero (uuid4 version nibble)
    span_id = uuid.uuid4().hex[:16]  # 16 hex chars, non-zero for the same reason
    traceparent = f"00-{trace_id}-{span_id}-01"

    executed = requests.post(
        f"{PAYMENTS_API_URL}/payments/{payment_id}/execute",
        # The execute call states what it is paying. payments-api confirms that
        # against the proposal it recorded and refuses a bodyless execution.
        json={"amount": 6200, "currency": "EUR"},
        headers={"traceparent": traceparent},
        timeout=10,
    )
    executed.raise_for_status()
    assert executed.json()["status"] == "executed"

    client = JaegerClient(JAEGER_URL)
    trace = client.wait_for_trace_id(
        trace_id,
        operation="POST /payments/{paymentId}/execute",
        timeout=90,
    )
    return trace, trace_id, span_id


@pytest.fixture(scope="module")
def executed_payment_trace(executed_payment):
    trace, _trace_id, _span_id = executed_payment
    return trace


def test_execute_emits_a_server_span(executed_payment_trace):
    assert executed_payment_trace.has_span("POST /payments/{paymentId}/execute")
    assert_span_kind(
        executed_payment_trace, "POST /payments/{paymentId}/execute", "server"
    )


def test_execute_span_is_a_child_of_the_injected_context(executed_payment):
    # This is the strengthened version of "a span matching my tag exists
    # somewhere": it proves this specific request produced this specific
    # span, parented to the exact span ID this test injected, inside the
    # exact trace ID this test injected. That proves propagation, not mere
    # co-existence.
    trace, trace_id, span_id = executed_payment
    span = trace.span("POST /payments/{paymentId}/execute")
    assert span.parent_id == span_id, (
        f"execute span's parent is {span.parent_id!r}, expected the injected "
        f"span id {span_id!r} — the server did not extract our traceparent"
    )
    # Jaeger's TraceID.String() drops a whole-number leading zero nibble
    # (see model.TraceID.String() in jaeger-idl) when the high 64 bits start
    # with a zero hex digit, so compare normalized rather than requiring an
    # exact 32-char match.
    assert trace.trace_id.lower().lstrip("0") == trace_id.lower().lstrip("0")


def test_span_carries_http_semantic_conventions(executed_payment_trace):
    # otelhttp v0.71.0 (2026-09-11, M3; v0.66.0 before that, itself bumped
    # from v0.57.0 per amendment A12) emits the stable HTTP
    # semantic-convention spelling by default, no opt-in env var needed.
    # Confirmed empirically against a deployed payments-api trace; see the
    # telemetry contract in Task 8 and task-2-report.md for the full
    # attribute map and otelhttp version. Re-measured after the v0.71.0 bump
    # on trace eacff63cc15a0868cee60332b1728735: the attribute set is
    # unchanged -- http.request.method / http.response.status_code /
    # http.route / url.path / url.scheme / server.* / network.peer.* /
    # network.protocol.version, and still no network.protocol.name.
    span = executed_payment_trace.span("POST /payments/{paymentId}/execute")
    assert span.attrs.get("http.request.method") == "POST"
    assert span.attrs.get("http.response.status_code") == 200


def test_service_name_is_payments_api(executed_payment_trace):
    assert "payments-api" in executed_payment_trace.services()
