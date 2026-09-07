"""Settlement decision.

Hardcoded and deterministic in M1.1. In M1.2 the body becomes a PydanticAI
agent; this signature and return shape stay the same so nothing downstream
changes.
"""
from __future__ import annotations


def decide_settlement(claim: dict) -> dict:
    claim_id = claim.get("claimId")
    if not claim_id:
        raise ValueError("claimId is required")

    amount = claim.get("amount")
    if amount is None:
        raise ValueError("amount is required")
    if amount < 0:
        raise ValueError("amount must not be negative")

    currency = claim.get("currency") or "EUR"

    return {
        "amount": float(amount),
        "currency": currency,
        "narrative": (
            f"Claim {claim_id} is covered. Settling at "
            f"{amount:.0f} {currency} against the assessed damage."
        ),
    }
