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

**Corrected 2026-09-15 (M6 Task 1), M6 clause only:** M6 is Backstage over the
`FeatureFlag` resource M5 introduced, not Crossplane/Argo CD/Backstage —
M5's own spec dropped the first two (`docs/superpowers/specs/
2026-09-15-northstar-m6-golden-path-design.md` §1). Left as written above per
this repo's standing convention for historical specs; not rewritten.

---

## 9. Carried debt M4 inherits and does NOT clear

Recorded so it is not quietly missed, and so the next spec inherits an accurate
list rather than a stale one. **Clearing any item means striking it off every
list that names it** — the lesson M3 recorded after that failure recurred four
times.

- **M2 criterion 7** — direct agent → model-provider egress is still allowed.
  Closing it means routing model traffic through agentgateway's LLM path. Owned
  by no spec since M2.
- **Network policy stops at the agent plane, and the platform plane has none**
  (ruling M4-R17; measured 2026-09-13 with `cilium-dbg endpoint list -o json`
  on the live cluster, reading each endpoint's realized `policy-enabled`, not
  read off the manifests). Of `northstar-platform`'s six Cilium endpoints,
  exactly one — `policy-decision-service` — is enforcing anything, and it is
  `ingress` only. `agentgateway`, `eval-scorer` and the three SPIRE workloads
  (`spire-server`, `spiffe-csi-driver`, `spiffe-oidc-discovery-provider`) are
  `enforcement=none`. **No endpoint anywhere in the cluster has egress
  enforcement except the five in `northstar-agents`**, and `payments-api` —
  the component that holds the money — has no policy of any kind. This is
  **not a regression**: M2's criterion was agent-plane egress, and
  `deploy/platform/cilium/`'s own header says so. It has simply never been
  written down beside the criterion it is the complement of, which is how a
  reader comes away believing the mesh is enforced. Owned by M5 alongside
  criterion 7; the two are the same piece of work.
- **Two workloads share one SPIFFE ID** (ruling M4-R18; measured 2026-09-13 in
  `spire-server entry show`). `claims-agent` and `customer-ui` both run as
  `sa/default` in `northstar-agents`, and SPIRE holds **two separate
  registration entries that both issue
  `spiffe://northstar.local/ns/northstar-agents/sa/default`** — so an
  authorization decision made on that ID cannot tell the browser-facing UI from
  the agent that files claims. In a repo whose thesis is workload identity that
  is worth stating out loud. Three more entries share
  `.../ns/observability/sa/default` the same way (jaeger, opensearch,
  otel-collector), and `payments-api` and `payments-mcp` each run as their
  namespace's `default`. Pre-existing, not M4's, and not fixable by M4: giving
  each workload its own ServiceAccount changes every `ClusterSPIFFEID`
  selector, the PDS's caller table and the gateway's audience list at once.
  Owned by M5.
- **`pytest` is deliberately one release behind, and this is where that is
  written down** (ruling M4-R20). Seven `requirements.txt` files pin
  `pytest==9.0.3` and six `test_pins.py` assert it; **9.1.1** is the latest
  release, confirmed against PyPI on 2026-09-13. The standing rule in this repo
  is that pins sit at the latest released version, so without this entry the
  next reader finds seven stale pins with no explanation and reads drift where
  there was a decision. The decision: the bump is orthogonal to M4's spec, it
  touches seven requirements files and six pre-existing virtualenvs — and
  installing into an existing service venv is forbidden by the standing
  constraint, because their contents are pinned and asserted — and closing a
  milestone is the wrong moment to re-pin the test runner that every one of
  those assertions executes under. Cost if wrong: one release behind on a test
  runner for the length of a merge. **Scheduled first after M4 merges. Owned by
  M5.** Recorded here rather than in the execution ledger because
  `.superpowers/` is git-ignored, and M4's final review found this item closed
  in prose and open in fact — which is the whole argument for §9 existing.
  Treat "recorded in the ledger" as "not recorded" once a branch merges.
