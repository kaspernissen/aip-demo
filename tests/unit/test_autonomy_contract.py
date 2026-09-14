"""M5 Task 2: the contract resource, guarded hermetically -- no cluster needed.

`deploy/platform/openfeature/featureflag.yaml` is what Task 3's Policy
Decision Service will resolve the `autonomy-level` flag against, through the
flagd sidecar the Open Feature Operator injects. These guards parse the YAML
directly; they say nothing about whether OFO, cert-manager or the sidecar are
actually running, which is what Step 6's cluster verification (this task's
report) covers instead.

Two properties, both about what happens when this resource is wrong rather
than right -- the interesting case for a contract that gates money:

1. A resource that fails to parse, or that truncates before its targeting
   rule, must deny rather than permit. `defaultVariant` is the fail-closed
   position (`pds/rules.py`'s `EXECUTE_LIMITS` has no permissive fallback of
   its own), so it must name the restrictive level.
2. Every variant this resource can hand back must have a row in the policy
   table `pds/rules.py` actually enforces -- a variant the table does not
   know denies through `unknown_caller`, silently, rather than through a
   decision anyone reading the audit trail would recognise as a deliberate
   refusal.
"""
from __future__ import annotations

import ast
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
FEATUREFLAG = REPO_ROOT / "deploy" / "platform" / "openfeature" / "featureflag.yaml"
RULES = (
    REPO_ROOT / "platform" / "policy-decision-service" / "pds" / "rules.py"
)


def _load_featureflag() -> dict:
    assert FEATUREFLAG.exists(), (
        f"{FEATUREFLAG} does not exist -- M5 Task 2 creates it; this guard "
        "has nothing to check without it"
    )
    return yaml.safe_load(FEATUREFLAG.read_text())


def _parse_execute_limits() -> list[tuple[str, str]]:
    """`EXECUTE_LIMITS`'s keys as `pds/rules.py` literally declares them.

    Parsed with `ast`, never imported -- the same technique
    `platform/eval-scorer/tests/test_authority_matches_the_policy.py` uses on
    the same file, and for the same reason: `ast.parse` discards comments, so
    a comment quoting a stale row cannot satisfy this, and this suite gains no
    import-time dependency on the PDS's own package.
    """
    assert RULES.exists(), (
        f"{RULES} does not exist. This guard compares the contract's "
        "variants against the policy's own rows; if the policy moved, point "
        "this at where it went rather than deleting the check."
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
        return [tuple(ast.literal_eval(key)) for key in value.keys]
    raise AssertionError(f"no `EXECUTE_LIMITS = {{...}}` assignment found in {RULES}")


def test_the_contract_defaults_to_the_restrictive_variant():
    """A truncated or mis-applied contract must deny, not permit."""
    spec = _load_featureflag()["spec"]["flagSpec"]["flags"]["autonomy-level"]
    assert spec["defaultVariant"] == "assisted", (
        "defaultVariant is the fail-closed position; an 'autonomous' default "
        "means a resource that fails to parse its targeting permits everything"
    )


def test_every_variant_has_a_row_in_the_policy_table():
    """A variant the rule table does not know denies via unknown_caller -- but
    silently. Assert the two stay in step."""
    variants = set(_load_featureflag()["spec"]["flagSpec"]["flags"]
                   ["autonomy-level"]["variants"])
    levels = {level for _, level in _parse_execute_limits()}
    assert variants <= levels, f"variants with no policy row: {variants - levels}"
