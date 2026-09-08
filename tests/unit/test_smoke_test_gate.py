"""A19 guard: pins the two real plan defects found and fixed in Task 8's
`scripts/smoke-test.sh` against exactly the brief's superseded version being
restored.

The brief's own sketch was:

    python -m pytest tests/e2e -v -k "not browser"

    if [[ "${RUN_BROWSER_TESTS:-}" == "1" ]]; then
      python -m pytest tests/e2e/test_browser_root_span.py -v
    else
      echo "Skipping browser tests. Set RUN_BROWSER_TESTS=1 to include them."
    fi

That version has two defects a review found real and measured:

1. It scopes the trace-assertion run to `tests/e2e`, silently dropping the 12
   `tests/unit/` harness-correctness tests (measured: 35 passed instead of the
   documented 53 -- see docs/telemetry-contract.md and task-8-report.md).
2. It makes the browser hop -- the milestone's own headline claim -- opt-in
   via an environment variable the gate's own caller controls, rather than
   something the gate forces unconditionally (A22).

Task 8's `smoke-test.sh` fixes both with one line:
`RUN_BROWSER_TESTS=1 "$ROOT/.venv/bin/python" -m pytest "$ROOT/tests" -v`.
This module asserts on the *substance* of that line -- not merely that the
script exists or is executable -- so that reverting it back to the brief's
shape (something a well-meaning maintainer reconciling script to prose might
do, since a stale contract paragraph used to argue for exactly that -- see
the B1 finding in task-8-review.md) fails here rather than shipping silently.
"""
import re
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "smoke-test.sh"


def _script_text() -> str:
    return SCRIPT_PATH.read_text()


def test_gate_forces_run_browser_tests_unconditionally_not_via_a_conditional():
    """The brief's version gated the browser file behind an `if
    [[ "${RUN_BROWSER_TESTS:-}" == "1" ]]` check -- i.e. a caller who does not
    set the env var gets a green run that skipped the browser hop entirely.
    The committed gate must set RUN_BROWSER_TESTS=1 itself, unconditionally,
    with no branch that lets the caller's environment decide."""
    text = _script_text()

    assert not re.search(r"if\s*\[\[[^\n]*RUN_BROWSER_TESTS", text), (
        "scripts/smoke-test.sh must never branch on RUN_BROWSER_TESTS "
        "conditionally -- that is exactly the brief's superseded shape, "
        "which lets a caller who forgets to set the env var get a green "
        "run that silently skipped the browser hop (A22)"
    )
    assert re.search(r"^RUN_BROWSER_TESTS=1\s", text, re.MULTILINE), (
        "scripts/smoke-test.sh must set RUN_BROWSER_TESTS=1 itself, as an "
        "unconditional prefix on its own pytest invocation -- not something "
        "read from (and therefore skippable via) the caller's environment"
    )


def test_gate_pytest_target_is_the_whole_tests_tree_not_tests_e2e():
    """The brief's version targeted `tests/e2e -k "not browser"`, which
    silently drops every test under tests/unit/ (12 tests, including the
    A20a quiet-window timing-simulation tests the multi-service
    trace-completeness wait depends on). The committed gate must target the
    repo-root `tests` directory as a whole, with no `-k` filter that could
    exclude the browser tests even when RUN_BROWSER_TESTS=1 is set (a bare
    `-k "not browser"` filters by test *name* and deselects them regardless
    of the env var -- measured directly, see task-8-report.md)."""
    text = _script_text()

    match = re.search(
        r'RUN_BROWSER_TESTS=1\s+"[^"]*"\s+-m\s+pytest\s+"([^"]+)"([^\n]*)',
        text,
    )
    assert match, (
        "expected to find a single line of the shape "
        '`RUN_BROWSER_TESTS=1 "<python>" -m pytest "<target>" ...` in '
        "scripts/smoke-test.sh"
    )
    target, rest_of_line = match.group(1), match.group(2)

    normalized = target.rstrip("/")
    assert not normalized.endswith("tests/e2e"), (
        f"gate's pytest target regressed to {target!r} -- this is the "
        "brief's superseded tests/e2e-only scope, which silently drops "
        "tests/unit/ (12 harness-correctness tests) from the gate"
    )
    assert normalized.endswith("tests"), (
        f"gate's pytest target is {target!r}; expected the whole "
        "$ROOT/tests tree"
    )
    assert "-k" not in rest_of_line, (
        "the gate's pytest invocation must not carry a -k filter -- a bare "
        '-k "not browser" deselects the browser tests by name even when '
        "RUN_BROWSER_TESTS=1 is set, silently reintroducing the exact gap "
        "this gate exists to close"
    )
