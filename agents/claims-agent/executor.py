"""A2A executor for claims-agent.

Every claim is an A2A task. If the settlement-agent settles it, the task
completes at once. If the platform routes the payment to a human, the task
moves to input-required and this execute() stays running, parked on an
in-process future, until the reviewer-ui resumes the same task id.

Why it must stay running (a2a-sdk 0.3.7, read from source, measured in
NOTES.md): tasks/resubscribe needs the task's event queue, and the SDK removes
that queue when execute() returns.

Why both status updates are final=False: the SDK's EventConsumer closes the root
queue immediately on a final update, which clears every resubscriber's queue
before it reads the event. The stream ends instead when this execute() returns
and the SDK closes the queue gracefully. Clients end on the task state.

The resume is never trusted. The outcome the customer sees is whatever the
ledger says: executed -> Settled, rejected -> Declined by a reviewer, anything
else -> the resume is refused and the task keeps waiting.

A refusal the settlement-agent could not record on the ledger
(`refusal_unrecorded`) is terminal here: there is no payment awaiting approval
for a reviewer to find, so parking the task would wait for nobody.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TaskState, TaskStatus, TaskStatusUpdateEvent, TextPart
from a2a.utils import new_task
from opentelemetry import context as otel_context
from opentelemetry import trace

from correlation import set_claim_id
from delegation import ALLOWED_AGENTS, A2ADelegate
from intake import parse_claim
from ledger import LedgerReader

logger = logging.getLogger(__name__)

AWAITING_APPROVAL = "awaiting_approval"
REFUSAL_UNRECORDED = "refusal_unrecorded"
UNROUTED_NARRATIVE = (
    "This claim could not be routed for review. Nothing was paid. "
    "Please contact Northstar so a person can look at it.")
FAILED_NARRATIVE = (
    "We could not process this claim. Nothing was paid. Please try again, or contact Northstar.")
OUTCOMES = {"executed": "approved", "rejected": "rejected"}
# What the customer is told once a person has decided. The settlement agent's
# own narrative was written before the decision ("I could not release this...")
# and would contradict the outcome, so the decision replaces it.
DECIDED_NARRATIVE = {
    "approved": "A person at Northstar reviewed this payment and approved it. The payment has been made.",
    "rejected": "A person at Northstar reviewed this payment and declined it. Nothing was paid.",
}


@dataclass
class _Waiting:
    reply: dict[str, Any]
    outcome: asyncio.Future
    published: asyncio.Event = field(default_factory=asyncio.Event)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClaimsAgentExecutor(AgentExecutor):
    def __init__(self, delegates: dict[str, A2ADelegate] | None = None,
                 ledger: LedgerReader | None = None):
        self._delegates = delegates if delegates is not None else {
            name: A2ADelegate(url, name) for name, url in ALLOWED_AGENTS.items()}
        self._ledger = ledger or LedgerReader()
        self._waiting: dict[str, _Waiting] = {}

    def _delegate(self, name: str) -> A2ADelegate:
        try:
            return self._delegates[name]
        except KeyError:
            raise RuntimeError(
                f"claims-agent is not permitted to delegate to {name!r}; "
                f"permitted: {sorted(self._delegates)}") from None

    async def execute(self, request_context: RequestContext, event_queue: EventQueue) -> None:
        task = request_context.current_task
        if task is not None and task.status.state == TaskState.input_required:
            await self._resume(task.id)
            return
        await self._claim(request_context, event_queue)

    async def _claim(self, request_context: RequestContext, event_queue: EventQueue) -> None:
        claim = parse_claim(request_context.get_user_input())
        # Every span from here on carries northstar.claim.id: set once, at
        # intake, and held for the whole claim (including the wait), so the
        # enqueue_event spans for the task and its status updates carry it too.
        token = otel_context.attach(set_claim_id(claim["claimId"]))
        try:
            await self._run_claim(claim, request_context, event_queue)
        finally:
            otel_context.detach(token)

    async def _run_claim(self, claim: dict[str, Any], request_context: RequestContext,
                         event_queue: EventQueue) -> None:
        task = new_task(request_context.message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        span = trace.get_current_span()
        span.set_attribute("northstar.a2a.task.id", task.id)

        try:
            settlement = await self._delegate("settlement-agent").send({
                "claimId": claim["claimId"], "amount": claim["estimatedAmount"],
                "currency": "EUR", "a2aTaskId": task.id})
        except Exception as exc:  # noqa: BLE001 -- reported on the task, with the real reason
            # On a stream the task already exists, so an exception that escaped
            # execute() would abort the HTTP response mid-body ("incomplete
            # chunked read") and the customer would see a dropped connection.
            # Say what happened on the task instead: `failed`, with the real
            # reason in `error` (the blocking message/send used to return it as
            # a JSON-RPC error).
            logger.exception("claim %s: settlement failed", claim["claimId"])
            span.set_attribute("northstar.a2a.task.state", TaskState.failed.value)
            failure = {
                "claimId": claim["claimId"], "policyNumber": claim["policyNumber"],
                "paymentId": None, "amount": claim["estimatedAmount"], "currency": "EUR",
                "status": "failed", "narrative": FAILED_NARRATIVE, "rule": None,
                "limit": None, "decision": None, "error": str(exc),
            }
            await updater.failed(updater.new_agent_message([Part(root=TextPart(text=json.dumps(failure)))]))
            return

        reply = {
            "claimId": claim["claimId"], "policyNumber": claim["policyNumber"],
            "paymentId": settlement.get("paymentId"), "amount": settlement.get("amount"),
            "currency": settlement.get("currency"), "status": settlement.get("status"),
            "narrative": settlement.get("narrative"), "rule": settlement.get("rule"),
            "limit": settlement.get("limit"), "decision": None,
        }
        if reply["status"] == REFUSAL_UNRECORDED:
            reply["narrative"] = UNROUTED_NARRATIVE
        if reply["status"] != AWAITING_APPROVAL:
            span.set_attribute("northstar.a2a.task.state", TaskState.completed.value)
            await updater.complete(updater.new_agent_message([Part(root=TextPart(text=json.dumps(reply)))]))
            return

        waiting = _Waiting(reply=reply, outcome=asyncio.get_running_loop().create_future())
        self._waiting[task.id] = waiting
        try:
            span.set_attribute("northstar.a2a.task.state", TaskState.input_required.value)
            await updater.update_status(
                TaskState.input_required,
                message=updater.new_agent_message([Part(root=TextPart(text=json.dumps(reply)))]),
                final=False)
            outcome, resume_context = await waiting.outcome
            # Publish under the resume's context (trace B), never trace A's, and
            # still carrying the claim id.
            token = otel_context.attach(set_claim_id(claim["claimId"], ctx=resume_context))
            try:
                await event_queue.enqueue_event(TaskStatusUpdateEvent(
                    task_id=task.id, context_id=task.context_id, final=False,
                    status=TaskStatus(
                        state=TaskState.completed, timestamp=_now(),
                        message=updater.new_agent_message(
                            [Part(root=TextPart(text=json.dumps(outcome)))]))))
            finally:
                otel_context.detach(token)
            waiting.published.set()
        finally:
            self._waiting.pop(task.id, None)

    async def _resume(self, task_id: str) -> None:
        waiting = self._waiting.get(task_id)
        if waiting is None:
            raise RuntimeError(
                f"task {task_id} is not waiting in this claims-agent process "
                "(it may have restarted); nothing was resumed")
        record = await self._ledger.get(waiting.reply["paymentId"])
        decision = OUTCOMES.get(record.get("status"))
        if decision is None:
            raise RuntimeError(
                f"task {task_id}: the ledger shows {record.get('status')!r} for "
                f"{waiting.reply['paymentId']}, so there is no human decision to report")
        trace.get_current_span().set_attribute("northstar.a2a.task.state", TaskState.completed.value)
        outcome = {**waiting.reply, "status": record["status"], "decision": decision,
                   "narrative": DECIDED_NARRATIVE[decision]}
        waiting.outcome.set_result((outcome, otel_context.get_current()))
        # The parked execute() publishes the completion and returns. Bounded, so
        # a parked task that died cannot hold the reviewer-ui's resume forever.
        await asyncio.wait_for(waiting.published.wait(), timeout=30)

    async def cancel(self, request_context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("claims-agent does not support cancellation")
