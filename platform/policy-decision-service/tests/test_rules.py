import ast
import re
from pathlib import Path

import pytest
from pds.rules import Decision, PolicyInput, decide

# platform/policy-decision-service/tests/test_rules.py -> repo root is three
# parents up.
REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_SUPERVISED = REPO_ROOT / "tests" / "e2e" / "test_supervised.py"
CRITERION_4_FUNCTION = (
    "test_criterion_4_the_pds_permits_under_the_same_rule_for_a_different_caller"
)

AGENT = "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
OTHER = "spiffe://northstar.local/ns/northstar-agents/sa/claims-agent"
SENIOR = "spiffe://northstar.local/ns/northstar-agents/sa/senior-settlement-agent"


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


def test_the_autonomous_row_gets_its_own_honest_rule_name():
    """M5 Task 3, Step 8: `(settlement-agent, autonomous)` used to be
    reported under the literal `settlement-agent.assisted.execute_limit` --
    pre-existing (M2.2, `fb3e294`), and unreachable until M5 made
    `autonomous` an actual resolvable level. Composing the autonomy segment
    fixes exactly this row; nothing else in `EXECUTE_LIMITS` was mislabelled
    for a caller this table already knew, so this is the one new positive
    assertion Step 8 needs and the only row whose rule name actually
    changes."""
    d = decide(_input(caller=AGENT, amount=6200.0, autonomy_level="autonomous"))
    assert d.permit is True
    assert d.rule == "settlement-agent.autonomous.execute_limit"


def test_the_autonomous_rows_deny_branch_also_gets_its_own_honest_rule_name():
    """Whole-branch review, J2: the test above only ever drives the PERMIT
    side of the `(settlement-agent, autonomous)` row -- 6,200 against its
    1,000,000 limit -- so nothing anywhere asserted the DENY branch's rule
    name. Reverting `decide()`'s deny-branch composition to the pre-M5 literal
    left the entire PDS suite green (review mutation A16); the control
    mutation on the permit branch (A17) went red, which is what made this the
    one uncovered branch: an autonomous-level request over its own limit would
    have its denial reported under `settlement-agent.assisted.execute_limit`,
    the exact mislabel this milestone's commit title claims to have closed,
    surviving in the one place nothing exercised.
    """
    d = decide(_input(caller=AGENT, amount=2_000_000.0, autonomy_level="autonomous"))
    assert d.permit is False
    assert d.rule == "settlement-agent.autonomous.execute_limit"


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


# ---------------------------------------------------------------------------
# M3 Task 3: the colleague with higher approval authority.
#
# One new EXECUTE_LIMITS row, nothing else -- `decide()` itself is unchanged.
# The rule name `settlement-agent.assisted.execute_limit` is composed from
# `req.autonomy_level` only, never from `req.caller` (M5 Task 3 replaced the
# literal with an f-string, but the load-bearing property this section is
# about did not change: it is still keyed off the autonomy level alone), so
# the senior identity's decision -- permit or deny -- is reported under the
# exact same rule string as the junior identity's. That is what "same rule,
# different principal" means in practice, and it is asserted here rather than
# narrated.
# ---------------------------------------------------------------------------


def test_the_senior_identity_is_permitted_at_the_amount_the_junior_is_denied():
    """Exit criterion 1: EUR 6,200 refused for one principal, permitted for the
    other, same rule name."""
    junior = decide(_input(caller=AGENT, amount=6200.0))
    senior = decide(_input(caller=SENIOR, amount=6200.0))

    assert junior.permit is False
    assert senior.permit is True
    assert junior.rule == senior.rule == "settlement-agent.assisted.execute_limit"


def test_the_senior_identity_is_denied_above_its_own_higher_limit():
    """More authority, not unlimited authority. The colleague still has a
    ceiling, and going over it is refused under the same rule name."""
    d = decide(_input(caller=SENIOR, amount=30_000.0))
    assert d.permit is False
    assert d.rule == "settlement-agent.assisted.execute_limit"
    assert d.limit == 25_000.0


def test_the_senior_limit_is_higher_than_the_junior_limit_not_equal_to_it():
    junior = decide(_input(caller=AGENT, amount=6000.0))
    senior = decide(_input(caller=SENIOR, amount=6000.0))
    assert senior.limit is not None and junior.limit is not None
    assert senior.limit > junior.limit


