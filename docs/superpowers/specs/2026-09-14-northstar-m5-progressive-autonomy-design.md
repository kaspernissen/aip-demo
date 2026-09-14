# Northstar M5 "Progressive autonomy" — design

Status: design approved 2026-09-14, not yet planned or implemented.
Base: `main` at `5d6457d` (M4 merged and tagged `m4-verified`).
Predecessor spec: `docs/superpowers/specs/2026-09-11-northstar-m4-observable-design.md`.

**The line this milestone earns on stage:**

> *"We didn't change a prompt. We changed a contract."*

---

## 1. What M5 is

M1 connected the system. M2 interposed a platform that decides. M3 put a human
in the loop when the platform refuses. M4 made the whole trail queryable.

Every one of those milestones ran at **one autonomy level**, and it was a
constant: `pds/server.py`'s module-level `AUTONOMY_LEVEL = "assisted"`, passed
on every `PolicyInput`. M5 is the milestone that makes that value come from
somewhere real.

The demonstration is the **€6,200 claim that M3 refuses**. Today it is
proposed, refused, queued for a human, approved by a click, and executed under a
second identity. After M5, with one field changed in one Kubernetes resource, the
same claim settles unattended — and with that field changed back, it refuses
again. No agent is redeployed. No prompt is edited. No image is rebuilt. The
diff is a reviewable change to a named resource.

**The decisive property: the rule engine does not change.** `pds/rules.py`'s
`EXECUTE_LIMITS` has been keyed by `(SPIFFE ID, autonomy level)` since M2.2 and
already carries an `autonomous` row that nothing in this repo can reach. The
comment above `AUTONOMY_LEVEL` says so explicitly and instructs the reader not
to invent a mechanism elsewhere. So M5 changes a **value**, not a shape — which
is what makes the stage line literally true rather than a slogan. This is the
same argument §1 of the M4 spec made about the evidence view: a property of how
the platform was built, not a feature added for the occasion.

---

## 2. Scope, and what was rejected

Decided in the design conversation of 2026-09-14, recorded with the reasoning so
a later reader can see what was traded:

| Decision | Rejected alternative | Why |
|---|---|---|
| Autonomy as originally sketched | Reshaping M5 around M4's carried identity/network-policy debt | The debt is real and load-bearing for the thesis, but it has no stage line of its own, and it would leave the seam M2 deliberately cut unused for another milestone. The debt stays carried, with owners, in §11. |
| Stepped levels, per agent | Fractional/percentage rollout | Deterministic on stage: the same €6,200 claim settles or refuses because the contract says so, not because of a hash bucket. Fractional targeting is truer to production rollout and is a worse demo and a harder test. Not ruled out for later. |
| Open Feature Operator + `FeatureFlag` CRD | flagd Deployment reading a git-tracked ConfigMap; in-process file-mounted evaluation | The canonical OpenFeature-on-Kubernetes story, which suits a reference implementation. Note this does **not** conflict with M6 dropping Crossplane: a flag-definition CRD and a platform-API CRD are different layers. |
| The audit resolves the flag independently | Reading `policy.autonomy.level` off the span being judged; refusing to score under variable autonomy | See §7. Reading the level off the audited span is the vacuity trap `eval-scorer/scorer/authority.py` was written to avoid, one field over. |
| Record the evaluation **both** ways | Log event only; span event only | OTel semconv and OpenFeature Appendix D disagree on the emission shape (§6). Recording both is conformant, does not fight the SDK, and turns the disagreement into a visible artifact. Written up as Finding 13. |
| Logs → OpenSearch, flag events only | Full logs signal (application + container logs, evidence-view panel); no logs backend at all | The collector's `logs` pipeline currently exports to `debug` alone, so events would vanish. Routing it is one exporter block plus a parity assertion. A first-class logs signal is most of a milestone on its own. |
| Six tasks in one milestone | Splitting into M5/M5.1; a standalone pre-M5 fix pass | Against the standing lean 4–5 rule, accepted deliberately: Task 1 is mechanical and Task 4 is a config change. Splitting the carried batch out costs an extra full gate run, which is the exact thing batching it was meant to avoid. |

---

## 3. Architecture

Stage 5 adds the **Open Feature Operator** and **flagd** to `northstar-platform`,
plus **cert-manager** if OFO still requires it (§12 — unverified, and a Task 2
prerequisite check, not an assumption). Nothing else moves. No agent image, no
prompt, no manifest outside the platform namespace.

