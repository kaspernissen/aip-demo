"""The one definition of "does this object exist, or could I not look?".

Several `tests/e2e` modules (`test_prometheus_metrics_ingest.py` and others) need to skip when a cluster is
below the stage that provisions some object, but only when that object is
genuinely absent -- not when `kubectl` merely failed to reach the API server.
Reading `returncode == 0` as "it exists" and every other exit as "absent"
makes a transient kubectl failure look exactly like the object being absent,
turning a skip into a false negative: the checks that were supposed to run
silently don't. This module is the one place that tells the two cases apart,
so every caller shares the strict shape instead of each keeping its own,
weaker copy.

A skip that says "nothing to check here" when the truth is "I could not
look" is worse in a gate run, where a skip is invisible in an otherwise green
result.
"""
from __future__ import annotations

import re
import subprocess

# kubectl's own "the API server says this object is absent" shape, with the
# object named: `services "prometheus" not found`. Anchored on the quoted name
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
    makes a transient kubectl failure look exactly like the object being
    absent -- silently skipping the checks that were supposed to run.

    `NotFound` is matched on kubectl's own error text, which is stable across
    resource kinds: `Error from server (NotFound): services "x" not found`.
    Either half of that is accepted, because `kubectl wait` and some subresource
    paths print the quoted-name half without the API reason -- but a bare
    lowercase `not found` anywhere in stderr is *not* accepted, because
    `kubectl: command not found` is a missing binary, not a missing object,
    and reading it as one would skip the checks on a machine that cannot run
    kubectl at all.
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
