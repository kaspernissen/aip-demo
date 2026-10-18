"""customer-ui's server: the static export, plus the A2A push relay.

Replaces nginx. The claims-agent POSTs each task snapshot to /a2a/push; the
browser, which cannot take an inbound request, reads them from /claims/events.
The token that ties the two together is issued by /claims/push-config and
travels in headers only, never in a URL, a span attribute or a log line.
"""
from __future__ import annotations

import bootstrap  # noqa: F401  (must run first: installs the OTel providers)

import json
import os
from pathlib import Path

import uvicorn
from opentelemetry.instrumentation.starlette import StarletteInstrumentor
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from relay import Relay

PUSH_URL = os.environ.get(
    "CUSTOMER_UI_PUSH_URL",
    "http://customer-ui.northstar-agents.svc.cluster.local:8080/a2a/push",
)
STATIC_DIR = os.environ.get("STATIC_DIR", "/app/static")


def create_app(relay: Relay | None = None, static_dir: str = STATIC_DIR) -> Starlette:
    relay = relay or Relay()
    root = Path(static_dir).resolve()

    async def push_config(request: Request) -> Response:
        return JSONResponse({"url": PUSH_URL, "token": relay.issue()})

    async def push(request: Request) -> Response:
        token = request.headers.get("x-a2a-notification-token")
        if not relay.known(token):
            return Response(status_code=401)
        try:
            task = await request.json()
        except ValueError:
            return Response(status_code=400)
        if not isinstance(task, dict):
            return Response(status_code=400)
        return Response(status_code=relay.push(token, task))

    async def events(request: Request) -> Response:
        token = request.headers.get("x-claim-token")
        claim = relay.claim_for(token)
        if claim is None:
            return Response(status_code=401)

        async def frames():
            async for snapshot in relay.follow(claim):
                yield f"data: {json.dumps(snapshot)}\n\n"

        return StreamingResponse(
            frames(), media_type="text/event-stream", headers={"cache-control": "no-store"}
        )

    async def healthz(request: Request) -> Response:
        return Response("ok")

    def _file(candidate: Path) -> Path | None:
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if resolved.is_file() and resolved.is_relative_to(root):
            return resolved
        return None

    async def static(request: Request) -> Response:
        # nginx: try_files $uri $uri.html $uri/index.html /index.html
        rel = request.path_params["path"].strip("/")
        tries = [root / rel, root / f"{rel}.html", root / rel / "index.html"] if rel else [root / "index.html"]
        for candidate in tries:
            found = _file(candidate)
            if found:
                return FileResponse(found)
        found = _file(root / "index.html")
        if found:
            return FileResponse(found)
        raise HTTPException(404)

    app = Starlette(
        routes=[
            Route("/claims/push-config", push_config, methods=["POST"]),
            Route("/a2a/push", push, methods=["POST"]),
            Route("/claims/events", events, methods=["GET"]),
            Route("/healthz", healthz, methods=["GET"]),
            Route("/{path:path}", static, methods=["GET", "HEAD"]),
        ]
    )
    StarletteInstrumentor().instrument_app(app)
    return app


if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=8080, access_log=False)
