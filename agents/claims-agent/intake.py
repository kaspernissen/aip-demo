"""Claim intake parsing.

Deliberately strict and deterministic: this is the validated boundary for
claim data, regardless of whatever handles free-text claims upstream.
"""
from __future__ import annotations

import json


def parse_claim(text: str) -> dict:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"claim must be a JSON object: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("claim must be a JSON object")

    for field in ("claimId", "policyNumber", "estimatedAmount"):
        if raw.get(field) in (None, ""):
            raise ValueError(f"{field} is required")

    return {
        "claimId": str(raw["claimId"]),
        "policyNumber": str(raw["policyNumber"]),
        "description": str(raw.get("description") or ""),
        "estimatedAmount": float(raw["estimatedAmount"]),
    }