- **Two infrastructure pins are one patch release behind, for the same reason
  and with the same owner** (M4 final review, finding L1; both verified against
  upstream on 2026-09-14, not read off a local `helm search` cache, which is
  how a stale index produces a confident wrong answer):
  - `otel/opentelemetry-collector-contrib` pinned at **0.158.0**; latest
    release **0.160.0** (`opentelemetry-collector-releases`). Five files carry
    the literal — `.env.template`, `deploy/observability/collector.yaml`,
    both collector ConfigMaps, and `tests/unit/test_metrics_wiring.py`, which
    asserts it — plus the captured collector output in
    `docs/telemetry-contract.md`, which is a **record of a run** and must not
    be edited to a version that run did not use.
  - `spiffe/spire` chart pinned at **0.30.1**; latest **0.30.2**. Chart-only:
    both charts declare `appVersion: 1.15.3`, so no SPIRE binary moves. Nine
    files name the chart version, one of them (`test_stage2_spire_config.py`)
    asserting it. `spire-crds` 0.6.1 **is** the latest and is not debt.

  Not bumped here because each bump is only real once the cluster runs it, and
  this branch is forbidden from recreating the cluster; a pin changed in the
  tree but never rolled out is worse than one left alone, because the tree then
  claims a version nothing has executed. Cost if wrong: one patch release
  behind on a collector image and a Helm chart for the length of a merge.
  **Scheduled with the `pytest` bump above, on the same first-after-merge
  cluster rebuild. Owned by M5.**
