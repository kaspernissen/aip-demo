"""M4 Task 1, fix round 1: the guard ruling M4-R6 asks for.

**Why this file exists at all.** M4 Task 1 shipped with the `opensearch/trace`
exporter permanently rejecting every `policy-decision-service` span. The
`policy.decision` span sets both `policy.decision` (a string) and
`policy.decision.retryable` (a bool); OpenSearch expands dotted keys into
objects at index time, so one document needed `attributes.policy.decision` to
be a scalar and an object at once, and OpenSearch answered `can't merge a non
object mapping [attributes.policy.decision] with an object mapping` -- once per
document, permanently, with the only evidence in a collector log nobody reads.
Jaeger held 142 spans for the reference trace and OpenSearch 139; the three
missing ones were exactly the three decision spans, and 0 of the index's 1,723
documents came from the policy decision service. **119 green unit tests saw
none of it**, and the report that shipped it called the path verified.

So ruling M4-R6: the re-measurement needs a guard, not a paragraph. Prose in a
config comment is what failed last time. These four tests are the deliverable:

1. every span Jaeger holds for one driven trace is in OpenSearch, compared as
   span-ID *sets* rather than counts, because "139 of 142" is a count that
   survived and a set difference names what did not;
2. the `policy.decision` span is in OpenSearch by name, by service, and with
   its decision attribute readable -- that single span is what Task 3's
   evidence view and Task 4's policy-adherence scorer exist to query;
3. Jaeger still receives the canonical attribute names (ruling M4-R5): the
   OpenSearch-compat rewrite lives in the `traces/opensearch` branch only, and
   an evidence view whose field names differ from the trace an operator reads
   in Jaeger is worse than no evidence view;
4. the collector logged no export rejection during the run -- read over the
   **whole** window since the run began, never a `--tail` of it. Finding F2 was
   a `--tail=200` against a 17,173-line log reported as "no errors at any point
   during or after the run".

**This file drives its own trace and never reads a recorded one.** It speaks
MCP to the gateway directly with a SPIRE-minted JWT-SVID, following
`tests/e2e/test_caller_identity.py`'s pattern (whose constants and
`_json_body` it imports rather than reimplements), so it needs no model call
and no browser: one `propose_payment` that is permitted and one
`execute_payment` above the €6,000 allowance that is denied. Both produce a
`policy.decision` span, one of each outcome, which is the point -- the bug
rejected permits and denials alike, because `policy.decision.retryable` is set
on every decision span (`pds/server.py:277`).

**It supplies its own `traceparent`** so the trace id is known before the call
is made, rather than searched for afterwards. Measured 2026-09-13: agentgateway
honours an inbound `traceparent` and the whole chain lands under it.

**Stage gate, and why it is not a way to hide.** OpenSearch is a stage-4
component, so on a stage-1/2/3 cluster there is nothing to compare against.
This file skips only when the OpenSearch Service is absent *and* the deployed
collector ConfigMap does not name the `opensearch/trace` exporter -- i.e. only
when the cluster is coherently below stage 4. A collector configured to export
to an OpenSearch that is not installed is finding F10's exact broken state, and
that combination **fails** here rather than skipping.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid

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
from tests.harness.jaeger import JaegerClient

OBSERVABILITY_NAMESPACE = "observability"
OPENSEARCH_SERVICE = "opensearch-cluster-master"
COLLECTOR_DEPLOYMENT = "deploy/otel-collector"
COLLECTOR_CONFIGMAP = "otel-collector-config"

# The exporter id both collector configs use, and the string the stage gate
# below looks for in the *deployed* ConfigMap rather than in the repo.
OPENSEARCH_EXPORTER_ID = "opensearch/trace"

# The index the `opensearch` exporter's default ss4o mapping mode writes traces
# to: `ss4o_traces-<dataset>-<namespace>`, both defaulting to "default" and
# "namespace" since neither is set in the exporter config. Measured in
# `_cat/indices` on this cluster.
TRACE_INDEX = "ss4o_traces-default-namespace"

DECISION_SPAN = "policy.decision"
PDS_SERVICE = "policy-decision-service"

# The collider, and its OpenSearch-branch-only replacement. Canonical name from
# platform/policy-decision-service/pds/telemetry.py:40; the replacement from
# the `attributes/opensearch-compat` processor in both collector configs.
ATTR_RETRYABLE_CANONICAL = "policy.decision.retryable"
ATTR_RETRYABLE_OPENSEARCH = "policy.decision_retryable"
ATTR_DECISION = "policy.decision"

# €6,200 against settlement-agent's €6,000 assisted execute allowance -- the
# same numbers the M3 denial scenario uses, so this file and the demo exercise
# the same rule.
DENIED_AMOUNT = 6200

# Services whose spans must all have landed before the trace is compared.
# `propose_payment` really executes, so payments-api is in the trace too.
EXPECTED_SERVICES = {"agentgateway", "payments-mcp", "payments-api", PDS_SERVICE}

# OpenSearch is near-real-time, and the collector batches on a 1s timeout, so
# documents appear a little after Jaeger has the spans. Poll to this deadline
# for the document count to reach Jaeger's span count; exceeding it is a real
# failure (that is what dropped spans look like), never a reason to compare
# whatever happened to be there.
OPENSEARCH_SETTLE_TIMEOUT_S = 90.0


def _kubectl(*args: str, stdin: str | None = None, timeout: int = 90):
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _opensearch(path: str, body: dict | None = None) -> dict:
    """One OpenSearch request, made from inside the cluster.

    `kubectl exec ... curl` rather than a port-forward on purpose: this file
    should not add a fifth thing that can leave a socket behind, and the repo's
    standing rule is to own every forward you start. The OpenSearch Service is
    reachable in-cluster without one.
    """
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
    return _kubectl(
        "-n", OBSERVABILITY_NAMESPACE, "get", f"svc/{OPENSEARCH_SERVICE}",
        "-o", "name", timeout=30,
    ).returncode == 0


def _deployed_collector_config() -> str:
    result = _kubectl(
        "-n", OBSERVABILITY_NAMESPACE, "get", "configmap", COLLECTOR_CONFIGMAP,
        "-o", "jsonpath={.data.collector-config\\.yaml}", timeout=30,
    )
    return result.stdout if result.returncode == 0 else ""


@pytest.fixture(scope="module", autouse=True)
def stage_4_or_a_reason():
    """Skip below stage 4; fail on finding F10's incoherent middle state."""
    installed = _opensearch_is_installed()
    exports = OPENSEARCH_EXPORTER_ID in _deployed_collector_config()
    if not installed and not exports:
        pytest.skip(
            f"svc/{OPENSEARCH_SERVICE} is not installed and the deployed "
            f"collector config does not name {OPENSEARCH_EXPORTER_ID!r}, so "
            "this cluster is coherently below stage 4. Bring it up with "
            "./scripts/setup-kind.sh --stage 4 to run these."
        )
    assert installed, (
        f"the deployed collector ConfigMap names the {OPENSEARCH_EXPORTER_ID!r} "
        f"exporter but svc/{OPENSEARCH_SERVICE} does not exist in the "
        f"{OBSERVABILITY_NAMESPACE} namespace. That is review finding F10: a "
        "traces pipeline exporting to a backend this cluster does not have, "
        "which fails permanently and logs into a file nobody reads. "
        "scripts/setup-kind.sh strips the stage-4-only regions of the "
        "collector config below stage 4 -- check that it did."
    )
    assert exports, (
        f"svc/{OPENSEARCH_SERVICE} is installed but the deployed collector "
        f"ConfigMap does not name the {OPENSEARCH_EXPORTER_ID!r} exporter, so "
        "no span can possibly reach it. Re-run ./scripts/setup-kind.sh "
        "--stage 4, which applies the ConfigMap and restarts the collector."
    )


