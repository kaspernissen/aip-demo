"""M9: the platform emits what the telemetry registry says it emits.

`telemetry/registry` is Northstar's declared telemetry. This file is the
executable half of that claim: the collector sends a COPY of every span, metric
and log to the weaver-live-check pod (`deploy/observability/weaver-live-check.yaml`),
and this test reads its report and fails on any finding at `violation` about a
name this project owns (`northstar.*`, and the retired un-prefixed namespaces).
Everything else the cluster emits -- Kyverno, Cilium, SPIRE, agentgateway, the
GenAI instrumentation -- is reported at `information` by `telemetry/live-check.toml`
and does not fail anything; what the registry does not describe is listed in the
output, not judged.

**It runs last, and on its own.** The report covers everything emitted since
live-check was last restarted, so `scripts/smoke-test.sh` restarts it before the
e2e run, runs the rest of `tests/` with `--ignore` on this file, and then runs
this file as a separate pytest step. The ordering is the script's, not
collection order: pytest's alphabetical order would put this file in the middle
of the e2e directory. Run on its own against a quiet cluster it fails, honestly,
with "live-check received nothing".

**A report with nothing in it fails.** A live-check that was never sent anything
would otherwise report zero violations and pass; the sample-count assertions are
what make "no violations" mean something. They are paired with the two spans the
whole registry was written about.

Gated like every live module here (`RUN_BROWSER_TESTS=1`): it needs the stage-7
cluster, and it FAILS rather than skips when the cluster or live-check is absent.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.harness import livecheck

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason=(
        "needs the live stage-7 cluster and the traffic the rest of the gate "
        "drives; gated behind the same RUN_BROWSER_TESTS=1 flag "
        "scripts/smoke-test.sh sets unconditionally"
    ),
)

REPORT_PATH = Path(__file__).resolve().parents[2] / ".demo" / "m9-live-check-report.json"


@pytest.fixture(scope="module")
def report():
    return livecheck.report(save_to=REPORT_PATH)


def test_live_check_received_telemetry_of_every_signal(report):
    counts = livecheck.sample_counts(report)
    assert counts, (
        "live-check received nothing: its report holds no entities at all. Is the "
        "collector's otlp_grpc/weaver exporter failing (kubectl -n observability "
        "logs deploy/otel-collector | grep weaver), and was live-check restarted "
        "BEFORE the traffic this test is meant to judge?"
    )
    for signal in ("span", "metric", "log"):
        assert counts.get(signal, 0) > 0, (
            f"live-check received no {signal}s ({counts}). The registry cannot be "
            f"checked against a signal that never arrived."
        )
    print(f"\nlive-check received: { {k: counts[k] for k in ('span', 'metric', 'log')} }")


def test_the_two_spans_the_registry_is_about_were_seen(report):
    names = livecheck.span_names(report)
    for expected in ("northstar.policy.decision", "northstar.review.decision"):
        assert expected in names, (
            f"no {expected!r} span reached live-check; an empty answer here would "
            f"make 'no violations' mean nothing. Span names seen: {sorted(names)[:40]}"
        )


def test_no_service_still_emits_a_legacy_span_name(report):
    """Live-check judges attributes and metrics, not span names, so a service that went back
    to an un-prefixed span name raises no finding. This is the check for span names."""
    legacy = livecheck.legacy_span_names(report)
    assert legacy == [], (
        f"{len(legacy)} span name(s) from the retired un-prefixed namespaces reached "
        f"live-check (every owned span is `northstar.*` since M9): {legacy}. "
        f"Fix the emitter; live-check itself does not flag span names."
    )


def test_there_are_no_owned_violations(report):
    violations = livecheck.owned_violations(report)
    top = livecheck.top_foreign_findings(report)
    print(f"\nowned violations: {len(violations)}")
    print("top findings about names this project does not own (information only):")
    for message, count in top:
        print(f"  {count:5d}  {message}")
    assert violations == [], (
        f"{len(violations)} owned violation(s): the platform emitted telemetry the "
        "registry does not describe. Fix the emitter or the registry "
        "(telemetry/registry). Full report: .demo/m9-live-check-report.json\n"
        + "\n".join(
            f"  [{v['id']}] {livecheck.judged_name(v)} on {v['signal_type']} "
            f"{v.get('signal_name')!r}: {v['message']}"
            for v in violations
        )
    )
