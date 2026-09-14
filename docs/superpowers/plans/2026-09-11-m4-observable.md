# M4 "Observable" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the audit trail this platform already emits queryable — one saved
dashboard reconstructs why each of two payouts happened, policy-adherence scores
ride the same pipeline, and agent-health metrics are measured against what the
frameworks actually emit.

**Architecture:** The collector fans out to OpenSearch and Prometheus alongside
the existing Jaeger and optional Dash0 destinations — destinations are collector
config, not application code. The one piece of real application work is standing
up a `MeterProvider`, because nothing in this system emits a metric today.

**Tech Stack:** OpenSearch + OpenSearch Dashboards, Prometheus (OTLP ingest),
`opensearch-genai-observability-sdk-py`, OpenTelemetry Collector, Python 3.13,
kind.

**Spec:** `docs/superpowers/specs/2026-09-11-northstar-m4-observable-design.md`

## Global Constraints

- **No invented telemetry.** Third-party framework telemetry is recorded as it
  actually appears. Only components in this repo may author telemetry. A metric a
  framework does not emit is a **gap to record**, never a hole to hand-roll shut.
- **No unmeasured claims.** Every number, metric name, attribute key, version and
  default written in code, comments, docs or assertions traces to a live
  measurement or to read source.
- **No exact whole-trace span totals** may be published anywhere, with one
  carve-out added by M4's final review (finding M8): a **dated,
  trace-qualified incident record whose claim is a _difference_ between two
  counts of the same trace** may state both, because the difference *is* the
  finding and cannot be stated without them. The one live instance is
  `deploy/observability/collector-config.yaml`, recording that trace
  `e25671d6…` held 142 spans in Jaeger and 139 documents in OpenSearch and that
  the three missing ones are exactly its three `policy.decision` spans.
  Everything the rule was written against still falls: a total published as a
  property *of the system* rather than of one dated capture, which is not
  reproducible because `EventQueue.dequeue_event` is a polling span measured
  varying 10–16 across identically-shaped runs. M4 shipped one of those too —
  `docs/telemetry-contract.md`'s "148 spans" line, retired by the same review,
  where the two service counts that dominated the total were the two carrying
  the polling span. The rule needed both halves stated, not relaxing.
- **Every GenAI metric in the spec's §6 table is Development stability.** Any
  sentence published about one carries that qualifier and the version it was
  measured against.
- **Amounts, currencies, statuses and payment ids** on any surface come from the
  payments ledger, never from model output.
- **This demo never samples telemetry.** `tests/unit/test_no_sampling.py` guards
  it. If a change alters a default sampler, the guard is right and the change is
  wrong.
- **Nothing floats:** every requirements file keeps its full transitive closure
  pinned with `==`.
- **Pin the latest released version** of anything new, verified against upstream
  releases (Artifact Hub / GitHub releases / PyPI), not a local index. Skip
  release candidates.
- **Each Python service's suite runs from its own directory** with its own
  `.venv/bin/python -m pytest`. Bare `python3` is 3.10.4 here and `uv run pytest`
  grabs homebrew 3.13 — both produce failures that are not real.
- **Never install into a service venv** casually; contents are pinned and
  asserted.
- **Never run `scripts/teardown.sh`.** Never touch the `kind-registry`
  container. **Recreating the `northstar` cluster requires explicit permission
  from the human partner, each time.**
- **Nothing is filed, commented, or written to any upstream repository or
  tracker.** Reading public trackers, releases and source is expected.
- **The gate (`scripts/smoke-test.sh`) runs once, in Task 5.** It performs live
  settlements that cost real money.
- Port-forwards: `smoke-test.sh` checks for them and exits without creating
  them. A dead `kubectl port-forward` keeps its socket bound, so use a real HTTP
  request as the liveness check, never `lsof`. Kill only the PIDs you started.
- **Adding a workload means adding its SPIFFE ID to
  `tests/unit/test_stage2_spire_config.py`'s allow-list in the same task**
  (M3 ruling R22 — a guard that fails on a correct document teaches readers to
  edit the document to match the test).

## File Structure