@pytest.fixture(scope="module")
def gateway_url():
    """An ephemeral port-forward to `svc/agentgateway`, owned by this file.

    Same shape as `tests/e2e/test_caller_identity.py::gateway_url`: `:3000`
    with no local port makes kubectl choose a free one and print it, so this
    cannot shadow one of the repo-managed forwards even by accident, and the
    port is parsed out of kubectl's own line rather than guessed.
    """
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

    Never logged: it is a live bearer credential.
    """
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


class _Run:
    def __init__(self, trace_id, trace, started_at):
        self.trace_id = trace_id
        self.trace = trace
        self.started_at = started_at


@pytest.fixture(scope="module")
def ingest_run(gateway_url, svid):
    """One driven trace: a permitted proposal and a denied execution.

    Returns the completed Jaeger trace plus the wall-clock instant the run
    began, which the log test below uses to size its `--since` window.
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
                "clientInfo": {"name": "m4-opensearch-ingest-probe", "version": "0"},
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

    claim_id = f"CLM-M4INGEST-{uuid.uuid4().hex[:8]}"
    proposed = post(
        {
            "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "tools/call",
            "params": {
                "name": "propose_payment",
                "arguments": {
                    "claim_id": claim_id, "amount": DENIED_AMOUNT, "currency": "EUR",
                },
            },
        },
        session,
    )
    body = _json_body(proposed) or {}
    payment_id = ((body.get("result") or {}).get("structuredContent") or {}).get(
        "paymentId"
    )
    assert payment_id, (
        f"the proposal did not return a paymentId, so the execute call below "
        f"cannot reach the policy decision: {proposed.text[:300]!r}"
    )

    executed = post(
        {
            "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "tools/call",
            "params": {
                "name": "execute_payment",
                "arguments": {
                    "payment_id": payment_id, "amount": DENIED_AMOUNT,
                    "currency": "EUR",
                },
            },
        },
        session,
    )
    executed_body = _json_body(executed) or {}
    assert executed_body.get("error") is not None, (
        f"€{DENIED_AMOUNT} above a €6,000 allowance was not refused, so this "
        "run did not produce the denial half of the decision pair: "
        f"{executed.text[:300]!r}"
    )

    # `services=` makes this wait for the trace to look *complete* and then
    # hold steady for the harness's quiet window, so the span set compared
    # below is the final one rather than a snapshot mid-export.
    trace = JaegerClient().wait_for_trace_id(
        trace_id, services=EXPECTED_SERVICES, timeout=120.0
    )
    return _Run(trace_id, trace, started_at)


