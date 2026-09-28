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

# The index this system's traces land in. `ss4o` is the collector's
# OpenSearch exporter's own naming scheme (<dataset>-<namespace>); it is not
# a name this repo chose.
TRACE_INDEX = "ss4o_traces-default-namespace"

# In-cluster address of the chart's Service. Overridable so the suite can
# point this module at a port-forward or a fake.
DEFAULT_OPENSEARCH_URL = (
    "http://opensearch-cluster-master.observability.svc.cluster.local:9200"
)

# Measured on this cluster: a supervised pair (refusal trace + approval
# trace) is 214-218 documents, split roughly 141-145 and 73. A
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

# The terms-aggregation cap for step 1 -- a measured constant, not a round
# number.
#
# **Measured against the live index**, not estimated: a single
# `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` (417s end to end) contributes
# **8** distinct traces carrying `CLM-1842` — the whole gated suite drives more
# than one supervised pair. So `DEFAULT_WINDOW_S`'s 15 minutes holds about
# **2.5 gate runs**, not the ten supervised settlements a per-pair estimate
# suggests. Unbounded, the same index answers **57** distinct traces for that
# claim across 2,263 documents, and the aggregation below returns 20 of them
# with `sum_other_doc_count: 874`.
#
# That measurement is the argument for the guard rather than for a bigger
# number. Raising the cap moves the cliff; it does not remove it, and a cap
# that cannot be exceeded is not available at any size while `CLM-1842` is a
# fixed constant this repo's suite re-drives forever. 20 is kept, and
# exceeding it is now a loud failure.
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


