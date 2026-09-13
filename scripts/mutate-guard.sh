#!/usr/bin/env bash
# Mutate one file, run a test command, restore the file, report the verdict.
#
# ---------------------------------------------------------------------------
# WHY THIS SCRIPT EXISTS, AND WHY YOU SHOULD NOT HAND-ROLL THE LOOP
#
# This repo's standing rule is that a guard is not trusted until it has been
# mutated and watched go red for the reason it names. That loop -- edit, run,
# revert -- has a trap in it that produces BOTH false negatives and false
# positives, and it was hit for real in M4 Task 4 and reproduced in both
# directions by that task's review.
#
# CPython validates a cached `.pyc` against `(int(source_mtime), source_size)`
# and nothing else. So if a mutation preserves the file's byte length -- and
# the useful ones often do, `6000` -> `6500`, `==` -> `!=`, `True` -> `1==1`
# -- and both the edit and the revert land in the same integer second as the
# write that produced the cached bytecode, then:
#
#   * FALSE GREEN: the mutant is on disk, the ORIGINAL bytecode executes, and
#     the round records "the guard did not catch it" about a mutation that
#     never ran.
#   * FALSE RED (the dangerous one): the revert is on disk, the MUTANT
#     bytecode still executes, and every subsequent mutation in the round is
#     evaluated against a contaminated module -- reddening for the previous
#     mutation's reason.
#
# The fastest suites are the most exposed, because an edit-run-revert cycle
# fits comfortably inside one second: `platform/eval-scorer`'s runs in 0.15s.
# pytest's own rewritten test-module caches invalidate on the same pair, so
# mutating a test file is exposed too.
#
# Two things remove the vector, and this script does both: `PYTHONDONTWRITEBYTECODE=1`
# so no cache is written, and a `__pycache__` purge so no cache already on
# disk is read. Text targets (YAML, Markdown, shell) are structurally immune
# -- no bytecode is involved -- but running them through here costs nothing
# and keeps one protocol rather than two.
#
# ---------------------------------------------------------------------------
# USAGE
#
#   scripts/mutate-guard.sh <file> <old-text> <new-text> <test command...>
#
#   scripts/mutate-guard.sh platform/eval-scorer/scorer/authority.py \
#     '"assisted"): 6000.0' '"assisted"): 6500.0' \
#     bash -c 'cd platform/eval-scorer && .venv/bin/python -m pytest tests -q'
#
# <file> is relative to the repo root. The first occurrence of <old-text> is
# replaced. The file is restored on every exit path, including a failed or
# interrupted test run. Exit 0 means the suite stayed GREEN, which for a
# mutation means the guard is INERT and you have found something.
#
# It refuses a no-op replacement rather than reporting a green run for a
# mutation that never happened -- the same failure the `.pyc` trap produces,
# reached by a different route.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $# -lt 4 ]]; then
  sed -n '2,50p' "${BASH_SOURCE[0]}"
  exit 2
fi

TARGET="$1"; OLD="$2"; NEW="$3"; shift 3

[[ -f "$ROOT/$TARGET" ]] || { echo "no such file: $ROOT/$TARGET" >&2; exit 2; }

BACKUP="$(mktemp)"
cp "$ROOT/$TARGET" "$BACKUP"
restore() { cp "$BACKUP" "$ROOT/$TARGET"; rm -f "$BACKUP"; purge_caches; }
trap restore EXIT INT TERM

purge_caches() {
  find "$ROOT" -name __pycache__ -type d -not -path '*/.venv/*' \
    -not -path '*/node_modules/*' -exec rm -rf {} + 2>/dev/null || true
}

OLD="$OLD" NEW="$NEW" python3 - "$ROOT/$TARGET" <<'PY' || exit 2
import os, pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
old, new = os.environ["OLD"], os.environ["NEW"]
if old not in text:
    sys.exit(f"mutation text not found in {path}, so nothing would change:\n  {old!r}")
if old == new:
    sys.exit("the replacement is identical to the original: this would report a "
             "green run for a mutation that never happened")
path.write_text(text.replace(old, new, 1))
PY

purge_caches
PYTHONDONTWRITEBYTECODE=1 "$@"
RC=$?
purge_caches

echo ""
if [[ $RC -eq 0 ]]; then
  echo "GREEN (exit 0) -- the mutation survived. Either no guard covers this,"
  echo "or the one that claims to does not reach it. That is a finding."
else
  echo "RED (exit $RC) -- a guard caught it. Read the failure message and check"
  echo "it names THIS mutation: a guard that reddens for another reason is not"
  echo "the guard you just tested."
fi
exit $RC
