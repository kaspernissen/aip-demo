"""Guards for the metrics wiring, from source rather than a cluster.

The live half is `tests/e2e/test_prometheus_metrics_ingest.py`. This is what
runs without a cluster, and it exists because the live file structurally
cannot see five things:

  * A service whose `_configure_metrics` was deleted, or never called, is
    indistinguishable in Prometheus from a service that simply recorded nothing
    in the window -- and "recorded nothing" is a legitimate state for
    `policy-decision-service`, which authors no metric at all. Only the source
    can tell those apart.
  * A `views=[View(..., DropAggregation())]` added to one service's provider
    would silently discard a series inside the SDK. Nothing downstream can see
    what never left the process.
  * The version pins, which are claims about the world rather than about this
    cluster.
  * **Who authored a series.** Prometheus records what a metric is called,
    never which source created it, so the live file cannot enforce the
    conventions' prohibition on hand-rolling a `gen_ai.*` instrument.
  * **A Deployment manifest that changes the SDK from outside the source.**
    `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` and
    `OTEL_METRIC_EXPORT_INTERVAL` falsify measured properties the contract
    publishes without touching a line of Python.

**Everything here is parsed, never grepped.** A substring check for the
Prometheus exporter in the deployed collector config stays green even after
the exporter is removed from the pipeline, since the string still appears in
the exporter's declaration and in a comment -- the e2e file's
`_metrics_pipeline_exports_to_prometheus` carries that lesson. The same rule
is applied here from the start: `ast` for Python, `yaml` for config, and
where a shell script has to be read, a comment-stripped copy.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_KIND = REPO_ROOT / "scripts/setup-kind.sh"
PROMETHEUS_VALUES = REPO_ROOT / "deploy/observability/prometheus/values.yaml"
COLLECTOR_CONFIGS = [
    REPO_ROOT / "deploy/observability/collector-config.yaml",
]

# Every Python service that gets a MeterProvider, and the module that installs
# it. `tools/payments-mcp` is included alongside settlement-agent,
# claims-agent, policy-decision-service and reviewer-ui because it is the
# process in which `gen_ai.execute_tool.duration` would be recorded if
# FastMCP recorded it. Without a provider there, "FastMCP emits no
# execute_tool metric" would be a claim about our wiring rather than about
# FastMCP -- see docs/upstream-findings.md Finding 12.
#
# `apps/customer-ui` is deliberately NOT here: it is a Next.js app with no
# Python and no OpenTelemetry dependency of its own (there is no
# `opentelemetry` import anywhere under apps/customer-ui). `systems/payments-api`
# is Go and is covered by the scope note in docs/telemetry-contract.md's metrics
# section, not by this list.
METRICS_BOOTSTRAPS = {
    "settlement-agent": REPO_ROOT / "agents/settlement-agent/bootstrap.py",
    "claims-agent": REPO_ROOT / "agents/claims-agent/bootstrap.py",
    "reviewer-ui": REPO_ROOT / "apps/reviewer-ui/bootstrap.py",
    "payments-mcp": REPO_ROOT / "tools/payments-mcp/bootstrap.py",
    "policy-decision-service":
        REPO_ROOT / "platform/policy-decision-service/pds/telemetry.py",
}

PROMETHEUS_EXPORTER_ID = "otlp_http/prometheus"
OTLP_RECEIVER_FLAG = "web.enable-otlp-receiver"


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def _function(path: Path, name: str) -> ast.FunctionDef | None:
    for node in _module(path).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _normalised_body(func: ast.FunctionDef) -> str:
    """The function's body as an AST dump, with its docstring removed.

    Docstrings are stripped because the five copies deliberately differ there:
    settlement-agent's carries the full rationale and the other four point at
    it, which is this repo's own convention for a block that must not be
    duplicated into five drifting copies. What must NOT differ is the code, and
    that is what this compares. Line numbers and column offsets are excluded by
    `ast.dump`'s defaults, so formatting differences do not register either.
    """
    body = list(func.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return "\n".join(ast.dump(node) for node in body)


# --- The provider exists, is installed, and is installed the same way --------


@pytest.mark.parametrize("service", sorted(METRICS_BOOTSTRAPS))
def test_every_python_service_installs_a_meter_provider(service):
    """Without this wiring, `metrics.get_meter_provider()` in each of these
    processes returns the API's `_ProxyMeterProvider` and every measurement is
    discarded inside the process.

    This binds to the *call*, not to the definition: a `_configure_metrics`
    that exists and is never called installs nothing, and is the shape that
    would leave the published inventory's every "not emitted" row unfounded.
    """
    path = METRICS_BOOTSTRAPS[service]
    assert path.exists(), f"{path.relative_to(REPO_ROOT)} does not exist"

    configure_metrics = _function(path, "_configure_metrics")
    assert configure_metrics is not None, (
        f"{path.relative_to(REPO_ROOT)} defines no `_configure_metrics`, so "
        f"{service} installs no MeterProvider and every framework metric it "
        "would emit is discarded at the SDK"
    )

    configure = _function(path, "configure")
    assert configure is not None, f"{path.relative_to(REPO_ROOT)} has no configure()"
    called = [
        node.func.id
        for node in ast.walk(configure)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "_configure_metrics" in called, (
        f"{path.relative_to(REPO_ROOT)}'s configure() never calls "
        "`_configure_metrics`. The function exists but installs nothing, so "
        f"{service}'s MeterProvider is the API proxy at runtime while this "
        "repo's source reads as though it were configured -- which is worse "
        "than not having written it, because the published inventory's "
        "absences would look measured and would not be."
    )


def test_the_five_copies_of_the_metrics_wiring_have_not_drifted():
    """Five services, one wiring. Four of the five docstrings say
    "`agents/settlement-agent/bootstrap.py`'s copy carries the full rationale",
    and that sentence is only honest if the code they point at is the code they
    run.

    A near-identical block copied N times is a block that drifts in N-1 of
    them, and a guard that diffs them is a better fix than a one-time
    correction.
    """
    bodies = {}
    for service, path in sorted(METRICS_BOOTSTRAPS.items()):
        for name in ("_configure_metrics", "_metric_reader"):
            func = _function(path, name)
            assert func is not None, (
                f"{path.relative_to(REPO_ROOT)} defines no `{name}`"
            )
            bodies.setdefault(name, {})[service] = _normalised_body(func)

    for name, per_service in bodies.items():
        distinct = set(per_service.values())
        assert len(distinct) == 1, (
            f"`{name}` differs between services. Each service's copy must be "
            "code-identical (docstrings may differ, and deliberately do):\n"
            + "\n".join(
                f"  {service}: {body[:200]}"
                for service, body in sorted(per_service.items())
            )
        )


# --- 100% of telemetry, metrics included ------------------------------------


# The metrics-side equivalents of `tests/unit/test_no_sampling.py`'s three
# assertions. A View with `DropAggregation` discards a whole instrument inside
# the SDK; a View with `attribute_keys` discards dimensions; a temporality
# preference of DELTA is not itself a drop but hands Prometheus increments it
# must re-total, and choosing one silently would be a change to what is stored.
# None of these is configured anywhere today -- this asserts the absence,
# because absence is exactly what a later edit removes silently while the rest
# of the suite stays green.
#
# **Matched against identifiers in the parsed tree, never against the file's
# text, and the first version of this guard got that wrong.** A substring scan
# reddened immediately on `agents/settlement-agent/bootstrap.py`, whose
# `_configure_metrics` docstring names all three while explaining why none is
# configured -- prose making a guard fail is the same defect as prose making
# one pass, and this repo has shipped the second kind more than once. Reading
# `ast.Name`, `ast.Attribute` and `keyword.arg` means only real code counts.
_FORBIDDEN_SDK_KNOBS = frozenset({
    "DropAggregation",
    "preferred_temporality",
    "preferred_aggregation",
    "attribute_keys",
    "View",
})


@pytest.mark.parametrize("service", sorted(METRICS_BOOTSTRAPS))
def test_nothing_configures_a_view_or_a_temporality_that_would_drop_data(service):
    path = METRICS_BOOTSTRAPS[service]
    tree = _module(path)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "MeterProvider":
            continue
        keywords = {kw.arg for kw in node.keywords}
        assert "views" not in keywords, (
            f"{path.relative_to(REPO_ROOT)} passes `views=` to MeterProvider. "
            "This demo records 100% of its telemetry, metrics included -- a "
            "View with DropAggregation is the metrics-side tail sampler, and "
            "one narrowing attribute_keys discards series just as effectively. "
            "Neither is visible to any collector-side guard, because what it "
            "drops never leaves the process."
        )

    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            used.add(node.arg)
    present = sorted(used & _FORBIDDEN_SDK_KNOBS)
    assert not present, (
        f"{path.relative_to(REPO_ROOT)} uses {present} in code, which are the "
        "SDK knobs that change or discard what is recorded. If one of these "
        "is genuinely needed, say why there and amend this guard in the same "
        "commit -- do not delete it. The standing requirement is that nothing "
        "in this demo samples, filters or aggregates telemetry away."
    )


# Every environment variable that lets a Deployment manifest change what the
# SDK records or how often, without touching a line of the Python this file
# otherwise guards. `agents/settlement-agent/bootstrap.py` publishes
# CUMULATIVE temporality and a 60 000 ms export interval as measured
# properties of this system, and both are settable from outside the source
# that was measured -- so the guard protecting those sentences cannot live
# only in the AST of the five bootstraps.
#
#   * `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` -- `delta` or
#     `lowmemory` replaces CUMULATIVE, handing Prometheus increments it must
#     re-total and falsifying the temporality the contract publishes.
#   * `OTEL_METRIC_EXPORT_INTERVAL` -- lengthening it is not sampling, but it
#     changes the published interval and the window `FRESH_WINDOW_S` in
#     tests/e2e/test_prometheus_metrics_ingest.py is sized against.
#   * `OTEL_METRICS_EXPORTER=none` and `OTEL_SDK_DISABLED=true` -- the two ways
#     to switch the whole thing off while every source-level guard here stays
#     green.
#   * `OTEL_METRICS_EXEMPLAR_FILTER` -- narrows what is retained alongside.
#
# All five are absent from every manifest today, measured by this test.
_FORBIDDEN_MANIFEST_ENV = frozenset({
    "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
    "OTEL_METRIC_EXPORT_INTERVAL",
    "OTEL_METRICS_EXPORTER",
    "OTEL_METRICS_EXEMPLAR_FILTER",
    "OTEL_SDK_DISABLED",
})


def test_no_deployment_manifest_changes_what_the_sdk_records():
    """The manifest-side half of "this demo never samples", metrics included.

    The AST guard above can only see the five Python files. A Deployment that
    sets `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE: delta` changes the
    same behaviour from outside them, with every unit test here staying green
    and `docs/telemetry-contract.md`'s measured temporality quietly false.

    Scoped to `deploy/k8s/`, the only place this repo sets application env.
    Written as a text scan of the whole manifest rather than a parse of the
    container `env:` lists on purpose: a variable set through `envFrom`, a
    ConfigMap reference or a second container would evade the structured read
    and would still take effect. Whole-line comments are stripped first, so a
    manifest may explain why it does NOT set one of these.
    """
    manifests = sorted((REPO_ROOT / "deploy/k8s").glob("*.yaml"))
    assert manifests, "deploy/k8s holds no manifests -- this guard has nothing to read"

    offenders = []
    for manifest in manifests:
        text = "\n".join(
            line for line in manifest.read_text().splitlines()
            if not line.lstrip().startswith("#")
        )
        for name in sorted(_FORBIDDEN_MANIFEST_ENV):
            if name in text:
                offenders.append(f"{manifest.name}: {name}")

    assert not offenders, (
        "these deploy/k8s manifests set an environment variable that changes "
        f"what the metrics SDK records: {offenders}. This demo records 100% of "
        "its telemetry, metrics included, and docs/telemetry-contract.md "
        "publishes the temporality and the export interval as MEASURED "
        "properties of the running system -- a manifest that overrides either "
        "makes a published number false while every source-level guard in "
        "this file stays green. If one is genuinely needed, re-measure the "
        "contract in the same commit and amend this guard with the reason -- "
        "do not delete it."
    )


# The Meter API's instrument factories, all seven
# (`opentelemetry/metrics/_internal/__init__.py`, class `Meter`). Any metric
# this repo ever authors is created by one of these.
_INSTRUMENT_FACTORIES = frozenset({
    "create_counter",
    "create_up_down_counter",
    "create_histogram",
    "create_gauge",
    "create_observable_counter",
    "create_observable_up_down_counter",
    "create_observable_gauge",
})

# Namespaces an instrumentation library owns. No metric here is hand-rolled
# to fill a hole in a framework's coverage: a locally-invented
# `gen_ai.invoke_agent.duration` would disagree with whatever a future, fixed
# PydanticAI emits natively, and every dashboard built on it would have to be
# rewritten twice. `northstar.*`, `policy.*`, `review.*` and `settlement.*` are
# this project's own and are deliberately not here.
_CONVENTION_NAMESPACES = ("gen_ai.", "mcp.", "http.", "rpc.", "db.", "messaging.")

# Where this repo's own application code lives. `.venv` and `node_modules` are
# excluded because they hold the frameworks themselves -- pydantic_ai really
# does create `gen_ai.client.token.usage`, and that is the finding rather than
# a violation.
_SOURCE_ROOTS = ("agents", "apps", "platform", "tools", "systems")
_EXCLUDED_DIRS = {".venv", "node_modules", "__pycache__", ".next", "dist", "build"}


def _repo_python_sources() -> list[Path]:
    files = []
    for root in _SOURCE_ROOTS:
        for path in (REPO_ROOT / root).rglob("*.py"):
            if _EXCLUDED_DIRS & set(path.relative_to(REPO_ROOT).parts):
                continue
            files.append(path)
    return sorted(files)


def test_no_service_authors_a_metric_under_a_convention_namespace():
    """The standing prohibition on hand-rolling a convention-namespaced
    metric, enforced at the only layer that can see it.

    `tests/e2e/test_prometheus_metrics_ingest.py`'s
    `test_no_metric_was_invented_to_fill_a_framework_gap` cannot prove that
    no `gen_ai.*` series "originates here": it reads Prometheus, and
    Prometheus records what a series is called, never who authored it. A
    hand-rolled instrument under a name PydanticAI also emits would pass it.
    This is the half that can see origin, and it needs no cluster.

    Bound to the instrument-creating CALL with a literal name, never to the
    string anywhere in the file: this repo's own tests and documents name
    `gen_ai.*` metrics constantly while describing the ones it does NOT emit,
    and a guard those trip is a guard that gets deleted. That is the same
    lesson `_FORBIDDEN_SDK_KNOBS` above records.

    `systems/payments-api` is Go and has no Python to walk. It has no
    MeterProvider at all, so it cannot author a metric of any kind today;
    docs/telemetry-contract.md's metrics section scopes it out.
    """
    offenders = []
    for path in _repo_python_sources():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            factory = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if factory not in _INSTRUMENT_FACTORIES:
                continue
            names = [
                arg.value for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            ]
            names += [
                kw.value.value for kw in node.keywords
                if kw.arg == "name" and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ]
            for name in names:
                if name.startswith(_CONVENTION_NAMESPACES):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}: {factory}({name!r})"
                    )

    assert not offenders, (
        "these services author a metric under a namespace an instrumentation "
        f"library owns: {offenders}. Hand-rolling a metric to fill a hole in "
        "a framework's coverage is forbidden -- an invented `gen_ai.*` "
        "instrument makes this project's telemetry disagree with whatever the "
        "framework emits once it implements the metric, which is the same "
        "argument docs/telemetry-contract.md's Gap 5 makes for not "
        "hand-setting `gen_ai.operation.name` on FastMCP's spans. Our own "
        "components may author metrics -- under `northstar.*`, `policy.*` or "
        "another prefix this project owns."
    )


def test_the_collector_metrics_pipeline_filters_nothing():
    """The collector-side half. `filter`, `tail_sampling` and
    `probabilistic_sampler` are the processors that would drop data points
    after the SDK already sent them; `transform` can too, by dropping metrics
    in an OTTL statement.

    `cumulativetodelta` and `deltatocumulative` are also forbidden, because
    they change or discard what Prometheus ends up storing just as
    effectively: they rewrite temporality *after* the SDK chose it, which
    would falsify the measured CUMULATIVE property the contract publishes, and
    `metricstransform` can drop or aggregate series outright. None belongs in
    this pipeline.

    `tests/unit/test_no_sampling.py` makes the same assertion for traces and
    for the processor map as a whole; this one is specifically about what the
    `metrics` pipeline runs, because a processor can be declared and used in
    one pipeline only.
    """
    forbidden = {
        "filter", "tail_sampling", "probabilistic_sampler", "transform",
        "cumulativetodelta", "deltatocumulative", "metricstransform",
    }
    for config in COLLECTOR_CONFIGS:
        parsed = yaml.safe_load(config.read_text())
        pipeline = parsed["service"]["pipelines"]["metrics"]
        used = {name.split("/")[0] for name in (pipeline.get("processors") or [])}
        assert not (used & forbidden), (
            f"{config.name}'s metrics pipeline runs "
            f"{sorted(used & forbidden)}. This demo records 100% of its "
            "telemetry -- remove the processor rather than tune its predicate."
        )


# --- The routing: collector -> Prometheus ------------------------------------


@pytest.mark.parametrize("config", COLLECTOR_CONFIGS, ids=lambda p: p.name)
def test_the_metrics_pipeline_exports_to_prometheus(config):
    """Asserted on the PIPELINE rather than on the file's text -- see this
    module's docstring for why a substring check here is inert.
    """
    parsed = yaml.safe_load(config.read_text())
    pipeline = parsed["service"]["pipelines"]["metrics"]
    assert PROMETHEUS_EXPORTER_ID in pipeline["exporters"], (
        f"{config.name}'s metrics pipeline does not export to "
        f"{PROMETHEUS_EXPORTER_ID!r}: {pipeline['exporters']}. An exporter "
        "declared under `exporters:` but listed in no pipeline is inert, and "
        "nothing this system emits reaches Prometheus."
    )
    assert PROMETHEUS_EXPORTER_ID in parsed["exporters"], (
        f"{config.name}'s metrics pipeline names {PROMETHEUS_EXPORTER_ID!r} "
        "but the `exporters` section does not declare it -- the collector "
        "refuses to start on this."
    )


@pytest.mark.parametrize("config", COLLECTOR_CONFIGS, ids=lambda p: p.name)
def test_the_exporter_endpoint_matches_the_service_the_chart_publishes(config):
    """The endpoint and the Service that answers it are named in two different
    files, so they can disagree. Both chart defaults are wrong for this
    endpoint -- the Service would be `prometheus-server` on port 80 -- so the
    values file's two overrides are what make it resolve, and deleting either
    of them breaks metric ingest with nothing else going red.
    """
    parsed = yaml.safe_load(config.read_text())
    endpoint = parsed["exporters"][PROMETHEUS_EXPORTER_ID]["endpoint"]

    values = yaml.safe_load(PROMETHEUS_VALUES.read_text())
    service_name = values["server"]["fullnameOverride"]
    service_port = values["server"]["service"]["servicePort"]

    expected = (
        f"http://{service_name}.observability.svc.cluster.local:{service_port}"
        "/api/v1/otlp"
    )
    assert endpoint == expected, (
        f"{config.name}'s {PROMETHEUS_EXPORTER_ID} endpoint is {endpoint!r}, "
        f"but deploy/observability/prometheus/values.yaml publishes the "
        f"Service as {service_name!r} on port {service_port}, which makes "
        f"{expected!r} the only address that resolves. `/api/v1/otlp` is the "
        "base path Prometheus serves the OTLP receiver on; the exporter "
        "appends `/v1/metrics` itself."
    )


def test_the_prometheus_values_enable_the_otlp_receiver():
    """The whole ingest path depends on one command-line flag.

    Its spelling was read out of Prometheus v3.14.0's own
    `cmd/prometheus/main.go:475` -- `--web.enable-otlp-receiver`, "Enable API
    endpoint accepting OTLP write requests" -- and NOT from memory or from a
    2.x-era `--enable-feature=otlp-write-receiver`, which that version's
    feature list no longer contains. Without it Prometheus starts happily, 404s
    every OTLP write, and this system's metrics vanish with only the
    collector's log to say so.
    """
    values = yaml.safe_load(PROMETHEUS_VALUES.read_text())
    flags = values["server"]["extraFlags"]
    assert OTLP_RECEIVER_FLAG in flags, (
        f"deploy/observability/prometheus/values.yaml's server.extraFlags is "
        f"{flags}, which does not include {OTLP_RECEIVER_FLAG!r}. The chart "
        "has no `otlp.enabled` boolean -- `server.otlp` is the Prometheus "
        "config-file section, not the receiver switch -- so this flag is the "
        "only thing that turns OTLP ingest on."
    )
    assert "web.enable-lifecycle" in flags, (
        "server.extraFlags replaces the chart's default list rather than "
        "appending to it, so dropping `web.enable-lifecycle` here silently "
        "removes a capability the chart shipped by default"
    )


def test_prometheus_scrapes_nothing_so_otlp_is_the_only_ingestion_path():
    """This Prometheus must run no scrape configuration, and this file's
    sibling in tests/e2e depends on it for something stronger: with every job
    disabled, any series in Prometheus can only have arrived over OTLP, which
    is what makes "no `gen_ai.execute_tool.duration` anywhere" attributable to
    the framework rather than to a scrape that did or did not fire.

    Re-enable one job and that argument quietly stops holding, which is exactly
    the kind of change that would otherwise go unnoticed.
    """
    values = yaml.safe_load(PROMETHEUS_VALUES.read_text())
    scrape_configs = values.get("scrapeConfigs") or {}
    assert scrape_configs, (
        "deploy/observability/prometheus/values.yaml declares no "
        "`scrapeConfigs` at all. The chart's default is ten ENABLED jobs and "
        "its own comment says each is disabled by setting `enabled: false` -- "
        "an empty map here means all ten are on."
    )
    enabled = sorted(
        job for job, spec in scrape_configs.items()
        if (spec or {}).get("enabled") is not False
    )
    assert not enabled, (
        f"these Prometheus scrape jobs are enabled: {enabled}. This "
        "Prometheus must have exactly one ingestion path, the OTLP receiver "
        "-- see this test's docstring for what the e2e inventory depends on."
    )


# --- Version pins agree across the two files that state them -----------------


def test_the_prometheus_chart_version_agrees_between_script_and_values():
    """`scripts/setup-kind.sh` installs a version; the values file's header
    documents one, with the two-source evidence this repo requires. A drift
    between them means the documented evidence is about a chart nobody
    installs -- the same drift `tests/unit/test_stage2_linkerd_config.py` and
    `test_stage2_spire_config.py` pin for Linkerd and SPIRE.
    """
    script = "\n".join(
        line for line in SETUP_KIND.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    match = re.search(r'^PROMETHEUS_CHART_VERSION="([^"]+)"$', script, re.MULTILINE)
    assert match, (
        "scripts/setup-kind.sh no longer defines PROMETHEUS_CHART_VERSION, so "
        "the chart version is either floating or gone"
    )
    pinned = match.group(1)

    header = PROMETHEUS_VALUES.read_text()
    assert f"pinned to {pinned}," in header, (
        f"scripts/setup-kind.sh installs Prometheus chart {pinned}, but "
        "deploy/observability/prometheus/values.yaml's header does not say "
        f"'pinned to {pinned},'. The header carries this repo's required "
        "two-source version evidence (Artifact Hub and the GitHub releases "
        "API); if the chart moved, re-verify against both and update the "
        "header in the same commit -- do not just change the number."
    )
    assert "prerelease" in header, (
        "the values file's version evidence no longer mentions prerelease "
        "status. This repo pins the newest NON-prerelease release, and the "
        "check that it is one is part of the evidence."
    )


def test_the_deprecated_otlphttp_alias_is_not_used():
    """The pinned collector image (otel-collector-contrib 0.162.0) accepts
    `otlphttp` and warns, verbatim:

        "otlphttp" alias is deprecated; use "otlp_http" instead

    -- the spelling in the reference config this block was copied from. It
    is renamed rather than left to warn, and this keeps it renamed, in step
    with the `otlp_grpc/` exporters spelled that way throughout.
    """
    for config in COLLECTOR_CONFIGS:
        parsed = yaml.safe_load(config.read_text())
        deprecated = sorted(
            name for name in parsed["exporters"]
            if name.split("/")[0] in {"otlphttp", "otlpgrpc"}
        )
        assert not deprecated, (
            f"{config.name} declares exporters under a deprecated alias: "
            f"{deprecated}. The collector logs a deprecation warning for each "
            "on every start; use `otlp_http`/`otlp_grpc`."
        )