| Path | Responsibility | Task |
|---|---|---|
| ~~`deploy/kind/stage4.yaml`~~ | **Not created — amendment A1.** Stage 4 reuses `deploy/kind/stage3.yaml`; Dashboards is reached by port-forward | 1 |
| `scripts/setup-kind.sh` | `--stage 4`, installs OpenSearch + Prometheus | 1, 2 |
| `deploy/observability/opensearch/values.yaml` | single-node OpenSearch, measured heap | 1 |
| `deploy/observability/opensearch/dashboards-values.yaml` | OpenSearch Dashboards | 1 |
| `deploy/observability/collector-config.yaml` | the OpenSearch and Prometheus exporters and their pipelines | 1, 2 |
| `deploy/observability/prometheus/values.yaml` | Prometheus with OTLP receiver enabled | 2 |
| `agents/settlement-agent/bootstrap.py` (and siblings) | `MeterProvider` wiring | 2 |
| `docs/telemetry-contract.md` | the measured metric inventory; OQ-1's answer | 2, 5 |
| `deploy/observability/dashboards/evidence.ndjson` | the saved evidence dashboard | 3 |
| `tests/e2e/test_evidence_view.py` | the dashboard's queries, asserted | 3 |
| `platform/eval-scorer/` | the policy-adherence scorer | 4 |
| `tests/e2e/test_policy_scores.py` | scores land; non-compliance scores differently | 4 |
| `docs/upstream-findings.md` | any framework gap this milestone finds | 2, 5 |

---

### Task 1: OpenSearch in the cluster, and the trace-ingest path measured

The spec's §3 says this is first because everything downstream rests on it and
the path is **not** established. Produce the measurement, then the config.

> **Amendment A1, 2026-09-12 — the human partner declined the cluster recreate.**
> Steps 1–3 below were written assuming a fifth host port mapping for OpenSearch
> Dashboards, which `extraPortMappings` can only add at cluster creation. Asked
> directly, the human partner chose **"No — port-forward Dashboards instead"**.
> So: **no `deploy/kind/stage4.yaml` is created**, `--stage 4` reuses
> `deploy/kind/stage3.yaml` unchanged, the running cluster is left alone, and
> Dashboards is reached with `kubectl port-forward`. Steps 1–3 are replaced by
> the single step A1 below; Step 9's guard changes accordingly. Everything from
> Step 4 on is unaffected. The original steps are left struck through rather than
> deleted, because what was planned and why it changed is the useful part of the
> record.

**Files:**
- Create: `deploy/observability/opensearch/values.yaml`, `deploy/observability/opensearch/dashboards-values.yaml`
- Modify: `scripts/setup-kind.sh`, `deploy/observability/collector-config.yaml`
- Test: `tests/unit/test_stage4_config.py`

**Interfaces:**
- Produces: a queryable OpenSearch at
  `opensearch-cluster-master.observability.svc.cluster.local:9200` holding this
  system's traces, and Dashboards reachable from the host. Tasks 3 and 4 consume
  both.

- [ ] **Step A1 (replaces Steps 1–3): Point `--stage 4` at the existing stage-3 cluster config**

Already measured, 2026-09-12: `deploy/kind/stage3.yaml` has exactly four
`extraPortMappings` (30080→8080 customer-ui, 30686→16686 jaeger, 30318→4318
collector OTLP/HTTP, 30081→8081 reviewer-ui) and no spare. The human partner
declined the recreate. **Create no `stage4.yaml`, touch no kind config, and do
not delete or recreate the cluster.** `--stage 4` reuses `stage3.yaml`, and
OpenSearch Dashboards is reached by port-forward via Step A2's script.

- [ ] **Step A2 (new): Write `scripts/start-demo.sh`, which owns every port-forward**

This exists because port-forwards are this project's most-repeated operational
failure: rulings R10 and R20 both came from them, **a dead `kubectl
port-forward` keeps its listening socket bound so `lsof` reports it healthy**,
and every M3 task re-established them by hand — one of them measured against
dead ones without noticing.

Model it on the human partner's own
`/Users/kaspernissen/dash0/demos/otel-platform-demo/01_start-demo.sh`: a `PIDS`
array, background `kubectl port-forward` per surface, and a `cleanup` function
on `trap ... EXIT INT TERM`. **Two deliberate differences from that reference,
both required here:**

1. **Do not add its `pkill -f "kubectl port-forward"` fallback.** It kills
   forwards this script never started, including the human partner's own in
   another terminal. The tracked `PIDS` array is the whole cleanup; this repo's
   standing rule is to kill only the PIDs you started.
