"""A2A delegation.

Which agents may be delegated to is configuration, never discovery. In M1 that
configuration is an environment variable; in M2 these become agentgateway route
names enforced at the gateway. M6 does NOT generate this from a Crossplane
`Agent` CRD -- that was the plan when this docstring was written, but M5's own
spec dropped Crossplane and Argo CD in favour of Backstage over the
`FeatureFlag` resource it introduced (docs/superpowers/specs/
2026-09-15-northstar-m6-golden-path-design.md §1), and M6 does not reintroduce
a CRD layer here either. This allow-list is unchanged by M6: it is still the
M2-era environment variable, read by `allowed_agents_from_env()` below.

No spans are created here by hand. `bootstrap.py`'s HTTPXClientInstrumentor
instruments the `httpx.AsyncClient` this module builds, emitting the CLIENT
span for the outbound call and injecting trace context into its headers (see
NOTES.md for whether that context actually reaches settlement-agent).

This deliberately talks raw A2A JSON-RPC over httpx rather than a2a-sdk's own
client (`a2a.client.A2AClient` / `ClientFactory`). a2a-sdk does ship a client
with its own telemetry (`a2a.client.transports.jsonrpc.JsonRpcTransport` is
`@trace_class(kind=SpanKind.CLIENT)`), but its typed response objects and
error handling don't match the plain dict-in/dict-out contract this module
tests against; the client-side a2a-sdk telemetry is investigated separately
for NOTES.md's upstream-evidence record without changing this production
implementation.
"""
from __future__ import annotations

import json
import os
import uuid

import httpx


def allowed_agents_from_env(value: str | None = None) -> dict[str, str]:
    raw = value if value is not None else os.environ.get("NORTHSTAR_ALLOWED_AGENTS", "")
    agents: dict[str, str] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, _, url = entry.partition("=")
        if name and url:
            agents[name.strip()] = url.strip()
    return agents


ALLOWED_AGENTS = allowed_agents_from_env()


class A2ADelegate:
    def __init__(
        self,
        base_url: str,
        agent_name: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 120.0,
    ):
        self._base_url = base_url.rstrip("/") + "/"
        self._agent_name = agent_name
        self._transport = transport
        self._timeout = timeout

    async def send(self, payload: dict) -> dict:
        request = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": uuid.uuid4().hex,
                    "parts": [{"kind": "text", "text": json.dumps(payload)}],
                }
            },
        }

        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout
        ) as client:
            response = await client.post(self._base_url, json=request)
            response.raise_for_status()
            body = response.json()

        if "error" in body:
            raise RuntimeError(
                f"{self._agent_name} returned an error: {body['error'].get('message')}"
            )

        return _extract_json_result(body["result"], self._agent_name)


def _extract_json_result(result: dict, agent_name: str) -> dict:
    """Pull the last text part out of an A2A result and parse it as JSON."""
    parts = result.get("parts")
    if parts is None:
        history = result.get("status", {}).get("message", {})
        parts = history.get("parts", [])

    texts = [p.get("text") for p in parts or [] if p.get("kind") == "text" and p.get("text")]
    for text in reversed(texts):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    raise RuntimeError(f"{agent_name} returned no JSON payload: {result!r}")
