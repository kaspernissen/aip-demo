"""The evidence read, from Jaeger: the demo shows traces and span attributes,
so `scripts/demo evidence` reads Jaeger and nothing else.

The join (GAP 4): a claim id is
on the spans of customer-ui, claims-agent, settlement-agent and reviewer-ui
(W3C baggage), and NOT on the policy decision spans or the money hops, which
sit in the same traces. So: claim id -> the traces it touched -> every span in
them. Jaeger's search needs a service name, so the claim id is searched in the
four services that carry it and the trace ids are unioned.

Times are Jaeger's own microseconds since the epoch; the window arguments are
epoch milliseconds like the rest of the driver.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from tests.harness.jaeger import JaegerClient, Span, Trace

JAEGER_API = os.environ.get("JAEGER_URL", "http://localhost:16686")

ATTR_CLAIM_ID = "northstar.claim.id"
ATTR_DECISION = "northstar.policy.decision"
ATTR_POLICY_TOOL = "northstar.policy.tool.name"
ATTR_POLICY_RULE = "northstar.policy.rule"
ATTR_POLICY_CALLER = "northstar.policy.caller.spiffe_id"
ATTR_POLICY_LIMIT = "northstar.policy.limit.amount"
ATTR_POLICY_AMOUNT = "northstar.policy.request.amount"
ATTR_AUTONOMY = "northstar.policy.autonomy.level"
ATTR_FLAG_VERSION = "feature_flag.version"
ATTR_REVIEW_REVIEWER = "northstar.review.reviewer.id"
ATTR_REVIEW_DECISION = "northstar.review.decision"
ATTR_REVIEW_WAIT_S = "northstar.review.wait.duration"  # double, seconds
DECISION_SPAN = "northstar.policy.decision"
REVIEW_DECISION_SPAN = "northstar.review.decision"
PDS_SERVICE = "policy-decision-service"
REVIEWER_SERVICE = "reviewer-ui"
PAYMENTS_API = "payments-api"
EXECUTE_TOOL = "execute_payment"

# Services whose spans carry the claim id as baggage.
CLAIM_SERVICES = ("claims-agent", "settlement-agent", "reviewer-ui", "customer-ui")
CLAIM_LOOKBACK_S = 900
SEARCH_LIMIT = 100


def _client() -> JaegerClient:
    return JaegerClient(JAEGER_API)


def _search(service: str, *, operation: str | None = None, tags: dict[str, str] | None = None,
            since_ms: int | None = None, lookback_s: int = 3600) -> list[str]:
    """Trace ids matching the search, from `since_ms` (or `lookback_s` back) to now."""
    now_us = int(time.time() * 1_000_000)
    start_us = (since_ms * 1000) if since_ms is not None else now_us - lookback_s * 1_000_000
    params: dict[str, Any] = {
        "service": service, "start": start_us, "end": now_us + 60_000_000, "limit": SEARCH_LIMIT,
    }
    if operation:
        params["operation"] = operation
    if tags:
        params["tags"] = json.dumps(tags)
    resp = requests.get(f"{_client().base_url}/api/traces", params=params, timeout=15)
    resp.raise_for_status()
    found = [t["traceID"] for t in (resp.json().get("data") or [])]
    # Jaeger returns at most `limit` traces and says nothing when it cut the list
    # short. A silently truncated search would drop claims from the evidence, so a
    # full page is a failure, not a result.
    assert len(found) < SEARCH_LIMIT, (
        f"Jaeger returned {len(found)} traces for {service!r} {operation or tags or ''}: the "
        f"search limit ({SEARCH_LIMIT}) was reached, so the list may be truncated and the "
        "evidence incomplete. Narrow the window (reset starts a new run) or raise SEARCH_LIMIT."
    )
    return found


def _claim_traces(claim_id: str, since_ms: int | None, lookback_s: int = 24 * 3600) -> list[Trace]:
    """Every trace the claim id is on, whole (re-fetched by id: a search result
    can be cut short), oldest first."""
    ids: list[str] = []
    for service in CLAIM_SERVICES:
        for tid in _search(service, tags={ATTR_CLAIM_ID: claim_id}, since_ms=since_ms,
                           lookback_s=lookback_s):
            if tid not in ids:
                ids.append(tid)
    traces = [_client().get_trace(tid) for tid in ids]
    return sorted(traces, key=lambda t: min((s.start_us for s in t.spans), default=0))


def _claim_first_span_us(trace: Trace, claim_id: str) -> int | None:
    starts = [s.start_us for s in trace.spans if s.attrs.get(ATTR_CLAIM_ID) == claim_id]
    return min(starts) if starts else None


def claim_first_seen_ms(claim_id: str) -> int | None:
    """When the first span carrying this claim id started (epoch ms), looking
    back at most `CLAIM_LOOKBACK_S`; None if Jaeger has none yet."""
    lower_ms = int((time.time() - CLAIM_LOOKBACK_S) * 1000)
    firsts = [
        us for t in _claim_traces(claim_id, lower_ms, CLAIM_LOOKBACK_S)
        if (us := _claim_first_span_us(t, claim_id)) is not None
    ]
    return min(firsts) // 1000 if firsts else None


def claim_ids_since(window_start_ms: int, filed_after_ms: int | None = None) -> list[str]:
    """Every claim FILED since the window opened, oldest first (how `demo
    evidence` finds the claims a human filed in the browser).

    Candidates are the claim ids on the traces of this window's decision spans
    (service policy-decision-service, operation northstar.policy.decision). "Filed"
    means the claim's first span ever -- over the last day, not just the window
    -- is at or after `filed_after_ms` (default: the window start), so a claim
    carried over from before the run, declined by `demo reset` inside it, is
    not this run's.
    """
    filed_after_ms = window_start_ms if filed_after_ms is None else filed_after_ms
    candidates: list[str] = []
    for tid in _search(PDS_SERVICE, operation=DECISION_SPAN, since_ms=window_start_ms):
        for span in _client().get_trace(tid).spans:
            claim = span.attrs.get(ATTR_CLAIM_ID)
            if claim and claim not in candidates:
                candidates.append(claim)
    filed = []
    for claim_id in candidates:
        firsts = [
            us for t in _claim_traces(claim_id, None)
            if (us := _claim_first_span_us(t, claim_id)) is not None
        ]
        first_ms = min(firsts) / 1000 if firsts else None
        if first_ms is not None and first_ms >= filed_after_ms:
            filed.append((first_ms, claim_id))
    return [c for _, c in sorted(filed)]


def evidence_for(claim_id: str, window_start_ms: int) -> dict:
    """One row of the evidence, read out of Jaeger. `link_trace_id` is the
    approval's own trace when there is one, else the claim's first trace (the
    one the demo links)."""
    traces = [t for t in _claim_traces(claim_id, window_start_ms, 24 * 3600)
              if min((s.start_us for s in t.spans), default=0) >= window_start_ms * 1000]
    spans: list[Span] = [s for t in traces for s in t.spans]
    spans.sort(key=lambda s: s.start_us)

    decisions = [s for s in spans if s.operation == DECISION_SPAN and s.service == PDS_SERVICE
                 and s.attrs.get(ATTR_POLICY_TOOL) == EXECUTE_TOOL]
    first_decision = decisions[0].attrs if decisions else {}
    permitted = [d for d in decisions if d.attrs.get(ATTR_DECISION) == "permit"]
    executor = permitted[-1].attrs if permitted else {}

    reviews = [s for s in spans if s.operation == REVIEW_DECISION_SPAN and s.service == REVIEWER_SERVICE
               and s.attrs.get(ATTR_CLAIM_ID) == claim_id]
    review = reviews[-1].attrs if reviews else {}
    # A resume-only decision (northstar.review.resume_only) carries no wait
    # duration and is not the approval.
    approving = [r for r in reviews if r.attrs.get(ATTR_REVIEW_DECISION) == "approve"
                 and r.attrs.get(ATTR_REVIEW_WAIT_S) is not None]
    approval = approving[-1] if approving else None
    approval_attrs = approval.attrs if approval else {}
    money = [s.operation for s in spans if s.service == PAYMENTS_API]

    trace_ids = [t.trace_id for t in traces]
    link_trace = approval.trace_id if approval else (trace_ids[0] if trace_ids else None)
    return {
        "claim_id": claim_id,
        "trace_ids": trace_ids,
        "link_trace_id": link_trace,
        "policy_decision": first_decision.get(ATTR_DECISION),
        "rule": first_decision.get(ATTR_POLICY_RULE),
        "limit": first_decision.get(ATTR_POLICY_LIMIT),
        "requested": first_decision.get(ATTR_POLICY_AMOUNT),
        "autonomy_level": first_decision.get(ATTR_AUTONOMY),
        "contract_version": first_decision.get(ATTR_FLAG_VERSION),
        "reviewer": review.get(ATTR_REVIEW_REVIEWER),
        "approved_by": approval_attrs.get(ATTR_REVIEW_REVIEWER),
        "human_wait_s": approval_attrs.get(ATTR_REVIEW_WAIT_S),
        "review_decision": review.get(ATTR_REVIEW_DECISION),
        "caller_identity": first_decision.get(ATTR_POLICY_CALLER),
        "executing_identity": executor.get(ATTR_POLICY_CALLER),
        "executing_rule": executor.get(ATTR_POLICY_RULE),
        "money_hops": money,
        "decision_count": len(decisions),
        "span_count": len(spans),
    }
