"""A2A executor for settlement-agent.

M1.1 has no LLM. The executor parses the incoming message, calls
decide_settlement, then drives payments-mcp: propose, then execute.

In M1.1 it executes unconditionally. That is the point: EUR 6,200 leaves the
company because a message asked for it. M2 puts a policy decision in the path.
"""
from __future__ import annotations

import json
import logging
import os

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message

from mcp_tools import PaymentsTools
from settlement import decide_settlement

logger = logging.getLogger(__name__)


class SettlementAgentExecutor(AgentExecutor):
    def __init__(self, payments: PaymentsTools | None = None):
        self._payments = payments or PaymentsTools(
            os.environ.get("PAYMENTS_MCP_URL", "http://payments-mcp:8000/mcp")
        )

    async def execute(
        self, request_context: RequestContext, event_queue: EventQueue
    ) -> None:
        # Named request_context, not context: `context` shadows
        # opentelemetry.context, which Task 7 needs in the claims-agent
        # executor. Keeping the name consistent across agents avoids a rename.
        claim = json.loads(request_context.get_user_input())

        decision = decide_settlement(claim)
        logger.info("Assessed settlement: %s", decision["narrative"])

        # propose() and execute() raise mcp_tools.ToolCallError (payments-api's
        # 404/409 surfaced as the tool's own error text) or ValueError on an
        # unhandled call shape. Left to propagate, the a2a-sdk's request
        # handler turns an unhandled exception from this method into a
        # JSON-RPC error response carrying str(exc) (verified empirically),
        # so the caller sees the real reason a settlement failed rather than
        # a flattened success or a swallowed exception.
        proposed = await self._payments.propose(
            claim["claimId"], decision["amount"], decision["currency"]
        )
        payment_id = proposed["paymentId"]
        logger.info("Proposed payment %s.", payment_id)

        executed = await self._payments.execute(payment_id)

        # Only one Message is ever enqueued, deliberately: verified
        # empirically (see NOTES.md) that a2a-sdk 0.3.7's blocking
        # `message/send` handler (ResultAggregator.consume_and_break_on_interrupt)
        # returns as soon as it sees the *first* Message-kind event and closes
        # the queue — any Message enqueued after that is silently dropped
        # ("Queue is closed. Event will not be enqueued."). The brief's
        # original sketch enqueued three messages assuming the caller would
        # see the last one; that assumption doesn't hold for message/send, so
        # the caller would have received only the intermediate "Assessed
        # settlement" text and never the promised JSON reply. Emitting a
        # single message here is what actually satisfies this agent's
        # documented interface: "replies with a JSON object {claimId,
        # paymentId, amount, status, narrative}".
        await event_queue.enqueue_event(
            new_agent_text_message(
                json.dumps(
                    {
                        "claimId": claim["claimId"],
                        "paymentId": payment_id,
                        "amount": executed["amount"],
                        "currency": executed["currency"],
                        "status": executed["status"],
                        "narrative": decision["narrative"],
                    }
                )
            )
        )

    async def cancel(
        self, request_context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise NotImplementedError("settlement-agent does not support cancellation in M1")
