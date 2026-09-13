"""The discriminator every stage gate in `tests/e2e` now depends on.

Ruling M4-R14 reconciled two skip shapes into one definition,
`tests/harness/kube.py::absent_or_fail`, on the grounds that the strict shape
is the one worth keeping: an unreachable API server must be a failure, not a
skip. That argument was written down and then asserted by nothing. Three e2e
modules import the helper, and every one of them is itself skipped on a cluster
below stage 4 -- so a regression that widened `absent_or_fail` back to
"non-zero means absent" would turn those files green-by-skipping in exactly the
situation the ruling was about, and no test in the repo would notice.

These run with no cluster and no `kubectl`: `CompletedProcess` is constructed
directly, which is also the only way to exercise the "API server unreachable"
branch deterministically.
"""
from __future__ import annotations

import subprocess

import pytest

from tests.harness.kube import _context_of, absent_or_fail

ARGV = ["kubectl", "--context", "kind-northstar", "get", "svc", "opensearch"]


def _result(returncode: int, stderr: str = "", args=None) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=ARGV if args is None else args, returncode=returncode, stdout="", stderr=stderr
    )


def test_a_clean_exit_means_the_object_exists():
    assert absent_or_fail(_result(0), "the opensearch service") is True


@pytest.mark.parametrize(
    "stderr",
    [
        pytest.param(
            'Error from server (NotFound): services "opensearch" not found',
            id="kubectl-get-a-missing-service",
        ),
        pytest.param(
            'Error from server (NotFound): configmaps "otel-collector-config" not found',
            id="kubectl-get-a-missing-configmap",
        ),
        pytest.param(
            'Error from server (NotFound): namespaces "observability" not found',
            id="kubectl-get-in-a-missing-namespace",
        ),
    ],
)
def test_the_api_server_saying_it_does_not_exist_means_absent(stderr):
    """The one non-zero exit that is allowed to become a skip.

    Three real kubectl texts rather than one, because the match is on kubectl's
    error string: a helper that keyed on the resource kind would pass a
    single-example test and skip wrongly on the next kind a milestone adds.
    """
    assert absent_or_fail(_result(1, stderr), "the object") is False


@pytest.mark.parametrize(
    "returncode,stderr",
    [
        pytest.param(
            1,
            "The connection to the server 127.0.0.1:60471 was refused - did you "
            "specify the right host or port?",
            id="api-server-unreachable",
        ),
        pytest.param(
            1,
            'error: context "kind-northstar" does not exist',
            id="context-missing-or-misspelled",
        ),
        pytest.param(
            1,
            'Error from server (Forbidden): services "opensearch" is forbidden: '
            'User "x" cannot get resource "services"',
            id="credential-lacks-the-permission",
        ),
        pytest.param(
            1, "Unable to connect to the server: dial tcp: i/o timeout", id="dial-timeout"
        ),
        pytest.param(127, "kubectl: command not found", id="kubectl-not-installed"),
        pytest.param(1, "", id="non-zero-with-no-stderr-at-all"),
    ],
)
def test_every_other_non_zero_exit_is_a_failure_and_not_a_skip(returncode, stderr):
    """This is the whole of ruling M4-R14.

    Each of these is a way to get a non-zero exit that does *not* mean "the
    cluster is below stage 4". Under the shape M4-R14 replaced -- `returncode
    == 0` -- every row here returns "absent", and the milestone's assertions
    quietly leave the gate run that is supposed to perform them.

    The empty-stderr row matters on its own: a substring match against `""`
    finds nothing, so the helper must fail rather than fall through to some
    default.
    """
    with pytest.raises(AssertionError) as excinfo:
        absent_or_fail(_result(returncode, stderr), "the opensearch service")
    message = str(excinfo.value)
    assert "the opensearch service" in message, (
        "the failure must name the object that was being checked, or a gate "
        f"failure cannot be read back to a cause: {message}"
    )
    assert str(returncode) in message, f"the exit code is not in the message: {message}"


def test_the_word_forbidden_is_not_read_as_not_found():
    """A near-miss the substring match could plausibly get wrong.

    `not found` is matched case-insensitively, so any kubectl message that
    happens to contain the phrase would become a skip. This pins the specific
    pair that shares the most words -- Forbidden mentions the resource by name
    the way NotFound does -- as a failure.
    """
    stderr = (
        'Error from server (Forbidden): services "opensearch" is forbidden: '
        'User "system:anonymous" cannot get resource "services" in API group ""'
    )
    with pytest.raises(AssertionError):
        absent_or_fail(_result(1, stderr), "the opensearch service")


def test_the_failure_names_the_context_the_command_actually_used():
    """`_context_of` reads argv, so it cannot disagree with the command.

    Seven `tests/e2e` modules each define their own `KUBE_CONTEXT` literal. A
    helper that imported one constant would print that constant no matter which
    context the failing command used -- sending a reader to the wrong cluster
    to debug a failure. The value in the message comes from the argv of the
    process that failed.
    """
    result = _result(
        1,
        "The connection to the server 127.0.0.1:60471 was refused",
        args=["kubectl", "--context", "some-other-cluster", "get", "svc", "opensearch"],
    )
    with pytest.raises(AssertionError) as excinfo:
        absent_or_fail(result, "the opensearch service")
    assert "'some-other-cluster'" in str(excinfo.value)


def test_a_command_with_no_context_flag_says_so_rather_than_guessing():
    assert _context_of(_result(1, "boom", args=["kubectl", "get", "svc"])) == (
        "(no --context in the command's argv)"
    )


def test_a_trailing_context_flag_with_no_value_does_not_raise():
    """`--context` as the last argv element must not IndexError.

    The helper only ever runs on a path that is already failing; an IndexError
    here would replace a legible assertion message with a traceback from the
    error handler itself.
    """
    assert _context_of(_result(1, "boom", args=["kubectl", "get", "svc", "--context"])) == (
        "(no --context in the command's argv)"
    )


def test_every_e2e_caller_uses_the_shared_definition_and_not_a_local_copy():
    """The ruling is "one definition", which is a property of the repo.

    M4-R14 was discharged by deleting two local copies. Nothing stopped a
    fourth caller from pasting the weaker `returncode == 0` shape back in under
    a private name, which is how the repo got two shapes in the first place.
    This walks `tests/e2e` and requires that any module doing this check does
    it by importing the shared helper.
    """
    import pathlib
    import re

    e2e = pathlib.Path(__file__).resolve().parents[1] / "e2e"
    offenders = []
    for path in sorted(e2e.glob("test_*.py")):
        text = path.read_text()
        if re.search(r"^\s*def _?absent_or_fail\(", text, re.M):
            offenders.append(path.name)
    assert not offenders, (
        "these tests/e2e modules define their own absent-or-fail helper instead "
        "of importing tests.harness.kube.absent_or_fail, which is the one "
        f"definition ruling M4-R14 left standing: {offenders}"
    )
