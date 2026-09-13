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


def test_contracts_published_counts_match_what_pytest_actually_collects():
    collected = _collected_test_count()
    text = CONTRACT_PATH.read_text()

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
