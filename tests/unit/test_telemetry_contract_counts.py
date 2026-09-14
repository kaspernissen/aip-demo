"""Guards `docs/telemetry-contract.md`'s published test counts against the
exact staleness the second final-review round found (finding 3): the
document still said `49 passed, 6 skipped, 1 xfailed` / `55 passed, 1
xfailed` after a commit had already taken the real numbers to `55/7/1` and
`62/1` — the commit message and the task report were both updated, but the
one artefact whose entire purpose is stating what is true was not.

**What the whole-tree check can and cannot verify, and why it stops there:**
the contract publishes two numbers for the *same* fixed set of collected
tests — what a bare `pytest tests/` run proves (passed + skipped + xfailed)
and what the gate's `RUN_BROWSER_TESTS=1` run proves (passed + xfailed, no
skips, since nothing is gated out). Both totals must equal each other and
must equal how many tests `pytest` actually collects under `tests/` today.
`--collect-only` needs no live cluster, no `.env`, and no port-forwards --
it only imports test modules and enumerates test IDs -- so this can run
anywhere the rest of `tests/unit/` runs. What it deliberately does NOT do is
re-run the full suite to confirm every documented "passed" is still true:
that would require the live cluster and the browser, making this "unit"
test as heavyweight and environment-coupled as the e2e suite it would be
checking, for a check `smoke-test.sh` already performs directly every time
it runs. A collection-count cross-check catches the specific, cheap,
frequent failure mode (someone adds or removes a test and forgets the two
numbers in the doc) without trying to be a second copy of the gate.

**Why a second, per-service check exists (M1.2 Task 6).** The contract also
publishes one more row -- "Per-service unit suites" -- and the whole-tree
check above structurally cannot see it: each per-service suite lives
*outside* `tests/`, in its own directory with its own `.venv` (or its own
Go module, or its own Node package), so `pytest --collect-only tests/`
never touches it. This is not a hypothetical blind spot: the
`settlement-agent` count in that row went stale at "19" for the entirety of
M1.2 Tasks 1-4 -- three tasks in a row that each added tests to that
suite -- and nothing caught it until a human re-counted by hand in Task 5.
That is the third time a published count in this document has drifted
silently; the first two both went through the whole-tree check above, which
is exactly why it exists, and this one didn't, which is exactly why it kept
recurring. `test_per_service_counts_match_what_each_suites_own_runner_collects`
below closes that blind spot the same way the whole-tree check closes its
own: by asking each service's own test runner what it actually has, rather
than trusting a number someone typed by hand.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "docs" / "telemetry-contract.md"

# Module-level (M5 Task 1, V2) so it can be asserted on directly, not only
# through the one property test that reads it. See the comment beside its use
# in test_a_predicted_gate_row_is_labelled_as_one for why that test alone is
# not enough.
GATE_PATHS = ["agents", "apps", "contracts", "deploy", "platform", "scripts", "tests", "systems", "tools"]


def _collected_test_count() -> int:
    # A5: the bare `python3` on PATH is the too-old system 3.10.4, which
    # lacks every package this suite installs into `.venv` (amendment A5
    # forbids relying on it) -- re-exec with the *running* interpreter
    # instead, so this always collects with the same environment pytest
    # itself was just launched under.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "`pytest --collect-only -q tests/` failed, so this test cannot "
        f"verify the contract's published counts:\nstdout:\n{result.stdout}"
        f"\nstderr:\n{result.stderr}"
    )
    match = re.search(r"(\d+) tests? collected", result.stdout)
    assert match, (
        "could not parse a '<N> tests collected' summary line out of "
        f"pytest's --collect-only output:\n{result.stdout}"
    )
    return int(match.group(1))


_BLOCK_HEADING_RE = re.compile(r"^\*\*(20\d\d-\d\d-\d\d) ", re.M)


def _current_figures_block() -> str:
    """The contract's CURRENT measured-counts block, sliced out by its heading.

    M4 final review, suspected finding S1. Every check below used to take the
    *first* match in the whole document and rely on a sentence asking authors
    to keep the current block first. That is the document's own stated
    convention and it has held -- but "the guard reads the right paragraph as
    long as nobody reorders the file" is a guard on prose order, not on
    figures, and this file's entire existence is the argument against that. A
    block moved, or a new milestone's block appended at the bottom out of
    habit, and these tests would silently start checking a historical figure
    nobody had touched, and report green.

    So the slice is explicit: from the first `**<date> ` heading to the next
    one. It also makes the historical blocks *unreachable* to these checks,
    which is what lets the M4 Task 5 block keep its own wrong bare row on the
    record with a correction beside it, rather than be rewritten to suit a
    test.
    """
    text = CONTRACT_PATH.read_text()
    headings = list(_BLOCK_HEADING_RE.finditer(text))
    assert len(headings) >= 2, (
        "expected at least two '**<YYYY-MM-DD> ...' measured-counts block "
        f"headings in {CONTRACT_PATH.relative_to(REPO_ROOT)}; found "
        f"{len(headings)}. With no following heading the 'current block' would "
        "silently become the whole rest of the document, which is the failure "
        "this slice exists to prevent."
    )
    return text[headings[0].start():headings[1].start()]


def test_contracts_published_counts_match_what_pytest_actually_collects():
    collected = _collected_test_count()
    text = _current_figures_block()

    # `\s+` rather than a literal space between the words: the contract is
    # hard-wrapped prose, and a figure that happens to wrap *inside* its
    # backticks is invisible to a space-only pattern -- so this check falls
    # through to the next, historical figure and verifies that one instead,
    # reporting drift against a number the author never touched. That is the
    # same failure mode the module docstring describes for a historical figure
    # moved above a current one, and it happened for real while M4 Task 3's fix
    # round was updating the current row. Every historical figure sits *below*
    # the current one, so tolerating a wrap cannot change which match is first.
    bare_match = re.search(
        r"`(\d+)\s+passed,\s+(\d+)\s+skipped,\s+(\d+)\s+xfailed`", text
    )
    assert bare_match, (
        "expected to find the bare-suite result as "
        "'`<N> passed, <N> skipped, <N> xfailed`' in "
        f"{CONTRACT_PATH.relative_to(REPO_ROOT)}"
    )
    bare_total = sum(int(g) for g in bare_match.groups())

    gate_match = re.search(r"`(\d+)\s+passed,\s+(\d+)\s+xfailed`", text)
    assert gate_match, (
        "expected to find the gate's result as '`<N> passed, <N> xfailed`' "
        f"in {CONTRACT_PATH.relative_to(REPO_ROOT)}"
    )
    gate_total = sum(int(g) for g in gate_match.groups())

    assert bare_total == collected, (
        f"the contract's bare-suite row sums to {bare_total} tests "
        f"({bare_match.group(0)}), but `pytest --collect-only tests/` "
        f"actually collects {collected} today -- the document has drifted "
        f"from the suite (this is exactly finding 3, second final-review "
        f"round); update {CONTRACT_PATH.relative_to(REPO_ROOT)}"
    )
    assert gate_total == collected, (
        f"the contract's gate row sums to {gate_total} tests "
        f"({gate_match.group(0)}), but `pytest --collect-only tests/` "
        f"actually collects {collected} today -- the document has drifted "
        f"from the suite; update {CONTRACT_PATH.relative_to(REPO_ROOT)}"
    )


# --- Per-service row (M1.2 Task 6) -----------------------------------------
#
# The row this guards reads, verbatim, as of this writing (M3 Task 5B; it read
# `claims-agent 14, settlement-agent 31, payments-mcp 7, payments-api (Go) 7,
# customer-ui (vitest) 6` when this guard was written at M1.2 Task 6, and
# `claims-agent 16, settlement-agent 79, payments-mcp 9, payments-api (Go) 20,
# customer-ui (vitest) 6, policy-decision-service 96` at the M2.2 final-review
# fix round -- which is the drift it exists to catch):
#
#   claims-agent 16, settlement-agent 108, payments-mcp 15,
#   payments-api (Go) 31, customer-ui (vitest) 6,
#   policy-decision-service 105, reviewer-ui 50 -- all passing.
#
# Each service is checked with the *same command* `scripts/smoke-test.sh`
# uses for it (Step 1/2), just in collect/list mode instead of run mode, so
# this never actually executes a service's tests: no cluster, no tokens, no
# side effects, and -- as of this fix round -- no network access either.
# Each counter asserts its own toolchain is present first (venv python,
# vitest binary) and fails loudly if it isn't, rather than falling back to
# something that could reach out over the network (`npx vitest`) or resolve
# unpredictably (a global npx cache); `_go_test_count` additionally runs
# with `GOPROXY=off` so a cold Go module cache fails loudly instead of
# fetching. A missing toolchain is a hard failure here, never a skip -- a
# guard that skips silently is a guard that stops guarding.

PER_SERVICE_ROW_RE = re.compile(
    r"Per-service unit suites[^\n]*:\s*\n(.*?)\N{EM DASH} all passing",
    re.DOTALL,
)


def _pytest_collected_count(service_dir: Path) -> int:
    venv_python = service_dir / ".venv" / "bin" / "python"
    assert venv_python.exists(), (
        f"{venv_python} does not exist -- this check needs each service's "
        "own virtualenv, the same one scripts/smoke-test.sh requires"
    )
    result = subprocess.run(
        [str(venv_python), "-m", "pytest", "--collect-only", "-q", "tests"],
        cwd=service_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"`{venv_python} -m pytest --collect-only -q tests` failed in "
        f"{service_dir}, so this test cannot verify its published count:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    match = re.search(r"(\d+) tests? collected", result.stdout)
    assert match, (
        f"could not parse a '<N> tests collected' summary line out of "
        f"pytest's --collect-only output in {service_dir}:\n{result.stdout}"
    )
    return int(match.group(1))


def _go_test_count(service_dir: Path) -> int:
    # `go test -list '.*'` prints one matching test name per line and never
    # runs a single one of them -- the Go analogue of `--collect-only`,
    # confirmed by timing (sub-second here vs. the real run) and by reading
    # `go help testflag`. Mirrors smoke-test.sh's own `GOTOOLCHAIN=auto`.
    # 2026-09-11 (M3): that was `GOTOOLCHAIN=local` on both sides until
    # payments-api moved to otel-go v1.46.0, which declares `go 1.25.0`;
    # `go.mod` now pins `toolchain go1.27.1`, so `auto` is still exactly one
    # reproducible release and not a floating one. `GOPROXY=off` is kept and
    # still does its job: it forbids the Go toolchain from reaching the module
    # proxy network for anything not already in the local module cache -- a
    # missing/incomplete cache (the pinned toolchain included) must fail
    # loudly here, not silently fetch.
    env = dict(os.environ)
    env["GOTOOLCHAIN"] = "auto"
    env["GOPROXY"] = "off"
    result = subprocess.run(
        ["go", "test", "./...", "-list", ".*"],
        cwd=service_dir,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, (
        f"`go test ./... -list '.*'` failed in {service_dir}, so this test "
        f"cannot verify its published count:\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    # Every line is a matched test name except the trailing `ok  <pkg> ...`
    # (or `?   <pkg> [no test files]`) summary line(s) `go test` always
    # prints, with or without -list.
    names = [
        line
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith(("ok", "?", "FAIL"))
    ]
    return len(names)


def _vitest_test_count(service_dir: Path) -> int:
    # `vitest list` enumerates every test case (one per line, "<file> >
    # <describe> > <test name>") without running any of them -- the vitest
    # analogue of `--collect-only`, confirmed by timing against a real
    # `vitest run` of the same suite.
    #
    # Invoke the service's own installed binary directly rather than `npx
    # vitest list`: `npx` resolves the name over the network (a registry
    # metadata fetch, then a download) whenever it isn't already satisfied
    # by *some* local copy -- including a stale, unrelated global npx cache
    # under `~/.npm/_npx` that happens to have `vitest` from an earlier,
    # unrelated invocation on this machine, which would make this "no side
    # effects" guard silently network-dependent on a machine where that
    # cache doesn't exist. Asserting the project's own binary exists first,
    # the same way `_pytest_collected_count()` asserts its venv python
    # exists, makes a missing toolchain fail loudly instead of resolving to
    # the wrong copy or reaching the network.
    vitest_bin = service_dir / "node_modules" / ".bin" / "vitest"
    assert vitest_bin.exists(), (
        f"{vitest_bin} does not exist -- this check needs the service's own "
        "installed vitest binary (from `npm install`), the same one "
        "scripts/smoke-test.sh requires; it deliberately does not fall back "
        "to `npx vitest`, which can silently resolve over the network or "
        "through an unrelated global cache instead of failing loudly"
    )
    result = subprocess.run(
        [str(vitest_bin), "list"],
        cwd=service_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"`{vitest_bin} list` failed in {service_dir}, so this test cannot "
        f"verify its published count:\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, (
        f"`{vitest_bin} list` in {service_dir} printed no test lines at all:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return len(lines)


# (published name in the doc row, directory relative to REPO_ROOT, counter)
_PER_SERVICE_SUITES = [
    ("claims-agent", REPO_ROOT / "agents" / "claims-agent", _pytest_collected_count),
    ("settlement-agent", REPO_ROOT / "agents" / "settlement-agent", _pytest_collected_count),
    ("payments-mcp", REPO_ROOT / "tools" / "payments-mcp", _pytest_collected_count),
    ("payments-api", REPO_ROOT / "systems" / "payments-api", _go_test_count),
    ("customer-ui", REPO_ROOT / "apps" / "customer-ui", _vitest_test_count),
    # The sixth row, added in the M2.2 final-review fix round (finding 1,
    # ruling R42). It was left out on the stated reasoning that the PDS suite
    # needs a generation step (`gen-stubs.sh`) that a hermetic `tests/unit`
    # should not take on -- but the review demonstrated that reasoning false in
    # its premise: `tests/unit` ALREADY requires the generated stubs, because
    # `agents/settlement-agent`'s own suite imports `pds.server` and the row
    # three lines above collects that suite. Removing the stubs reddens this
    # very test today. The cost was already being paid; only the tests were
    # missing. `scripts/smoke-test.sh` now runs `gen-stubs.sh` ahead of every
    # suite, which is what makes this row collectible in the gate as well as
    # here.
    #
    # `_pytest_collected_count` needs no special case: the PDS suite runs with
    # its own `.venv/bin/python` from its own directory, exactly like the three
    # Python services above, and `gen-stubs.sh` leaves the stubs in that
    # directory (flat, because protoc emits `import ext_mcp_pb2`), which the
    # `cwd=service_dir` invocation puts on `sys.path`. If the stubs are
    # missing, the PDS's `conftest.py` raises rather than skipping and this
    # counter's `returncode == 0` assertion fails loudly carrying that message
    # -- which is the intended behaviour, not a gap.
    (
        "policy-decision-service",
        REPO_ROOT / "platform" / "policy-decision-service",
        _pytest_collected_count,
    ),
    # The seventh row, added in M3 Task 5B (ruling R32's sibling finding). It
    # is the sixth row's defect recurring one milestone later, and the
    # recurrence is worth naming because the two are not identical: the PDS was
    # left out while `tests/unit` already *depended* on it, so removing its
    # stubs reddened this very test. `apps/reviewer-ui` was left out with no
    # such backstop at all -- M3 Task 4 shipped 50 tests for the component that
    # carries this milestone's human decision, and nothing anywhere ran them,
    # so `scripts/smoke-test.sh` could go green with that suite entirely
    # broken. Adding a service to `deploy/k8s/` means adding it here, in the
    # same task.
    #
    # `_pytest_collected_count` needs no special case: like the four Python
    # services above it has its own `.venv`, its own `requirements.txt` and its
    # own `pytest.ini`, and its suite is hermetic -- no cluster, no network, no
    # model, 0.3s.
    ("reviewer-ui", REPO_ROOT / "apps" / "reviewer-ui", _pytest_collected_count),
    # The eighth row, added in M4 Task 4 -- in the same task that added the
    # service, which is what the sixth and seventh rows' comments above both say
    # should happen and what neither of them did. `platform/eval-scorer` ships
    # in `deploy/k8s/eval-scorer.yaml`, so the rule quoted two comments up
    # ("Adding a service to `deploy/k8s/` means adding it here, in the same
    # task") is the one that applies.
    #
    # `_pytest_collected_count` needs no special case: like the five Python
    # services above it has its own `.venv`, its own pinned `requirements.txt`
    # and a `tests/` directory it collects from its own root, and the suite is
    # hermetic by construction -- `scorer/trajectory.py`, `scorer/authority.py`
    # and `scorer/scores.py` import nothing outside the standard library, so
    # collection touches no cluster, no network and no model.
    ("eval-scorer", REPO_ROOT / "platform" / "eval-scorer", _pytest_collected_count),
]


def test_per_service_counts_match_what_each_suites_own_runner_collects():
    text = CONTRACT_PATH.read_text()

    row_match = PER_SERVICE_ROW_RE.search(text)
    assert row_match, (
        "could not find the 'Per-service unit suites' row in "
        f"{CONTRACT_PATH.relative_to(REPO_ROOT)} -- expected a paragraph "
        "starting 'Per-service unit suites' and ending '— all passing', "
        "listing one count per service"
    )
    row = row_match.group(1)

    mismatches = []
    missing = []
    for name, service_dir, counter in _PER_SERVICE_SUITES:
        # Matches "claims-agent 14", "payments-api (Go) 7", "customer-ui
        # (vitest) 6" -- an optional "(<runner>)" annotation between the
        # service name and its count.
        name_match = re.search(
            re.escape(name) + r"(?:\s*\([^)]*\))?\s+(\d+)", row
        )
        if not name_match:
            missing.append(name)
            continue
        published = int(name_match.group(1))
        actual = counter(service_dir)
        if published != actual:
            mismatches.append(
                f"{name}: the contract says {published}, but its own test "
                f"runner collects {actual} in {service_dir.relative_to(REPO_ROOT)}"
            )

    assert not missing, (
        "the 'Per-service unit suites' row is missing a published count for: "
        f"{', '.join(missing)} -- expected every one of "
        f"{[n for n, _, _ in _PER_SERVICE_SUITES]} to appear as '<name> <N>' "
        f"in that row:\n{row}"
    )
    assert not mismatches, (
        "docs/telemetry-contract.md's 'Per-service unit suites' row has "
        "drifted from what each service's own suite actually collects "
        "(this is the third recurrence of exactly this defect -- see the "
        "module docstring):\n" + "\n".join(mismatches)
    )


# --- M4 Task 5: the gated figure, which was published and unguarded ----------


def _gated_test_count() -> tuple[int, dict[str, int]]:
    """How many collected tests are gated behind `RUN_BROWSER_TESTS=1`, asked
    of pytest rather than of a document.

    Same shape as `_collected_test_count` above -- `--collect-only`, no cluster,
    no `.env`, no port-forwards -- with a tiny in-process plugin that reads each
    item's own `skipif` markers. A marker counts when its reason names
    `RUN_BROWSER_TESTS` *and* its condition is true with the variable unset,
    which is exactly the question "would a bare `pytest tests/` skip this?".

    `RUN_BROWSER_TESTS` is removed from the child's environment rather than set
    to something falsy: this test may itself be running inside the gate, where
    it is set to 1, and a condition evaluated under that would report zero
    gated tests and make every assertion below vacuously true. That is the
    failure mode this file exists to catch, one level up.
    """
    plugin = REPO_ROOT / "tests" / "unit" / "_count_gated_plugin.py"
    env = dict(os.environ)
    env.pop("RUN_BROWSER_TESTS", None)
    env["PYTHONPATH"] = str(plugin.parent) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/",
            "-p", "no:cacheprovider", "-p", "_count_gated_plugin",
        ],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env, timeout=300,
    )
    assert result.returncode == 0, (
        "collection failed while counting gated tests:\n"
        f"{result.stdout[-3000:]}\n{result.stderr[-2000:]}"
    )
    marker = re.search(r"^GATED-COUNT (\d+) (\{.*\})$", result.stdout, re.M)
    assert marker, (
        "the gated-count plugin printed nothing. Its output line is the only "
        "channel between it and this test, so a plugin that failed to load "
        "would otherwise look like 'zero tests are gated'.\n"
        f"{result.stdout[-3000:]}"
    )
    import ast

    return int(marker.group(1)), ast.literal_eval(marker.group(2))


def test_the_contracts_gated_figure_is_what_pytest_would_actually_skip():
    """The gated count was published in three documents and re-measured by
    nobody.

    `docs/telemetry-contract.md` and `README.md` both state how many tests sit
    behind `RUN_BROWSER_TESTS=1`, and the contract's own prose says the figure
    "is measured ... a `--collect-only` run with a plugin that counts items
    carrying a `skipif` whose reason names `RUN_BROWSER_TESTS`". It was --
    once, by hand, per milestone. Nothing re-ran it, so **deleting a
    `pytestmark` from a settlement-driving e2e file changed no published number
    and reddened no test**: the bare and gate rows above both sum to the
    *collected* total, which a removed gate does not move. A repo whose whole
    cost model is "one flag gates every model call" had no guard on the flag.

    M4 Task 5 moved that figure from 41 to 71 by gating three files and two
    parametrised controls, which is exactly the kind of change that should not
    be checkable only by re-reading prose.
    """
    gated, per_file = _gated_test_count()
    text = _current_figures_block()

    # `\s+`, not a literal space, for the reason the bare-row pattern above
    # gives at length: the contract is hard-wrapped prose and this phrase wraps
    # between "and" and "reports" today. With a literal space the search
    # returns None and the *assertion message* is the only thing that tells you
    # so -- which is a better failure than the one the bare row had, but only
    # because that row's lesson was already learned here.
    published = re.search(r"and\s+reports (\d+) of (\d+),", text)
    assert published, (
        "expected docs/telemetry-contract.md's current block to state the "
        "gated figure as 'and reports <N> of <M>,'"
    )
    assert int(published.group(1)) == gated, (
        f"the contract publishes {published.group(1)} gated tests; a "
        f"--collect-only run with RUN_BROWSER_TESTS unset finds {gated}. "
        f"Per file: {per_file}"
    )
    assert int(published.group(2)) == _collected_test_count(), (
        f"the contract's gated figure is stated 'of {published.group(2)}' but "
        f"pytest collects {_collected_test_count()}"
    )

    readme = (REPO_ROOT / "README.md").read_text()
    readme_figure = re.search(
        r"modules sit behind `RUN_BROWSER_TESTS=1`, \*\*(\d+)\*\* tests in all", readme
    )
    assert readme_figure, (
        "expected README.md to state '<word> modules sit behind "
        "`RUN_BROWSER_TESTS=1`, **<N>** tests in all'"
    )
    assert int(readme_figure.group(1)) == gated, (
        f"README.md publishes {readme_figure.group(1)} gated tests and pytest "
        f"finds {gated}. Per file: {per_file}"
    )


def test_the_published_browser_split_is_what_the_source_says():
    """M4's final review, finding M1: the one gated figure with no guard.

    `RUN_BROWSER_TESTS=1` is a **cost** gate, and the two documents say so by
    stating how few of the gated modules actually start a browser. That number
    has now been wrong twice in three tasks: "six of the ten" (M4 Task 4's, left
    behind when Task 5 added four gated modules), then "only one of the eleven"
    — published in three places at once *by the stale-prose pass*, which moved
    the denominator and the numerator and re-derived neither. The truth is
    three. The pre-M4 text it replaced had it right.

    Every other count in these documents is checked against pytest. This one
    was checked against a reader, which is why it is the one that broke. It is
    trivially derivable, so it is derived: `chromium.launch(` in `tests/e2e`,
    intersected with the gated module list pytest itself reports.
    """
    _, per_file = _gated_test_count()
    gated_modules = set(per_file)

    e2e = REPO_ROOT / "tests" / "e2e"
    browser_modules = {
        path.name
        for path in sorted(e2e.glob("test_*.py"))
        if "chromium.launch(" in path.read_text()
    }
    assert browser_modules, (
        "no module under tests/e2e calls chromium.launch( -- either the browser "
        "tests were removed (in which case these documents need rewriting, not "
        "renumbering) or Playwright is now driven some other way and this "
        "derivation has gone blind. It must not silently answer zero."
    )
    assert browser_modules <= gated_modules, (
        f"these modules launch a browser and are NOT gated: "
        f"{sorted(browser_modules - gated_modules)}. A bare `pytest tests/` "
        "would start Chromium, which is the one thing the flag's name promises "
        "it will not."
    )

    with_browser = len(browser_modules)
    without_browser = len(gated_modules) - with_browser
    total = len(gated_modules)

    for path in (CONTRACT_PATH, REPO_ROOT / "README.md"):
        text = path.read_text()
        claims = re.findall(
            r"\*\*(\d+) of the (\d+) (?:gated modules )?(launch a browser|start no browser)\*\*",
            text,
        )
        assert claims, (
            f"{path.name} states no browser split in the checked form. Write it "
            f"as '**{with_browser} of the {total} launch a browser**' or "
            f"'**{without_browser} of the {total} start no browser**' so this "
            "guard can read it -- a figure nothing can read is a figure nothing "
            "checks, which is how this one was wrong twice."
        )
        for count, denominator, kind in claims:
            expected = with_browser if kind == "launch a browser" else without_browser
            assert (int(count), int(denominator)) == (expected, total), (
                f"{path.name} publishes '{count} of the {denominator} {kind}'. "
                f"Measured: {with_browser} of the {total} launch a browser "
                f"({sorted(browser_modules)}), {without_browser} of the {total} "
                f"start no browser."
            )


def test_the_bare_rows_xfail_term_accounts_for_gating():
    """M4's final review, finding M3: a premise changed and the conclusion was
    re-applied.

    The bare-suite row is derived as `collected - gated - xfailed`. That
    subtraction was correct for three milestones, because the tree's only
    `pytest.xfail` lived in an **ungated** module and therefore really did
    xfail on a bare run. M4 Task 5 gated that module -- and re-derived the row
    in the same commit, keeping the `- 1 xfailed` term. On a bare run that test
    now *skips*: it is already inside the 71, and subtracting it again counts
    one test twice. The row summed to the collected total only because of the
    double count.

    So the term is derived here rather than trusted. A test can only xfail on a
    bare run if the module holding it runs on a bare run.
    """
    _, per_file = _gated_test_count()
    gated_modules = set(per_file)

    xfail_modules = set()
    for directory in ("e2e", "unit", "harness"):
        for path in sorted((REPO_ROOT / "tests" / directory).glob("**/*.py")):
            text = path.read_text()
            if re.search(r"pytest\.xfail\(|pytest\.mark\.xfail\b", text):
                xfail_modules.add(path.name)
    assert xfail_modules, (
        "no module under tests/ contains an xfail any more. The bare and gate "
        "rows in the contract both carry an xfailed term, and if nothing can "
        "xfail then both are wrong -- this must not pass quietly."
    )

    ungated_xfail_modules = sorted(xfail_modules - gated_modules)
    text = _current_figures_block()
    bare = re.search(r"`(\d+)\s+passed,\s+(\d+)\s+skipped,\s+(\d+)\s+xfailed`", text)
    assert bare, "the contract's bare-suite row is not in the expected form"
    published_xfailed = int(bare.group(3))

    if not ungated_xfail_modules:
        assert published_xfailed == 0, (
            f"the contract's bare-suite row claims {published_xfailed} xfailed, "
            f"but every module containing an xfail is gated ({sorted(xfail_modules)}) "
            "-- on a bare run those tests skip and are already counted in the "
            "skipped column. Subtracting them again counts a test twice, which "
            "is what made the row sum correctly while being wrong."
        )
    else:
        assert published_xfailed > 0, (
            f"these modules contain an xfail and are NOT gated: "
            f"{ungated_xfail_modules}. A bare run would report at least one "
            f"xfailed and the contract's row claims {published_xfailed}."
        )


def test_gate_paths_widened_list_actually_sees_systems_and_tools():
    """M5 Task 1 (V2), the mutation round's own finding.

    Mutating `"systems", "tools"` out of the list this file uses to decide
    gate-row staleness left `test_a_predicted_gate_row_is_labelled_as_one`
    GREEN -- because a task branch's own working tree, while this task is in
    progress, always has plenty of OTHER `gate_paths` files changed too
    (`scripts/`, `tests/`, ...), so "did anything change" stays true either
    way and that test cannot tell `systems`/`tools` apart from the rest. A
    guard that only asks "did anything change" is not the same guard as one
    that asks "did the right thing get looked at", and V2 was supposed to be
    the second.

    So this checks the two things the mutation proved neither of the other
    tests here actually cover: that `GATE_PATHS` names both directories, and
    that doing so is not decorative -- against the real commit `716d70c`,
    which changed exactly one file, `tools/payments-mcp/bootstrap.py`, and
    nothing under any of the other seven `GATE_PATHS` entries. Step 4's manual
    verification (narrow list: 0, widened list: 1), encoded so it is not
    manual the next time someone touches this list.
    """
    assert "systems" in GATE_PATHS and "tools" in GATE_PATHS, (
        "GATE_PATHS must include both 'systems' and 'tools': "
        "scripts/smoke-test.sh runs `(cd \"$ROOT/systems/payments-api\" && "
        "go test ./...)` and `(cd \"$ROOT/tools/payments-mcp\" && pytest "
        "tests -q)`, so a change under either one moves what the gate "
        "actually proves"
    )

    result = subprocess.run(
        ["git", "diff", "--name-only", "716d70c^..716d70c", "--", "systems", "tools"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"`git diff 716d70c^..716d70c -- systems tools` failed:\n{result.stderr}"
    )
    changed = [line for line in result.stdout.splitlines() if line.strip()]
    assert changed == ["tools/payments-mcp/bootstrap.py"], (
        "expected commit 716d70c to have changed exactly "
        f"['tools/payments-mcp/bootstrap.py'] under systems/ or tools/; got "
        f"{changed}. If this repo's history changed, point this test at a "
        "different real commit that changes only one of these two "
        "directories rather than deleting the check."
    )

    other_gate_paths = [p for p in GATE_PATHS if p not in ("systems", "tools")]
    narrow = subprocess.run(
        ["git", "diff", "--name-only", "716d70c^..716d70c", "--", *other_gate_paths],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert narrow.returncode == 0, f"git diff failed: {narrow.stderr}"
    narrow_hits = [
        line for line in narrow.stdout.splitlines()
        if line.strip() and (line.startswith("systems/") or line.startswith("tools/"))
    ]
    assert not narrow_hits, (
        "the pre-fix (narrow) path list should not have been able to see "
        f"anything under systems/ or tools/ for commit 716d70c; found "
        f"{narrow_hits}"
    )


def test_a_predicted_gate_row_is_labelled_as_one():
    """M4's final review asked whether the fix wave warrants a new gate run.

    It does, and this is that answer written where it cannot rot. The contract
    publishes two rows for the same collection: the bare row, which is
    *derived* and says so, and the gate row, which until now was **measured**
    and said so. Every check above compares those rows to today's collection --
    so the moment a commit adds a test, the only way to keep them green is to
    edit the gate row, and editing a measured row turns a record of a run into
    arithmetic wearing a measurement's clothes. Nothing in the repo could tell
    the two apart afterwards.

    So the row carries the commit it was measured at, and this test compares
    that to the **working tree** (see the comment on `gate_paths` below for
    why `HEAD` would have been satisfied by the fix wave itself):

    * they differ  -> the row must be labelled `PREDICTED, NOT MEASURED`;
    * they match   -> the label must be **gone**, so a stale label cannot sit
      there permanently and make every future row unfalsifiable.

    The second direction is the one worth having. A warning that is always
    present is not a warning.
    """
    block = _current_figures_block()

    named = re.search(r"gate run was at commit `([0-9a-f]{7,40})`", block)
    assert named, (
        "the contract's current block does not say which commit its gate row "
        "was measured at. Write it as \"gate run was at commit `<sha>`\": a "
        "gate row with no commit cannot be shown to be stale, and this "
        "milestone's review found exactly that -- a measured row that five "
        "later tests had quietly turned into a prediction."
    )

    measured_sha = named.group(1)

    # The comparison is the WORKING TREE against that commit, not HEAD against
    # it. Comparing shas would have been satisfied by this very fix wave: at
    # the moment it was written HEAD still *was* the gate's commit, with five
    # new tests and a changed `eval-scorer` sitting uncommitted beside it --
    # the guard would have said "measured" about a tree the gate had never
    # seen. What makes a gate row stale is a changed file, and a file is
    # changed whether or not it has been committed yet.
    #
    # Scoped to what the gate runs and deploys. `docs/` is deliberately absent:
    # this very document is edited by every task that touches the figures, and
    # a rule that demanded a 7-minute cluster run for a typo is a rule people
    # delete. `systems/` and `tools/` ARE executed by the gate --
    # scripts/smoke-test.sh runs `(cd "$ROOT/systems/payments-api" && go test
    # ./...)` and `(cd "$ROOT/tools/payments-mcp" && pytest tests -q)`, so a
    # change under either one moves what the gate actually proves and this
    # guard was silent about it (M5 Task 1). The list is module-level
    # (`GATE_PATHS`) so `test_gate_paths_widened_list_actually_sees_systems_and_tools`
    # below can check its membership directly -- mutating "systems", "tools"
    # out of it left every test in THIS function green, because this task's
    # own working tree always has other gate_paths files changed too, and
    # this function only asks "did anything change", never "did the right
    # thing get looked at". That was a real gap, found by mutating.
    gate_paths = GATE_PATHS

    diff = subprocess.run(
        ["git", "diff", "--name-only", measured_sha, "--", *gate_paths],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert diff.returncode == 0, (
        f"`git diff {measured_sha}` failed, so this test cannot tell whether "
        f"the published gate row is current:\nstderr:\n{diff.stderr}"
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *gate_paths],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert untracked.returncode == 0, (
        f"`git ls-files --others` failed:\nstderr:\n{untracked.stderr}"
    )
    changed = sorted(set(diff.stdout.split()) | set(untracked.stdout.split()))

    # The label has to be ON the gate row, not merely somewhere in the block.
    # A first draft of this test searched the whole block, and mutating the
    # row's own label to "measured" left it GREEN -- the block's *heading* also
    # contained the phrase, so the guard was reading a sentence about the row
    # rather than the row. That is the same defect it was written to catch, one
    # level up.
    gate_row = re.search(r"Gate row:(.{0,200})", block, re.S)
    assert gate_row, (
        "the contract's current block has no 'Gate row:' -- there is nothing "
        "to label, and the checks above are comparing an unidentified figure "
        "to today's collection."
    )
    labelled = "PREDICTED, NOT MEASURED" in gate_row.group(1)
    if changed:
        assert labelled, (
            f"the contract's gate row says it was measured at {measured_sha}, "
            f"but {len(changed)} file(s) the gate runs or deploys have changed "
            f"since then -- e.g. {changed[:5]}. The published row is therefore "
            "arithmetic, not a measurement. Either re-run "
            "`RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh`, record the new "
            "figures and the new commit -- or mark the row "
            "'PREDICTED, NOT MEASURED' and say what has to happen before "
            "merge. Silently editing the numbers is the one option this test "
            "removes."
        )
    else:
        assert not labelled, (
            f"nothing the gate runs has changed since {measured_sha}, so the "
            "row is a measurement again -- but it is still labelled "
            "'PREDICTED, NOT MEASURED'. Remove the label. A caveat that "
            "outlives its cause teaches readers to ignore caveats, and the "
            "next genuinely predicted row would read exactly like this one."
        )
