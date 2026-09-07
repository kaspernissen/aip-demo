"""Asserts payments-api emits the HTTP server spans the chain depends on."""
import os
import uuid

import pytest
import requests

from tests.harness.jaeger import JaegerClient, assert_span_kind

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
PAYMENTS_API_URL = os.environ.get("PAYMENTS_API_URL", "http://localhost:18080")


@pytest.fixture(scope="module")
def executed_payment_trace():
    claim_id = f"CLM-TEST-{uuid.uuid4().hex[:8]}"

    proposed = requests.post(
        f"{PAYMENTS_API_URL}/payments/propose",
        json={"claimId": claim_id, "amount": 6200, "currency": "EUR"},
        timeout=10,
    )
    proposed.raise_for_status()
    payment_id = proposed.json()["paymentId"]

    executed = requests.post(
        f"{PAYMENTS_API_URL}/payments/{payment_id}/execute", timeout=10
    )
    executed.raise_for_status()
    assert executed.json()["status"] == "executed"

    client = JaegerClient(JAEGER_URL)
    # Tag on url.path (the stable-semconv attribute carrying the raw path) so
    # this fixture's own trace is the one returned, not some other execute
    # trace still sitting in Jaeger's shared in-memory store (e.g. from a
    # prior local test run against this long-lived demo cluster).
    return client.wait_for_trace(
        service="payments-api",
        operation="POST /payments/{paymentId}/execute",
        tags={"url.path": f"/payments/{payment_id}/execute"},
        timeout=90,
    )


def test_execute_emits_a_server_span(executed_payment_trace):
    assert executed_payment_trace.has_span("POST /payments/{paymentId}/execute")
    assert_span_kind(
        executed_payment_trace, "POST /payments/{paymentId}/execute", "server"
    )


def test_span_carries_http_semantic_conventions(executed_payment_trace):
    # otelhttp v0.66.0 (bumped from v0.57.0 per amendment A12) emits the
    # stable HTTP semantic-convention spelling by default, no opt-in env var
    # needed. Confirmed empirically against a deployed payments-api trace;
    # see the telemetry contract in Task 8 and task-2-report.md for the full
    # attribute map and otelhttp version.
    span = executed_payment_trace.span("POST /payments/{paymentId}/execute")
    assert span.attrs.get("http.request.method") == "POST"
    assert span.attrs.get("http.response.status_code") == 200


def test_service_name_is_payments_api(executed_payment_trace):
    assert "payments-api" in executed_payment_trace.services()