The MCP call path is unchanged from M3:

```text
settlement-agent ──MCP──► agentgateway ──ExtMCP/gRPC──► policy-decision-service
                                                              │
                                                              └──► flagd
                                                                   (autonomy level
                                                                    for this caller)
```

The PDS gains exactly one lookup, before `decide()` and after the caller's
SPIFFE ID has been extracted — which it already does, for the rule lookup. The
resolved level is passed on the `PolicyInput` that `decide()` already accepts.

**`pds/rules.py` is not modified except for the `rule` literal (§5).** Its table,
its keys and its fail-closed behaviour are inherited intact.

---

## 4. The contract object

A `FeatureFlag` custom resource, git-tracked under
`deploy/platform/openfeature/`, holds the autonomy profiles keyed by SPIFFE ID:

- `spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent` →
  `assisted` | `autonomous`
- `spiffe://northstar.local/ns/northstar-agents/sa/senior-settlement-agent` →
  `assisted`

The SPIFFE ID is the targeting key because identity is already load-bearing in
this system — M2's spec §4 records *"with amount alone the rule still denies on
stage, but SPIRE becomes scenery and M5 has no contract to change."* Targeting
on the same ID the rule table is keyed by keeps that true.

**This resource is the contract.** It is the artifact a human edits, the diff a
reviewer reads, and — with Crossplane dropped from M6 — the object M6's
Backstage will put a UI on. That inheritance is recorded here because five
committed documents currently name a Crossplane `Agent` CRD as the autonomy
profile's eventual home (§11).

---

## 5. The decision path

`AUTONOMY_LEVEL` stops being a module constant and becomes a per-decision
resolution against flagd, keyed on the caller's SPIFFE ID as the evaluation
context.

**Fail closed, in two independent ways.** An unresolvable flag — flagd
unreachable, CR absent, provider error — resolves to `assisted`, the most
restrictive level, never the permissive one. Separately, an *unknown* variant is
already fail-closed by the existing table without new code: `decide()` looks up
`(caller, level)` in `EXECUTE_LIMITS`, misses, and returns `unknown_caller`. A
garbage value in the CR therefore denies. Both properties are asserted, not
trusted.

**The span records which of those happened.** `policy.autonomy.level` stays on
the `policy.decision` span — it is the input the decision actually used, a
property of the whole operation, which is where such a property belongs. The
resolution *reason* is recorded alongside it so a trace distinguishes
"autonomous because the contract says so" from "assisted because we could not
ask". An audit that cannot tell those apart will read a flagd outage as a policy
change.

**The `rule` literal is fixed here, carefully.** `pds/rules.py`'s
`rule="settlement-agent.assisted.execute_limit"` is a literal that mislabels the
`(settlement-agent, autonomous)` row, and its own comment assigns the honest fix
to M5 on the grounds that M5 is the milestone that makes `autonomous` reachable
and therefore observable. The constraint the comment also records must be
honoured: `(senior-settlement-agent, assisted)` deliberately reports the junior's
rule name, because M3's claim is that approval changes *who is asking* rather
than what is allowed, and `tests/e2e/test_supervised.py`'s criterion 4 asserts
that exact string. So the fix must make the `autonomous` row honest **without**
composing the name from `(caller, autonomy_level)` wholesale.

---

## 6. Observing the evaluation — and Finding 13

**Two upstream specifications disagree about how a flag evaluation is recorded,
and both are current.** This is not a library falling short of a convention,
which is what the other twelve findings are; it is two conventions falling short
of each other. It is written up as **Finding 13** in
`docs/upstream-findings.md`, and like the rest it stays local.

- **OpenTelemetry semantic conventions**: an event named
  `feature_flag.evaluation` — a log record with an event name — emitted on every
  evaluation, even when the result is unchanged. The older model that also
  covered spans was retired.
- **OpenFeature specification, Appendix D**: recommends **span events** as the
  preferred emission pattern, while acknowledging OpenTelemetry is "trending
  toward using log-based events".

A second, smaller divergence sits in the same place. On errors, Appendix D maps
the error message to **`error.message`**; the OTel registry defines
**`feature_flag.error.message`** as the active attribute. Both specify
`error.type` alongside it.

