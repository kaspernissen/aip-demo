"""The one definition of "what does the DEPLOYED collector actually route?".

Several `tests/e2e` modules ask a version of this question to decide whether
metrics or traces are actually reaching their backend, and a plain substring
check (`EXPORTER_ID in <config text>`) is unreliable for it: deleting an
exporter from a pipeline's exporter list can still leave its id present
elsewhere in the same YAML (its own declaration under `exporters:`, or a
comment), so the substring stays `True` even though nothing is routed to it
any more. Parsing the pipeline structure and checking the exporter list
directly avoids that false positive, which is why every caller shares this
one definition rather than repeating its own substring check.

Takes the config *text* rather than running `kubectl` itself, so callers keep
their own `_kubectl` (each carries its own timeout and namespace constants)
and this stays unit-testable with no cluster.
"""
from __future__ import annotations

import yaml


def pipeline_exporters(config_text: str, signal: str) -> set[str]:
    """Every exporter id listed by a `service.pipelines` entry for `signal`.

    `signal` matches on the pipeline name's leading segment, so `"traces"`
    covers `traces` and any named branch such as `traces/<name>`. It does not
    match a pipeline merely *containing* the word: `signal="traces"` does not
    select `metrics/traces`.

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
