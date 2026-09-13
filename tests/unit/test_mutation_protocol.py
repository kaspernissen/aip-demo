"""The mutation protocol has to survive, so it is pinned rather than written.

**What this guards and why it is in `tests/unit` rather than in a task report.**
This repo's standing convention is that a guard is not trusted until it has
been mutated and watched go red for the reason it names. M4 Task 4 found that
the naive edit/run/revert loop is unsound: CPython validates a cached `.pyc`
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

Reads two files. No cluster, no network, no subprocess.
"""
import os
import re
import stat
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
        "scripts/mutate-guard.sh no longer runs the test command with "
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
    # purges nothing. Found by mutating this guard -- the first version matched
    # only `find … -name __pycache__ -type d` and stayed green on exactly that
    # edit.
    assert re.search(
        # Bounded `.{0,300}?` rather than a bare `.*?`: the real command wraps
        # across lines, so the match has to cross a newline, but an unbounded
        # gap would happily pair this `find` with some unrelated `rm -rf`
        # hundreds of lines later and call that a purge.
        r"find .{0,300}?-name __pycache__ -type d.{0,300}?-exec\s+rm\s+-rf",
        script, re.S,
    ), (
        "scripts/mutate-guard.sh no longer DELETES __pycache__ directories. "
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
        "scripts/mutate-guard.sh no longer restores the mutated file via a "
        "trap on EXIT/INT/TERM. A round interrupted with ctrl-C, or one whose "
        "test command aborts, would leave the mutation in the working tree."
    )


def test_the_mutation_script_refuses_a_replacement_that_changes_nothing():
    """A no-op mutation runs a green suite and looks like a passing guard --
    the same false-green the `.pyc` trap produces, reached by a different
    route. It has to be refused rather than reported."""
    script = _effective_script()
    assert "if old not in text" in script and "if old == new" in script, (
        "scripts/mutate-guard.sh no longer refuses a mutation that would "
        "change nothing (text absent, or replacement identical). Either is a "
        "green run for a mutation that never happened."
    )


def test_the_readme_documents_the_protocol_and_names_the_script():
    """The script is the tool; the README is how the next implementer finds
    it. Task reports are not where anyone looks, which is the whole reason
    this moved out of one.
    """
    readme = README_PATH.read_text()
    assert "scripts/mutate-guard.sh" in readme, (
        "README.md no longer names scripts/mutate-guard.sh, so the mutation "
        "protocol is a script nobody is told about"
    )
    assert "PYTHONDONTWRITEBYTECODE" in readme, (
        "README.md's mutation-testing section no longer names "
        "PYTHONDONTWRITEBYTECODE=1. The reason the loop is unsound without it "
        "is the only part of that section that is not obvious, and it is the "
        "part that gets trimmed."
    )
    assert __name__.split(".")[-1] in readme or "test_mutation_protocol" in readme, (
        "README.md no longer points at tests/unit/test_mutation_protocol.py. "
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