**M5 records both**, deliberately: the span event the OpenFeature hook emits
natively — the repo's standing principle is to use the telemetry frameworks
emit rather than invent our own — *and* the semconv `feature_flag.evaluation`
log event. Volume is irrelevant: nothing here is sampled, and a flag resolves a
handful of times per claim.

**Attribute names are the current spellings, and a guard enforces it.** As of
semconv 1.44.0 the active attributes are `feature_flag.key`,
`feature_flag.provider.name`, `feature_flag.result.variant`,
`feature_flag.result.value`, `feature_flag.result.reason`,
`feature_flag.context.id`, `feature_flag.set.id`, `feature_flag.version` and
`feature_flag.error.message` — all at **Release Candidate** stability. Four
spellings are deprecated and are what older hooks and most published examples
still emit: `feature_flag.provider_name`, `feature_flag.variant`,
`feature_flag.evaluation.reason` and `feature_flag.evaluation.error.message`. A
guard fails if any deprecated spelling appears in emitted attributes, derived
from a list rather than retyped.

**The logs pipeline has to become real.** The collector's `logs` pipeline today
is `receivers: [otlp], processors: [batch], exporters: [debug]` — logs go
nowhere, exactly the shape the metrics pipeline had before M4 Task 2, whose own
comment records that ruling M4-R8 forbids the collector naming a backend the
cluster does not have. M5 routes it to OpenSearch, which already holds the
traces. Scope is **flag evaluation events only**: no application logs, no
container logs, no logs panel in the evidence view.

---

## 7. The independent audit, and its temporal problem

`eval-scorer/scorer/authority.py` holds its own copy of the execute allowances
precisely so the audit can disagree with the Policy Decision Service — *"a score
that cannot disagree with the thing it scores is decoration."* But
`scorer/trajectory.py` reads the **autonomy level** from `policy.autonomy.level`
on the very span it is judging. Today that is harmless, because the level is a
constant compiled identically into both services. **M5 opens that hole**: a PDS
resolving `autonomous` when the published contract says `assisted` would send the
audit to the €1,000,000 row, and the audit would agree.

So the scorer resolves the flag **itself**, and a disagreement with the level the
span claims is a finding that scores 0.0. This extends the existing pattern
exactly: a deliberate second copy, independent at runtime, checked at build time
by `tests/test_authority_matches_the_policy.py`'s AST parse of `pds/rules.py`.

**The temporal problem, which is the harder half.** Scoring happens after
execution — `/score` is pull-only, and sometimes runs minutes later. If the
contract was flipped in between, the scorer's independent resolution describes a
different moment than the decision it is judging, and it would report a
disagreement that is really just time passing.

This is M4's truncation lesson in different clothes: a bound on what the
evaluator can establish, biasing the result. The resolution follows the
discipline ruling H1 already set. `feature_flag.version` exists in the
conventions for exactly this — *"the version of the ruleset used during the
evaluation"*. So:

- the ruleset version is recorded on both the evaluation record and the decision
  span;
- the scorer compares ruleset versions as well as levels;
- **if the versions differ, the scorer refuses to score that dimension** rather
  than reporting an agreement or a disagreement it cannot substantiate.

Emitting no score beats emitting a flattering one, and beats emitting a false
alarm.

---

## 8. Task shape

Six tasks, recorded here because §2 trades a decision on the count and a reader
cannot weigh that trade without seeing them. The plan owns the detail; this is
the shape the size decision was made against.

| # | Task | Notes |
|---|---|---|
| 1 | Carried batch and pin bumps | V2, V5, V9, V11, V13 and V12's collector fragment, plus `pytest` 9.1.1, collector 0.160.0 and `spire` chart 0.30.2. One gate run, one cluster rebuild. Includes the memory-budget citation correction described in §12 — the M4 spec and `README.md` both cite the M1 spec's §12 for a table that is in its §9. |
| 2 | Stage 5: OFO, flagd, the `FeatureFlag` CR | Versions verified against upstream releases; cert-manager prerequisite confirmed rather than assumed; `CURRENT_STAGE` raised to 5. Carries the hook-emission spike Finding 13 rests on. |
| 3 | Resolution in the Policy Decision Service | Constant becomes a per-decision lookup keyed on caller SPIFFE ID; fail-closed both ways; the `rule` literal mislabel fixed under §5's constraint. |
| 4 | Flag observability | Span event and semconv log event, current spellings, deprecated-spelling guard, collector logs pipeline to OpenSearch with count parity. |
| 5 | The independent audit | `eval-scorer` resolves the flag itself; ruleset-version discipline; refuses rather than flatters. |
| 6 | Demo, evidence and docs | Stage 5 wiring, the anti-vacuity e2e, evidence view and doc updates, measured memory footprint. |

