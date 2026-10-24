"""Guards two ways `scripts/smoke-test.sh` could silently under-test:

1. Scoping the trace-assertion run to `tests/e2e` alone would silently drop
   the `tests/unit/` harness-correctness tests from the gate (see
   docs/telemetry-contract.md for the published per-suite counts).
2. Making the browser hop opt-in via an environment variable the gate's own
   caller controls, rather than something the gate forces unconditionally,
   would let a caller who forgets to set it get a green run that silently
   skipped it.

The gate closes both with one line:
`RUN_BROWSER_TESTS=1 "$ROOT/.venv/bin/python" -m pytest "$ROOT/tests" -v`.
This module asserts on the *substance* of that line -- not merely that the
script exists or is executable -- so that either regression fails here
rather than shipping silently.
"""
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "smoke-test.sh"


def _script_text() -> str:
    return SCRIPT_PATH.read_text()


def _effective_script() -> str:
    """The gate with the shebang and every whole-line comment removed.

    Same tool, and the same reason, as `test_stage2_linkerd_config.py` and
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
    """Gating the browser file behind an `if
    [[ "${RUN_BROWSER_TESTS:-}" == "1" ]]` check would let a caller who does
    not set the env var get a green run that skipped the browser hop
    entirely. The gate must set RUN_BROWSER_TESTS=1 itself, unconditionally,
    with no branch that lets the caller's environment decide."""
    text = _script_text()

    assert not re.search(r"if\s*\[\[[^\n]*RUN_BROWSER_TESTS", text), (
        "scripts/smoke-test.sh must never branch on RUN_BROWSER_TESTS "
        "conditionally -- that lets a caller who forgets to set the env "
        "var get a green run that silently skipped the browser hop"
    )
    assert re.search(r"^RUN_BROWSER_TESTS=1\s", text, re.MULTILINE), (
        "scripts/smoke-test.sh must set RUN_BROWSER_TESTS=1 itself, as an "
        "unconditional prefix on its own pytest invocation -- not something "
        "read from (and therefore skippable via) the caller's environment"
    )


