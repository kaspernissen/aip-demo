"""Weaver live-check, as the gate drives it (M9).

The collector sends the weaver-live-check pod (observability namespace) a COPY
of every span, metric and log. The pod serves its admin API on 4320, which
`scripts/start-demo.sh` forwards to `localhost:18400`:

  GET  /health    {"status":"ready"} once the registry is loaded
  POST /stop      stop receiving and build the report (returns when ready)
  GET  /report    the report; 409 while still receiving
  POST /shutdown  the process exits 0

`restart()` empties the pod's state, `report()` returns what it saw, and
`owned_violations()` picks out the findings that fail the gate.

**Why `restart()` is `POST /shutdown`, not `kubectl rollout restart`.** A
rollout replaces the pod, and the `kubectl port-forward` on 18400 is bound to
the OLD pod: its socket stays open and every request through it fails, which is
exactly what `scripts/smoke-test.sh` refuses to start without. The gate creates
no port-forward of its own. Shutting the process down makes the kubelet restart
the container inside the SAME pod and network namespace, so the forward keeps
working and the new process starts with an empty report.

Failure style: plain `assert`/`RuntimeError` with a message, like the other
helpers here. An unreachable live-check is a failure, never a skip.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = "http://localhost:18400"

# Names this project owns, and the retired un-prefixed namespaces it must no
# longer emit. This mirrors the `sample_names` of the violation override in
# telemetry/live-check.toml; tests/unit/test_live_check_config.py pins the two
# together so they cannot drift.
OWNED_PREFIXES = ("northstar.", "policy.", "review.", "claim.", "settlement.")


def _request(method: str, path: str, timeout: float = 30.0) -> tuple[int, bytes]:
    request = urllib.request.Request(f"{BASE_URL}{path}", method=method)
    if method == "POST":
        request.data = b""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise RuntimeError(
            f"weaver live-check is unreachable at {BASE_URL}{path}: {error}. "
            "Is the forward up (./scripts/start-demo.sh) and "
            "deploy/weaver-live-check Ready in the observability namespace?"
        ) from error


def healthy() -> bool:
    try:
        status, _ = _request("GET", "/health", timeout=3.0)
    except RuntimeError:
        return False
    return status == 200


def _wait(want_healthy: bool, timeout_s: float, what: str) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if healthy() == want_healthy:
            return
        time.sleep(1.0)
    raise RuntimeError(f"weaver live-check: {what} within {timeout_s:.0f}s did not happen")


def restart(timeout_s: float = 180.0) -> None:
    """Empty the live-check's state: shut the process down, wait for the
    container to come back, and wait until it is ready again.

    Fails if the process never goes away: an old process that answers /health
    would otherwise be mistaken for a fresh one, and its report would carry
    traffic from before the gate began.
    """
    if not healthy():
        raise RuntimeError(
            f"weaver live-check is not healthy at {BASE_URL}/health before the restart"
        )
    status, body = _request("POST", "/shutdown")
    assert status == 200, f"POST /shutdown answered {status}: {body[:200]!r}"
    _wait(False, 30.0, "the process going away after POST /shutdown")
    _wait(True, timeout_s, "the container coming back Ready")


def report(save_to: str | Path | None = None, timeout_s: float = 120.0) -> dict[str, Any]:
    """Stop receiving, fetch the report, shut the process down.

    The shutdown makes the pod start over empty, so a second `report()` without
    a `restart()`/traffic in between answers an empty report, which the e2e test
    reads as a failure ("live-check received nothing"), not as a pass.
    """
    status, body = _request("POST", "/stop", timeout=timeout_s)
    assert status == 200, f"POST /stop answered {status}: {body[:200]!r}"
    deadline = time.monotonic() + timeout_s
    while True:
        status, body = _request("GET", "/report")
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
    # Best effort: the report is in hand; a failure to shut down loses nothing.
    try:
        _request("POST", "/shutdown")
    except RuntimeError:
        pass
    return parsed


def sample_counts(report_: dict[str, Any]) -> dict[str, int]:
    """Entities the live-check received, by type (span, metric, log, ...)."""
    return dict(report_.get("statistics", {}).get("total_entities_by_type", {}))


def _walk(node: Any, path: str = ""):
    """Every dict in the report with the path that leads to it."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


def _is_owned(name: str) -> bool:
    return name.startswith(OWNED_PREFIXES)


def violations(report_: dict[str, Any]) -> list[dict[str, Any]]:
    """Every finding at level `violation`, owned or not."""
    return [
        node for _, node in _walk(report_.get("samples", []))
        if node.get("level") == "violation" and "id" in node
    ]


def owned_violations(report_: dict[str, Any]) -> list[dict[str, Any]]:
    """Findings at `violation` whose signal or attribute name this project owns.

    `telemetry/live-check.toml` already lowers every finding about a name we do
    not own to `information`, so on a correct config `violations()` and this
    function agree. This filter is the second line: a config edit that let a
    foreign name through as a violation must not fail the gate on Kyverno's or
    Cilium's behalf.
    """
    return [
        finding for finding in violations(report_)
        if _is_owned(str(finding.get("signal_name", "")))
        or _is_owned(str(finding.get("context", {}).get("attribute_name", "")))
    ]


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "restart":
        restart()
        print("weaver live-check restarted, empty")
    elif command == "health":
        sys.exit(0 if healthy() else 1)
    else:
        sys.exit("usage: python -m tests.harness.livecheck restart|health")
