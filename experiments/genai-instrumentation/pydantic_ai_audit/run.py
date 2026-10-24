"""Offline audit of what settlement-agent's PydanticAI agent emits.

Runs the repo's *real* `settlement.settlement_agent` and `decide_settlement`
(imported from agents/settlement-agent, unmodified) against a scripted
Anthropic Messages API (common/fake_anthropic.py) behind the real
`AnthropicModel` + `anthropic` client, so provider-specific attributes
(`gen_ai.provider.name`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`,
usage) come out exactly as they would against api.anthropic.com. No network,
no API key, no tokens.

Instrumentation is configured the way bootstrap.py configures it
(`Agent.instrument_all(InstrumentationSettings(include_content=..., version=5))`)
but with an in-memory exporter instead of OTLP. bootstrap.py itself is not
imported: it installs an OTLP exporter at import time.

Scenarios:
  happy       propose -> execute -> narrative
  refused     execute is denied by the platform (PolicyDenial, a ToolFailed)
              -> the model narrates the refusal
  tool_error  execute fails with ToolCallError (a malfunction) -> the run raises

Usage:
  python run.py [--version 5|6] [--content on|off] [--out DIR]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
AGENT = REPO / "agents" / "settlement-agent"
sys.path[:0] = [str(HERE.parent), str(AGENT), str(AGENT / "tests")]
os.environ.setdefault("ANTHROPIC_API_KEY", "fake-key-never-sent")

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from common import dump, fake_anthropic  # noqa: E402


class FakePayments:
    """payments-api shapes, as tests/test_settlement.py's FakePayments."""

    def __init__(self, *, execute_raises: Exception | None = None) -> None:
        self._execute_raises = execute_raises

    async def propose(self, claim_id, amount, currency="EUR"):
        return {"paymentId": "PAY-000001", "claimId": claim_id, "amount": float(amount),
                "currency": currency, "status": "PROPOSED"}

    async def execute(self, payment_id, amount, currency="EUR"):
        if self._execute_raises:
            raise self._execute_raises
        return {"paymentId": payment_id, "claimId": "CLM-1842", "amount": float(amount),
                "currency": currency, "status": "EXECUTED"}

    async def refuse(self, payment_id, **kw):
        return {"paymentId": payment_id, "status": "awaiting_approval"}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", type=int, default=5)
    ap.add_argument("--content", choices=["on", "off"], default="on")
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--pydantic", choices=["on", "off"], default="on",
                    help="PydanticAI's own instrumentation (off: only --extra emits gen_ai spans)")
    ap.add_argument("--extra", default="none",
                    choices=["none", "openinference", "openinference-processor", "openllmetry", "otel-genai"],
                    help="a third-party instrumentation to add (needs its package installed)")
    args = ap.parse_args()

    import httpx2
    from anthropic import AsyncAnthropic
    from pydantic_ai import Agent
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.models.instrumented import InstrumentationSettings
    from pydantic_ai.providers.anthropic import AnthropicProvider

    from mcp_tools import ToolCallError
    from settlement import decide_settlement, settlement_agent
    from test_mcp_tools import _denial_error

    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    if args.extra == "openinference-processor":
        # Rewrites PydanticAI's gen_ai spans into OpenInference attributes on
        # end; must run before the exporting processor.
        from openinference.instrumentation.pydantic_ai import OpenInferenceSpanProcessor

        provider.add_span_processor(OpenInferenceSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Registered globally, as bootstrap.py does. This matters beyond PydanticAI:
    # anthropic>=1.14.0 traces natively with the *global* provider (on by
    # default), so a harness that never sets one would hide that second span.
    from opentelemetry import trace

    trace.set_tracer_provider(provider)
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    if args.pydantic == "on":
        Agent.instrument_all(
            InstrumentationSettings(
                tracer_provider=provider,
                meter_provider=meter_provider,
                include_content=args.content == "on",
                version=args.version,
            )
        )
    else:
        Agent.instrument_all(False)

    # Client-library instrumentors that patch the `anthropic` SDK, run
    # alongside (or instead of) PydanticAI's own spans.
    if args.extra == "openinference":
        from openinference.instrumentation.anthropic import AnthropicInstrumentor

        AnthropicInstrumentor().instrument(tracer_provider=provider)
    elif args.extra == "openllmetry":
        os.environ.setdefault("TRACELOOP_TRACE_CONTENT", "true" if args.content == "on" else "false")
        from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor

        AnthropicInstrumentor().instrument(tracer_provider=provider, meter_provider=meter_provider)
    elif args.extra == "otel-genai":
        os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
        if args.content == "on":
            os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_ONLY")
        from opentelemetry.instrumentation.genai.anthropic import AnthropicInstrumentor

        AnthropicInstrumentor().instrument(tracer_provider=provider, meter_provider=meter_provider)

    client = AsyncAnthropic(
        api_key="fake", http_client=httpx2.AsyncClient(transport=fake_anthropic.httpx2_transport())
    )
    model = AnthropicModel(
        fake_anthropic.MODEL, provider=AnthropicProvider(anthropic_client=client)
    )

    claim = {"claimId": "CLM-1842", "amount": 6200, "currency": "EUR"}
    scenarios = {
        "happy": FakePayments(),
        "refused": FakePayments(execute_raises=_denial_error()),
        "tool_error": FakePayments(
            execute_raises=ToolCallError("payment PAY-000001 already executed (409)")
        ),
    }
    tag = f"v{args.version}-content-{args.content}"
    if args.pydantic == "off":
        tag = f"pydantic-off-content-{args.content}"
    if args.extra != "none":
        tag += f"+{args.extra}"
    out = Path(args.out) / tag
    for name, payments in scenarios.items():
        exporter.clear()
        with settlement_agent.override(model=model):
            try:
                reply = await decide_settlement(claim, payments)
                print(f"[{name}] reply status={reply['status']}")
            except Exception as exc:  # tool_error is expected to raise
                print(f"[{name}] raised {type(exc).__name__}: {exc}")
        spans = list(exporter.get_finished_spans())
        dump.write(out, name, spans)
        print(dump.tree(spans))
    dump.write_metrics(out, reader.get_metrics_data())
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
