from __future__ import annotations

import os

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Sink(BaseHTTPRequestHandler):
    """A collector that accepts everything: exports succeed, nothing is logged."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


_sink = HTTPServer(("127.0.0.1", 0), _Sink)
threading.Thread(target=_sink.serve_forever, daemon=True).start()
# Before bootstrap is imported: nothing here may reach a real collector.
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"http://127.0.0.1:{_sink.server_address[1]}"
for _signal in ("TRACES", "METRICS", "LOGS"):
    os.environ.pop(f"OTEL_EXPORTER_OTLP_{_signal}_ENDPOINT", None)
    os.environ[f"OTEL_{_signal}_EXPORTER"] = "none"

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from starlette.testclient import TestClient

import main
from relay import Relay


@pytest.fixture(scope="session")
def span_exporter():
    # Reads every span the process makes: nothing is sampled or filtered.
    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


@pytest.fixture
def static_dir(tmp_path):
    (tmp_path / "index.html").write_text("INDEX")
    (tmp_path / "foo.html").write_text("FOO")
    (tmp_path / "bar").mkdir()
    (tmp_path / "bar" / "index.html").write_text("BAR-INDEX")
    (tmp_path / "_next").mkdir()
    (tmp_path / "_next" / "app.js").write_text("JS")
    return tmp_path


@pytest.fixture
def app(static_dir, span_exporter):
    span_exporter.clear()
    return main.create_app(relay=Relay(), static_dir=str(static_dir))


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
async def aclient(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c
