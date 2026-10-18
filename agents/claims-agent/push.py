"""A2A push notifications for claims-agent, behind an exact-match URL allowlist.

A push URL is supplied by the client. An agent that POSTs wherever it is told
is a server-side request forgery hole, so the URL is checked here, before the
task starts, against an exact list from the Deployment's env. Cilium's Reach
rule (deploy/platform/cilium-policies.yaml) enforces the same thing on the
wire; this is the application's half.

a2a-sdk 0.3.7: a ServerError raised from a handler method is turned into a
JSON-RPC error response by JSONRPCHandler (error.code -32602 for
InvalidParamsError). The check runs before _setup_message_execution, so a
refused request never starts the executor.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.types import InvalidParamsError
from a2a.utils.errors import ServerError

DEFAULT_PUSH_URL = "http://customer-ui.northstar-agents.svc.cluster.local:8080/a2a/push"


class PushUrlRefused(ServerError):
    def __init__(self, url: str):
        super().__init__(error=InvalidParamsError(message=f"push URL not allowed: {url!r}"))


def allowlist_from_env(env: Mapping[str, str] = os.environ) -> list[str]:
    raw = env.get("CLAIMS_AGENT_PUSH_URL_ALLOWLIST", DEFAULT_PUSH_URL)
    return [u.strip() for u in raw.split(",") if u.strip()]


class AllowlistedRequestHandler(DefaultRequestHandler):
    def __init__(self, *args, push_url_allowlist: list[str], **kwargs):
        super().__init__(*args, **kwargs)
        self._allowed = frozenset(push_url_allowlist)

    def _check(self, config) -> None:
        if config is not None and config.url not in self._allowed:
            raise PushUrlRefused(config.url)

    @staticmethod
    def _config_of(params):
        return params.configuration and params.configuration.push_notification_config

    async def on_message_send(self, params, context=None):
        self._check(self._config_of(params))
        return await super().on_message_send(params, context)

    async def on_message_send_stream(self, params, context=None):
        self._check(self._config_of(params))
        async for event in super().on_message_send_stream(params, context):
            yield event

    async def on_set_task_push_notification_config(self, params, context=None):
        self._check(params.push_notification_config)
        return await super().on_set_task_push_notification_config(params, context)
