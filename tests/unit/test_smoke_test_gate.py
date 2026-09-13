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


def _effective_script() -> str:
    """The gate with the shebang and every whole-line comment removed.

    Same tool, and the same reason, as `test_stage2_cilium_config.py` and
    `test_stage4_config.py`: a comment must not be able to satisfy an
    assertion. The stage-4 check below is described at length in a comment
    block that names the Service, so without this the assertion would pass
    against a script that had only the explanation and not the check.
    """
    return "\n".join(
        line
        for line in _script_text().splitlines()
        if not line.lstrip().startswith("#")
    )


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


def test_gate_refuses_to_run_below_stage_4():
    """Ruling M4-R9 (re-review finding N3): a skip must not be how the gate
    passes.

    `tests/e2e/test_opensearch_trace_ingest.py` is M4 Task 1's deliverable --
    the guard that catches spans silently failing to reach OpenSearch, which
    is the defect that shipped at `955dd18` past 119 green unit tests. It
    skips on a coherently stage-3 cluster, correctly: OpenSearch is a stage-4
    component and failing for its absence would cry wolf on every lower stage.

    That leaves the gate able to report exit 0 with that whole file skipped
    and every one of its assertions unexecuted. `test_telemetry_contract_counts.py`
    compares the published rows against `pytest --collect-only`, never against
    an actual run, so nothing anywhere reddens. This is the same shape as the
    defect class A22 exists for -- the gate passing because something did not
    run -- one milestone later, which is why it belongs in this file rather
    than in the M4 one.

    So the gate asks the cluster whether OpenSearch is there, and refuses if
    it is not. Asserted against the comment-stripped script: the check must be
    a real command, not the paragraph above it explaining one.
    """
    script = _effective_script()

    match = re.search(
        r"^kubectl [^\n]*get svc/opensearch-cluster-master[^\n]*\|\| \{",
        script,
        re.MULTILINE,
    )
    assert match, (
        "scripts/smoke-test.sh no longer refuses to run below stage 4. Its "
        "prerequisite block must check that svc/opensearch-cluster-master "
        "exists, unconditionally and at top level, before it runs the suite "
        "-- otherwise every assertion in "
        "tests/e2e/test_opensearch_trace_ingest.py skips on a stage-3 cluster "
        "and the gate goes green having proved nothing about M4's "
        "trace-ingest path (ruling M4-R9)."
    )
    check_line = match.group(0)
    assert "-n observability" in check_line, (
        "the stage-4 prerequisite check does not name the observability "
        f"namespace, so it cannot find the Service it looks for: {check_line}"
    )

    # It must be a *prerequisite*, i.e. ahead of the run it protects. A check
    # placed after the suite would let the gate spend five minutes producing
    # the very skips it exists to prevent before saying so.
    pytest_run = re.search(r"^RUN_BROWSER_TESTS=1\s", script, re.MULTILINE)
    assert pytest_run, "the gate's own pytest invocation is missing"
    assert match.start() < pytest_run.start(), (
        "the stage-4 prerequisite check sits AFTER the gate's pytest run, so "
        "the run it protects has already happened (and already skipped) by "
        "the time it fires"
    )

    # And it must be unconditional -- wrapping it in a stage/env-var branch is
    # how A22's browser gap was originally introduced.
    preceding = script[: match.start()].rstrip().splitlines()
    assert not preceding or not preceding[-1].strip().endswith("then"), (
        "the stage-4 prerequisite check is inside a conditional. It must be "
        "unconditional for the same reason RUN_BROWSER_TESTS=1 is: a check a "
        "caller's environment can switch off is not a check."
    )


