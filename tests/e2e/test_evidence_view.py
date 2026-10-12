"""The evidence view, asserted by its queries rather than its pixels.

The claim this demo needs to support on stage is *"Can we prove why €5,800
was paid?"*, and `docs/architecture.md`'s own section on the evidence view
answers it with one saved dashboard showing two claims side by side: €5,800
permitted straight through under a rule, €6,200 denied under **the same
rule**, escalated to a human, and executed by a different identity. This
file asserts the **queries** that dashboard is built from --
`deploy/observability/dashboards/evidence.ndjson` -- against live OpenSearch.

**Why the queries and not the dashboard.** Screenshot and layout assertions
are out of scope. A dashboard that renders beautifully and silently goes
blank when instrumentation regresses is worse than no dashboard, because it
is trusted. So every field the saved dashboard puts on screen is read here,
out of the same index, by the same two-step join -- and a regression
reddens this file instead of being noticed on stage.

**This file drives the runs it asserts on.** Same rule
`test_policy_denial.py` states and `test_opensearch_trace_ingest.py` repeats:
every assertion runs against traffic this file itself caused. It costs two
settlements' worth of Anthropic tokens (one denied, one permitted) and no
browser -- the claim goes to claims-agent as a direct A2A POST the way
`test_claim_correlation.py` sends one, and the reviewer's approval is a plain
form POST because reviewer-ui renders server-side with no JavaScript at all
(`apps/reviewer-ui/bootstrap.py`).

**Gated behind `RUN_BROWSER_TESTS=1` even though it starts no browser** --
`test_settlement_llm_trace.py`'s precedent, whose own `pytestmark` says the
same thing: the flag is this repo's single cost gate and
`scripts/smoke-test.sh` sets it unconditionally, so a gated run loses
nothing. Left ungated, a bare `pytest tests/` would drive **four**
settlements instead of two and -- new in kind, not only in degree -- execute
a real €6,200 payment under `senior-settlement-agent`'s identity, because
this is the only file that drives the whole supervised path outside
`test_supervised.py` (which gates 6 of its 7 tests for exactly that reason).
The three chain files that would otherwise spend tokens on every bare run --
`test_claim_correlation.py`, `test_claims_chain_trace.py`,
`test_settlement_agent_trace.py` -- carry the same gate, so a bare
`pytest tests/` spends nothing at all. See docs/telemetry-contract.md's own
section on it for the measurement and for what the bare run gives up in
exchange.

**The time window is load-bearing, not hygiene.** `CLM-1842` is a fixed
constant, so the index accumulates one set of traces per run of it forever;
a long-running cluster's history for `CLM-1842` can span many traces across
hours, some from before OpenSearch trace ingest existed at all and
therefore carrying no `policy.decision` span, and the total can grow past
the 1,000-document page `_spans_in_traces` reads and trip its own
truncation guard — so the bound is what keeps these queries answerable at
all, not only what keeps them correct. Every query below is bounded to the
window this module opened, and the saved dashboard is bounded by OpenSearch
Dashboards' own time picker, which is the same bound by a different name.
Without it both would answer with whichever run happened to sort first.

**Gap 4 is why the join is two steps.** See `_trace_ids_for_claim` and
`_spans_in_traces` below, and the header of
`deploy/observability/dashboards/evidence.ndjson`.

**Stage gate, and why it is not a way to hide.** OpenSearch is a stage-4
component. There is exactly one skip shape in this repo --
`tests/harness/kube.py`'s `absent_or_fail`, which this file imports rather
than re-implements: "absent" means the API server said `NotFound`, and
every other way `kubectl` can exit non-zero is a hard failure, because "I
could not look" must never be recorded as "there was nothing to check". The
discriminator is deliberately a **different object** than the one under
test: whether this is a stage-4 cluster is decided by the OpenSearch
Service and the deployed collector's traces pipeline; the index
`ss4o_traces-default-namespace` and its documents are the target, so a
missing index is a **failure**, never a skip. Mutation-verified: pointing
`TRACE_INDEX` at a missing index reddens this file rather than skipping it.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
import requests

# Reuse, not re-implementation -- the same convention test_supervised.py states
# for its own imports. These are the claim, the amounts and the route fixtures
# already established elsewhere for exactly these two runs.
from tests.e2e.test_policy_denial import (  # noqa: F401 -- fixture used by name
    ATTR_AUTONOMY,
    ATTR_DECISION,
    ATTR_FLAG_VERSION,
    ATTR_POLICY_AMOUNT,
    ATTR_POLICY_CALLER,
    ATTR_POLICY_LIMIT,
    ATTR_POLICY_RULE,
    ATTR_POLICY_TOOL,
    CLAIMS_AGENT_URL,
    DENIED_AMOUNT,
    DENIED_CLAIM_ID,
    EXECUTE_TOOL,
    PERMITTED_AMOUNT,
    claims_agent_route,
)
from tests.e2e.test_supervised import (  # noqa: F401 -- fixture used by name
    REVIEW_DECISION_SPAN,
    REVIEWER_NAME_PREFIX,
    reviewer_ui_route,
)
from tests.e2e.test_opensearch_trace_ingest import (
    DECISION_SPAN,
    OBSERVABILITY_NAMESPACE,
    OPENSEARCH_EXPORTER_ID,
    OPENSEARCH_SERVICE,
    PDS_SERVICE,
    TRACE_INDEX,
    _kubectl,
    _opensearch,
)
from tests.e2e.test_caller_identity import KUBE_CONTEXT
# `absent_or_fail` lives in the shared harness so that neither of the two
# e2e modules that need it owns it, and so both check "does this object
# exist?" against the same one definition rather than against a copy.
from tests.harness.kube import absent_or_fail
# Same reasoning: the parsed reading of the deployed pipelines lives in the
# shared harness rather than as a private function duplicated per caller.
# See tests/harness/collector.py.
from tests.harness.collector import pipeline_exporters

COLLECTOR_CONFIGMAP = "otel-collector-config"
# The connector that tees the main `traces` pipeline into the OpenSearch-only
# branch. Not exported by test_opensearch_trace_ingest.py, which only names the
# exporter; the id is read off the deployed ConfigMap and pinned by
# tests/unit/test_stage4_config.py::test_the_rename_is_confined_to_the_opensearch_branch,
# which asserts it is `traces/opensearch`'s only receiver.
OPENSEARCH_TEE_ID = "forward/opensearch"

# The attribute the claim-id half of the join reads, and the only domain
# attribute that reaches settlement-agent's spans at all. Canonical name from
# agents/settlement-agent/correlation.py:23.
ATTR_CLAIM_ID = "northstar.claim.id"
# The two keys the evidence view reads off reviewer-ui's `review.decision`
# span, from docs/telemetry-contract.md's "Attributes on the `review.decision`
# span" table. Every other attribute name this file uses -- the `policy.*` set
# and the span name -- is imported above rather than restated, so the two files
# cannot disagree about what the decision span carries.
ATTR_REVIEW_REVIEWER = "review.reviewer.id"
ATTR_REVIEW_DECISION = "review.decision"

# The rule this demo's evidence story turns on: it fires for both claims,
# and decides them differently. Not a constant this file chose -- read off
# the live decision spans of both runs and already published in
# docs/telemetry-contract.md's decision table.
EXECUTE_LIMIT_RULE = "settlement-agent.assisted.execute_limit"

PAYMENTS_API = "payments-api"

# The money hops: payments-api's own server spans. They carry no
# `northstar.claim.id` (Gap 4), which is exactly why the second step of the
# join exists -- see `test_the_trace_id_join_is_what_reaches_the_money_hops`.
EXECUTE_HOP = "POST /payments/{paymentId}/execute"
PROPOSE_HOP = "POST /payments/propose"

# A settlement is three sequential model turns plus MCP round trips; 180s
# matches every other call site in this suite that drives one. The approval POST
# below reuses it and is nowhere near needing it (`execute_approved` makes no
# model call -- measured at 0.1s on this cluster), which is deliberate: one
# number, not two, and the generous one is the safe direction.
SETTLEMENT_TIMEOUT_S = 180
# OpenSearch is near-real-time and the collector batches on a 1s timeout, so
# documents appear a little after the HTTP call returns. Same reasoning and the
# same order of magnitude as test_opensearch_trace_ingest.py's own settle
# timeout; exceeding it is a real failure, never a reason to assert on whatever
# happened to be there.
EVIDENCE_SETTLE_TIMEOUT_S = 120.0

# Clock-skew allowance on the window's lower bound. kind runs in Docker on this
# host, so the two clocks are the same one, but a span's `startTime` is set by
# the emitting pod and a second of slack costs nothing. It is small on purpose:
# the window is what keeps two runs of the fixed `CLM-1842` apart, and a
# generous margin would hand that property back.
WINDOW_SKEW_S = 30.0

# One page is enough for a claim's whole story and the assertion below proves it
# rather than assuming it. A supervised pair (refusal trace + approval trace)
# is **214-218 documents** on this cluster, split roughly 141-145 and 73
# between the two traces -- comfortable headroom below the 1,000-document
# page `_spans_in_traces` reads.
SPAN_PAGE_SIZE = 1000

_WINDOW_START_MS: int | None = None

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason=(
        "needs the live stage-4 cluster, spends two settlements' worth of "
        "model tokens and executes a real payment under the senior identity; "
        "gated behind the same RUN_BROWSER_TESTS=1 flag "
        "scripts/smoke-test.sh sets unconditionally (no browser is started -- "
        "test_settlement_llm_trace.py rides the same flag for the same reason)"
    ),
)


def _search(body: dict) -> dict:
    """One `_search` against the trace index, with a missing index as a failure.

    `_opensearch` (imported from test_opensearch_trace_ingest.py) asserts that
    `curl` ran; it does not assert that OpenSearch liked the request. A 404
    `index_not_found_exception` comes back as a perfectly well-formed JSON body
    with an `error` key and no `hits`, so without this the mutation Step 7 asks
    for -- point the query at an index that does not exist -- would surface as
    a `KeyError` traceback rather than as a named red. It is named here.
    """
    result = _opensearch(f"/{TRACE_INDEX}/_search", body)
    assert "error" not in result, (
        f"OpenSearch refused the evidence query against {TRACE_INDEX!r}: "
        f"{json.dumps(result.get('error'))[:600]}\n"
        "If this says `index_not_found_exception`, the index this dashboard "
        "and this file both read does not exist -- which is a stage-4 cluster "
        "with no trace ingest, not a cluster below stage 4. Check the "
        "`opensearch/trace` exporter in the deployed collector config and the "
        "collector's own log for `Exporting failed`."
    )
    return result


def _window_filter() -> dict:
    """The lower bound every query below carries. See the module docstring."""
    assert _WINDOW_START_MS is not None, (
        "the evidence window was never opened -- the `evidence_window` fixture "
        "did not run before a query was made"
    )
    # Epoch milliseconds rather than an ISO string: `startTime` is mapped
    # `{"type": "date"}` with no explicit format (read from
    # `_mapping/field/startTime` on this cluster), so both parse, and millis
    # cannot be misread as a local time by anything.
    return {"range": {"startTime": {"gte": _WINDOW_START_MS}}}


def _trace_ids_for_claim(claim_id: str) -> list[str]:
    """Step 1 of the join: claim id -> the trace ids that claim touched.

    **GAP 4 -- DO NOT COLLAPSE THE TWO STEPS INTO ONE FILTER.** A single
    `attributes.northstar.claim.id = X` filter would return a strict and
    silently incomplete subset of this claim's story. `northstar.claim.id`
    travels as W3C baggage and is copied onto spans only inside the processes
    that hold it (customer-ui, claims-agent, settlement-agent). It does **not**
    cross the MCP boundary -- FastMCP 4.0.3 never reads or writes `baggage` in
    `params._meta`, Gap 4 in docs/telemetry-contract.md -- so payments-mcp and
    payments-api spans never carry it; and the Policy Decision Service is
    reached over the gateway's guardrail hop, which carries no baggage either,
    so `policy.decision` spans never carry it. Measured across several
    supervised runs rather than assumed from one: a `CLM-1842` filter
    returns **73-75 documents** per run (settlement-agent and claims-agent
    only), while the two traces those runs actually span hold **214-218**.
    **Zero** of the filtered documents are from payments-api or the PDS in any
    of the four -- the filter drops all 5 payments-api and all 5
    policy-decision-service documents every time, i.e. every span that records
    the decision and every span where money moved.
    **The trace id is the join.** This file does not close Gap 4 (closing it
    means changing baggage propagation in the money path, more risk than this
    file's own scope justifies); it is built around the constraint, here and
    in the saved dashboard's own two-step layout.

    Returns more than one trace id for a supervised claim, and that is the
    point rather than an accident: the refusal is one trace (rooted at
    customer-ui) and the human approval is a second (rooted at reviewer-ui's
    `review.decision` span), joined to the claim because
    `settlement.execute_approved` re-attaches the claim id from the ledger
    record -- see `agents/settlement-agent/settlement.py::execute_approved`.
    """
    buckets = _search(
        {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {f"attributes.{ATTR_CLAIM_ID}.keyword": claim_id}},
                        _window_filter(),
                    ]
                }
            },
            "aggs": {
                "traces": {
                    "terms": {"field": "traceId.keyword", "size": 20},
                    "aggs": {"first": {"min": {"field": "startTime"}}},
                }
            },
        }
    )["aggregations"]["traces"]["buckets"]
    return [bucket["key"] for bucket in sorted(buckets, key=lambda b: b["first"]["value"])]


def _spans_in_traces(trace_ids: list[str]) -> list[dict]:
    """Step 2 of the join: every span in those traces, claim id or not.

    This is the half `_trace_ids_for_claim`'s warning is about. Filtering on
    the claim id here as well -- which reads like a harmless belt-and-braces
    narrowing -- would drop the decision spans and the money hops again.
    """
    if not trace_ids:
        return []
    result = _search(
        {
            "size": SPAN_PAGE_SIZE,
            "query": {"bool": {"filter": [{"terms": {"traceId": trace_ids}}, _window_filter()]}},
            "sort": [{"startTime": "asc"}],
            "track_total_hits": True,
        }
    )["hits"]
    hits = result["hits"]
    # A silent truncation here would present as a missing money hop -- a red,
    # but one pointing at instrumentation instead of at this page size. A
    # supervised pair is 214-218 documents against a 1,000-document page (see
    # SPAN_PAGE_SIZE), so this has headroom and is a tripwire rather than a
    # live risk -- but it is not idle, either: run unbounded over an
    # unbounded accumulation of `CLM-1842` traces, this fires, which is what
    # the time window exists to prevent.
    # `track_total_hits` is set because OpenSearch caps the
    # reported total at 10,000 by default, which would make the comparison
    # itself lie exactly when it matters.
    total = (result.get("total") or {}).get("value", len(hits))
    assert len(hits) == total, (
        f"the trace-id join returned {len(hits)} of {total} matching documents "
        f"-- `size` is {SPAN_PAGE_SIZE} and these traces no longer fit in one "
        "page, so the evidence row below is built from a truncated span set. "
        "Raise SPAN_PAGE_SIZE or paginate; do not read the short list."
    )
    return [hit["_source"] for hit in hits]


def evidence_for(claim_id: str) -> dict:
    """One row of the evidence view, read out of OpenSearch exactly as the
    saved dashboard's panels read it.

    Each key is something a panel of `evidence.ndjson` puts on screen, and the
    panel it comes from is named beside it:

      `trace_ids`                  panel 1, "Claims in this window"
      `policy_decision`/`rule`/    panel 2, "What the platform decided"
        `limit`/`requested`/
        `autonomy_level`/
        `contract_version`/
        `executing_identity`/
        `executing_rule`
      `reviewer`/`review_decision` panel 3, "Was a human in the loop?"
      `money_hops`                 panel 4, the ledger hops

    `autonomy_level` and `contract_version` are the contract's own two
    signals on the first decision span -- `ATTR_AUTONOMY` (always present,
    `pds/server.py`) and `ATTR_FLAG_VERSION` (present only when flagd's
    response carries a version, which measurably never happens against
    this deployment; see `scorer/contract.py`'s module docstring).
    `contract_version` is expected to read `None` for both of
    this file's live claims today -- that is the honest value, not a gap in
    this function -- and the panel's own `missingBucket` surfaces it as
    "(no version published)" rather than an empty row.

    So a field that stops arriving reddens here *and* empties a panel there,
    which is the whole reason this file exists.
    """
    trace_ids = _trace_ids_for_claim(claim_id)
    spans = _spans_in_traces(trace_ids)

    def attrs(span: dict) -> dict:
        # ss4o writes attribute keys FLAT and dotted into `_source` while
        # OpenSearch expands them into objects for the mapping -- so the Python
        # lookup is the dotted string and the query path is the dotted path.
        # Measured; the same shape test_opensearch_trace_ingest.py reads.
        return span.get("attributes") or {}

    decisions = [
        span
        for span in spans
        if span.get("name") == DECISION_SPAN
        and attrs(span).get(ATTR_POLICY_TOOL) == EXECUTE_TOOL
    ]
    # The decision on the agent's OWN attempt to move the money: the first
    # `execute_payment` decision in the window. For CLM-1842 that is the
    # refusal; a later, permitted one exists in the approval trace and belongs
    # in `executing_identity`, not here.
    first_decision = decisions[0] if decisions else {}
    # Who was actually allowed to move the money: the last PERMITTED
    # `execute_payment` decision. For the €5,800 claim that is
    # settlement-agent's own attempt; for CLM-1842 it is senior-settlement-agent
    # acting on the human's approval.
    permitted = [d for d in decisions if attrs(d).get(ATTR_DECISION) == "permit"]
    executor = permitted[-1] if permitted else {}

    reviews = [span for span in spans if span.get("name") == REVIEW_DECISION_SPAN]
    review = reviews[-1] if reviews else {}

    money = [
        span
        for span in spans
        if (span.get("resource") or {}).get("service.name") == PAYMENTS_API
    ]

    return {
        "claim_id": claim_id,
        "trace_ids": trace_ids,
        "policy_decision": attrs(first_decision).get(ATTR_DECISION),
        "rule": attrs(first_decision).get(ATTR_POLICY_RULE),
        "limit": attrs(first_decision).get(ATTR_POLICY_LIMIT),
        "requested": attrs(first_decision).get(ATTR_POLICY_AMOUNT),
        "autonomy_level": attrs(first_decision).get(ATTR_AUTONOMY),
        "contract_version": attrs(first_decision).get(ATTR_FLAG_VERSION),
        "reviewer": attrs(review).get(ATTR_REVIEW_REVIEWER),
        "review_decision": attrs(review).get(ATTR_REVIEW_DECISION),
        "executing_identity": attrs(executor).get(ATTR_POLICY_CALLER),
        "executing_rule": attrs(executor).get(ATTR_POLICY_RULE),
        "money_hops": [span["name"] for span in money],
        "decision_count": len(decisions),
        "span_count": len(spans),
    }


def _settled_evidence(claim_id: str, required: tuple[str, ...]) -> dict:
    """Poll `evidence_for` until `required` are all populated, then return it.

    Never settles for a partial row. A timeout here is a real failure -- that
    is what a dropped span or a broken rename looks like -- and the message
    names the fields that never arrived and the row as it last stood, so the
    reader is not left to re-run the query by hand.
    """
    deadline = time.time() + EVIDENCE_SETTLE_TIMEOUT_S
    row: dict = {}
    while time.time() < deadline:
        row = evidence_for(claim_id)
        if all(row.get(field) for field in required):
            return row
        time.sleep(3)
    missing = [field for field in required if not row.get(field)]
    raise AssertionError(
        f"the evidence for claim {claim_id!r} never became complete within "
        f"{EVIDENCE_SETTLE_TIMEOUT_S:.0f}s: {missing} are still empty.\n"
        f"row as it last stood: {json.dumps(row, indent=2)}\n"
        "This is what a regression in instrumentation looks like from the "
        "dashboard's side -- a panel that renders and says nothing. Read the "
        "WHOLE collector log, not a tail of it:\n"
        "  kubectl -n observability logs deploy/otel-collector --since=1h "
        "| grep 'Exporting failed'"
    )


# --- stage gate and window ---------------------------------------------------


def _opensearch_is_installed() -> bool:
    return absent_or_fail(
        _kubectl(
            "-n", OBSERVABILITY_NAMESPACE, "get", f"svc/{OPENSEARCH_SERVICE}",
            "-o", "name", timeout=30,
        ),
        f"svc/{OPENSEARCH_SERVICE} in the {OBSERVABILITY_NAMESPACE} namespace",
    )


def _traces_pipeline_tees_to_opensearch() -> bool:
    """Whether the DEPLOYED collector actually routes traces to OpenSearch.

    **Both halves of the tee are read, because either one missing means no span
    reaches the index.** Checking only one half can satisfy that sentence
    while the other half is broken. Measured on the deployed ConfigMap: the
    string `opensearch/trace` occurs in exactly one pipeline's exporter
    list, `traces/opensearch`; the main `traces` pipeline's exporters are
    `['otlp_grpc/jaeger', 'forward/opensearch', 'otlp_grpc/dash0', 'debug']`.
    Deleting `forward/opensearch` from the main pipeline -- the tee that
    actually feeds the branch -- would leave a single-half check returning
    True while nothing reaches the index.

    Both are now required:

      * some `traces*` pipeline exports to `opensearch/trace` (the branch that
        writes to the index), and
      * some `traces*` pipeline exports to `forward/opensearch` (the tee that
        feeds that branch).

    Parsed, not grepped, for the reason
    `test_prometheus_metrics_ingest.py::_metrics_pipeline_exports_to_prometheus`
    records from its own mutation run: each id also occurs under `exporters:`
    and in a comment, so a substring test stays true after the pipeline entry
    that makes it do anything has been deleted.
    """
    result = _kubectl(
        "-n", OBSERVABILITY_NAMESPACE, "get", "configmap", COLLECTOR_CONFIGMAP,
        "-o", "jsonpath={.data.collector-config\\.yaml}", timeout=30,
    )
    if not absent_or_fail(result, f"configmap/{COLLECTOR_CONFIGMAP}"):
        return False
    exporters = pipeline_exporters(result.stdout, "traces")
    return OPENSEARCH_EXPORTER_ID in exporters and OPENSEARCH_TEE_ID in exporters


@pytest.fixture(scope="module", autouse=True)
def stage_4_or_a_reason():
    """Skip below stage 4; fail on the incoherent middle state.

    The discriminator is the OpenSearch Service and the deployed collector's
    traces pipeline -- **not** the index or the documents, which are what this
    file asserts. A missing index on a stage-4 cluster reddens `_search`.
    """
    # The cluster-reachability probe, ahead of and separate from the stage-4
    # discriminator below -- the same split `test_gateway_config_drift.py`'s
    # `live_gateway_config_text` makes, and what makes the stage-4 skip message
    # beneath it a true statement whenever it fires.
    reachable = _kubectl("cluster-info", timeout=30)
    if reachable.returncode != 0:
        pytest.skip(
            f"needs the live cluster: cannot reach {KUBE_CONTEXT} "
            f"({reachable.stderr.strip() or 'no output'}); "
            "run ./scripts/setup-kind.sh --stage 7"
        )

    installed = _opensearch_is_installed()
    exports = _traces_pipeline_tees_to_opensearch()
    if not installed and not exports:
        pytest.skip(
            f"svc/{OPENSEARCH_SERVICE} is not installed and the deployed "
            f"collector config has no traces pipeline exporting to both "
            f"{OPENSEARCH_TEE_ID!r} and {OPENSEARCH_EXPORTER_ID!r}, so this "
            "cluster is coherently below stage 4. Bring it up with "
            "./scripts/setup-kind.sh --stage 7 to run these."
        )
    assert installed, (
        f"the deployed collector ConfigMap tees traces to "
        f"{OPENSEARCH_TEE_ID!r} and on to {OPENSEARCH_EXPORTER_ID!r} "
        f"but svc/{OPENSEARCH_SERVICE} does not "
        f"exist in the {OBSERVABILITY_NAMESPACE} namespace: a pipeline "
        "exporting to a backend this cluster does not have, which fails "
        "permanently and logs into a file nobody reads."
    )
    assert exports, (
        f"svc/{OPENSEARCH_SERVICE} is installed but the deployed collector "
        f"ConfigMap does not carry BOTH halves of the tee -- some traces "
        f"pipeline must export to {OPENSEARCH_TEE_ID!r} (the tee off the main "
        f"pipeline) and some traces pipeline must export to "
        f"{OPENSEARCH_EXPORTER_ID!r} (the branch that writes the index). "
        "Without both, no span can reach the index this dashboard reads. "
        "Re-run ./scripts/setup-kind.sh --stage 7, which applies the ConfigMap "
        "and restarts the collector."
    )


@pytest.fixture(scope="module", autouse=True)
def evidence_window():
    """Open the window every query below is bounded to.

    Autouse and module-scoped so it runs before anything drives traffic, and so
    the bound is the same one for both claims -- the dashboard's time picker
    has one range too.
    """
    global _WINDOW_START_MS
    _WINDOW_START_MS = int((time.time() - WINDOW_SKEW_S) * 1000)
    yield
    _WINDOW_START_MS = None


# --- driving the two stories -------------------------------------------------


def _submit_claim(claim_id: str, amount: float) -> dict:
    """One settlement, submitted to claims-agent over A2A, no browser.

    `test_claim_correlation.py`'s shape: this test originates the request, so
    it mints its own trace id and sends it as a `traceparent` rather than
    searching for a plausible trace afterwards. Nothing below reads this
    trace id: the evidence query starts from the claim id, because that is what
    an operator reading the dashboard actually has. It is minted anyway so that
    a failure in this fixture can be traced by hand without guessing which of
    the day's traces it was.
    """
    trace_id = uuid.uuid4().hex
    traceparent = f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01"
    response = httpx.post(
        f"{CLAIMS_AGENT_URL}/",
        json={
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": uuid.uuid4().hex,
                    "parts": [
                        {
                            "kind": "text",
                            "text": json.dumps(
                                {
                                    "claimId": claim_id,
                                    "policyNumber": "POL-77120",
                                    "description": "Rear-end collision at a junction",
                                    "estimatedAmount": amount,
                                }
                            ),
                        }
                    ],
                }
            },
        },
        headers={"traceparent": traceparent},
        timeout=SETTLEMENT_TIMEOUT_S,
    )
    response.raise_for_status()
    body = response.json()
    assert "error" not in body, f"claims-agent refused claim {claim_id!r}: {response.text[:500]}"
    reply = json.loads(body["result"]["parts"][0]["text"])
    return {"trace_id": trace_id, "reply": reply}


@pytest.fixture(scope="module")
def denied_claim(claims_agent_route, reviewer_ui_route):
    """The €6,200 story: refused, escalated, approved, executed by the senior.

    `CLM-1842` at €6,200 -- the same claim and amount `test_policy_denial.py`
    and `test_supervised.py` drive, because this is the same event the spec's
    own table names, not a new one invented for the dashboard.

    The reviewer id is unique per run (`REVIEWER_NAME_PREFIX` plus a uuid),
    matching the same precedent `test_supervised.py` sets, for the same
    reason: payments-api's payment ids are sequential only within one
    process lifetime, so `review.payment.id` alone is not a unique key -- a
    shared reviewer name across concurrent runs can read a stale
    `review.decision` trace from an unrelated earlier test. Nothing here
    searches by that key -- the claim-id join finds the approval trace --
    but a shared reviewer name across concurrent runs would still make
    `reviewer` ambiguous in the row below.
    """
    run = _submit_claim(DENIED_CLAIM_ID, DENIED_AMOUNT)
    reply = run["reply"]
    assert reply.get("status") == "refused", (
        f"€{DENIED_AMOUNT:.0f} above settlement-agent's €6,000 assisted "
        f"execute allowance was not refused, so this run produced no denial "
        f"half of the story: {reply}"
    )
    payment_id = reply.get("paymentId")
    assert payment_id, f"the refused settlement returned no paymentId: {reply}"

    reviewer_id = f"{REVIEWER_NAME_PREFIX}-{uuid.uuid4().hex[:8]}"
    approval = requests.post(
        f"{reviewer_ui_route}/review/{payment_id}",
        data={"reviewer_id": reviewer_id, "decision": "approve"},
        timeout=SETTLEMENT_TIMEOUT_S,
    )
    assert approval.status_code == 200 and "Review recorded" in approval.text, (
        f"reviewer-ui did not record an approval for {payment_id}: "
        f"HTTP {approval.status_code} {approval.text[:400]!r}"
    )

    # The settle check below is a *timing* precondition, and it has a
    # consequence worth naming: because it polls until these five are
    # truthy, a regression that empties one of them reddens as an ERROR
    # raised here after the 120s timeout -- naming the empty field and
    # printing the whole row -- rather than as a FAILED at the assertion
    # that reads it. The assertions' *values* still discriminate (`==
    # "deny"`, `== EXECUTE_LIMIT_RULE`, `.endswith("/sa/senior-settlement-agent")`).
    # Both go red; only the label differs, and the message here is the more
    # useful of the two.
    row = _settled_evidence(
        DENIED_CLAIM_ID,
        ("policy_decision", "rule", "reviewer", "executing_identity", "money_hops"),
    )
    return SimpleNamespace(
        claim_id=DENIED_CLAIM_ID,
        payment_id=payment_id,
        reviewer_id=reviewer_id,
        trace_id=run["trace_id"],
        evidence=row,
    )


@pytest.fixture(scope="module")
def permitted_claim_id(claims_agent_route) -> str:
    """The €5,800 story: permitted straight through, no human, and a fresh id.

    **Step 1's choice, in one line, with the reasoning in
    `deploy/observability/dashboards/evidence.ndjson`'s header:** the permitted
    claim keeps a per-run unique id and the dashboard is parameterised rather
    than pointed at a fixed fixture claim.
    """
    claim_id = f"CLM-PERMIT-{uuid.uuid4().hex[:8]}"
    reply = _submit_claim(claim_id, PERMITTED_AMOUNT)["reply"]
    assert reply.get("status") == "executed", (
        f"€{PERMITTED_AMOUNT:.0f} is below the €6,000 assisted execute "
        f"allowance and should have been paid straight through: {reply}"
    )
    _settled_evidence(
        claim_id, ("policy_decision", "rule", "executing_identity", "money_hops")
    )
    return claim_id


# --- the assertions ----------------------------------------------------------


def test_the_evidence_query_returns_both_stories(denied_claim, permitted_claim_id):
    """One query, two claims, two different audit trails.

    Asserts the fields the dashboard renders, straight from OpenSearch. If
    instrumentation regresses, this reddens instead of the dashboard quietly
    going blank on stage.
    """
    denied = evidence_for(DENIED_CLAIM_ID)
    assert denied["policy_decision"] == "deny", denied
    assert denied["rule"] == "settlement-agent.assisted.execute_limit", denied
    assert denied["reviewer"], "the approval leg is missing from the denied claim"
    assert denied["executing_identity"].endswith("/sa/senior-settlement-agent"), denied

    permitted = evidence_for(permitted_claim_id)
    assert permitted["policy_decision"] == "permit", permitted
    assert permitted["rule"] == denied["rule"], (
        "both claims must be decided by the SAME rule -- that is the whole "
        f"point: {permitted['rule']!r} vs {denied['rule']!r}"
    )
    assert permitted["reviewer"] is None, (
        "the permitted claim must show no human in the loop; a reviewer here "
        "means the two stories are not actually different"
    )


def test_the_contract_signals_reach_the_decision_panel(denied_claim, permitted_claim_id):
    """The decision panel also carries the contract's own two signals -- the
    autonomy level a decision actually ran under, and the ruleset version it
    saw, if any -- read off the same first decision span
    `rule`/`limit`/`requested` already come from, not a second query.

    Both claims in this file run under the shipped, refusing contract, so
    both must show `autonomy_level == "assisted"`. `contract_version` is
    asserted `None` for both, which is the *honest* value today:
    `featureflag.yaml` publishes no flag `metadata`, so flagd never returns
    one. If this assertion ever reddens
    because a real version appeared, that means route 1 has been taken for
    real -- update it and `platform/eval-scorer/scorer/contract.py`'s
    `CURRENT_CONTRACT_VERSION` together, the same discipline that module's
    own `test_the_currently_published_contract_version_is_none` holds itself
    to; do not silently widen this assertion to tolerate a string.
    """
    denied = evidence_for(DENIED_CLAIM_ID)
    permitted = evidence_for(permitted_claim_id)

    assert denied["autonomy_level"] == "assisted", denied
    assert permitted["autonomy_level"] == "assisted", permitted
    assert denied["contract_version"] is None, denied
    assert permitted["contract_version"] is None, permitted


def test_the_trace_id_join_is_what_reaches_the_money_hops(
    denied_claim, permitted_claim_id
):
    """Gap 4, asserted so nobody can collapse the join into one filter.

    The comment on `_trace_ids_for_claim` says the claim-id filter alone drops
    the decision spans and the money hops. A comment is what failed in this
    repo before, so this measures it: the same window, the same two claims,
    queried both ways. Mutation-verified -- collapsing `_spans_in_traces` into
    a single claim-id filter empties `money_hops`, and the run goes red naming
    every field that vanished with it (the fixtures' own settle check is what
    fires first; this test is what still catches it if that check is ever
    relaxed).
    """
    for claim_id in (DENIED_CLAIM_ID, permitted_claim_id):
        row = evidence_for(claim_id)
        assert EXECUTE_HOP in row["money_hops"] and PROPOSE_HOP in row["money_hops"], (
            f"claim {claim_id}: the trace-id join did not reach payments-api's "
            f"own server spans; money_hops={row['money_hops']}"
        )

        claim_scoped = _search(
            {
                "size": 0,
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {f"attributes.{ATTR_CLAIM_ID}.keyword": claim_id}},
                            _window_filter(),
                        ]
                    }
                },
                "aggs": {
                    "services": {
                        "terms": {"field": "resource.service.name.keyword", "size": 20}
                    }
                },
            }
        )["aggregations"]["services"]["buckets"]
        reached_by_claim_id = {bucket["key"] for bucket in claim_scoped}
        assert not (reached_by_claim_id & {PAYMENTS_API, PDS_SERVICE}), (
            f"claim {claim_id}: {ATTR_CLAIM_ID!r} now reaches "
            f"{sorted(reached_by_claim_id & {PAYMENTS_API, PDS_SERVICE})}. If "
            "Gap 4 has genuinely been closed, that is good news -- but this "
            "file, the saved dashboard's two-step layout and Gap 4 in "
            "docs/telemetry-contract.md all have to change in the same commit."
        )


def test_the_denied_claim_and_the_permitted_one_are_decided_by_one_rule(
    denied_claim, permitted_claim_id
):
    """The demo's own claim about the evidence view, as an assertion.

    Same rule, opposite outcomes, and the limit each was measured against --
    the row the dashboard puts side by side. `executing_rule` is asserted too:
    the senior's permitted execution is the *same* rule at a higher limit
    (€25,000 against settlement-agent's €6,000), not a different rule that
    happens to allow it, which would make the demo's claim false.
    """
    denied = evidence_for(DENIED_CLAIM_ID)
    permitted = evidence_for(permitted_claim_id)

    assert (
        denied["rule"] == permitted["rule"] == denied["executing_rule"]
        == permitted["executing_rule"] == EXECUTE_LIMIT_RULE
    ), (
        "every `execute_payment` decision in both stories must name "
        f"{EXECUTE_LIMIT_RULE!r}: denied={denied['rule']!r}/"
        f"{denied['executing_rule']!r} permitted={permitted['rule']!r}/"
        f"{permitted['executing_rule']!r}"
    )
    assert denied["requested"] == DENIED_AMOUNT and denied["limit"] == 6000, denied
    assert permitted["requested"] == PERMITTED_AMOUNT and permitted["limit"] == 6000, (
        permitted
    )
    assert permitted["executing_identity"].endswith("/sa/settlement-agent"), (
        "the €5,800 claim must be executed by settlement-agent's own identity "
        f"-- no escalation happened: {permitted}"
    )
    assert denied["review_decision"] == "approve", denied
