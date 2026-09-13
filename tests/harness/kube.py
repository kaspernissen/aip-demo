"""The one definition of "does this object exist, or could I not look?".

**Ruling M4-R14.** M4 finished Task 3 carrying two different skip shapes for the
same question:

  * `tests/e2e/test_prometheus_metrics_ingest.py` (Task 2) hard-failed any
    non-`NotFound` `kubectl` exit -- its review finding F7 -- so an unreachable
    API server could not be mistaken for "this cluster is below stage 4".
  * `tests/e2e/test_opensearch_trace_ingest.py` (Task 1) still read
    `returncode == 0` and treated every other exit as "absent". F7 was raised
    against Task 2's file and deliberately not widened into Task 1's without a
    review asking for it; that file's own docstring said so.

Task 3 then *imported* the stricter one rather than copying it, which is why
this reconciliation is a move rather than a merge: there was one definition,
and this module is where it now lives so that neither of the two callers owns
it and a third cannot pick the weaker shape by accident.

The shape that survives is the strict one. A skip that says "nothing to check
here" when the truth is "I could not look" is the same false negative these
files exist to prevent, one level up -- and it is worse in the gate, where a
skip is invisible in a green run (ruling M4-R9).
"""
from __future__ import annotations

import re
import subprocess

# kubectl's own "the API server says this object is absent" shape, with the
# object named: `services "opensearch" not found`. Anchored on the quoted name
# so that a `not found` from anywhere else -- a shell reporting a missing
# binary, a library error -- is not mistaken for it.
_NAMED_OBJECT_NOT_FOUND = re.compile(r'"[^"]+"\s+not found', re.IGNORECASE)


def _context_of(result: subprocess.CompletedProcess) -> str:
    """The `--context` the failed command actually used, read off its own argv.

    Read from `result.args` rather than taken as a parameter or imported from a
    module-level constant: seven `tests/e2e` modules each define their own
    `KUBE_CONTEXT = "kind-northstar"` literal, so a helper that named an
    imported constant could name a context the command did not use. The argv is
    the command that ran, so this cannot disagree with it.
    """
    args = list(result.args or [])
    if "--context" in args:
        index = args.index("--context")
        if index + 1 < len(args):
            return repr(args[index + 1])
    return "(no --context in the command's argv)"


def absent_or_fail(result: subprocess.CompletedProcess, what: str) -> bool:
    """True if `what` exists, False if the API server says it does not, and a
    hard failure for every other way `kubectl` can exit non-zero.

    An unreachable API server, a missing or misspelled context, an expired
    credential and a genuine `NotFound` are all non-zero exits, so reading
    `returncode == 0` as "it exists" and everything else as "it is absent"
    makes a transient kubectl failure look exactly like a cluster below stage 4
    -- silently removing a milestone's assertions from the one gate run that
    performs them.

    `NotFound` is matched on kubectl's own error text, which is stable across
    resource kinds: `Error from server (NotFound): services "x" not found`.
    Either half of that is accepted, because `kubectl wait` and some subresource
    paths print the quoted-name half without the API reason -- but a bare
    lowercase `not found` anywhere in stderr is *not*, which M4 Task 5's first
    test of this function caught: `kubectl: command not found` is a missing
    binary, and reading it as a missing object would skip a milestone's
    assertions on a machine that cannot run kubectl at all.
    """
    if result.returncode == 0:
        return True
    stderr = result.stderr or ""
    if "NotFound" in stderr or _NAMED_OBJECT_NOT_FOUND.search(stderr):
        return False
    raise AssertionError(
        f"`kubectl` failed while checking whether {what} exists, and the "
        "failure is not 'it does not exist' -- so this file cannot tell "
        "whether the cluster is below stage 4 or simply unreachable, and it "
        "must not guess. Exit code "
        f"{result.returncode}; stderr:\n{stderr.strip() or '(empty)'}\n"
        f"Context in use: {_context_of(result)}."
    )
