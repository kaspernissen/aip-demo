"""M4 Task 2: what Prometheus actually holds, asserted against the live cluster.

This file is the executable half of `docs/telemetry-contract.md`'s "Metrics, as
measured (M4 Task 2)" section and of `docs/upstream-findings.md` Finding 12. §6
of the M4 spec asks one question -- *once a MeterProvider exists, which GenAI
agent metrics does anything actually emit?* -- and the answer published there is
"one of six". A published negative is only worth anything if something re-checks
it, and M4's exit criterion 6 says Task 5 re-verifies the inventory; this is
what it re-verifies with.

**Every assertion here is paired with a positive control, deliberately.** The
expensive failure mode for this milestone is a FALSE negative: concluding "the
framework emits nothing" from a query that would have returned empty whatever
the framework did. So no test below asserts an absence without, in the same
test, asserting a presence that could only be there if the path the absent
metric would have travelled is working end to end.

That sentence was aspirational rather than true when this file shipped, and
review finding F3 demonstrated it: three tests -- the five-absence
parametrisation, the unpredicted-name backstop and the invented-metric check --
read only the cached `metric_names` set and passed against a completely empty
Prometheus. The file as a whole still went red in that situation, because the
control tests below do not, but the claim was per-test and those three did not
have it. They now call `_assert_the_genai_path_is_live()` as their own first
assertion, so the sentence above is now literal for all fourteen. The controls,
in the order the data crosses them:

  1. **Collector -> Prometheus.** `deploy/observability/prometheus/values.yaml`
     disables all ten of the chart's default scrape jobs, so this Prometheus
     has exactly one ingestion path: the OTLP receiver. SPIRE's series are
     therefore proof of that hop *by themselves* -- they are scraped by the
     collector's own `prometheus/spire` receiver and pushed here over OTLP, and
     no Python service is involved in producing them.
  2. **Per-service SDK -> collector.** Each service that is asserted to emit no
     `gen_ai.*` metric must be shown emitting *some* metric, under its own
     `job` label, in the same window. A service with no series at all proves
     nothing about any framework -- it only proves that process had nowhere to
     put a measurement, which is the pre-M4 state the whole section exists to
     move past.

**Stage gate, and why it is not a way to hide.** Same rule
`test_opensearch_trace_ingest.py` established: skip only when the cluster is
*coherently* below stage 4 -- Prometheus absent AND the deployed collector
config not naming the exporter. The incoherent middle (an exporter pointed at a
Service that does not exist) fails here rather than skipping, because that is
exactly review finding F10's shape. Fix round 1 closed the third case (finding
F7): "absent" now means the API server said `NotFound`, and every other way
`kubectl` can exit non-zero -- unreachable server, wrong context, expired
credential, timeout -- is a hard failure. "I could not look" must never be
recorded as "there was nothing to check", least of all in the single gate run
that re-verifies this milestone's published negative.

**This file drives no traffic of its own**, and that is a deliberate choice
rather than laziness: a file that drove its own settlement would spend model
tokens to re-measure something the rest of the suite has already caused, and
this suite's convention is one settlement's worth of tokens per file that
genuinely needs one. It reads what the cluster already holds.

The cost of that choice is staleness, and it is paid for explicitly rather than
hoped away. Prometheus's retention here is 15 days, so an unbounded presence
check would stay green for a fortnight after every service stopped emitting --
turning each absence below back into the false negative this design exists to
prevent. So **every positive control is bounded to `FRESH_WINDOW_S` and polled
for up to `CONTROL_POLL_TIMEOUT_S`**, while the absence assertions are
deliberately left unbounded, where a longer window only makes the negative
stronger. On an idle cluster the controls fail and say to drive traffic; they
never pass on two-week-old data.
"""
from __future__ import annotations

import json
import re
import subprocess
import time

import pytest
import yaml

KUBE_CONTEXT = "kind-northstar"
OBSERVABILITY_NAMESPACE = "observability"
PROMETHEUS_SERVICE = "prometheus"
COLLECTOR_CONFIGMAP = "otel-collector-config"
PROMETHEUS_EXPORTER_ID = "otlp_http/prometheus"

