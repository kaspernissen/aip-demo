"""A2A push notifications: the push URL is client-supplied, so it is checked
against an exact allowlist before the task starts (SSRF guard)."""
import uuid

import httpx
import pytest
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import JSONRPCHandler
from a2a.server.tasks import InMemoryPushNotificationConfigStore, InMemoryTaskStore, TaskUpdater
from a2a.types import (AgentCapabilities, AgentCard, DeleteTaskPushNotificationConfigParams,
                       GetTaskPushNotificationConfigParams, ListTaskPushNotificationConfigParams,
                       Message, MessageSendConfiguration,
                       MessageSendParams, Part, PushNotificationConfig, Role,
                       SendStreamingMessageRequest, Task, TaskIdParams, TaskPushNotificationConfig,
                       TaskState, TaskStatus, TextPart)

from push import (AllowlistedRequestHandler, PushConfigReadRefused, PushUrlRefused,
                  TerminalPushNotificationSender, allowlist_from_env)

ALLOWED = "http://customer-ui.northstar-agents.svc.cluster.local:8080/a2a/push"


class CountingExecutor(AgentExecutor):
    def __init__(self):
        self.started = 0

    async def execute(self, context, event_queue):
        self.started += 1
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.complete()

    async def cancel(self, context, event_queue):
        pass


class RecordingSender:
    def __init__(self):
        self.pushed = []

    async def send_notification(self, task):
        self.pushed.append(task)


def _handler(allowlist):
    executor = CountingExecutor()
    handler = AllowlistedRequestHandler(
        agent_executor=executor, task_store=InMemoryTaskStore(),
        push_config_store=InMemoryPushNotificationConfigStore(),
        push_sender=RecordingSender(), push_url_allowlist=allowlist)
    handler.executor = executor
    return handler


def _params(push_url, token=None):
    return MessageSendParams(
        message=Message(role=Role.user, message_id=uuid.uuid4().hex,
                        parts=[Part(root=TextPart(text="{}"))]),
        configuration=MessageSendConfiguration(
            push_notification_config=PushNotificationConfig(url=push_url, token=token)))


@pytest.mark.parametrize("url", [
    ALLOWED + "/", ALLOWED.upper(), ALLOWED + "?x=1", "http://example.com/a2a/push",
    "http://169.254.169.254/latest/meta-data", ""])
async def test_a_push_url_off_the_allowlist_is_refused_before_the_task_starts(url):
    handler = _handler([ALLOWED])
    with pytest.raises(PushUrlRefused):
        await handler.on_message_send_stream(_params(url)).__anext__()
    with pytest.raises(PushUrlRefused):
        await handler.on_message_send(_params(url))
    assert handler.executor.started == 0


async def test_the_refusal_reaches_the_client_as_json_rpc_invalid_params_and_the_executor_never_ran():
    handler = _handler([ALLOWED])
    card = AgentCard(name="t", description="t", url="http://t", version="1",
                     capabilities=AgentCapabilities(streaming=True, push_notifications=True),
                     default_input_modes=["text"], default_output_modes=["text"], skills=[])
    rpc = JSONRPCHandler(card, handler)
    req = SendStreamingMessageRequest(id="1", params=_params("http://example.com/"))
    responses = [r async for r in rpc.on_message_send_stream(req)]
    assert len(responses) == 1
    assert responses[0].root.error.code == -32602
    assert handler.executor.started == 0


async def test_the_allowlisted_url_is_accepted_stored_and_pushed_to():
    handler = _handler([ALLOWED])
    events = [e async for e in handler.on_message_send_stream(_params(ALLOWED, token="t"))]
    task_id = events[0].task_id
    configs = await handler._push_config_store.get_info(task_id)
    assert [c.url for c in configs] == [ALLOWED]
    assert handler.executor.started == 1
    assert handler._push_sender.pushed  # the SDK pushed after events of the stored config


