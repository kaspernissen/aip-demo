"""Weaver live-check is the gate's, not a demo surface: when start-demo.sh's
18400 forward cannot bind and whatever holds 18400 does not answer, start-demo
warns in yellow and carries on (the other forwards stay up, the URL summary is
printed, exit 0). Before the fix `verify_owned_forward` exited 1 there.

Runs the real scripts/start-demo.sh against fake kubectl/lsof/curl, the shape
test_start_demo_v11.py uses.
"""
from __future__ import annotations

import os
import signal
import stat
import subprocess
import time
from pathlib import Path

from _start_demo_fakes import port_aware_fake_curl
from test_start_demo_v11 import _FAKE_LSOF

REPO_ROOT = Path(__file__).resolve().parents[2]
START_DEMO = REPO_ROOT / "scripts" / "start-demo.sh"

# Only svc/weaver-live-check exists besides the stage-1 surfaces; its forward
# fails to bind (exit 1), every other forward is a long-running sleep.
_FAKE_KUBECTL = r"""#!/usr/bin/env bash
echo "$*" >> "$PIDFILE_DIR/calls.log"
if [[ "$*" == *"get svc/weaver-live-check"* ]]; then exit 0; fi
if [[ "$*" == *" get "* ]]; then exit 1; fi
if [[ "$*" == *"port-forward"* ]]; then
  if [[ "$*" == *"svc/weaver-live-check"* ]]; then exit 1; fi
  for arg in "$@"; do
    case "$arg" in svc/*) echo "$$" > "$PIDFILE_DIR/${arg#svc/}.pid" ;; esac
  done
  exec sleep 300
fi
exit 0
"""


def _write_fake(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_an_unbindable_weaver_forward_is_a_warning_not_a_failed_start(tmp_path):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    for name, body in {
        "kubectl": _FAKE_KUBECTL, "lsof": _FAKE_LSOF,
        "curl": port_aware_fake_curl(refusing_ports={"18400"}),
    }.items():
        _write_fake(bindir, name, body)
    pidfiles = tmp_path / "pidfiles"
    pidfiles.mkdir()
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", PIDFILE_DIR=str(pidfiles))
    proc = subprocess.Popen(
        ["bash", str(START_DEMO)], cwd=REPO_ROOT, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True,
    )
    lines: list[str] = []
    try:
        # A successful start blocks on `wait` after the summary, so read to the
        # last summary line (or the script's exit) and then stop it.
        deadline = time.time() + 30
        for line in proc.stdout:
            lines.append(line)
            if "Press Ctrl+C" in line or time.time() > deadline:
                break
        output = "".join(lines)
        assert proc.poll() is None, f"start-demo must not fail on weaver (exit {proc.returncode}):\n{output}"
        calls = (pidfiles / "calls.log").read_text()
        assert "rollout restart deployment/weaver-live-check" in calls, calls
        assert "weaver live-check: the 18400 forward never bound" in output, output
        assert "http://localhost:18400" not in output, "a dead weaver forward must not be advertised"
        assert "http://localhost:19001" in output, f"the URL summary was not reached:\n{output}"
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for pidfile in pidfiles.glob("*.pid"):
            try:
                os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)
            except (ValueError, ProcessLookupError):
                pass
