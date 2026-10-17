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
import queue
import re
import subprocess
import sys
import threading
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
FORWARD_TIMEOUT_S = 30.0
# Spans reach live-check up to ~6 s after the code emitted them (the SDK's batch
# processor, then the collector's); /stop before that loses the last test's spans.
FLUSH_S = 10.0
# Where restart() records which pod and container it left running, so report()
# can tell that nothing restarted underneath the run.
STATE_PATH = Path(__file__).resolve().parents[2] / ".demo" / "m9-live-check-state.json"

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
    # readline() blocks, so a kubectl that prints nothing would hang the gate.
    # A reader thread feeds a queue and the deadline is enforced on the queue.
    lines: queue.Queue = queue.Queue()

    def _pump() -> None:
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=_pump, daemon=True).start()
    port = None
    deadline = time.monotonic() + FORWARD_TIMEOUT_S
    try:
        while port is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                break
            if line is None:
                break
            match = re.search(rf"Forwarding from 127\.0\.0\.1:(\d+) -> {ADMIN_PORT}", line)
            if match:
                port = int(match.group(1))
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


def _pod_identity() -> dict[str, Any]:
    """The live-check pod's UID and its container's restartCount."""
    result = _kubectl("get", "pods", "-l", f"app={DEPLOYMENT}", "-o", "json")
    if result.returncode != 0:
        raise RuntimeError(f"could not read the {DEPLOYMENT} pod: {result.stderr}")
    items = json.loads(result.stdout)["items"]
    if len(items) != 1:
        raise RuntimeError(f"expected exactly one {DEPLOYMENT} pod, found {len(items)}")
    pod = items[0]
    statuses = pod["status"].get("containerStatuses") or [{}]
    return {"uid": pod["metadata"]["uid"], "restartCount": statuses[0].get("restartCount")}


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
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(_pod_identity()) + "\n")


def _assert_not_restarted_since_restart() -> None:
    """A live-check that restarted mid-run lost everything it had seen, and its
    report would look fine: fewer samples, still no violations."""
    if not STATE_PATH.exists():
        raise AssertionError(
            "live-check report requested but restart() never recorded the pod "
            f"({STATE_PATH}): the report would cover an unknown window. "
            "scripts/smoke-test.sh restarts live-check before the e2e run."
        )
    recorded = json.loads(STATE_PATH.read_text())
    now = _pod_identity()
    assert now == recorded, (
        f"weaver live-check restarted during the run (recorded {recorded}, now {now}): "
        "its report covers only the time since, so a pass would prove less than it "
        "says. Investigate why the pod restarted (OOM? kubectl -n observability "
        "describe pod -l app=weaver-live-check) and re-run the gate."
    )


def report(save_to: str | Path | None = None, timeout_s: float = 120.0) -> dict[str, Any]:
    """POST /stop, GET /report, POST /shutdown, in that order.

    Fails if the pod or container restarted since `restart()`. After the
    shutdown the container restarts empty, so a second `report()` fails the
    restart check rather than answering a window it did not watch.
    """
    _assert_not_restarted_since_restart()
    time.sleep(FLUSH_S)
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
    # Stop, report, SHUTDOWN. A stopped receiver answers every export with
    # "the OTLP receiver has stopped", and the collector logs each as `Exporting
    # failed` -- which other e2e guards count as telemetry loss. Shutting down
    # makes the kubelet restart the container with a listening, empty receiver.
    with _forward() as base:
        status, body = _request(base, "POST", "/shutdown")
        assert status == 200, f"POST /shutdown answered {status}: {body[:200]!r}"
    # The container is gone for a few seconds, and the collector logs `Exporting
    # failed ... connection refused` (retried, nothing dropped) until it is
    # back. Wait for it, so the burst is over when report() returns. Guards
    # that count `Exporting failed` in a trailing window still see it for up to
    # a minute; the gate runs this last, after every such guard.
    time.sleep(2.0)
    deadline = time.monotonic() + 90
    while not healthy():
        if time.monotonic() > deadline:
            raise RuntimeError(f"{DEPLOYMENT} did not come back after POST /shutdown")
        time.sleep(2.0)
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
