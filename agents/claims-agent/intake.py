"""Claim intake parsing and validation.

Deliberately strict and deterministic: this is the validated boundary for
claim data, regardless of whatever handles free-text claims upstream.

Two ways in since M13, one boundary. A JSON object goes through
`parse_claim` exactly as before. Free text goes to the model
(extraction.py), and the draft it returns comes back HERE, through the same
`validate_claim`, plus the few checks a first notice of loss needs that a
JSON claim never had to pass (`check_draft`). The model's output is never
trusted more than a customer's JSON: a draft that would not validate as JSON
is not a claim, and what is missing is decided by this file, not by the
model's own list.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import secrets
from typing import Any

# The draft's fields, in the order a missing one is asked about. The names are
# the JSON claim's own, so a draft and a JSON claim are the same shape.
DRAFT_FIELDS = ("policyNumber", "estimatedAmount", "incidentDate", "description")

# Northstar settles in euro, and the delegation to settlement-agent has always
# said "EUR" (executor.py). A free-text amount in another currency is not
# converted by anyone: it is treated as missing and the customer is asked for
# the amount in euro.
SETTLEMENT_CURRENCY = "EUR"

# What the customer is asked when the code finds a field missing that the
# model did not ask about (or the model's question is empty). One per field,
# so the agent never has to send the model's words when it cannot.
FALLBACK_QUESTIONS = {
    "policyNumber": "What is your policy number? It starts with POL-, for example POL-77120.",
    "estimatedAmount": "Roughly how much will the repair cost, in euro?",
    "incidentDate": "On what date did this happen?",
    "description": "Can you tell us briefly what happened to the vehicle?",
}

# Bounds on what a draft may carry. A model can be talked into anything; these
# keep a pasted essay out of task metadata and out of the settlement payload.
MAX_POLICY_NUMBER = 64
MAX_DESCRIPTION = 2000


def is_json_object(text: str) -> bool:
    """Whether `text` takes the deterministic JSON path.

    Only a JSON *object* does. Anything else -- prose, a malformed object, a
    bare number or list -- is free text for the model. Today's JSON path is
    unchanged for every input it accepted, and the inputs it used to reject
    as "not a JSON object" now get a model to read them instead.
    """
    try:
        return isinstance(json.loads(text), dict)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False


def parse_claim(text: str) -> dict:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"claim must be a JSON object: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("claim must be a JSON object")

    return validate_claim(raw)


def validate_claim(raw: dict) -> dict:
    """The one validator both intake paths share. Raises ValueError."""
    for field in ("claimId", "policyNumber", "estimatedAmount"):
        if raw.get(field) in (None, ""):
            raise ValueError(f"{field} is required")

    return {
        "claimId": str(raw["claimId"]),
        "policyNumber": str(raw["policyNumber"]),
        "description": str(raw.get("description") or ""),
        "estimatedAmount": float(raw["estimatedAmount"]),
    }


def new_intake_claim_id() -> str:
    """A claim id for a claim that arrived as free text.

    `CLM-INTAKE-` plus six upper-case hex digits, the shape of the demo
    driver's `CLM-DEMO-XXXXXX`, so a claim the model took in is recognisable
    on sight in a trace, a ledger row or the reviewer queue. The customer never
    chooses it: free text carries no id, and an id lifted from the text would
    let a customer name someone else's claim.
    """
    return f"CLM-INTAKE-{secrets.token_hex(3).upper()}"


def _amount(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount) or amount <= 0:
        return None
    return amount


def _incident_date(value: Any, today: dt.date) -> str | None:
    try:
        when = dt.date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None
    return when.isoformat() if when <= today else None


def check_draft(claim_id: str, draft: dict, today: dt.date | None = None) -> tuple[dict | None, list[str]]:
    """Decide, deterministically, whether a model's draft is a claim.

    Returns `(claim, [])` when it is, and `(None, missing)` when it is not,
    with `missing` in DRAFT_FIELDS order. Never raises on a bad draft: a value
    that does not validate is a missing value, and the customer is asked for
    it. The claim is built by `validate_claim`, the JSON path's own validator,
    so a free-text claim is never looser than a JSON one; the checks above it
    are what a first notice of loss needs on top (a positive euro amount, a
    date that is not in the future, a description, bounded lengths).
    """
    today = today or dt.date.today()
    missing: list[str] = []

    policy = str(draft.get("policyNumber") or "").strip()
    if not policy or len(policy) > MAX_POLICY_NUMBER:
        missing.append("policyNumber")

    amount = _amount(draft.get("estimatedAmount"))
    currency = str(draft.get("currency") or SETTLEMENT_CURRENCY).strip().upper()
    if amount is None or currency != SETTLEMENT_CURRENCY:
        missing.append("estimatedAmount")

    incident_date = _incident_date(draft.get("incidentDate"), today)
    if incident_date is None:
        missing.append("incidentDate")

    description = str(draft.get("description") or "").strip()
    if not description:
        missing.append("description")

    if missing:
        return None, missing

    claim = validate_claim({
        "claimId": claim_id,
        "policyNumber": policy,
        "description": description[:MAX_DESCRIPTION],
        "estimatedAmount": amount,
    })
    claim["incidentDate"] = incident_date
    return claim, []
