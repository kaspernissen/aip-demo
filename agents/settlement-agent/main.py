import bootstrap  # noqa: F401  configures OTel before the A2A SDK is imported

import json
import os
from pathlib import Path

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard
from opentelemetry.instrumentation.starlette import StarletteInstrumentor

from executor import SettlementAgentExecutor

CARD_PATH = Path(os.environ.get("AGENT_CARD_PATH", "/app/agent-card.json"))


def load_agent_card() -> AgentCard:
    return AgentCard.model_validate(json.loads(CARD_PATH.read_text()))


def build_app():
    handler = DefaultRequestHandler(
        agent_executor=SettlementAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    app = A2AStarletteApplication(
        agent_card=load_agent_card(), http_handler=handler
    ).build()
    # A18: a2a-sdk 0.3.7 never extracts inbound W3C trace context itself
    # (verified in NOTES.md) — StarletteInstrumentor is the OTel project's
    # own ASGI-level instrumentation, applied per-app since the Starlette
    # instance doesn't exist until `.build()` returns. It extracts the
    # incoming traceparent/baggage (via the global propagator set in
    # bootstrap.py) and starts a real server span from it, which a2a-sdk's
    # own spans then nest under via the ordinary ambient-context mechanism.
    StarletteInstrumentor().instrument_app(app)
    return app


if __name__ == "__main__":
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "9002")))