2. **Liveness is a real HTTP request, never process existence.** A live PID
   proves nothing — that is exactly what R20 measured. Reuse the two-question
   shape `scripts/smoke-test.sh` already implements (`lsof`, then `curl`) rather
   than inventing a second one.

Cover the four forwards `smoke-test.sh` checks for — 19001 claims-agent, 19002
settlement-agent, 18000 payments-mcp, 18080 — plus the reviewer-ui one M3 added,
and leave a clearly-marked place for Task 2's Prometheus and this task's
OpenSearch Dashboards. Print the surface table (URL per surface) once everything
is verified up.

Then change `scripts/smoke-test.sh`'s failure message to point at
`./scripts/start-demo.sh` instead of a `kubectl port-forward` line the operator
has to assemble. **`smoke-test.sh` still creates nothing** — that division is
deliberate and predates this milestone; only the remedy text changes.

- [ ] **Step 4: Add `--stage 4` to `scripts/setup-kind.sh`**

Follow exactly how `--stage 3` is wired. The stage gate's upper bound is
asserted by `tests/unit/test_stage2_cilium_config.py` — M3 ruling R5 records that
raising it without running `tests/unit` left the branch red across two commits.
Run `tests/unit` in this step, not at the end.

- [ ] **Step 5: Install OpenSearch, single node, with the heap measured**

Pin the latest chart version verified against Artifact Hub. Single node, security
demo config off for a local demo cluster, and an explicitly set heap — the
default is sized for a server, and stage 4's budget is ~8 GB total.

Write `deploy/observability/opensearch/values.yaml` with `singleNode: true`, one
replica, and `OPENSEARCH_JAVA_OPTS` set to the heap you choose. Then **measure**:

```bash
kubectl -n observability top pod -l app.kubernetes.io/name=opensearch
```

Record the observed figure in the values file's header comment alongside the heap
you set, in the house style — the number and how it was obtained, not a guess.

- [ ] **Step 6: Measure the trace-ingest path — reuse first**

The user's instruction: *"reuse what you can, add Data Prepper if it's needed to
send traces to OpenSearch."* Their `otel-platform-demo` proves the `opensearch`
exporter for **logs** only.

Add the exporter and a traces branch to `deploy/observability/collector-config.yaml`,
following the reference's shape (`infrastructure/collector/deployment-values.yaml`
in `/Users/kaspernissen/dash0/demos/otel-platform-demo`):

```yaml
exporters:
  opensearch/trace:
    http:
      endpoint: https://opensearch-cluster-master.observability.svc.cluster.local:9200
      tls:
        insecure_skip_verify: true
```

Add `opensearch/trace` to the existing `traces` pipeline's exporters. Apply,
restart the collector, drive one settlement, and answer three questions with
commands rather than reasoning:

```bash
kubectl -n observability logs deploy/otel-collector | tail -40
kubectl -n observability exec svc/opensearch-cluster-master -- \
  curl -sk https://localhost:9200/_cat/indices
```

1. Does the collector accept the config and export without error?
2. Does an index appear?
3. Is a span queryable out of it with its attributes intact?

- [ ] **Step 7: If and only if Step 6 fails, add Data Prepper**

Escalate only on a measured failure, and record what failed. If Step 6 succeeded,
skip this step and say so in your report — an unnecessary component in the
tightest-memory stage is a defect.

- [ ] **Step 8: Re-verify the M3 baseline on the new cluster**

Mirror M3 Task 1 Step 7: before anything else is added, run the full denial
scenario and confirm `policy.decision` = deny, no execute span, ledger `refused`.
Record the trace id.

- [ ] **Step 9: Write the config guard**

Amendment A1 removed the `stage4.yaml` this step was going to guard. Guard what
actually shipped instead — that stage 4 adds no kind config, and that the one
document an operator reads to reach Dashboards names the script rather than a
hand-assembled command.

