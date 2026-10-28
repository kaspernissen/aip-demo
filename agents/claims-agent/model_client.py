"""The model, reached through agentgateway, with this workload's identity on every call.

In the cluster, ANTHROPIC_BASE_URL points the anthropic SDK at agentgateway's
`llm` listener (deploy/k8s/claims-agent.yaml). The gateway verifies a
JWT-SVID on every model call exactly as it does on the MCP route (`jwtAuth` in
deploy/platform/agentgateway/config.yaml), records the verified caller as
`jwt.sub` on its span, and adds the model key itself: this pod holds none. The
SDK still sends an x-api-key (a placeholder the gateway replaces); the SVID
rides in Authorization, which the gateway strips before forwarding.

The token is fetched per request rather than set once on the client, because a
JWT-SVID expires within minutes; svid.py caches it until 30s before expiry.

With no ANTHROPIC_BASE_URL (the unit suite, a run against the provider
directly) the model name is returned unchanged and PydanticAI resolves it as it
always has.
"""
from __future__ import annotations

import os
from typing import Any

import httpx2

from svid import fetch_jwt_svid


class SvidBearer(httpx2.Auth):
    """Adds `Authorization: Bearer <JWT-SVID>` for `audience` to each request."""

    def __init__(self, audience: str) -> None:
        self.audience = audience

    async def async_auth_flow(self, request: httpx2.Request):
        request.headers["Authorization"] = f"Bearer {await fetch_jwt_svid(self.audience)}"
        yield request


def gateway_model(name: str) -> Any:
    """`name` ("anthropic:<model>"), routed through the gateway when one is configured."""
    if not os.getenv("ANTHROPIC_BASE_URL"):
        return name
    # Required, not defaulted: the value the gateway's jwtAuth accepts as
    # `aud`, as for settlement-agent's MCP calls. A wrong audience is a 401
    # on every model call, so a missing one should fail here, at startup.
    audience = os.environ["NORTHSTAR_GATEWAY_AUDIENCE"]
    from pydantic_ai._http import create_async_httpx2_client
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    client = create_async_httpx2_client()  # PydanticAI's own timeouts: 600s, 5s connect
    client.auth = SvidBearer(audience)
    _, _, model = name.partition(":")
    return AnthropicModel(model or name, provider=AnthropicProvider(http_client=client))
