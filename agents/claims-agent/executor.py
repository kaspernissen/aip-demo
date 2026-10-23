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

M13: the intake, and a second reason to wait. A claim that arrives as a JSON
object takes the path above, unchanged and with no model. Anything else is a
customer's first notice of loss in their own words: extraction.py's model
reads it into a draft, intake.check_draft judges the draft as strictly as a
JSON claim, and a draft that is not yet a claim parks the task at
input-required with the customer's question as the agent message. The
customer's reply is a user message on the same task, a fresh execute() that
re-reads everything they have said, and either delegates or asks again (at
most MAX_QUESTIONS times, then the task fails, politely). Plain REST has no
"this request is waiting for you to say more" state; A2A's input-required is
exactly that, in the same task the customer is already holding.

Which wait a resume ends is read ONLY from `northstar.a2a.parked_reason`,
which this agent writes on the status update that parks the task (and which,
as above, a client cannot write). Never from the message: a customer's reply
cannot reach the ledger read in `_resume`, and a reviewer's resume cannot
reach the model. A task parked before the key existed carries none, and is
the approval wait it always was. An unknown reason is refused, and the task
keeps waiting. The task metadata is merged, never replaced, so the approval
park writes its reason explicitly: a claim that waited for information and
then for a person must not still read `needs_information`.

