"""Resume the customer's waiting task: A2A's way to give an input-required
task its input is a message on the same task id (and context id).

tasks/get first, for two reasons: the context id (the ledger stores only the
task id), and to say plainly when the task is gone (claims-agent restarted).
The message is sent non-blocking (configuration.blocking=false): the
claims-agent completes the task on its own stream, and a blocking send would
race the SDK's cleanup of that task's queue.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx


class ResumeFailed(RuntimeError):
    """The customer's task could not be resumed. The money decision stands."""


class TaskNotWaiting(ResumeFailed):
    """The task is no longer `input-required`: someone already resumed it, or it
    reached a terminal state. Distinct from a transient failure, because only
    this one means there is nothing left to heal."""


class ClaimsClient:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 30.0):
        self._url = base_url.rstrip("/") + "/"
        self._transport = transport
        self._timeout = timeout

    async def _call(self, client: httpx.AsyncClient, method: str, params: dict) -> dict:
        request_id = uuid.uuid4().hex
        response = await client.post(self._url, json={
            "jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            raise ResumeFailed(f"claims-agent {method}: {body['error'].get('message')}")
        if body.get("id") != request_id or "result" not in body:
            raise ResumeFailed(f"claims-agent {method}: reply does not answer this request")
        return body["result"]

    async def resume(self, task_id: str, payload: dict[str, Any]) -> str:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
                task = await self._call(client, "tasks/get", {"id": task_id})
                if task["status"]["state"] != "input-required":
                    raise TaskNotWaiting(f"task {task_id} is {task['status']['state']!r}, not waiting")
                accepted = await self._call(client, "message/send", {
                    "message": {"role": "user", "messageId": uuid.uuid4().hex,
                                "kind": "message",
                                "taskId": task_id, "contextId": task["contextId"],
                                "parts": [{"kind": "text", "text": json.dumps(payload)}]},
                    "configuration": {"blocking": False}})
        except httpx.HTTPError as exc:
            raise ResumeFailed(f"could not reach claims-agent to resume {task_id}: {exc}") from exc
        return (accepted.get("status") or {}).get("state", "unknown")