# ---------------------------------------------------------------------------
# M3 Task 3 Step 5's prerequisite: recording a refusal must itself be a policy
# call the gateway will pass through (settlement-agent reaches payments-api
# only through the same governed MCP path everything else in this repo uses).
# `refuse_payment` moves no money -- it writes a reason beside a payment that
# already did not execute -- so it belongs with `propose_payment` among the
# tools that carry no effect to authorise, not with `execute_payment`.
# ---------------------------------------------------------------------------


def test_refusing_a_payment_is_unlimited_for_either_settlement_identity():
    """M3 Task 3 fix round (R12): unconditional was wrong -- `refuse_payment`
    is scoped to the two settlement identities, as data, not to every caller.
    A caller-scoped tool is still unlimited *for its allowed callers*, which
    is what this test pins; the disallowed and unauthenticated cases are
    below."""
    assert decide(_input(tool="refuse_payment", amount=None, caller=AGENT)).permit is True
    assert decide(_input(tool="refuse_payment", amount=None, caller=SENIOR)).permit is True


# ---------------------------------------------------------------------------
# M3 Task 3 review, finding 1 (R11): `execute_approved` needs a way to read
# the ledger back before executing. `get_payment` is read-only, moves no
# money and reverses nothing -- the same argument `propose_payment` already
# carries in UNLIMITED_TOOLS -- so it joins that set unconditionally, for any
# caller. Unlike `refuse_payment`, nothing about `get_payment` writes to the
# human-review surface, so it is not caller-scoped.
# ---------------------------------------------------------------------------


def test_getting_a_payment_is_unlimited_for_any_caller():
    assert decide(_input(tool="get_payment", amount=None, caller=AGENT)).permit is True
    assert decide(_input(tool="get_payment", amount=None, caller=SENIOR)).permit is True
    assert decide(_input(tool="get_payment", amount=None, caller=OTHER)).permit is True
    assert decide(_input(tool="get_payment", amount=None, caller=None)).permit is True


# ---------------------------------------------------------------------------
# M3 Task 3 review, finding 2 (R12b): `refuse_payment` unconditional for any
# caller -- including `caller=None` -- let any holder of a valid SVID (or, in
# principle, no identity at all) author the justification a human reviewer
# reads before releasing money. Fixed as data: a tool -> allowed-callers
# mapping alongside EXECUTE_LIMITS/UNLIMITED_TOOLS, consulted by one more
# lookup in `decide()`, not a new branch keyed on the tool name.
# ---------------------------------------------------------------------------


def test_refusing_a_payment_is_denied_for_a_caller_outside_the_settlement_identities():
    assert decide(_input(tool="refuse_payment", amount=None, caller=OTHER)).permit is False


def test_refusing_a_payment_is_denied_for_caller_none():
    """The review's own point (finding 2, item 4): an unlimited tool that
    permits `caller=None` is unlimited even for an unidentified principal.
    `jwtAuth: mode: strict` makes this defence-in-depth in practice, but the
    policy itself must not be the layer that waves it through."""
    assert decide(_input(tool="refuse_payment", amount=None, caller=None)).permit is False


def test_proposing_a_payment_is_still_unlimited_for_any_caller():
    """propose_payment is NOT caller-scoped -- only refuse_payment is (R12b).
    Regression guard: the caller-scope data must not spread to a tool the
    review never asked to restrict."""
    assert decide(_input(tool="propose_payment", amount=None, caller=None)).permit is True
    assert decide(_input(tool="propose_payment", amount=None, caller=OTHER)).permit is True


# ---------------------------------------------------------------------------
# M5 Task 3, Step 6: a contract that hands back a variant with no policy row
# must deny -- and does, with no production change here. `EXECUTE_LIMITS` is
# keyed by `(caller, autonomy_level)`, so an autonomy level nothing wrote a
# row for is simply a dict miss, denied by the same branch an unrecognised
# *caller* falls through.
#
# Whole-branch review, N6/M5-R25: that branch used to report BOTH misses
# under `unknown_caller`, which is a misnomer for this case -- nothing about
# the caller (`settlement-agent`, a row this table knows at two other levels)
# was unknown here; the level was. `decide()` now derives whether the caller
# itself has any row at all (`_KNOWN_CALLERS`) and reports this case under
# `unknown_autonomy_level` instead. The fail-closed BEHAVIOUR is unchanged --
# still a plain dict miss, still denied, still no defensive branch added to
# hold the property -- only the audit-trail label moved to name the
# dimension that was actually wrong. This test used to assert
# `unknown_caller` here; that was itself the M5-R25 ambiguity made concrete,
# not a fact worth preserving, so it is updated rather than left green by
# accident.
# ---------------------------------------------------------------------------


