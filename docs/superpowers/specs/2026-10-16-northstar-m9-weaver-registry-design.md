# Northstar M9 — the telemetry contract as a Weaver registry

Status: approved in conversation 2026-10-16; this document is the written spec.
Builds on M8 (`2026-10-13-northstar-m8-human-in-the-loop-design.md`), branch
`m9-weaver-registry` from `m8-human-in-the-loop` at `7cfa9aa`.

## 1. Why

The demo's message is that what the agents do is **secured and instrumented**,
guardrails stop them changing anything they should not, and rules grant a
measured amount of autonomy. The instrumentation half of that rests on
attributes this project invented, because no OpenTelemetry convention covers a
policy decision or a human review (telemetry-contract Gap 3). Today those names
are Python constants, hand-written guard tests and prose. That is custom
machinery, and the names break OpenTelemetry's naming rules in four places.

M9 replaces the machinery with the tool OpenTelemetry ships for this:
**OTel Weaver**. The registry becomes the one definition of the platform's own
telemetry; Weaver checks the registry, checks the live system against it, and
generates the docs. The demo's evidence step moves from OpenSearch to the trace
and its span attributes in Jaeger.

Decisions made by Kasper (2026-10-16):

- Switch to OTel Weaver.
- OpenSearch leaves the **demo**, not the stack: "I would rather show the traces
  and span attributes."
- Every name we own is explicitly prefixed `northstar.`.
- Approach A: the registry is the **checked contract**. No generated constants.

## 2. Naming rules (from the OTel naming guide)

1. Reuse a semantic-convention name before inventing one.
2. Our own names carry the application prefix `northstar.`.
3. No name we own sits in a namespace we do not own (`policy.`, `review.`, `claim.`).
4. A name is never both an attribute and a namespace.
5. Precise, not generic names.
6. No units in names; base units (seconds) for durations.
7. Closed sets are enums; everything we define is `stability: development`.

## 3. The rename

| Today | M9 | Rule |
|---|---|---|
| span `policy.decision` | span `northstar.policy.decision` | 2, 3 |
| `policy.decision` | `northstar.policy.decision` (enum `permit`, `deny`) | 2, 7 |
| `policy.decision.retryable` | `northstar.policy.retryable` | 4 |
| `policy.rule` | `northstar.policy.rule` | 2, 5 |
| `policy.caller.spiffe_id` | `northstar.policy.caller.spiffe_id` | 2 (no semconv for a SPIFFE ID) |
| `policy.tool.name` | `gen_ai.tool.name` (ref) | 1 |
| `policy.request.amount`, `policy.request.currency` | `northstar.policy.request.amount`, `.currency` | 2 |
| `policy.limit.amount` | `northstar.policy.limit.amount` | 2 |
| `policy.autonomy.level` | `northstar.policy.autonomy.level` (enum `assisted`, `autonomous`) | 2, 7 |
| `policy.autonomy.reason` | `northstar.policy.autonomy.reason` | 2 |
| `policy.ledger.status` | `northstar.policy.ledger.status` (enum `proposed`, `awaiting_approval`, `executed`, `rejected`) | 2, 7 |
| span `review.decision` | span `northstar.review.decision` | 2, 3 |
| span `review.queue`, `review.list` | `northstar.review.queue`, `northstar.review.list` | 2, 3 |
| `review.decision` | `northstar.review.decision` (enum `approve`, `reject`) | 2, 7 |
| `review.elapsed_ms` (int, ms) | `northstar.review.wait.duration` (double, s) | 6 |
| `review.reviewer.id`, `.reviewer.spiffe_id`, `.payment.id`, `.resume_only` | `northstar.review.*` | 2 |
| `review.refused.trace_id` | **removed**: the span link carries it | — |
| span `claim.submit`, `claim.wait` | `northstar.claim.submit`, `northstar.claim.wait` | 2, 3 |
| `northstar.policy.number` (the insurance policy) | `northstar.claim.policy_number` | 5: it is not the authorization policy |
| `northstar.claim.id`, `northstar.payment.refusal_cause` | unchanged | — |
| event `northstar.autonomy_contract.change`, `northstar.contract.*` | unchanged | — |
| metric `northstar.approval.wait.duration` | unchanged | — |
| `feature_flag.version`, `gen_ai.evaluation.*` | ref, unchanged | 1 |

The eval-scorer's `settlement.execute_approved` constant names a span from the
retired senior path. If nothing emits it, it is deleted, not registered.

