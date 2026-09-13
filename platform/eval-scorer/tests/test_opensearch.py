"""The two-step join, asserted on the query bodies rather than on a comment.

`scorer/opensearch.py`'s header says step 2 must not also filter on the claim
id. A comment saying so is exactly what has failed in this repo before, so
this file reads the bodies the client actually builds and fails if the
narrowing ever reappears. Nothing here opens a socket: `search` is replaced
with a recorder.
"""
from __future__ import annotations

import json

import pytest

import fixtures
from scorer.opensearch import (
    SPAN_PAGE_SIZE,
    TRACE_INDEX,
    OpenSearchClient,
    OpenSearchError,
    TrajectoryTruncated,
)
from scorer.trajectory import ATTR_CLAIM_ID

SINCE_MS = 1789322500000


class RecordingClient(OpenSearchClient):
    """An `OpenSearchClient` whose `search` answers from a script and records
    every body it was given."""

    def __init__(self, responses):
        super().__init__(base_url="http://recorded.invalid:9200")
        self.responses = list(responses)
        self.bodies = []

    def search(self, body):
        self.bodies.append(body)
        return self.responses.pop(0)


def _trace_bucket_response(*trace_ids):
    return {
        "aggregations": {
            "traces": {
                "buckets": [
                    {
                        "key": trace_id,
                        "doc_count": 10,
                        "first": {"value": 1789322500000 + index},
                    }
                    for index, trace_id in enumerate(trace_ids)
                ]
            }
        }
    }


def _hits_response(documents, total=None):
    return {
        "hits": {
            "hits": [{"_source": d} for d in documents],
            "total": {"value": len(documents) if total is None else total},
        }
    }


def _filters(body):
    return body["query"]["bool"]["filter"]


def test_step_one_filters_on_the_claim_id_and_the_window():
    client = RecordingClient([_trace_bucket_response("aaaa", "bbbb")])
    assert client.trace_ids_for_claim("CLM-1842", SINCE_MS) == ["aaaa", "bbbb"]

    filters = _filters(client.bodies[0])
    assert {"term": {f"attributes.{ATTR_CLAIM_ID}.keyword": "CLM-1842"}} in filters
    assert {"range": {"startTime": {"gte": SINCE_MS}}} in filters


def test_step_two_filters_on_trace_ids_and_never_on_the_claim_id():
    """GAP 4 -- DO NOT COLLAPSE THE TWO STEPS INTO ONE FILTER.

    `northstar.claim.id` does not cross the MCP boundary, so a claim-id filter
    here would drop every `policy.decision` span -- which is every span these
    scorers read. The service would then score an empty trajectory and report
    five compliant `not-applicable`s for a settlement that really happened.
    """
    client = RecordingClient([_hits_response([])])
    client.spans_in_traces(["aaaa", "bbbb"], SINCE_MS)

    body = client.bodies[0]
    filters = _filters(body)
    assert {"terms": {"traceId": ["aaaa", "bbbb"]}} in filters
    assert {"range": {"startTime": {"gte": SINCE_MS}}} in filters

    rendered = json.dumps(body)
    assert ATTR_CLAIM_ID not in rendered, (
        "the span query names the claim id, so the join has been collapsed "
        f"back into one filter: {rendered}"
    )
    assert body["size"] == SPAN_PAGE_SIZE
    assert body["track_total_hits"] is True
    assert body["sort"] == [{"startTime": "asc"}]


def test_no_trace_ids_means_no_second_query_at_all():
    """An empty `terms` filter matches nothing but still costs a round trip,
    and `{"terms": {"traceId": []}}` is the kind of query that quietly becomes
    a match-all after a refactor."""
    client = RecordingClient([])
    assert client.spans_in_traces([], SINCE_MS) == []
    assert client.bodies == []


def test_a_truncated_page_raises_rather_than_scoring_what_it_got():
    """The one failure mode that must never degrade into an answer: a
    truncated trajectory scores HIGHER than the real one, because a retry that
    fell off the end of the page reads as compliance."""
    client = RecordingClient([_hits_response([fixtures.root(0.0)], total=4000)])
    with pytest.raises(TrajectoryTruncated) as excinfo:
        client.spans_in_traces(["aaaa"], SINCE_MS)
    assert "1 of 4000" in str(excinfo.value)
    assert isinstance(excinfo.value, OpenSearchError)


