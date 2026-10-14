"""Guards `scripts/rebuild-and-deploy.sh`'s missing-build-context handling by
RUNNING the script against a hermetic sandbox, not by parsing its source text.

A missing build context must be a hard error in every case, default run or an
explicitly named component alike -- a bare `./scripts/rebuild-and-deploy.sh`
run must not silently skip a component whose build context does not exist and
continue on to the rest.

Behavioural rather than textual, because a source-text check is easy to
satisfy without the underlying behaviour holding. A regex meant to capture
just the missing-context branch's body
(`if \\[\\[ ! -d "\\$ROOT/\\$context" \\]\\]; then\\n(.*?)\\nfi`) looks
precise, but `.*?` is non-greedy only in *how much* it matches, not in *which*
`fi` it stops at -- in `DOTALL` mode it can run past the intended branch all
the way to the script's last column-0 `fi`, and an `exit 1` assertion can then
be satisfied incidentally by a wholly unrelated branch's own `exit 1`.
Replacing the hard error with a bare silent `continue` -- the most dangerous
possible regression -- would leave a check shaped like that green. A guard
that cannot be broken by the exact defect it exists to catch is not a guard.

This version instead runs the real script (or a temporarily corrupted copy of
it, in a RED demonstration) in an isolated sandbox with every build context
absent, and asserts on its actual exit code and diagnostic output -- for both
calling conventions.

**The deploy half.** The tests above all run against a sandbox where every
build context is *missing*, so the script hard-errors before it ever reaches a
`kubectl rollout`. The second group of tests below gives the *present*-build-
context path a sandbox of its own (fake `docker`/`kubectl` that succeed and
record what they were called with). There is no companion-deployment mechanism
any more: M8 retired the one second Deployment of the settlement-agent image.
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
# other invocation in these tests is unreachable if the missing-build-context
# guard holds (build context is absent, so the script must exit before any
# real docker build/push or kubectl apply/rollout), so both fakes fail loudly
# if ever called with anything else -- that would mean the guard did NOT hold.
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
    (the script requires one to exist), and a fake docker/kubectl on PATH. It
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
    """A missing build context is a hard error in every case, default run or
    explicitly named component alike. No arguments -> the script walks
    COMPONENTS in order (payments-api first) and must fail hard on the
    first one, naming both the component and its absent path, rather than
    skipping it and continuing to the rest."""
    script_copy, env = _sandbox(tmp_path)
    result = _run(script_copy, env, [])

    assert result.returncode != 0, (
        "a default run against a sandbox where every build context is "
        "missing must exit non-zero; got exit 0.\n"
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
    """Same rule, the other calling convention: naming a component
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
    """Proves this guard actually guards: replacing the hard `exit 1` with a
    bare, silent `continue` -- the shape this guard exists to catch --
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
        "sanity check on the corruption itself: a silent `continue` should "
        "let the script run to completion (exit 0) rather than error -- got "
        f"exit {result.returncode}, meaning this test's corruption doesn't "
        "reproduce the defect being guarded against.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "payments-api" not in result.stderr, (
        "the corrupted (silently-skipping) script printed no diagnostic "
        "naming the missing component, confirming the two behavioural tests "
        "above would correctly go RED against this exact reintroduction:\n"
        f"stderr:\n{result.stderr}"
    )


# --- The deploy half ---------------------------------------------------------
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
# `docker build` is faked here, so nothing ever reads the build context's
# `.catalog-stage/` directory the way a real `COPY ./catalog-stage ...` in
# platform/backstage/Dockerfile would -- and the real script `rm -rf`s it
# right after this call returns, so by the time a test could inspect the
# sandbox on disk, the directory is already gone (the same "copy in, always
# clean up" cleanup agent-card.json gets). Snapshotting it HERE, at the
# moment a real `docker build` would have read it, is the only
# point in this fake's lifecycle where the directory is guaranteed to still
# exist. Only runs when a test opts in via $CATALOG_STAGE_SNAPSHOT, so every
# other sandbox using this same fake is unaffected.
if [[ "$1" == "build" && -n "${CATALOG_STAGE_SNAPSHOT:-}" ]]; then
  build_context="${@: -1}"
  if [[ -d "$build_context/.catalog-stage" ]]; then
    cp -r "$build_context/.catalog-stage" "$CATALOG_STAGE_SNAPSHOT"
  fi
fi
exit 0
"""

_RECORDING_KUBECTL = """#!/usr/bin/env bash
echo "kubectl $*" >> "$FAKE_CMD_LOG"
exit 0
"""

