"""A15a guard: pins `scripts/rebuild-and-deploy.sh`'s missing-build-context
handling against A15's retired skip branch being restored.

A15 (superseded by amendment A15a) let a default no-argument run silently
skip a component whose build context
directory did not exist yet, printing a yellow warning and continuing. That
was correct while most of M1.1's five build contexts genuinely did not exist
(before Task 6 landed) and a bare `./scripts/rebuild-and-deploy.sh` still had
to be usable. The final whole-branch review found that condition is gone —
all five contexts have existed since Task 6 — so the skip branch is
unreachable in a healthy tree today, and the day M1.2/M1.3 adds a sixth
component to COMPONENTS, a mistyped or half-created build context on a bare
run would be silently skipped rather than failing.

A15a's ruling: a missing build context is a hard error in every case,
default run or an explicitly named component alike. This module asserts on
the *substance* of the script's current text -- not merely that it exists --
so that reintroducing the skip branch (something a maintainer reconciling
the script against A15's original, still-present prose might reasonably do)
fails here rather than shipping silently.
"""
import re
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "rebuild-and-deploy.sh"
)


def _script_text() -> str:
    return SCRIPT_PATH.read_text()


def test_missing_build_context_has_no_skip_branch():
    """A15's retired shape printed a yellow 'Skipping <component>' line and
    `continue`d past a missing build context whenever the run had no
    explicitly named components. That branch, and the `skipped` bookkeeping
    array it fed, must not exist: every path through a missing build context
    must exit non-zero."""
    text = _script_text()

    assert "Skipping" not in text, (
        "scripts/rebuild-and-deploy.sh must not print a 'Skipping ...' line "
        "for a missing build context -- that is exactly A15's retired shape, "
        "which silently swallows a missing/mistyped build context on a "
        "default (no-argument) run (A15a)"
    )
    assert "skipped" not in text.lower(), (
        "scripts/rebuild-and-deploy.sh must not track a list of skipped "
        "components -- A15a retired the concept entirely: a missing build "
        "context is always a hard error, never something to skip and report"
    )


def test_missing_build_context_is_a_hard_error_unconditionally():
    """A15a: 'a missing build context is a hard error in every case, default
    run or explicitly named component'. Pin that the check has no
    conditional (like the retired `if [[ "$explicit" == true ]]`) gating
    whether it exits -- it must exit unconditionally on the very first
    branch that tests for the directory's absence."""
    text = _script_text()

    match = re.search(
        r'if \[\[ ! -d "\$ROOT/\$context" \]\]; then\n(.*?)\nfi',
        text,
        re.DOTALL,
    )
    assert match, (
        'expected scripts/rebuild-and-deploy.sh to contain a single '
        '`if [[ ! -d "$ROOT/$context" ]]; then ... fi` block guarding a '
        "missing build context"
    )
    body = match.group(1)
    assert "explicit" not in body, (
        "the missing-build-context check must not branch on whether the "
        "component was named explicitly -- A15a retired that distinction; "
        "every missing build context is a hard error unconditionally"
    )
    assert re.search(r"exit 1\b", body), (
        "the missing-build-context branch must exit 1 unconditionally; "
        f"found body: {body!r}"
    )