def _numeric_aggregation_field(
    aggregation: dict, key: str, claim_id: str, purpose: str
) -> float:
    """`aggregation[key]`, refusing both an ABSENT key and a non-numeric one.

    Shared by `trace_ids_for_claim`'s two truncation guards so both hold the
    same standard: `.get(key) or 0` reads a missing key and an honest zero
    identically, and a presence check alone
    (`key not in aggregation`) still reads a PRESENT `None` the same as an
    honest zero, because `if value:` is falsy for both. Either shape is "I
    could not look", not "there was nothing to find", and this guard must
    not skip where it should fail.

    `bool` is excluded from the numeric check on purpose even though
    `isinstance(True, int)` is `True` in Python -- a boolean here would be a
    stranger response shape than a missing key, not a number this guard
    should ever treat as a legitimate count.
    """
    if key not in aggregation:
        raise TrajectoryTruncated(
            f"claim {claim_id!r}: the trace aggregation carries no {key!r} "
            f"key at all, so this read cannot tell whether {purpose}. "
            "Defaulting an absent field to zero would record 'I could not "
            "look' as 'nothing was dropped'. No score is emitted."
        )
    value = aggregation[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrajectoryTruncated(
            f"claim {claim_id!r}: {key!r} is {value!r}, not a number, so "
            f"this read cannot tell whether {purpose}. A present-but-null "
            "(or otherwise non-numeric) value is 'I could not look' in a "
            "different disguise than an absent key, not an honest zero. No "
            "score is emitted."
        )
    return value


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
        """Step 1 of the join. Ordered by when each trace started.

        **Refuses a truncated read, symmetrically with `spans_in_traces`.**
        This method caps the terms aggregation at `MAX_TRACES_PER_CLAIM` and
        must inspect what came back rather than trust it silently: step 2 of
        the join refuses to score an incomplete read and says why, so step 1
        has to hold to the same standard or its own truncation goes
        unnoticed.

        **And it truncates in the direction that reads as compliant.** A terms
        aggregation orders buckets by `doc_count` descending before the `size`
        cut, so the trace preferentially dropped is the *smallest* — which in a
        supervised pair is the approval trace (73 documents against the
        refusal trace's 141–145, recorded above). That trace is where the
        second `execute_payment` decision lives, and its absence is exactly
        what would invert `stopped_when_refused` from `0.0 retry-after-refusal`
        to `1.0 compliant` -- "a partial trajectory always reads higher" is
        the general shape of that failure, and this guard is what keeps this
        step from reproducing it.

        **What this does not rely on.** In today's measured shape step 2 would
        often catch it anyway — 20 traces at ~70 documents each is 1,400
        against `SPAN_PAGE_SIZE`'s 1,000 — but that is arithmetic coincidence
        between two constants chosen for different reasons, not a design. A
        claim whose traces average under 50 documents (a denial-only path, a
        shorter trajectory, a future trimmed span set) fits 20 traces inside
        one page and truncates here in perfect silence. The guard is placed
        where the truncation happens.
        """
        aggregation = self.search(
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
        )["aggregations"]["traces"]
        buckets = aggregation["buckets"]

        # Buckets OpenSearch had to drop to honour `size`. Any value above zero
        # means this list is not the claim's trajectory, it is the busiest part
        # of it.
        #
        # `.get(...) or 0` would read a genuinely ABSENT key the same as a
        # present, honest zero -- "I could not look" recorded as "there was
        # nothing to find", the exact rule `search()`'s own
        # `"error" in payload` check enforces ten lines above. Defensive
        # rather than live: OpenSearch always sends this key on a real terms
        # aggregation. But a field this guard's correctness depends on must
        # raise on absence, not silently assume the value that happens to
        # make the guard pass.
        #
        # A presence check alone would still let a PRESENT-BUT-NULL value
        # through -- `if "key" not in aggregation` passes for
        # `{"key": None}`, after which `if dropped:` is falsy for `None`
        # exactly as it is for an absent key defaulted to 0. Checked via
        # `_numeric_aggregation_field` so both guards in this method hold the
        # same standard: present, AND a real number.
        dropped = _numeric_aggregation_field(
            aggregation, "sum_other_doc_count", claim_id,
            "OpenSearch had to drop any bucket to honour `size`",
        )
        if dropped:
            raise TrajectoryTruncated(
                f"claim {claim_id!r} touched more than "
                f"{MAX_TRACES_PER_CLAIM} traces in this window: the "
                f"aggregation returned {len(buckets)} trace(s) and dropped "
                f"{dropped} further document(s) (`sum_other_doc_count`). A "
                "terms aggregation drops the SMALLEST buckets, which in a "
                "supervised pair is the approval trace -- the one holding the "
                "second execute decision -- so scoring what came back would "
                "report a trajectory that stopped at the refusal as "
                "compliant. No score is emitted. Narrow the window "
                f"(currently since={since_ms}), or score a claim id that is "
                "not re-driven by the suite."
            )

        # Non-zero only when the index has more than one shard and a bucket's
        # count could be wrong: the ordering that decides *which* traces the
        # cut keeps is then itself approximate, so "nothing was dropped" stops
        # being something this read can assert. Measured 0 on this
        # single-shard index; checked anyway, because the value that makes it
        # non-zero is a chart setting nobody would connect to a score. Same
        # `.get(...) or 0` risk as `sum_other_doc_count` above, checked with
        # the same guard.
        error_bound = _numeric_aggregation_field(
            aggregation, "doc_count_error_upper_bound", claim_id,
            "bucket counts are exact",
        )
        if error_bound:
            raise TrajectoryTruncated(
                f"claim {claim_id!r}: the trace aggregation reports "
                f"`doc_count_error_upper_bound` {error_bound}, so bucket "
                "counts are approximate and the ordering that decides which "
                "traces survive the `size` cut cannot be trusted. A "
                "trajectory assembled from an approximate bucket list may be "
                "missing a trace without saying so, and a missing trace reads "
                "as compliant. No score is emitted."
            )

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
        #
        # `(result.get("total") or {}).get("value", len(hits))` would default
        # a missing `total` (or a missing `total.value`) to `len(hits)`
        # itself -- comparing that number to itself always passes, which
        # makes the very next line's truncation check vacuously false exactly
        # when the response is too malformed to trust. Absence must raise,
        # not stand in for "definitely not truncated".
        total_block = result.get("total")
        if not isinstance(total_block, dict) or "value" not in total_block:
            raise TrajectoryTruncated(
                "the trace-id join's response carries no `hits.total.value` "
                "-- `track_total_hits` was requested, so its absence means "
                "this read cannot confirm the page was not truncated. "
                "Defaulting to len(hits) would compare a number to itself "
                "and always pass. No score is emitted."
            )
        total = total_block["value"]
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
