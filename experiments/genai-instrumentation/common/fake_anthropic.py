"""A scripted, token-free stand-in for the Anthropic Messages API.

It answers `POST /v1/messages` the way the real API would for the settlement
flow, so the *real* Anthropic client code paths run (and anything that
instruments them sees a real request/response pair):

  turn 1  (no tool results yet)             -> tool_use propose_payment, stop_reason=tool_use
  turn 2  (tool result for propose, ok)     -> tool_use execute_payment, stop_reason=tool_use
  turn 3  (any other tool result, ok/error) -> text narrative,           stop_reason=end_turn

It is transport-agnostic: `handle(body) -> dict` holds the script, and
`httpx_transport()` / `httpx2_transport()` wrap it in a MockTransport for
whichever HTTP library the client under test is built on (anthropic 1.x uses
httpx2; older anthropic releases use httpx).

Usage figures are deterministic so every framework's token attributes can be
compared like-for-like. No network, no API key, no tokens spent.
"""
from __future__ import annotations

import itertools
import json
from typing import Any

MODEL = "claude-sonnet-5"
_ids = itertools.count(1)


def _tool_results(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]


def _tool_names_by_id(messages: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for m in messages:
        if m.get("role") != "assistant" or not isinstance(m.get("content"), list):
            continue
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                names[b["id"]] = b["name"]
    return names


def _amount_from_prompt(messages: list[dict[str, Any]]) -> float:
    first = messages[0].get("content") if messages else ""
    text = first if isinstance(first, str) else " ".join(
        b.get("text", "") for b in first if isinstance(b, dict)
    )
    for tok in text.replace(",", " ").split():
        try:
            return float(tok)
        except ValueError:
            continue
    return 6200.0


def handle(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages", [])
    last = messages[-1] if messages else {}
    results = _tool_results(last)
    names = _tool_names_by_id(messages)
    n = next(_ids)
    amount = _amount_from_prompt(messages)

    # Non-zero cache figures on purpose: Anthropic's `input_tokens` EXCLUDES
    # cached tokens, and the GenAI semconv (anthropic.md) says
    # gen_ai.usage.input_tokens = input_tokens + cache_read + cache_write.
    # With cache at 0 an instrumentation that forgets to sum looks correct.
    # Expected semconv input_tokens per turn = 400 + 100*len(messages) + 120.
    usage = {
        "input_tokens": 400 + 100 * len(messages),
        "output_tokens": 50,
        "cache_creation_input_tokens": 20,
        "cache_read_input_tokens": 100,
    }

    def tool_use(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": f"msg_fake_{n:04d}",
            "type": "message",
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "tool_use", "id": f"toolu_fake_{n:04d}", "name": name, "input": args},
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": usage,
        }

    if not results:
        return tool_use("propose_payment", {"amount": amount, "currency": "EUR"})

    last_name = names.get(results[-1].get("tool_use_id"), "")
    if last_name == "propose_payment" and not results[-1].get("is_error"):
        return tool_use(
            "execute_payment",
            {"payment_id": "PAY-000001", "amount": amount, "currency": "EUR"},
        )

    refused = any(r.get("is_error") for r in results) or any(
        "denied" in json.dumps(r.get("content", "")).lower()
        or "exceeds" in json.dumps(r.get("content", "")).lower()
        for r in results
    )
    text = (
        "Your payment needs a human reviewer's approval before it can be released."
        if refused
        else "Your claim has been settled in full."
    )
    return {
        "id": f"msg_fake_{n:04d}",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": usage,
    }


def to_sse(msg: dict[str, Any]) -> bytes:
    """The same message as the Messages API's server-sent event stream."""
    events: list[tuple[str, dict[str, Any]]] = []
    start = {**msg, "content": [], "stop_reason": None,
             "usage": {**msg["usage"], "output_tokens": 1}}
    events.append(("message_start", {"type": "message_start", "message": start}))
    for i, block in enumerate(msg["content"]):
        if block["type"] == "text":
            events.append(("content_block_start", {"type": "content_block_start", "index": i,
                                                   "content_block": {"type": "text", "text": ""}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                   "delta": {"type": "text_delta", "text": block["text"]}}))
        else:
            events.append(("content_block_start", {"type": "content_block_start", "index": i,
                                                   "content_block": {**block, "input": {}}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                   "delta": {"type": "input_json_delta",
                                                             "partial_json": json.dumps(block["input"])}}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    events.append(("message_delta", {"type": "message_delta",
                                     "delta": {"stop_reason": msg["stop_reason"], "stop_sequence": None},
                                     "usage": msg["usage"]}))
    events.append(("message_stop", {"type": "message_stop"}))
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


def _handler(httpmod):
    def handler(request):
        if not request.url.path.endswith("/v1/messages"):
            return httpmod.Response(404, json={"error": "not scripted: " + request.url.path})
        body = json.loads(request.content.decode() or "{}")
        msg = handle(body)
        if body.get("stream"):
            return httpmod.Response(200, content=to_sse(msg),
                                    headers={"content-type": "text/event-stream", "request-id": "req_fake"})
        return httpmod.Response(200, json=msg, headers={"request-id": "req_fake"})

    return handler


def httpx_transport():
    import httpx

    return httpx.MockTransport(_handler(httpx))


def httpx2_transport():
    import httpx2

    return httpx2.MockTransport(_handler(httpx2))
