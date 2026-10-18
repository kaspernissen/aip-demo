from __future__ import annotations

import os

# Before bootstrap is imported: nothing here may reach a real collector.
os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")

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