async def test_tasks_push_notification_config_set_is_checked_too():
    handler = _handler([ALLOWED])
    params = TaskPushNotificationConfig(
        task_id="t1", push_notification_config=PushNotificationConfig(url="http://example.com/"))
    with pytest.raises(PushUrlRefused):
        await handler.on_set_task_push_notification_config(params)


def test_the_allowlist_env_is_parsed_exactly():
    assert allowlist_from_env({"CLAIMS_AGENT_PUSH_URL_ALLOWLIST": f" {ALLOWED} , "}) == [ALLOWED]
    assert allowlist_from_env({}) == [ALLOWED]


async def _sent_for(state):
    sent = []

    def respond(request):
        sent.append(request)
        return httpx.Response(204)

    store = InMemoryPushNotificationConfigStore()
    await store.set_info("t1", PushNotificationConfig(url=ALLOWED, token="tok"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        sender = TerminalPushNotificationSender(client, store)
        await sender.send_notification(
            Task(id="t1", context_id="c1", status=TaskStatus(state=state)))
    return sent


@pytest.mark.parametrize("state", [
    TaskState.submitted, TaskState.working, TaskState.input_required, TaskState.auth_required])
async def test_a_non_terminal_task_is_not_pushed(state):
    assert await _sent_for(state) == []


@pytest.mark.parametrize("state", [
    TaskState.completed, TaskState.failed, TaskState.canceled, TaskState.rejected])
async def test_a_terminal_task_is_pushed_with_the_token(state):
    sent = await _sent_for(state)
    assert len(sent) == 1
    assert str(sent[0].url) == ALLOWED
    assert sent[0].headers["x-a2a-notification-token"] == "tok"


async def test_only_the_outcome_is_pushed_when_the_whole_stream_runs():
    sent = []

    def respond(request):
        sent.append(request)
        return httpx.Response(204)

    store = InMemoryPushNotificationConfigStore()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        executor = CountingExecutor()
        handler = AllowlistedRequestHandler(
            agent_executor=executor, task_store=InMemoryTaskStore(), push_config_store=store,
            push_sender=TerminalPushNotificationSender(client, store),
            push_url_allowlist=[ALLOWED])
        [e async for e in handler.on_message_send_stream(_params(ALLOWED, token="t"))]
    assert len(sent) == 1  # the completed snapshot; the submitted one was not pushed


async def test_push_config_get_list_and_delete_are_refused():
    handler = _handler([ALLOWED])
    for call, params in [
        (handler.on_get_task_push_notification_config, TaskIdParams(id="t1")),
        (handler.on_get_task_push_notification_config, GetTaskPushNotificationConfigParams(id="t1")),
        (handler.on_list_task_push_notification_config, ListTaskPushNotificationConfigParams(id="t1")),
        (handler.on_delete_task_push_notification_config,
         DeleteTaskPushNotificationConfigParams(id="t1", push_notification_config_id="x")),
    ]:
        with pytest.raises(PushConfigReadRefused):
            await call(params)


async def test_the_refusal_reaches_the_client_as_json_rpc_unsupported_operation():
    from a2a.types import (DeleteTaskPushNotificationConfigRequest,
                           GetTaskPushNotificationConfigRequest,
                           ListTaskPushNotificationConfigRequest)
    handler = _handler([ALLOWED])
    card = AgentCard(name="t", description="t", url="http://t", version="1",
                     capabilities=AgentCapabilities(streaming=True, push_notifications=True),
                     default_input_modes=["text"], default_output_modes=["text"], skills=[])
    rpc = JSONRPCHandler(card, handler)
    got = await rpc.get_push_notification_config(
        GetTaskPushNotificationConfigRequest(id="1", params=TaskIdParams(id="t1")))
    listed = await rpc.list_push_notification_config(
        ListTaskPushNotificationConfigRequest(id="2", params=ListTaskPushNotificationConfigParams(id="t1")))
    deleted = await rpc.delete_push_notification_config(
        DeleteTaskPushNotificationConfigRequest(
            id="3", params=DeleteTaskPushNotificationConfigParams(id="t1", push_notification_config_id="x")))
    assert [r.root.error.code for r in (got, listed, deleted)] == [-32004] * 3
