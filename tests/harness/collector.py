"""The one definition of "what does the DEPLOYED collector actually route?".

Three `tests/e2e` modules ask a version of this question to decide whether the
cluster is at stage 4, and the substring form of it is demonstrably inert.
`test_prometheus_metrics_ingest.py` recorded the measurement in its own
docstring: deleting `otlp_http/prometheus` from the `metrics` pipeline's
exporter list -- the edit that stops every metric in this system reaching
Prometheus -- left `PROMETHEUS_EXPORTER_ID in <config text>` true and all
fourteen tests in that file green, because the id still occurs twice more in
the file, once as the exporter's own declaration under `exporters:` and once in
the comment above it.

That finding was fixed in two of the three callers and, until M4 Task 5, not in
the third: `test_opensearch_trace_ingest.py` still read
`OPENSEARCH_EXPORTER_ID in _deployed_collector_config()`. Same defect, same
repo, written down twice, still live in the earliest file -- which is the
argument for one definition rather than three careful copies.

Takes the config *text* rather than running `kubectl` itself, so the three
callers keep their own `_kubectl` (each carries its own timeout and namespace
constants) and this stays unit-testable with no cluster.
"""
from __future__ import annotations

import yaml


def pipeline_exporters(config_text: str, signal: str) -> set[str]:
    """Every exporter id listed by a `service.pipelines` entry for `signal`.

    `signal` matches on the pipeline name's leading segment, so `"traces"`
    covers both `traces` and the named branch `traces/opensearch` -- which is
    the shape the OpenSearch tee uses, and which a plain `pipelines["traces"]`
    lookup would miss half of. It deliberately does not match a pipeline merely
    *containing* the word: `signal="traces"` does not select `metrics/traces`.

    Returns an empty set for empty, unparseable or structurally unexpected
    text rather than raising. The callers turn that into a stage-4 skip only in
    combination with the backend Service being absent too; on its own it means
    "nothing is routed", which is the honest answer to this question when the
    ConfigMap could not be read.
    """
    if not config_text or not config_text.strip():
        return set()
    try:
        parsed = yaml.safe_load(config_text)
    except yaml.YAMLError:
        return set()
    if not isinstance(parsed, dict):
        return set()
    pipelines = ((parsed.get("service") or {}).get("pipelines")) or {}
    if not isinstance(pipelines, dict):
        return set()
    found: set[str] = set()
    for name, pipeline in pipelines.items():
        if str(name).split("/", 1)[0] != signal:
            continue
        for exporter in (pipeline or {}).get("exporters") or []:
            found.add(str(exporter))
    return found
