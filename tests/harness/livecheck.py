"""Weaver live-check, as the gate drives it (M9).

The collector sends the weaver-live-check pod (observability namespace) a COPY
of every span, metric and log. The pod serves its admin API on 4320:

  GET  /health    {"status":"ready"} once the registry is loaded
  POST /stop      stop receiving and build the report (returns when ready)
  GET  /report    the report; 409 while still receiving
  POST /shutdown  the process exits 0

`restart()` empties the pod's state, `report()` returns what it saw, and
`owned_violations()` picks out the findings that fail the gate.

**Why every call opens its own port-forward** (ephemeral local port, the same
pattern as `live.gateway_port_forward`) instead of using the operator forward
`scripts/start-demo.sh` keeps on `localhost:18400`. Both ways of emptying the
pod's state kill that forward: a rollout replaces the pod the forward is bound
to, and a container restart makes `kubectl port-forward` exit (measured). The
gate would then be unable to read its own report, and `scripts/smoke-test.sh`
creates no port-forwards of its own. A forward owned by the call cannot go
stale underneath it.

Failure style: plain `assert`/`RuntimeError` with a message, like the other
helpers here. An unreachable live-check is a failure, never a skip.
"""
from __future__ import annotations

import contextlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

KUBE_CONTEXT = "kind-northstar"
NAMESPACE = "observability"
DEPLOYMENT = "weaver-live-check"
ADMIN_PORT = 4320
SETTLE_S = 15.0

# Names this project owns, and the retired un-prefixed namespaces it must no
# longer emit. This mirrors the `sample_names` of the violation override in
# telemetry/live-check.toml; tests/unit/test_live_check_config.py pins the two
# together so they cannot drift.
OWNED_PREFIXES = ("northstar.", "policy.", "review.", "claim.", "settlement.")


def _kubectl(*args: str, timeout: float = 200.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, "-n", NAMESPACE, *args],
        capture_output=True, text=True, timeout=timeout,
    )