```python
def test_stage_4_reuses_the_stage_3_cluster_config():
    """Amendment A1: no stage4.yaml exists, deliberately.

    The human partner declined a cluster recreate on 2026-09-12, so stage 4 runs
    on stage 3's cluster and reaches OpenSearch Dashboards by port-forward. A
    stage4.yaml appearing later means someone recreated the cluster without
    asking -- which is a standing constraint, not a preference.
    """
    assert not (REPO_ROOT / "deploy/kind/stage4.yaml").exists(), (
        "deploy/kind/stage4.yaml exists. Stage 4 was deliberately built on "
        "stage 3's cluster config (plan amendment A1) because extraPortMappings "
        "is create-time only and the recreate was declined. If a recreate has "
        "since been authorised, delete this test in the same commit that adds "
        "the file, and say who authorised it."
    )
    setup = (REPO_ROOT / "scripts/setup-kind.sh").read_text()
    assert "stage3.yaml" in setup, (
        "setup-kind.sh no longer references stage3.yaml, so --stage 4 has "
        "nothing to bring a cluster up from"
    )


def test_the_port_forward_remedy_points_at_the_script():
    """Step A2: smoke-test.sh tells the operator to run start-demo.sh.

    Mutation-check: change the remedy text back to a bare kubectl line and
    confirm this goes red rather than skipped.
    """
    smoke = (REPO_ROOT / "scripts/smoke-test.sh").read_text()
    assert "start-demo.sh" in smoke, (
        "smoke-test.sh's port-forward failure message must name "
        "./scripts/start-demo.sh -- assembling a kubectl port-forward by hand is "
        "what rulings R10/R20 came from"
    )
    assert (REPO_ROOT / "scripts/start-demo.sh").exists(), (
        "scripts/start-demo.sh is referenced but missing"
    )
```

- [ ] **Step 10: Run `tests/unit` from the repo root, then commit**

```bash
.venv/bin/python -m pytest tests/unit -q
git add -A && git commit -m "feat(m4): stage 4, OpenSearch, and the measured trace-ingest path"
```

---

### Task 2: Metrics, and what agent health actually reports

The spec's §6 is the finding this task exists to produce. **Nothing in this
system emits a metric today** — measured during design: no `MeterProvider` in any
Python or Go source, no `OTEL_METRICS_*` in any manifest, and the collector's
`metrics` pipeline exports to `debug` alone.

**Files:**
- Create: `deploy/observability/prometheus/values.yaml`
- Modify: `agents/settlement-agent/bootstrap.py` and the equivalent bootstrap in `agents/claims-agent`, `platform/policy-decision-service`, `apps/reviewer-ui`; `deploy/observability/collector-config.yaml`; `scripts/setup-kind.sh`; `docs/telemetry-contract.md`
- Test: `agents/settlement-agent/tests/test_telemetry.py`

**Interfaces:**
- Consumes: Task 1's stage-4 installer.
- Produces: a `MeterProvider` in each Python service, and a published inventory
  of which GenAI metrics are actually emitted. Task 5 re-verifies the inventory.

- [ ] **Step 1: Write the failing test for the MeterProvider**

```python
def test_configure_installs_a_meter_provider_that_exports_over_otlp():
    """A MeterProvider must exist, or no framework metric can ever appear.

    This is the premise of the whole agent-health section: PydanticAI can only
    emit gen_ai.* metrics into a provider that exists.
    """
    from opentelemetry import metrics
    telemetry.configure()
    provider = metrics.get_meter_provider()
    assert not isinstance(provider, metrics.NoOpMeterProvider), (
        "metrics.get_meter_provider() is still the no-op provider: "
        "telemetry.configure() did not install one, so every framework metric "
        "is silently discarded at the SDK, not at the collector"
    )
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd agents/settlement-agent && .venv/bin/python -m pytest tests/test_telemetry.py -q
```

Expected: FAIL — the no-op provider is what is installed today.

- [ ] **Step 3: Add the MeterProvider, following this repo's own wiring pattern**

Match `platform/policy-decision-service/pds/telemetry.py`: read endpoints from the
standard `OTEL_EXPORTER_OTLP_*` environment variables, never hardcode them.

```python
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

def _configure_metrics(resource) -> None:
    """Install a MeterProvider. Endpoint and protocol come from the standard
    OTEL_EXPORTER_OTLP_* variables, as for traces."""
    reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[reader])
    )
```

Call it from each service's `configure()` beside the existing `TracerProvider`
setup, reusing that function's `Resource`.

- [ ] **Step 4: Run the test and watch it pass**

- [ ] **Step 5: Install Prometheus with OTLP ingest enabled**

Copy the route from the user's reference demo — Prometheus receives OTLP
directly, so there is no scrape config and no application change:

