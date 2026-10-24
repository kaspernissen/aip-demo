"""`scripts/start-demo.sh::verify_owned_forward` accepts a healthy foreign
port-forward instead of refusing outright, and does not fire the EXIT trap
on a genuine refusal. That fix shipped with no regression test of its own,
in a repo whose defect history is specifically about untested/inert guards --
a real risk worth covering regardless.

Behavioural, not structural: this runs the REAL
`scripts/start-demo.sh` against fake `kubectl`/`lsof`/`curl` on `PATH` (same
shape `test_stage4_config.py::test_start_demo_skips_stage_gated_surfaces_that_do_not_exist`
already uses for this script), and asserts on what actually happened --
process liveness, not text in the script.

One run covers both changed behaviours:

1. **Accept a healthy foreign holder.** `claims-agent`'s own `kubectl
   port-forward` is simulated failing to bind (fake kubectl exits 1
   immediately, the way real kubectl does when the local port is already
   taken) while something else already answers on that port (fake curl
   reports success for it). The script must print that it is accepting the
   foreign holder and must NOT exit -- it must continue on to verify the
   remaining surfaces.
2. **A refusal must not tear down forwards already established.**
   `settlement-agent` and `payments-mcp` are simulated as genuinely owned,
   healthy forwards (fake kubectl execs `sleep 300`, standing in for the
   long-running port-forward process; fake curl answers for their ports).
   `payments-api` is simulated exactly like `claims-agent` -- kubectl fails
   to bind -- but its foreign holder does NOT answer (fake curl fails for
   its port only), which is a genuine refusal: the script must exit non-zero.
   The two genuinely-owned forwards (`settlement-agent`, `payments-mcp`)
   must still be *running* after the script has exited -- proving the EXIT
   trap did not fire and kill them.

The stage-gated surfaces (OpenSearch Dashboards, reviewer-ui, Prometheus and
the rest) are reported absent throughout (fake kubectl's
`get svc/...` always exits 1), which keeps this test to exactly the four
unconditional stage-1 forwards the two behaviours above need and nothing
else.
"""
from __future__ import annotations

import os
import signal
import stat
import subprocess
import time
from pathlib import Path

from _start_demo_fakes import port_aware_fake_curl

REPO_ROOT = Path(__file__).resolve().parents[2]
START_DEMO = REPO_ROOT / "scripts" / "start-demo.sh"

_FAKE_KUBECTL = r"""#!/usr/bin/env bash
# `get svc/NAME -o name` -- every stage-gated surface is reported absent, so
# the script only ever forwards the four unconditional stage-1 surfaces this
# test exercises.
if [[ "$*" == *"get svc/"* ]]; then
  exit 1
fi
if [[ "$*" == *"port-forward"* ]]; then
  # claims-agent and payments-api: simulate the local port already being
  # held by something else -- the real `kubectl port-forward` fails to bind
  # immediately in that case, exactly like an absent Service does (the same
  # trick test_stage4_config.py's fake uses).
  if [[ "$*" == *"svc/claims-agent"* ]] || [[ "$*" == *"svc/payments-api"* ]]; then
    exit 1
  fi
  # settlement-agent and payments-mcp: a genuinely successful forward. Record
  # the real PID this fake ends up as (post-`exec`, so it is the PID the
  # `sleep` actually runs under) keyed by service name, so the test can check
  # it is still alive after start-demo.sh has exited.
  for arg in "$@"; do
    case "$arg" in
      svc/*) echo "$$" > "$PIDFILE_DIR/${arg#svc/}.pid" ;;
    esac
  done
  exec sleep 300
fi
exit 0
"""

# Ignores the actual port queried and reports every currently-alive PID on
# the machine as "listening" on it -- the same trick
# test_stage4_config.py's fake lsof uses. `verify_owned_forward`'s ownership
# check (`grep -x "${pid}"` against this output) then reduces to "is the PID
# this script launched still alive": true for settlement-agent/payments-mcp
# (their fake kubectl `exec`'d into a long-running `sleep 300`), false for
# claims-agent/payments-api (their fake kubectl already exited).
_FAKE_LSOF = r"""#!/usr/bin/env bash
echo "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME"
ps -axo pid= | while read -r p; do echo "fake $p user 1u IPv4 0x0 0t0 TCP *:1 (LISTEN)"; done
exit 0
"""

# Port-aware, from the shared `_start_demo_fakes` helper, which
# `test_stage4_config.py` also uses: this test needs curl to distinguish
# "answers" from "does not answer" per port, since that distinction is what
# separates an accepted healthy foreign holder from a genuine refusal. Every
# port answers except payments-api's (18080), which is the one genuine
# refusal.
_FAKE_CURL = port_aware_fake_curl(refusing_ports={"18080"})


def _write_fake(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_v11_accepts_a_healthy_foreign_forward_and_does_not_tear_down_the_others(tmp_path):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    for name, body in {
        "kubectl": _FAKE_KUBECTL, "lsof": _FAKE_LSOF, "curl": _FAKE_CURL,
    }.items():
        _write_fake(bindir, name, body)

    pidfile_dir = tmp_path / "pidfiles"
    pidfile_dir.mkdir()

    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["PIDFILE_DIR"] = str(pidfile_dir)

    result = subprocess.run(
        ["bash", str(START_DEMO)],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env, timeout=30,
    )
    output = result.stdout + result.stderr

    try:
        # --- Behaviour 1: the healthy foreign holder on claims-agent's port
        # is accepted, not treated as fatal -- the script must have continued
        # past it (verify_owned_forward returns 0 rather than exiting).
        assert "claims-agent" in output and "accepting it instead of starting a second forward" in output, (
            "expected start-demo.sh to report accepting claims-agent's "
            f"already-held, answering port instead of refusing it:\n{output}"
        )

        # --- The genuine refusal (payments-api's foreign holder does not
        # answer) must still have happened -- the script must exit non-zero,
        # and it must be THIS surface's refusal, not some earlier accident.
        assert result.returncode != 0, (
            f"expected start-demo.sh to exit non-zero on payments-api's "
            f"unresponsive foreign holder:\n{output}"
        )
        assert "payments-api" in output and "does not answer" in output, (
            f"expected the refusal to name payments-api and say its holder "
            f"does not answer:\n{output}"
        )

        # --- Behaviour 2, the more valuable half: settlement-agent's and
        # payments-mcp's own genuinely-owned forwards, verified live BEFORE
        # payments-api's refusal fired, must still be running AFTER the
        # script has exited -- proving the EXIT trap did not tear them down.
        for service in ("settlement-agent", "payments-mcp"):
            pidfile = pidfile_dir / f"{service}.pid"
            assert pidfile.exists(), (
                f"{service}'s fake kubectl never recorded a PID -- it did not "
                f"reach the point of forwarding at all:\n{output}"
            )
            pid = int(pidfile.read_text().strip())
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                raise AssertionError(
                    f"{service}'s forward (pid {pid}) is not running after "
                    "start-demo.sh exited on payments-api's refusal -- the EXIT "
                    "trap tore it down, which is exactly the regression this "
                    f"test guards against. Full output:\n{output}"
                )
    finally:
        # Clean up whatever background `sleep 300`s this test's run of the
        # real script left behind -- they are orphaned once the script that
        # spawned them has exited, and nothing else will reap them within
        # 300s.
        for pidfile in pidfile_dir.glob("*.pid"):
            try:
                pid = int(pidfile.read_text().strip())
                os.kill(pid, signal.SIGKILL)
            except (ValueError, ProcessLookupError):
                pass
