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


# --- which path, and the free-text draft's boundary ---------------------

import datetime as dt
import re

from intake import check_draft, is_json_object, new_intake_claim_id, validate_claim

TODAY = dt.date(2026, 10, 23)
GOOD_DRAFT = {"policyNumber": "POL-77120", "description": "Rear-ended at a junction",
              "estimatedAmount": 5800, "currency": "EUR", "incidentDate": "2026-10-20",
              "missing": [], "question": None}


@pytest.mark.parametrize("text,expected", [
    ('{"claimId": "CLM-1"}', True),
    ('  {"claimId": "CLM-1"}  ', True),
    ("Someone drove into my car yesterday", False),
    ('{"claimId": ', False),         # a broken object is prose to the model, not a 400
    ("[1, 2]", False),
    ("6200", False),
    ('"a JSON string"', False),
    ("", False),
])
def test_only_a_json_object_takes_the_json_path(text, expected):
    assert is_json_object(text) is expected


def test_parse_claim_is_validate_claim_on_a_parsed_object():
    raw = {"claimId": "CLM-1842", "policyNumber": "POL-77120", "estimatedAmount": "6200"}
    assert parse_claim(json.dumps(raw)) == validate_claim(raw)


def test_an_intake_claim_id_has_the_documented_shape_and_is_not_reused():
    ids = {new_intake_claim_id() for _ in range(50)}
    assert all(re.fullmatch(r"CLM-INTAKE-[0-9A-F]{6}", i) for i in ids)
    assert len(ids) > 45


def test_a_complete_draft_is_a_claim_built_by_the_json_validator():
    claim, missing = check_draft("CLM-INTAKE-ABC123", GOOD_DRAFT, TODAY)
    assert missing == []
    assert claim == {"claimId": "CLM-INTAKE-ABC123", "policyNumber": "POL-77120",
                     "description": "Rear-ended at a junction", "estimatedAmount": 5800.0,
                     "incidentDate": "2026-10-20"}


@pytest.mark.parametrize("change,field", [
    ({"policyNumber": None}, "policyNumber"),
    ({"policyNumber": "   "}, "policyNumber"),
    ({"policyNumber": "P" * 65}, "policyNumber"),
    ({"estimatedAmount": None}, "estimatedAmount"),
    ({"estimatedAmount": 0}, "estimatedAmount"),
    ({"estimatedAmount": -5800}, "estimatedAmount"),
    ({"estimatedAmount": float("nan")}, "estimatedAmount"),
    ({"estimatedAmount": float("inf")}, "estimatedAmount"),
    ({"estimatedAmount": "lots"}, "estimatedAmount"),
    ({"estimatedAmount": True}, "estimatedAmount"),
    ({"currency": "USD"}, "estimatedAmount"),   # never converted: asked again, in euro
    ({"incidentDate": None}, "incidentDate"),
    ({"incidentDate": "last Tuesday"}, "incidentDate"),
    ({"incidentDate": "2026-10-24"}, "incidentDate"),  # tomorrow
    ({"description": ""}, "description"),
])
def test_a_value_that_would_not_validate_is_missing_not_trusted(change, field):
    claim, missing = check_draft("CLM-INTAKE-ABC123", {**GOOD_DRAFT, **change}, TODAY)
    assert claim is None and missing == [field]


def test_the_models_own_missing_list_does_not_decide():
    # The model says nothing is missing; the code finds the policy number is.
    claim, missing = check_draft("CLM-INTAKE-ABC123",
                                 {**GOOD_DRAFT, "policyNumber": None, "missing": []}, TODAY)
    assert claim is None and missing == ["policyNumber"]
    # The model says the policy number is missing; the code finds it is not.
    claim, missing = check_draft("CLM-INTAKE-ABC123",
                                 {**GOOD_DRAFT, "missing": ["policyNumber"]}, TODAY)
    assert claim is not None and missing == []


def test_missing_fields_come_back_in_a_fixed_order():
    claim, missing = check_draft("CLM-INTAKE-ABC123", {"missing": ["description"]}, TODAY)
    assert claim is None
    assert missing == ["policyNumber", "estimatedAmount", "incidentDate", "description"]


def test_a_draft_never_carries_its_own_claim_id_into_the_claim():
    claim, _ = check_draft("CLM-INTAKE-ABC123", {**GOOD_DRAFT, "claimId": "CLM-1842"}, TODAY)
    assert claim["claimId"] == "CLM-INTAKE-ABC123"


def test_a_long_description_is_bounded():
    claim, _ = check_draft("CLM-INTAKE-ABC123", {**GOOD_DRAFT, "description": "x" * 5000}, TODAY)
    assert len(claim["description"]) == 2000