def _opensearch_spans(trace_id: str, expected: int) -> list[dict]:
    """Every indexed document for `trace_id`, once the count has caught up.

    Polls for the count to *reach* `expected` and never settles for less: a
    short count is the defect this file exists to catch, so it must be a
    failure with a named difference, not a quietly smaller list.
    """
    deadline = time.time() + OPENSEARCH_SETTLE_TIMEOUT_S
    count = -1
    while time.time() < deadline:
        count = _opensearch(
            f"/{TRACE_INDEX}/_count", {"query": {"term": {"traceId": trace_id}}}
        ).get("count", 0)
        if count >= expected:
            break
        time.sleep(3)
    hits = _opensearch(
        f"/{TRACE_INDEX}/_search",
        {"size": 1000, "query": {"term": {"traceId": trace_id}}},
    )["hits"]["hits"]
    return [hit["_source"] for hit in hits]


def test_opensearch_holds_every_span_jaeger_holds_for_the_driven_trace(ingest_run):
    """Ruling M4-R6's parity assertion, compared as sets and not as counts.

    The shipped defect was reported as "every one of that trace's 139 spans is
    queryable" -- individually true, and the trace had 142. Equal counts alone
    could still be satisfied by a substitution, so both directions of the set
    difference are asserted and both are named in the failure message.
    """
    jaeger_ids = {span.span_id for span in ingest_run.trace.spans}
    documents = _opensearch_spans(ingest_run.trace_id, len(jaeger_ids))
    opensearch_ids = {doc["spanId"] for doc in documents}

    missing = jaeger_ids - opensearch_ids
    extra = opensearch_ids - jaeger_ids
    described = {
        span.span_id: f"{span.service}:{span.operation}"
        for span in ingest_run.trace.spans
    }
    assert not missing, (
        f"trace {ingest_run.trace_id}: OpenSearch is missing "
        f"{len(missing)} of Jaeger's {len(jaeger_ids)} spans -- "
        f"{sorted(described[span_id] for span_id in missing)}. Spans are being "
        "dropped between the collector and the index. Read the WHOLE collector "
        "log, not a tail of it:\n"
        "  kubectl -n observability logs deploy/otel-collector --since=1h "
        "| grep 'Exporting failed'"
    )
    assert not extra, (
        f"trace {ingest_run.trace_id}: OpenSearch holds {len(extra)} span ids "
        f"Jaeger does not: {sorted(extra)}"
    )