def test_the_gate_runs_every_per_service_suite_the_contract_publishes():
    """A suite nothing runs, caught here instead of a milestone later.

    This repo has shipped that defect three times.
    `platform/policy-decision-service` went a milestone with 105 tests and no
    runner (M2.2 finding 1, ruling R42); `apps/reviewer-ui` went a milestone
    with 50 and no runner (M3 Task 5B), and its own note in
    `tests/unit/test_telemetry_contract_counts.py` says in as many words that
    "adding a service to `deploy/k8s/` means adding it here, in the same
    task". Both times the *count* guard was satisfied -- it asserts the
    published figure matches what the suite collects, which stays true whether
    or not anything ever runs it -- and both times the gap was found by a human
    re-reading the script.

    M4 Task 4 added an eighth service and mutation-tested its own work:
    deleting `(cd "$ROOT/platform/eval-scorer" && ... pytest tests -q)` from
    the gate left `tests/unit` completely green. That is the third recurrence
    of this class and the first one caught by mutating rather than by reading,
    so the check goes in here rather than being reported as a residual.

    Derived from `_PER_SERVICE_SUITES`, not restated: that list is what the
    contract's per-service row is measured against, so the two cannot describe
    different sets of services. Asserted against the comment-stripped script,
    because this file's whole subject is checks that a comment could satisfy.

    **Three places, not two (fix round 1).** The first version derived
    `_PER_SERVICE_SUITES` -> `smoke-test.sh` and stopped there, and a review
    found what that leaves open *in the very commit that wrote it*:
    `platform/eval-scorer` went into the gate's **prerequisite** loop -- which
    `exit 1`s on a missing `.venv` -- and not into the README block an operator
    copies to create those venvs, so a fresh clone could no longer run the
    gate. Fourth instance of the same defect. The set now has to agree across
    all three: the published row, the gate (venv loop and suite invocation),
    and README's setup loop.

    The loops are matched by *directory*, not by the service's published name:
    two services could share a display name, and it is the directory that
    decides which venv is created and which tests run.
    """
    from test_telemetry_contract_counts import _PER_SERVICE_SUITES

    script = _effective_script()
    repo_root = SCRIPT_PATH.resolve().parents[1]
    readme = (repo_root / "README.md").read_text()

    # The `for svc in … ; do` loop in README's setup block, anchored on the
    # loop itself so prose that merely mentions a service path cannot satisfy
    # this.
    readme_loop = re.search(r"^for svc in ([^\n;]+); do$", readme, re.MULTILINE)
    assert readme_loop, (
        "README.md no longer contains a `for svc in <paths>; do` setup loop. "
        "That block is what an operator copies to create the per-service venvs "
        "scripts/smoke-test.sh then requires; if it moved, point this guard at "
        "where it went rather than deleting the check."
    )
    readme_services = set(readme_loop.group(1).split())

    # The gate's own prerequisite loop, read the same way. Compared as SETS
    # rather than per known service: a first version walked `_PER_SERVICE_SUITES`
    # and asked of each whether it was in both loops, which could not see a
    # path present in one loop and in no suite list at all -- adding
    # `tools/nonesuch` to the README left it green, found by mutating this
    # guard. An operator following the README would have built a venv for a
    # directory that does not exist.
    gate_loop = re.search(r"^for svc in ([^\n;]+); do$", script, re.MULTILINE)
    assert gate_loop, (
        "scripts/smoke-test.sh no longer has a `for svc in <paths>; do` "
        "prerequisite loop, which is what refuses to run the gate without "
        "each per-service venv"
    )
    gate_services = set(gate_loop.group(1).split())

    not_in_readme = sorted(gate_services - readme_services)
    not_required = sorted(readme_services - gate_services)

    not_run = []
    for name, service_dir, _counter in _PER_SERVICE_SUITES:
        relative = service_dir.relative_to(repo_root).as_posix()
        # The gate runs each suite as `(cd "$ROOT/<dir>" && <runner> ...)`.
        if not re.search(rf'cd "\$ROOT/{re.escape(relative)}"\s*&&', script):
            not_run.append(f"{name} ({relative})")

    # Both loops name real directories. Only the Python services appear in
    # them -- payments-api (Go) and customer-ui (Node) have no `.venv` and are
    # correctly in neither -- so this is what catches a typo'd or deleted path
    # that set comparison alone would call consistent.
    unreal = sorted(
        path for path in gate_services | readme_services
        if not (repo_root / path).is_dir()
    )
    assert not unreal, (
        f"a venv setup loop names {unreal}, which is not a directory in this "
        "repo. The gate would `exit 1` on it and an operator following the "
        "README would be told to create a venv somewhere that does not exist."
    )

    assert not not_run, (
        "scripts/smoke-test.sh does not run the per-service suite(s) for: "
        + ", ".join(not_run)
        + ". docs/telemetry-contract.md publishes a test count for each of "
        "them and tests/unit/test_telemetry_contract_counts.py re-measures "
        "it, so the count is gating while the assertions are not -- which is "
        "the exact state policy-decision-service and reviewer-ui were each "
        "found in, one milestone after they shipped."
    )
    assert not not_in_readme, (
        "scripts/smoke-test.sh's prerequisite loop requires a .venv for "
        + ", ".join(not_in_readme)
        + " and README.md's setup loop does not create one. The gate `exit 1`s "
        "on that missing venv, so a fresh clone following the README cannot "
        "run the gate at all. Add the path to README.md's `for svc in …` block."
    )
    assert not not_required, (
        "README.md's setup loop creates a .venv for "
        + ", ".join(not_required)
        + " that scripts/smoke-test.sh's prerequisite loop does not require. "
        "Either the gate stopped needing it -- in which case stop telling a "
        "new operator to build it -- or the gate lost a prerequisite."
    )