The table is the intent. The plan's first task produces the **measured**
inventory (every name emitted, by service, read from code and from a live run)
and the registry is built from that; any name found that is not in this table
gets a row ruled by the rules above.

## 4. The registry

`telemetry/registry/`:

- `manifest.yaml`: name `northstar`, `schema_url` with version `0.1.0`, one
  dependency on OTel semantic conventions **v1.44.0** (latest release,
  verified 2026-10-16).
- `policy.yaml`, `review.yaml`, `claim.yaml`, `contract.yaml`, `metrics.yaml`:
  attribute groups, the spans with their kind and required attributes, the
  event, the histogram with its unit and attributes (`northstar.review.decision`,
  `northstar.policy.autonomy.level`).
- Every definition has a one-line `brief` saying what it means in the demo.

Weaver pinned to **v0.28.0** (2026-10-14, latest), as the `otel/weaver:v0.28.0`
image, used both locally (docker) and in-cluster. Plan task 1 verifies, before
writing YAML, whether Weaver's v2 registry format is the recommended one for a
new registry at v0.28.0 (live-check matchers require it); if it is, the
registry uses it.

## 5. The rename wave

One wave, no compatibility period (the demo has no outside consumers).

- Writers: policy-decision-service, reviewer-ui, claims-agent,
  settlement-agent, customer-ui, the Backstage autonomy plugin, payments-api.
- Readers: eval-scorer, `scripts/demo`, `tests/harness`, the tests, the
  OpenSearch evidence view's saved objects (kept working for the longer slot).
- The collector/OpenSearch ingest rename for `policy.decision.retryable` is
  removed: rule 4 removes the collision it existed for.
- `review.elapsed_ms` → seconds: the evidence output and deck show seconds.

## 6. Checks

**Static, every gate run** (`scripts/check-registry.sh`, called by
`scripts/smoke-test.sh`): `weaver registry check`, and regenerate the
attribute tables of `docs/telemetry-contract.md` with `weaver registry
generate` and a markdown template, failing if the committed file differs.
These need the network (they resolve the semconv dependency), so they are not
in `tests/unit`, which stays hermetic.

**Live, in the gate:** a `weaver-live-check` Deployment in `observability`
running `weaver registry live-check --input-source otlp --output http`, bound
to `0.0.0.0` so the collector can reach it. The collector gets one more
exporter (traces, metrics, logs) to it: a copy of everything, never a sample.
Its failure does not affect the other exporters. The gate restarts the pod at
the start of the run, and at the end calls `POST /stop`, `GET /report`,
`POST /shutdown` through a port-forward.

- **Fails** on any `violation` finding about a `northstar.*` name or a group we
  define: unknown name, wrong type, value outside its enum, required attribute
  missing.
- Findings about names we do not own (agentgateway, a2a-sdk, Kyverno, SPIRE,
  framework attributes) are reported at `information` level and do not fail.
- The live-check report is saved under `.demo/` and its counts are recorded in
  the gate row.

**Deleted**, because live-check does their job on the wire: the PDS
`ATTRIBUTES`/`INVENTED_ATTRIBUTES` tuples and the "visibly ours" provenance
guard. Behaviour tests (a deny carries its rule and limit; the approval trace
links to the claim's) stay, with the new names.

## 7. The demo

- `scripts/demo evidence` reads **Jaeger**, not OpenSearch. For each claim of
  the run: the decision span's `northstar.policy.*` attributes, the review span
  (who, decision, `northstar.review.wait.duration`), the outcome, and the
  Jaeger URL of the trace to open.
- Runbook step 5 and the deck point at span attributes in Jaeger.
- One new slide: a registry YAML excerpt beside the same attribute on a Jaeger
  span: *the platform's decision contract is a published schema.*
- Screenshots that show attribute names are retaken.
- The README and `docs/architecture.md` use the new names.

## 8. Out of scope

- Generated constants in any language (approach B/C).
- Removing OpenSearch from the stack, or moving the eval-scorer off it.
- Perses (considered, not decided).
- Proposing `northstar.policy.*` upstream as a semantic convention.

## 9. Done when

- The registry passes `weaver registry check` at v0.28.0 against semconv v1.44.0.
- A gate run is MEASURED green with live-check reporting **zero violations**
  for `northstar.*` names.
- No `policy.*`, `review.*` or un-prefixed `claim.*` name we own is emitted
  (live-check's non-registry statistics show none).
- `scripts/demo evidence` prints the three claims from Jaeger.
- The telemetry-contract attribute tables are generated and match.