```yaml
exporters:
  otlphttp/prometheus:
    endpoint: http://prometheus.observability.svc.cluster.local:9090/api/v1/otlp
    tls:
      insecure: true
```

Add it to the collector's `metrics` pipeline exporters, keeping `debug`. Prometheus
needs its OTLP receiver feature enabled in `values.yaml`; verify the flag name
against the chart version you pin rather than assuming it.

- [ ] **Step 6: Measure what is actually emitted — this is the task's product**

Redeploy, drive one settlement end to end, then read what arrived:

```bash
kubectl -n observability port-forward svc/prometheus 9090:9090 &
curl -s 'http://localhost:9090/api/v1/label/__name__/values' | tr ',' '\n' | grep -i gen_ai
```

Compare against the spec's §6 table — `gen_ai.invoke_agent.duration`,
`gen_ai.invoke_agent.inference_calls`, `gen_ai.invoke_agent.tool_calls`,
`gen_ai.execute_tool.duration`, `gen_ai.client.token.usage`,
`gen_ai.client.operation.duration`.

**Record exactly which appear and which do not.** Do not add a metric a framework
does not emit. If PydanticAI emits none, that is this milestone's finding and it
gets written up as such.

- [ ] **Step 7: Publish the inventory, and file the gap where gaps go**

Add a measured metrics section to `docs/telemetry-contract.md` in the same shape
as its existing "as measured" sections — instrument, type, unit, attributes,
**and the stability qualifier**, every metric in the §6 table being Development.
Note there that the GenAI conventions now live in
`open-telemetry/semantic-conventions-genai`, and correct any sentence in that
document still citing the main semconv repository for them.

If a framework emits nothing where the conventions define something, add a
numbered finding to `docs/upstream-findings.md` following Findings 1–11's
structure. File nothing upstream.

- [ ] **Step 8: Run every affected service's suite from its own directory, then commit**

```bash
git add -A && git commit -m "feat(m4): a MeterProvider, Prometheus over OTLP, and what agent health really reports"
```

---

### Task 3: The evidence dashboard

**Files:**
- Create: `deploy/observability/dashboards/evidence.ndjson`, `tests/e2e/test_evidence_view.py`
- Modify: `scripts/setup-kind.sh` (import the saved object), `docs/architecture.md`

**Interfaces:**
- Consumes: Task 1's queryable OpenSearch.
- Produces: the dashboard and the queries Task 5's gate runs.

- [ ] **Step 1: Settle the €5,800 claim id**

Spec §4 records the measurement: `CLM-1842` is a fixed constant
(`tests/e2e/test_policy_denial.py:124`) but the permitted path generates
`f"CLM-PERMIT-{uuid.uuid4().hex[:8]}"` (`:412`), so there is no stable claim for a
saved dashboard to point at.

Read M3's precedent first — `tests/e2e/test_supervised.py`'s per-run unique
reviewer id, which exists because a fixed value collided across runs. Then choose:
a fixed fixture id for the demo, or a dashboard parameterised on claim id. Record
the choice and its reason in the dashboard file's header. Both are defensible; an
unrecorded choice is not.

- [ ] **Step 2: Write the query test first, before the dashboard**

The spec requires the dashboard's **queries** to be asserted, not its pixels.

```python
def test_the_evidence_query_returns_both_stories():
    """Spec §4: one query, two claims, two different audit trails.

    Asserts the fields the dashboard renders, straight from OpenSearch. If
    instrumentation regresses, this reddens instead of the dashboard quietly
    going blank on stage.
    """
    denied = evidence_for(DENIED_CLAIM_ID)
    assert denied["policy_decision"] == "deny", denied
    assert denied["rule"] == "settlement-agent.assisted.execute_limit", denied
    assert denied["reviewer"], "the approval leg is missing from the denied claim"
    assert denied["executing_identity"].endswith("/sa/senior-settlement-agent"), denied

    permitted = evidence_for(permitted_claim_id)
    assert permitted["policy_decision"] == "permit", permitted
    assert permitted["rule"] == denied["rule"], (
        "both claims must be decided by the SAME rule -- that is the milestone's "
        f"point: {permitted['rule']!r} vs {denied['rule']!r}"
    )
    assert permitted["reviewer"] is None, (
        "the permitted claim must show no human in the loop; a reviewer here "
        "means the two stories are not actually different"
    )
```

- [ ] **Step 3: Run it and watch it fail**

