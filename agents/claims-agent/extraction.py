"""The intake model: a customer's first notice of loss, in their own words,
read into a draft claim.

The model reads; it decides nothing. What it returns is a DRAFT that
intake.check_draft validates exactly as strictly as a JSON claim, and it is
that check, not the model's own `missing` list, that decides whether the claim
goes to settlement-agent or the customer is asked for more. The model's one
other contribution is the wording of that question.

Built like settlement-agent's agent (agents/settlement-agent/settlement.py):
the model name comes from the environment with the same default, model
resolution is deferred so the token-free suite can import this module without
ANTHROPIC_API_KEY, and the instrumentation is bootstrap.py's process-wide
`Agent.instrument_all(...)` with the same settings as settlement-agent's
(`Agent(instrument=...)` does not exist at pydantic-ai-slim 2.42.0). `name` is
set explicitly, so the invoke_agent and chat spans say
`gen_ai.agent.name = claims-agent` rather than an inferred variable name.

The user prompt starts with INTAKE_PROMPT_PREFIX on purpose: the run's
conversation id is the claim id, so AgentEvals files this run in the same
session as the settlement's (one claim, one session), and its code evaluators
skip invocations that open with that prefix rather than judge an intake as a
settlement (deploy/observability/agentevals/evaluators/).
"""
from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from model_client import gateway_model

DEFAULT_MODEL = "anthropic:claude-sonnet-5"
INTAKE_PROMPT_PREFIX = "First notice of loss"


class IntakeDraft(BaseModel):
    """What the model may say about a claim. Every field may be absent: a
    customer who did not say it has not said it, and guessing is the failure
    this shape exists to make unnecessary."""

    policyNumber: str | None = Field(None, description="The policy number exactly as the customer gave it, e.g. POL-77120.")
    description: str | None = Field(None, description="What happened, in one or two plain sentences.")
    estimatedAmount: float | None = Field(None, description="The customer's estimate of the damage, as a number. No currency sign.")
    currency: str | None = Field(None, description="ISO 4217 code of that amount, e.g. EUR. Null if the customer gave no amount.")
    incidentDate: str | None = Field(None, description="The date of the incident as YYYY-MM-DD, only if the customer stated it or it follows from what they said and today's date.")
    missing: list[str] = Field(default_factory=list, description="The fields above the customer has not given yet.")
    question: str | None = Field(None, description="ONE short, friendly question asking for what is missing. Null if nothing is missing.")


intake_agent = Agent(
    gateway_model(os.environ.get("NORTHSTAR_CLAIMS_MODEL", DEFAULT_MODEL)),
    name="claims-agent",
    output_type=IntakeDraft,
    # See settlement.py: without this, constructing the Agent at import time
    # can require ANTHROPIC_API_KEY, and the unit suite imports this module
    # under `intake_agent.override(model=...)` only.
    defer_model_check=True,
    instructions=(
        "You take first notices of loss for motor insurance claims at Northstar Insurance.\n"
        "Read what the customer wrote and fill in the claim fields you can.\n"
        "Never invent a value. A field the customer has not given is null and is listed in missing.\n"
        "Northstar settles in euro. If the customer gave an amount in another currency, "
        "keep their currency and list estimatedAmount as missing.\n"
        "If something is missing, write ONE short, friendly question that asks for it. "
        "Ask for at most two things in that question.\n"
        "You do not decide whether the claim is covered or paid, and you never promise a payment."
    ),
)


def _prompt(claim_id: str, customer_text: list[str], previous: dict[str, Any] | None, today: str) -> str:
    lines = [f"{INTAKE_PROMPT_PREFIX} for claim {claim_id}. Today is {today}."]
    if previous:
        lines.append("The draft so far: " + json.dumps(previous, sort_keys=True))
    lines.append("What the customer has written, oldest first:")
    lines += [f"{n}. {text}" for n, text in enumerate(customer_text, start=1)]
    return "\n".join(lines)


async def extract_draft(claim_id: str, customer_text: list[str], previous: dict[str, Any] | None,
                        today: str) -> dict[str, Any]:
    """One model run over everything the customer has said. Returns the draft
    as a plain dict, for intake.check_draft to judge.

    Re-extracts from all of the customer's messages each round, with the
    previous draft beside them, rather than patching the draft with the newest
    reply: a reply like "sorry, it was the 12th, not the 11th" corrects an
    earlier answer, and only a reading of the whole exchange sees that.
    """
    result = await intake_agent.run(
        _prompt(claim_id, customer_text, previous, today),
        # The claim id is the run's conversation id, as in settlement.py:
        # PydanticAI writes it as `gen_ai.conversation.id` on the invoke_agent
        # span. One claim, one session, intake and settlement alike.
        conversation_id=claim_id,
    )
    return result.output.model_dump()
