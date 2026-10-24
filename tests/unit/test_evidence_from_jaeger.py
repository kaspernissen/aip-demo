"""The demo's evidence read comes from Jaeger (M9 Task 4), not OpenSearch.

A fake Jaeger: two traces for one claim, the refusal trace (decision span with
no claim id, claim id on the agent spans) and the approval's own trace (the
review span carries the claim id). The join is by trace, as the real one is.
"""
from tests.harness import evidence
from tests.harness.jaeger import Trace

CLAIM = "CLM-X"
T0 = 1_700_000_000_000_000  # microseconds


def _span(sid, service, op, start_us, parent=None, **tags):
    return {"spanID": sid, "processID": service, "operationName": op, "startTime": start_us,
            "duration": 1000, "references": [{"refType": "CHILD_OF", "spanID": parent}] if parent else [],
            "tags": [{"key": k, "value": v} for k, v in tags.items()]}


def _trace(tid, spans):
    services = {s["processID"] for s in spans}
    return {"traceID": tid, "processes": {n: {"serviceName": n} for n in services}, "spans": spans}


REFUSAL = _trace("a" * 32, [
    _span("1", "claims-agent", "POST /", T0, **{"northstar.claim.id": CLAIM}),
    _span("2", "policy-decision-service", "northstar.policy.decision", T0 + 10, "1", **{
        "northstar.policy.tool.name": "execute_payment", "northstar.policy.decision": "deny",
        "northstar.policy.rule": "settlement-agent.assisted.execute_limit",
        "northstar.policy.limit.amount": 0.0, "northstar.policy.request.amount": 5800.0,
        "northstar.policy.autonomy.level": "assisted",
        "northstar.policy.caller.spiffe_id": "spiffe://x/settlement-agent"}),
])
APPROVAL = _trace("b" * 32, [
    _span("3", "reviewer-ui", "northstar.review.decision", T0 + 5_000_000, **{
        "northstar.claim.id": CLAIM, "northstar.review.decision": "approve",
        "northstar.review.reviewer.id": "kasper.on.stage", "northstar.review.wait.duration": 41.5}),
    _span("4", "payments-api", "POST /payments/{paymentId}/execute", T0 + 5_000_100, "3"),
])


def _fake(monkeypatch, traces):
    by_id = {t["traceID"]: t for t in traces}
    monkeypatch.setattr(evidence, "_search", lambda service, **kw: [
        t["traceID"] for t in traces
        if any(s["processID"] == service and (not kw.get("tags") or
               {x["key"]: x["value"] for x in s["tags"]}.get("northstar.claim.id") == kw["tags"]["northstar.claim.id"]
               if kw.get("tags") else s["processID"] == service and s["operationName"] == kw.get("operation"))
               for s in t["spans"])
    ])
    monkeypatch.setattr(evidence.JaegerClient, "get_trace", lambda self, tid: Trace(by_id[tid]))


def test_the_row_is_built_from_the_decision_and_review_spans(monkeypatch):
    _fake(monkeypatch, [REFUSAL, APPROVAL])
    row = evidence.evidence_for(CLAIM, T0 // 1000 - 1000)
    assert row["policy_decision"] == "deny"
    assert row["rule"] == "settlement-agent.assisted.execute_limit"
    assert row["limit"] == 0.0 and row["requested"] == 5800.0
    assert row["autonomy_level"] == "assisted"
    assert row["approved_by"] == "kasper.on.stage" and row["human_wait_s"] == 41.5
    assert row["money_hops"] == ["POST /payments/{paymentId}/execute"]
    # The link is the approval's own trace when there is one.
    assert row["link_trace_id"] == "b" * 32
    assert row["trace_ids"] == ["a" * 32, "b" * 32]


def test_a_claim_with_no_approval_links_its_first_trace(monkeypatch):
    _fake(monkeypatch, [REFUSAL])
    row = evidence.evidence_for(CLAIM, T0 // 1000 - 1000)
    assert row["approved_by"] is None and row["human_wait_s"] is None
    assert row["link_trace_id"] == "a" * 32


def test_a_resume_only_review_is_not_the_approval(monkeypatch):
    resume = _trace("c" * 32, [_span("5", "reviewer-ui", "northstar.review.decision", T0 + 9_000_000, **{
        "northstar.claim.id": CLAIM, "northstar.review.decision": "approve",
        "northstar.review.reviewer.id": "someone.else", "northstar.review.resume_only": True})])
    _fake(monkeypatch, [REFUSAL, APPROVAL, resume])
    row = evidence.evidence_for(CLAIM, T0 // 1000 - 1000)
    assert row["approved_by"] == "kasper.on.stage"


def test_the_window_excludes_earlier_traces(monkeypatch):
    _fake(monkeypatch, [REFUSAL, APPROVAL])
    row = evidence.evidence_for(CLAIM, (T0 + 1_000_000) // 1000)
    assert row["trace_ids"] == ["b" * 32] and row["policy_decision"] is None


def test_claims_filed_before_the_run_are_not_this_runs(monkeypatch):
    _fake(monkeypatch, [REFUSAL, APPROVAL])
    # The decision is in the window, but the claim's first span is before filed_after.
    assert evidence.claim_ids_since(T0 // 1000 - 1000, filed_after_ms=T0 // 1000 - 500) == [CLAIM]
    assert evidence.claim_ids_since(T0 // 1000 - 1000, filed_after_ms=T0 // 1000 + 1) == []


def test_the_driver_never_mentions_opensearch():
    # OpenSearch left the demo (it stays in the stack for the evidence view):
    # not a call, not a help string, not a line printed to the room.
    from pathlib import Path
    driver = (Path(__file__).resolve().parents[2] / "scripts" / "demo.py").read_text()
    assert "opensearch" not in driver.lower()


def test_a_full_search_page_fails_loudly_instead_of_dropping_claims(monkeypatch):
    import pytest

    class Resp:
        def __init__(self, n): self.n = n
        def raise_for_status(self): pass
        def json(self): return {"data": [{"traceID": f"{i:032x}"} for i in range(self.n)]}

    monkeypatch.setattr(evidence.requests, "get", lambda *a, **k: Resp(evidence.SEARCH_LIMIT))
    with pytest.raises(AssertionError, match="search limit"):
        evidence._search("policy-decision-service", operation=evidence.DECISION_SPAN, since_ms=0)
    monkeypatch.setattr(evidence.requests, "get", lambda *a, **k: Resp(evidence.SEARCH_LIMIT - 1))
    assert len(evidence._search("policy-decision-service", since_ms=0)) == evidence.SEARCH_LIMIT - 1