# `  "name:context:namespace"` lines inside the COMPONENTS array. Anchored on
# the opening `COMPONENTS=(` and stopped at the closing `)` so the identically
# shaped arrays elsewhere in the script cannot be swept up.
_COMPONENTS_BLOCK_RE = re.compile(r"^COMPONENTS=\(\n(.*?)^\)", re.M | re.S)
_COMPONENT_ENTRY_RE = re.compile(r'^\s*"([^:"]+):([^:"]+):([^:"]+)"\s*$', re.M)


def _component_contexts() -> list[str]:
    """Every build context `scripts/rebuild-and-deploy.sh` walks on a bare run.

    Read from the script so a sandbox built from it covers whatever COMPONENTS
    actually holds today, rather than whatever it held when the test was
    written -- see `test_the_catalog_stage_holds_exactly_the_real_sources_it_should_have_copied` for the
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
        f"{absent}. A bare run of that script would hard-error on them, so "
        "this is a real defect in the script rather than in this parse."
    )
    return contexts


def _component_name_context_pairs() -> list[tuple[str, str]]:
    """`(name, context)` for every `COMPONENTS` entry, in order -- the mapping
    the catalog-staging test below needs (it must know which context is
    "backstage" and which staged file each OTHER context should have produced)
    and `_component_contexts()` throws away by returning only the context."""
    text = (REPO_ROOT / SCRIPT_RELATIVE_PATH).read_text()
    block = _COMPONENTS_BLOCK_RE.search(text)
    assert block, f"could not find a `COMPONENTS=(` ... `)` array in {SCRIPT_RELATIVE_PATH}"
    return [
        (m.group(1), m.group(2))
        for m in _COMPONENT_ENTRY_RE.finditer(block.group(1))
    ]


def _deploying_sandbox(
    tmp_path: Path, build_contexts: list[str], script_text: str | None = None
) -> tuple[Path, dict, Path]:
    """A sandbox the script can run to completion in.

    `build_contexts` are paths relative to ROOT that must exist (the script's
    check is `-d "$ROOT/$context"`, so an empty directory is enough -- `docker
    build` never really runs). Returns the script copy, the env, and
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
        # When "backstage" is among the targets, the script's own staging step
        # `cp`s every OTHER component's `catalog-info.yaml` (plus the
        # repo-root one) into platform/backstage/.catalog-stage/ before
        # building. The files must exist or that `cp` fails and `set -e`
        # kills the run before it ever reaches this test's actual assertions.
        #
        # Each stub carries content derived from its own context rather than a
        # byte-identical placeholder, so that a correctly-staged
        # `.catalog-stage/` (each file copied from its own real source) can be
        # told apart from one where a `cp` source argument names the wrong
        # path (e.g. the ROOT System stub instead of the per-component one) --
        # both would otherwise produce identical files here. Distinct,
        # source-derived content lets a test assert each staged file's content
        # traces back to the ONE source it should have been copied from.
        (tmp_path / context / "catalog-info.yaml").write_text(f"# {context}\n")
    (tmp_path / "catalog-info.yaml").write_text("# root system\n")
    # The System's own TechDocs source. The real repo has mkdocs.yml and docs/
    # at its root and the script stages both beside system-catalog-info.yaml,
    # unconditionally -- a System entity that carries
    # `backstage.io/techdocs-ref: dir:.` and finds no mkdocs.yml there renders
    # an empty Docs tab, which is a silent failure with nothing else to catch
    # it. Unconditional means a sandbox without these files makes the script
    # fail loudly, so they belong here, with source-derived content for the
    # same reason the per-component stubs above have it.
    (tmp_path / "mkdocs.yml").write_text("# root mkdocs\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.md").write_text("# root docs index\n")

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


def test_rebuilding_settlement_agent_restarts_one_deployment_and_no_other(tmp_path):
    """M8 retired senior-settlement-agent, the only second Deployment that ran
    the settlement-agent image. Rebuilding that image restarts
    `deployment/settlement-agent` and nothing else, and the script no longer
    knows the name of a retired identity.

    Behavioural, not a source grep: the script is really run and the real
    `kubectl` argv is asserted.
    """
    script_copy, env, log = _deploying_sandbox(
        tmp_path, ["agents/settlement-agent"]
    )
    result = _run(script_copy, env, ["settlement-agent"])
    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    calls = log.read_text().splitlines()
    assert any(
        "rollout restart deployment/settlement-agent" in call for call in calls
    ), f"the component's own restart is missing entirely: {calls}"
    restarts = [c for c in calls if "rollout restart" in c]
    assert len(restarts) == 1, f"expected exactly one restart, got {restarts}"
    assert not any("senior" in call for call in calls), calls


