"""The claim id must reach spans via baggage, never via function arguments."""
from opentelemetry import baggage, context

from correlation import CLAIM_ID_KEY, set_claim_id


def test_set_claim_id_puts_the_value_in_baggage():
    ctx = set_claim_id("CLM-1842")

    assert baggage.get_baggage(CLAIM_ID_KEY, ctx) == "CLM-1842"


def test_set_claim_id_does_not_mutate_the_ambient_context():
    set_claim_id("CLM-1842")

    assert baggage.get_baggage(CLAIM_ID_KEY, context.get_current()) is None


def test_key_is_the_documented_attribute_name():
    assert CLAIM_ID_KEY == "northstar.claim.id"
