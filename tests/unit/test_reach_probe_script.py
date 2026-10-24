"""`scripts/reach-probe.py` and the demo's reach beat, run for real against a local server.

The probe runs INSIDE a pod and prints one line the beat script and the demo
driver branch on. It is executed here with `python -c`, exactly as
`kubectl exec ... python3 -c "$(cat reach-probe.py)"` does, against a local
HTTP server that answers the shapes a Linkerd-enforced cluster produces (an empty 403,
an empty 404, an app 404 with a body, a 504 carrying `l5d-proxy-error`).
"""
import http.server
import importlib.util
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests.harness import reach

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
PROBE = (SCRIPTS / "reach-probe.py").read_text()


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _answer(self):
        code, header, body = {
            "/403": (403, None, b""),
            "/404": (404, None, b""),
            "/200": (200, None, b"ok"),
            "/app404": (404, None, b"not found"),
            "/401": (401, None, b'{"error":"x"}'),
            "/proxyerr": (504, "endpoint x: client error (Connect)", b""),
        }[self.path]
        self.send_response(code)
        if header:
            self.send_header("l5d-proxy-error", header)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer


@pytest.fixture(scope="module")
def base():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _run(url, method="GET"):
    return subprocess.run(
        [sys.executable, "-c", PROBE, url, method, "3"], capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize(
    "path,method,expected",
    [
        ("/403", "POST", "REFUSED 403"),
        ("/404", "GET", "REFUSED 404"),
        ("/200", "GET", "REACHED 200"),
        ("/app404", "GET", "REACHED 404"),   # an app's own 404 has a body: reached
        ("/401", "POST", "REACHED 401"),     # the app answered: reached
        ("/proxyerr", "POST", "DROPPED proxy-error"),
    ],
)
def test_probe_lines(base, path, method, expected):
    assert _run(base + path, method) == expected


def test_a_closed_port_is_dropped_not_refused():
    """From a meshed pod nothing refuses a connect (the proxy accepts it), so a
    transport failure is never `REFUSED`: that word is Linkerd's policy."""
    assert _run("http://127.0.0.1:1/").startswith("DROPPED ")


def test_the_probe_and_the_harness_agree_on_every_shape(base):
    """One rule, two copies (the in-pod script cannot import the harness)."""
    for path, status in (("/403", 403), ("/404", 404), ("/200", 200), ("/app404", 404),
                         ("/proxyerr", 504)):
        line = _run(base + path)
        harness = reach.classify_ingress({
            "status": status,
            "headers": {"l5d-proxy-error": "x"} if path == "/proxyerr" else {},
            "body_len": 0 if path in ("/403", "/404", "/proxyerr") else 5,
        })
        if line.startswith("REFUSED"):
            assert harness.startswith("refused-")
        elif line.startswith("DROPPED"):
            assert harness == "proxy-error"
        else:
            assert harness == "answered"


def _driver():
    spec = importlib.util.spec_from_file_location("northstar_demo_driver_reach", SCRIPTS / "demo.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _patch(monkeypatch, demo, probe_line, rises):
    class Done:
        def __init__(self, out):
            self.stdout, self.returncode = out, 0

    monkeypatch.setattr(demo.live, "kubectl", lambda *a, **k: Done("somepod"))
    labels = dict(srv_name="payments-api", route_name="payments-api-execute")
    base = reach.DenyCounter(tuple(sorted(labels.items())), 4.0)
    bumped = reach.DenyCounter(tuple(sorted(labels.items())), 5.0)
    calls = iter([[base], [bumped if rises else base]])
    monkeypatch.setattr(demo.reach, "read_deny_counters", lambda *a: next(calls))
    monkeypatch.setattr(demo, "probe_from_claims_agent", lambda *a, **k: probe_line)


def test_the_demo_prints_the_refusal_line_the_plan_specifies(monkeypatch):
    demo = _driver()
    _patch(monkeypatch, demo, "REFUSED 403", rises=True)
    assert demo.refusal_from_reviewer_ui() == (
        "REFUSED 403 by payments-api/payments-api-execute (deny counter +1)")


def test_the_demo_fails_a_403_that_moved_no_counter(monkeypatch):
    demo = _driver()
    _patch(monkeypatch, demo, "REFUSED 403", rises=False)
    with pytest.raises(demo.Fail, match="did not rise"):
        demo.refusal_from_reviewer_ui()


def test_the_demo_fails_when_reviewer_ui_gets_through(monkeypatch):
    demo = _driver()
    _patch(monkeypatch, demo, "REACHED 200", rises=True)
    with pytest.raises(demo.Fail, match="not refused 403"):
        demo.refusal_from_reviewer_ui()
