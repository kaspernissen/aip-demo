import json

import pytest

from intake import parse_claim


def test_parses_a_valid_claim():
    claim = parse_claim(
        json.dumps(
            {
                "claimId": "CLM-1842",
                "policyNumber": "POL-77120",
                "description": "Rear-end collision at a junction",
                "estimatedAmount": 6200,
            }
        )
    )

    assert claim["claimId"] == "CLM-1842"
    assert claim["policyNumber"] == "POL-77120"
    assert claim["estimatedAmount"] == 6200.0


def test_rejects_non_json_input():
    with pytest.raises(ValueError, match="JSON"):
        parse_claim("please pay my claim")


def test_rejects_missing_claim_id():
    with pytest.raises(ValueError, match="claimId"):
        parse_claim(json.dumps({"policyNumber": "POL-77120", "estimatedAmount": 100}))


def test_rejects_missing_policy_number():
    with pytest.raises(ValueError, match="policyNumber"):
        parse_claim(json.dumps({"claimId": "CLM-1842", "estimatedAmount": 100}))


def test_rejects_missing_estimated_amount():
    with pytest.raises(ValueError, match="estimatedAmount"):
        parse_claim(json.dumps({"claimId": "CLM-1842", "policyNumber": "POL-77120"}))


def test_defaults_description_to_empty_string():
    claim = parse_claim(
        json.dumps(
            {"claimId": "CLM-1842", "policyNumber": "POL-77120", "estimatedAmount": 6200}
        )
    )

    assert claim["description"] == ""
