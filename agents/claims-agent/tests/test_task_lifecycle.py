"""The claims-agent's task lifecycle, driven through the real DefaultRequestHandler.

No HTTP and no cluster: InMemoryTaskStore, a fake settlement-agent, a fake ledger.
These tests are also the measurement of a2a-sdk 0.3.7's resubscribe behaviour
(NOTES.md, M8 Task 2).
"""
import asyncio
import json
import uuid

import pytest
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (Message, MessageSendConfiguration, MessageSendParams, Part,
                       Role, Task, TaskIdParams, TaskQueryParams, TaskState,
                       TaskStatus, TaskStatusUpdateEvent, TextPart)
from a2a.utils.errors import ServerError

from executor import ClaimsAgentExecutor

CLAIM = {"claimId": "CLM-T1", "policyNumber": "POL-1", "estimatedAmount": 5800}


class FakeDelegate:
    def __init__(self, reply):
        self.reply, self.sent = reply, []

    async def send(self, payload):
        self.sent.append(payload)
        return {**self.reply}


class FakeLedger:
    def __init__(self, status):
        self.status = status

    async def get(self, payment_id):
        return {"paymentId": payment_id, "status": self.status, "amount": 5800.0, "currency": "EUR"}


def _awaiting():
    return {"paymentId": "PAY-000001", "amount": 5800.0, "currency": "EUR",
            "status": "awaiting_approval", "narrative": "A colleague will review this.",
            "rule": "settlement-agent.assisted.execute_limit", "limit": 0.0}


def _msg(text, task_id=None, context_id=None):
    return Message(role=Role.user, message_id=uuid.uuid4().hex, task_id=task_id,
                   context_id=context_id, parts=[Part(root=TextPart(text=text))])


def _handler(reply, ledger_status="executed"):
    executor = ClaimsAgentExecutor(delegates={"settlement-agent": FakeDelegate(reply)},
                                   ledger=FakeLedger(ledger_status))
    return DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore()), executor


def _state(event):
    if isinstance(event, Task):
        return event.status.state
    if isinstance(event, TaskStatusUpdateEvent):
        return event.status.state
    return None


async def _until_input_required(handler):
    stream = handler.on_message_send_stream(MessageSendParams(message=_msg(json.dumps(CLAIM))))
    seen = []
    async for event in stream:
        seen.append(event)
        if _state(event) == TaskState.input_required:
            break
    await stream.aclose()   # the customer-ui closes its claim stream here
    task = next(e for e in seen if isinstance(e, Task))
    return task, seen


async def _resume(handler, task, decision="approved"):
    return await handler.on_message_send(MessageSendParams(
        message=_msg(json.dumps({"decision": decision, "paymentId": "PAY-000001"}),
                     task_id=task.id, context_id=task.context_id),
        configuration=MessageSendConfiguration(blocking=False)))


async def test_a_claim_within_the_band_completes_without_waiting():
    handler, _ = _handler({**_awaiting(), "status": "executed", "rule": None, "limit": None})
    states = [_state(e) async for e in handler.on_message_send_stream(
        MessageSendParams(message=_msg(json.dumps(CLAIM))))]
    assert states[-1] == TaskState.completed
    assert TaskState.input_required not in states


async def test_a_claim_above_the_band_waits_with_the_rule_and_the_limit():
    handler, _ = _handler(_awaiting())
    _, seen = await _until_input_required(handler)
    update = seen[-1]
    body = json.loads(update.status.message.parts[0].root.text)
    assert body["status"] == "awaiting_approval"
    assert body["rule"] == "settlement-agent.assisted.execute_limit" and body["limit"] == 0.0
    assert update.final is False


async def test_the_task_id_is_passed_down_to_the_settlement_agent():
    handler, executor = _handler(_awaiting())
    task, _ = await _until_input_required(handler)
    assert executor._delegates["settlement-agent"].sent[0]["a2aTaskId"] == task.id


async def test_resubscribe_stays_open_and_delivers_completed_after_the_resume():
    handler, _ = _handler(_awaiting(), ledger_status="executed")
    task, _ = await _until_input_required(handler)
    got = []

    async def listen():
        async for event in handler.on_resubscribe_to_task(TaskIdParams(id=task.id)):
            got.append(event)

    listener = asyncio.create_task(listen())
    await asyncio.sleep(0.2)
    assert not listener.done()          # open, waiting on the human
    await _resume(handler, task, "approved")
    await asyncio.wait_for(listener, timeout=5)
    completed = [e for e in got if _state(e) == TaskState.completed]
    assert completed, got
    body = json.loads(completed[-1].status.message.parts[0].root.text)
    assert body["status"] == "executed" and body["decision"] == "approved"
    # The pre-decision narrative ("I could not release this") would contradict
    # the outcome, so the decision replaces it.
    assert "approved it" in body["narrative"] and "colleague" not in body["narrative"]
    # Design pin: the stream ends when execute() returns and the SDK drains the
    # queue, never on a final update, which makes a consumer close the root
    # queue immediately. (In-process the SDK did not lose the event either way;
    # see NOTES.md "M8 Task 2 measurements".)
    assert completed[-1].final is False


async def test_a_rejection_completes_the_task_declined():
    handler, _ = _handler(_awaiting(), ledger_status="rejected")
    task, _ = await _until_input_required(handler)
    await _resume(handler, task, "rejected")
    await asyncio.sleep(0.2)
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.completed
    body = json.loads(stored.status.message.parts[0].root.text)
    assert body["status"] == "rejected" and body["decision"] == "rejected"
    assert "Nothing was paid" in body["narrative"]


