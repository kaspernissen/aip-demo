"""The claim id reaches this service's spans through the upstream
BaggageSpanProcessor, restricted to that one baggage key."""
import ast
from pathlib import Path

from opentelemetry import baggage, context
from opentelemetry.processor.baggage import BaggageSpanProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from correlation import CLAIM_ID_KEY, claim_id_span_processor, is_claim_id, set_claim_id

SERVICE_DIR = Path(__file__).resolve().parents[1]


def test_the_processor_is_upstreams_and_its_predicate_admits_only_the_claim_id():
    processor = claim_id_span_processor()
    assert isinstance(processor, BaggageSpanProcessor)
    assert processor._predicates == [is_claim_id]
    assert is_claim_id(CLAIM_ID_KEY)
    for other in ("northstar.claim", "northstar.claim.id.x", "user.email", "", "*"):
        assert not is_claim_id(other), other


def test_only_the_claim_id_is_copied_onto_a_span():
    """Baggage can carry other entries; none of them may reach the span."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(claim_id_span_processor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    ctx = baggage.set_baggage("user.email", "someone@example.com", context=set_claim_id("CLM-1842"))
    token = context.attach(ctx)
    try:
        with provider.get_tracer("test").start_as_current_span("work"):
            pass
    finally:
        context.detach(token)
    (span,) = exporter.get_finished_spans()
    assert span.attributes.get(CLAIM_ID_KEY) == "CLM-1842"
    assert "user.email" not in span.attributes


def test_bootstrap_adds_the_processor_to_its_tracer_provider():
    """Read from bootstrap.py's configure(), so a dropped or swapped processor
    fails here rather than as a missing attribute on the live trace."""
    tree = ast.parse((SERVICE_DIR / "bootstrap.py").read_text())
    configure = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "configure")
    added = [
        ast.unparse(call.args[0])
        for call in ast.walk(configure)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "add_span_processor"
    ]
    assert "claim_id_span_processor()" in added, added
    assert not any("ALLOW_ALL" in a or "BaggageSpanProcessor" in a for a in added), added
