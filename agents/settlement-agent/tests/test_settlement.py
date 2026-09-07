"""Tests the hardcoded settlement decision.

M1.1 has no LLM: decide_settlement is deliberately dumb and deterministic, so
the demo's numbers are identical on every run. M1.2 replaces the body with a
PydanticAI agent while keeping this signature.
"""
import pytest

from settlement import decide_settlement


def test_settles_at_the_assessed_amount():
    result = decide_settlement({"claimId": "CLM-1842", "amount": 6200, "currency": "EUR"})

    assert result["amount"] == 6200
    assert result["currency"] == "EUR"


def test_narrative_mentions_the_claim_and_amount():
    result = decide_settlement({"claimId": "CLM-1842", "amount": 6200, "currency": "EUR"})

    assert "CLM-1842" in result["narrative"]
    assert "6200" in result["narrative"].replace(",", "")


def test_defaults_currency_to_eur():
    result = decide_settlement({"claimId": "CLM-1901", "amount": 800})

    assert result["currency"] == "EUR"


def test_rejects_missing_claim_id():
    with pytest.raises(ValueError, match="claimId"):
        decide_settlement({"amount": 6200})


def test_rejects_missing_amount():
    with pytest.raises(ValueError, match="amount"):
        decide_settlement({"claimId": "CLM-1842"})


def test_rejects_negative_amount():
    with pytest.raises(ValueError, match="amount"):
        decide_settlement({"claimId": "CLM-1842", "amount": -5})


def test_is_deterministic():
    claim = {"claimId": "CLM-1842", "amount": 6200, "currency": "EUR"}

    assert decide_settlement(claim) == decide_settlement(claim)
