"""Shared helpers for tests that read or run `scripts/setup-kind.sh`.

`effective_script()` strips the shebang and every whole-line comment, so no
assertion over it can be satisfied by prose: a comment naming a step cannot
stand in for the step. Trailing (end-of-line) comments are deliberately not
stripped -- doing that safely needs a shell parser, since `#` is an ordinary
character inside strings -- so assertions must be specific enough that a
trailing comment cannot supply them.

`run_setup_kind()` runs the real script in a throwaway tree with fake `kind`,
`kubectl`, `docker`, `helm` and `pkill` ahead of it on PATH, and returns what
it invoked. Nothing it does touches a real cluster, registry, Docker, the
network, the machine's own port-forwards or the repo's `.env`.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_KIND = REPO_ROOT / "scripts" / "setup-kind.sh"
KIND_CONFIG = REPO_ROOT / "deploy" / "kind" / "cluster.yaml"


def effective_script() -> str:
    return "\n".join(
        line
        for line in SETUP_KIND.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


FAKES = {
    # `docker inspect` reports the registry running; `docker port` prints the
    # mappings listed in $FAKE_MAPPED_PORTS (space-separated host ports).
    "docker": r"""#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  inspect) echo "true"; exit 0 ;;
  port)
    echo "6443/tcp -> 127.0.0.1:41234"
    for p in ${FAKE_MAPPED_PORTS:-}; do echo "3${p}/tcp -> 0.0.0.0:${p}"; done
    exit 0 ;;
esac
exit 0
""",
    "kind": r"""#!/usr/bin/env bash
echo "kind $*" >> "$FAKE_LOG"
if [[ "$1 $2" == "get clusters" ]]; then
  [[ -n "${FAKE_CLUSTER_EXISTS:-}" ]] && echo northstar
  exit 0
fi
if [[ "$1 $2" == "get nodes" ]]; then echo northstar-control-plane; exit 0; fi
exit 0
""",
    # `cluster-info` fails unless $FAKE_CLUSTER_INFO_OK is set, which stops the
    # run right after cluster creation. Everything else succeeds silently.
    "kubectl": r"""#!/usr/bin/env bash
echo "kubectl $*" >> "$FAKE_LOG"
for arg in "$@"; do
  if [[ "$arg" == "cluster-info" && -z "${FAKE_CLUSTER_INFO_OK:-}" ]]; then exit 1; fi
done
cat >/dev/null 2>&1 || true
exit 0
""",
    "helm": r"""#!/usr/bin/env bash
echo "helm $*" >> "$FAKE_LOG"
exit 0
""",
    "pkill": r"""#!/usr/bin/env bash
echo "pkill $*" >> "$FAKE_LOG"
exit 1
""",
}

# Files the script reads itself (rather than handing to a faked tool).
# `scripts/rebuild-and-deploy.sh` is deliberately NOT copied: a run that gets
# past cluster creation dies at the first component build, so everything
# logged before that ran before any workload was deployed.
COPIED = [
    Path("scripts/setup-kind.sh"),
    Path("scripts/linkerd-certs.sh"),
    Path("deploy/kind/cluster.yaml"),
    Path("deploy/observability/collector.yaml"),
]


def run_setup_kind(tmp_path, *, args=(), cluster_exists=False, mapped_ports=(),
                   cluster_info_ok=False):
    """Run the real script against fakes. Returns (returncode, output, calls)."""
    tree = tmp_path / "tree"
    for relative in COPIED:
        destination = tree / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    # Only the one variable the script requires; no secret is involved.
    (tree / ".env").write_text("OPENTELEMETRY_COLLECTOR_CONTAINER_IMAGE=fake/collector:0\n")

    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    for name, body in FAKES.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    log = tmp_path / "invocations.log"
    log.write_text("")
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["FAKE_LOG"] = str(log)
    env["FAKE_MAPPED_PORTS"] = " ".join(str(p) for p in mapped_ports)
    if cluster_exists:
        env["FAKE_CLUSTER_EXISTS"] = "1"
    if cluster_info_ok:
        env["FAKE_CLUSTER_INFO_OK"] = "1"
    # Never let a real credential reach the subprocess.
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("DASH0_AUTH_TOKEN", None)

    proc = subprocess.run(
        ["bash", str(tree / "scripts" / "setup-kind.sh"), *args],
        capture_output=True, text=True, env=env, timeout=180, stdin=subprocess.DEVNULL,
    )
    return proc.returncode, proc.stdout + proc.stderr, log.read_text().splitlines()
