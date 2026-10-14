import pytest
from pds.rules import Decision, PolicyInput, decide

AGENT = "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
OTHER = "spiffe://northstar.local/ns/northstar-agents/sa/claims-agent"
REVIEWER = "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"


def _input(**kw):
    base = dict(caller=AGENT, tool="execute_payment", amount=6200.0,
                currency="EUR", autonomy_level="assisted", ledger_status="proposed")
    base.update(kw)
    return PolicyInput(**base)


def test_assisted_may_release_nothing():
    d = decide(_input(amount=0.01, autonomy_level="assisted"))
    assert d.permit is False
    assert d.rule == "settlement-agent.assisted.execute_limit"
    assert d.limit == 0.0


def test_autonomous_may_release_up_to_6000_inclusive():
    assert decide(_input(amount=6000.0, autonomy_level="autonomous")).permit is True


def test_autonomous_denies_a_cent_over_6000():
    d = decide(_input(amount=6000.01, autonomy_level="autonomous"))
    assert d.permit is False
    assert d.rule == "settlement-agent.autonomous.execute_limit"
    assert d.limit == 6000.0


def test_autonomous_permits_below_the_limit():
    assert decide(_input(amount=5800.0, autonomy_level="autonomous")).permit is True


def test_proposing_stays_allowed_at_every_level():
    for level in ("assisted", "autonomous"):
        assert decide(_input(tool="propose_payment", autonomy_level=level)).permit is True


def test_a_human_may_release_a_payment_awaiting_approval_at_any_level():
    for level in ("assisted", "autonomous", "some-level-nobody-published"):
        d = decide(_input(caller=REVIEWER, amount=60000.0, autonomy_level=level,
                          ledger_status="awaiting_approval"))
        assert d.permit is True
        assert d.rule == "human-approved.execute"
        assert d.limit is None


def test_a_human_may_only_release_a_payment_awaiting_approval():
    for status in ("proposed", "executed", "rejected", None):
        d = decide(_input(caller=REVIEWER, ledger_status=status))
        assert d.permit is False
        assert d.rule == "human-approved.execute"
        assert repr(status) in d.reason


def test_a_human_approval_still_refuses_a_non_amount():
    for amount in (None, 0.0, -1.0, float("nan")):
        d = decide(_input(caller=REVIEWER, amount=amount, ledger_status="awaiting_approval"))
        assert d.permit is False


def test_the_ledger_status_widens_nothing_for_the_agent():
    d = decide(_input(amount=5800.0, autonomy_level="assisted", ledger_status="awaiting_approval"))
    assert d.permit is False and d.rule == "settlement-agent.assisted.execute_limit"


def test_the_agent_may_not_execute_a_payment_routed_to_a_human_even_at_autonomous():
    d = decide(_input(amount=5800.0, autonomy_level="autonomous", ledger_status="awaiting_approval"))
    assert d.permit is False
    assert d.rule == "settlement-agent.payment_not_proposed"
    assert d.limit == 6000.0


def test_the_agent_may_execute_a_proposed_payment_within_its_limit():
    d = decide(_input(amount=5800.0, autonomy_level="autonomous", ledger_status="proposed"))
    assert d.permit is True and d.rule == "settlement-agent.autonomous.execute_limit"


@pytest.mark.parametrize("status", [None, "executed", "rejected", "refused", ""])
def test_the_agent_is_denied_when_the_ledger_is_unreadable_or_not_proposed(status):
    d = decide(_input(amount=5800.0, autonomy_level="autonomous", ledger_status=status))
    assert d.permit is False and d.rule == "settlement-agent.payment_not_proposed"


def test_an_over_limit_amount_keeps_its_limit_rule_name_whatever_the_ledger_says():
    d = decide(_input(amount=6200.0, autonomy_level="autonomous", ledger_status=None))
    assert d.rule == "settlement-agent.autonomous.execute_limit" and d.permit is False


def test_no_senior_identity_is_known_any_more():
    from pds.rules import EXECUTE_LIMITS, HUMAN_APPROVERS
    named = {caller for caller, _ in EXECUTE_LIMITS} | HUMAN_APPROVERS
    assert not any("senior" in c for c in named)


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
    assert decide(_input(caller=OTHER, amount=100.0, autonomy_level="autonomous")).permit is False
    assert decide(_input(caller=AGENT, amount=100.0, autonomy_level="autonomous")).permit is True


