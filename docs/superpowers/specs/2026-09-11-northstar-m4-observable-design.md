# Northstar M4 "Observable" — design

Status: design approved 2026-09-11, not yet planned or implemented.
Base: `main` at `7ba222c` (M3 merged and tagged `m3-verified`, plus the
dependency-update round that followed it).
Predecessor spec: `docs/superpowers/specs/2026-09-10-northstar-m3-supervised-design.md`.

**The line this milestone earns on stage:**

> *"Can we prove why €5,800 was paid?"*

---

## 1. What M4 is

M1 connected the system. M2 interposed a platform that decides. M3 put a human
in the loop when the platform refuses. Each of those produced telemetry as a
by-product of being built correctly — a single correlated trace carrying the
model's reasoning, the policy decision, the human's approval and the ledger's
record of what moved.

M4 is the milestone that **collects on that**. The claim is not "we added
observability". It is that the audit trail already exists, because of how the
system was built, and M4 makes it queryable.

The demonstration is an **evidence view**: one saved dashboard that
reconstructs, from telemetry alone, why a payout happened — for two claims
whose stories differ completely. €5,800 was paid because the model proposed it
and the platform permitted it under a rule, with no human involved. €6,200 was
paid only after the platform refused it, a person read the refusal and
approved, and a *different identity* executed it. Same query, same fields, two
audit trails.

**The decisive property: no new application code.** If the evidence view
requires a bespoke surface written for the demo, a reviewer can fairly say the
audit trail was manufactured for the occasion. A saved dashboard over spans the
system already emits says something stronger — that this is a property of how
the platform is built, not a feature someone added. This is the same argument
§2 of the M2 spec made about interposition, applied to observability.

---

## 2. Scope, and what was rejected

Decided in the design conversation of 2026-09-11, recorded with the reasoning so
a later reader can see what was traded:

| Decision | Rejected alternative | Why |
|---|---|---|
| Evidence view is the headline | The eval loop; platform SLOs | The line *"Can we prove why €5,800 was paid?"* was written into the M1 spec's milestone table and is an **audit** question. Evals answer a different one — *was it a good decision* — and make a weaker headline for a platform talk. |
| A saved OpenSearch dashboard, no new app code | A custom audit surface like `reviewer-ui` | A bespoke screen invites "you built that for the demo". See §1. |
| Policy-adherence scores, deterministic | LLM-as-judge on trajectory quality | Reproducible on stage, costs no tokens, and grades the property M2 and M3 spent two milestones building. **A judge is not ruled out** — the user's instruction was *"let's start with policy and then see if it makes sense to add a judge."* If M4 lands and the shape holds, a judge is M5's option, not M4's obligation. |
| OpenSearch **beside** Jaeger | Replacing Jaeger | Every earlier milestone's demo and several e2e tests read Jaeger's API. Replacing it is a large blast radius for a milestone whose headline is elsewhere. |
| OQ-1 measured and written up, default unchanged | Redaction as a demo beat | Keeps M4 at three things. The redaction story is real and worth having, but it is a fourth build in the stage with the tightest memory budget. |
| Prometheus included | Deferring it | The stage table promises it, and §6 turns it from scenery into a real question about what an agent platform can actually measure. **"Minimally" was the word used when this was decided, and it turned out to be true of only half the work:** routing metrics is one exporter block, but nothing in this system emits any metric today, so *producing* them is the actual milestone content. The decision stands; its size was misjudged, and §6 is the correction. |

---

## 3. Architecture

```text
                   every service, unchanged
                            │ OTLP
                            ▼
                  OpenTelemetry Collector
                            │
        ┌───────────────┬───┴───────────┬──────────────┐
        ▼               ▼               ▼              ▼
     Jaeger         OpenSearch      Prometheus      Dash0
   (unchanged,     (traces: the    (metrics via    (optional,
    span waterfall  evidence view   OTLP direct)    unchanged)
    for every       and the scores)
    prior demo)
```

**No application code changes for the destinations.** A destination is
collector configuration; the Dash0 export already proved that pattern in M1 and
nothing about it changes here. The application-code work in this milestone is
elsewhere — see §6 — and it is about *producing* metrics, not routing them.

### The trace-ingest path is to be measured, not assumed