def test_the_catalog_stage_holds_exactly_the_real_sources_it_should_have_copied(
    tmp_path,
):
    """A `.catalog-stage/` built by correctly copying each source must be
    distinguishable from one built by a `cp` that copied the WRONG source into
    every destination -- e.g. the root System stub substituted in for every
    per-component source. If every stub carried the same content, that bug
    would ship a catalog with zero real components and nothing here could
    tell.

    Each source is distinct (`f"# {context}\\n"`, the root gets
    `"# root system\\n"`), so this asserts `.catalog-stage/` -- snapshotted by
    the fake `docker build` at the one moment it still exists, since the real
    script `rm -rf`s it right after -- holds exactly one file per
    NON-backstage component, each matching that component's own source, plus
    `system-catalog-info.yaml` matching the repo root's.
    """
    pairs = _component_name_context_pairs()
    contexts = [context for _, context in pairs]
    backstage_context = next(
        context for name, context in pairs if name == "backstage"
    )
    other_contexts = [context for name, context in pairs if name != "backstage"]

    script_copy, env, log = _deploying_sandbox(tmp_path, contexts)
    snapshot_dir = tmp_path / "catalog-stage-snapshot"
    env["CATALOG_STAGE_SNAPSHOT"] = str(snapshot_dir)

    result = _run(script_copy, env, [])
    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    assert snapshot_dir.is_dir(), (
        "the fake docker never snapshotted .catalog-stage/ -- either "
        "'backstage' was not built in this run, or the staging step no "
        "longer creates that directory before `docker build`"
    )

    system_stub = snapshot_dir / "system-catalog-info.yaml"
    assert system_stub.read_text() == "# root system\n", (
        f"system-catalog-info.yaml holds {system_stub.read_text()!r}, not the "
        "repo root's own stub -- the staging step copied the wrong source"
    )

    # The System's TechDocs source, staged beside its entity. Asserted on
    # content, not just existence, for the same reason as everything else in
    # this test: `cp` copying the wrong file is the failure being guarded
    # against, and a bare `is_file()` cannot tell the two apart.
    staged_mkdocs = snapshot_dir / "mkdocs.yml"
    assert staged_mkdocs.is_file(), (
        "the repo root's mkdocs.yml was never staged into .catalog-stage/. "
        "The System entity carries `backstage.io/techdocs-ref: dir:.`, which "
        "resolves to this directory, so without it the system's Docs tab -- "
        "the solution documentation -- renders empty while every component's "
        "own docs still work, and nothing else in the repo notices."
    )
    assert staged_mkdocs.read_text() == "# root mkdocs\n", (
        f".catalog-stage/mkdocs.yml holds {staged_mkdocs.read_text()!r}, not "
        "the repo root's own -- the staging step copied the wrong source"
    )
    staged_index = snapshot_dir / "docs" / "index.md"
    assert staged_index.is_file(), (
        "the repo root's docs/ was never staged into .catalog-stage/. "
        "mkdocs.yml declares `docs_dir: docs` relative to itself, so an "
        "mkdocs.yml with no docs/ beside it builds nothing."
    )
    assert staged_index.read_text() == "# root docs index\n", (
        f".catalog-stage/docs/index.md holds {staged_index.read_text()!r}, "
        "not the repo root's own -- the staging step copied the wrong source"
    )

    for context in other_contexts:
        staged = snapshot_dir / context / "catalog-info.yaml"
        assert staged.is_file(), (
            f"{context}/catalog-info.yaml was never staged into "
            f".catalog-stage/ -- the loop over COMPONENTS skipped it"
        )
        expected = f"# {context}\n"
        assert staged.read_text() == expected, (
            f".catalog-stage/{context}/catalog-info.yaml holds "
            f"{staged.read_text()!r}, expected {expected!r} (that context's "
            "own source) -- this is exactly the byte-identical-fixture shape "
            "that would let a typo'd `cp` source substitute the wrong file "
            "and go unnoticed"
        )

    # And nothing under backstage's OWN context was staged -- the loop
    # explicitly `continue`s past it.
    backstage_self_stage = snapshot_dir / backstage_context
    assert not backstage_self_stage.exists(), (
        f"backstage's own context ({backstage_context}) was staged into its "
        "own .catalog-stage/ -- the loop's `[[ \"$cname\" == \"backstage\" ]] "
        "&& continue` guard should have skipped it"
    )
