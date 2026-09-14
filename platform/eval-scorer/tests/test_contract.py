"""`scorer/contract.py`: the audit's own, independent read of the level.

**Two things this file proves, and neither is the same claim.**

1. `contract_level_at` implements route 2 of the STOP decision recorded in
   `scorer/contract.py`'s module docstring: a version present on only one
   side, or present and different on both, is a refusal; absent on both
   sides is agreement. `test_*_is_refused` / `test_*_is_agreement` below
   exercise all four quadrants, not just the two the plan's sketch
   considered.
2. `current_level` (and the `CURRENT_LEVEL` constant it returns) must not
   silently fall behind `deploy/platform/openfeature/featureflag.yaml` the
   way `scorer/authority.py`'s table must not fall behind `pds/rules.py` --
   `test_current_level_matches_the_feature_flags_targeting_branches` is that
   guard, parsed rather than retyped (`fixtures.featureflag_targeting_branch_values`).
"""
from __future__ import annotations

import pytest

import fixtures
from scorer.authority import SETTLEMENT_AGENT
from scorer.contract import (
    CURRENT_CONTRACT_VERSION,
    CURRENT_LEVEL,
    TARGETED_CALLER,
    TrajectoryUnscorable,
    contract_level_at,
    current_level,
)

OTHER_CALLER = "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"


# --- `current_level`: the independent second copy --------------------------


def test_current_level_is_assisted_for_the_targeted_caller_today():
    """M5-R8: the demo ships in its refusing state. Both targeting branches
    are "assisted", so the targeted caller (`settlement-agent`) resolves
    "assisted" today, not "autonomous"."""
    assert current_level(TARGETED_CALLER) == "assisted"


def test_current_level_is_the_same_for_a_caller_the_flag_never_names():
    """M5-R8's no-op: EVERY caller resolves the default branch's value today,
    because both branches carry the same string. This is expected to change
    the day Task 6 flips the targeted branch -- see the module docstring's
    "what this means for Task 6"."""
    assert current_level(OTHER_CALLER) == CURRENT_LEVEL


def test_targeted_caller_is_the_one_the_flag_names():
    assert TARGETED_CALLER == SETTLEMENT_AGENT


def test_current_level_matches_the_feature_flags_targeting_branches():
    """The build-time check `authority.py`'s docstring argues for, applied to
    the level instead of the limit table. Parsed from
    `featureflag.yaml`, not retyped -- if a human flips one branch without
    updating `CURRENT_LEVEL` (Task 6's job, per the module docstring), this
    reddens rather than silently scoring against a stale copy.
    """
    branches = fixtures.featureflag_targeting_branch_values()
    assert set(branches) == {CURRENT_LEVEL}, (
        f"featureflag.yaml's targeting branches are {branches}, but "
        f"scorer/contract.py's CURRENT_LEVEL is {CURRENT_LEVEL!r}. If "
        "M5-R8's no-op has been flipped (Task 6), update CURRENT_LEVEL (and "
        "make current_level() caller-dependent, since the branches now "
        "disagree) in the same commit -- do not leave this test to catch it "
        "after the fact."
    )


def test_every_flag_variant_has_a_published_row_for_the_targeted_caller():
    """`current_level` must only ever be able to return a string
    `authority.py`'s table actually has a row for, for at least the caller
    the targeting rule names -- otherwise a future variant addition could
    make `current_level` return a value `published_limit` treats as
    `unknown-identity` for the very caller the flag is supposed to be
    governing."""
    from scorer.authority import PUBLISHED_EXECUTE_LIMITS

    variants = fixtures.featureflag_variants()
    assert CURRENT_LEVEL in variants, (
        f"CURRENT_LEVEL {CURRENT_LEVEL!r} is not one of featureflag.yaml's "
        f"declared variants {variants}"
    )
    assert (TARGETED_CALLER, CURRENT_LEVEL) in PUBLISHED_EXECUTE_LIMITS