async def test_a_resume_the_ledger_does_not_back_changes_nothing():
    handler, _ = _handler(_awaiting(), ledger_status="awaiting_approval")
    task, _ = await _until_input_required(handler)
    # The refusal reaches the caller (the reviewer-ui records it as resume_error).
    with pytest.raises(RuntimeError, match="no human decision"):
        await _resume(handler, task, "approved")
    await asyncio.sleep(0.2)
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.input_required


async def test_a_refused_resume_leaves_the_task_able_to_complete_on_a_real_decision():
    handler, executor = _handler(_awaiting(), ledger_status="awaiting_approval")
    task, _ = await _until_input_required(handler)
    with pytest.raises(RuntimeError):
        await _resume(handler, task, "approved")
    executor._ledger.status = "executed"
    await _resume(handler, task, "approved")
    await asyncio.sleep(0.2)
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.completed


async def test_a_resume_for_a_task_not_waiting_here_is_an_error():
    handler, _ = _handler(_awaiting())
    with pytest.raises(ServerError):
        await _resume(handler, Task(id="no-such-task", context_id="c",
                                    status=TaskStatus(state=TaskState.input_required)))


async def test_tasks_get_after_completion_reads_completed():
    handler, _ = _handler(_awaiting(), ledger_status="executed")
    task, _ = await _until_input_required(handler)
    await _resume(handler, task, "approved")
    await asyncio.sleep(0.2)
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.completed


async def test_a_refusal_the_ledger_never_recorded_completes_the_task_and_never_parks_it():
    # Controller ruling: refusal_unrecorded is terminal for the claims-agent.
    reply = {**_awaiting(), "status": "refusal_unrecorded"}
    handler, executor = _handler(reply)
    stream = handler.on_message_send_stream(MessageSendParams(message=_msg(json.dumps(CLAIM))))
    events = [e async for e in stream]
    states = [_state(e) for e in events]
    assert states[-1] == TaskState.completed
    assert TaskState.input_required not in states
    body = json.loads(events[-1].status.message.parts[0].root.text)
    assert body["status"] == "refusal_unrecorded"
    assert "could not be routed for review" in body["narrative"]
    assert executor._waiting == {}


async def test_resubscribe_after_the_task_finished_is_refused_not_streamed():
    # Measured (a2a-sdk 0.3.7): once execute() has returned the task is terminal
    # in the store and tasks/resubscribe answers InvalidParams "is in terminal
    # state". A task the store never held (a restarted claims-agent) is
    # TaskNotFound. Either way the customer-ui's awaitOutcome reads tasks/get once and resolves if terminal.
    handler, _ = _handler(_awaiting(), ledger_status="executed")
    task, _ = await _until_input_required(handler)
    await _resume(handler, task, "approved")
    await asyncio.sleep(0.2)
    with pytest.raises(ServerError) as excinfo:
        async for _ in handler.on_resubscribe_to_task(TaskIdParams(id=task.id)):
            pass
    assert "terminal state" in str(excinfo.value.error)
    with pytest.raises(ServerError) as gone:
        async for _ in handler.on_resubscribe_to_task(TaskIdParams(id="never-existed")):
            pass
    assert "not found" in str(gone.value.error).lower()


async def test_a_client_still_on_the_claim_stream_and_a_resubscriber_both_see_completed():
    # Why the completion is final=False. The EventConsumer of the first stream
    # closes the ROOT queue immediately on a final update, which clears every
    # tapped child (the resubscriber's) before it reads the event. With the
    # first stream detached (the customer-ui's case) that cannot happen, so this
    # test keeps it attached.
    handler, _ = _handler(_awaiting(), ledger_status="executed")
    stream = handler.on_message_send_stream(MessageSendParams(message=_msg(json.dumps(CLAIM))))
    first, resub = [], []

    async def stay_on_the_claim_stream():
        async for event in stream:
            first.append(event)

    async def resubscribed(task_id):
        async for event in handler.on_resubscribe_to_task(TaskIdParams(id=task_id)):
            resub.append(event)

    staying = asyncio.create_task(stay_on_the_claim_stream())
    for _ in range(100):
        await asyncio.sleep(0.05)
        if any(_state(e) == TaskState.input_required for e in first):
            break
    task = next(e for e in first if isinstance(e, Task))
    listener = asyncio.create_task(resubscribed(task.id))
    await asyncio.sleep(0.2)
    await _resume(handler, task, "approved")
    await asyncio.wait_for(asyncio.gather(staying, listener), timeout=5)
    assert TaskState.completed in [_state(e) for e in resub], resub
    assert TaskState.completed in [_state(e) for e in first], first


class FailingDelegate:
    async def send(self, payload):
        raise RuntimeError("settlement-agent returned an error: amount must not be negative")


async def test_a_settlement_failure_fails_the_task_with_the_real_reason_instead_of_dropping_the_stream():
    # On message/stream the task already exists, so an exception out of execute()
    # would abort the HTTP body. The task is failed, with the reason in `error`.
    executor = ClaimsAgentExecutor(delegates={"settlement-agent": FailingDelegate()},
                                   ledger=FakeLedger("executed"))
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
    events = [e async for e in handler.on_message_send_stream(
        MessageSendParams(message=_msg(json.dumps(CLAIM))))]
    assert _state(events[-1]) == TaskState.failed
    body = json.loads(events[-1].status.message.parts[0].root.text)
    assert body["status"] == "failed" and "amount must not be negative" in body["error"]
    assert executor._waiting == {}