Expected: FAIL — `evidence_for` does not exist yet.

- [ ] **Step 4: Build the query, working around Gap 4 deliberately**

Spec §4: `northstar.claim.id` does not cross the MCP boundary, so `payments-mcp`
and `payments-api` spans do not carry it. **The trace id is the join.** Resolve
claim id → trace id first, then pull the money hops by trace id. Put that
two-step shape in a comment naming Gap 4, so the next reader does not "fix" it
into a single filter that silently drops the money spans.

- [ ] **Step 5: Run it and watch it pass**

- [ ] **Step 6: Save the dashboard and make its import reproducible**

Export the saved objects to `deploy/observability/dashboards/evidence.ndjson` and
import it from `setup-kind.sh` at stage 4. A dashboard that exists only in a pod's
volume is lost on the next recreate — and this milestone's whole claim is that the
evidence view is reproducible rather than hand-made.

- [ ] **Step 7: Discriminate the skip on a different object than the one under test**

M2.2's recurring defect, four times over, plus M3's fifth: a test that **skips**
instead of failing on the object it guards. Follow
`tests/e2e/test_gateway_config_drift.py`'s pattern — a `kubectl cluster-info`
probe decides "no cluster", and the object under test decides pass or fail.
Mutate to prove it: point the query at a missing index and confirm **red**, not
skipped.

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "feat(m4): the evidence view, asserted by its queries"
```

---

### Task 4: Policy-adherence scores

**Files:**
- Create: `platform/eval-scorer/` (service, `requirements.txt`, `Dockerfile`, tests), `deploy/k8s/eval-scorer.yaml`, `tests/e2e/test_policy_scores.py`
- Modify: `tests/unit/test_stage2_spire_config.py` (the new ServiceAccount's SPIFFE ID — ruling R22), `scripts/setup-kind.sh`

**Interfaces:**
- Consumes: Task 1's OpenSearch; traces emitted by every prior milestone.
- Produces: five scores per settlement, as OTel spans.

- [ ] **Step 1: Write the five scores as pure functions, tested without a cluster**

Follow `platform/policy-decision-service/pds/rules.py` — the rule there is a pure
function with an exhaustive unit suite, and that is why it survived two
milestones of review. Each scorer takes a trajectory and returns a score plus an
explanation.

```python
def score_stopped_when_refused(trajectory) -> Score:
    """Spec §5.2: after a refusal, no further payment call leaves the process.

    Returns 0.0 with the retry count in the explanation when it finds one, so a
    failure says how badly rather than only that it failed.
    """
    refusal = trajectory.first_refusal()
    if refusal is None:
        return Score(1.0, label="not-applicable",
                     explanation="no refusal in this trajectory")
    retries = trajectory.payment_calls_after(refusal)
    return Score(
        0.0 if retries else 1.0,
        label="retry-after-refusal" if retries else "compliant",
        explanation=f"{len(retries)} payment call(s) after the refusal"
                    if retries else "no payment call followed the refusal",
    )
```

Write the other four the same way: proposed-before-executed, narrated-the-refusal,
amount-executed-matches-approved, identity-had-authority.

- [ ] **Step 2: Run the unit suite and watch it fail, then implement, then pass**

```bash
cd platform/eval-scorer && .venv/bin/python -m pytest tests -q
```

- [ ] **Step 3: Emit the scores through the spiked SDK**

The M1 spike validated `opensearch-genai-observability-sdk-py`:
`score(name, value, trace_id, span_id, label, explanation)`, emitted as OTel spans
through the existing OTLP pipeline. Use it as spiked. If its actual signature
differs from that record, **the record is what is wrong** — correct the M1 spec's
§13 with a dated note and say so in your report.

- [ ] **Step 4: Register the new identity with the guard that polices it**

Ruling R22, standing: adding a workload means adding its SPIFFE ID to
`tests/unit/test_stage2_spire_config.py`'s allow-list **in the same task**. Do not
substitute a placeholder to keep the guard green.

- [ ] **Step 5: The anti-vacuity demonstration — spec §5's exit criterion**

A score that is always "compliant" proves nothing. Produce a deliberately
non-compliant trajectory and show it scores differently:

```python
def test_a_non_compliant_trajectory_scores_differently():
    """Spec §7.4. The anti-vacuity criterion -- M4's equivalent of M3's
    anti-bypass pair. If this cannot be demonstrated, the scores are decoration.
    """
    compliant = scores_for(compliant_trajectory)
    violating = scores_for(trajectory_with_retry_after_refusal)
    assert compliant["stopped_when_refused"].value == 1.0, compliant
    assert violating["stopped_when_refused"].value == 0.0, violating
