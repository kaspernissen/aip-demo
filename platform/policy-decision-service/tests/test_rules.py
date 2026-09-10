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
