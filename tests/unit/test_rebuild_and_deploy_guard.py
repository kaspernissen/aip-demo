"""A15a guard: pins `scripts/rebuild-and-deploy.sh`'s missing-build-context
handling by RUNNING the script against a hermetic sandbox, not by parsing
its source text.

A15 (superseded by amendment A15a) let a default no-argument run silently
skip a component whose build context directory did not exist yet, printing
a yellow warning and continuing. That was correct while most of M1.1's five
build contexts genuinely did not exist (before Task 6 landed) and a bare
`./scripts/rebuild-and-deploy.sh` still had to be usable. The final
whole-branch review found that condition is gone — all five contexts have
existed since Task 6 — so the skip branch is unreachable in a healthy tree
today, and the day M1.2/M1.3 adds a sixth component to COMPONENTS, a
mistyped or half-created build context on a bare run would be silently
skipped rather than failing. A15a's ruling: a missing build context is a
hard error in every case, default run or an explicitly named component
alike.

**Second final-review round, finding 1:** the first version of this guard
asserted on the script's source text instead of its behaviour — a regex
`if \\[\\[ ! -d "\\$ROOT/\\$context" \\]\\]; then\\n(.*?)\\nfi` intended to
capture just the missing-context branch's body, but `.*?` is non-greedy
only in *how much* it matches, not in *which* `fi` it stops at when the
first candidate isn't reachable in `DOTALL` mode the way the author
expected — it actually ran all the way to the script's LAST column-0 `fi`
(38 lines later, past the `docker build` failure branch), and the
`exit 1` assertion was satisfied incidentally by that unrelated branch's
own `exit 1`. The reviewer proved this by replacing the hard error with a
bare silent `continue` — the single most dangerous reintroduction possible,
since it is byte-for-byte A15's retired shape — and got `2 passed in
0.00s`. A guard that cannot be broken by the exact defect it exists to
catch is not a guard.

This version instead runs the real script (or a temporarily corrupted copy
of it, in a RED demonstration) in an isolated sandbox with every build
context absent, and asserts on its actual exit code and diagnostic output —
for both calling conventions A15a covers.

**M3 final-review fix round — the companion-deployment half.** The tests
above all run against a sandbox where every build context is *missing*, so
the script hard-errors before it ever reaches a `kubectl rollout`. That left
the whole deploy half of this script unguarded, and the whole-branch review
found a real defect in it: `deploy/k8s/settlement-agent.yaml` and
`deploy/k8s/senior-settlement-agent.yaml` name the same mutable image tag
(`localhost:5001/northstar/settlement-agent:dev`), and this script restarted
only the first of them — so the senior identity went on serving the previous
build of the image the demo's whole argument says is byte-identical. The
second group of tests below gives the *present*-build-context path a sandbox
of its own (fake `docker`/`kubectl` that succeed and record what they were
called with) and pins the restart, plus a RED demonstration that emptying
`COMPANION_DEPLOYMENTS` really does redden it.
"""
import os
import re
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_RELATIVE_PATH = Path("scripts") / "rebuild-and-deploy.sh"

# Hermetic stand-ins for docker/kubectl. The script's prerequisite checks
# only need `docker ps ...` to report the local registry as running; every
# other invocation in these tests is unreachable if A15a's guard holds
# (build context is absent, so the script must exit before any real
# docker build/push or kubectl apply/rollout), so both fakes fail loudly if
# ever called with anything else -- that would mean the guard did NOT hold.
_FAKE_DOCKER = """#!/usr/bin/env bash
if [[ "$1" == "ps" ]]; then
  echo "kind-registry"
  exit 0
fi
echo "fake docker: unexpected invocation: docker $*" >&2
exit 1
"""

_FAKE_KUBECTL = """#!/usr/bin/env bash
echo "fake kubectl: unexpected invocation: kubectl $*" >&2
exit 1
"""


