"""The MeterProvider, which is the premise of the whole agent-health section.

No service in this system emits a single metric without one: no
`MeterProvider` in any Python or Go source, no `OTEL_METRICS_*` in any
manifest, and a collector `metrics` pipeline exporting to `debug` alone would
leave every measurement stranded. PydanticAI can only emit a `gen_ai.*` metric
into a provider that exists, so until one does, "which GenAI metrics does the
framework emit?" is an unanswerable question -- every answer is "none", for a
reason that says nothing about the framework.

**`assert not isinstance(provider, metrics.NoOpMeterProvider)` is inert, and
this file does not use it.** Measured against the installed
`opentelemetry-api==1.44.0` on an unconfigured process:

    $ .venv/bin/python -c "from opentelemetry import metrics; \
        print(metrics.get_meter_provider())"
    <opentelemetry.metrics._internal._ProxyMeterProvider object at 0x106e481a0>

`get_meter_provider()` returns `_PROXY_METER_PROVIDER`, never a
`NoOpMeterProvider`, whenever `set_meter_provider()` has not been called and
`OTEL_PYTHON_METER_PROVIDER` is unset -- read directly out of
`opentelemetry/metrics/_internal/__init__.py`:

    if _METER_PROVIDER is None:
        if OTEL_PYTHON_METER_PROVIDER not in environ:
            return _PROXY_METER_PROVIDER

So that assertion is green on an unconfigured process too. Binding to the SDK
`MeterProvider` type is what actually distinguishes "installed" from "nothing
installed": the proxy is an API object that forwards to a real provider *if
one is ever set* and silently discards every measurement if one is not.
"""
from __future__ import annotations

import pytest
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

import bootstrap


def _installed_provider() -> SdkMeterProvider:
    """The globally installed provider, after `configure()` has run.

    `bootstrap` calls `configure()` at import (its last line), and
    `metrics.set_meter_provider()` is once-only by design -- a second call logs
    "Overriding of current MeterProvider is not allowed" and returns without
    replacing anything. So the provider under test is the one the import
    installed, and calling `configure()` again here would prove nothing extra.
    """
    return metrics.get_meter_provider()


def test_configure_installs_a_meter_provider_that_exports_over_otlp():
    """A MeterProvider must exist, or no framework metric can ever appear.

    This is the premise of the whole agent-health section: PydanticAI can only
    emit `gen_ai.*` metrics into a provider that exists.
    """
    provider = _installed_provider()
    assert isinstance(provider, SdkMeterProvider), (
        "metrics.get_meter_provider() is "
        f"{type(provider).__module__}.{type(provider).__qualname__}, not the "
        "SDK MeterProvider: bootstrap.configure() did not install one, so "
        "every framework metric is silently discarded at the SDK, not at the "
        "collector. (Note the type in the message -- an unconfigured process "
        "reports _ProxyMeterProvider, NOT NoOpMeterProvider, which is why this "
        "assertion is on the SDK type rather than on `not isinstance(...,"
        " NoOpMeterProvider)`; see this module's docstring.)"
    )


def test_the_installed_provider_exports_over_otlp_on_a_periodic_reader():
    """The layer below the provider. A MeterProvider with no reader, or with a
    reader nothing collects from, records measurements into memory and drops
    them -- which looks identical, from outside, to a framework that emits
    nothing. This test is what tells those two cases apart."""
    provider = _installed_provider()
    readers = list(provider._metric_readers)
    assert readers, (
        "the installed MeterProvider has no metric readers, so every "
        "measurement is aggregated in memory and never exported -- "
        "indistinguishable, from Prometheus, from a framework that emits "
        "nothing"
    )
    periodic = [r for r in readers if isinstance(r, PeriodicExportingMetricReader)]
    assert periodic, (
        f"no PeriodicExportingMetricReader among {[type(r).__name__ for r in readers]}"
    )
    exporters = [r._exporter for r in periodic]
    assert any(isinstance(e, OTLPMetricExporter) for e in exporters), (
        "no reader exports over OTLP "
        f"({[type(e).__name__ for e in exporters]}), so nothing reaches the "
        "collector's metrics pipeline"
    )


def test_the_metrics_endpoint_comes_from_the_standard_otel_env_var():
    """Asserted rather than trusted: endpoint and protocol come
    from the standard `OTEL_EXPORTER_OTLP_*` variables, exactly as the
    TracerProvider's do, so `deploy/k8s/*.yaml` stays the only place an
    endpoint is named.

    Built fresh under a monkeypatched environment rather than read off the
    installed exporter: the installed one was constructed at import, before
    this test could influence anything, so it can only ever show the ambient
    default. Constructing a second one is what demonstrates the value tracks
    the variable rather than a literal in `bootstrap.py`.
    """
    sentinel = "http://collector.invalid:4318"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", sentinel)
        patch.delenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", raising=False)
        reader = bootstrap._metric_reader()
        try:
            endpoint = reader._exporter._endpoint
        finally:
            reader.shutdown()
    assert endpoint == f"{sentinel}/v1/metrics", (
        f"the metric exporter resolved {endpoint!r} while "
        f"OTEL_EXPORTER_OTLP_ENDPOINT was {sentinel!r}. bootstrap.py is "
        "naming an endpoint itself instead of letting the standard variable "
        "carry it, which is how a service ends up exporting to a collector "
        "the manifest never pointed it at."
    )


def test_no_view_drops_an_instrument():
    """This demo never samples, metrics included.

    A `View(instrument_name=..., aggregation=DropAggregation())` on the
    provider is the metrics-side equivalent of a tail sampler: the instrument
    still exists, the framework still records into it, and the series never
    leaves the process. Nothing configures a view today -- this asserts the
    absence, because absence is exactly what a later edit removes silently
    while the rest of the suite stays green (the argument
    tests/unit/test_no_sampling.py makes for traces).
    """
    provider = _installed_provider()
    assert provider._sdk_config.views == (), (
        f"the MeterProvider declares views: {provider._sdk_config.views!r}. "
        "This demo records 100% of its telemetry, metrics included -- a view "
        "with DropAggregation, or one narrowing an instrument's attribute "
        "keys, discards data inside the SDK where no collector-side guard can "
        "see it."
    )
