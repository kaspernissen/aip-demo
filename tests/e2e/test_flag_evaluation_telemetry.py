"""The logs pipeline's parity guard for feature-flag evaluation, measured live.

**Why this file exists.** A real backend can reject records permanently
while every unit test stays green and the only evidence sits in a collector
log nobody reads with `--tail` -- this repo has seen that shape before, for
traces. Logs are a newer signal here than traces or metrics, so the same
class of loss is possible on the first day any given signal's pipeline
exists, and "no errors" is not the measurement; parity against a second
count is.

**Two records are emitted per evaluation.** `pds/autonomy.py`'s
`resolve_autonomy` already carries the `openfeature-hooks-opentelemetry`
`TracingHook` (installed on `pds.server`'s module-scope client), which puts a
`feature_flag.evaluation` SPAN EVENT on the active `policy.decision` span --
verified independently by `tests/test_autonomy.py::
test_the_tracing_hook_puts_a_feature_flag_evaluation_event_on_the_active_span`.
That is signal #1 and this file does not re-prove it. Signal #2 is
`pds/autonomy.py`'s `emit_evaluation_event`, called from `server.py` right
after `resolve_autonomy`, which sends a genuine LOG RECORD of the same name
through a `LoggerProvider` `pds/telemetry.py` wires -- the third signal this
service exports, after traces and metrics. This file is the live proof that
record survives the collector and lands in OpenSearch.

**The standalone OTel Events API does not exist in the installed SDK.**
`opentelemetry-api`/`-sdk` 1.44.0 has no `opentelemetry._events` or
`opentelemetry.sdk._events` module (measured: both raise
`ModuleNotFoundError`) -- that API was folded into the Logs API. What is
actually emitted is an `opentelemetry._logs.Logger.emit(event_name=...,
attributes=...)` call, which is what `_collector_accepted_log_records` and
`_opensearch_log_documents` below are both measuring the delivery of.

**The tension this file does not paper over.** `resolution.version` (and
`.set_id`) measure `None` for every real evaluation against this deployment --
`featureflag.yaml`'s `autonomy-level` flag defines no `flag_metadata`
(measured directly here too, in `test_the_live_event_carries_no_
fabricated_version_or_set_id`). Per this repo's standing rule that a missing
value is absent, never zero or invented, `emit_evaluation_event` OMITS both
attributes entirely rather than writing `null` or `""` -- matching the
`policy.decision` span's own convention for the same field
(`server.py`: `if resolution.version is not None: span.set_attribute(...)`).
The eval-scorer routes around this: it reads `feature_flag.version` off the
`policy.decision` SPAN (also absent, same reason) and resolves the *current*
contract version from flagd directly, so its correctness does not depend on
this logs pipeline. This log event stays the human-readable record and the
parity target here, not a dependency of any score.

**Emitting a log record with no explicit `timestamp=` is a live trap.** It
measured `"@timestamp": "1970-01-01T00:00:00Z"` in
`ss4o_logs-default-namespace` -- the installed `LogRecord.__init__` only
defaults `observed_timestamp` to `time_ns()` when unset, and stores
`timestamp` verbatim, `None` included, which the OTLP exporter serialises as
the epoch. A Dashboards time-range query defaults to `@timestamp` and would
never surface a record stamped at the epoch: a real loss dressed as a
successful export, one field over from the loss this file's parity test
exists to catch. `emit_evaluation_event` passes `timestamp=time_ns()`
explicitly for this reason; `test_the_live_events_carry_a_real_
timestamp_not_the_epoch` is the live guard for it, alongside the hermetic one
in `platform/policy-decision-service/tests/test_autonomy.py`.

**Correlated by trace id, not by time window.** `Logger.emit()` picks up the
ambient span context automatically when no `context=` is passed (measured:
a log record emitted inside an active span carries that span's `trace_id`).
This file supplies its own `traceparent`, so it can query OpenSearch by the
exact trace id the driven call produced, the same technique `tests/e2e/
test_opensearch_trace_ingest.py` uses for spans.

**Stage gate.** OpenSearch is a stage-4 component and this logs pipeline is
wired stage-4-only in both collector configs (reusing the existing marker
and strip machinery rather than inventing a `stage-5-only` one). This file
skips only when the OpenSearch Service is absent *and* no `logs` pipeline in
the deployed collector ConfigMap exports to `opensearch/logs`; a collector
naming a backend the cluster does not have fails here rather than skipping,
the same discipline `test_opensearch_trace_ingest.py` applies to its own
signal.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from tests.e2e.test_caller_identity import (
    CALLER_SPIFFE_ID,
    GATEWAY_AUDIENCE,
    GATEWAY_NAMESPACE,
    KUBE_CONTEXT,
    MCP_PROTOCOL_VERSION,
    SPIRE_SERVER_POD,
    _json_body,
)
from tests.harness.collector import pipeline_exporters
from tests.harness.kube import absent_or_fail

OBSERVABILITY_NAMESPACE = "observability"
OPENSEARCH_SERVICE = "opensearch-cluster-master"
COLLECTOR_DEPLOYMENT = "deploy/otel-collector"
COLLECTOR_CONFIGMAP = "otel-collector-config"

# The exporter id both collector configs use for this signal, read out of the
# *deployed* ConfigMap's `logs` pipeline rather than out of the repo.
LOGS_EXPORTER_ID = "opensearch/logs"

# The index the `opensearch` exporter's default ss4o mapping mode writes LOG
# records to: `ss4o_logs-<dataset>-<namespace>`, both defaulting to "default"
# and "namespace" since neither is set in the exporter config -- the same
# shape `ss4o_traces-default-namespace` uses for traces. Measured in
# `_cat/indices` on this cluster.
LOGS_INDEX = "ss4o_logs-default-namespace"

EVENT_NAME = "feature_flag.evaluation"
FLAG_KEY = "autonomy-level"


def _kubectl(*args: str, stdin: str | None = None, timeout: int = 90):
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _opensearch(path: str, body: dict | None = None) -> dict:
    """One OpenSearch request, made from inside the cluster -- same shape as
    `test_opensearch_trace_ingest.py`'s helper, and for the same reason: no
    port-forward this file would have to remember to tear down."""
    args = [
        "-n", OBSERVABILITY_NAMESPACE, "exec", "-i", f"svc/{OPENSEARCH_SERVICE}",
        "--", "curl", "-s", "-X", "GET" if body is None else "POST",
        f"http://localhost:9200{path}",
    ]
    stdin = None
    if body is not None:
        args += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
        stdin = json.dumps(body)
    result = _kubectl(*args, stdin=stdin)
    assert result.returncode == 0, (
        f"could not reach OpenSearch at {path!r}:\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover -- diagnostic path
        raise AssertionError(
            f"OpenSearch returned a non-JSON body for {path!r}: "
            f"{result.stdout[:500]!r}"
        ) from exc


def _opensearch_is_installed() -> bool:
    return absent_or_fail(
        _kubectl(
            "-n", OBSERVABILITY_NAMESPACE, "get", f"svc/{OPENSEARCH_SERVICE}",
            "-o", "name", timeout=30,
        ),
        f"svc/{OPENSEARCH_SERVICE} in the {OBSERVABILITY_NAMESPACE} namespace",
    )


def _deployed_collector_config() -> str:
    result = _kubectl(
        "-n", OBSERVABILITY_NAMESPACE, "get", "configmap", COLLECTOR_CONFIGMAP,
        "-o", "jsonpath={.data.collector-config\\.yaml}", timeout=30,
    )
    if not absent_or_fail(result, f"configmap/{COLLECTOR_CONFIGMAP}"):
        return ""
    return result.stdout


@pytest.fixture(scope="module", autouse=True)
def stage_4_or_a_reason():
    """Skip below stage 4; fail on a collector naming a backend that is not
    there -- the same discipline applied to this signal as to traces.

    Unlike the traces pipeline, `logs` needs no `forward`/tee connector --
    `emit_evaluation_event`'s attributes have no colliding key pair, so the
    exporter is wired directly into the one `logs` pipeline. The
    discriminator here is therefore just the exporter id's presence in the
    deployed `logs` pipeline, plus the OpenSearch Service.
    """
    installed = _opensearch_is_installed()
    exporters = pipeline_exporters(_deployed_collector_config(), "logs")
    exports = LOGS_EXPORTER_ID in exporters
    if not installed and not exports:
        pytest.skip(
            f"svc/{OPENSEARCH_SERVICE} is not installed and no logs pipeline "
            f"in the deployed collector config exports to {LOGS_EXPORTER_ID!r}, "
            "so this cluster is coherently below stage 4. Bring it up with "
            "./scripts/setup-kind.sh --stage 5 to run these."
        )
    assert installed, (
        f"the deployed collector ConfigMap's logs pipeline exports to "
        f"{LOGS_EXPORTER_ID!r} but svc/{OPENSEARCH_SERVICE} does not exist in "
        f"the {OBSERVABILITY_NAMESPACE} namespace -- a collector naming a "
        "backend the cluster does not have. "
        "scripts/setup-kind.sh strips the stage-4-only regions of the "
        "collector config below stage 4 -- check that it did."
    )
    assert exports, (
        f"svc/{OPENSEARCH_SERVICE} is installed but the deployed collector "
        f"ConfigMap's logs pipeline does not export to {LOGS_EXPORTER_ID!r}, "
        f"so no feature_flag.evaluation record can possibly reach it. It "
        f"exports to: {sorted(exporters) or 'nothing'}. Re-run "
        "./scripts/setup-kind.sh --stage 5, which applies the ConfigMap and "
        "restarts the collector."
    )


@pytest.fixture(scope="module")
def gateway_url():
    """An ephemeral port-forward to `svc/agentgateway`, owned by this file --
    same shape as the other e2e modules that need one."""
    proc = subprocess.Popen(
        [
            "kubectl", "--context", KUBE_CONTEXT, "-n", GATEWAY_NAMESPACE,
            "port-forward", "svc/agentgateway", ":3000",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    port = None
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            match = re.search(r"Forwarding from 127\.0\.0\.1:(\d+) -> 3000", line)
            if match:
                port = int(match.group(1))
                break
        assert port, (
            "could not establish a port-forward to svc/agentgateway in "
            f"{GATEWAY_NAMESPACE} -- is the kind-northstar cluster up?"
        )
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def svid():
    """A real JWT-SVID for settlement-agent, minted by SPIRE at runtime.
    Never logged: it is a live bearer credential."""
    result = _kubectl(
        "-n", GATEWAY_NAMESPACE, "exec", SPIRE_SERVER_POD, "-c", "spire-server",
        "--", "/opt/spire/bin/spire-server", "jwt", "mint",
        "-spiffeID", CALLER_SPIFFE_ID, "-audience", GATEWAY_AUDIENCE,
        "-output", "json",
    )
    assert result.returncode == 0, (
        "`spire-server jwt mint` failed, so this file cannot drive the gateway:"
        f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)["svid"]["token"]


class _DrivenClaim:
    def __init__(self, trace_id: str, started_at: float):
        self.trace_id = trace_id
        self.started_at = started_at


@pytest.fixture(scope="module")
def driven_claim(gateway_url, svid):
    """One `propose_payment` call, under a `traceparent` this file supplies
    itself -- so the trace id is known before the call is made, the same
    technique `test_opensearch_trace_ingest.py` uses. `resolve_autonomy` (and
    the `emit_evaluation_event` call right after it, in `server.py`) runs
    unconditionally for every `tools/call` the PDS's `Check` handles, so a
    single decision is sufficient to produce one `feature_flag.evaluation`
    log record -- unlike the trace-parity file, this does not need a second,
    denied call.
    """
    started_at = time.time()
    trace_id = uuid.uuid4().hex
    traceparent = f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01"
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "authorization": f"Bearer {svid}",
        "traceparent": traceparent,
    }

    def post(payload: dict, extra: dict) -> httpx.Response:
        return httpx.post(
            f"{gateway_url}/mcp", json=payload, headers={**headers, **extra},
            timeout=30,
        )

    init = post(
        {
            "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "m5-flag-telemetry-probe", "version": "0"},
            },
        },
        {},
    )
    session_id = init.headers.get("mcp-session-id")
    assert init.status_code == 200 and session_id, (
        "the gateway did not open an MCP session, so this file cannot drive a "
        f"decision: HTTP {init.status_code} body={init.text[:300]!r}"
    )
    session = {"mcp-session-id": session_id}

    claim_id = f"CLM-M5FLAGTELEMETRY-{uuid.uuid4().hex[:8]}"
    proposed = post(
        {
            "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "tools/call",
            "params": {
                "name": "propose_payment",
                "arguments": {"claim_id": claim_id, "amount": 100, "currency": "EUR"},
            },
        },
        session,
    )
    body = _json_body(proposed) or {}
    payment_id = ((body.get("result") or {}).get("structuredContent") or {}).get(
        "paymentId"
    )
    assert payment_id, (
        "the proposal did not return a paymentId, so no policy.decision was "
        f"reached and no feature_flag.evaluation could have been emitted: "
        f"{proposed.text[:300]!r}"
    )
    return _DrivenClaim(trace_id, started_at)


# The collector's own `debug` exporter, sitting alongside `opensearch/logs` in
# the SAME `logs` pipeline, downstream of the SAME `batch` processor -- so
# whatever it logs having received is a second, independent count of what the
# collector accepted, taken from a source that is not OpenSearch. Verbosity
# "normal" prints one summary line per export call, of the form:
#   {"otelcol.component.id": "debug", ..., "otelcol.signal": "logs",
#    "resource logs": 1, "log records": 1}
# Measured directly against this cluster (driving one call and reading
# `kubectl logs` immediately after); not assumed from the exporter's
# documentation.
_DEBUG_LOGS_SUMMARY_RE = re.compile(
    r'"otelcol\.component\.id":\s*"debug".*?"otelcol\.signal":\s*"logs".*?'
    r'"log records":\s*(\d+)'
)


def _collector_accepted_log_records(since_seconds: int) -> int:
    """How many log records the collector's `debug` exporter reports having
    received, summed across every export in the window.

    Read over the WHOLE window (`--since=<n>s`), never a `--tail` of it --
    this repo has lost every `policy.decision` span exactly this way before,
    reading a 200-line tail of a much longer log and reporting 'no errors'.
    `--since` is sized
    from the instant this file's driven call began, with generous padding.
    """
    result = _kubectl(
        "-n", OBSERVABILITY_NAMESPACE, "logs", COLLECTOR_DEPLOYMENT,
        f"--since={since_seconds}s", timeout=120,
    )
    assert result.returncode == 0, (
        f"could not read the collector log:\nstderr:\n{result.stderr}"
    )
    return sum(
        int(match.group(1))
        for match in _DEBUG_LOGS_SUMMARY_RE.finditer(result.stdout)
    )


def _opensearch_log_count_since(since_epoch: float) -> int:
    """Every document the logs index holds with `@timestamp` at or after
    `since_epoch` (a Unix timestamp) -- regardless of trace.

    The parity test needs a count taken from the SAME window on both sides,
    and 'normal' debug verbosity's summary line (measured: `{"otelcol.
    component.id": "debug", ..., "log records": N}`) carries no per-record
    trace id to filter by -- only `_opensearch_log_documents`'s per-document
    reads do, from the indexed document itself. So the parity comparison is
    windowed, not trace-scoped: both sides count everything since the
    instant this file's call began, which is bounded on the collector side by
    `--since=<n>s` (measured against "now") and here by an explicit
    `@timestamp` lower bound -- the two are computed from the same
    `since_epoch` so neither window can drift wider than the other by
    construction.
    """
    since_iso = (
        datetime.fromtimestamp(since_epoch, tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    )
    return _opensearch(
        f"/{LOGS_INDEX}/_count",
        {"query": {"range": {"@timestamp": {"gte": since_iso}}}},
    ).get("count", 0)


def _opensearch_log_documents(trace_id: str, expected: int) -> list[dict]:
    """Every `feature_flag.evaluation` document OpenSearch holds for
    `trace_id`, once the count has caught up. Polls for the count to *reach*
    `expected` and never settles for less -- a short count is the defect this
    file exists to catch, so it must be a failure with a named difference,
    never a quietly smaller list."""
    deadline = time.time() + 90.0
    count = -1
    while time.time() < deadline:
        count = _opensearch(
            f"/{LOGS_INDEX}/_count",
            {"query": {"term": {"traceId": trace_id}}},
        ).get("count", 0)
        if count >= expected:
            break
        time.sleep(3)
    hits = _opensearch(
        f"/{LOGS_INDEX}/_search",
        {"size": 1000, "query": {"term": {"traceId": trace_id}}},
    )["hits"]["hits"]
    return [hit["_source"] for hit in hits]


def test_flag_evaluation_events_reach_opensearch_without_loss(driven_claim):
    """The parity assertion this file exists for. 'No errors' is not the
    measurement; parity against an independent count taken from the same
    pipeline's other exporter is.

    First, confirm THIS call's own record specifically arrived (also settles
    the whole pipeline generally: by the time it does, the batch upstream of
    both exporters has necessarily flushed). Then compare a WINDOWED count,
    not a trace-scoped one: 'normal' debug verbosity's summary line (measured:
    `{"otelcol.component.id": "debug", ..., "log records": N}`) carries no
    per-record trace id, so the only apples-to-apples comparison against it is
    "everything since this call began", taken on both sides from the same
    `since_epoch`. The window comparison is retried for a few seconds, not
    read once: OpenSearch indexing and the collector's own stdout can each
    lag the other by a beat, and a single-shot read can race the PDS's 1s
    `BatchLogRecordProcessor`.
    """
    documents = _opensearch_log_documents(driven_claim.trace_id, expected=1)
    assert documents, (
        f"no feature_flag.evaluation document reached OpenSearch for trace "
        f"{driven_claim.trace_id} within the settle window -- either the PDS "
        "did not emit the event at all (check pds/server.py calls "
        "emit_evaluation_event) or it never reached OpenSearch"
    )

    since_epoch = driven_claim.started_at - 2  # small buffer for clock skew
    deadline = time.time() + 20.0
    collector_count = opensearch_count = -1
    while time.time() < deadline:
        age_seconds = max(int(time.time() - since_epoch) + 2, 5)
        collector_count = _collector_accepted_log_records(age_seconds)
        opensearch_count = _opensearch_log_count_since(since_epoch)
        if collector_count == opensearch_count:
            break
        time.sleep(2)

    # Step 8's mandated `==` -> `>=` mutation, run through scripts/mutate-guard.sh
    # against the live cluster, first came back GREEN -- disbelieved, not waved
    # through. Diagnosis: with real (lossless) counts the loop above already
    # converges `collector_count == opensearch_count` before this line runs, so
    # swapping the operator on two already-equal integers cannot redden either
    # way; that GREEN proved nothing, favourable or not. It became a real guard
    # only once a genuine one-record discrepancy was forced INTO the comparison
    # (mutating the line itself to `opensearch_count + 1 == collector_count`):
    # that redenned correctly (RED, "collector counted 1 ... OpenSearch holds
    # 1", i.e. `assert (1 + 1) == 1`), proving the equality is load-bearing
    # against a real mismatch. Combining that same forced mismatch with the
    # mandated operator swap (`opensearch_count + 1 >= collector_count`)
    # reproduced the GREEN on purpose: a true one-record loss makes
    # `collector_count == opensearch_count + 1`, so `opensearch_count + 1 >=
    # collector_count` reads `opensearch_count + 1 >= opensearch_count + 1` --
    # always true. `>=` here would silently forgive exactly a one-document
    # loss, which is the one thing this assertion exists to catch. `==` stays.
    assert opensearch_count == collector_count, (
        f"the collector's debug exporter counted {collector_count} log "
        f"record(s) since this call began, but OpenSearch holds "
        f"{opensearch_count} for the same window -- the difference is loss, "
        "and loss looks exactly like sampling to an audience. Read the WHOLE "
        "collector log, not a tail of it:\n"
        f"  kubectl -n {OBSERVABILITY_NAMESPACE} logs {COLLECTOR_DEPLOYMENT} "
        f"--since={age_seconds}s | grep 'Exporting failed'"
    )


def test_the_live_event_is_the_semconv_event_with_the_hooks_two_gaps_filled(
    driven_claim,
):
    """The record is not just *present*; it is the right shape -- named
    `feature_flag.evaluation`, carrying the flag key and a readable result,
    correlated to the call that produced it by trace id (not by a time
    window), from the right service."""
    documents = _opensearch_log_documents(driven_claim.trace_id, expected=1)
    assert documents, f"no log document found for trace {driven_claim.trace_id}"
    document = documents[0]

    assert document["eventName"] == EVENT_NAME, document
    assert document["resource"]["service.name"] == "policy-decision-service", document
    assert document["spanId"], (
        "the log record carries no span id -- Logger.emit() should pick up "
        "the ambient policy.decision span's context automatically"
    )

    attributes = document["attributes"]
    assert attributes.get("feature_flag.key") == FLAG_KEY, attributes
    assert attributes.get("feature_flag.result.value") in {"assisted", "autonomous"}, (
        attributes
    )
    assert attributes.get("feature_flag.result.reason"), (
        "feature_flag.result.reason is missing or empty -- every real "
        "resolution carries one (resolve_autonomy defaults to 'UNKNOWN' at "
        "worst, never absent)"
    )
    assert attributes.get("feature_flag.context.id") == CALLER_SPIFFE_ID, attributes


def test_the_live_event_carries_no_fabricated_version_or_set_id(driven_claim):
    """The tension named explicitly above, checked live rather than only in
    the hermetic suite: `featureflag.yaml`'s `autonomy-level` flag defines
    no `flag_metadata`, so `feature_flag.set.id`/`feature_flag.version` must
    be ABSENT from the real record -- not `null`, not `""`. If this ever
    reddens because the contract grew `flag_metadata`, that is good news for
    the eval-scorer's audit and this assertion should be updated to match,
    not silently deleted."""
    documents = _opensearch_log_documents(driven_claim.trace_id, expected=1)
    attributes = documents[0]["attributes"]
    for key in ("feature_flag.set.id", "feature_flag.version"):
        assert key not in attributes, (
            f"{key!r} is present on the live event ({attributes.get(key)!r}) "
            "-- either featureflag.yaml now carries flag_metadata (update "
            "this test to assert the real value) or a fabricated placeholder "
            "was written where an absent value belongs"
        )


def test_the_live_events_carry_a_real_timestamp_not_the_epoch(driven_claim):
    """Emitting without an explicit `timestamp=` lands with `@timestamp` at
    the Unix epoch, which a Dashboards time-range query would never surface.
    Checked live, alongside the hermetic guard in
    `platform/policy-decision-service/tests/
    test_autonomy.py::test_emit_evaluation_event_stamps_a_real_timestamp`."""
    documents = _opensearch_log_documents(driven_claim.trace_id, expected=1)
    stamp = documents[0]["@timestamp"]
    assert not stamp.startswith("1970-01-01"), (
        f"the live record's @timestamp is {stamp!r} -- the epoch, which is "
        "what an unset timestamp= produces without "
        "emit_evaluation_event passing timestamp=time_ns() explicitly"
    )


def test_the_collector_rejected_no_logs_during_the_run(driven_claim):
    """Read the WHOLE window, never a `--tail` of it -- the exact discipline
    `test_opensearch_trace_ingest.py`'s equivalent guard states in full.
    Scoped to `Exporting failed` specifically, from any component, not
    narrowed to `opensearch/logs` alone, for the same reason that file
    scopes its own equivalent guard the same way."""
    age_seconds = max(int(time.time() - driven_claim.started_at) + 30, 60)
    result = _kubectl(
        "-n", OBSERVABILITY_NAMESPACE, "logs", COLLECTOR_DEPLOYMENT,
        f"--since={age_seconds}s", timeout=120,
    )
    assert result.returncode == 0, (
        f"could not read the collector log:\nstderr:\n{result.stderr}"
    )
    lines = result.stdout.splitlines()
    rejections = [line for line in lines if "Exporting failed" in line]
    components = sorted(
        set(re.findall(r'"otelcol\.component\.id":\s*"([^"]+)"',
                       "\n".join(rejections)))
    )
    assert not rejections, (
        f"the collector rejected data during this run -- {len(rejections)} "
        f"'Exporting failed' events in the {age_seconds}s window "
        f"({len(lines)} lines read in full, not tailed). Components: "
        f"{components or '(none parsed)'}\n" + "\n".join(rejections[:5])
    )