# M4 spec §6's table, verbatim. Read 2026-09-13 from
# `open-telemetry/semantic-conventions-genai`'s own
# `docs/gen-ai/gen-ai-metrics.md` on `main` -- NOT from the main
# `semantic-conventions` repository, which no longer carries them. Every one is
# Development stability; that qualifier is part of the published inventory
# (docs/telemetry-contract.md) rather than an optional extra, because a
# Development metric can be renamed upstream and a claim written without it
# becomes false at the next bump with nothing to catch it.
SIX_GENAI_METRICS = (
    "gen_ai.invoke_agent.duration",
    "gen_ai.invoke_agent.inference_calls",
    "gen_ai.invoke_agent.tool_calls",
    "gen_ai.execute_tool.duration",
    "gen_ai.client.token.usage",
    "gen_ai.client.operation.duration",
)

# The one of the six that any framework in this system emits. Measured, not
# chosen: `pydantic_ai/models/instrumented.py` is the only file in
# pydantic_ai 2.42.0 that names any of the six, and a2a-sdk 0.3.7,
# fastmcp 4.0.3 and mcp 2.2.0 contain no `get_meter(` call at all. See
# docs/upstream-findings.md Finding 12 for the full source-layer sweep.
EMITTED_GENAI_METRIC = "gen_ai.client.token.usage"

# Prometheus v3.14.0's default OTLP translation strategy is
# `UnderscoreEscapingWithSuffixes` (`config/config.go:280`), which this cluster
# leaves unchanged -- so a dotted OTel histogram name becomes underscores plus
# the three histogram suffixes. Derived here rather than hardcoded, so the
# mapping is stated once and a strategy change fails loudly instead of turning
# every assertion below into a tautology about names nothing uses.
def _prometheus_histogram_names(otel_name: str) -> tuple[str, ...]:
    base = otel_name.replace(".", "_")
    return tuple(f"{base}_{suffix}" for suffix in ("bucket", "count", "sum"))


