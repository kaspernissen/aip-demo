"""S7 guard (final-review.md): pins that
`deploy/observability/collector-config.yaml` and
`collector-config-dash0.yaml` never diverge except in the one place they are
allowed to -- the Dash0 exporter and its place in each pipeline's exporter
list.

The two files are near-identical by construction (A3 mandated the Dash0
exporter *shape*, not the duplication): setup-kind.sh picks whichever one
applies wholesale, so a receiver, processor or CORS change made in only one
of them silently applies to just one of the two collector configurations
this project ships, and nothing caught that before this test. A guard test
that diffs the two files is a better fix than a one-time correction, since a
static fix cannot stop the next edit from drifting again the same way.
"""
import copy
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE = REPO_ROOT / "deploy" / "observability" / "collector-config.yaml"
DASH0 = REPO_ROOT / "deploy" / "observability" / "collector-config-dash0.yaml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def test_receivers_and_processors_are_identical():
    """Nothing about how telemetry is received or processed -- the CORS
    allowlist included -- may differ between the two files; only the export
    side is allowed to."""
    base = _load(BASE)
    dash0 = _load(DASH0)
    assert base["receivers"] == dash0["receivers"], (
        "collector-config.yaml and collector-config-dash0.yaml's receivers "
        "sections have drifted -- a CORS/protocol change must be made in "
        "both files identically"
    )
    assert base["processors"] == dash0["processors"], (
        "collector-config.yaml and collector-config-dash0.yaml's processors "
        "sections have drifted -- a processor change must be made in both "
        "files identically"
    )
    # M4 Task 1, fix round 1: `connectors` is a third section that is neither
    # a receiver nor an exporter, and nothing covered it before the
    # forward/opensearch tee existed. It is on the receiving side of the
    # export split, so like receivers and processors it must be identical.
    assert base.get("connectors") == dash0.get("connectors"), (
        "collector-config.yaml and collector-config-dash0.yaml's connectors "
        "sections have drifted -- the forward/opensearch tee must be declared "
        "identically in both files"
    )


def test_dash0_config_exporters_are_the_base_exporters_plus_dash0_only():
    """The Dash0 file's `exporters` map must be exactly the base file's map
    with one additional `otlp_grpc/dash0` entry -- not a divergent `debug` or
    `otlp_grpc/jaeger` block that happened to get edited in only one file."""
    base = _load(BASE)
    dash0 = _load(DASH0)

    base_exporters = base["exporters"]
    dash0_exporters = dict(dash0["exporters"])
    assert "otlp_grpc/dash0" in dash0_exporters, (
        "collector-config-dash0.yaml is missing its own otlp_grpc/dash0 "
        "exporter entry"
    )
    dash0_only = dash0_exporters.pop("otlp_grpc/dash0")
    assert dash0_exporters == base_exporters, (
        "collector-config-dash0.yaml's exporters (minus otlp_grpc/dash0) "
        "have drifted from collector-config.yaml's -- an exporter change "
        "(e.g. the debug verbosity, or the jaeger endpoint) must be made in "
        "both files identically"
    )
    assert set(dash0_only) >= {"endpoint", "headers"}, (
        f"otlp_grpc/dash0 exporter is missing expected keys: {dash0_only!r}"
    )


# M4 Task 1, fix round 1: the one pipeline that must NOT gain the Dash0
# exporter, and the only exception to the rule the test below enforces.
#
# `traces/opensearch` is the OpenSearch-only branch the `forward/opensearch`
# connector tees off the main traces pipeline. It runs
# `attributes/opensearch-compat`, which rewrites `policy.decision.retryable`
# to a non-colliding key so OpenSearch can index the decision span at all.
# Ruling M4-R5 says Dash0 and Jaeger receive the canonical names, and they do
# -- they export from the main `traces` pipeline, upstream of the tee. Adding
# `otlp_grpc/dash0` here would send Dash0 a second, renamed copy of every
# span. So this is a real exception, named and asserted rather than skipped.
OPENSEARCH_BRANCH = "traces/opensearch"


def test_dash0_pipelines_are_the_base_pipelines_plus_dash0_exporter():
    """Each pipeline (traces/metrics/logs) in the Dash0 file must have the
    same receivers and processors as the base file, and an exporters list
    that is exactly the base file's list with `otlp_grpc/dash0` inserted --
    not a pipeline that silently forgot debug, or forgot to add dash0 to a
    pipeline the base file has (A3 requires all three signal types get the
    same treatment, not just traces).

    `traces/opensearch` is exempt from the `+ otlp_grpc/dash0` half and from
    that half only: its exporter list must still be *identical* between the
    two files, and must still contain no Dash0 exporter. See OPENSEARCH_BRANCH
    above for why."""
    base = _load(BASE)
    dash0 = _load(DASH0)

    base_pipelines = base["service"]["pipelines"]
    dash0_pipelines = dash0["service"]["pipelines"]
    assert base_pipelines.keys() == dash0_pipelines.keys(), (
        f"pipeline sets have drifted: base {sorted(base_pipelines)}, "
        f"dash0 {sorted(dash0_pipelines)}"
    )
    for name, base_pipeline in base_pipelines.items():
        dash0_pipeline = dash0_pipelines[name]
        assert base_pipeline["receivers"] == dash0_pipeline["receivers"], (
            f"pipeline {name!r}'s receivers have drifted between the two files"
        )
        assert base_pipeline["processors"] == dash0_pipeline["processors"], (
            f"pipeline {name!r}'s processors have drifted between the two files"
        )

        base_exporters = list(base_pipeline["exporters"])
        dash0_exporters = list(dash0_pipeline["exporters"])
        expected = copy.deepcopy(base_exporters)
        assert "otlp_grpc/dash0" not in expected, (
            f"base pipeline {name!r} must not itself list otlp_grpc/dash0"
        )
        if name == OPENSEARCH_BRANCH:
            assert dash0_exporters == base_exporters, (
                f"pipeline {name!r}'s exporters have drifted between the two "
                f"files: {dash0_exporters} vs {base_exporters}"
            )
            assert "otlp_grpc/dash0" not in dash0_exporters, (
                f"pipeline {name!r} has gained otlp_grpc/dash0. It carries the "
                "OpenSearch-compat attribute rewrite, so exporting it to Dash0 "
                "would both duplicate every span there and duplicate it under "
                "a renamed attribute -- ruling M4-R5 says Dash0 receives the "
                "canonical names, which it does from the main `traces` "
                "pipeline upstream of the forward/opensearch tee"
            )
            continue
        expected_with_dash0 = set(expected) | {"otlp_grpc/dash0"}
        assert set(dash0_exporters) == expected_with_dash0, (
            f"pipeline {name!r}'s exporters in collector-config-dash0.yaml "
            f"({sorted(dash0_exporters)}) are not exactly the base file's "
            f"exporters ({sorted(base_exporters)}) plus otlp_grpc/dash0"
        )
