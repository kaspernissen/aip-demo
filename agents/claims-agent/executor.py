"""A2A executor for claims-agent.

Every claim is an A2A task. If the settlement-agent settles it, the task
completes at once. If the platform routes the payment to a human, the task
moves to input-required and execute() RETURNS. Nothing in this process waits
for the human.

Why it returns (M10): a parked execute() made a2a-sdk 0.3.7 add an
`EventQueue.dequeue_event` span every 0.5 s to the agent's trace for the whole
wait. Returning ends trace A at the hand-off, with no polling. The reviewer-ui's
resume is a `message/send` on the same task id; the SDK starts a fresh
execute() for it, in the reviewer's trace (B). The parked span's context is
stored in the task's metadata (`northstar.a2a.parked_span_context`) and the
resume span carries it as a link, so B points back at A.

The resume is never trusted, and it carries no state: the reply to report is
read from the task's metadata (`northstar.a2a.parked_reply`, written by this agent), and the outcome the customer sees is whatever the
ledger says: executed -> Settled, rejected -> Declined by a reviewer, anything
else -> the resume is refused (execute() raises) and the task stays
input-required, able to take a later, real decision.

The input-required update is final=False and the stream ends when execute()
returns and the SDK closes the queue gracefully; clients end on the task state.
The completion uses the updater's default (final=True): with no resubscriber
left, nothing taps the queue that a final update closes (measured, 0.3.7).

A refusal the settlement-agent could not record on the ledger
(`refusal_unrecorded`) is terminal here: there is no payment awaiting approval
for a reviewer to find, so parking the task would wait for nobody.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Role, Task, TaskState, TextPart
from a2a.utils import new_task
from opentelemetry import context as otel_context
from opentelemetry import trace

from correlation import set_claim_id
from delegation import ALLOWED_AGENTS, A2ADelegate
from intake import parse_claim
from ledger import LedgerReader

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("claims-agent")

PARKED_REPLY_KEY = "northstar.a2a.parked_reply"
PARKED_CONTEXT_KEY = "northstar.a2a.parked_span_context"
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


def _span_context_dict(ctx: trace.SpanContext) -> dict[str, Any]:
    return {"trace_id": format(ctx.trace_id, "032x"), "span_id": format(ctx.span_id, "016x"),
            "trace_flags": int(ctx.trace_flags)}


def _parked_link(task: Task) -> trace.Link | None:
    raw = (task.metadata or {}).get(PARKED_CONTEXT_KEY)
    try:
        ctx = trace.SpanContext(trace_id=int(raw["trace_id"], 16), span_id=int(raw["span_id"], 16),
                                is_remote=True, trace_flags=trace.TraceFlags(int(raw["trace_flags"])))
    except (TypeError, KeyError, ValueError):
        logger.warning("task %s: no usable parked span context (%r); resuming without a link", task.id, raw)
        return None
    if not ctx.is_valid:
        logger.warning("task %s: parked span context is not valid; resuming without a link", task.id)
        return None
    return trace.Link(ctx, {"northstar.a2a.link.reason": "resume"})


def _waiting_reply(task: Task) -> dict[str, Any]:
    """The reply the task parked with, read ONLY from the task's metadata.

    Only an agent-side status update writes `task.metadata` (a2a-sdk 0.3.7
    task_manager.py), so a client cannot forge it. The task's history is not
    safe to read: the SDK appends every incoming message to it without looking
    at its role, and a refused message stays there.
    """
    reply = (task.metadata or {}).get(PARKED_REPLY_KEY)
    if not isinstance(reply, dict) or not reply.get("claimId") or not reply.get("paymentId"):
        raise RuntimeError(f"task {task.id} is input-required but carries no parked reply to resume")
    return reply


class ClaimsAgentExecutor(AgentExecutor):
    def __init__(self, delegates: dict[str, A2ADelegate] | None = None,
                 ledger: LedgerReader | None = None):
        self._delegates = delegates if delegates is not None else {
            name: A2ADelegate(url, name) for name, url in ALLOWED_AGENTS.items()}
        self._ledger = ledger or LedgerReader()

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
            # A resume is a person's message. The SDK files an agent-role message
            # the same way, so refuse it here; the task keeps waiting.
            if request_context.message is None or request_context.message.role != Role.user:
                raise RuntimeError(f"task {task.id}: a resume must be a user message; refused")
            await self._resume(task, event_queue)
            return
        await self._claim(request_context, event_queue)

    async def _claim(self, request_context: RequestContext, event_queue: EventQueue) -> None:
        claim = parse_claim(request_context.get_user_input())
        # Every span from here on carries northstar.claim.id: set once, at
        # intake, and held for the whole of this execute(), so the enqueue_event
        # spans for the task and its status updates carry it too.
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

        span.set_attribute("northstar.a2a.task.state", TaskState.input_required.value)
        await updater.update_status(
            TaskState.input_required,
            message=updater.new_agent_message([Part(root=TextPart(text=json.dumps(reply)))]),
            final=False,
            metadata={PARKED_CONTEXT_KEY: _span_context_dict(span.get_span_context()),
                      PARKED_REPLY_KEY: reply})
        # execute() returns here. Trace A ends at the hand-off; the resume is a
        # new execute() in the reviewer's trace (B), linked back to this span.

    async def _resume(self, task: Task, event_queue: EventQueue) -> None:
        reply = _waiting_reply(task)
        link = _parked_link(task)
        token = otel_context.attach(set_claim_id(reply["claimId"]))
        try:
            with _tracer.start_as_current_span(
                    "northstar.a2a.resume", links=[link] if link else []) as span:
                span.set_attribute("northstar.a2a.task.id", task.id)
                record = await self._ledger.get(reply["paymentId"])
                decision = OUTCOMES.get(record.get("status"))
                if decision is None:
                    raise RuntimeError(
                        f"task {task.id}: the ledger shows {record.get('status')!r} for "
                        f"{reply['paymentId']}, so there is no human decision to report")
                span.set_attribute("northstar.a2a.task.state", TaskState.completed.value)
                outcome = {**reply, "status": record["status"], "decision": decision,
                           "narrative": DECIDED_NARRATIVE[decision]}
                updater = TaskUpdater(event_queue, task.id, task.context_id)
                await updater.complete(updater.new_agent_message([Part(root=TextPart(text=json.dumps(outcome)))]))
        finally:
            otel_context.detach(token)

    async def cancel(self, request_context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("claims-agent does not support cancellation")
