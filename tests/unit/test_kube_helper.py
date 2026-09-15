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

import ast
import subprocess
import textwrap

import pytest

from tests.harness.kube import _context_of, absent_or_fail

ARGV = ["kubectl", "--context", "kind-northstar", "get", "svc", "opensearch"]


def _reads_returncode(expr) -> bool:
    """A raw `.returncode` comparison, by structure rather than by name.

    Module-level (M5 Task 1, V9) so the vacuity control below can call the
    same function the repo walk uses, rather than a second copy that could
    silently drift from it.
    """
    return any(
        isinstance(inner, ast.Compare)
        and isinstance(inner.left, ast.Attribute)
        and inner.left.attr == "returncode"
        for inner in ast.walk(expr)
    )

# The functions allowed to decide a skip from a raw `kubectl` exit code, each
# with the reason it is not ruling M4-R14's defect. Every one is a
# **reachability** probe -- "can I talk to this cluster / is this dataplane
# here at all" -- where a non-zero exit genuinely means "there is nothing to
# check", as opposed to "does this named object exist", where it does not.
#
# This list exists because the shape walk below found eleven hits and only
# three were defects. Flagging the other eight would have taught a reader to
# edit correct code to please a test, which is ruling R22's hazard. An
# allow-list turns the walk into a forced classification instead: a NEW
# function that decides a skip from a raw exit code fails until someone
# either routes it through `absent_or_fail` or writes down why it is a probe.
_RAW_RETURNCODE_SKIPS_ALLOWED = {
    # `kubectl cluster-info` -- the deliberate split this suite documents:
    # the reachability probe sits ahead of, and separate from, the stage
    # discriminator beneath it, which is what makes the stage skip's message a
    # true statement whenever it fires.
    ("test_evidence_view.py", "stage_4_or_a_reason"),
    ("test_policy_scores.py", "stage_4_or_a_reason"),
    ("test_progressive_autonomy.py", "stage_5_or_a_reason"),
    ("test_gateway_config_drift.py", "live_gateway_config_text"),
    ("test_supervised.py", "reviewer_ui_route"),
    # Label-selector pod lookups and in-pod `exec` probes. A selector does not
    # 404, and an exec that cannot start means the dataplane is absent rather
    # than that an object is missing -- `absent_or_fail` has no `NotFound` to
    # match on for either.
    ("test_egress_policy.py", "_pod"),
    ("test_egress_policy.py", "_probe"),
    ("test_egress_policy.py", "datapath"),
    ("test_egress_policy.py", "test_dns_still_resolves_inside_every_agent_pod"),
    ("test_gateway_is_the_only_route.py", "_pod"),
    ("test_gateway_is_the_only_route.py", "_probe"),
    ("test_gateway_is_the_only_route.py", "datapath_ingress"),
}


