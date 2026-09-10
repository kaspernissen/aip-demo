import pytest
from pds.rules import Decision, PolicyInput, decide

AGENT = "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
OTHER = "spiffe://northstar.local/ns/northstar-agents/sa/claims-agent"


def _input(**kw):
    base = dict(caller=AGENT, tool="execute_payment", amount=6200.0,
                currency="EUR", autonomy_level="assisted")
    base.update(kw)
    return PolicyInput(**base)


def test_execute_above_the_threshold_is_denied():
    d = decide(_input(amount=6200.0))
    assert d.permit is False
    assert d.rule == "settlement-agent.assisted.execute_limit"
    assert d.limit == 6000.0


def test_execute_at_the_threshold_is_permitted():
    """The limit is inclusive: 'up to €6,000' means €6,000 is allowed."""
    assert decide(_input(amount=6000.0)).permit is True


def test_execute_below_the_threshold_is_permitted():
    """Exit criterion 6: the platform must permit as well as refuse."""
    assert decide(_input(amount=5800.0)).permit is True


def test_proposing_any_amount_is_permitted():
    """Only execution is limited. A proposal has no effect on the world, and
    the demo depends on the model being able to propose 6200 and then be
    refused execution of it."""
    assert decide(_input(tool="propose_payment", amount=6200.0)).permit is True


def test_identity_is_load_bearing_not_decoration():
    """Exit criterion 5: the decision changes when the identity changes.

    An unknown caller is denied at an amount an authorised caller may execute,
    which is what makes the SPIFFE ID a real input rather than scenery.
    """
    assert decide(_input(caller=OTHER, amount=100.0)).permit is False
    assert decide(_input(caller=AGENT, amount=100.0)).permit is True


def test_an_unknown_tool_is_denied():
    assert decide(_input(tool="drop_database")).permit is False


def test_a_missing_amount_on_execute_is_denied_not_defaulted():
    """Fail closed. A malformed request must never become a permit."""
    assert decide(_input(amount=None)).permit is False


def test_a_higher_autonomy_level_raises_the_limit():
    """M5 flips this value and the sentence stays true. Constant in M2."""
    assert decide(_input(amount=6200.0, autonomy_level="autonomous")).permit is True


def test_the_denial_reason_names_both_numbers():
    """The model narrates this string, so it must contain what a customer
    needs to hear: what was asked and what the limit is."""
    reason = decide(_input(amount=6200.0)).reason
    assert "6200" in reason and "6000" in reason


def test_decisions_are_not_retryable():
    assert decide(_input(amount=6200.0)).retryable is False


# --- F5. The limit's only test is `amount > limit`, so everything merely "not
# --- greater" permitted -- including values that are not money at all. Fixed
# --- in both layers; these are the rule layer's half.


@pytest.mark.parametrize("amount", [
    float("nan"),        # not greater than anything, so it used to permit
    float("inf"),        # denied before, but as an "over the limit" payment
    float("-inf"),
])
def test_a_non_finite_amount_is_denied(amount):
    """A policy service that permits NaN is worse than one with no policy at
    all: the permit came back stamped with the limit rule's own name."""
    assert decide(_input(amount=amount)).permit is False


@pytest.mark.parametrize("amount", [0.0, -0.0, -0.01, -500000.0])
def test_a_zero_or_negative_amount_is_denied(amount):
    """A EUR 0 or -EUR 500,000 'payment' is outside the domain the limit
    reasons about, not inside a EUR 6,000 allowance."""
    assert decide(_input(amount=amount)).permit is False


@pytest.mark.parametrize("amount", [
    float("nan"), float("inf"), float("-inf"), 0.0, -0.0, -0.01, -500000.0,
])
def test_an_unpayable_amount_is_not_attributed_to_the_limit_rule(amount):
    """The rule name is the audit record. Refusing NaN under
    `settlement-agent.assisted.execute_limit` would claim the limit rule had
    considered a NaN payment and reached a view about EUR 6,000, which is not
    what happened. A reader must be able to tell "the limit refused this" from
    "this was never a payable amount".
    """
    decision = decide(_input(amount=amount))
    assert decision.rule == "invalid_amount"
    assert decision.rule != "settlement-agent.assisted.execute_limit"


def test_the_unpayable_amount_denial_still_reports_the_callers_allowance():
    """`limit` is what the caller *was* allowed, which an audit trail needs
    even for a request that never qualified for it."""
    assert decide(_input(amount=float("nan"))).limit == 6000.0


def test_the_smallest_payable_amount_is_still_permitted():
    """The guard must refuse non-money, not quietly tighten the limit. Without
    this, `amount <= 0` could drift to `amount <= 1` unnoticed."""
    assert decide(_input(amount=0.01)).permit is True


def test_a_missing_currency_does_not_leave_a_hole_in_the_refusal_text():
    """The reason is narrated to a customer. `server.py` falls back to an empty
    currency rather than inventing "EUR", so the text must still read as
    English: not "requested 6200  exceeds the 6000  limit"."""
    reason = decide(_input(amount=6200.0, currency="")).reason
    assert "  " not in reason
    assert "requested 6200 exceeds the 6000 limit" in reason


def test_the_refusal_text_still_names_the_currency_when_one_was_sent():
    """The other half of the helper: EUR must not have been dropped. This is
    the exact string measured off the running container in Task 3."""
    assert decide(_input(amount=6200.0)).reason == (
        "requested 6200 EUR exceeds the 6000 EUR limit for this agent at "
        "autonomy level assisted")


def test_the_permit_text_also_names_the_currency_once_each():
    assert decide(_input(amount=5800.0)).reason == \
        "5800 EUR is within the 6000 EUR limit"
