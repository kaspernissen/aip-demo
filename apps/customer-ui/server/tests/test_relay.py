from __future__ import annotations

import asyncio
import json

import pytest

from main import PUSH_URL
from relay import Relay

TASK = {
    "id": "t1",
    "contextId": "c1",
    "kind": "task",
    "status": {"state": "input-required", "message": {"parts": [{"kind": "text", "text": "{}"}]}},
}


def _task(state, **kw):
    return {**TASK, "status": {"state": state}, **kw}


def _push(client, token, task):
    return client.post("/a2a/push", json=task, headers={"x-a2a-notification-token": token})


def _apush(aclient, token, task):
    return aclient.post("/a2a/push", json=task, headers={"X-A2A-Notification-Token": token})


def _frames(text):
    return [json.loads(b[len("data: "):]) for b in text.split("\n\n") if b.startswith("data: ")]


def test_push_config_issues_a_fresh_token_and_the_fixed_url(client):
    a, b = client.post("/claims/push-config").json(), client.post("/claims/push-config").json()
    assert a["url"] == PUSH_URL and a["token"] != b["token"] and len(a["token"]) >= 32
    assert PUSH_URL == "http://customer-ui.northstar-agents.svc.cluster.local:8080/a2a/push"


def test_a_push_without_a_token_or_with_a_wrong_one_is_rejected(client):
    assert client.post("/a2a/push", json=TASK).status_code == 401
    assert client.post("/a2a/push", json=TASK, headers={"x-a2a-notification-token": "nope"}).status_code == 401


def test_a_push_for_a_second_task_on_the_same_token_is_rejected(client):
    tok = client.post("/claims/push-config").json()["token"]
    assert _push(client, tok, TASK).status_code == 204
    assert _push(client, tok, {**TASK, "id": "other"}).status_code == 409


def test_a_push_that_is_not_json_is_a_400(client):
    tok = client.post("/claims/push-config").json()["token"]
    r = client.post("/a2a/push", content=b"not json", headers={"x-a2a-notification-token": tok})
    assert r.status_code == 400


async def test_a_push_before_the_subscriber_is_delivered_at_once(aclient):
    tok = (await aclient.post("/claims/push-config")).json()["token"]
    assert (await _apush(aclient, tok, _task("input-required"))).status_code == 204
    assert (await _apush(aclient, tok, _task("completed"))).status_code == 204
    r = await asyncio.wait_for(aclient.get("/claims/events", headers={"x-claim-token": tok}), 5)
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-store"
    assert [f["status"]["state"] for f in _frames(r.text)] == ["input-required", "completed"]


async def test_the_subscriber_waits_and_ends_on_the_terminal_push(aclient):
    tok = (await aclient.post("/claims/push-config")).json()["token"]
    events = asyncio.create_task(aclient.get("/claims/events", headers={"x-claim-token": tok}))
    await asyncio.sleep(0.05)
    assert not events.done()  # blocked on the event, not finished
    await _apush(aclient, tok, _task("input-required"))
    await asyncio.sleep(0.05)
    assert not events.done()  # not terminal: still open
    await _apush(aclient, tok, _task("completed"))
    r = await asyncio.wait_for(events, 5)
    assert [f["status"]["state"] for f in _frames(r.text)] == ["input-required", "completed"]


async def test_a_first_push_that_is_already_terminal_binds_the_token_and_ends_the_stream(aclient):
    # claims-agent pushes terminal states only, so the FIRST push a token sees is the outcome.
    tok = (await aclient.post("/claims/push-config")).json()["token"]
    assert (await _apush(aclient, tok, _task("completed"))).status_code == 204
    assert (await _apush(aclient, tok, {**_task("completed"), "id": "other"})).status_code == 409
    r = await asyncio.wait_for(aclient.get("/claims/events", headers={"x-claim-token": tok}), 5)
    assert [f["status"]["state"] for f in _frames(r.text)] == ["completed"]


def test_events_without_a_known_token_is_rejected(client):
    assert client.get("/claims/events", headers={"x-claim-token": "nope"}).status_code == 401
    assert client.get("/claims/events").status_code == 401


def test_a_token_in_the_query_string_is_not_accepted(client):
    tok = client.post("/claims/push-config").json()["token"]
    assert client.get(f"/claims/events?token={tok}").status_code == 401


def test_a_non_ascii_token_is_a_401_not_a_500(client):
    # Starlette decodes header values as latin-1; "\xe9" would crash compare_digest as str.
    assert client.get("/claims/events", headers={"x-claim-token": b"caf\xe9"}).status_code == 401
    assert client.post("/a2a/push", json=TASK, headers={"x-a2a-notification-token": b"caf\xe9"}).status_code == 401


async def test_an_eviction_after_the_check_does_not_break_an_open_stream():
    r = Relay(max_claims=1)
    tok = r.issue()
    claim = r.claim_for(tok)
    r.push(tok, _task("completed"))
    r.issue()  # evicts tok
    assert [s["status"]["state"] async for s in r.follow(claim)] == ["completed"]


def test_healthz(client):
    assert client.get("/healthz").status_code == 200


def test_the_token_never_appears_in_a_span(client, span_exporter):
    tok = client.post("/claims/push-config").json()["token"]
    assert _push(client, tok, _task("completed")).status_code == 204
    assert client.get("/claims/events", headers={"x-claim-token": tok}).status_code == 200
    spans = span_exporter.get_finished_spans()
    assert spans, "no spans were recorded, so this test would prove nothing"
    for span in spans:
        for key, value in dict(span.attributes).items():
            assert tok not in str(value), f"token in {span.name}:{key}"
        assert tok not in span.name


def test_the_static_export_is_served_like_nginx_did(client):
    assert client.get("/").text == "INDEX"
    assert client.get("/foo").text == "FOO"
    assert client.get("/bar").text == "BAR-INDEX"  # $uri/index.html
    assert client.get("/_next/app.js").text == "JS"
    assert client.get("/no/such/page").text == "INDEX"  # /index.html fallback
    assert client.get("/%2e%2e/%2e%2e/etc/passwd").text == "INDEX"  # no traversal


def test_relay_evicts_the_oldest_token_past_its_bound():
    r = Relay(max_claims=2)
    a, b, c = r.issue(), r.issue(), r.issue()
    assert not r.known(a) and r.known(b) and r.known(c)


def test_relay_has_no_timer():
    import relay, inspect
    src = inspect.getsource(relay)
    assert "sleep" not in src and "wait_for" not in src and "timeout" not in src