---

## 9. Exit criteria

1. `--stage 5` installs the Open Feature Operator, flagd and the `FeatureFlag`
   CR on top of everything stage 4 installs, and every operator-facing
   instruction names stage 5 (`CURRENT_STAGE` raised to 5).
2. The Policy Decision Service resolves the autonomy level per decision from
   flagd, keyed on the caller's SPIFFE ID; `pds/rules.py`'s table and keys are
   unchanged.
3. An unresolvable flag resolves to `assisted`, and an unknown variant denies via
   `unknown_caller`. Both asserted, both mutated.
4. The €6,200 claim **refuses** at `assisted` and **settles unattended** at
   `autonomous`, flipped by editing the `FeatureFlag` resource alone — no
   redeploy, no image rebuild, no prompt change. This is the milestone's
   anti-vacuity control.
5. Flag evaluations are recorded as both a span event and a semconv
   `feature_flag.evaluation` log event, using current attribute spellings, with a
   guard that fails on any deprecated spelling.
6. The collector's logs pipeline reaches OpenSearch, and `feature_flag.evaluation`
   events are queryable there, with count parity asserted between what the
   collector received and what OpenSearch holds — the generalisation of M4's
   span-count-parity guard.
7. `eval-scorer` resolves the flag independently, scores 0.0 on a level
   disagreement, and refuses to score when the ruleset version at scoring time
   differs from the one recorded at decision time.
8. The `rule` literal no longer mislabels the `autonomous` row, and
   `tests/e2e/test_supervised.py`'s criterion 4 still passes unchanged.
9. Finding 13 is written up in `docs/upstream-findings.md`, resting on a measured
   observation of what the OpenFeature Python hook actually emits.
10. Stage 5's memory footprint is **measured** against the M1 spec's ~8 GB budget
    on the complete cluster, not extrapolated from stage 4's 5.06 GiB.
11. The gate passes at `--stage 5` with zero skips.

---

## 10. Out of scope

- **Fractional or percentage-based rollout.** Deferred, not rejected (§2).
- **Application and container logs.** The logs pipeline carries flag evaluation
  events only.
- **An LLM-as-judge.** Still the option M4's spec left open; still not taken.
- **A UI for editing the contract.** That is M6's Backstage.
- **Authenticating the human.** M3's carried limitation, untouched here.

---

## 11. Carried debt M5 inherits and does NOT clear

Recorded so the next spec inherits an accurate list rather than a stale one.
Clearing any item means striking it off every list that names it.