The information wait follows the M10 rules: execute() returns when it parks,
and the resume span (`northstar.a2a.resume`, in the customer's reply trace)
links back to the span that parked it.
"""
from __future__ import annotations

import datetime as dt
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
from extraction import extract_draft
from intake import (DRAFT_FIELDS, FALLBACK_QUESTIONS, MAX_DESCRIPTION, SETTLEMENT_CURRENCY,
                    check_draft, is_json_object, new_intake_claim_id, parse_claim)
from ledger import LedgerReader

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("claims-agent")

PARKED_REPLY_KEY = "northstar.a2a.parked_reply"
PARKED_CONTEXT_KEY = "northstar.a2a.parked_span_context"
PARKED_REASON_KEY = "northstar.a2a.parked_reason"
INTAKE_STATE_KEY = "northstar.a2a.intake_state"
AWAITING_APPROVAL = "awaiting_approval"
NEEDS_INFORMATION = "needs_information"
PARKED_REASONS = frozenset({AWAITING_APPROVAL, NEEDS_INFORMATION})
# The span attribute that says which wait a span parked or resumed
# (telemetry/registry/claim.yaml). Same values as the metadata key.
PARKED_REASON_ATTRIBUTE = "northstar.a2a.parked_reason"
# Questions the agent may ask before it gives up on a claim. Three is enough
# for a customer who forgot the policy number, the date and the amount one at a
# time, and few enough that a model which keeps asking cannot hold a task open.
MAX_QUESTIONS = 3
# Bounds on what is copied into task metadata per customer message, and on the
# question the model writes. Metadata lives in memory for the task's life.
MAX_CUSTOMER_MESSAGE = 2000
MAX_QUESTION = 300
INTAKE_FAILED_NARRATIVE = (
    "We could not take this claim from your description. Nothing was paid. "
    "Please try again, or contact Northstar.")
GAVE_UP_NARRATIVE = (
    "We still do not have everything we need, so this claim has not been filed and nothing was "
    "paid. Please contact Northstar, or start again with your policy number, the date, what "
    "happened and an estimate in euro.")
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


def _parked_reason(task: Task) -> str:
    """Which wait this task is in, read ONLY from the metadata this agent wrote.

    No key is the approval wait: every task parked before M13 is one. A key
    with any other value is refused rather than guessed at.
    """
    reason = (task.metadata or {}).get(PARKED_REASON_KEY, AWAITING_APPROVAL)
    if reason not in PARKED_REASONS:
        raise RuntimeError(f"task {task.id}: parked for an unknown reason {reason!r}; refused")
    return reason


def _intake_state(task: Task) -> dict[str, Any]:
    """The intake so far, read ONLY from task metadata, like `_waiting_reply`.

    Not from the task's history: the customer's earlier messages are there too,
    but so is anything else a client sent, of any role. This is the agent's
    own copy of what it read.
    """
    state = (task.metadata or {}).get(INTAKE_STATE_KEY)
    if (not isinstance(state, dict) or not state.get("claimId")
            or not isinstance(state.get("customerText"), list)
            or not isinstance(state.get("questions"), int)):
        raise RuntimeError(f"task {task.id} is waiting for information but carries no intake state")
    return state


def _today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _kept_draft(draft: dict[str, Any]) -> dict[str, Any]:
    """The part of a model's draft worth carrying to the next round: the claim
    fields, bounded. Not its `missing` or `question`, which are re-asked."""
    kept: dict[str, Any] = {}
    for key in (*DRAFT_FIELDS, "currency"):
        value = draft.get(key)
        kept[key] = value[:MAX_DESCRIPTION] if isinstance(value, str) else value
    return kept


def _question(draft: dict[str, Any], missing: list[str]) -> str:
    """The model's question, when it asked about something that is really
    missing; otherwise the code's own question for the first missing field.

    intake.check_draft decides WHAT is missing. The model only words the
    question, and only gets to when its own idea of what is missing overlaps
    the code's: a model that says "nothing is missing" about a draft with no
    policy number does not get to send an empty or irrelevant question.
    """
    asked = draft.get("question")
    said_missing = set(draft.get("missing") or [])
    if isinstance(asked, str) and asked.strip() and said_missing & set(missing):
        return asked.strip()[:MAX_QUESTION]
    return FALLBACK_QUESTIONS[missing[0]]


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
            # The path is the parked reason's, never the message's: nothing a
            # client sends can turn a customer's reply into an approval resume.
            if _parked_reason(task) == NEEDS_INFORMATION:
                await self._continue_intake(task, request_context, event_queue)
                return
            await self._resume(task, event_queue)
            return
        await self._claim(request_context, event_queue)

    async def _claim(self, request_context: RequestContext, event_queue: EventQueue) -> None:
        text = request_context.get_user_input()
        if not is_json_object(text):
            await self._intake(text, request_context, event_queue)
            return
        claim = parse_claim(text)
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
        task, updater, span = await self._open_task(request_context, event_queue)
        await self._settle(claim, task, updater, span)

    async def _open_task(self, request_context: RequestContext,
                         event_queue: EventQueue) -> tuple[Task, TaskUpdater, trace.Span]:
        task = new_task(request_context.message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        span = trace.get_current_span()
        span.set_attribute("northstar.a2a.task.id", task.id)
        return task, updater, span

    async def _settle(self, claim: dict[str, Any], task: Task, updater: TaskUpdater,
                      span: trace.Span) -> None:
        try:
            # `description` rides along for M13's assessing agent; settlement-agent's
            # _validate ignores keys it does not read (its tests hold that).
            settlement = await self._delegate("settlement-agent").send({
                "claimId": claim["claimId"], "amount": claim["estimatedAmount"],
                "currency": "EUR", "a2aTaskId": task.id, "description": claim["description"]})
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
        span.set_attribute(PARKED_REASON_ATTRIBUTE, AWAITING_APPROVAL)
        await updater.update_status(
            TaskState.input_required,
            message=updater.new_agent_message([Part(root=TextPart(text=json.dumps(reply)))]),
            final=False,
            # The reason is written explicitly, not left to "no key means
            # approval": metadata merges, and a task that waited for
            # information first already carries `needs_information`.
            metadata={PARKED_CONTEXT_KEY: _span_context_dict(span.get_span_context()),
                      PARKED_REPLY_KEY: reply, PARKED_REASON_KEY: AWAITING_APPROVAL})
        # execute() returns here. Trace A ends at the hand-off; the resume is a
        # new execute() in the reviewer's trace (B), linked back to this span.

    async def _intake(self, text: str, request_context: RequestContext,
                      event_queue: EventQueue) -> None:
        """A claim in the customer's own words: the first round of the intake."""
        claim_id = new_intake_claim_id()
        # Known before the model runs, so the invoke_agent and chat spans carry
        # northstar.claim.id like every other span of this claim.
        token = otel_context.attach(set_claim_id(claim_id))
        try:
            task, updater, span = await self._open_task(request_context, event_queue)
            state = {"claimId": claim_id, "customerText": [text[:MAX_CUSTOMER_MESSAGE]],
                     "draft": None, "questions": 0}
            await self._take_intake(state, task, updater, span)
        finally:
            otel_context.detach(token)

    async def _continue_intake(self, task: Task, request_context: RequestContext,
                               event_queue: EventQueue) -> None:
        """The customer answered the question: re-read everything they said."""
        state = _intake_state(task)
        link = _parked_link(task)
        token = otel_context.attach(set_claim_id(state["claimId"]))
        try:
            with _tracer.start_as_current_span(
                    "northstar.a2a.resume", links=[link] if link else []) as span:
                span.set_attribute("northstar.a2a.task.id", task.id)
                span.set_attribute(PARKED_REASON_ATTRIBUTE, NEEDS_INFORMATION)
                reply = request_context.get_user_input()[:MAX_CUSTOMER_MESSAGE]
                state = {**state, "customerText": [*state["customerText"], reply]}
                updater = TaskUpdater(event_queue, task.id, task.context_id)
                await self._take_intake(state, task, updater, span)
        finally:
            otel_context.detach(token)

    async def _take_intake(self, state: dict[str, Any], task: Task, updater: TaskUpdater,
                           span: trace.Span) -> None:
        """One round: read, judge, then settle, ask, or give up."""
        claim_id = state["claimId"]
        today = _today()
        try:
            draft = await extract_draft(claim_id, state["customerText"], state["draft"],
                                        today.isoformat())
        except Exception as exc:  # noqa: BLE001 -- reported on the task, with the real reason
            # As for a settlement failure: an exception out of execute() would
            # drop the customer's stream mid-body. Fail the task and say why.
            logger.exception("claim %s: the intake model failed", claim_id)
            await self._fail_intake(claim_id, state.get("draft"), updater, span,
                                    INTAKE_FAILED_NARRATIVE, str(exc))
            return

        claim, missing = check_draft(claim_id, draft, today)
        if claim is not None:
            logger.info("claim %s: intake complete after %d question(s)", claim_id, state["questions"])
            await self._settle(claim, task, updater, span)
            return

        if state["questions"] >= MAX_QUESTIONS:
            logger.info("claim %s: still missing %s after %d questions; giving up",
                        claim_id, missing, state["questions"])
            await self._fail_intake(claim_id, draft, updater, span, GAVE_UP_NARRATIVE,
                                    f"still missing {', '.join(missing)} after {MAX_QUESTIONS} questions")
            return

        question = _question(draft, missing)
        kept = _kept_draft(draft)
        reply = {
            "claimId": claim_id, "policyNumber": kept.get("policyNumber"), "paymentId": None,
            "amount": None, "currency": SETTLEMENT_CURRENCY, "status": NEEDS_INFORMATION,
            "narrative": question, "question": question, "missing": missing,
            "rule": None, "limit": None, "decision": None,
        }
        logger.info("claim %s: missing %s; asking the customer", claim_id, missing)
        span.set_attribute("northstar.a2a.task.state", TaskState.input_required.value)
        span.set_attribute(PARKED_REASON_ATTRIBUTE, NEEDS_INFORMATION)
        await updater.update_status(
            TaskState.input_required,
            # The question as plain text first, for any A2A client; the reply
            # JSON last, where customer-ui and delegation.py look for it.
            message=updater.new_agent_message([Part(root=TextPart(text=question)),
                                               Part(root=TextPart(text=json.dumps(reply)))]),
            final=False,
            metadata={PARKED_REASON_KEY: NEEDS_INFORMATION,
                      PARKED_CONTEXT_KEY: _span_context_dict(span.get_span_context()),
                      INTAKE_STATE_KEY: {**state, "draft": kept, "questions": state["questions"] + 1}})
        # execute() returns here, as for the approval wait. The customer's reply
        # is a new execute(), in its own trace, linked back to this span.

    async def _fail_intake(self, claim_id: str, draft: dict[str, Any] | None, updater: TaskUpdater,
                           span: trace.Span, narrative: str, error: str) -> None:
        span.set_attribute("northstar.a2a.task.state", TaskState.failed.value)
        failure = {
            "claimId": claim_id, "policyNumber": (draft or {}).get("policyNumber"),
            "paymentId": None, "amount": None, "currency": SETTLEMENT_CURRENCY,
            "status": "failed", "narrative": narrative, "rule": None,
            "limit": None, "decision": None, "error": error,
        }
        await updater.failed(updater.new_agent_message([Part(root=TextPart(text=json.dumps(failure)))]))

    async def _resume(self, task: Task, event_queue: EventQueue) -> None:
        reply = _waiting_reply(task)
        link = _parked_link(task)
        token = otel_context.attach(set_claim_id(reply["claimId"]))
        try:
            with _tracer.start_as_current_span(
                    "northstar.a2a.resume", links=[link] if link else []) as span:
                span.set_attribute("northstar.a2a.task.id", task.id)
                span.set_attribute(PARKED_REASON_ATTRIBUTE, AWAITING_APPROVAL)
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
