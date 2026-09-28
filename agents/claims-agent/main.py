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
from starlette.middleware.cors import CORSMiddleware

from executor import ClaimsAgentExecutor

CARD_PATH = Path(os.environ.get("AGENT_CARD_PATH", "/app/agent-card.json"))


def load_agent_card() -> AgentCard:
    return AgentCard.model_validate(json.loads(CARD_PATH.read_text()))


def build_app():
    handler = DefaultRequestHandler(
        agent_executor=ClaimsAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    app = A2AStarletteApplication(
        agent_card=load_agent_card(), http_handler=handler
    ).build()

    # a2a-sdk 0.3.7 never extracts inbound W3C trace context itself (see
    # settlement-agent's NOTES.md) — StarletteInstrumentor is the OTel
    # project's own ASGI-level instrumentation, applied per-app since the
    # Starlette instance doesn't exist until `.build()` returns. It extracts
    # the incoming traceparent/baggage (via the global propagator set in
    # bootstrap.py) and starts a real server span from it, which a2a-sdk's
    # own spans then nest under via the ordinary ambient-context mechanism.
    StarletteInstrumentor().instrument_app(app)

    # The browser is a first-class A2A client. Without traceparent and
    # baggage in allow_headers the browser's root span detaches and the demo
    # produces two traces instead of one.
    origins = [
        o.strip()
        for o in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:8080").split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type", "traceparent", "tracestate", "baggage"],
    )
    return app


if __name__ == "__main__":
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "9001")))