@contextlib.contextmanager
def _forward():
    """A port-forward to the admin port, owned and cleaned up by the caller."""
    proc = subprocess.Popen(
        ["kubectl", "--context", KUBE_CONTEXT, "-n", NAMESPACE, "port-forward",
         f"svc/{DEPLOYMENT}", f":{ADMIN_PORT}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    port = None
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            match = re.search(rf"Forwarding from 127\.0\.0\.1:(\d+) -> {ADMIN_PORT}", line)
            if match:
                port = int(match.group(1))
                break
        if not port:
            raise RuntimeError(
                f"weaver live-check is unreachable: no port-forward to svc/{DEPLOYMENT} "
                f"in {NAMESPACE}. Is deploy/{DEPLOYMENT} Ready? "
                "(./scripts/setup-kind.sh --stage 7)"
            )
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _request(base: str, method: str, path: str, timeout: float = 30.0) -> tuple[int, bytes]:
    request = urllib.request.Request(f"{base}{path}", method=method)
    if method == "POST":
        request.data = b""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise RuntimeError(f"weaver live-check: {method} {path} failed: {error}") from error


def healthy() -> bool:
    try:
        with _forward() as base:
            status, _ = _request(base, "GET", "/health", timeout=5.0)
        return status == 200
    except RuntimeError:
        return False


def restart(timeout_s: float = 180.0) -> None:
    """Empty the live-check's state: `kubectl rollout restart`, wait for the
    rollout to finish (old pod gone, so none of its state can answer), then wait
    for /health through a fresh forward."""
    result = _kubectl("rollout", "restart", f"deploy/{DEPLOYMENT}")
    if result.returncode != 0:
        raise RuntimeError(f"rollout restart of {DEPLOYMENT} failed:\n{result.stdout}{result.stderr}")
    result = _kubectl("rollout", "status", f"deploy/{DEPLOYMENT}", f"--timeout={int(timeout_s)}s",
                      timeout=timeout_s + 30)
    if result.returncode != 0:
        raise RuntimeError(f"{DEPLOYMENT} did not finish rolling out:\n{result.stdout}{result.stderr}")
    # `rollout status` returns once the NEW pod is available, not once the old
    # one is gone, and the old pod keeps answering the collector's connection
    # until it dies. Wait for exactly one pod.
    deadline = time.monotonic() + 90
    while True:
        pods = _kubectl("get", "pods", "-l", f"app={DEPLOYMENT}", "-o", "name").stdout.split()
        if len(pods) == 1:
            break
        if time.monotonic() > deadline:
            raise RuntimeError(f"{DEPLOYMENT}: the old pod is still there after 90s: {pods}")
        time.sleep(1.0)
    deadline = time.monotonic() + 60
    while not healthy():
        if time.monotonic() > deadline:
            raise RuntimeError(f"{DEPLOYMENT} rolled out but /health never answered ready")
        time.sleep(2.0)
    # Gone is not connected: the collector's gRPC exporter reconnects to the new
    # pod on its own schedule (keepalive pings in the exporter config bound it).
    # A short settle covers that; the gate drives minutes of traffic after this
    # call, a focused run does not.
    time.sleep(SETTLE_S)


def report(save_to: str | Path | None = None, timeout_s: float = 120.0) -> dict[str, Any]:
    """Stop receiving and fetch the report.

    Leaves the pod stopped: a later `report()` answers the same report, and the
    next gate run starts with `restart()`. The pod is NOT shut down here, so a
    failing run can be inspected.
    """
    with _forward() as base:
        status, body = _request(base, "POST", "/stop", timeout=timeout_s)
        assert status == 200, f"POST /stop answered {status}: {body[:200]!r}"
        deadline = time.monotonic() + timeout_s
        while True:
            status, body = _request(base, "GET", "/report", timeout=timeout_s)
            if status == 200:
                break
            assert status == 409 and time.monotonic() < deadline, (
                f"GET /report answered {status}: {body[:200]!r}"
            )
            time.sleep(1.0)
    parsed = json.loads(body)
    if save_to is not None:
        path = Path(save_to)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(parsed, indent=2, sort_keys=True) + "\n")
    return parsed


def sample_counts(report_: dict[str, Any]) -> dict[str, int]:
    """Entities the live-check received, by type (span, metric, log, ...)."""
    return dict(report_.get("statistics", {}).get("total_entities_by_type", {}))


def _walk(node: Any):
    """Every dict nested anywhere in the report."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def findings(report_: dict[str, Any]) -> list[dict[str, Any]]:
    """Every finding in the report, at any level. Each is a dict with `id`,
    `level`, `message`, `signal_type`, `signal_name` and a `context`."""
    return [
        advice
        for node in _walk(report_.get("samples", []))
        for advice in (node.get("all_advice") or [])
    ]


def judged_name(finding: dict[str, Any]) -> str:
    """The name the finding is about, which is what the live-check.toml override
    matches on: the attribute key for an attribute finding, the metric name for a
    metric one, the signal's own name otherwise."""
    context = finding.get("context") or {}
    return str(context.get("attribute_key") or context.get("metric_name")
               or finding.get("signal_name") or "")


def owned_violations(report_: dict[str, Any]) -> list[dict[str, Any]]:
    """Findings at `violation` whose judged name this project owns.

    `telemetry/live-check.toml` already lowers every finding about a name we do
    not own to `information`, so on a correct config every violation is owned.
    This filter is the second line: a config edit that let a foreign name
    through as a violation must not fail the gate on Kyverno's or Cilium's
    behalf.
    """
    return [
        f for f in findings(report_)
        if f.get("level") == "violation" and judged_name(f).startswith(OWNED_PREFIXES)
    ]


def span_names(report_: dict[str, Any]) -> set[str]:
    return {s["span"]["name"] for s in report_.get("samples", []) if "span" in s}


def top_foreign_findings(report_: dict[str, Any], limit: int = 5) -> list[tuple[str, int]]:
    """The most common messages for names this project does not own, for the
    gate's output: what the registry does not describe, and so does not judge."""
    counts: dict[str, int] = {}
    for finding in findings(report_):
        if not judged_name(finding).startswith(OWNED_PREFIXES):
            counts[finding["message"]] = counts.get(finding["message"], 0) + 1
    return sorted(counts.items(), key=lambda item: -item[1])[:limit]


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "restart":
        restart()
        print("weaver live-check restarted, empty")
    else:
        sys.exit("usage: python -m tests.harness.livecheck restart")
