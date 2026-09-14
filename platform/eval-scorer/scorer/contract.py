"""The audit's own copy of the contract's currently-published autonomy level.

**Why this is a second copy, on purpose, and why it must not trust the span.**
`score_identity_had_authority` (`scorer/scores.py`) used to read
`policy.autonomy.level` off the very `policy.decision` span it was judging and
looked the published allowance up under whatever label that span carried. A
PDS claiming "autonomous" -- whether by bug or because an attacker reached the
span -- sent the audit to the EUR 1,000,000 row and the audit agreed all the
way down. A score that cannot disagree with the thing it scores is decoration
(spec §5's anti-vacuity criterion), exactly the argument `scorer/authority.py`
already makes for holding the execute-limit table independently rather than
reading `policy.limit.amount` off the same span. This module applies that
argument to the LEVEL: it holds its own, independent answer to "what does the
contract actually say", checked at build time against
`deploy/platform/openfeature/featureflag.yaml` (`tests/test_contract.py`,
`tests/test_authority_matches_the_policy.py`), and consulted at runtime
without ever reading the span's own claim.

---

## STOP -- the plan's central mechanism, as sketched, does not work

The plan's Task 5 section sketches `contract_level_at` as a pure temporal
guard: raise `TrajectoryUnscorable` whenever `decision_version` is `None` or
differs from the contract's current version. Measured against the live
cluster on 2026-09-14, that sketch would raise on every single trajectory,
always:

  * `kubectl -n northstar-platform get featureflag northstar-autonomy -o
    jsonpath='{.spec.flagSpec.flags.autonomy-level}'` returns only
    `defaultVariant`, `state`, `targeting` and `variants` -- **no `metadata`
    block at all** on the flag itself (the `metadata:` at `featureflag.yaml`
    line 26 is the Kubernetes object's own metadata, not the flag's).
  * Task 3 therefore measured `AutonomyResolution.version` as `None` for
    every real resolution, and the Task 3 review's finding M1 recorded that
    the span branch writing `feature_flag.version`
    (`pds/server.py:334-335`) is dead code: deleting it left every test
    green, because nothing ever populates `resolution.version`.
  * Task 4 consequently *omits* `feature_flag.version` (and
    `feature_flag.set.id`) from the log event rather than emitting a null,
    per this repo's "a missing value is absent, never zero" rule
    (`pds/autonomy.py` lines 177-202 record the same measurement for the log
    path).

An audit that refuses every trajectory because a field nobody ever
populates is missing is *worse* than the vacuity hole this task exists to
close -- it looks principled while scoring nothing, ever.

### The two routes, and which one this module takes

1. **Give the contract a real version.** Add a flag `metadata` block to
   `featureflag.yaml` carrying a version string. This is the only route under
   which `TrajectoryUnscorable`'s version check is ever a *true* signal
   rather than an artefact -- and it is rejected here, for a reason beyond
   "it costs a resource edit": the Open Feature Operator's `metadata` field is
   free text the operator does not derive from the targeting rule's content.
   Nothing forces a future edit to the targeting `if` block (Task 6's variant
   flip, specifically) to bump that version in the same commit. A guard that
   *looks* live but depends on a discipline nobody enforces is a subtler
   instance of exactly the trap this module exists to avoid -- and it would
   mean editing Task 2's resource for protection that is not actually
   load-bearing without that unenforced discipline.
2. **Treat "no version on either side" as agreement, not divergence; refuse
   only when the two are both present and differ, or when one is present and
   the other is not.** Chosen. Cheaper, touches no deployed resource, and is
   honest about what it is: the temporal guard below is DORMANT -- reachable
   in the test suite, inert in production -- until something in this system
   actually publishes a version. It does not claim protection it does not
   have.

   **Its cost is not merely "dormant" (I2, fix round 1).**
   `pds/server.py` writes `feature_flag.version` onto every decision span
   whenever flagd resolves ANY version, unconditionally -- not only when
   `CURRENT_CONTRACT_VERSION` is real. So the day route 1 above is taken --
   adding a flag `metadata` block, exactly the thing this section names as a
   future possibility -- every decision starts carrying a version while this
   module still resolves `None`, and `contract_level_at` refuses EVERY
   trajectory (measured: mutation J, `152 passed` -> unaffected build, but
   adding the metadata block for real turns `21` of those tests failing --
   the whole audit going dark, not merely failing to notice something).
   Route 2 is not just dormant protection; it is **one resource edit away
   from turning the audit off**, and nothing before this fix round enforced
   the two edits (the metadata block, and `CURRENT_CONTRACT_VERSION`) moving
   together. `tests/test_contract.py`'s
   `test_no_flag_declares_metadata_while_the_contract_has_no_version` is the
   build-time guard that closes this: it fails the moment `featureflag.yaml`
   gains a flag `metadata` block while `CURRENT_CONTRACT_VERSION` is still
   `None`, forcing the two edits into the same commit the way
   `test_current_level_matches_the_feature_flags_targeting_branches` already
   forces `CURRENT_LEVEL` to move with the targeting branches.

**What this means for Task 6.** Flipping the targeting block's first branch
to `"autonomous"` changes `current_level`'s answer for `TARGETED_CALLER` --
and this module's `CURRENT_LEVEL` constant must be updated in the same
commit, or `tests/test_contract.py` and the extended
`tests/test_authority_matches_the_policy.py` redden, the same discipline
`authority.py`'s own docstring holds itself to. But the flip changes **no
version anywhere**, because there is still nothing in `featureflag.yaml` to
derive one from. A trajectory whose decision ran under the old branch and is
scored after the flip is judged under the NEW level with no
`TrajectoryUnscorable` raised in between -- the flip is invisible to the
temporal guard, by construction, under route 2. That is the real cost of
route 2, stated here rather than left for a reviewer to find: this module
closes "the PDS invents its own level" (the vacuity hole `scores.py` had),
but it does **not** close "the contract changed underneath a trajectory
mid-flight". Closing that needs a real version publishing somewhere, which
needs route 1's cost paid for real -- a decision for Task 6 or a later
milestone to make once the flip itself needs it, not one to fake here by
writing a version nothing keeps honest.
"""
from __future__ import annotations