# --- `contract_level_at`: the temporal guard, all four quadrants -----------


def test_both_versions_absent_is_agreement_not_divergence():
    """Route 2's whole point: nothing anywhere publishes a version today
    (measured in the module docstring), so this must not refuse by default."""
    assert (
        contract_level_at(None, TARGETED_CALLER, now_version=None)
        == CURRENT_LEVEL
    )


def test_matching_versions_are_agreement():
    assert (
        contract_level_at("v3", TARGETED_CALLER, now_version="v3")
        == CURRENT_LEVEL
    )


def test_differing_versions_are_refused():
    with pytest.raises(TrajectoryUnscorable):
        contract_level_at("v2", TARGETED_CALLER, now_version="v3")


def test_one_sided_version_is_refused_not_waved_through():
    """The quadrant the plan's sketch did not distinguish from "both
    absent": a version on only one side is itself a real change (something
    started, or stopped, publishing one) and must not be forgiven the way
    "both absent" is."""
    with pytest.raises(TrajectoryUnscorable):
        contract_level_at("v2", TARGETED_CALLER, now_version=None)
    with pytest.raises(TrajectoryUnscorable):
        contract_level_at(None, TARGETED_CALLER, now_version="v3")


def test_the_refusal_names_both_versions_and_the_caller():
    with pytest.raises(TrajectoryUnscorable) as excinfo:
        contract_level_at("v2", TARGETED_CALLER, now_version="v3")
    message = str(excinfo.value)
    assert "v2" in message
    assert "v3" in message
    assert TARGETED_CALLER in message


def test_no_flag_declares_metadata_while_the_contract_has_no_version():
    """I2 (fix round 1): route 2's dormancy was hoped for, not enforced.

    `pds/server.py` writes `feature_flag.version` onto every decision span
    whenever flagd resolves ANY version, with no check that
    `CURRENT_CONTRACT_VERSION` is real. So the day `featureflag.yaml` gains
    a flag `metadata` block -- the very route 1 this module's docstring
    names as a future possibility, and the only thing that would make flagd
    resolve a version at all -- every decision starts carrying one while
    this module still resolves `None`, and `contract_level_at` refuses
    EVERY trajectory (`decision_version != None` for all of them). Measured
    by the task-5 review: adding such a block left all 152 tests GREEN
    before this guard existed, and the review's mutation K showed the
    blast radius if the two ever go out of step for real -- 21 tests
    failing, all of `test_server.py` among them. This is the build-time
    half that keeps it from shipping silently: the two edits (the metadata
    block, and `CURRENT_CONTRACT_VERSION`) must land in the same commit, the
    same discipline `test_current_level_matches_the_feature_flags_targeting_branches`
    already holds `CURRENT_LEVEL` to.
    """
    if CURRENT_CONTRACT_VERSION is not None:
        pytest.skip(
            "CURRENT_CONTRACT_VERSION is no longer None -- route 1 has been "
            "taken for real, and a flag metadata block is now expected. "
            "This specific guard's job ends here; see the module docstring "
            "for what replaces it."
        )
    assert not fixtures.featureflag_flag_declares_metadata(), (
        f"{fixtures.FEATUREFLAG} now declares a flag `metadata` block while "
        "CURRENT_CONTRACT_VERSION is still None. Every policy.decision span "
        "would carry a feature_flag.version this contract has nothing to "
        "compare it against, and contract_level_at would refuse every "
        "trajectory. Update CURRENT_CONTRACT_VERSION (route 1, see this "
        "module's docstring) in the same commit that adds the block."
    )


def test_the_currently_published_contract_version_is_none():
    """Pinned so a future change to `CURRENT_CONTRACT_VERSION` is a
    deliberate edit, not a silent one: if this ever stops being `None`, route
    1 has been taken (a real `metadata` block exists) and the module
    docstring's STOP section needs a dated correction, not just a new value
    here."""
    assert CURRENT_CONTRACT_VERSION is None