**Cleared by this milestone:** the `pytest` 9.0.3 → 9.1.1 bump, the collector
0.158.0 → 0.160.0 and `spire` chart 0.30.1 → 0.30.2 bumps, and the batch of six
fixes (V2, V5, V9, V11, V13, V12's collector fragment) — all in Task 1, sharing
one gate run and one cluster rebuild, exactly as M4's §9 scheduled them. The
`rule` literal mislabel is cleared in Task 3 (§5).

**Not cleared, and why:**

- **M2 criterion 7** — direct agent → model-provider egress is still allowed.
  Owned by no spec since M2.
- **Network policy stops at the agent plane.** Of `northstar-platform`'s six
  Cilium endpoints only `policy-decision-service` enforces anything, and that is
  ingress only; `payments-api`, which holds the money, has no policy of any kind.
  M5 adds flagd to that unenforced plane rather than fixing it. Measured in M4
  (ruling M4-R17).
- **`claims-agent` and `customer-ui` share one SPIFFE ID.** Considered as M5's
  spine and rejected (§2). It matters more after this milestone, not less: an
  autonomy contract keyed on identity is worth what the identity is worth.
  Measured in M4 (ruling M4-R18).
- **Five committed documents name a Crossplane `Agent` CRD as the autonomy
  profile's eventual home** — `docs/architecture.md:637`, the M1 spec at `:256`
  and `:440`, the M2 spec at `:346`, and the M4 spec at `:304`. M6 is now
  intended to drop Crossplane and Argo CD in favour of Backstage over the
  `FeatureFlag` resource this milestone introduces. Those five sites are left for
  M6's spec to correct, deliberately, rather than edited by a milestone that is
  not yet making that change.
- **cert-manager is named in the M1 spec's M2 row as "installed, never clicked"
  and has never been installed** (§12, measured). If OFO does not require it,
  this milestone does not install it either, and the stale claim is M6's or a
  doc pass's to correct.
- **`platform/eval-scorer` has a row in neither component inventory.** M4's §9
  assigned this to M5; it is deferred again, because M5 adds two more platform
  components and the sensible fix is one inventory pass covering all of them.
  Owned by M6.
- **`senior-settlement-agent` serves an agent card naming the junior**;
  **`get_payment` is unlimited and unscoped**; **`a2a-sdk` 0.3.7 → 1.x is a
  protocol migration, not a bump**. All inherited from M4 unchanged.

---

## 12. Evidence this spec rests on

Recorded so a later reader can tell what was measured from what was reasoned.

**Measured on this machine, 2026-09-14:**
- `helm list -A` holds six releases — `cilium` 1.20.1, `opensearch` 3.8.0,
  `opensearch-dashboards` 3.8.0, `prometheus` 29.29.0 (v3.14.0), `spire` 0.30.1
  (appVersion 1.15.3), `spire-crds` 0.6.1. **cert-manager is not among them**,
  the `cert-manager` namespace does not exist, and no cert-manager, OpenFeature
  or flagd CRD is registered.
- The collector's `logs` pipeline is `receivers: [otlp], processors: [batch],
  exporters: [debug]`.
- `pds/server.py:51` is `AUTONOMY_LEVEL = "assisted"`, a module constant, read at
  `:270`, `:276` and `:350`.
- `pds/rules.py`'s `EXECUTE_LIMITS` holds three rows keyed by
  `(SPIFFE ID, autonomy level)`, including
  `(settlement-agent, autonomous) → 1_000_000.0`.
- `eval-scorer/scorer/authority.py`'s `PUBLISHED_EXECUTE_LIMITS` mirrors those
  three rows; `scorer/trajectory.py:207` reads the autonomy level from the
  `policy.autonomy.level` span attribute; `scorer/scores.py:346` uses it as a
  lookup key.
- The working tree matches the commit the published gate row names (`b215426`)
  across every gate path, so M4's MEASURED row still stands.

**Read from upstream documentation, 2026-09-14:**
- The nine active `feature_flag.*` attributes and their Release Candidate
  stability, and the four deprecated spellings, from the OpenTelemetry attribute
  registry.
- The `feature_flag.evaluation` event name and log-record shape, and that the
  spans-based model was retired, from the OpenTelemetry feature-flags
  conventions. Latest released semconv indexed: 1.44.0.
- Appendix D's recommendation of span events, its acknowledgement of the
  log-event trend, and its `error message` → `error.message` mapping, from the
  OpenFeature specification.

**Established earlier in this project, relied on here:**
- The autonomy seam: M2's spec records *"the level is a constant in M2 and
  becomes an OpenFeature/flagd flag in M5"*, and `pds/server.py`'s own comment
  instructs against inventing a mechanism elsewhere.
- Stage 5's ~8 GB budget, from the M1 spec's stage table at **§9 "Cluster and
  deployment"**, whose row reads `| 5 | flagd | ~8 GB |` — the same budget as
  stage 4, against which stage 4 measured 5.06 GiB (M4). **Note the citation
  correction:** the M4 spec and `README.md` both attribute this budget to "M1
  spec §12", and §12 is "Acceptance criteria" — it says nothing about memory.
  The table has always been in §9. Reported rather than fixed here, because
  correcting a committed document from a milestone that is not making the change
  is Task 1's doc-pass work, not this spec's.
- The anti-vacuity discipline (M4 spec §5) and ruling H1's "emit no score rather
  than a flattering one".

**Not yet established, and gating implementation:**
- Whether the Open Feature Operator still requires cert-manager. Task 2
  prerequisite; confirm against the operator's current release, not from memory.
- The latest released versions of the Open Feature Operator and flagd, verified
  against **upstream releases** rather than a local Helm index.
- **What the OpenFeature Python OTel hook actually emits** — span event or log
  event, current or deprecated attribute spellings, and which error attribute.
  Finding 13 rests on this measurement; it is evidence, not a footnote.
- Whether the collector's `opensearch` exporter carries **logs** as usefully as
  it carries traces, or whether a different exporter is required.
- Stage 5's memory footprint on the complete cluster.