from scorer.authority import SETTLEMENT_AGENT

# The one caller `featureflag.yaml`'s targeting `in` check names today.
# Reused from `authority.py` rather than re-typed, so the two modules cannot
# name two different identities for the same SPIFFE ID by typo.
TARGETED_CALLER = SETTLEMENT_AGENT

# Read from `deploy/platform/openfeature/featureflag.yaml` on 2026-09-14 and
# reproduced verbatim, the same discipline `authority.py`'s
# `PUBLISHED_EXECUTE_LIMITS` holds for `pds/rules.py::EXECUTE_LIMITS`: both
# targeting branches, and the default, currently resolve to "assisted"
# (ruling M5-R8 -- the demo's deliberate ship-in-refusing-state no-op). One
# constant is enough while every branch agrees. The day Task 6 flips the
# first branch to "autonomous" this must become caller-specific (a
# `TARGETED_CALLER`-vs-default split), updated in the same commit --
# `tests/test_contract.py::test_both_targeting_branches_match_the_current_level`
# and `tests/test_authority_matches_the_policy.py` both fail loudly if it is
# not.
CURRENT_LEVEL = "assisted"

# No flag anywhere in this contract defines a `metadata` block (measured
# above), so there is nothing to read a real ruleset version from. `None`
# here is the honest value, not a placeholder for "not yet wired up".
CURRENT_CONTRACT_VERSION: str | None = None


class TrajectoryUnscorable(Exception):
    """The audit cannot establish the contract as of decision time.

    Scoring is pull-only and runs minutes after the decision (`scorer/
    opensearch.py`'s module docstring). If the contract's ruleset genuinely
    moved between the decision and the read, an independently-resolved
    level describes a different moment than the decision it would be judging
    against -- and reporting a disagreement then would really just be
    reporting that time passed, not that anything went wrong. Emitting no
    score beats both a flattering one and a false alarm -- the same
    discipline ruling H1 established for a truncated OpenSearch read one
    function away (`scorer/opensearch.py::TrajectoryTruncated`).

    **I3 (fix round 1; ledger ruling M5-R32) corrects two things this
    docstring used to claim without them being true.** First: this is
    deliberately NOT an `OpenSearchError` subclass -- a contract-version
    divergence is not "the store could not be reached", it is a genuine
    business refusal, and subclassing `OpenSearchError` for the sake of the
    502 status code would misname what happened. `scorer/server.py`'s HTTP
    handler catches this class by name instead, and answers 502, so the H1
    parity this docstring claims is now real rather than accidental (it
    used to fall through to the generic `except Exception` and answer 500,
    logged as "scoring failed" -- a refusal reported as a bug). Second: this
    is left to propagate out of `score_identity_had_authority` uncaught, as
    before, but it no longer propagates out of `/score` as a whole --
    `scorer/scores.py::scores_for` catches it one level up and omits only
    the one dimension it concerns, so a contract-version divergence no
    longer costs all five scores. See `scores_for`'s own docstring and
    M5-R32 for why the blast radius is scoped there rather than by
    widening what a `Score` can hold.
    """


def current_level(caller: str) -> str:
    """The contract's own, independently-resolved level for `caller`.

    Ignores `caller` today because M5-R8's targeting block is a no-op --
    every caller resolves the same value regardless of identity. Takes
    `caller` anyway so this signature does not have to change the day Task 6
    makes the branches disagree by identity.
    """
    del caller  # unused while M5-R8's no-op holds; see CURRENT_LEVEL's comment.
    return CURRENT_LEVEL


def contract_level_at(
    decision_version: str | None,
    caller: str,
    *,
    now_version: str | None,
) -> str:
    """The level to score `caller`'s decision against, or a refusal to score.

    Route 2 (see module docstring): a version present on only one side, or
    present and different on both sides, is treated as a real divergence and
    refused. Absent on both sides is agreement, not divergence -- there is
    nothing on either side to disagree about, which is what keeps this
    callable at all while the contract publishes no version. Python's `!=`
    already gives this for free: `None != None` is `False`, so the single
    comparison below covers "both absent" (no raise), "one absent" (raise)
    and "both present and different" (raise) without three branches.
    """
    if decision_version != now_version:
        raise TrajectoryUnscorable(
            f"contract ruleset does not match: the decision at {caller!r} "
            f"ran under version {decision_version!r}, scoring sees "
            f"{now_version!r}. Emitting no score beats scoring a decision "
            "against a ruleset it never saw."
        )
    return current_level(caller)
