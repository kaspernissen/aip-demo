"""`docs/telemetry-contract.md`'s export-destinations table can drift from
the very file it claims to be read from: a collector config change (adding
`opensearch/logs` to the `logs` pipeline, say) has no way to force the
hand-written table to update alongside it.

Per this repo's standing rule, a table whose own header says "read directly from
`deploy/observability/collector-config-dash0.yaml`'s own pipeline
definitions" and which nothing actually reads from that file is a retyped
figure wearing a provenance claim. This test makes the claim true by deriving
the table from the two collector configs on every run, using the same
`pipeline_exporters` helper `tests/unit/test_collector_pipeline_helper.py`
already exercises against the *deployed* config -- here pointed at the two
files on disk, so it needs no cluster and no `kubectl`.

Only `collector-config-dash0.yaml` is checked against the table's own
exporter lists: that is the file the table's header names, and
`test_collector_configs_in_sync.py` already guards that the vendor-neutral
`collector-config.yaml` cannot diverge from it except by the Dash0 exporter
alone, so a divergence there would already be caught one file over.

This reads the pipelines directly with `yaml.safe_load`, the same way
`test_collector_configs_in_sync.py` does, rather than through
`tests.harness.collector.pipeline_exporters`: that helper deliberately merges
a named branch (`traces/opensearch`) into its parent signal (`traces`) for its
own three e2e callers, which is the wrong shape here -- the table publishes
`traces` and `traces/opensearch` as two separate rows with two separate
exporter lists, and merging them would make a divergence between the two
invisible.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "docs" / "telemetry-contract.md"
DASH0_CONFIG = REPO_ROOT / "deploy" / "observability" / "collector-config-dash0.yaml"

# Anchored on the sentence immediately above the table (unique in the
# document) so this cannot silently start reading some other table if the
# file grows another one above it.
_TABLE_RE = re.compile(
    r"are not symmetric\*\*:\n\n"
    r"\| Pipeline \| Exporters \|\n"
    r"\|---\|---\|\n"
    r"((?:\|.*\|\n)+)"
)
# `\`(name)\`` for the pipeline column (a trailing "(stage 4 only)" outside
# the backticks is discarded, not captured); the exporters column is parsed
# separately below, by pulling every backtick-quoted token out of it -- which
# skips the "(stage 4 only)" annotations there too, since those sit outside
# backticks by construction.
_ROW_RE = re.compile(r"^\| `([^`]+)`(?: \([^)]*\))? \| (.+?) \|$", re.M)
_TOKEN_RE = re.compile(r"`([^`]+)`")


def _published_table() -> dict[str, list[str]]:
    text = CONTRACT_PATH.read_text()
    table_match = _TABLE_RE.search(text)
    assert table_match, (
        "could not find the export-destinations table in "
        f"{CONTRACT_PATH.relative_to(REPO_ROOT)} by its anchor sentence -- "
        "either the table moved, was reworded, or was deleted. If it was "
        "deleted, this guard should be deleted with it, not left to fail on "
        "a table that no longer exists."
    )
    rows = _ROW_RE.findall(table_match.group(1))
    assert rows, "the table's own header matched but no '| `name` | ... |' rows did"
    return {name: _TOKEN_RE.findall(exporters) for name, exporters in rows}


def _configured_pipelines() -> dict[str, list[str]]:
    parsed = yaml.safe_load(DASH0_CONFIG.read_text())
    pipelines = parsed["service"]["pipelines"]
    return {
        name: [str(exporter) for exporter in (pipeline or {}).get("exporters") or []]
        for name, pipeline in pipelines.items()
    }


def test_the_export_destinations_table_matches_the_dash0_collector_config():
    """Every row the table publishes must equal that pipeline's actual
    exporter list in `collector-config-dash0.yaml` -- the file the table's own
    header claims to read from. Mutate a pipeline's exporter list in that file
    (`scripts/mutate-guard.sh`) and this must go RED -- proof the guard
    catches drift, not merely a one-time correction of the table."""
    published = _published_table()
    configured = _configured_pipelines()

    assert set(published) == set(configured), (
        f"the table's own pipeline set is {sorted(published)}, "
        f"collector-config-dash0.yaml's is {sorted(configured)} -- either a "
        "pipeline was added/removed in the config with no matching row, or "
        "the table's row set drifted"
    )

    mismatches = []
    for name, published_exporters in published.items():
        actual = set(configured[name])
        if set(published_exporters) != actual:
            mismatches.append(
                f"{name!r}: table says {published_exporters}, "
                f"collector-config-dash0.yaml actually routes to {sorted(actual)}"
            )
    assert not mismatches, (
        "docs/telemetry-contract.md's export-destinations table has drifted "
        "from deploy/observability/collector-config-dash0.yaml (this table "
        "has drifted from its source config before -- see the note in the "
        "document immediately below the table):\n" + "\n".join(mismatches)
    )


def test_the_logs_summary_sentence_names_opensearch():
    """The narrower, human-readable claim the table backs: when the logs
    pipeline routes to OpenSearch as well as Dash0, the summary sentence has
    to say so rather than continuing to claim "logs go to Dash0 only".
    Checked directly against the config rather than trusting the
    table-vs-prose consistency to hold on its own, since the table and the
    sentence are two separate hand-written artefacts describing the same
    fact."""
    logs_exporters = _configured_pipelines()["logs"]

    text = CONTRACT_PATH.read_text()
    summary = re.search(r"\*\*Traces go to.*?\.\*\*", text, re.S)
    assert summary, "expected the bolded 'Traces go to ... logs go to ...' summary sentence"

    if any("opensearch" in exp for exp in logs_exporters):
        assert "OpenSearch" in summary.group(0) and "logs" in summary.group(0), (
            "the logs pipeline exports to OpenSearch but the summary sentence "
            f"does not say so: {summary.group(0)!r}"
        )
