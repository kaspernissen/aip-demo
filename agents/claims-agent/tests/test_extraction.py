"""The intake model, with no API key and no network.

FunctionModel stands in for Anthropic and answers with the output tool the
agent offers it, exactly as a real model would end a structured run.
"""
from __future__ import annotations

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import extraction
from extraction import INTAKE_PROMPT_PREFIX, extract_draft, intake_agent


def _answering(draft: dict, seen: list | None = None) -> FunctionModel:
    def model(messages, info):
        if seen is not None:
            seen.append(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, draft)])
    return FunctionModel(model)


async def test_the_draft_comes_back_as_a_plain_dict_with_every_field():
    with intake_agent.override(model=_answering({"policyNumber": "POL-77120", "estimatedAmount": 5800})):
        draft = await extract_draft("CLM-INTAKE-ABC123", ["My car was hit"], None, "2026-10-23")
    assert draft["policyNumber"] == "POL-77120" and draft["estimatedAmount"] == 5800
    assert set(draft) == {"policyNumber", "description", "estimatedAmount", "currency",
                          "incidentDate", "missing", "question"}
    assert draft["missing"] == [] and draft["incidentDate"] is None


async def test_the_prompt_opens_with_the_prefix_the_evaluators_skip_and_carries_everything():
    seen: list = []
    previous = {"policyNumber": "POL-77120"}
    with intake_agent.override(model=_answering({}, seen)):
        await extract_draft("CLM-INTAKE-ABC123", ["first message", "second message"], previous,
                            "2026-10-23")
    prompt = seen[0][-1].parts[-1].content
    assert prompt.startswith(f"{INTAKE_PROMPT_PREFIX} for claim CLM-INTAKE-ABC123. Today is 2026-10-23.")
    assert '"policyNumber": "POL-77120"' in prompt
    assert prompt.index("1. first message") < prompt.index("2. second message")


async def test_the_claim_id_is_the_runs_conversation_id(monkeypatch):
    """As settlement-agent's: one claim, one AgentEvals session."""
    seen = {}
    original = type(intake_agent).run

    async def spy(self, *args, **kwargs):
        seen["conversation_id"] = kwargs.get("conversation_id")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(type(intake_agent), "run", spy)
    with intake_agent.override(model=_answering({})):
        await extract_draft("CLM-INTAKE-ABC123", ["x"], None, "2026-10-23")
    assert seen["conversation_id"] == "CLM-INTAKE-ABC123"


def test_the_agent_is_named_so_its_spans_say_claims_agent():
    assert intake_agent.name == "claims-agent"


def test_the_default_model_is_settlement_agents():
    # Not reloaded with the env var set: a reload would replace `intake_agent`
    # under every test that holds the old one, and an override on the old one
    # would then let a later test reach the real model.
    assert extraction.DEFAULT_MODEL == "anthropic:claude-sonnet-5"
