"""Reconstructing a trajectory out of OpenSearch. The only I/O in this service.

**The two-step join, and why it must stay two steps.** `tests/e2e/
test_evidence_view.py` documents this at length and this module deliberately
reuses its *shape* rather than inventing a second one:

  1. claim id -> the trace ids that claim touched, by aggregating
     `traceId.keyword` over documents carrying
     `attributes.northstar.claim.id`.
  2. those trace ids -> **every** span in those traces, claim id or not.

Step 2 must not also filter on the claim id. That reads like harmless
belt-and-braces narrowing and is in fact the whole defect: `northstar.claim.id`
travels as W3C baggage and is copied onto spans only inside the processes that
hold it, and it does not cross the MCP boundary (Gap 4,
`docs/telemetry-contract.md`). So `policy.decision` spans -- every span these
scorers read -- carry no claim id at all. Collapsing the join into one filter
would leave this service scoring an empty trajectory and reporting it as
compliant.

**The window is load-bearing, not hygiene.** `CLM-1842` is a fixed constant in
this repo's demo and its e2e suite, so the index accumulates one set of traces
per run of it forever. Without a lower bound, a request to score that claim
would score every run since the index was created, mixed together -- and would
trip the page-size guard below rather than answering. Every caller passes a
window; `DEFAULT_WINDOW_S` is what a bare request gets.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from scorer.trajectory import ATTR_CLAIM_ID, Trajectory

logger = logging.getLogger(__name__)

# The index M4 Task 1 measured this system's traces into. `ss4o` is the
# collector's OpenSearch exporter's own naming scheme
# (<dataset>-<namespace>); it is not a name this repo chose.
TRACE_INDEX = "ss4o_traces-default-namespace"

# In-cluster address of the chart's Service. Overridable so the suite can
# point this module at a port-forward or a fake.
DEFAULT_OPENSEARCH_URL = (
    "http://opensearch-cluster-master.observability.svc.cluster.local:9200"
)

# Measured on this cluster 2026-09-13: a supervised pair (refusal trace +
# approval trace) is 214-218 documents, split roughly 141-145 and 73. A
# 1,000-document page is ~4.6x headroom, the same size and the same reasoning
# as `tests/e2e/test_evidence_view.py::SPAN_PAGE_SIZE`. The guard below is a
# tripwire rather than a live risk -- but it is not idle either: run unbounded
# over this index's accumulated `CLM-1842` traces it fires, which is what the
# window exists to prevent.
SPAN_PAGE_SIZE = 1000

# 15 minutes. Long enough for a supervised settlement including a human
# pausing to read the refusal, short enough that a fixed claim id does not
# collect two runs.
DEFAULT_WINDOW_S = 900

MAX_TRACES_PER_CLAIM = 20


class OpenSearchError(RuntimeError):
    """OpenSearch could not be reached, or refused the query.

    Its own exception type so `server.py` can answer 502 for "the store is
    unavailable" and 500 for a bug in this service. A scorer that cannot read
    the trace must never report a score; silence is the only honest answer,
    and a 200 carrying five 1.0s would be the dishonest one.
    """


class TrajectoryTruncated(OpenSearchError):
    """The span page was full, so the trajectory read back is incomplete.

    Raised rather than scored. An incomplete trajectory scores *higher* than
    the real one -- a retry that fell off the end of the page is a retry this
    service would report as compliant -- so this is the one failure mode that
    absolutely must not degrade into an answer.
    """


class OpenSearchClient:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        self.base_url = (
            base_url or os.environ.get("NORTHSTAR_OPENSEARCH_URL")
            or DEFAULT_OPENSEARCH_URL
        ).rstrip("/")
        self.timeout = timeout

    def search(self, body: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/{TRACE_INDEX}/_search",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise OpenSearchError(
                f"could not query {self.base_url}/{TRACE_INDEX}/_search: {exc}"
            ) from exc
        # A 404 `index_not_found_exception` comes back as a well-formed JSON
        # body with an `error` key and no `hits`, so without this check it
        # would surface as a KeyError rather than as the named failure it is.
        # Same correction `tests/e2e/test_evidence_view.py::_search` records.
        if "error" in payload:
            raise OpenSearchError(
                f"OpenSearch refused the query against {TRACE_INDEX!r}: "
                f"{json.dumps(payload['error'])[:600]}"
            )
        return payload

    def trace_ids_for_claim(self, claim_id: str, since_ms: int) -> list[str]:
        """Step 1 of the join. Ordered by when each trace started."""
        buckets = self.search(
            {
                "size": 0,
                "query": {
                    "bool": {
                        "filter": [
                            {
                                "term": {
                                    f"attributes.{ATTR_CLAIM_ID}.keyword": claim_id
                                }
                            },
                            {"range": {"startTime": {"gte": since_ms}}},
                        ]
                    }
                },
                "aggs": {
                    "traces": {
                        "terms": {
                            "field": "traceId.keyword",
                            "size": MAX_TRACES_PER_CLAIM,
                        },
                        "aggs": {"first": {"min": {"field": "startTime"}}},
                    }
                },
            }
        )["aggregations"]["traces"]["buckets"]
        return [
            bucket["key"]
            for bucket in sorted(buckets, key=lambda b: b["first"]["value"])
        ]

    def spans_in_traces(self, trace_ids: list[str], since_ms: int) -> list[dict]:
        """Step 2 of the join: every span in those traces, claim id or not."""
        if not trace_ids:
            return []
        result = self.search(
            {
                "size": SPAN_PAGE_SIZE,
                "query": {
                    "bool": {
                        "filter": [
                            {"terms": {"traceId": trace_ids}},
                            {"range": {"startTime": {"gte": since_ms}}},
                        ]
                    }
                },
                "sort": [{"startTime": "asc"}],
                "track_total_hits": True,
            }
        )["hits"]
        hits = result["hits"]
        # `track_total_hits` is set because OpenSearch caps the reported total
        # at 10,000 by default, which would make this comparison itself lie
        # exactly when it matters.
        total = (result.get("total") or {}).get("value", len(hits))
        if len(hits) != total:
            raise TrajectoryTruncated(
                f"the trace-id join returned {len(hits)} of {total} matching "
                f"documents -- `size` is {SPAN_PAGE_SIZE} and these traces no "
                "longer fit in one page. Scoring a truncated trajectory would "
                "report a retry that fell off the end of the page as "
                "compliant, so no score is emitted. Narrow the window or "
                "paginate."
            )
        return [hit["_source"] for hit in hits]

    def trajectory_for_claim(self, claim_id: str, since_ms: int) -> Trajectory:
        trace_ids = self.trace_ids_for_claim(claim_id, since_ms)
        documents = self.spans_in_traces(trace_ids, since_ms)
        logger.info(
            "claim %s: %d trace(s), %d span(s) since %d",
            claim_id, len(trace_ids), len(documents), since_ms,
        )
        return Trajectory.from_documents(claim_id, documents)