- **A batch of six fixes that each land *inside* the gate-row staleness guard's
  `gate_paths`, and therefore share one gate run** (M4 re-review, findings V2,
  V5, V9, V11, V13 and V12's first item). Recorded together because the reason
  none shipped with the final-review fix wave is the same for all six, and it
  is measured rather than asserted: `tests/unit/test_telemetry_contract_counts.py::test_a_predicted_gate_row_is_labelled_as_one`
  compares the working tree against the commit the published gate row names,
  over `agents apps contracts deploy platform scripts tests`. Editing any of
  those re-labels the row `PREDICTED, NOT MEASURED` and demands a fresh
  `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh`. Confirmed by mutation: a
  one-line change to the guard's own path list reddens that test naming
  `tests/unit/test_telemetry_contract_counts.py`. Six behaviour-neutral fixes
  are worth exactly one gate run between them, not six — **so they are M5's
  first task, on the same rebuild as the pin bumps above.** In descending
  order of how much they matter:

  1. **The guard under-covers the gate it protects** (V2, the only one that is
     not cosmetic). `gate_paths` omits `systems` and `tools`, and the comment
     above it asserts *"`systems/` and `tools/` are not executed by the gate"*
     — which is false: `scripts/smoke-test.sh` runs
     `(cd "$ROOT/systems/payments-api" && go test ./...)` and
     `(cd "$ROOT/tools/payments-mcp" && pytest tests -q)`, 31 and 16 tests. So
     a change to either leaves the published row labelled **measured** with no
     warning. Demonstrated on a real commit: `716d70c` changed
     `tools/payments-mcp/bootstrap.py`, and the narrow path list reports that
     file **0** times against the widened list's **1**. Fix: add `"systems"`
     and `"tools"`, delete the false comment, and mutate to watch the wider set
     redden. A guard that silently under-covers is worse than none, and this
     one's job is protecting the MEASURED row the merge rests on.
  2. **The zero-skip check is blind to two skip shapes** (V5).
     `scripts/smoke-test.sh`'s `grep -cE "^[^ ]+::[^ ]+ SKIPPED"` misses a
     collection-time module skip (which prints no `path::test` line at all —
     the largest possible instance of the failure the check exists for) and any
     parametrised id containing a space. Both were reproduced empirically on a
     scratch project: 3 real skips, 1 counted. An id with a space already
     exists in this tree
     (`test_no_stray_core_networkpolicy_shadows_the_cilium_one[kind: NetworkPolicy]`),
     though that test cannot skip. Worse, `tests/unit/test_smoke_test_gate.py`
     now pins the *literal* fragile regex, so the shape is locked in. Fix:
     anchor on pytest's own summary line (`N skipped`) — the comment's stated
     objection, that a bare `grep SKIPPED` matches the word inside a test name,
     does not apply to the summary line — and re-point the unit test at the
     property rather than at the regex.
  3. **The `absent_or_fail` shape walk has no vacuity control** (V9).
     `tests/unit/test_kube_helper.py`'s AST walk would pass green forever if
     `_reads_returncode` ever stopped matching — a refactor, a renamed
     attribute — guarding nothing. Every comparable guard in this repo has a
     positive control (`test_an_untruncated_read_is_not_refused`,
     `test_mutation_protocol.py`); this one does not, and the fix wave's
     mutation round proves it live *today* but nothing in the committed tree
     pins that. Fix: assert the walk finds a known-offending synthetic source.
     Two smaller items in the same file: `_RAW_RETURNCODE_SKIPS_ALLOWED` holds
     **11** entries against the fix report's claim of 8 "with a reason each"
     (the reasons are two group comments), and nothing detects a *stale*
     allow-list entry — the rename→RED direction is proven, the rot direction
     (an allow-listed probe later acquiring a genuine existence check) is not.
  4. **`start-demo.sh` refuses to start when any of its ports is already held,
     and tears down the forwards it just created on the way out** (V11 and the
     re-review's §6). Refusing is *right* — ruling R20 measured that a dead
     `kubectl port-forward` keeps its listening socket, so "something is bound"
     is not evidence, and a script that owns forwards must be able to say
     *these are mine and they work*. The error text is also fine as written: it
     names the conclusion first and offers `lsof` as the way to see *who*. Two
     things are wrong. **The script already has the safe alternative and does
     not reach for it:** `verify_existing_surface` (`lsof` **plus** a real HTTP
     request) is exactly the "not mine, but verified live" check, and is what
     reviewer-ui already uses — applying it to a foreign holder of 18080 or
     5601 accepts precisely the case an operator can verify by hand and weakens
     nothing R20 measured. **And the real cost is the teardown:** under
     `set -e` the `exit 1` fires the EXIT trap, which kills every forward the
     script had already established, so an operator with one stray forward ends
     up with fewer than they started with. That is review finding F8's
     consequence recurring through a different door — F8 was fixed for "the
     Service does not exist" and not for "the port is already held". This was
     hit for real during M4's second gate run: two pre-existing forwards held
     18080 and 5601, the script refused, and the gate's forwards had to be
     established by hand. Also in this file and cosmetic: `:128` says "the
     **three** stage-gated surfaces" and the same commit added a fourth
     `service_exists` gate (eval-scorer).
  5. **The gate-row guard's own docstring names the wrong mechanism** (V13).
     `test_a_predicted_gate_row_is_labelled_as_one`'s docstring says it
     *"compares that to `HEAD`"*; the code and its own inline comment say the
     opposite and explain at length why comparing `HEAD` would have been
     satisfied by the fix wave itself. The docstring describes the defect, not
     the fix — on the merge's load-bearing guard.
  6. **A duplicated clause and a verbless fragment in
     `deploy/observability/collector-config.yaml`** (V12, first item). The
     comment explaining the milestone's central mechanism reads
     *"…ATTR_DECISION and ATTR_RETRYABLE in …/pds/telemetry.py, **as
     ATTR_DECISION and ATTR_RETRYABLE** — cited by NAME… **In the comment
     explaining the milestone's central mechanism.** OpenSearch expands dotted
     keys…"*. The facts are right; the prose broke while being corrected.
- **`eval-scorer` has no completeness notion of its own** (M4 re-review, §2
  residual and finding S-a). §7.4 is whole — the scores differ where they must,
  and the truncation refusal H1 added is deployed and verified against the live
  pod — but "is this trajectory finished?" lives in **prose** (the walkthrough
  tells a human to poll with `"emit": false` first) and in the **gated e2e
  test** (`QUIESCENCE_S`, then equality on `decisionCount`). It does not live in
  the service. `/score` will answer over a half-indexed trajectory with a
  flattering result and no signal, because OpenSearch is near-real-time and the
  service has no notion of "how much should be here". The cheap shape, if M5
  wants it closed in the service: have `/score` accept an optional
  `expectDecisions` and **refuse rather than answer** when the count is below
  it — the same "no score is emitted" discipline H1 established one function
  away. Related and smaller (S-b): both truncation guards read an *absent*
  field as "nothing was dropped"
  (`aggregation.get("sum_other_doc_count") or 0`, and a `len(hits)` default
  that makes the page check vacuously false), which is "I could not look"
  recorded as "there was nothing to find" — the exact rule `search()`'s own
  error check enforces ten lines away. Defensive rather than live, since
  OpenSearch always sends both. **Owned by M5.**
- **`platform/eval-scorer` has a row in neither component inventory** (M4
  re-review, L9's fifth item). `docs/architecture.md`'s application-components
  table is the application plane and eval-scorer runs in `northstar-platform`;
  the platform-components section opens "none is this project's code", which
  eval-scorer falsifies. Both headings now carry a note saying so, but the
  component still has no row. Deciding which plane owns it, and writing the
  row, is **owned by M5.**
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
- Stage 4's ~8 GB budget (M1 spec §9, "Cluster and deployment", row 5).

**Not yet established, and gating §3:** whether the collector's `opensearch`
exporter can carry traces usefully, or whether Data Prepper is required.
