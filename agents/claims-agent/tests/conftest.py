"""No test in this suite may export telemetry to a real endpoint.

The service's bootstrap builds OTLP exporters that default to
localhost:4318, which is a kind host port onto the live collector.
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
