"""The claims-agent's task lifecycle, driven through the real DefaultRequestHandler.

No HTTP and no cluster: InMemoryTaskStore, a fake settlement-agent, a fake ledger.
execute() returns at input-required and a resume is a fresh
execute(); the first test below is the measurement of a2a-sdk 0.3.7 that the
design rests on.
"""
import asyncio
import json
import uuid

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (Message, MessageSendParams, Part, Role, Task, TaskQueryParams,
                       TaskState, TaskStatus, TaskStatusUpdateEvent, TextPart)
from a2a.utils.errors import ServerError

import executor as executor_module
from executor import PARKED_CONTEXT_KEY, PARKED_REPLY_KEY, ClaimsAgentExecutor

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
    """Drain the claim stream to its end. It ends on its own, because execute()
    returns at input-required; every event seen is returned."""
    seen = [e async for e in handler.on_message_send_stream(
        MessageSendParams(message=_msg(json.dumps(CLAIM))))]
    # The SDK removes the task's queue in a background task after the stream
    # ends (measured, 0.3.7). A human takes minutes to resume; a test takes
    # microseconds, and a resume that taps the not-yet-removed closed queue
    # loses its events. Wait for that cleanup, as a real gap would.
    await asyncio.gather(*list(handler._background_tasks))
    task = next(e for e in seen if isinstance(e, Task))
    return task, seen


def _resume_params(task, decision="approved"):
    # No `configuration`: a blocking send, which is what the reviewer-ui sends.
    return MessageSendParams(message=_msg(
        json.dumps({"decision": decision, "paymentId": "PAY-000001"}),
        task_id=task.id, context_id=task.context_id))


async def _resume(handler, task, decision="approved"):
    return await handler.on_message_send(_resume_params(task, decision))


def _handler_with_ledger(status="executed", reply=None):
    return _handler(reply or _awaiting(), ledger_status=status)