def _kubectl(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _absent_or_fail(result: subprocess.CompletedProcess, what: str) -> bool:
    """True if `what` exists, False if the API server says it does not, and a
    hard failure for every other way `kubectl` can exit non-zero.

    **Review finding F7.** Both callers below used to read `returncode == 0`
    and treat anything else as "absent". An unreachable API server, a missing
    or misspelled context, an expired credential and a genuine `NotFound` are
    all non-zero, so a transient kubectl failure made this file conclude the
    cluster was "coherently below stage 4" and skip all fourteen tests --
    silently removing the milestone's inventory re-verification from the one
    gate run that performs it (M4 exit criterion 6). A skip that says "nothing
    to check here" when the truth is "I could not look" is the same false
    negative this file exists to prevent, one level up.

    `NotFound` is matched on kubectl's own error text, which is stable across
    resource kinds: `Error from server (NotFound): services "x" not found`.

    The sibling `tests/e2e/test_opensearch_trace_ingest.py` still has the older
    shape this was inherited from; F7 was raised against this file and is fixed
    here rather than widened into Task 1's file without a review asking for it.
    """
    if result.returncode == 0:
        return True
    stderr = result.stderr or ""
    if "NotFound" in stderr or "not found" in stderr.lower():
        return False
    raise AssertionError(
        f"`kubectl` failed while checking whether {what} exists, and the "
        "failure is not 'it does not exist' -- so this file cannot tell "
        "whether the cluster is below stage 4 or simply unreachable, and it "
        "must not guess. Exit code "
        f"{result.returncode}; stderr:\n{stderr.strip() or '(empty)'}\n"
        f"Context in use: {KUBE_CONTEXT!r}."
    )


def _prometheus_is_installed() -> bool:
    return _absent_or_fail(
        _kubectl(
            "-n", OBSERVABILITY_NAMESPACE, "get", f"svc/{PROMETHEUS_SERVICE}",
            "-o", "name", timeout=30,
        ),
        f"svc/{PROMETHEUS_SERVICE} in the {OBSERVABILITY_NAMESPACE} namespace",
    )


def _deployed_collector_config() -> str:
    result = _kubectl(
        "-n", OBSERVABILITY_NAMESPACE, "get", "configmap", COLLECTOR_CONFIGMAP,
        "-o", "jsonpath={.data.collector-config\\.yaml}", timeout=30,
    )
    if not _absent_or_fail(result, f"configmap/{COLLECTOR_CONFIGMAP}"):
        return ""
    return result.stdout


def _metrics_pipeline_exports_to_prometheus() -> bool:
    """Whether the DEPLOYED collector actually routes metrics to Prometheus.

    **Parsed, not grepped, and this is not a style preference -- the grep
    version of this check was written first and demonstrated inert.** Mutation
    run 2026-09-13: `- otlp_http/prometheus` was deleted from the `metrics`
    pipeline's exporter list and the ConfigMap re-applied, which is precisely
    the edit that stops every metric in this system from reaching Prometheus.
    `PROMETHEUS_EXPORTER_ID in _deployed_collector_config()` stayed TRUE and all
    fourteen tests in this file stayed green, because the string still occurs
    twice more in the file: once as the exporter's own declaration under
    `exporters:`, and once inside the comment above it. A declared-but-unused
    exporter is exactly the silently-broken shape this repo exists to catch.

    Reading the pipeline means a comment cannot satisfy it and neither can a
    dangling declaration.
    """
    raw = _deployed_collector_config()
    if not raw:
        return False
    parsed = yaml.safe_load(raw)
    pipelines = ((parsed or {}).get("service") or {}).get("pipelines") or {}
    exporters = (pipelines.get("metrics") or {}).get("exporters") or []
    return PROMETHEUS_EXPORTER_ID in exporters


@pytest.fixture(scope="module", autouse=True)
def stage_4_or_a_reason():
    """Skip below stage 4; fail on finding F10's incoherent middle state."""
    installed = _prometheus_is_installed()
    exports = _metrics_pipeline_exports_to_prometheus()
    if not installed and not exports:
        pytest.skip(
            f"svc/{PROMETHEUS_SERVICE} is not installed and the deployed "
            f"collector config does not name {PROMETHEUS_EXPORTER_ID!r}, so "
            "this cluster is coherently below stage 4. Bring it up with "
            "./scripts/setup-kind.sh --stage 4 to run these."
        )
    assert installed, (
        f"the deployed collector ConfigMap names the {PROMETHEUS_EXPORTER_ID!r} "
        f"exporter but svc/{PROMETHEUS_SERVICE} does not exist in the "
        f"{OBSERVABILITY_NAMESPACE} namespace. That is review finding F10's "
        "shape applied to metrics: a pipeline exporting to a backend this "
        "cluster does not have. scripts/setup-kind.sh strips the "
        "stage-4-only regions of the collector config below stage 4 -- check "
        "that it did."
    )
    assert exports, (
        f"svc/{PROMETHEUS_SERVICE} is installed but the deployed collector "
        f"ConfigMap's `metrics` pipeline does not export to "
        f"{PROMETHEUS_EXPORTER_ID!r}, so no metric can possibly reach it. "
        "Note this reads the PIPELINE, not the file: an exporter that is "
        "declared but listed in no pipeline is inert, and is what the "
        "mutation described on _metrics_pipeline_exports_to_prometheus "
        "produced. Re-run ./scripts/setup-kind.sh --stage 4, which applies "
        "the ConfigMap and restarts the collector."
    )


@pytest.fixture(scope="module")
def prometheus_url():
    """An ephemeral port-forward to `svc/prometheus`, owned by this file.

    `:9090` with no local port makes kubectl choose a free one and print it, so
    this cannot shadow `scripts/start-demo.sh`'s own 9090 forward even when
    both are up -- the same shape `test_opensearch_trace_ingest.py::gateway_url`
    uses, and for the same reason (ruling A9: a stray forward silently
    shadowing a host port has cost this project a measurement before).
    """
    proc = subprocess.Popen(
        [
            "kubectl", "--context", KUBE_CONTEXT, "-n", OBSERVABILITY_NAMESPACE,
            "port-forward", f"svc/{PROMETHEUS_SERVICE}", ":9090",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    port = None
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            match = re.search(r"Forwarding from 127\.0\.0\.1:(\d+) -> 9090", line)
            if match:
                port = int(match.group(1))
                break
        assert port, (
            f"could not establish a port-forward to svc/{PROMETHEUS_SERVICE} "
            f"in {OBSERVABILITY_NAMESPACE} -- is the kind-northstar cluster up?"
        )
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


def _query(prometheus_url: str, path: str, params: list[str] = ()) -> dict:
    """A Prometheus HTTP API call, made with curl through the forward.

    `curl` rather than `httpx` only so the command in the task report is the
    command this test runs; the API is the same either way.
    """
    command = ["curl", "-s", "--max-time", "30"]
    for param in params:
        command += ["--data-urlencode", param]
    command.append(f"{prometheus_url}{path}")
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (
        f"curl failed against {path}: rc={result.returncode} "
        f"stderr={result.stderr[:400]!r}"
    )
    body = json.loads(result.stdout)
    assert body.get("status") == "success", f"{path} returned {body!r}"
    return body["data"]


@pytest.fixture(scope="module")
def metric_names(prometheus_url) -> set[str]:
    return set(_query(prometheus_url, "/api/v1/label/__name__/values"))


@pytest.fixture(scope="module")
def jobs(prometheus_url) -> set[str]:
    return set(_query(prometheus_url, "/api/v1/label/job/values"))


def _series(prometheus_url: str, selector: str) -> list[dict]:
    """Every series matching `selector` over Prometheus's whole retention.

    Used for the ABSENCE assertions, deliberately unbounded in time: "this
    metric has never appeared in fifteen days" is a stronger negative than
    "it did not appear in the last few minutes".
    """
    return _query(prometheus_url, "/api/v1/series", [f"match[]={selector}"])


# How recent a sample has to be for a positive control to count.
#
# **Why the controls are time-bounded when the absences are not.** Prometheus's
# retention here is the chart default, 15 days
# (`--storage.tsdb.retention.time=15d` on the running container). An unbounded
# presence check would therefore stay green for a fortnight after every service
# in this system stopped emitting -- which would quietly turn every absence in
# this file back into the false negative the whole design exists to prevent,
# and would do it invisibly. A control that proves the path worked two weeks
# ago is not a control.
#
# 15 minutes, not 5: the SDK's export interval is 60 s, the gate drives its
# traffic across several files, and cumulative temporality means a live process
# re-exports its accumulated points every interval, so a 15-minute window is
# generous for any cluster that is actually running while staying far short of
# proving nothing.
FRESH_WINDOW_S = 900

# How long to wait for a control's series to turn up before failing. One export
# interval is 60 s; two plus slack covers a settlement driven moments before
# this file runs, which is exactly what happens in a full gate run.
CONTROL_POLL_TIMEOUT_S = 150.0


def _recent_series(prometheus_url: str, selector: str) -> list[dict]:
    """Series matching `selector` with at least one sample inside
    `FRESH_WINDOW_S`, polled until `CONTROL_POLL_TIMEOUT_S`."""
    deadline = time.monotonic() + CONTROL_POLL_TIMEOUT_S
    found: list[dict] = []
    while True:
        now = time.time()
        found = _query(
            prometheus_url, "/api/v1/series",
            [f"match[]={selector}", f"start={now - FRESH_WINDOW_S:.0f}",
             f"end={now:.0f}"],
        )
        if found or time.monotonic() >= deadline:
            return found
        time.sleep(10)


# --- Control 1: the collector -> Prometheus hop, proved without any service --


def test_prometheus_holds_series_that_can_only_have_arrived_over_otlp(
    prometheus_url, metric_names, jobs
):
    """Exit criterion 5, and the first positive control.

    This Prometheus scrapes nothing -- every one of the chart's ten default
    jobs is `enabled: false` in `deploy/observability/prometheus/values.yaml`,
    and the rendered `prometheus.yml` has an empty `scrape_configs:`. So a
    SPIRE series in here did not come from a scrape *by Prometheus*: it was
    scraped by the collector's `prometheus/spire` receiver and pushed over
    OTLP, which is the only door in.

    Asserting this first matters. Every "no such metric" below is read out of
    the same store, and an empty answer from a store nothing can reach is
    indistinguishable from an empty answer about a silent framework.
    """
    assert "spire-server" in jobs and "spire-agent" in jobs, (
        "neither SPIRE job is present in Prometheus, so the "
        "collector -> Prometheus OTLP hop is not demonstrably working and no "
        f"absence measured below can be trusted. Jobs present: {sorted(jobs)}"
    )
    assert _recent_series(prometheus_url, '{job="spire-server"}'), (
        "SPIRE has a `job` label in Prometheus but no sample inside the last "
        f"{FRESH_WINDOW_S // 60} minutes, so this control is proving only "
        "that the OTLP hop worked at some point in the retention window, not "
        "that it works now. Check the collector's `prometheus/spire` receiver "
        "and its otlp_http/prometheus exporter."
    )
    spire_names = {name for name in metric_names if name.startswith("spire_")}
    assert len(spire_names) > 100, (
        f"only {len(spire_names)} spire_* metric names in Prometheus. SPIRE "
        "1.15.3 exposes several hundred; a handful suggests the collector's "
        "scrape or its OTLP export is partly failing, which would make every "
        "absence below unsafe to read"
    )

    deployed = _deployed_collector_config()
    assert "prometheus/spire" in deployed, (
        "the deployed collector config no longer declares the "
        "`prometheus/spire` receiver, so this control has stopped being "
        "independent of the Python services it is meant to be a control for"
    )


def test_prometheus_itself_scrapes_nothing(prometheus_url):
    """The other half of the control above: if Prometheus grew a scrape job,
    the argument "a series in here arrived over OTLP" would silently stop
    holding, and this file's every conclusion with it.

    Asserted against the *running* server's own targets API, not the values
    file -- a values file says what was intended, `/api/v1/targets` says what
    is.
    """
    targets = _query(prometheus_url, "/api/v1/targets")
    active = targets.get("activeTargets") or []
    assert active == [], (
        "Prometheus has active scrape targets: "
        f"{[t.get('scrapePool') for t in active]}. "
        "deploy/observability/prometheus/values.yaml disables all ten default "
        "jobs so that OTLP is the only ingestion path, which is what makes M4 "
        "exit criterion 5 demonstrable and what makes every absence this file "
        "measures attributable to the framework rather than to a scrape."
    )


# --- Control 2 + the finding: per-service presence, then the absences --------


# (job label, a metric that service is known to emit, why it is known)
#
# The `job` label is Prometheus's rendering of OTLP's `service.name` resource
# attribute, so these are the service names, not Deployment names.
# `senior-settlement-agent` is deliberately absent from this list: it runs the
# settlement-agent image with `OTEL_SERVICE_NAME=settlement-agent` on purpose
# (deploy/k8s/senior-settlement-agent.yaml's own header -- the demo's point is
# that the code is byte-identical and the platform tells the two apart by
# identity), so it has no `job` of its own by design.
SERVICES_WITH_A_KNOWN_METRIC = (
    (
        "settlement-agent",
        "gen_ai_client_token_usage_count",
        "PydanticAI records it on every model turn "
        "(pydantic_ai/models/instrumented.py::record_metrics)",
    ),
    (
        "claims-agent",
        "http_server_request_duration_seconds_count",
        "the ASGI/Starlette instrumentation records it on every inbound request",
    ),
    (
        "payments-mcp",
        "http_client_request_duration_seconds_count",
        "HTTPXClientInstrumentor records it on every call to payments-api",
    ),
    (
        "reviewer-ui",
        "http_client_duration_milliseconds_count",
        "HTTPXClientInstrumentor records it on every ledger read -- note the "
        "LEGACY name: reviewer-ui is the one service whose manifest sets no "
        "OTEL_SEMCONV_STABILITY_OPT_IN, measured 2026-09-13 and recorded in "
        "docs/telemetry-contract.md",
    ),
)


@pytest.mark.parametrize(
    "job,known_metric,why",
    SERVICES_WITH_A_KNOWN_METRIC,
    ids=[row[0] for row in SERVICES_WITH_A_KNOWN_METRIC],
)
def test_each_service_demonstrably_gets_a_metric_all_the_way_to_prometheus(
    prometheus_url, job, known_metric, why
):
    """The per-service positive control, one test each.

    Before M4 Task 2 every one of these services had no `MeterProvider` at all,
    so `metrics.get_meter_provider()` returned the API's `_ProxyMeterProvider`
    and every measurement was discarded inside the process. This asserts the
    opposite is now true *per service*, against live data -- which is the
    precondition for reading anything into the absences below.
    """
    series = _recent_series(
        prometheus_url, f'{{__name__="{known_metric}",job="{job}"}}'
    )
    assert series, (
        f"{job} has no {known_metric} series in the last "
        f"{FRESH_WINDOW_S // 60} minutes of Prometheus, though {why}. "
        "Until it does, this service's lack of gen_ai.* metrics says nothing "
        "about any framework -- only that nothing from this process is "
        "arriving at all. Check, in order: the MeterProvider is installed "
        "(its own suite's test_telemetry.py), the service has been redeployed "
        "since M4 Task 2, and the collector logs no export failure over the "
        "WHOLE window (`kubectl -n observability logs deploy/otel-collector "
        "--since=<pod age>`, never a --tail of it). If the cluster has simply "
        "been idle, drive traffic first -- this file deliberately does not "
        "accept a stale series as proof that the path still works."
    )


def _assert_the_genai_path_is_live(prometheus_url: str, absence: str) -> None:
    """The in-test positive control every absence below is paired with.

    **Fix round 1, review finding F3.** This module's docstring claims that no
    test asserts an absence "without, in the same test, asserting a presence".
    That was false for three of the fourteen: they read only the cached
    `metric_names` set and passed against a completely empty Prometheus. The
    file as a whole still went red in that situation -- the control tests above
    see to that -- but the claim was made per-test and the three did not have
    it, and a docstring is exactly where a reader goes to decide whether a
    published negative is trustworthy. Making the three honest is the better of
    the two available fixes, because an absence assertion that passes against
    an empty backend is the defect class this repo keeps re-finding.

    The control is `gen_ai.client.token.usage`, not SPIRE, and deliberately so:
    it is the single strongest one available for a `gen_ai.*` absence, because
    it shares EVERY layer with the metric that is missing -- PydanticAI's own
    meter, the process's MeterProvider, the PeriodicExportingMetricReader, the
    OTLPMetricExporter, the collector's `metrics` pipeline, the
    `otlp_http/prometheus` exporter, and Prometheus's OTLP receiver. If it is
    present and recent, none of those layers can be the reason another
    `gen_ai.*` name is not.
    """
    missing = [
        name for name in _prometheus_histogram_names(EMITTED_GENAI_METRIC)
        if not _recent_series(prometheus_url, f'{{__name__="{name}"}}')
    ]
    assert not missing, (
        f"the positive control for this test is not in Prometheus: "
        f"{EMITTED_GENAI_METRIC} is missing {missing} from the last "
        f"{FRESH_WINDOW_S // 60} minutes. Until that is fixed this test's "
        f"finding -- {absence} -- is UNPROVEN rather than true: an empty "
        "answer from a broken path is indistinguishable from an empty answer "
        "about a silent framework, which is the false negative this whole "
        "file exists to prevent. Fix the path (see "
        "test_the_one_genai_metric_any_framework_emits_is_present's message "
        "for what to check), then re-read the absence."
    )


def test_the_one_genai_metric_any_framework_emits_is_present(
    prometheus_url, metric_names
):
    """`gen_ai.client.token.usage`, the single row of §6's table that is not a
    gap. Published in docs/telemetry-contract.md with its Development stability
    qualifier.

    This is also what makes the five absences below readable: the same
    MeterProvider, the same PeriodicExportingMetricReader, the same
    OTLPMetricExporter, the same collector pipeline and the same Prometheus
    receiver carried THIS histogram. Nothing in that chain is a candidate
    explanation for the other five.
    """
    expected = _prometheus_histogram_names(EMITTED_GENAI_METRIC)
    missing = [
        name for name in expected
        if not _recent_series(prometheus_url, f'{{__name__="{name}"}}')
    ]
    assert not missing, (
        f"{EMITTED_GENAI_METRIC} is missing {missing} from the last "
        f"{FRESH_WINDOW_S // 60} minutes of Prometheus. "
        "pydantic_ai 2.42.0 creates this histogram in "
        "InstrumentationSettings.__init__ and records into it in "
        "record_metrics(), so its absence means the path is broken rather "
        "than that the framework went quiet -- and every 'not emitted' "
        "verdict in docs/telemetry-contract.md's metrics section depends on "
        "this test passing."
    )


@pytest.mark.parametrize(
    "metric",
    [m for m in SIX_GENAI_METRICS if m != EMITTED_GENAI_METRIC],
)
def test_the_other_five_genai_metrics_are_emitted_by_nothing(
    prometheus_url, metric, metric_names
):
    """The finding, asserted so it cannot rot into a stale sentence.

    Five of the six metrics §6 asks about reach Prometheus from no service in
    this system, and the reason is not this project's wiring: measured
    2026-09-13 by reading the installed packages, **not one of these names
    appears anywhere in pydantic_ai 2.42.0, a2a-sdk 0.3.7, fastmcp 4.0.3 or
    mcp 2.2.0** -- and the last three contain no `get_meter(` call at all, so
    they create no instrument of any kind. They are dropped at the SDK, before
    any reader, exporter, collector or Prometheus could be blamed.
    docs/upstream-findings.md Finding 12 is the write-up.

    **If one of these ever starts arriving, this test goes red, and that is the
    intended behaviour.** It means a framework has implemented the metric and
    the published inventory is now understating what the system emits. Move the
    row from the gap column to the emitted column in
    docs/telemetry-contract.md, and say which version changed it -- do not
    delete the assertion.
    """
    _assert_the_genai_path_is_live(prometheus_url, f"{metric} is emitted by nothing")
    candidates = set(_prometheus_histogram_names(metric)) | {metric.replace(".", "_")}
    present = sorted(candidates & metric_names)
    assert not present, (
        f"{metric} now has series in Prometheus ({present}), and "
        "docs/telemetry-contract.md publishes it as a measured gap. A "
        "framework in this system has started emitting it -- update the "
        "inventory and Finding 12 with the version that changed, in the same "
        "commit that changes this test."
    )


def test_no_genai_metric_arrived_under_a_name_this_file_did_not_predict(
    prometheus_url, metric_names
):
    """The backstop for the test above, and the reason it is not circular.

    Every assertion so far checks names this file derived. If a framework
    emitted `gen_ai.agent.duration`, or Prometheus's translation produced a
    spelling the helper above does not generate, the parametrised test would
    still pass while the published inventory was wrong. So: enumerate every
    `gen_ai`-ish name Prometheus actually holds and require it to be one this
    file already accounts for.
    """
    _assert_the_genai_path_is_live(
        prometheus_url, "the gen_ai inventory is closed at the names below"
    )
    accounted = set(_prometheus_histogram_names(EMITTED_GENAI_METRIC))
    surprises = sorted(
        name for name in metric_names
        if "gen_ai" in name and name not in accounted
    )
    assert not surprises, (
        f"Prometheus holds gen_ai metric names this file does not account "
        f"for: {surprises}. docs/telemetry-contract.md's metrics section "
        "publishes a closed inventory, so a new name means the document is "
        "now incomplete. Add it there -- with its instrument type, unit, "
        "attributes and stability qualifier, measured -- and here."
    )


def test_no_metric_was_invented_to_fill_a_framework_gap(
    prometheus_url, metric_names
):
    """§6's standing prohibition: "No metric is hand-rolled to fill a hole in a
    framework's coverage."

    The tempting shape is a `gen_ai.*`-named instrument authored in this repo's
    own source so a dashboard looks complete. That would make this project's
    telemetry disagree with what a future, fixed framework emits natively --
    the invented-convention risk the governing rule exists to avoid, and the
    same argument Gap 5 makes for not hand-setting
    `gen_ai.operation.name` on FastMCP's spans.

    **What this test can and cannot see, stated precisely -- review finding
    F5.** It reads Prometheus, so it can only say *which `gen_ai.*` names are
    in the store*, never *who authored them*. A hand-rolled instrument in this
    repo's own source under a name PydanticAI also emits would pass here, and
    two committed documents used to cite this test as proving origin. That
    citation was wrong and is corrected; the source-side half of §6's
    prohibition -- no `gen_ai.`-prefixed instrument created anywhere under
    `agents/`, `apps/`, `platform/`, `tools/` or `systems/` -- is enforced by
    `tests/unit/test_metrics_wiring.py::test_no_service_authors_a_metric_under_a_convention_namespace`,
    which needs no cluster. The two together are the guard; neither is alone.

    What remains here, and is worth keeping: the store must hold no `gen_ai.*`
    name beyond the one PydanticAI emits, whatever produced it.
    """
    _assert_the_genai_path_is_live(
        prometheus_url, "no gen_ai.* name beyond PydanticAI's is in the store"
    )
    genai = {name for name in metric_names if name.startswith("gen_ai")}
    allowed = set(_prometheus_histogram_names(EMITTED_GENAI_METRIC))
    invented = sorted(genai - allowed)
    assert not invented, (
        f"gen_ai metric names present that PydanticAI does not emit: "
        f"{invented}. If one of this repo's own services is authoring a "
        "gen_ai.* instrument, remove it: our components may author metrics, "
        "but under our own `northstar.*`/`policy.*` prefixes, never under a "
        "convention namespace a framework owns (M4 spec §6)."
    )
