from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import approval_metrics


def test_records_one_sample_in_seconds_with_decision_and_level():
    reader = InMemoryMetricReader()
    approval_metrics.use_meter(MeterProvider(metric_readers=[reader]).get_meter("test"))
    approval_metrics.record_wait(92.5, decision="approve", autonomy_level="assisted")
    metrics = reader.get_metrics_data().resource_metrics[0].scope_metrics[0].metrics
    (metric,) = [m for m in metrics if m.name == "northstar.approval.wait.duration"]
    assert metric.unit == "s"
    (point,) = metric.data.data_points
    assert point.count == 1 and point.sum == 92.5
    assert dict(point.attributes) == {
        "northstar.review.decision": "approve",
        "northstar.policy.autonomy.level": "assisted",
    }


def test_no_reviewer_id_ever_becomes_an_attribute():
    import inspect
    assert "reviewer" not in inspect.signature(approval_metrics.record_wait).parameters
