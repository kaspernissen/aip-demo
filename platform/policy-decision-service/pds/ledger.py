"""The one ledger read the policy needs: is this payment awaiting a human?

Read only for callers in rules.HUMAN_APPROVERS, only for execute_payment.
Every failure (unreachable, slow, not JSON, no status, an id that is not a
payment id) returns None, and rules._human_approval denies on None. That is
fail-closed: a ledger the PDS cannot read never widens what is permitted.

stdlib urllib, not a new HTTP dependency. `traceparent` is injected by hand so
payments-api's `GET /payments/{paymentId}` span joins the decision's trace.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from opentelemetry import propagate

LEDGER_URL = os.environ.get(
    "PAYMENTS_API_URL", "http://payments-api.northstar-systems.svc.cluster.local:8080"
).rstrip("/")
TIMEOUT_S = 2.0
# payments-api's own id shape (store.go: fmt.Sprintf("PAY-%06d", ...)). Anything
# else is never put into a URL.
_PAYMENT_ID = re.compile(r"PAY-[0-9]{6}")


def payment_status(payment_id: str | None, *, opener=urllib.request.urlopen) -> str | None:
    if not isinstance(payment_id, str) or not _PAYMENT_ID.fullmatch(payment_id):
        return None
    request = urllib.request.Request(f"{LEDGER_URL}/payments/{payment_id}")
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    for key, value in carrier.items():
        request.add_header(key, value)
    try:
        with opener(request, timeout=TIMEOUT_S) as response:
            record = json.load(response)
    except Exception:  # noqa: BLE001 -- every failure is "no status": deny
        return None
    status = record.get("status") if isinstance(record, dict) else None
    return status if isinstance(status, str) else None