def test_gate_pytest_target_is_the_whole_tests_tree_not_tests_e2e():
    """Targeting `tests/e2e -k "not browser"` would silently drop every test
    under tests/unit/, including the quiet-window timing-simulation tests the
    multi-service trace-completeness wait depends on. The gate must target
    the repo-root `tests` directory as a whole, with no `-k` filter that
    could exclude the browser tests even when RUN_BROWSER_TESTS=1 is set (a
    bare `-k "not browser"` filters by test *name* and deselects them
    regardless of the env var)."""
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
        f"gate's pytest target regressed to {target!r} -- a tests/e2e-only "
        "scope silently drops the tests/unit/ harness-correctness tests "
        "from the gate"
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
    """A skip must not be how the gate passes.

    `tests/e2e/test_opensearch_trace_ingest.py` is the guard that catches
    spans silently failing to reach OpenSearch. It skips on a coherently
    stage-3 cluster, correctly: OpenSearch is a stage-4 component and failing
    for its absence would cry wolf on every lower stage.

    Left unchecked, that lets the gate report exit 0 with that whole file
    skipped and every one of its assertions unexecuted --
    `test_telemetry_contract_counts.py` compares the published rows against
    `pytest --collect-only`, never against an actual run, so nothing anywhere
    reddens.

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
        "and the gate goes green having proved nothing about the trace-ingest "
        "path."
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

    # And it must be unconditional -- wrapping it in a stage/env-var branch
    # would let a caller's environment switch it off.
    preceding = script[: match.start()].rstrip().splitlines()
    assert not preceding or not preceding[-1].strip().endswith("then"), (
        "the stage-4 prerequisite check is inside a conditional. It must be "
        "unconditional for the same reason RUN_BROWSER_TESTS=1 is: a check a "
        "caller's environment can switch off is not a check."
    )


# --- Zero skips as a property of the gate ------------------------------------


def test_the_gate_fails_when_the_suite_skips_anything():
    """A skip is invisible in a green gate unless the gate itself checks for it.

    `smoke-test.sh` refuses to run below stage 4 by asking the cluster for
    `svc/opensearch-cluster-master`. The pytest run itself needs its own
    check that fails on a skip, since several modules inside it hold
    reachable `pytest.skip` calls on conditions that refusal does not cover.

    Asserted structurally rather than by running the gate, for the obvious
    reason -- the gate spends model tokens -- so this checks the three things
    that make the check real rather than decorative: the output is captured,
    pytest's own exit status survives the pipe, and a non-zero skip count
    exits non-zero.

    It also checks the *property* the skip count computes rather than a
    literal regex. Pinning the per-test verdict form
    `grep -cE "^[^ ]+::[^ ]+ SKIPPED"` has a real gap: a collection-time
    module skip prints no `path::test` line at all (the largest possible
    instance of what this check exists for), and neither does a
    parametrised id containing a space, which this tree already has
    (`test_reach_policy.py::test_ingress_reach_matrix[agentgateway->payments-mcp:8000 POST /mcp = allow]`).
    Pinning that literal string would keep this test green while the
    gate it guards silently undercounted skips. So instead this extracts
    whatever expression the script actually uses to compute `SKIPPED` and
    evaluates it against two synthetic pytest-shaped logs: one whose only
    skip is a collection-time module skip, and one whose only skip carries a
    space in its parametrised id. Both must be counted as 1.
    """
    text = _effective_script()

    assert "PIPESTATUS" in text, (
        "the gate pipes pytest into `tee` to capture skips while keeping the "
        "run visible. Under `set -e` a pipeline's status is its LAST "
        "command's -- `tee`'s, which always succeeds -- so without reading "
        "PIPESTATUS[0] a FAILING suite would sail past the check below and "
        "the gate would report success. That is a worse bug than the one this "
        "capture was added to fix."
    )
    assert re.search(r"SKIPPED\b", text), (
        "nothing in the gate looks for skipped tests"
    )
    assert re.search(r'\$SKIPPED"?\]?\]?\s*-ne\s*0|"\$SKIPPED"\s*-ne\s*0', text), (
        "the gate must exit non-zero when the skip count is not zero"
    )

    raw_text = _script_text()
    match = re.search(r'SKIPPED=\$\(([^\n]+)\)\n', raw_text)
    assert match, (
        "expected a `SKIPPED=$(...)` assignment in scripts/smoke-test.sh "
        "computing the skip count"
    )
    pipeline = match.group(1)

    module_skip_log = (
        "collected 5 items\n"
        "tests/unit/test_thing.py::test_a PASSED\n"
        "tests/unit/test_thing.py::test_b PASSED\n"
        "SKIPPED [1] tests/unit/test_module_skip.py:3: module needs a live cluster\n"
        "========== 4 passed, 1 skipped in 0.42s ==========\n"
    )
    space_id_log = (
        "collected 5 items\n"
        "tests/unit/test_a.py::test_a PASSED\n"
        "tests/unit/test_b.py::test_ingress_reach_matrix[agentgateway->payments-mcp:8000 POST /mcp = allow] SKIPPED\n"
        "========== 4 passed, 1 skipped in 0.42s ==========\n"
    )

    for name, log_text in (
        ("collection-time module skip", module_skip_log),
        ("space-containing parametrised id", space_id_log),
    ):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write(log_text)
            log_path = fh.name
        try:
            result = subprocess.run(
                ["bash", "-c", f'SKIP_LOG={shlex.quote(log_path)}; SKIPPED=$({pipeline}); echo "$SKIPPED"'],
                capture_output=True, text=True, timeout=10,
            )
        finally:
            Path(log_path).unlink()
        assert result.returncode == 0, (
            f"the extracted SKIPPED pipeline failed on the {name} case:\n"
            f"{result.stderr}"
        )
        reported = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        assert reported == "1", (
            f"the {name} case has 1 skip in pytest's own summary line, but "
            f"the gate's SKIPPED pipeline reported {reported!r} for it:\n"
            f"{log_text}"
        )


def test_the_skip_check_cannot_be_satisfied_by_a_run_that_never_happened():
    """The vacuity question, asked of the check itself.

    A skip count read from a file pytest never wrote is zero, and zero passes.
    So the capture file has to be the same one the run writes, and the run has
    to have succeeded before the count is believed -- both are asserted here
    because "0 skips" from an empty log is exactly the false GREEN this repo
    keeps finding.
    """
    text = _effective_script()
    assert 'tee "$SKIP_LOG"' in text, (
        "the gate must tee pytest's output into the file the skip count is "
        "read from; a count read from a file nothing wrote is always zero"
    )
    run_at = text.index('tee "$SKIP_LOG"')
    status_at = text.index("PYTEST_STATUS")
    count_at = text.index("SKIPPED=$(")
    assert run_at < status_at < count_at, (
        "the order must be: run and capture, then check pytest's exit status, "
        "then count skips. Counting skips before failing on a non-zero exit "
        "would report 'passed with 0 skips' for a suite that crashed at "
        f"collection. Offsets: run={run_at}, status={status_at}, count={count_at}"
    )