def _make_executable(path: Path, contents: str) -> None:
    path.write_text(contents)
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _sandbox(tmp_path: Path, script_text: str | None = None) -> tuple[Path, dict]:
    """Builds an isolated ROOT containing only: a copy of the script (real,
    unless `script_text` overrides it for a RED demonstration), a `.env`
    (A2 requires one to exist), and a fake docker/kubectl on PATH. It
    deliberately contains NONE of COMPONENTS' real build-context
    directories (`systems/payments-api`, etc.) -- since the script's own
    `ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"` line makes ROOT track wherever
    the script physically lives, copying it into an otherwise-empty
    directory reproduces "every component's build context is absent" with
    nothing else mocked or special-cased.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script_copy = scripts_dir / "rebuild-and-deploy.sh"
    real_text = (REPO_ROOT / SCRIPT_RELATIVE_PATH).read_text()
    script_copy.write_text(script_text if script_text is not None else real_text)
    script_copy.chmod(0o755)
    (tmp_path / ".env").write_text("")

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    _make_executable(bin_dir / "docker", _FAKE_DOCKER)
    _make_executable(bin_dir / "kubectl", _FAKE_KUBECTL)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return script_copy, env


def _run(script_copy: Path, env: dict, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script_copy), *args],
        cwd=script_copy.parent.parent,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_missing_build_context_is_a_hard_error_on_a_default_run(tmp_path):
    """A15a: 'a missing build context is a hard error in every case, default
    run or explicitly named component'. No arguments -> the script walks
    COMPONENTS in order (payments-api first) and must fail hard on the
    first one, naming both the component and its absent path, rather than
    skipping it and continuing to the rest."""
    script_copy, env = _sandbox(tmp_path)
    result = _run(script_copy, env, [])

    assert result.returncode != 0, (
        "a default run against a sandbox where every build context is "
        "missing must exit non-zero (A15a); got exit 0.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "payments-api" in result.stderr, (
        "the failure must name the component whose build context is "
        f"missing; stderr was:\n{result.stderr}"
    )
    assert "systems/payments-api" in result.stderr, (
        "the failure must name the absent build-context path; stderr was:\n"
        f"{result.stderr}"
    )
    assert "Rebuilt and deployed" not in result.stdout, (
        "no component may be reported as deployed once the first one's "
        f"build context is missing and the script hard-errors; stdout:\n"
        f"{result.stdout}"
    )


def test_missing_build_context_is_a_hard_error_for_an_explicitly_named_component(tmp_path):
    """Same ruling, the other calling convention: naming a component
    explicitly must carry no different -- let alone more forgiving --
    behaviour than the default run. Uses a different component
    (claims-agent) than the default-run test, so the two tests cannot pass
    each other's assertions by accident."""
    script_copy, env = _sandbox(tmp_path)
    result = _run(script_copy, env, ["claims-agent"])

    assert result.returncode != 0, (
        "naming a component whose build context is missing must exit "
        f"non-zero; got exit 0.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "claims-agent" in result.stderr, (
        f"the failure must name the requested component; stderr:\n{result.stderr}"
    )
    assert "agents/claims-agent" in result.stderr, (
        f"the failure must name the absent build-context path; stderr:\n{result.stderr}"
    )


def test_guard_goes_red_against_a15s_retired_silent_continue(tmp_path):
    """Proves this guard actually guards: reintroducing A15's retired
    shape -- replace the hard `exit 1` with a bare, silent `continue` --
    must make the *behavioural* assertions above fail, not merely change
    unrelated output. This test corrupts an in-sandbox COPY of the script
    (never the real file on disk) and asserts on the corrupted copy
    directly, so it is itself deterministic and needs no manual RED/GREEN
    dance to stay meaningful over time."""
    real_text = (REPO_ROOT / SCRIPT_RELATIVE_PATH).read_text()
    marker = (
        'if [[ ! -d "$ROOT/$context" ]]; then\n'
        '    echo -e "${RED}Error: ${component}\'s build context ${context} '
        'does not exist${NC}" >&2\n'
        "    exit 1\n"
        "  fi"
    )
    assert marker in real_text, (
        "expected scripts/rebuild-and-deploy.sh's missing-build-context "
        "block to match this test's known-good text exactly -- if the "
        "script's wording changed, update `marker` above alongside it"
    )
    corrupted_text = real_text.replace(
        marker,
        'if [[ ! -d "$ROOT/$context" ]]; then\n'
        "    continue\n"
        "  fi",
    )
    assert corrupted_text != real_text  # the replace above must have applied

    script_copy, env = _sandbox(tmp_path, script_text=corrupted_text)
    result = _run(script_copy, env, [])

    assert result.returncode == 0, (
        "sanity check on the corruption itself: A15's retired silent "
        "`continue` shape should let the script run to completion (exit "
        f"0) rather than error -- got exit {result.returncode}, meaning "
        "this test's corruption doesn't reproduce the reviewer's finding.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "payments-api" not in result.stderr, (
        "the corrupted (A15-shaped) script printed no diagnostic naming "
        "the missing component, confirming the two behavioural tests above "
        "would correctly go RED against this exact reintroduction:\n"
        f"stderr:\n{result.stderr}"
    )


# --- The deploy half: companion deployments (M3 final-review fix round) ------
#
# A second sandbox shape. The one above deliberately has NO build contexts, so
# the script exits before deploying anything; this one has the requested
# component's context present and fake docker/kubectl that succeed, so the
# script runs all the way through its build/push/apply/restart sequence and
# every invocation is recorded for assertion.

_RECORDING_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_CMD_LOG"
if [[ "$1" == "ps" ]]; then
  echo "kind-registry"
