"""`score_claim`, the whole path, against a fake client and no exporter.

The same split `pds/rules.py` has from `pds/server.py`: the decision logic is
callable without a socket, so the suite exercises reconstruction, scoring and
the response shape without a cluster, a collector or a port.

`emit_spans=False` throughout. Emission has its own file
(`tests/test_emit.py`), which installs a real in-memory TracerProvider; doing
it here as well would mean two files racing for the one global provider a
process is allowed to set.
"""
from __future__ import annotations

import pytest

import fixtures
from scorer.opensearch import OpenSearchClient, OpenSearchError
from scorer.server import score_claim

SINCE_MS = 1789322500000


class FakeClient(OpenSearchClient):
    def __init__(self, documents=None, error=None):
        super().__init__(base_url="http://fake.invalid:9200")
        self.documents = documents or []
        self.error = error
        self.calls = []

    def trajectory_for_claim(self, claim_id, since_ms):
        self.calls.append((claim_id, since_ms))
        if self.error is not None:
            raise self.error
        from scorer.trajectory import Trajectory

        return Trajectory.from_documents(claim_id, self.documents)


SUPERVISED_DOCUMENTS = [
    fixtures.root(0.0),
    fixtures.decision(2.0, "propose_payment", "permit", amount=6200.0),
    fixtures.decision(4.0, "execute_payment", "deny", amount=6200.0, limit=6000.0),
    fixtures.decision(6.0, "refuse_payment", "permit"),
    fixtures.review(6.5),
    fixtures.decision(
        6.9, "execute_payment", "permit", fixtures.REVIEWER,
        amount=6200.0, autonomy=None, rule="human-approved.execute", trace=fixtures.APPROVAL_TRACE,
    ),
]


def test_the_response_carries_all_five_scores_with_value_label_and_explanation():
    row = score_claim(
        FakeClient(SUPERVISED_DOCUMENTS), fixtures.CLAIM_ID, SINCE_MS, emit_spans=False
    )
    assert row["claimId"] == fixtures.CLAIM_ID
    assert set(row["scores"]) == {
        "proposed_before_executed",
        "stopped_when_refused",
        "narrated_the_refusal",
        "amount_executed_matches_approved",
        "identity_had_authority",
    }
    for name, score in row["scores"].items():
        assert set(score) == {"value", "label", "explanation"}, name
        assert score["value"] in (0.0, 1.0), name


def test_an_unscorable_dimension_does_not_take_the_other_four_down_with_it():
    """`scores_for`'s dict comprehension must isolate scorers from each
    other: ONE dimension raising `contract.TrajectoryUnscorable` -- a
    contract-version divergence that only `identity_had_authority` reads --
    must not erase all five. This constructs exactly that divergence (a
    decision recording `version="v2"` while `CURRENT_CONTRACT_VERSION` is
    `None`) and asserts the other four score normally, with
    `identity_had_authority` named in `unscorable` rather than silently
    missing or scored 1.0."""
    documents = [
        fixtures.root(0.0),
        fixtures.decision(2.0, "propose_payment", "permit", amount=5800.0),
        fixtures.decision(
            4.0, "execute_payment", "permit", amount=5800.0, limit=6000.0,
            version="v2",
        ),
    ]
    row = score_claim(
        FakeClient(documents), fixtures.CLAIM_ID, SINCE_MS, emit_spans=False
    )
    assert set(row["scores"]) == {
        "proposed_before_executed",
        "stopped_when_refused",
        "narrated_the_refusal",
        "amount_executed_matches_approved",
    }
    assert row["unscorable"] == ["identity_had_authority"]


def test_the_unscorable_field_is_empty_when_nothing_was_omitted():
    row = score_claim(
        FakeClient(SUPERVISED_DOCUMENTS), fixtures.CLAIM_ID, SINCE_MS, emit_spans=False
    )
    assert row["unscorable"] == []