This is the first task, for the same reason M2 made "establish ExtMCP's real
deny shape" its first task: everything downstream depends on it, and a spec
that assumes it would be resting on a prediction.

**Prior art, read during design and the reason this is flagged at all.** The
user's own `otel-platform-demo` repository
(`/Users/kaspernissen/dash0/demos/otel-platform-demo`,
`infrastructure/collector/deployment-values.yaml`) runs a collector fanning out
to Dash0, Jaeger, Prometheus and OpenSearch — and its OpenSearch branch carries
**logs only**:

- `traces` → `otlp/dash0`, `otlp/jaeger`
- `logs` → `otlp/dash0`, `forward/opensearch` → `logs/opensearch` →
  `opensearch/log`
- the OpenSearch branch runs a `resource/opensearch-compat` processor whose
  comment is *"the same enriched logs, **minus the keys OpenSearch cannot
  map**"*

So the proven path there does not cover traces. **Task 1 reuses that exporter
against the traces pipeline first**, per the user's instruction — *"reuse what
you can, add Data Prepper if it's needed to send traces to OpenSearch"* — and
escalates to Data Prepper (OpenSearch's own documented trace-analytics route)
only if the reuse is measured insufficient. Record which one shipped and why.

Two further things worth stealing from that reference, both already reasoned
through there:

- **The `forward/` connector pattern.** Tee a branch off the primary pipeline
  rather than re-declaring receivers and processors, so the branch cannot
  silently drift when the chart changes what it injects.
- **Prometheus over OTLP.** `otlphttp/prometheus` pointed at
  `/api/v1/otlp` — no scrape config, no Prometheus exporter, no application
  change. This is the route M4 takes.

### Memory is on the critical path

The M1 spec budgets stage 4 at **~8 GB** on a 16 GB Mac, and OpenSearch is the
expensive tenant. Heap settings are measured and recorded, not guessed, and the
measured figure goes in the README's stage table. If stage 4 cannot fit, that
is a finding worth publishing, not a reason to quietly drop a component.

---

## 4. The evidence view

A saved dashboard. Two claims, side by side.

| | the €5,800 claim | `CLM-1842` at €6,200 |
|---|---|---|
| Model proposed | yes | yes |
| Policy decision | **permit**, under `…assisted.execute_limit` | **deny**, same rule |
| Human involved | none | reviewer approved |
| Executed by | `settlement-agent` | `senior-settlement-agent` |
| Ledger | `executed` | `refused` → `executed` |

Every field above already exists in telemetry, emitted by M1–M3 and documented
attribute-by-attribute in `docs/telemetry-contract.md`: `gen_ai.*` on the chat
spans, `policy.decision` with its rule/limit/requested/caller-identity set,
`review.decision` with reviewer and elapsed time, and the ledger read-backs.
**M4 adds no span and no attribute to make this work.** If it turns out a field
is missing, that is a finding to record before it is a thing to add.

### The €5,800 claim has no stable id, and M4 has to settle that

**Measured 2026-09-11:** `CLM-1842` is a fixed constant
(`tests/e2e/test_policy_denial.py:124`, and the customer UI pre-fills it), but
the permitted €5,800 path uses a **generated** id —
`f"CLM-PERMIT-{uuid.uuid4().hex[:8]}"` (`:412`). So there is no stable claim to
point a saved dashboard at for the half of the story this milestone's line is
actually about.

Two ways out, and the choice belongs in the plan rather than here because it
should be made against the dashboard that exists by then: give the permitted
claim a fixed fixture id the way `CLM-1842` has one, or parameterise the
dashboard on claim id so the operator selects the run. The first is better for
a live demo and worse for a test suite that must not collide between runs — a
tension M3 already hit and solved with a per-run unique reviewer id, and that
precedent should be read before deciding.

### Gap 4 is the join, and M4 does not close it

`northstar.claim.id` does not cross the MCP boundary — Gap 4 in the telemetry
contract, baggage does not propagate through FastMCP — so `payments-mcp` and
`payments-api` spans do not carry it. A claim-id filter alone will not assemble
the whole story; **the trace id is the join** that reaches the money hops.

This is deliberate. Closing Gap 4 means changing baggage propagation in the
money path, which is a milestone's worth of risk for a milestone whose headline
is elsewhere. The dashboard is built around the constraint and the constraint is
documented where the dashboard is defined.

### How it is tested

**Its queries, not its pixels.** A test asserts that the queries the dashboard
is built from return the expected fields for both claims — so a regression in
instrumentation reddens a test rather than being noticed on stage. Screenshot
or layout assertions are explicitly out of scope.

---

## 5. Evals — policy adherence, scored from the trace

Scores are emitted via the SDK the M1 spike already validated
(`opensearch-genai-observability-sdk-py`:
`score(name, value, trace_id, span_id, label, explanation)`), which rides the
existing OTLP pipeline as spans. They therefore land wherever traces land, and
the optional Dash0 export receives them at no extra cost — verified in the
spike, recorded in the M1 spec §13.

What is scored, per settlement:

1. The agent proposed before it executed.
2. When refused, it stopped — no retry loop.
3. When refused, it narrated the refusal to the claimant.
4. The amount executed matches the amount approved.
5. The identity that executed had authority for that amount.

### The objection this must answer, in the spec rather than in review

Those five properties are already asserted by the e2e suite. A reviewer can
fairly ask whether this is the test suite with extra steps — the same shape of
objection the M2 spec anticipated about policy being "an if-statement with
extra steps", and it deserves the same direct answer.

**Tests assert at build time, against a fixed scenario, and their output is a
pass or a fail in a terminal. Scores attach a judgment to every live
trajectory, and land in the same store as the trace they judge, queryable
beside it.** The difference is visible the moment a trajectory nobody wrote a
test for runs: the suite says nothing, and the score is still there. If M4
cannot demonstrate that difference, the evals have not earned their place and
the milestone should say so.

### The anti-vacuity criterion

A score that is always "compliant" proves nothing. M4's equivalent of M3's
anti-bypass pair: **a deliberately non-compliant trajectory must score
differently from a compliant one**, demonstrated, not asserted.

---

## 6. Agent health — and the question it actually asks

The stage table promises Prometheus. Routing metrics is one exporter block
(§3). **Producing them is not, and that is the finding this section exists to
surface.**

**Measured 2026-09-11, during design:** no service in this system emits metrics
at all. There is no `MeterProvider` in any Python or Go service, and no
`OTEL_METRICS_*` variable in any manifest. `deploy/observability/collector-config.yaml`
does declare a `metrics` pipeline with `otlp` and `prometheus/spire` receivers —
exporting to `debug` only. So today the system receives SPIRE's scrape and
discards it, and emits nothing of its own.

So M4's real question is not *"can we draw a dashboard"* but:

> **Once a MeterProvider exists, which GenAI agent metrics does anything
> actually emit?**

The conventions to measure against — noting that **GenAI semantic conventions
have moved to their own repository**, `open-telemetry/semantic-conventions-genai`,
which also carries the MCP conventions (this repo's telemetry contract still
cites the main semconv repo and should be corrected):

| Metric | Type | Unit | Stability |
|---|---|---|---|
| `gen_ai.invoke_agent.duration` | Histogram | `s` | Development |
| `gen_ai.invoke_agent.inference_calls` | Histogram | `{inference_call}` | Development |
| `gen_ai.invoke_agent.tool_calls` | Histogram | `{tool_call}` | Development |
| `gen_ai.execute_tool.duration` | Histogram | `s` | Development |
| `gen_ai.client.token.usage` | Histogram | `{token}` | Development |
| `gen_ai.client.operation.duration` | Histogram | `s` | Development |

**Every one is Development stability.** This project scopes claims to versions,
and that qualifier is load-bearing: a metric at Development can be renamed
upstream, and a sentence written without the qualifier becomes false at the next
bump with nothing to catch it.

The standing constraint applies with full force. PydanticAI's *spans* are
documented span-by-span in the telemetry contract; its *metrics* have never been
looked at. Whatever it emits is what gets published — and whatever it does not
emit is a gap recorded in `docs/telemetry-contract.md` and, if it is a defect
rather than a scope boundary, a finding in `docs/upstream-findings.md`. **No
metric is hand-rolled to fill a hole in a framework's coverage.** Our own
components (`policy-decision-service`, `reviewer-ui`, the agents' owned spans)
may author metrics, on the same terms their spans are authored under.

The honest expectation, stated now so the milestone is not judged against a
fantasy: this is likely to be a **gap-reporting** section rather than a
dashboard-full-of-green section, and that is the more interesting result for a
talk about what agentic platforms can actually measure today.

---

## 7. Exit criteria

1. `--stage 4` brings up OpenSearch, OpenSearch Dashboards and Prometheus, with
   **measured** memory recorded against the ~8 GB budget in the README's stage
   table.
2. Traces reach OpenSearch and are queryable, by whichever path task 1
   measured — with the reuse-first, Data-Prepper-if-needed decision recorded.
3. A saved dashboard reconstructs both claims' audit trails **with no new
   application code**, and its queries are asserted by a test.
4. Policy-adherence scores land per settlement as OTel spans, and **a
   deliberately non-compliant trajectory scores differently from a compliant
   one**.
5. Prometheus receives metrics over OTLP with no application change to the
   routing and no scrape configuration.
6. The GenAI agent metrics in §6 are measured against what the frameworks
   actually emit once a MeterProvider exists, and every one is published as
   emitted or recorded as a gap.
7. OQ-1 is measured and written up — exactly which claims the evidence view can
   no longer support at `NORTHSTAR_CAPTURE_CONTENT=false` — with the default
   left unchanged and restored.
8. Every telemetry attribute and metric name published was measured, not
   predicted.
9. The full gate passes with browser tests enabled.

---

## 8. Out of scope

OpenFeature/flagd autonomy profiles (M5); Crossplane, Argo CD and Backstage
(M6); Kyverno; the M1.3 fan-out to four agents; Linkerd, still deferred with its
own attached experiment. An LLM-as-judge eval, deliberately deferred rather than
rejected — see §2.

---

## 9. Carried debt M4 inherits and does NOT clear

Recorded so it is not quietly missed, and so the next spec inherits an accurate
list rather than a stale one. **Clearing any item means striking it off every
list that names it** — the lesson M3 recorded after that failure recurred four
times.

- **M2 criterion 7** — direct agent → model-provider egress is still allowed.
  Closing it means routing model traffic through agentgateway's LLM path. Owned
  by no spec since M2.
- **`senior-settlement-agent` serves an agent card naming the junior**
  (`contracts/agent-cards/settlement-agent.json` is baked into the shared
  image). Latent: nothing does card discovery today, but a future client
  following the card would route an approval to the junior identity and read the
  resulting policy denial as the policy failing.
- **`get_payment` is unlimited and unscoped**, where `refuse_payment` was
  caller-scoped in M3's ruling R12b. Any holder of a valid gateway-audience SVID
  can read any ledger record, including the narrative.
- **`a2a-sdk` 0.3.7 → 1.x is a protocol migration, not a bump.** Measured
  2026-09-11: 1.1.2 does **not** fix Finding 1 (no `propagate`, `traceparent` or
  `TextMapPropagator` anywhere in the package; all 17 instrumented modules still
  carry a bare `@trace_class`), and it moves to `SendMessage` over
  `message/send` across 13 files, a protobuf `AgentCard`, and no
  `A2AStarletteApplication`. It needs its own milestone.

---

## 10. Evidence this spec rests on

Recorded so a later reader can tell what was measured from what was reasoned.

**Measured during this design conversation, 2026-09-11:**
- No service emits metrics: no `MeterProvider` in any Python or Go source, no
  `OTEL_METRICS_*` in any manifest, and the collector's `metrics` pipeline
  exports to `debug` alone.
- The GenAI metric inventory in §6, read from
  `open-telemetry/semantic-conventions-genai` — including that the conventions
  have moved out of the main semconv repository, and that every agent metric is
  at Development stability.
- The user's `otel-platform-demo` collector sends **logs**, not traces, to
  OpenSearch, and reaches Prometheus over OTLP at `/api/v1/otlp`.

**Established earlier in this project, relied on here:**
- `opensearch-genai-observability-sdk-py` emits scores as OTel spans through the
  existing pipeline (M1 spec §13, spike-verified).
- Gap 4: `northstar.claim.id` does not cross the MCP boundary
  (`docs/telemetry-contract.md`).
- Stage 4's ~8 GB budget (M1 spec §12).

**Not yet established, and gating §3:** whether the collector's `opensearch`
exporter can carry traces usefully, or whether Data Prepper is required.