def test_the_walk_detects_a_known_offender():
    """Vacuity control (M5 Task 1, V9): the walk in
    `test_every_e2e_caller_uses_the_shared_definition_and_not_a_local_copy`
    would pass green forever if `_reads_returncode` stopped matching anything
    -- an allow-list of zero real hits is indistinguishable from a broken
    detector. This asserts the helper still finds a source written to offend
    it.
    """
    offending = textwrap.dedent(
        """
        def probe():
            result = run(["kubectl", "get", "thing"])
            return result.returncode == 0
        """
    )
    assert _reads_returncode(ast.parse(offending)), (
        "the walk found nothing in a source written to offend it -- "
        "it is guarding nothing"
    )


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
        # M4 final review, suspected finding S3: is `"<name>" not found` still
        # broader than kubectl's NotFound? Measured rather than argued. The
        # pattern needs a QUOTED name immediately before `not found`, and the
        # near-miss kubectl messages that carry a quoted name -- Forbidden
        # (above), a missing context (above), an unrecognised resource type
        # (here) -- none of them put it there. The genuinely dangerous shape
        # would be an unquoted `not found`, which is `kubectl: command not
        # found` on the row above and is a failure. So the ruling is: the
        # pattern stays, because tightening it to require the literal
        # `(NotFound)` would key this helper on a parenthetical kubectl has
        # never promised to keep, in exchange for closing a class no message
        # in this repo populates.
        pytest.param(
            1,
            'error: the server doesn\'t have a resource type "widgets"',
            id="unrecognised-resource-type-quotes-the-name-but-is-not-NotFound",
        ),
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

    **M4 final review, finding L4 (second half): matching on the *name* was
    too weak.** The first version looked for `def _?absent_or_fail(`, so a copy
    under any other name -- `_exists`, `_installed`, `_or_skip` -- passed. The
    walk now also looks for the **shape**: a function that answers whether an
    object exists by reading a raw `.returncode`, which is the weaker
    discriminator M4-R14 retired, whatever it is called.
    """
    import pathlib

    e2e = pathlib.Path(__file__).resolve().parents[1] / "e2e"
    offenders = []
    for path in sorted(e2e.glob("test_*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.lstrip("_") == "absent_or_fail":
                offenders.append(f"{path.name}::{node.name} (by name)")
                continue
            # The retired shape, by structure rather than by name: a raw
            # `.returncode` comparison that *decides a skip* or that is
            # *returned as the answer*. Both are "I could not look" collapsing
            # into "there was nothing to check".
            #
            # The distinction matters, and getting it wrong first is how this
            # guard earned its own note: the same comparison inside an
            # `assert` is the CORRECT shape -- it fails loudly -- and two
            # fixtures in this suite legitimately contain one. A heuristic that
            # flagged any `.returncode` in a function whose name sounded like
            # an existence check reported those two as defects. Flagging a
            # correct construct teaches readers to edit the code to please the
            # test, which is the failure mode ruling R22 exists for.
            #
            # `_reads_returncode` itself is module-level and has its own
            # vacuity control below (V9): this walk finding zero offenders
            # forever if the helper silently stopped matching anything is
            # exactly the failure mode ruling R22 exists for, one level up.
            for inner in ast.walk(node):
                decides_a_skip = (
                    isinstance(inner, ast.If)
                    and _reads_returncode(inner.test)
                    and any(
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "skip"
                        for call in ast.walk(ast.Module(body=inner.body, type_ignores=[]))
                    )
                )
                is_the_answer = (
                    isinstance(inner, ast.Return)
                    and inner.value is not None
                    and _reads_returncode(inner.value)
                )
                if not (decides_a_skip or is_the_answer):
                    continue
                if (path.name, node.name) in _RAW_RETURNCODE_SKIPS_ALLOWED:
                    continue
                offenders.append(
                    f"{path.name}::{node.name} (a raw .returncode decides "
                    + ("a skip" if decides_a_skip else "the answer")
                    + ")"
                )
                break
    assert not offenders, (
        "these tests/e2e modules answer 'does this object exist?' from a raw "
        "`returncode` instead of importing tests.harness.kube.absent_or_fail, "
        "which is the one definition ruling M4-R14 left standing. A non-zero "
        "exit is not the same question as 'it does not exist' -- an "
        "unreachable API server, a wrong context and an expired credential all "
        f"land there, and all three become a silent skip: {offenders}"
    )


def test_the_shared_pipeline_reader_has_no_local_copies_either():
    """M4 final review, finding L4: the harder defect got the weaker protection.

    `absent_or_fail` got a repo walk. `pipeline_exporters` did not -- and it is
    the one whose defect actually survived, in one of three callers, across two
    whole tasks, while the finding that disproved it sat written down in this
    repo. `tests/harness/collector.py`'s own docstring calls that "the argument
    for one definition rather than three careful copies", and nothing stopped a
    fifth module writing `EXPORTER_ID in _deployed_collector_config()` again.

    The substring form is what is banned, by shape: a membership test of some
    `*EXPORTER_ID`/`*TEE_ID` constant against the *text* of the deployed config
    rather than against the parsed pipeline list.

    The ban is written as "the right-hand side is config text", not as "the
    right-hand side is not one of these three approved names". An approved-name
    list would have flagged the legitimate
    `OPENSEARCH_EXPORTER_ID in components` in
    `test_opensearch_trace_ingest.py`, where `components` is a list parsed out
    of the collector's own log lines and has nothing to do with the ConfigMap
    -- and the walk that flags correct code is the walk people edit the code
    to satisfy. Naming the defect instead means a new parsed source needs no
    permission, and a new `... in <the deployed YAML>` gets none.
    """
    import ast
    import pathlib

    def _is_config_text(expr) -> bool:
        """Does this expression evaluate to the deployed config as a string?"""
        if isinstance(expr, ast.Call):
            expr = expr.func
        name = None
        if isinstance(expr, ast.Name):
            name = expr.id
        elif isinstance(expr, ast.Attribute):
            name = expr.attr
        if name is None:
            return False
        name = name.lower()
        return any(word in name for word in ("config", "yaml", "stdout", "text", "raw"))

    e2e = pathlib.Path(__file__).resolve().parents[1] / "e2e"
    offenders = []
    for path in sorted(e2e.glob("test_*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not (len(node.ops) == 1 and isinstance(node.ops[0], ast.In)):
                continue
            left = node.left
            if not (isinstance(left, ast.Name) and left.id.endswith(("EXPORTER_ID", "TEE_ID"))):
                continue
            target = node.comparators[0]
            if not _is_config_text(target):
                continue
            offenders.append(
                f"{path.name}:{node.lineno}: `{left.id} in "
                f"{ast.unparse(target)}` reads the config as text"
            )
    assert not offenders, (
        "these tests/e2e modules test an exporter id against something other "
        "than a parsed pipeline exporter list. Measured on the live ConfigMap: "
        "`forward/opensearch` occurs 4 times in the deployed text and only one "
        "of those occurrences routes anything, so a substring test stays TRUE "
        "after the routing is deleted -- which is how this defect survived in "
        "test_opensearch_trace_ingest.py for two tasks after being measured "
        "and written down. Use tests.harness.collector.pipeline_exporters: "
        f"{offenders}"
    )
