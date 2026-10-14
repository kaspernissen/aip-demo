"""The audit's copy of the execute allowances must equal the policy's own.

`scorer/authority.py` deliberately holds a SECOND copy of
`platform/policy-decision-service/pds/rules.py::EXECUTE_LIMITS`. Read that
module's header for why: a scorer that checks a decision against the limit
that same decision reports cannot disagree with it, and a score that cannot
disagree is decoration.

Independence at runtime, agreement at build time. This file supplies the
second half.

**Parsed with `ast`, never imported.** Two reasons, and the first is the one
that matters. `eval-scorer`'s container carries no PDS code and must not: if
this suite imported `pds.rules`, the "independent copy" argument would be
false in the only environment where it counts, and a later reader would
reasonably "simplify" `authority.py` into an import. Second, `ast.parse`
discards comments, so a comment in `rules.py` quoting an old limit cannot
satisfy this -- the same correction `tests/unit/test_stage2_spire_config.py`
made to its own SPIFFE-literal check after a mutation defeated the text
version.
"""
from __future__ import annotations

import ast
from pathlib import Path

import fixtures
from scorer.authority import (
    PUBLISHED_EXECUTE_LIMITS,
    PUBLISHED_HUMAN_APPROVAL_RULE,
    PUBLISHED_HUMAN_APPROVERS,
)

# platform/eval-scorer/tests/ -> platform/ -> policy-decision-service/pds
RULES = (
    Path(__file__).resolve().parents[2]
    / "policy-decision-service"
    / "pds"
    / "rules.py"
)


def _execute_limits_from_rules() -> dict[tuple[str, str], float]:
    """`EXECUTE_LIMITS` as `rules.py` literally declares it."""
    assert RULES.exists(), (
        f"{RULES} does not exist. This guard compares eval-scorer's published "
        "allowances against the policy's own; if the policy moved, point this "
        "at where it went rather than deleting the check -- without it, "
        "scorer/authority.py is an unguarded second copy of a table that "
        "decides whether money may move."
    )
    tree = ast.parse(RULES.read_text())
    for node in ast.walk(tree):
        targets = (
            [node.target] if isinstance(node, ast.AnnAssign) else
            getattr(node, "targets", [])
        )
        if not any(
            isinstance(t, ast.Name) and t.id == "EXECUTE_LIMITS" for t in targets
        ):
            continue
        value = node.value
        assert isinstance(value, ast.Dict), (
            "EXECUTE_LIMITS is no longer a dict literal in pds/rules.py, so "
            "this guard can no longer read it statically"
        )
        return {
            tuple(ast.literal_eval(key)): float(ast.literal_eval(val))
            for key, val in zip(value.keys, value.values)
        }
    raise AssertionError(
        f"no `EXECUTE_LIMITS = {{...}}` assignment found in {RULES}"
    )


def _assigned_literal(name: str):
    """The literal `rules.py` assigns to a module-level `name`, via `ast`."""
    tree = ast.parse(RULES.read_text())
    for node in tree.body:
        targets = (
            [node.target] if isinstance(node, ast.AnnAssign) else
            getattr(node, "targets", [])
        )
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "frozenset"
            ):
                return frozenset(ast.literal_eval(value.args[0]))
            return ast.literal_eval(value)
    raise AssertionError(f"no `{name} = ...` assignment found in {RULES}")


def test_the_published_human_approvers_are_the_policys_own():
    policy = _assigned_literal("HUMAN_APPROVERS")
    assert policy, "pds/rules.py names no human approver at all"
    assert PUBLISHED_HUMAN_APPROVERS == policy, (
        f"eval-scorer publishes {sorted(PUBLISHED_HUMAN_APPROVERS)}; the "
        f"policy enforces {sorted(policy)}"
    )


def test_the_published_human_approval_rule_is_the_policys_own():
    assert PUBLISHED_HUMAN_APPROVAL_RULE == _assigned_literal(
        "HUMAN_APPROVAL_RULE"
    )


def test_the_published_allowances_are_the_policys_own_execute_limits():
    policy = _execute_limits_from_rules()
    assert PUBLISHED_EXECUTE_LIMITS == policy, (
        "scorer/authority.py's PUBLISHED_EXECUTE_LIMITS has drifted from "
        "platform/policy-decision-service/pds/rules.py's EXECUTE_LIMITS.\n"
        f"  eval-scorer publishes: {sorted(PUBLISHED_EXECUTE_LIMITS.items())}\n"
        f"  the policy enforces:   {sorted(policy.items())}\n"
        "If the policy genuinely changed, update this service's copy in the "
        "same commit and say so in the audit trail. If it did not, the "
        "policy has been widened and nothing published the change -- which "
        "is the finding `score_identity_had_authority` exists to surface, "
        "and it should not first appear as a red unit test."
    )


def test_the_guard_reads_a_table_with_something_in_it():
    """A guard that compares two empty dicts passes forever. `rules.py` holds
    three rows; fewer than two would mean the parse silently returned
    nothing useful."""
    policy = _execute_limits_from_rules()
    assert len(policy) >= 2, policy
    assert all(len(key) == 2 for key in policy), (
        "EXECUTE_LIMITS must stay keyed by (SPIFFE ID, autonomy level) -- "
        "scorer/authority.py's lookup is keyed the same way, and a change of "
        f"key shape here silently breaks it: {sorted(policy)}"
    )


# --- The flag source joins the check ---------------------------------------
#
# `scorer/contract.py` now resolves the LEVEL `score_identity_had_authority`
# looks the allowance up under, independent of the span. That table lookup
# (`published_limit`) is only ever safe if every level `featureflag.yaml`
# could actually hand out has a row in the published table for the caller
# the flag governs -- otherwise a future variant this flag starts returning
# would make a compliant execution read as `unknown-identity`, for a reason
# with nothing to do with the identity at all. Parsed via
# `fixtures.featureflag_variants` (regex, not `ast` -- this is YAML, not
# Python, and `platform/eval-scorer/.venv` carries no `pyyaml`; see that
# helper's docstring), never retyped.


def test_every_flag_variant_has_a_row_in_both_limit_tables():
    variants = fixtures.featureflag_variants()
    assert variants, "parsed no variants at all from featureflag.yaml"

    published_levels = {level for _, level in PUBLISHED_EXECUTE_LIMITS}
    policy_levels = {level for _, level in _execute_limits_from_rules()}

    missing_from_published = variants - published_levels
    missing_from_policy = variants - policy_levels
    assert not missing_from_published, (
        f"featureflag.yaml declares variant(s) {missing_from_published} that "
        "scorer/authority.py's PUBLISHED_EXECUTE_LIMITS has no row for at "
        "any caller -- a decision resolved to one of these levels would "
        "score `unknown-identity` regardless of who executed it."
    )
    assert not missing_from_policy, (
        f"featureflag.yaml declares variant(s) {missing_from_policy} that "
        "pds/rules.py's EXECUTE_LIMITS has no row for at any caller -- the "
        "contract can hand out a level the policy itself has never priced."
    )
