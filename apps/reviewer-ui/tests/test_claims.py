import json

import httpx
import pytest

import claims


def _transport(get_result, send_result, seen):
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if body["method"] == "tasks/get":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], **get_result})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], **send_result})
    return httpx.MockTransport(handler)


TASK = {"result": {"id": "task-1", "contextId": "ctx-1", "kind": "task",
                   "status": {"state": "input-required"}}}


async def test_resume_reads_the_context_then_sends_on_the_same_task_without_blocking():
    seen = []
    client = claims.ClaimsClient("http://claims", transport=_transport(TASK, TASK, seen))
    state = await client.resume("task-1", {"decision": "approved", "paymentId": "PAY-000001"})
    assert state == "input-required"
    assert [b["method"] for b in seen] == ["tasks/get", "message/send"]
    message = seen[1]["params"]["message"]
    assert message["taskId"] == "task-1" and message["contextId"] == "ctx-1"
    assert seen[1]["params"]["configuration"]["blocking"] is False


async def test_a_task_that_is_gone_is_resume_failed():
    client = claims.ClaimsClient("http://claims", transport=_transport(
        {"error": {"code": -32001, "message": "Task not found"}}, TASK, []))
    with pytest.raises(claims.ResumeFailed):
        await client.resume("task-1", {})


async def test_a_task_that_is_not_waiting_is_resume_failed():
    done = {"result": {**TASK["result"], "status": {"state": "completed"}}}
    client = claims.ClaimsClient("http://claims", transport=_transport(done, TASK, []))
    with pytest.raises(claims.ResumeFailed):
        await client.resume("task-1", {})


async def test_a_refused_resume_is_resume_failed():
    client = claims.ClaimsClient("http://claims", transport=_transport(
        TASK, {"error": {"code": -32603, "message": "the ledger shows 'awaiting_approval'"}}, []))
    with pytest.raises(claims.ResumeFailed, match="awaiting_approval"):
        await client.resume("task-1", {})


async def test_a_reply_for_another_request_id_is_resume_failed():
    def handler(request):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "someone-else", **TASK})
    client = claims.ClaimsClient("http://claims", transport=httpx.MockTransport(handler))
    with pytest.raises(claims.ResumeFailed):
        await client.resume("task-1", {})
