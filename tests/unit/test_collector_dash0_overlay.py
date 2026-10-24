"""The Dash0 overlay adds Dash0 to every pipeline and changes nothing else.

setup-kind.sh passes `collector-dash0-overlay.yaml` as a second `--config`
after `collector-config.yaml`. The collector merges maps and replaces lists, so
the overlay restates each pipeline's exporter list. This checks the merged
result.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE = REPO_ROOT / "deploy" / "observability" / "collector-config.yaml"
OVERLAY = REPO_ROOT / "deploy" / "observability" / "collector-dash0-overlay.yaml"
DASH0 = "otlp_grpc/dash0"


def _merge(base, overlay):
    """The collector's merge: maps merge key by key, anything else is replaced."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            merged[key] = _merge(base.get(key), value) if key in base else value
        return merged
    return overlay


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def test_the_overlay_adds_dash0_to_every_pipeline_and_nothing_else():
    base = _load(BASE)
    merged = _merge(base, _load(OVERLAY))
    base_pipelines = base["service"]["pipelines"]
    merged_pipelines = merged["service"]["pipelines"]
    assert merged_pipelines.keys() == base_pipelines.keys()
    for signal in ("traces", "metrics", "logs"):
        assert set(merged_pipelines[signal]["exporters"]) == set(base_pipelines[signal]["exporters"]) | {DASH0}, signal
        assert len(merged_pipelines[signal]["exporters"]) == len(base_pipelines[signal]["exporters"]) + 1, signal
    # Only the main pipelines get Dash0; every other pipeline is untouched.
    for name, pipeline in merged_pipelines.items():
        if name not in ("traces", "metrics", "logs"):
            assert pipeline == base_pipelines[name], name
    # Everything outside the exporter lists is the base config's, plus the one exporter.
    assert merged["exporters"].keys() == base["exporters"].keys() | {DASH0}
    for section in ("receivers", "processors", "connectors"):
        assert merged.get(section) == base.get(section), section


def test_the_dash0_exporter_reads_its_endpoint_and_credentials_from_the_environment():
    exporter = yaml.safe_load(OVERLAY.read_text())["exporters"][DASH0]
    assert exporter["endpoint"] == "${env:DASH0_GRPC_HOSTNAME}:${env:DASH0_GRPC_PORT}"
    assert exporter["headers"]["Authorization"] == "Bearer ${env:DASH0_AUTH_TOKEN}"