def test_the_policy_decision_span_is_queryable_out_of_opensearch(ingest_run):
    """The one span M4 exists to make queryable.

    Tasks 3 and 4 both consume it: the evidence view reconstructs why a payout
    happened from it, and the policy-adherence scorer reads its decision. It is
    also the span the shipped ingest path rejected 100% of the time, so this
    asserts it by name, by service and by attribute value rather than trusting
    the parity test above to have covered it.
    """
    documents = _opensearch(
        f"/{TRACE_INDEX}/_search",
        {
            "size": 10,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"traceId": ingest_run.trace_id}},
                        {"match_phrase": {"name": DECISION_SPAN}},
                    ]
                }
            },
        },
    )["hits"]["hits"]
    decisions = [hit["_source"] for hit in documents]

    expected = len(ingest_run.trace.spans_named(DECISION_SPAN))
    assert expected == 2, (
        f"this run should produce exactly two {DECISION_SPAN} spans (a permit "
        f"for the proposal and a deny for the execution); Jaeger has "
        f"{expected}, so the scenario changed and this test is measuring "
        "something else"
    )
    assert len(decisions) == expected, (
        f"Jaeger has {expected} {DECISION_SPAN} spans for trace "
        f"{ingest_run.trace_id} and OpenSearch has {len(decisions)}. This is "
        "the exact shape of the shipped defect: the decision span carries both "
        f"{ATTR_DECISION!r} and {ATTR_RETRYABLE_CANONICAL!r}, OpenSearch "
        "expands dotted keys into objects, and it refuses the document with "
        "`can't merge a non object mapping [attributes.policy.decision] with "
        "an object mapping`. The `attributes/opensearch-compat` processor in "
        "deploy/observability/collector-config*.yaml is what prevents it."
    )

    for document in decisions:
        assert document["resource"]["service.name"] == PDS_SERVICE, document
        attributes = document["attributes"]
        assert attributes.get(ATTR_DECISION) in {"permit", "deny"}, (
            f"the decision span indexed without a readable {ATTR_DECISION!r}: "
            f"{attributes!r}"
        )
        assert isinstance(attributes.get(ATTR_RETRYABLE_OPENSEARCH), bool), (
            f"{ATTR_RETRYABLE_OPENSEARCH!r} is missing or is not a boolean on "
            f"the indexed decision span, so the OpenSearch-compat rename in "
            f"the `traces/opensearch` pipeline is not doing what it claims: "
            f"{attributes!r}"
        )
        assert ATTR_RETRYABLE_CANONICAL not in attributes, (
            f"the indexed document still carries {ATTR_RETRYABLE_CANONICAL!r}, "
            "which is the key that collides -- the rename did not run, and "
            "indexing only succeeded by accident"
        )

    assert {document["attributes"][ATTR_DECISION] for document in decisions} == {
        "permit", "deny"
    }, (
        "both outcomes must be indexed, not just one: the rejection hit "
        f"permits and denials alike, because {ATTR_RETRYABLE_CANONICAL!r} is "
        "set on every decision span (pds/server.py:277)"
    )


def test_jaeger_still_receives_the_canonical_attribute_names(ingest_run):
    """Ruling M4-R5, asserted rather than assumed.

    The OpenSearch-compat rewrite runs in the `traces/opensearch` pipeline,
    downstream of the `forward/opensearch` connector. Jaeger and Dash0 export
    from the main `traces` pipeline, upstream of that tee. This test is what
    catches the rewrite leaking upstream -- which it could, if the collector's
    fanout ever stopped cloning for a mutating branch, or if someone
    'simplified' the branch away by renaming the attribute at the source.

    An evidence view whose field names differ from the trace an operator reads
    in Jaeger is worse than no evidence view.
    """
    for decision in ingest_run.trace.spans_named(DECISION_SPAN):
        assert ATTR_RETRYABLE_CANONICAL in decision.attrs, (
            f"Jaeger's {DECISION_SPAN} span has lost the canonical "
            f"{ATTR_RETRYABLE_CANONICAL!r} attribute the telemetry contract "
            f"defines: {sorted(decision.attrs)}"
        )
        assert ATTR_RETRYABLE_OPENSEARCH not in decision.attrs, (
            f"Jaeger's {DECISION_SPAN} span carries the OpenSearch-only name "
            f"{ATTR_RETRYABLE_OPENSEARCH!r}. The `attributes/opensearch-compat` "
            "processor has leaked out of the `traces/opensearch` branch into "
            "what Jaeger and Dash0 receive, which ruling M4-R5 forbids."
        )
        assert decision.attrs.get(ATTR_DECISION) in {"permit", "deny"}, (
            f"Jaeger's {DECISION_SPAN} span has no readable {ATTR_DECISION!r}: "
            f"{decision.attrs!r}"
        )


def test_the_collector_rejected_no_spans_during_the_run(ingest_run):
    """Finding F2: read the whole window, never a tail of it.

    The report that shipped the defect ran `logs --tail=200 | grep -i error`
    against a collector that had emitted 17,173 lines, and wrote down "no
    errors at any point during or after the run". The four rejections were in
    the window and outside the tail.

    So this sizes `--since` from the instant the run began and reads every line
    of it. It asserts on `Exporting failed` specifically -- the exporterhelper
    message that carries `rejected_items` -- rather than on any line containing
    "error", so it cannot be tripped by an unrelated warning and cannot be
    quieted by one either.
    """
    age_seconds = max(int(time.time() - ingest_run.started_at) + 30, 60)
    result = _kubectl(
        "-n", OBSERVABILITY_NAMESPACE, "logs", COLLECTOR_DEPLOYMENT,
        f"--since={age_seconds}s", timeout=120,
    )
    assert result.returncode == 0, (
        f"could not read the collector log:\nstderr:\n{result.stderr}"
    )
    lines = result.stdout.splitlines()
    rejections = [line for line in lines if "Exporting failed" in line]
    assert not rejections, (
        f"the collector rejected data during this run -- {len(rejections)} "
        f"`Exporting failed` events in the {age_seconds}s window "
        f"({len(lines)} lines read in full, not tailed):\n"
        + "\n".join(rejections[:5])
    )
