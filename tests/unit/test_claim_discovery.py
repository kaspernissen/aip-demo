"""`live.claim_ids_since` returns the claims FILED in this run, not every claim
a span touched in the window.

A claim left waiting by an earlier test file and declined by `demo reset`
inside the window has fresh spans in the window (the decline, and while it
waited a2a-sdk's EventQueue spans) but was filed before it. `demo evidence`
reported it as a fourth claim. The discriminator is the claim's FIRST span
ever, so the fake OpenSearch below answers the two queries over a span list and
honours the window filter only where the query carries one.
"""
from __future__ import annotations

from tests.harness import live

ATTR = f"attributes.{live.ATTR_CLAIM_ID}.keyword"


def _fake_search(spans):
    def search(body):
        filters = body["query"]["bool"]["filter"]
        lo = None
        ids = None
        for f in filters:
            if "range" in f:
                lo = f["range"]["startTime"]["gte"]
            if "terms" in f:
                ids = set(f["terms"][ATTR])
        rows = [
            s for s in spans
            if (lo is None or s["t"] >= lo) and (ids is None or s["claim"] in ids)
        ]
        buckets = []
        for claim in {s["claim"] for s in rows}:
            ts = [s["t"] for s in rows if s["claim"] == claim]
            buckets.append({"key": claim, "first": {"value": min(ts)}})
        return {"aggregations": {"claims": {"buckets": buckets}}}
    return search


def test_a_claim_whose_first_span_predates_the_window_is_excluded(monkeypatch):
    spans = [
        {"claim": "CLM-OLD", "t": 500},    # filed before the run, left waiting
        {"claim": "CLM-OLD", "t": 1500},   # declined by `demo reset` in the window
        {"claim": "CLM-NEW-B", "t": 1300},
        {"claim": "CLM-NEW-A", "t": 1100},
        {"claim": "CLM-NEW-A", "t": 1900},
    ]
    monkeypatch.setattr(live, "search", _fake_search(spans))
    assert live.claim_ids_since(1000) == ["CLM-NEW-A", "CLM-NEW-B"]


def test_a_claim_whose_first_span_is_exactly_the_window_start_is_included(monkeypatch):
    monkeypatch.setattr(live, "search", _fake_search([{"claim": "CLM-EDGE", "t": 1000}]))
    assert live.claim_ids_since(1000) == ["CLM-EDGE"]


def test_no_spans_in_the_window_is_no_claims(monkeypatch):
    monkeypatch.setattr(live, "search", _fake_search([{"claim": "CLM-OLD", "t": 10}]))
    assert live.claim_ids_since(1000) == []