def test_an_unknown_tool_is_denied():
    assert decide(_input(tool="drop_database")).permit is False


def test_a_missing_amount_on_execute_is_denied_not_defaulted():
    """Fail closed. A malformed request must never become a permit."""
    assert decide(_input(amount=None)).permit is False


def test_a_higher_autonomy_level_raises_the_limit():
    """A higher autonomy level raises the limit for the same identity."""
    assert decide(_input(amount=6000.0, autonomy_level="autonomous")).permit is True
    assert decide(_input(amount=6000.0, autonomy_level="assisted")).permit is False


def test_the_autonomous_row_gets_its_own_honest_rule_name():
    """`(settlement-agent, autonomous)` must not be reported under the
    literal `settlement-agent.assisted.execute_limit` -- composing the
    autonomy segment gives this row its own honest rule name, distinct from
    the assisted-level rows."""
    d = decide(_input(caller=AGENT, amount=5800.0, autonomy_level="autonomous"))
    assert d.permit is True
    assert d.rule == "settlement-agent.autonomous.execute_limit"


def test_the_autonomous_rows_deny_branch_also_gets_its_own_honest_rule_name():
    """The test above only ever drives the PERMIT side of the
    `(settlement-agent, autonomous)` row -- 5,800 against its 6,000
    limit -- so nothing asserts the DENY branch's rule name without this
    one. Reverting `decide()`'s deny-branch composition to a hardcoded
    literal would leave an autonomous-level request over its own limit
    reporting its denial under `settlement-agent.assisted.execute_limit` --
    the exact mislabel the composed rule name exists to avoid -- with
    nothing else in the suite catching it.
    """
    d = decide(_input(caller=AGENT, amount=6200.0, autonomy_level="autonomous"))
    assert d.permit is False
    assert d.rule == "settlement-agent.autonomous.execute_limit"


def test_the_denial_reason_names_both_numbers():
    """The model narrates this string, so it must contain what a customer
    needs to hear: what was asked and what the limit is."""
    reason = decide(_input(amount=6200.0, autonomy_level="autonomous")).reason
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
    assert decide(_input(amount=float("nan"))).limit == 0.0


def test_the_smallest_payable_amount_is_still_permitted():
    """The guard must refuse non-money, not quietly tighten the limit. Without
    this, `amount <= 0` could drift to `amount <= 1` unnoticed."""
    assert decide(_input(amount=0.01, autonomy_level="autonomous")).permit is True


def test_a_missing_currency_does_not_leave_a_hole_in_the_refusal_text():
    """The reason is narrated to a customer. `server.py` falls back to an empty
    currency rather than inventing "EUR", so the text must still read as
    English: not "requested 6200  exceeds the 6000  limit"."""
    reason = decide(_input(amount=6200.0, currency="", autonomy_level="autonomous")).reason
    assert "  " not in reason
    assert "requested 6200 exceeds the 6000 limit" in reason


def test_the_refusal_text_still_names_the_currency_when_one_was_sent():
    """The other half of the helper: EUR must not have been dropped. This is
    the exact string measured off the running container."""
    assert decide(_input(amount=6200.0, autonomy_level="autonomous")).reason == (
        "requested 6200 EUR exceeds the 6000 EUR limit for this agent at "
        "autonomy level autonomous")


def test_the_permit_text_also_names_the_currency_once_each():
    assert decide(_input(amount=5800.0, autonomy_level="autonomous")).reason == \
        "5800 EUR is within the 6000 EUR limit"


# ---------------------------------------------------------------------------
# `refuse_payment` moves no money -- it writes a reason beside a payment that
# already did not execute -- so it carries no effect to authorise. It is
# scoped to the settlement-agent: the only way a payment reaches
# `awaiting_approval` is this tool, and the human rule widens on that fact.
# ---------------------------------------------------------------------------


def test_only_the_settlement_agent_may_record_a_refusal():
    assert decide(_input(tool="refuse_payment", amount=None)).permit is True
    assert decide(_input(tool="refuse_payment", amount=None, caller=REVIEWER)).permit is False


