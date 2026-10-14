"""How long humans take: one OpenTelemetry histogram, recorded at the decision.

northstar.approval.wait.duration, seconds, from the ledger's awaitingSince to the
moment the reviewer's decision took effect. Attributes: decision and
autonomy.level. The reviewer's id is deliberately absent: one series per person
would make the metric high-cardinality. Not sampled; the default aggregation is
kept (bootstrap.py installs the provider, with no views).
"""
from __future__ import annotations

from opentelemetry import metrics

_NAME = "northstar.approval.wait.duration"
_DESCRIPTION = "Time from a payment awaiting approval to a human's decision"

_meter = metrics.get_meter("reviewer-ui")
_histogram = _meter.create_histogram(_NAME, unit="s", description=_DESCRIPTION)


def use_meter(meter) -> None:
    """Tests only: bind the instrument to a meter with an in-memory reader."""
    global _histogram
    _histogram = meter.create_histogram(_NAME, unit="s", description=_DESCRIPTION)


def record_wait(seconds: float, *, decision: str, autonomy_level: str) -> None:
    _histogram.record(seconds, {"decision": decision, "autonomy.level": autonomy_level})