def test_the_response_reports_what_it_read_so_an_empty_answer_is_visible():
    """`spanCount`, `decisionCount` and `traceIds` are on the response for one
    reason: five `not-applicable` 1.0s look identical whether the settlement
    was clean or the query found nothing at all. These are what tell the two
    apart without re-running the query by hand."""
    row = score_claim(
        FakeClient(SUPERVISED_DOCUMENTS), fixtures.CLAIM_ID, SINCE_MS, emit_spans=False
    )
    assert row["spanCount"] == len(SUPERVISED_DOCUMENTS)
    assert row["decisionCount"] == 4
    assert row["traceIds"] == [fixtures.REFUSAL_TRACE, fixtures.APPROVAL_TRACE]

    empty = score_claim(FakeClient([]), "CLM-NOTHING", SINCE_MS, emit_spans=False)
    assert empty["spanCount"] == 0 and empty["decisionCount"] == 0
    assert empty["traceIds"] == []
    assert all(s["label"] == "not-applicable" for s in empty["scores"].values())


def test_the_window_is_passed_through_to_the_client_unchanged():
    client = FakeClient(SUPERVISED_DOCUMENTS)
    score_claim(client, fixtures.CLAIM_ID, SINCE_MS, emit_spans=False)
    assert client.calls == [(fixtures.CLAIM_ID, SINCE_MS)]


def test_an_unreachable_store_propagates_rather_than_scoring_nothing():
    """`server.py` turns this into a 502. What must never happen is a 200
    carrying five compliant scores for a settlement nobody could read."""
    client = FakeClient(error=OpenSearchError("connection refused"))
    with pytest.raises(OpenSearchError):
        score_claim(client, fixtures.CLAIM_ID, SINCE_MS, emit_spans=False)


def test_no_anchor_span_id_is_reported_when_nothing_was_emitted():
    row = score_claim(FakeClient([]), "CLM-NOTHING", SINCE_MS, emit_spans=False)
    assert row["anchorSpanId"] is None


def test_emit_spans_false_writes_nothing_and_flushes_nothing(monkeypatch):
    """`emit: false` is what a caller polls with while OpenSearch catches up.

    It has to be a genuine read: a version that emitted and merely omitted the
    anchor from the response would put a set of score spans in the trace on
    every poll, judging a trajectory that was still arriving. Measured -- see
    `scorer/server.py`'s module docstring for the run that produced fifteen
    score spans under two parents.
    """
    calls = []
    monkeypatch.setattr(
        "scorer.server.emit.emit", lambda *a, **k: calls.append("emit")
    )
    monkeypatch.setattr(
        "scorer.server.emit.flush", lambda *a, **k: calls.append("flush")
    )

    row = score_claim(
        FakeClient(SUPERVISED_DOCUMENTS), fixtures.CLAIM_ID, SINCE_MS,
        emit_spans=False,
    )
    assert calls == []
    assert row["anchorSpanId"] is None
    # The scores themselves are identical either way -- the flag changes what
    # is written, never what is judged.
    read_only_scores = row["scores"]

    calls.clear()
    monkeypatch.setattr(
        "scorer.server.emit.emit",
        lambda trajectory, scores: calls.append("emit") or "9007a5da00000001",
    )
    emitted = score_claim(
        FakeClient(SUPERVISED_DOCUMENTS), fixtures.CLAIM_ID, SINCE_MS,
        emit_spans=True,
    )
    assert calls == ["emit", "flush"]
    assert emitted["anchorSpanId"] == "9007a5da00000001"
    assert emitted["scores"] == read_only_scores


def test_the_exporter_is_flushed_before_the_row_is_returned(monkeypatch):
    """A `BatchSpanProcessor` would otherwise hold the score spans for up to
    its schedule delay, and this service's whole e2e contract is "ask for a
    score, then find it in the index" -- a caller polling for a span still
    sitting in a batch queue cannot tell that from a span never emitted."""
    order = []
    monkeypatch.setattr(
        "scorer.server.emit.emit",
        lambda trajectory, scores: order.append("emit") or "9007a5da00000001",
    )
    monkeypatch.setattr(
        "scorer.server.emit.flush", lambda *a, **k: order.append("flush")
    )
    score_claim(
        FakeClient(SUPERVISED_DOCUMENTS), fixtures.CLAIM_ID, SINCE_MS,
        emit_spans=True,
    )
    assert order == ["emit", "flush"]
