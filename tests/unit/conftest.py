"""No unit test may export telemetry to a real endpoint.

Nothing here builds an exporter today, but an OTLP exporter defaults to
localhost:4318, which during a demo is a port-forward to the live collector.
Each unit test runs with the exporters off and the OTLP endpoint pointed at a
closed local port. Scoped to tests/unit: the e2e tests export to the cluster
on purpose.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_telemetry_export(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    for signal in ("TRACES", "METRICS", "LOGS"):
        monkeypatch.delenv(f"OTEL_EXPORTER_OTLP_{signal}_ENDPOINT", raising=False)
        monkeypatch.setenv(f"OTEL_{signal}_EXPORTER", "none")