fi
exit 0
"""

_RECORDING_KUBECTL = """#!/usr/bin/env bash
echo "kubectl $*" >> "$FAKE_CMD_LOG"
exit 0
"""

# `  "name:context:namespace"` lines inside the COMPONENTS array. Anchored on
# the opening `COMPONENTS=(` and stopped at the closing `)` so the identically
# shaped COMPANION_DEPLOYMENTS array below it cannot be swept up.
_COMPONENTS_BLOCK_RE = re.compile(r"^COMPONENTS=\(\n(.*?)^\)", re.M | re.S)
_COMPONENT_ENTRY_RE = re.compile(r'^\s*"([^:"]+):([^:"]+):([^:"]+)"\s*$', re.M)


def _component_contexts() -> list[str]:
    """Every build context `scripts/rebuild-and-deploy.sh` walks on a bare run.

    Read from the script so a sandbox built from it covers whatever COMPONENTS
    actually holds today, rather than whatever it held when the test was
    written -- see `test_a_bare_full_run_also_restarts_the_companion` for the
    drift that prompted this.
    """
    text = (REPO_ROOT / SCRIPT_RELATIVE_PATH).read_text()
    block = _COMPONENTS_BLOCK_RE.search(text)
    assert block, (
        "could not find a `COMPONENTS=(` ... `)` array in "
        f"{SCRIPT_RELATIVE_PATH} -- this helper builds the sandbox from it, so "
        "a rewritten array must be re-read here rather than silently yielding "
        "an empty context list"
    )
    contexts = [m.group(2) for m in _COMPONENT_ENTRY_RE.finditer(block.group(1))]

    # Parse sanity, derived rather than written down. A floor (`>= 7`, with
    # "there were 8 when this was written" in its message) stood here and had
    # the defect the whole change was against: a silent drop to seven passed,
    # and the number would have to be edited by hand on the ninth component.
    #
    # What is checked instead is that the parse agrees with the array it read
    # -- every non-blank, non-comment line inside `COMPONENTS=( ... )` must
    # have yielded a context -- and that every context is a real directory. A
    # regex that stops matching leaves lines unaccounted for; a typo'd path
    # leaves a context that does not exist. Neither needs a number.
    entries = [
        line for line in block.group(1).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert len(contexts) == len(entries), (
        f"{SCRIPT_RELATIVE_PATH}'s COMPONENTS array holds {len(entries)} "
        f"entry line(s) but only {len(contexts)} parsed as "
        '"name:context:namespace": the parse is missing some, so a sandbox '
        "built from it would silently stop covering a component.\n"
        f"  unparsed: {[l for l in entries if not _COMPONENT_ENTRY_RE.match(l)]}"
    )
    assert contexts, (
        f"{SCRIPT_RELATIVE_PATH}'s COMPONENTS array parsed empty, so the "
        "sandbox below would be built with no build contexts and every test "
        "using it would pass vacuously"
    )
    absent = [c for c in contexts if not (REPO_ROOT / c).is_dir()]
    assert not absent, (
        f"{SCRIPT_RELATIVE_PATH} names build context(s) that do not exist: "
        f"{absent}. A bare run of that script would hard-error on them "
        "(amendment A15a), so this is a real defect in the script rather "
        "than in this parse."
    )
    return contexts


def _deploying_sandbox(
    tmp_path: Path, build_contexts: list[str], script_text: str | None = None
) -> tuple[Path, dict, Path]:
    """A sandbox the script can run to completion in.

    `build_contexts` are paths relative to ROOT that must exist (the script's
    A15a check is `-d "$ROOT/$context"`, so an empty directory is enough --
    `docker build` never really runs). Returns the script copy, the env, and
    the path of the log both fakes append their full argv to.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script_copy = scripts_dir / "rebuild-and-deploy.sh"
    real_text = (REPO_ROOT / SCRIPT_RELATIVE_PATH).read_text()
    script_copy.write_text(script_text if script_text is not None else real_text)
    script_copy.chmod(0o755)
    (tmp_path / ".env").write_text("")
    # The manifests `kubectl apply -f` names. kubectl is faked, so the contents
    # are irrelevant -- but the directory keeps the sandbox honest about what
    # the script actually references.
    (tmp_path / "deploy" / "k8s").mkdir(parents=True)
    for context in build_contexts:
        (tmp_path / context).mkdir(parents=True)

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    _make_executable(bin_dir / "docker", _RECORDING_DOCKER)
    _make_executable(bin_dir / "kubectl", _RECORDING_KUBECTL)

    log = tmp_path / "fake-commands.log"
    log.write_text("")

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["FAKE_CMD_LOG"] = str(log)
    return script_copy, env, log


