"""The collector config as shipped must declare every component its pipelines
name: the collector refuses to start otherwise, and that shows up only as a
crash-looping pod after a rebuild."""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "deploy" / "observability" / "collector-config.yaml"


def test_the_shipped_collector_config_declares_every_component_it_names():
    """A connector, if one is added, must be used on both sides."""
    parsed = yaml.safe_load(CONFIG.read_text())
    receivers = set(parsed.get("receivers") or {})
    processors = set(parsed.get("processors") or {})
    exporters = set(parsed.get("exporters") or {})
    connectors = set(parsed.get("connectors") or {})

    used_as_receiver: set[str] = set()
    used_as_exporter: set[str] = set()
    for name, pipeline in parsed["service"]["pipelines"].items():
        for component in pipeline.get("receivers") or []:
            assert component in receivers | connectors, (
                f"pipeline {name!r} receives from {component!r}, which is declared "
                "neither as a receiver nor as a connector"
            )
            used_as_receiver.add(component)
        for component in pipeline.get("processors") or []:
            assert component in processors, (
                f"pipeline {name!r} runs processor {component!r}, which is not declared"
            )
        for component in pipeline.get("exporters") or []:
            assert component in exporters | connectors, (
                f"pipeline {name!r} exports to {component!r}, which is declared "
                "neither as an exporter nor as a connector"
            )
            used_as_exporter.add(component)

    for connector in connectors:
        assert connector in used_as_exporter, f"connector {connector!r} is fed by no pipeline"
        assert connector in used_as_receiver, f"connector {connector!r} feeds no pipeline"
