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
"""
import os
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