def test_rebuilding_settlement_agent_also_restarts_its_companion_senior_deployment(
    tmp_path,
):
    """The defect the whole-branch review found: both settlement Deployments
    name the mutable tag `localhost:5001/northstar/settlement-agent:dev`, so a
    push under that tag that restarts only one of them leaves the other
    serving the previous build -- two pods of "the same image" disagreeing,
    which is the exact premise M3's "the approval changes the caller, not the
    code" argument rests on.

    Behavioural, not a source grep: the script is really run and the real
    `kubectl` argv is asserted.
    """
    script_copy, env, log = _deploying_sandbox(
        tmp_path, ["agents/settlement-agent"]
    )
    result = _run(script_copy, env, ["settlement-agent"])
    assert result.returncode == 0, (
        "the script should run to completion in this sandbox (build context "
        f"present, fakes succeed); stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    calls = log.read_text().splitlines()
    assert any(
        "rollout restart deployment/settlement-agent" in call for call in calls
    ), f"the component's own restart is missing entirely: {calls}"
    assert any(
        "rollout restart deployment/senior-settlement-agent" in call
        for call in calls
    ), (
        "deployment/senior-settlement-agent was NOT restarted after a new "
        "settlement-agent image was pushed under the tag both Deployments "
        f"name, so it keeps serving the previous build: {calls}"
    )
    assert any(
        "rollout status deployment/senior-settlement-agent" in call
        for call in calls
    ), (
        "the companion was restarted but never waited on, so the script can "
        f"return while the old pod is still serving: {calls}"
    )
    # It is a restart, not an apply: senior-settlement-agent's manifest is
    # setup-kind.sh's to apply, and this script must not take that over.
    assert not any(
        "apply -f" in call and "senior-settlement-agent" in call for call in calls
    ), f"this script must not apply senior-settlement-agent's manifest: {calls}"


def test_a_bare_full_run_also_restarts_the_companion(tmp_path):
    """The other calling convention, and the likelier one before a demo: no
    arguments at all. Every component's build context is present here, so the
    script walks the whole COMPONENTS list.

    The context list is **read out of the script** rather than restated here.
    It was restated, as seven hard-coded paths, until M4 Task 4 appended an
    eighth component -- at which point this test failed with
    "eval-scorer's build context platform/eval-scorer does not exist", which is
    the sandbox being stale rather than anything being wrong with the script.
    A stale sandbox that fails loudly is the benign version; the malign one is
    a sandbox that stops covering a component and says nothing, which is what
    would have happened had the loop been over whatever happened to exist.
    `_components()` asserts it parsed something, so a rewritten COMPONENTS
    block reddens here instead of emptying this test.
    """
    script_copy, env, log = _deploying_sandbox(tmp_path, _component_contexts())
    result = _run(script_copy, env, [])
    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    calls = log.read_text().splitlines()
    assert any(
        "rollout restart deployment/senior-settlement-agent" in call
        for call in calls
    ), f"a bare full run left the senior identity on the previous build: {calls}"


def test_the_companion_guard_goes_red_when_the_mapping_is_emptied(tmp_path):
    """Proves the two tests above actually guard, the same way
    `test_guard_goes_red_against_a15s_retired_silent_continue` does for A15a:
    empty `COMPANION_DEPLOYMENTS` -- byte-for-byte the shape this script had
    before the M3 fix round -- and the companion restart must disappear while
    everything else still succeeds."""
    real_text = (REPO_ROOT / SCRIPT_RELATIVE_PATH).read_text()
    marker = (
        "COMPANION_DEPLOYMENTS=(\n"
        '  "settlement-agent:senior-settlement-agent:northstar-agents"\n'
        ")"
    )
    assert marker in real_text, (
        "expected scripts/rebuild-and-deploy.sh's COMPANION_DEPLOYMENTS list "
        "to match this test's known-good text exactly -- if a companion was "
        "added or the formatting changed, update `marker` above alongside it"
    )
    corrupted_text = real_text.replace(marker, "COMPANION_DEPLOYMENTS=()")
    assert corrupted_text != real_text

    script_copy, env, log = _deploying_sandbox(
        tmp_path, ["agents/settlement-agent"], script_text=corrupted_text
    )
    result = _run(script_copy, env, ["settlement-agent"])
    assert result.returncode == 0, (
        "sanity check on the corruption: an empty companion list should still "
        f"deploy the component itself.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    calls = log.read_text().splitlines()
    assert any(
        "rollout restart deployment/settlement-agent" in call for call in calls
    ), f"the corruption broke more than it should have: {calls}"
    assert not any(
        "senior-settlement-agent" in call for call in calls
    ), (
        "the corrupted script still restarted the companion, so the tests "
        f"above would not go red against this reintroduction: {calls}"
    )
