"""The mutation protocol has to survive, so it is pinned rather than written.

**What this guards and why it is in `tests/unit` rather than in a task report.**
This repo's standing convention is that a guard is not trusted until it has
been mutated and watched go red for the reason it names. The naive
edit/run/revert loop is unsound: CPython validates a cached `.pyc`
against `(int(source_mtime), source_size)` and nothing else, so a
length-preserving mutation (`6000` -> `6500`) that lands in the same integer
second as the cached write produces either

  * a **false green** -- the mutant is on disk, the original bytecode runs, and
    the round records "the guard did not catch it" about a mutation that never
    ran; or
  * a **false red** -- the revert is on disk, the mutant bytecode still runs,
    and every later mutation in the round reddens for the previous one's
    reason.

Both directions were reproduced from scratch. `PYTHONDONTWRITEBYTECODE=1` plus
a `__pycache__` purge removes the vector, and `scripts/mutate-guard.sh` is
where that lives so the next implementer inherits a tool rather than a
paragraph in a report nobody reads again.

A convention recorded only in prose is a convention that gets dropped in a
tidy-up, which is the failure this whole file exists against: the two lines
that make the script sound are load-bearing and look like boilerplate.

Reads two files, and runs `scripts/mutate-guard.sh` itself against a target
in pytest's `tmp_path` -- outside the working tree, so a round that failed to
restore cannot leave a mutant in the repo. No cluster, no network, no model.

The four behavioural tests at the bottom are behavioural on purpose. A guard
that instead matched the literal source of the uniqueness check, such as
`if old not in text`, would redden the moment that condition is rewritten to
an equivalent but differently-worded one, and would stay green if the check
were deleted and the surrounding words kept. A guard on a script's behaviour
is worth more than a guard on its source when running it costs a subprocess
and a second.
"""
import os
import re
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "mutate-guard.sh"
README_PATH = REPO_ROOT / "README.md"


def _script_text() -> str:
    assert SCRIPT_PATH.exists(), (
        f"{SCRIPT_PATH.relative_to(REPO_ROOT)} is gone. It is the repo's "
        "mutation-testing protocol; if it moved, point this guard at where it "
        "went rather than deleting the check."
    )
    return SCRIPT_PATH.read_text()


