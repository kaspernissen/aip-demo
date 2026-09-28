"""A2A executor for settlement-agent.

decide_settlement is a real PydanticAI agent: it decides whether and how to
pay, and drives payments-mcp itself (propose, then execute). The executor's
job is just intake and reply — parse the incoming message, call
decide_settlement once, and report what the ledger recorded.

There is no policy check in this file, and none in settlement.py either: left
unchecked, the model could choose to pay out any amount simply because a
message asked for it. That check lives deliberately somewhere this code
cannot reach around -- the gateway's policy hook, deciding on the
`tools/call` request itself. A refused settlement comes back as a reply with
`status: "refused"` carrying the model's explanation, not as a JSON-RPC error
envelope.

This same executor, in this same image, also runs as
senior-settlement-agent -- a second Deployment, its own ServiceAccount, its
own SPIFFE ID, nothing else different. So the one thing this file's `execute`
method has to do is tell the two requests it can receive apart: a claim to
settle (the shape above, routed to decide_settlement) and a human's approval
of a payment already refused (routed to execute_approved). The two are
distinguished structurally, by the presence of an `approval` key -- never by
which workload is running, since both workloads run this exact code.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message

from mcp_tools import PaymentsTools
from settlement import decide_settlement, execute_approved

logger = logging.getLogger(__name__)


def _validate_approval(
    payload: dict[str, Any],
) -> tuple[str, float, str, dict[str, Any], str, str]:
    """Read an approval payload, or fail loudly.

    Malformed approval payloads must never default to executing -- a missing
    field is a `ValueError`, exactly the failure shape `decide_settlement`'s
    own `_validate` already gives a malformed claim, and a2a-sdk turns an
    unhandled exception here into a JSON-RPC error rather than a flattened
    success.
    """
    payment_id = payload.get("paymentId")
    if not payment_id:
        raise ValueError("paymentId is required")

    amount = payload.get("amount")
    if amount is None:
        raise ValueError("amount is required")

    currency = payload.get("currency")
    if not currency:
        raise ValueError("currency is required")

    approval = payload.get("approval")
    if not isinstance(approval, dict) or not approval.get("reviewerId"):
        raise ValueError("approval.reviewerId is required")

    # The refusal's trace/span ids are optional on the wire (an approval for
    # a refusal recorded without a `refusedSpanId`, say) --
    # `execute_approved`/`_refusal_link` already treat an empty pair as "no
    # link to draw", not as malformed.
    refused_trace_id = payload.get("refusedTraceId") or ""
    refused_span_id = payload.get("refusedSpanId") or ""

    return payment_id, float(amount), currency, approval, refused_trace_id, refused_span_id


class SettlementAgentExecutor(AgentExecutor):
    def __init__(self, payments: PaymentsTools | None = None):
        self._payments = payments or PaymentsTools(
            os.environ.get("PAYMENTS_MCP_URL", "http://payments-mcp:8000/mcp")
        )

    async def execute(
        self, request_context: RequestContext, event_queue: EventQueue
    ) -> None:
        # Named request_context, not context: `context` shadows
        # opentelemetry.context, which the claims-agent executor needs to
        # import directly. Keeping the name consistent across agents avoids a
        # rename.
        payload = json.loads(request_context.get_user_input())

        if "approval" in payload:
            await self._execute_approved(payload, event_queue)
            return

        claim = payload

        # decide_settlement raises mcp_tools.ToolCallError (payments-api's
        # 404/409 surfaced as the tool's own error text) or ValueError on a
        # malformed claim. A policy refusal is deliberately NOT among them --
        # it comes back as a reply, because the claimant is owed an
        # explanation rather than a stack trace, and because the platform
        # refusing is the platform working.
        #
        # Left to propagate, the a2a-sdk's request handler
        # turns an unhandled exception from this method into a JSON-RPC error
        # response carrying str(exc) (verified empirically), so the caller
        # sees the real reason a settlement failed rather than a flattened
        # success or a swallowed exception.
        decision = await decide_settlement(claim, self._payments)
        # Not "Settled claim": this reply can say `refused`, and a log line
        # that calls a refusal a settlement would misrepresent exactly what
        # happened.
        logger.info(
            "Claim %s: %s -- %s",
            claim["claimId"],
            decision["status"],
            decision["narrative"],
        )

        # Only one Message is ever enqueued, deliberately: verified
        # empirically (see NOTES.md) that a2a-sdk 0.3.7's blocking
        # `message/send` handler (ResultAggregator.consume_and_break_on_interrupt)
        # returns as soon as it sees the *first* Message-kind event and closes
        # the queue — any Message enqueued after that is silently dropped
        # ("Queue is closed. Event will not be enqueued."). Enqueuing more than
        # one message here would mean the caller only ever sees the first of
        # them; emitting a single message is what actually satisfies this
        # agent's documented interface: "replies with a JSON object {claimId,
        # paymentId, amount, status, narrative}".
        await event_queue.enqueue_event(
            new_agent_text_message(
                json.dumps(
                    {
                        "claimId": claim["claimId"],
                        "paymentId": decision["paymentId"],
                        "amount": decision["amount"],
                        "currency": decision["currency"],
                        "status": decision["status"],
                        "narrative": decision["narrative"],
                    }
                )
            )
        )

    async def _execute_approved(
        self, payload: dict, event_queue: EventQueue
    ) -> None:
        """The approval path: a human reviewed the refused run, and this
        carries their instruction to the payment system. No model call --
        `execute_approved` makes none -- and no propose: the proposal is what
        was reviewed.

        `_validate_approval` raises `ValueError` on anything malformed, before
        any payment call, the same "fail loudly, never default to executing"
        contract `decide_settlement`'s own `_validate` holds for a claim.
        """
        payment_id, amount, currency, approval, refused_trace_id, refused_span_id = (
            _validate_approval(payload)
        )

        result = await execute_approved(
            payment_id, amount, currency, approval, self._payments,
            refused_trace_id, refused_span_id,
        )
        logger.info(
            "payment %s: approved by %s -- %s",
            payment_id, approval["reviewerId"], result.get("status"),
        )

        # Same single-message discipline as the claim path above: a2a-sdk's
        # blocking message/send handler returns on the first Message-kind
        # event and drops anything enqueued after it.
        await event_queue.enqueue_event(
            new_agent_text_message(
                json.dumps(
                    {
                        "paymentId": result["paymentId"],
                        "amount": result["amount"],
                        "currency": result["currency"],
                        "status": result["status"],
                        "reviewerId": approval["reviewerId"],
                    }
                )
            )
        )

    async def cancel(
        self, request_context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise NotImplementedError("settlement-agent does not support cancellation")
