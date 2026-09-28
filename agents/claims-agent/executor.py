"""A2A executor for claims-agent.

Delegates every claim directly to settlement-agent; this executor does not
itself implement policy or assessment routing.
"""
from __future__ import annotations

import json
import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message

from correlation import set_claim_id
from delegation import ALLOWED_AGENTS, A2ADelegate
from intake import parse_claim

logger = logging.getLogger(__name__)


class ClaimsAgentExecutor(AgentExecutor):
    def __init__(self, delegates: dict[str, A2ADelegate] | None = None):
        if delegates is not None:
            self._delegates = delegates
        else:
            self._delegates = {
                name: A2ADelegate(url, name) for name, url in ALLOWED_AGENTS.items()
            }

    def _delegate(self, name: str) -> A2ADelegate:
        try:
            return self._delegates[name]
        except KeyError:
            raise RuntimeError(
                f"claims-agent is not permitted to delegate to {name!r}; "
                f"permitted: {sorted(self._delegates)}"
            ) from None

    async def execute(
        self, request_context: RequestContext, event_queue: EventQueue
    ) -> None:
        # Named request_context, not context: `context` shadows
        # opentelemetry.context, imported here.
        from opentelemetry import context as otel_context

        claim = parse_claim(request_context.get_user_input())

        # Every span from here on carries northstar.claim.id: the attribute
        # is set once, here, at intake, and rides in baggage from this point
        # on. No downstream function takes a claim id parameter purely for
        # telemetry.
        token = otel_context.attach(set_claim_id(claim["claimId"]))
        try:
            # a2a-sdk 0.3.7's blocking message/send handler
            # (ResultAggregator.consume_and_break_on_interrupt) returns as
            # soon as it sees the FIRST Message-kind event enqueued and
            # closes the queue — anything enqueued after is silently dropped
            # ("Queue is closed. Event will not be enqueued."). Enqueuing a
            # "received" narration as an event before the final JSON result
            # would mean the caller only ever sees "Claim ... received.
            # Investigating." and never the settlement JSON this agent's
            # documented interface promises (same class of issue as
            # settlement-agent's; see agents/settlement-agent/NOTES.md).
            # Log the narration instead of enqueuing it, and enqueue exactly
            # one Message: the final JSON result. Regression test:
            # tests/e2e/test_claims_chain_trace.py::
            # test_message_send_returns_the_settlement_json.
            logger.info("Claim %s received. Investigating.", claim["claimId"])

            # settlement-agent's own executor lets a failed MCP tool call
            # (payments-api's 404/409, surfaced as the tool's own error text)
            # propagate out of AgentExecutor.execute() rather than
            # swallowing it, and a2a-sdk's request handler turns an
            # unhandled exception into a JSON-RPC error response carrying
            # str(exc). A2ADelegate.send() raises RuntimeError with
            # settlement-agent's own error message on a JSON-RPC error
            # reply; left to propagate here, the same mechanism surfaces
            # that real reason to claims-agent's own caller instead of
            # flattening it into a success or swallowing it.
            settlement = await self._delegate("settlement-agent").send(
                {
                    "claimId": claim["claimId"],
                    "amount": claim["estimatedAmount"],
                    "currency": "EUR",
                }
            )

            await event_queue.enqueue_event(
                new_agent_text_message(
                    json.dumps(
                        {
                            "claimId": claim["claimId"],
                            "policyNumber": claim["policyNumber"],
                            "paymentId": settlement.get("paymentId"),
                            "amount": settlement.get("amount"),
                            "currency": settlement.get("currency"),
                            "status": settlement.get("status"),
                            "narrative": settlement.get("narrative"),
                        }
                    )
                )
            )
        finally:
            otel_context.detach(token)

    async def cancel(
        self, request_context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise NotImplementedError("claims-agent does not support cancellation")