# ---------------------------------------------------------------------------
# `get_payment` is read by the reviewer-ui and alongside the claims-agent. It is read-only, moves no
# money and reverses nothing -- the same argument `propose_payment` already
# carries in UNLIMITED_TOOLS -- so it joins that set unconditionally, for any
# caller. Unlike `refuse_payment`, nothing about `get_payment` writes to the
# human-review surface, so it is not caller-scoped.
# ---------------------------------------------------------------------------


def test_getting_a_payment_is_unlimited_for_any_caller():
    assert decide(_input(tool="get_payment", amount=None, caller=AGENT)).permit is True
    assert decide(_input(tool="get_payment", amount=None, caller=OTHER)).permit is True
    assert decide(_input(tool="get_payment", amount=None, caller=None)).permit is True


# ---------------------------------------------------------------------------
# `refuse_payment` unconditional for any
# caller -- including `caller=None` -- would let any holder of a valid SVID (or, in
# principle, no identity at all) author the justification a human reviewer
# reads before releasing money. Fixed as data: a tool -> allowed-callers
# mapping alongside EXECUTE_LIMITS/UNLIMITED_TOOLS, consulted by one more
# lookup in `decide()`, not a new branch keyed on the tool name.
# ---------------------------------------------------------------------------


def test_refusing_a_payment_is_denied_for_a_caller_outside_the_settlement_identities():
    assert decide(_input(tool="refuse_payment", amount=None, caller=OTHER)).permit is False


def test_refusing_a_payment_is_denied_for_caller_none():
    """An unlimited tool that permits `caller=None` is unlimited even for an
    unidentified principal. `jwtAuth: mode: strict` makes this
    defence-in-depth in practice, but the policy itself must not be the
    layer that waves it through."""
    assert decide(_input(tool="refuse_payment", amount=None, caller=None)).permit is False


def test_proposing_a_payment_is_still_unlimited_for_any_caller():
    """propose_payment is NOT caller-scoped -- only refuse_payment is.
    Regression guard: the caller-scope data must not spread to a tool that
    has no reason to be restricted."""
    assert decide(_input(tool="propose_payment", amount=None, caller=None)).permit is True
    assert decide(_input(tool="propose_payment", amount=None, caller=OTHER)).permit is True


# ---------------------------------------------------------------------------
# A contract that hands back a variant with no policy row
# must deny -- and does, with no production change here. `EXECUTE_LIMITS` is
# keyed by `(caller, autonomy_level)`, so an autonomy level nothing wrote a
# row for is simply a dict miss, denied by the same branch an unrecognised
# *caller* falls through.
#
# That branch must not report BOTH misses under `unknown_caller`, which
# would be a misnomer for this case -- nothing about the caller
# (`settlement-agent`, a row this table knows at two other levels) is
# unknown here; the level is. `decide()` derives whether the caller itself
# has any row at all (`_KNOWN_CALLERS`) and reports this case under
# `unknown_autonomy_level` instead. The fail-closed BEHAVIOUR is the same
# either way -- still a plain dict miss, still denied, still no defensive
# branch added to hold the property -- only the audit-trail label names the
# dimension that is actually wrong.
# ---------------------------------------------------------------------------


def test_an_unknown_variant_denies_through_the_existing_table():
    """Garbage in the contract must deny -- and does, without a new branch,
    because EXECUTE_LIMITS is keyed by (caller, level) and will simply miss.
    The caller here (settlement-agent) is a known identity, just not at this
    level, so the rule name is `unknown_autonomy_level`, not `unknown_caller`
    -- see the section comment above."""
    decision = decide(_input(autonomy_level="banana"))
    assert decision.permit is False
    assert decision.rule == "unknown_autonomy_level"


def test_an_unrecognised_identity_still_denies_under_unknown_caller():
    """The other half of the caller-vs-level distinction above: an identity
    with no row at ANY level -- not merely at this one -- must still deny
    under `unknown_caller`. `OTHER` (claims-agent's SPIFFE ID) never appears
    in EXECUTE_LIMITS at all, so `_KNOWN_CALLERS` genuinely does not contain
    it."""
    decision = decide(_input(caller=OTHER, autonomy_level="assisted"))
    assert decision.permit is False
    assert decision.rule == "unknown_caller"
