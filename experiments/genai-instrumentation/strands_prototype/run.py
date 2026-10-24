"""settlement-agent's agent, rewritten in Strands Agents, run token-free.

Same instructions, same two MCP tools (served over stdio by
payments_mcp_stub.py, a stand-in for payments-mcp), same scripted Anthropic
Messages API (common/fake_anthropic.py) behind Strands' real `AnthropicModel`
and the real `anthropic` client. Spans go to an in-memory exporter on the
global tracer provider, which is what Strands' tracer uses.

What this prototype carries over from settlement.py, and how:
  * claim id: payments-mcp's propose_payment takes claim_id, which the model
    must not choose. settlement.py injects it from deps; here a
    BeforeToolCallEvent hook writes it into the tool input.
  * the latch (no payment call after a refusal): a BeforeToolCallEvent hook
    sets cancel_tool.
  * malfunction vs refusal: Strands turns every tool failure into an error
    tool result the model reads. settlement.py lets ToolCallError escape the
    run instead. Here an AfterToolCallEvent hook records the failure and the
    caller raises after the run when it was not a policy denial -- the model
    has already narrated by then, which is a behavioural difference.

Usage:
  python run.py [--semconv legacy|latest] [--out DIR]
    legacy  OTEL_SEMCONV_STABILITY_OPT_IN unset (Strands' default)
    latest  OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,gen_ai_tool_definitions
    latest-attrs  ... plus gen_ai_span_attributes_only (messages as span attributes, not span events)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

INSTRUCTIONS = (
    "You settle motor insurance claims for Northstar Insurance.\n"
    "You are given a claim that has already been assessed as covered.\n"
    "Settle it by calling propose_payment with the assessed amount, then "
    "execute_payment with the paymentId you get back and that same "
    "amount.\n"
    "Then reply with a short narrative for the claimant.\n"
    "Settle the assessed amount. Do not round it, discount it or "
    "renegotiate it. Do not state amounts in your narrative."
)


@dataclass
class Record:
    claim_id: str
    refused: str | None = None
    malfunction: str | None = None
    executed: dict | None = None
    calls: list[str] = field(default_factory=list)


def settlement_hooks(record: Record):
    from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

    class SettlementHooks(HookProvider):
        def register_hooks(self, registry: HookRegistry, **_: object) -> None:
            registry.add_callback(BeforeToolCallEvent, self.before)
            registry.add_callback(AfterToolCallEvent, self.after)

        def before(self, event: BeforeToolCallEvent) -> None:
            name = event.tool_use["name"]
            record.calls.append(name)
            if record.refused is not None:
                event.cancel_tool = f"policy refused this settlement earlier: {record.refused}"
                return
            if name == "propose_payment":
                event.tool_use["input"]["claim_id"] = record.claim_id

        def after(self, event: AfterToolCallEvent) -> None:
            res = event.result
            if not isinstance(res, dict) or event.tool_use["name"] != "execute_payment":
                return
            text = " ".join(c.get("text", "") for c in res.get("content", []) if isinstance(c, dict))
            if res.get("status") == "error":
                if "[policy-denial" in text:
                    record.refused = text
                else:
                    record.malfunction = text
            else:
                record.executed = res

    return SettlementHooks()


async def run_one(scenario: str, make_model, exporter, tracer_out: Path) -> None:
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client
    from strands import Agent
    from strands.tools.mcp import MCPClient

    from common import dump

    exporter.clear()
    env = {**os.environ, "SCENARIO": scenario}
    client = MCPClient(lambda: stdio_client(StdioServerParameters(
        command=sys.executable, args=[str(HERE / "payments_mcp_stub.py")], env=env)))
    record = Record(claim_id="CLM-1842")
    with client:
        agent = Agent(
            name="settlement_agent",
            agent_id="settlement-agent",
            description="Settles assessed motor claims by proposing and executing a payment.",
            model=make_model(),
            system_prompt=INSTRUCTIONS,
            tools=client.list_tools_sync(),
            hooks=[settlement_hooks(record)],
            callback_handler=None,
            trace_attributes={"session.id": "CLM-1842"},
        )
        result = await agent.invoke_async(
            "Claim CLM-1842 is assessed as covered for 6200 EUR. Settle it.")
    status = ("raise: " + record.malfunction) if record.malfunction else (
        "awaiting_approval" if record.refused else "EXECUTED" if record.executed else "?")
    print(f"[{scenario}] stop_reason={result.stop_reason} calls={record.calls} -> {status}")
    spans = list(exporter.get_finished_spans())
    dump.write(tracer_out, scenario, spans)
    print(dump.tree(spans))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--semconv", choices=["legacy", "latest", "latest-attrs"], default="latest")
    ap.add_argument("--out", default=str(HERE / "out"))
    args = ap.parse_args()
    if args.semconv == "latest":
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = "gen_ai_latest_experimental,gen_ai_tool_definitions"
    elif args.semconv == "latest-attrs":
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = (
            "gen_ai_latest_experimental,gen_ai_tool_definitions,gen_ai_span_attributes_only")
    else:
        os.environ.pop("OTEL_SEMCONV_STABILITY_OPT_IN", None)

    import httpx
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from strands.models.anthropic import AnthropicModel

    from common import fake_anthropic

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    def make_model():
        return AnthropicModel(
            client_args={"api_key": "fake",
                         "http_client": httpx.AsyncClient(transport=fake_anthropic.httpx_transport())},
            model_id=fake_anthropic.MODEL,
            max_tokens=1024,
        )

    out = Path(args.out) / args.semconv
    for scenario in ("happy", "refused", "tool_error"):
        await run_one(scenario, make_model, exporter, out)
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