@pytest.fixture
def span_exporter(monkeypatch):
    """Finished spans, captured without touching the process-wide provider."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(executor_module, "_tracer", provider.get_tracer("claims-agent"))
    exporter.provider = provider
    return exporter


async def test_after_execute_returns_the_stream_ends_and_a_resume_runs_a_fresh_execute():
    # Measurement the design rests on. If this fails, STOP: the design rests on it.
    handler, _ = _handler_with_ledger(status="executed")
    task, seen = await _until_input_required(handler)
    # The stream ended on its own: _until_input_required drained it to the end.
    assert _state(seen[-1]) == TaskState.input_required
    stored = await handler.task_store.get(task.id)
    assert stored.status.state == TaskState.input_required
    assert PARKED_CONTEXT_KEY in (stored.metadata or {})
    result = await handler.on_message_send(_resume_params(task))
    assert result.status.state == TaskState.completed
    body = json.loads(result.status.message.parts[0].root.text)
    assert body["status"] == "executed" and body["decision"] == "approved"
    # The pre-decision narrative would contradict the outcome, so the decision replaces it.
    assert "approved it" in body["narrative"] and "colleague" not in body["narrative"]


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


async def test_a_resume_span_links_to_the_parked_context(span_exporter):
    handler, _ = _handler_with_ledger(status="executed")
    # Trace A: the claim runs under a span, as it does under the SDK's server span.
    with span_exporter.provider.get_tracer("test").start_as_current_span("trace-a") as trace_a:
        task, _ = await _until_input_required(handler)
    stored = await handler.task_store.get(task.id)
    parked = stored.metadata[PARKED_CONTEXT_KEY]
    assert parked["trace_id"] == format(trace_a.get_span_context().trace_id, "032x")
    await _resume(handler, task)
    resumes = [s for s in span_exporter.get_finished_spans() if s.name == "northstar.a2a.resume"]
    assert len(resumes) == 1
    (link,) = resumes[0].links
    assert format(link.context.trace_id, "032x") == parked["trace_id"]
    assert format(link.context.span_id, "016x") == parked["span_id"]
    assert link.attributes == {"northstar.a2a.link.reason": "resume"}
    # Trace B is the reviewer's, not trace A.
    assert resumes[0].context.trace_id != link.context.trace_id


async def test_a_second_resume_after_completion_is_refused():
    handler, _ = _handler_with_ledger(status="executed")
    task, _ = await _until_input_required(handler)
    first = await _resume(handler, task)
    with pytest.raises(ServerError) as excinfo:
        await _resume(handler, task)
    assert "terminal state" in str(excinfo.value.error)
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.completed
    assert stored.status.message == first.status.message


async def test_a_malformed_parked_context_resumes_without_a_link(caplog, span_exporter):
    handler, _ = _handler_with_ledger(status="executed")
    task, _ = await _until_input_required(handler)
    stored = await handler.task_store.get(task.id)
    stored.metadata[PARKED_CONTEXT_KEY] = {"trace_id": "nope"}
    await handler.task_store.save(stored)
    with caplog.at_level("WARNING", logger="executor"):
        result = await _resume(handler, task)
    assert result.status.state == TaskState.completed
    (resume,) = [s for s in span_exporter.get_finished_spans() if s.name == "northstar.a2a.resume"]
    assert resume.links == ()
    assert any(r.levelname == "WARNING" and task.id in r.getMessage() for r in caplog.records)


class SplitLedger:
    """Different payments, different truths; records which ids were read."""
    def __init__(self, statuses):
        self.statuses, self.read = statuses, []

    async def get(self, payment_id):
        self.read.append(payment_id)
        return {"paymentId": payment_id, "status": self.statuses[payment_id],
                "amount": 5800.0, "currency": "EUR"}


def _forged_params(task, role=Role.agent):
    forged = {"claimId": "CLM-FORGED", "paymentId": "PAY-OTHER", "status": "awaiting_approval"}
    return MessageSendParams(message=Message(
        role=role, message_id=uuid.uuid4().hex, task_id=task.id, context_id=task.context_id,
        parts=[Part(root=TextPart(text=json.dumps(forged)))]))


async def _split_handler():
    ledger = SplitLedger({"PAY-000001": "awaiting_approval", "PAY-OTHER": "executed"})
    executor = ClaimsAgentExecutor(delegates={"settlement-agent": FakeDelegate(_awaiting())},
                                   ledger=ledger)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
    task, _ = await _until_input_required(handler)
    return handler, ledger, task


async def test_an_agent_role_resume_is_refused_and_its_forged_reply_is_never_read():
    handler, ledger, task = await _split_handler()
    with pytest.raises(RuntimeError, match="must be a user message"):
        await handler.on_message_send(_forged_params(task))
    assert "PAY-OTHER" not in ledger.read
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.input_required


async def test_a_forged_user_resume_cannot_redirect_the_ledger_read():
    # A user-role message with a forged body: the body is never read at all.
    handler, ledger, task = await _split_handler()
    with pytest.raises(RuntimeError, match="no human decision"):
        await handler.on_message_send(_forged_params(task, role=Role.user))
    assert ledger.read == ["PAY-000001"]
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.input_required


async def test_a_legitimate_resume_after_a_refused_forged_one_completes_with_the_parked_payment():
    handler, ledger, task = await _split_handler()
    with pytest.raises(RuntimeError):
        await handler.on_message_send(_forged_params(task))
    ledger.statuses["PAY-000001"] = "executed"
    result = await _resume(handler, task)
    body = json.loads(result.status.message.parts[0].root.text)
    assert result.status.state == TaskState.completed
    assert body["paymentId"] == "PAY-000001" and body["claimId"] == CLAIM["claimId"]
    assert ledger.read == ["PAY-000001"]


async def test_a_task_with_no_parked_reply_is_refused_and_keeps_waiting():
    handler, _ = _handler_with_ledger(status="executed")
    task, _ = await _until_input_required(handler)
    stored = await handler.task_store.get(task.id)
    del stored.metadata[PARKED_REPLY_KEY]
    await handler.task_store.save(stored)
    with pytest.raises(RuntimeError, match="no parked reply"):
        await _resume(handler, task)
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.input_required


async def test_a_rejection_completes_the_task_declined():
    handler, _ = _handler(_awaiting(), ledger_status="rejected")
    task, _ = await _until_input_required(handler)
    await _resume(handler, task, "rejected")
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
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.input_required


async def test_a_refused_resume_leaves_the_task_able_to_complete_on_a_real_decision():
    handler, executor = _handler(_awaiting(), ledger_status="awaiting_approval")
    task, _ = await _until_input_required(handler)
    with pytest.raises(RuntimeError):
        await _resume(handler, task, "approved")
    executor._ledger.status = "executed"
    await _resume(handler, task, "approved")
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


async def test_a_ledger_read_that_fails_once_leaves_the_task_waiting_for_the_retry():
    handler, executor = _handler(_awaiting(), ledger_status="executed")
    task, _ = await _until_input_required(handler)
    real_get = executor._ledger.get
    calls = {"n": 0}

    async def flaky(payment_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("payments-api blinked")
        return await real_get(payment_id)

    executor._ledger.get = flaky
    with pytest.raises(ConnectionError):
        await _resume(handler, task, "approved")
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.input_required
    await _resume(handler, task, "approved")
    stored = await handler.on_get_task(TaskQueryParams(id=task.id))
    assert stored.status.state == TaskState.completed