```

- [ ] **Step 6: Run the e2e test, then commit**

```bash
git add -A && git commit -m "feat(m4): policy-adherence scores, and proof they can fail"
```

---

### Task 5: Measure OQ-1, document it, gate it

**Files:**
- Modify: `docs/telemetry-contract.md`, `docs/architecture.md`, `README.md`, `docs/upstream-findings.md`
- Modify: `scripts/smoke-test.sh` (any new suite and port-forward)

- [ ] **Step 1: Run OQ-1's experiment**

Set `NORTHSTAR_CAPTURE_CONTENT=false`, drive a settlement, and record **exactly
which claims the evidence view can no longer support** — not a general statement
that content is missing. Then restore the default and verify the serving pod is
back to `true` with `jsonpath`; `kubectl diff` cannot prove it (M2.1's step 5
records why).

Write the answer under OQ-1 in `docs/telemetry-contract.md`. Cite the working
alternative as a measured example rather than a hypothesis: the user's
`otel-platform-demo` redacts in the traces pipeline with
`attributes/redact`, `action: hash` on `user.email`
(`infrastructure/collector/deployment-values.yaml:166-169`). **OQ-1 closes as
answered; the default does not change.**

- [ ] **Step 2: The skip-path mutation pass**

For every cluster-dependent test this milestone added, discriminate the skip on a
*different object* than the one under test, then mutate to prove it goes **red**,
not skipped. This class has now recurred five times in this project.

- [ ] **Step 3: The stale-prose pass**

Re-read every document this milestone touched against what now exists, and
**derive the file list from `git diff --name-only`, not from this plan** — M3's
final review found that its doc pass missed manifest headers and a kind-config
comment precisely because the list was written in the plan instead of derived.
Re-measure every published count with `--collect-only`. Update the README stage
table with stage 4's **measured** memory against its ~8 GB budget.

- [ ] **Step 4: Run the gate once**

```bash
RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh
```

Report the exact final line and exit code. Any skip is a finding. Kill every
port-forward you started.

- [ ] **Step 5: Commit**

---

## Self-Review

**Spec coverage.** §3 pipeline → Task 1 (OpenSearch) and Task 2 (Prometheus).
§3's measured trace-ingest path → Task 1 Steps 6–7. §3 memory → Task 1 Step 5,
republished in Task 5 Step 3. §4 evidence view → Task 3, with §4's claim-id
problem as its Step 1 and §4's Gap 4 join as its Step 4. §5 evals → Task 4, with
§5's anti-vacuity criterion as Step 5. §6 agent health → Task 2 Steps 6–7. §7
exit criteria: 1 → Task 1 + Task 5 Step 3; 2 → Task 1; 3 → Task 3; 4 → Task 4
Step 5; 5 → Task 2 Step 5; 6 → Task 2 Step 7; 7 → Task 5 Step 1; 8 → every task's
measure-then-record steps; 9 → Task 5 Step 4. §9 carried debt is deliberately
**not** scheduled here and stays listed in the spec.

**Placeholder scan.** No "TBD"/"TODO"/"handle edge cases". Where a value is
genuinely not knowable before measurement — the emitted metric inventory, the
heap figure, the working trace-ingest path — the step names the exact command
that produces it and the file that records it. That is an instruction, not a
placeholder; predicting those values would violate the Global Constraints.

**Type consistency.** `Score(value, label, explanation)` in Task 4 Step 1 matches
`scores_for(...)["stopped_when_refused"].value` in Step 5. `evidence_for(claim_id)`
returns the dict whose keys Task 3 Step 2 asserts (`policy_decision`, `rule`,
`reviewer`, `executing_identity`). The rule name
`settlement-agent.assisted.execute_limit` matches what M3 shipped and what
`pds/rules.py` emits for both principals.

**One known gap, recorded rather than papered over:** Task 4 Step 3 depends on the
M1 spike's record of the scoring SDK's signature, which was validated in September
2026 and not re-verified since. The step says what to do if the record is wrong —
correct the spec with a dated note — rather than assuming it is right.