def _effective_script() -> str:
    """The script with whole-line comments removed.

    The same tool `test_smoke_test_gate.py` uses, for the same reason: this
    script explains `PYTHONDONTWRITEBYTECODE` at length in its header, so
    without this the assertions below would pass against a script that had
    only the explanation and not the behaviour.
    """
    return "\n".join(
        line for line in _script_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def test_the_mutation_script_is_executable():
    mode = SCRIPT_PATH.stat().st_mode
    assert mode & stat.S_IXUSR, (
        f"{SCRIPT_PATH.relative_to(REPO_ROOT)} is not executable, so the "
        "README's `./scripts/mutate-guard.sh ...` invocation fails with "
        "Permission denied"
    )


def test_the_mutation_script_disables_bytecode_caching_on_the_run_itself():
    """`PYTHONDONTWRITEBYTECODE=1` must be on the command, not merely exported
    somewhere or described in a comment.

    Asserted on the comment-stripped text, and specifically as a prefix of the
    `"$@"` invocation: an `export` earlier in the file would work today and
    would silently stop working the moment the run moves into a subshell that
    scrubs the environment, which is exactly how this kind of line rots.
    """
    script = _effective_script()
    assert re.search(r'PYTHONDONTWRITEBYTECODE=1\s+"\$@"', script), (
        "scripts/mutate-guard.sh does not run the test command with "
        "PYTHONDONTWRITEBYTECODE=1 in front of it. Without it a "
        "length-preserving mutation reverted inside the same integer second "
        "leaves the MUTANT bytecode executing against reverted source -- so "
        "the next mutation in the round reddens for the previous one's "
        "reason. Both error directions were reproduced; see this file's "
        "docstring."
    )


def test_the_mutation_script_purges_stale_caches_around_the_run():
    """Not writing a cache is only half of it: a `__pycache__` already on disk
    from an ordinary test run is read just the same. Both purges must exist --
    before the run (so a cache from the *pre*-mutation state cannot serve the
    mutated source) and after it (so the round leaves nothing behind for the
    next one)."""
    script = _effective_script()
    # The `-exec rm -rf` half is asserted, not just the `find`: swapping the
    # deletion for `-print` leaves a line that still looks like a purge and
    # purges nothing, and a guard matching only `find … -name __pycache__ -type
    # d` would stay green on exactly that edit.
    assert re.search(
        # Bounded `.{0,300}?` rather than a bare `.*?`: the real command wraps
        # across lines, so the match has to cross a newline, but an unbounded
        # gap would happily pair this `find` with some unrelated `rm -rf`
        # hundreds of lines later and call that a purge.
        r"find .{0,300}?-name __pycache__ -type d.{0,300}?-exec\s+rm\s+-rf",
        script, re.S,
    ), (
        "scripts/mutate-guard.sh does not delete __pycache__ directories. "
        "PYTHONDONTWRITEBYTECODE stops new caches being written; it does not "
        "stop an existing one being read, so a find that merely lists them "
        "leaves the vector open."
    )
    body = script.split('PYTHONDONTWRITEBYTECODE=1 "$@"')
    assert len(body) == 2, "expected exactly one guarded test invocation"
    before, after = body
    assert "purge_caches" in before and "purge_caches" in after, (
        "the cache purge must happen on both sides of the run. Before: a "
        "cache written by an earlier ordinary run would serve the mutated "
        "source. After: the round leaves a cache built from mutated source "
        "for whatever runs next."
    )


def test_the_mutation_script_restores_the_file_on_every_exit_path():
    """A mutation round that leaves a mutant on disk is worse than no round:
    the repo is now silently wrong and the next command to run is the one that
    looks broken. `trap ... EXIT INT TERM` is what covers the interrupted and
    failed cases, which are the ones a plain trailing `cp` misses."""
    script = _effective_script()
    assert re.search(r"trap\s+restore\s+EXIT\s+INT\s+TERM", script), (
        "scripts/mutate-guard.sh does not restore the mutated file via a "
        "trap on EXIT/INT/TERM. A round interrupted with ctrl-C, or one whose "
        "test command aborts, would leave the mutation in the working tree."
    )


def _run_guard(*args: str) -> subprocess.CompletedProcess:
    """The script itself, run. See this file's docstring on why these four are
    behavioural where everything above is a substring match."""
    return subprocess.run(
        [str(SCRIPT_PATH), *args],
        capture_output=True, text=True, timeout=60, cwd=REPO_ROOT,
    )


def _target(tmp_path: Path, text: str) -> tuple[Path, str]:
    """A mutation target outside the repo, and the repo-relative path the
    script takes. `..`-relative rather than absolute because the script joins
    `$ROOT/$TARGET`; nothing is written inside the working tree."""
    path = tmp_path / "target.txt"
    path.write_text(text)
    return path, os.path.relpath(path, REPO_ROOT)


def test_the_mutation_script_refuses_a_replacement_that_changes_nothing(tmp_path):
    """A no-op mutation runs a green suite and looks like a passing guard --
    the same false-green the `.pyc` trap produces, reached by a different
    route. It has to be refused rather than reported."""
    path, rel = _target(tmp_path, "limit = 6000\n")

    absent = _run_guard(rel, "limit = 9999", "limit = 1", "true")
    assert absent.returncode == 2, (
        "scripts/mutate-guard.sh ran the test command for a mutation whose "
        f"text is not in the file, so a green run would have been recorded for "
        f"a mutation that never happened.\n{absent.stdout}{absent.stderr}"
    )
    assert "not found" in (absent.stdout + absent.stderr)

    identical = _run_guard(rel, "limit = 6000", "limit = 6000", "true")
    assert identical.returncode == 2, (
        "scripts/mutate-guard.sh accepted a replacement identical to the "
        f"original.\n{identical.stdout}{identical.stderr}"
    )
    assert path.read_text() == "limit = 6000\n", "the target was not left as it was found"


def test_the_mutation_script_refuses_a_pattern_that_is_not_unique(tmp_path):
    """**Why a pattern must be unique, and why this test is behavioural.**

    The script replaced the FIRST match and said nothing about the others, so a
    mutation whose text also appears in a test the command does not run lands
    there instead, the suite stays green, and the round records GREEN -- "no
    guard covers this" -- about a line that was never mutated. The tool built
    to stop false GREENs produced one.

    Asserted by running it, not by grepping for the check: a guard that
    matched the literal `if old not in text` would redden the moment that
    condition is rewritten to an equivalent but differently-worded one (such
    as `text.count(old)`), and would stay green if the check were deleted and
    the surrounding words kept.
    """
    path, rel = _target(tmp_path, "assert count >= 5\nfiller\nassert count >= 5\n")
    result = _run_guard(rel, "assert count >= 5", "assert count >= 99", "true")

    output = result.stdout + result.stderr
    assert result.returncode == 2, (
        "scripts/mutate-guard.sh accepted a pattern matching two lines. It "
        "either silently picked one of them -- which is how a mutation round "
        "records GREEN for a line it never touched -- or replaced both without "
        f"saying so.\n{output}"
    )
    assert "occurs 2 times" in output, (
        "the refusal does not say how many times the pattern matched, which is "
        f"the one fact that tells the operator how to narrow it.\n{output}"
    )
    assert path.read_text() == "assert count >= 5\nfiller\nassert count >= 5\n", (
        "the target was modified by a run that refused to mutate it"
    )


def test_the_mutation_script_really_mutates_and_really_restores(tmp_path):
    """The positive control for the two refusals above.

    Without it, a script that refused *everything* would pass both of them --
    the vacuity failure this repo keeps finding. The test command reads the
    file while the run is in progress, so a GREEN here proves the mutant was
    on disk when the command ran, and the assertion after it proves the
    original came back.
    """
    path, rel = _target(tmp_path, "limit = 6000\n")
    result = _run_guard(
        rel, "limit = 6000", "limit = 6500",
        "grep", "-q", "limit = 6500", str(path),
    )
    assert result.returncode == 0, (
        "the test command could not see the mutation on disk while it ran, so "
        "this script does not actually mutate the file it reports on.\n"
        f"{result.stdout}{result.stderr}"
    )
    assert "GREEN" in result.stdout, (
        "a surviving mutation must be reported as GREEN and named as a "
        f"finding.\n{result.stdout}"
    )
    assert path.read_text() == "limit = 6000\n", (
        "scripts/mutate-guard.sh left the mutation on disk. A round that does "
        "this makes the repo silently wrong and the next command run look "
        "broken."
    )


def test_the_usage_help_prints_the_whole_header(tmp_path):
    """Called with too few arguments, the script prints its header as usage.

    The range is derived rather than hard-coded (e.g. a fixed `sed -n
    '2,50p'`): a hard-coded line count stops the usage text mid-sentence the
    moment the header grows past it, cutting off the uniqueness rule that is
    the most common reason for the exit-2 it is printed by. This pins that the
    derived range still covers the last line of the header.
    """
    result = _run_guard("only", "three", "args")
    assert result.returncode == 2
    printed = result.stdout
    header = [
        line for line in _script_text().splitlines()[1:]
        if line.startswith("#")
    ]
    assert header, "the script has no comment header for the usage branch to print"
    assert header[0].lstrip("# ") in printed, "the usage text does not start at the header"
    assert header[-1].lstrip("# ") in printed, (
        "the usage text stops before the end of the header, so the rules added "
        "most recently -- which are the ones a reader has not seen -- are the "
        "ones cut off. Do not re-hardcode a line range; derive it."
    )


def test_the_readme_documents_the_protocol_and_names_the_script():
    """The script is the tool; the README is how the next implementer finds
    it. A convention documented only in an internal report is invisible to
    anyone who doesn't know to look for it.
    """
    readme = README_PATH.read_text()
    assert "scripts/mutate-guard.sh" in readme, (
        "README.md does not name scripts/mutate-guard.sh, so the mutation "
        "protocol is a script nobody is told about"
    )
    assert "PYTHONDONTWRITEBYTECODE" in readme, (
        "README.md's mutation-testing section does not name "
        "PYTHONDONTWRITEBYTECODE=1. The reason the loop is unsound without it "
        "is the only part of that section that is not obvious, and it is the "
        "part that gets trimmed."
    )
    assert __name__.split(".")[-1] in readme or "test_mutation_protocol" in readme, (
        "README.md does not point at tests/unit/test_mutation_protocol.py. "
        "A convention with a guard nobody knows about gets 'simplified' by "
        "the next person to read the script."
    )


def test_this_guard_is_reading_the_script_and_not_an_empty_string():
    """The inertness check this whole file is about, applied to itself: every
    assertion above is a substring match, and every one of them would pass
    vacuously against nothing at all if the read silently returned empty."""
    assert len(_effective_script().strip()) > 400, (
        "the comment-stripped script is nearly empty, so the assertions above "
        "are checking almost nothing. Has the file been emptied, or has it "
        "become all comments?"
    )
    assert os.path.basename(SCRIPT_PATH) == "mutate-guard.sh"
