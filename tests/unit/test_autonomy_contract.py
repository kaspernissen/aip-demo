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

M5 Task 4 adds a third property, unrelated to the contract resource: that
`pds/autonomy.py` never emits a deprecated `feature_flag.*` attribute
spelling. `DEPRECATED_SPELLINGS` below is DERIVED from the installed
`opentelemetry-semantic-conventions` package (0.65b0, measured), not
hand-copied -- the task brief's correction 4, because a hand-copied list is
exactly what M4 and M5 keep finding stale. See that constant's own docstring
for where each of the four spellings comes from and why two different
derivation techniques were needed to get all four honestly.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import yaml
from opentelemetry.semconv._incubating.attributes import feature_flag_attributes as _ffa
from opentelemetry.semconv.trace import SpanAttributes as _DeprecatedSpanAttributes

REPO_ROOT = Path(__file__).resolve().parents[2]
FEATUREFLAG = REPO_ROOT / "deploy" / "platform" / "openfeature" / "featureflag.yaml"
RULES = (
    REPO_ROOT / "platform" / "policy-decision-service" / "pds" / "rules.py"
)
AUTONOMY = (
    REPO_ROOT / "platform" / "policy-decision-service" / "pds" / "autonomy.py"
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


# --- M5 Task 4: the deprecated-spelling guard, derived not retyped ---------


def _current_feature_flag_spellings() -> frozenset[str]:
    """The NON-deprecated `feature_flag.*` attribute values
    `feature_flag_attributes` (0.65b0, incubating) declares today.

    Module-level attribute docstrings ("Deprecated: Replaced by ...") are
    PEP-258 source convention, not runtime state -- Python attaches no
    `__doc__` to the name above a bare string literal, so there is nothing
    for `getattr`/`inspect.getdoc` to read. The only way to see them at all
    is to read the module's own source and match each `NAME: Final = "..."`
    assignment against the string statement immediately below it, which is
    what this does with `ast` rather than a hand-copied list. (Measured: the
    one real `@deprecated` decorator in this module, on
    `FeatureFlagEvaluationReasonValues`, wraps an Enum class, not a
    `feature_flag.*` attribute string -- it plays no part in either
    function here.)
    """
    tree = ast.parse(inspect.getsource(_ffa))
    body = tree.body
    current: set[str] = set()
    for i, node in enumerate(body):
        is_flag_const = (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        if not is_flag_const:
            continue
        docstring = None
        if i + 1 < len(body):
            nxt = body[i + 1]
            if (isinstance(nxt, ast.Expr) and isinstance(nxt.value, ast.Constant)
                    and isinstance(nxt.value.value, str)):
                docstring = nxt.value.value
        if docstring is None or not docstring.strip().startswith("Deprecated"):
            current.add(node.value.value)
    return frozenset(current)


def _deprecated_feature_flag_spellings() -> frozenset[str]:
    """Every `feature_flag.*` spelling this installed SDK marks deprecated,
    from the TWO machine-readable sources that between them cover all four
    the plan's original (retyped) list named -- measured, not assumed, that
    both were necessary:

    1. `feature_flag_attributes` itself (see `_current_feature_flag_spellings`
       for why this needs `ast` rather than `__doc__`): three of the four --
       `feature_flag.variant`, `feature_flag.evaluation.reason`,
       `feature_flag.evaluation.error.message`.
    2. `opentelemetry.semconv.trace.SpanAttributes`, the pre-1.25 module the
       package deprecates at the CLASS level -- genuinely machine-readable
       at runtime via the `__deprecated__` attribute PEP 702 gives it
       (`typing_extensions.deprecated` sets this; asserted below, not
       assumed). Its members are candidates for the fourth,
       `feature_flag.provider_name` (the pre-registry, underscore spelling --
       distinct from the current `feature_flag.provider.name`), filtered
       against source (1)'s CURRENT set so a name merely RELOCATED rather
       than RENAMED (`FEATURE_FLAG_KEY`, still `feature_flag.key` in both
       modules) is not reported as deprecated.

    Confirmed by direct measurement, not by reading prose: this class also
    defines `FEATURE_FLAG_VARIANT = "feature_flag.variant"`, the same
    spelling source (1) already names -- included via the set union below,
    which is idempotent, rather than treated as a contradiction.
    """
    assert hasattr(_DeprecatedSpanAttributes, "__deprecated__"), (
        "opentelemetry.semconv.trace.SpanAttributes no longer carries a "
        "`__deprecated__` marker -- the whole-class deprecation this guard "
        "reads may have been removed or reshaped upstream. Re-derive rather "
        "than assume the fallback below still applies; if the marker is "
        "genuinely gone, retyping `feature_flag.provider_name` back in as a "
        "literal is acceptable, with a comment saying this assertion is what "
        "told you to."
    )
    current = _current_feature_flag_spellings()
    from_trace_module = {
        value for name, value in vars(_DeprecatedSpanAttributes).items()
        if name.startswith("FEATURE_FLAG") and isinstance(value, str)
        and value not in current
    }
    return frozenset(_current_and_deprecated_from_incubating() | from_trace_module)


def _current_and_deprecated_from_incubating() -> frozenset[str]:
    """The deprecated half of `feature_flag_attributes`, by the same `ast`
    walk `_current_feature_flag_spellings` does, keeping the docstring test
    inverted rather than duplicating the walk with copy-pasted drift risk."""
    tree = ast.parse(inspect.getsource(_ffa))
    body = tree.body
    deprecated: set[str] = set()
    for i, node in enumerate(body):
        is_flag_const = (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        if not is_flag_const:
            continue
        if i + 1 < len(body):
            nxt = body[i + 1]
            if (isinstance(nxt, ast.Expr) and isinstance(nxt.value, ast.Constant)
                    and isinstance(nxt.value.value, str)
                    and nxt.value.value.strip().startswith("Deprecated")):
                deprecated.add(node.value.value)
    return frozenset(deprecated)


DEPRECATED_SPELLINGS = _deprecated_feature_flag_spellings()


def _string_literals(path: Path) -> set[str]:
    """Every string literal `path` contains, parsed with `ast` -- the same
    "read it, do not import it" discipline `_parse_execute_limits` above
    uses, and for the same reason: this suite gains no dependency on the
    PDS's own package or its venv. A docstring mentioning a deprecated
    spelling in prose (this file's own module docstring above does, twice,
    inside backticks) is a distinct, much longer string value and cannot
    collide with an exact-match spelling by accident."""
    tree = ast.parse(path.read_text())
    return {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_no_deprecated_feature_flag_spelling_is_emitted():
    """`pds/autonomy.py` emits the `feature_flag.evaluation` log record
    (M5 Task 4). Four spellings were renamed by the OTel feature-flag
    semantic conventions and are what most published examples still use
    (Finding 13/14 material) -- `feature_flag.variant`,
    `feature_flag.evaluation.reason`, `feature_flag.evaluation.error.message`,
    `feature_flag.provider_name`. Semconv 0.65b0, measured.
    """
    assert AUTONOMY.exists(), f"{AUTONOMY} does not exist"
    emitted = _string_literals(AUTONOMY)
    hit = emitted & DEPRECATED_SPELLINGS
    assert not hit, f"deprecated spellings emitted: {sorted(hit)}"


def test_the_derived_deprecated_spellings_match_what_the_plan_named():
    """A cross-check, not a second source of truth: the plan's Step 3 sketch
    named four spellings by hand before this guard existed to derive them.
    If the installed semconv package ever adds a fifth deprecated
    `feature_flag.*` name, or drops one of these four, this reddens with the
    actual derived set in the message -- which is the point of deriving
    rather than retyping in the first place, made visible instead of silent."""
    assert DEPRECATED_SPELLINGS == frozenset({
        "feature_flag.provider_name",
        "feature_flag.variant",
        "feature_flag.evaluation.reason",
        "feature_flag.evaluation.error.message",
    }), sorted(DEPRECATED_SPELLINGS)