def test_an_unknown_variant_denies_through_the_existing_table():
    """Garbage in the contract must deny -- and does, without a new branch,
    because EXECUTE_LIMITS is keyed by (caller, level) and will simply miss.
    The caller here (settlement-agent) is a known identity, just not at this
    level, so the rule name is `unknown_autonomy_level`, not `unknown_caller`
    -- see the section comment above (N6/M5-R25)."""
    decision = decide(_input(autonomy_level="banana"))
    assert decision.permit is False
    assert decision.rule == "unknown_autonomy_level"


def test_an_unrecognised_identity_still_denies_under_unknown_caller():
    """The other half of N6/M5-R25's split: an identity with no row at ANY
    level -- not merely at this one -- must still deny under `unknown_caller`,
    unchanged. `OTHER` (claims-agent's SPIFFE ID) never appears in
    EXECUTE_LIMITS at all, so `_KNOWN_CALLERS` genuinely does not contain it."""
    decision = decide(_input(caller=OTHER, autonomy_level="assisted"))
    assert decision.permit is False
    assert decision.rule == "unknown_caller"


# ---------------------------------------------------------------------------
# M5-R23: the task-3 review found that "substitute #2" -- a direct read of
# criterion 4's own asserted string, parsed live from
# `tests/e2e/test_supervised.py` and compared against `decide()`'s actual
# output -- was described in the task-3 report as run, but left no artifact:
# no such test was in the diff, so it caught no future drift. This is that
# substitute, committed.
# ---------------------------------------------------------------------------


def _criterion_4_asserted_rule() -> str:
    """Criterion 4's own asserted `policy.rule` literal, parsed live out of
    `tests/e2e/test_supervised.py` -- not retyped here, so a change to that
    file's literal is what this test reads, rather than a copy that can
    drift from it silently."""
    source = TEST_SUPERVISED.read_text()
    tree = ast.parse(source, filename=str(TEST_SUPERVISED))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == CRITERION_4_FUNCTION:
            segment = ast.get_source_segment(source, node)
            match = re.search(r'ATTR_POLICY_RULE\)\s*==\s*"([^"]+)"', segment)
            assert match, (
                f"found {CRITERION_4_FUNCTION} in {TEST_SUPERVISED}, but no "
                '`ATTR_POLICY_RULE) == "..."` assertion inside it -- has its '
                "shape changed?"
            )
            return match.group(1)
    raise AssertionError(
        f"{TEST_SUPERVISED} no longer defines {CRITERION_4_FUNCTION} -- "
        "nothing to compare decide()'s output against"
    )


def test_criterion_4s_own_asserted_rule_matches_deciders_output_for_the_senior_identity():
    """Criterion 4 is a `@browser_test` (live cluster, live LLM narration),
    out of scope under this task's no-full-gate constraint (M5-R19 defers
    its live run to Task 6's gate). This is the hermetic substitute: read
    criterion 4's own literal (above) and compare it against `decide()`'s
    actual output for exactly the scenario criterion 4 exercises -- the
    senior identity, `assisted`, `execute_payment`, EUR 6,200 (permitted, per
    M3). It is evidence about `decide()`, not about the live path -- the link
    C1's guard closes is the one this substitute cannot cover -- but a
    regression in either `decide()`'s rule composition or criterion 4's own
    literal is caught here without a browser or a live cluster.
    """
    asserted = _criterion_4_asserted_rule()
    produced = decide(_input(caller=SENIOR, amount=6200.0)).rule
    assert produced == asserted, (
        f"decide()'s rule for the senior identity at EUR 6,200 is "
        f"{produced!r}, but tests/e2e/test_supervised.py's criterion 4 "
        f"asserts {asserted!r}"
    )
