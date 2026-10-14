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
`status: "awaiting_approval"` carrying the model's explanation, not as a JSON-RPC error
envelope.

This image runs one Deployment only, settlement-agent, and receives one kind
of request: a claim to settle. A payment the agent may not release is left
`awaiting_approval`, and a human releases it elsewhere: the reviewer-ui calls
execute_payment through the gateway under its own identity. Nothing in this
image executes an approval.
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
        # opentelemetry.context, which the claims-agent executor needs to
        # import directly. Keeping the name consistent across agents avoids a
        # rename.
        payload = json.loads(request_context.get_user_input())

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
        # Not "Settled claim": this reply can say `awaiting_approval`, and a log line
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
                        "rule": decision["rule"],
                        "limit": decision["limit"],
                    }
                )
            )
        )

    async def cancel(
        self, request_context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise NotImplementedError("settlement-agent does not support cancellation")
