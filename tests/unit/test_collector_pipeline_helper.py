"""The reading of the deployed collector that three stage gates depend on.

`tests/harness/collector.py::pipeline_exporters` exists because the substring
form of the same question was *measured* inert, twice, in this repo: deleting
the pipeline entry that routes a signal leaves the exporter id in the file
under `exporters:` and in the comment above it, so `id in config_text` stays
true while nothing is routed. Two of the three e2e callers were fixed from that
finding; the third still grepped until M4 Task 5.

Every case below is built from the shape of the collector config actually
deployed to `kind-northstar`, read 2026-09-14:

    traces:            [otlp_grpc/jaeger, forward/opensearch, otlp_grpc/dash0, debug]
    traces/opensearch: [opensearch/trace]
    metrics:           [otlp_http/prometheus, otlp_grpc/dash0, debug]
    logs:              [otlp_grpc/dash0, debug]
"""
from __future__ import annotations

import textwrap

import pytest

from tests.harness.collector import pipeline_exporters

DEPLOYED = textwrap.dedent(
    """
    exporters:
      # opensearch/trace -- named here in a comment too, which is the point
      opensearch/trace:
        http:
          endpoint: http://opensearch-cluster-master:9200
      otlp_http/prometheus:
        endpoint: http://prometheus:9090/api/v1/otlp
      otlp_grpc/jaeger:
        endpoint: jaeger-collector:4317
    connectors:
      forward/opensearch: {}
    service:
      pipelines:
        traces:
          receivers: [otlp]
          exporters: [otlp_grpc/jaeger, forward/opensearch, otlp_grpc/dash0, debug]
        traces/opensearch:
          receivers: [forward/opensearch]
          exporters: [opensearch/trace]
        metrics:
          receivers: [otlp]
          exporters: [otlp_http/prometheus, otlp_grpc/dash0, debug]
        logs:
          receivers: [otlp]
          exporters: [otlp_grpc/dash0, debug]
    """
)


def test_it_reads_the_deployed_shape_including_the_named_branch():
    """`traces` must cover `traces/opensearch`, or half the tee is invisible.

    A plain `pipelines["traces"]` lookup -- the obvious implementation -- finds
    `forward/opensearch` and misses `opensearch/trace` entirely, because the
    exporter that writes to the index is listed by the *branch*. Two callers
    require both ids, so that implementation would make the stage gate
    unsatisfiable on a correctly configured cluster.
    """
    assert pipeline_exporters(DEPLOYED, "traces") == {
        "otlp_grpc/jaeger",
        "forward/opensearch",
        "otlp_grpc/dash0",
        "debug",
        "opensearch/trace",
    }
    assert pipeline_exporters(DEPLOYED, "metrics") == {
        "otlp_http/prometheus",
        "otlp_grpc/dash0",
        "debug",
    }


def test_the_mutation_that_defeated_the_substring_form_defeats_this_one():
    """The measurement, replayed as a unit test.

    `otlp_http/prometheus` is deleted from the `metrics` pipeline and from
    nowhere else -- the exporter declaration and the comment stay, exactly as
    the real mutation left them. The substring reading of this config is still
    true; this one is false, which is the whole reason the helper exists.
    """
    mutated = DEPLOYED.replace(
        "exporters: [otlp_http/prometheus, otlp_grpc/dash0, debug]",
        "exporters: [otlp_grpc/dash0, debug]",
    )
    assert mutated != DEPLOYED, "the mutation matched nothing; the fixture drifted"
    assert "otlp_http/prometheus" in mutated, (
        "the point of this test is that the id is still in the text"
    )
    assert "otlp_http/prometheus" not in pipeline_exporters(mutated, "metrics")


def test_deleting_the_tee_from_the_main_pipeline_is_visible():
    """The traces half of the same mutation.

    `forward/opensearch` feeds the branch. With it gone from the main pipeline
    the branch still exists and still lists `opensearch/trace`, so a check that
    only looked for the exporter stays green while no span reaches the index --
    this is fix round 1's finding F2 against `test_evidence_view.py`, which is
    why both callers require both ids.
    """
    mutated = DEPLOYED.replace(
        "exporters: [otlp_grpc/jaeger, forward/opensearch, otlp_grpc/dash0, debug]",
        "exporters: [otlp_grpc/jaeger, otlp_grpc/dash0, debug]",
    )
    assert mutated != DEPLOYED, "the mutation matched nothing; the fixture drifted"
    exporters = pipeline_exporters(mutated, "traces")
    assert "opensearch/trace" in exporters, "the branch is untouched by this mutation"
    assert "forward/opensearch" not in exporters


def test_the_signal_is_matched_on_the_whole_leading_segment():
    """`traces` selects `traces` and `traces/x`, and nothing else.

    Prefix matching with `startswith` would have selected a pipeline named
    `traces_debug`; substring matching would have selected `metrics/traces`.
    Neither routes the signal asked about.
    """
    config = textwrap.dedent(
        """
        service:
          pipelines:
            traces:            {exporters: [a]}
            traces/opensearch: {exporters: [b]}
            traces_debug:      {exporters: [c]}
            metrics/traces:    {exporters: [d]}
        """
    )
    assert pipeline_exporters(config, "traces") == {"a", "b"}


@pytest.mark.parametrize(
    "config",
    [
        pytest.param("", id="empty-string-the-configmap-was-absent"),
        pytest.param("   \n\n", id="whitespace-only"),
        pytest.param("just a string", id="not-a-mapping"),
        pytest.param("service: {}", id="no-pipelines-key"),
        pytest.param("service:\n  pipelines: null", id="pipelines-is-null"),
        pytest.param("service:\n  pipelines:\n    traces: null", id="pipeline-is-null"),
        pytest.param(
            "service:\n  pipelines:\n    traces:\n      receivers: [otlp]",
            id="pipeline-with-no-exporters",
        ),
        pytest.param("a: [unclosed", id="unparseable-yaml"),
    ],
)
def test_it_answers_nothing_is_routed_rather_than_raising(config):
    """Every degenerate input means "nothing is routed", not a traceback.

    `_deployed_collector_config` returns `""` when the ConfigMap is absent,
    which is a normal stage-1 cluster. The callers turn an empty result into a
    skip only in combination with the backend Service also being absent, so
    answering honestly here is safe; raising would turn a below-stage-4 cluster
    into a collection error.
    """
    assert pipeline_exporters(config, "traces") == set()


def test_exporter_ids_come_back_as_strings():
    """YAML will hand back a bool for an unquoted `on`, `no`, `y`.

    The callers compare against string constants, so a non-string exporter id
    must not silently fail to match by type.
    """
    config = "service:\n  pipelines:\n    traces:\n      exporters: [on, 3, debug]"
    assert pipeline_exporters(config, "traces") == {"True", "3", "debug"}
