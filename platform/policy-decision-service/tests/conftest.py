"""No test in this suite may export telemetry to a real endpoint.

The service's bootstrap builds OTLP exporters that default to
localhost:4318, which during a demo is a port-forward to the live collector.
Before any test module imports the bootstrap, point every OTLP exporter at a
sink in this process that accepts and discards everything. Tests that check
spans add their own in-memory exporter; tests that check exporter settings
set the variables they need themselves.
"""
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Sink(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


_sink = HTTPServer(("127.0.0.1", 0), _Sink)
threading.Thread(target=_sink.serve_forever, daemon=True).start()
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"http://127.0.0.1:{_sink.server_address[1]}"
for _signal in ("TRACES", "METRICS", "LOGS"):
    os.environ.pop(f"OTEL_EXPORTER_OTLP_{_signal}_ENDPOINT", None)
    os.environ[f"OTEL_{_signal}_EXPORTER"] = "none"


# Refuse to run at all without the generated ExtMCP stubs.
#
# `ext_mcp_pb2.py` / `ext_mcp_pb2_grpc.py` are git-ignored and generated from
# the vendored proto by `./gen-stubs.sh`. Their absence is a broken build and
# must look like one.
#
# Deliberately *not* a `pytest.importorskip` or a `skipif`: a skip does not fail
# a run. This project has already been burned by exactly that shape -- deleting a
# live network policy turned four e2e tests into skips and left the suite green.
# Raising from a conftest fails the whole session with a usage error, which is
# the loudest thing pytest can do, and prints the one command that fixes it.

_HOW = (
    "\n\n"
    "The generated ExtMCP gRPC stubs are missing.\n"
    "Generate them from the vendored proto, then re-run:\n"
    "\n"
    "    cd platform/policy-decision-service && ./gen-stubs.sh\n"
    "\n"
    "They are git-ignored on purpose: proto/ext_mcp.proto is the single\n"
    "source of truth, and committing the stubs would let the image\n"
    "and the repo disagree. The Dockerfile runs the same script at build time.\n"
)

try:
    import ext_mcp_pb2  # noqa: F401
    import ext_mcp_pb2_grpc  # noqa: F401
except ImportError as exc:  # pragma: no cover - exercised by deleting the stubs
    raise RuntimeError(str(exc) + _HOW) from exc