class _Response:
    """The two methods `urllib.request.urlopen`'s context manager needs."""

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_an_opensearch_error_body_is_raised_and_not_read_as_no_hits(monkeypatch):
    """A 404 `index_not_found_exception` is a well-formed JSON body with an
    `error` key and no `hits`. Read as "no hits" it would score every claim
    compliant-by-absence, which is "I could not look" recorded as "there was
    nothing to find".

    Patched at `urlopen` rather than at `search`, deliberately: the error
    check lives *inside* `search`, so a fake that replaces `search` would test
    the fake. That is the mistake this test was written with and is the reason
    it is written this way now.
    """
    monkeypatch.setattr(
        "scorer.opensearch.urllib.request.urlopen",
        lambda request, timeout=None: _Response(
            {"error": {"type": "index_not_found_exception", "reason": "no such index"}}
        ),
    )
    with pytest.raises(OpenSearchError) as excinfo:
        OpenSearchClient("http://fake.invalid:9200").trace_ids_for_claim(
            "CLM-1842", SINCE_MS
        )
    assert "index_not_found_exception" in str(excinfo.value)


def test_an_unreachable_store_is_an_opensearch_error_not_a_bare_oserror(monkeypatch):
    """`server.py` answers 502 on `OpenSearchError` and 500 on anything else.
    A raw `URLError` escaping here would be reported as a bug in this service
    rather than as an unreachable backend."""

    def _refuse(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("scorer.opensearch.urllib.request.urlopen", _refuse)
    with pytest.raises(OpenSearchError):
        OpenSearchClient("http://fake.invalid:9200").trace_ids_for_claim(
            "CLM-1842", SINCE_MS
        )


def test_a_non_json_body_is_an_opensearch_error(monkeypatch):
    class _Garbage(_Response):
        def __init__(self):
            self._payload = b"<html>502 Bad Gateway</html>"

    monkeypatch.setattr(
        "scorer.opensearch.urllib.request.urlopen",
        lambda request, timeout=None: _Garbage(),
    )
    with pytest.raises(OpenSearchError):
        OpenSearchClient("http://fake.invalid:9200").trace_ids_for_claim(
            "CLM-1842", SINCE_MS
        )


def test_the_search_url_names_the_index_this_task_measured(monkeypatch):
    seen = {}

    def _capture(request, timeout=None):
        seen["url"] = request.full_url
        seen["method"] = request.method
        seen["body"] = json.loads(request.data)
        return _Response(_trace_bucket_response("aaaa"))

    monkeypatch.setattr("scorer.opensearch.urllib.request.urlopen", _capture)
    OpenSearchClient("http://fake.invalid:9200").trace_ids_for_claim("CLM-1842", SINCE_MS)
    assert seen["url"] == f"http://fake.invalid:9200/{TRACE_INDEX}/_search"
    assert seen["method"] == "POST"
    assert seen["body"]["size"] == 0


def test_the_trajectory_is_built_from_both_steps():
    client = RecordingClient(
        [
            _trace_bucket_response(fixtures.REFUSAL_TRACE, fixtures.APPROVAL_TRACE),
            _hits_response(
                [
                    fixtures.root(0.0),
                    fixtures.decision(2.0, "propose_payment", "permit", amount=6200.0),
                    fixtures.decision(
                        4.0, "execute_payment", "deny", amount=6200.0, limit=6000.0
                    ),
                ]
            ),
        ]
    )
    trajectory = client.trajectory_for_claim("CLM-1842", SINCE_MS)
    assert trajectory.claim_id == "CLM-1842"
    assert len(trajectory.spans) == 3
    assert [d.tool for d in trajectory.decisions()] == [
        "propose_payment",
        "execute_payment",
    ]


def test_the_index_name_is_the_one_task_1_measured():
    assert TRACE_INDEX == "ss4o_traces-default-namespace"


def test_the_base_url_comes_from_the_environment_when_set(monkeypatch):
    monkeypatch.setenv("NORTHSTAR_OPENSEARCH_URL", "http://elsewhere:9200/")
    assert OpenSearchClient().base_url == "http://elsewhere:9200"
    assert OpenSearchClient("http://explicit:9200").base_url == "http://explicit:9200"
