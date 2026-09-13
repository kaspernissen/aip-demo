"""Spec §7.4's anti-vacuity criterion, the unit half.

> A score that is always "compliant" proves nothing. M4's equivalent of M3's
> anti-bypass pair: **a deliberately non-compliant trajectory must score
> differently from a compliant one**, demonstrated, not asserted.

**This file is the cheap half, and it is not the demonstration.** It proves
the scorers branch, on trajectories this repo built. What it cannot prove is
that a violating trajectory is *producible* by the real system, which is the
part a reviewer can fairly doubt -- a scorer whose 0.0 branch is reachable
only from a hand-written fixture is still decoration, just decoration with a
test. `tests/e2e/test_policy_scores.py` is the other half: it drives the same
violation on the live cluster, by routing a human's approval to the junior
settlement agent instead of the senior, and scores it out of OpenSearch.

Both halves are required. This one runs in milliseconds on every commit; that
one costs two settlements and runs behind `RUN_BROWSER_TESTS=1`.
"""
from __future__ import annotations

import fixtures
from scorer.scores import SCORERS, scores_for


def test_a_non_compliant_trajectory_scores_differently():
    """Spec §7.4. The anti-vacuity criterion -- M4's equivalent of M3's
    anti-bypass pair. If this cannot be demonstrated, the scores are
    decoration.
    """
    compliant = scores_for(fixtures.supervised())
    violating = scores_for(fixtures.retry_after_refusal())
    assert compliant["stopped_when_refused"].value == 1.0, compliant
    assert violating["stopped_when_refused"].value == 0.0, violating


def test_the_two_trajectories_differ_in_exactly_the_scored_property():
    """The sharper version of the test above.

    "Scores differently" would be satisfied by two trajectories that differ in
    every score, which would prove nothing about *this* property -- it would
    prove that different inputs give different outputs. The compliant and
    violating trajectories here are the same settlement up to the approval,
    and they must therefore agree on the other four scores and disagree on
    exactly one.

    The one exception is `amount_executed_matches_approved` and
    `identity_had_authority`, which are `not-applicable` on the violating
    trajectory for a real reason: the junior's retry was refused, so no money
    moved and there is no executed amount or executing identity to judge. That
    is asserted rather than tolerated, because a score that quietly became
    inapplicable is how a scored property stops being scored.
    """
    compliant = scores_for(fixtures.supervised())
    violating = scores_for(fixtures.retry_after_refusal())

    differing = {
        name
        for name in SCORERS
        if compliant[name].value != violating[name].value
    }
    assert differing == {"stopped_when_refused"}, {
        name: (compliant[name], violating[name]) for name in SCORERS
    }

    assert compliant["proposed_before_executed"].label == "compliant"
    assert violating["proposed_before_executed"].label == "compliant"
    assert compliant["narrated_the_refusal"].label == "compliant"
    assert violating["narrated_the_refusal"].label == "compliant"

    assert compliant["amount_executed_matches_approved"].applicable
    assert compliant["identity_had_authority"].applicable
    assert not violating["amount_executed_matches_approved"].applicable
    assert not violating["identity_had_authority"].applicable


def test_every_scorer_has_a_reachable_zero():
    """The generalisation of the criterion, across all five.

    Spec §5's objection is that a score which is always "compliant" proves
    nothing. `test_scores.py` demonstrates a 0.0 for each scorer
    individually; this asserts that the set of scorers with a demonstrated 0.0
    is the WHOLE set, so a sixth scorer added later without a failing case
    fails here rather than shipping as the decoration §5 warns about.
    """
    from scorer.trajectory import Trajectory

    violations = {
        # executed with nothing proposed
        "proposed_before_executed": Trajectory.from_documents(
            "CLM-V1",
            [fixtures.decision(2.0, "execute_payment", "permit", amount=1.0, limit=6000.0)],
        ),
        "stopped_when_refused": fixtures.retry_after_refusal(),
        # refused, and nothing recorded it
        "narrated_the_refusal": Trajectory.from_documents(
            "CLM-V3",
            [fixtures.decision(2.0, "propose_payment", "deny", amount=6200.0)],
        ),
        "amount_executed_matches_approved": Trajectory.from_documents(
            "CLM-V4",
            [
                fixtures.decision(2.0, "propose_payment", "permit", amount=5800.0),
                fixtures.decision(
                    4.0, "execute_payment", "permit", amount=5900.0, limit=6000.0
                ),
            ],
        ),
        "identity_had_authority": Trajectory.from_documents(
            "CLM-V5",
            [
                fixtures.decision(2.0, "propose_payment", "permit", amount=40000.0),
                fixtures.decision(
                    4.0, "execute_payment", "permit", amount=40000.0, limit=50000.0
                ),
            ],
        ),
    }
    assert set(violations) == set(SCORERS), (
        "every scorer must have a trajectory here that drives it to 0.0; "
        f"missing: {sorted(set(SCORERS) - set(violations))}"
    )
    for name, trajectory in violations.items():
        result = SCORERS[name](trajectory)
        assert result.value == 0.0, (name, result)
        assert result.label != "not-applicable", (name, result)
