# Northstar — an Agentic Internal Platform reference implementation

Northstar Insurance is a fictional insurer. A customer files a vehicle-damage
claim; a set of agents collaborates to investigate and settle it. The platform
decides whether those interactions are permitted, whether a human must approve
them, records what happened, evaluates the quality of the trajectory, and
gradually determines how much autonomy the system should have.

Built from open standards and existing open-source projects, not a proprietary
"AI platform".

## Status

**Running the demo?** Start at
[`docs/demo-runbook.md`](docs/demo-runbook.md) — the operator's document:
pre-flight, the beat-by-beat run with what to say at each one, every option
available, the reset checklist, and live triage. This file is the reference;
that one is the script.

**M6 — Golden path.** The question this milestone answers is *"where is the
platform API, and what does a human see of it?"* Stage 6 adds **Backstage**,
in `northstar-platform`, built and pushed through
`scripts/rebuild-and-deploy.sh` like every other component. The answer it
gives is deliberately anticlimactic: **the platform API is the
`northstar-autonomy` `FeatureFlag` M5 already shipped, and Backstage is a
view onto it.** M6's own design (§1) records that M5's spec had already
dropped Crossplane and Argo CD and reassigned the platform-API role to that
resource — so a Crossplane `Agent` CRD composing Deployments that already
work would have been a wrapper over working things. The **Autonomy contract**
card (http://localhost:7007/autonomy-contract) is a *live read* of the object
per identity through `GET /api/autonomy-contract/status`, and its one write
— `POST /api/autonomy-contract/variant`, flipping `settlement-agent`'s
targeting branch `assisted` ⇄ `autonomous` — goes through a **separate,
deliberately narrow ServiceAccount**: `backstage-autonomy-writer` holds
`get`/`list`/`patch` on `resourceNames: [northstar-autonomy]` and nothing
else, no `delete`, no `create`, kept independently reviewable from the
read-only ServiceAccount the Kubernetes plugin uses. `tests/e2e/
test_golden_path.py` is the milestone's anti-vacuity proof: it flips the
contract **over Backstage's own HTTP route** rather than `kubectl`, submits
the identical €6,200 claim before and after, and asserts refused-then-executed
— with an independent `kubectl get` witness, so the card's response agreeing
with itself proves nothing. Every successful flip through the card emits a
`northstar.autonomy_contract.change` span (service `backstage`), asserted to
reach OpenSearch as well as Jaeger; that span is the **durable** half of the
audit claim, because the object's own `managedFields` entry is overwritten by
the very next write, whichever tool makes it, and is a point-in-time fact
rather than a history. The catalog is **nine `Component` entities**, and
neither side of the check that asserts it is a hand-written list — the live
catalog is built from the committed `catalog-info.yaml` files staged into the
image, and the test's expectation is a directory walk. Measured pod and
memory figures are in the Stages table's row 6 below and are not repeated
here.

**What M6 deliberately does not do, and says out loud on stage.** Three, all
in the design's §10 and in `docs/architecture.md`'s "Known limitations":
**Backstage here is unauthenticated** — the guest login is a real
`POST /api/auth/guest/refresh` round trip that asks nothing of the caller, so
anyone who can reach port 7007 can flip the contract; what keeps that honest
is the narrowness of the one write, not a login, and the sharper trade this
project did *not* make (a separate Deployment fronting the write, so a
Backstage compromise yields the ability to *ask* rather than a credential to
*keep*) is written down as such. **No scaffolder is demonstrated**, so M1's
own claim — "the rest is implementation detail; a platform team could add a
service in an afternoon through the same API" — is honestly **half** shown:
the platform API exists as a real object with a real UI, and nothing here
generates a new agent from it. And **the catalog is static**, because there
is no git remote for Backstage's discovery processors to point at (see
`docs/upstream-findings.md` Finding 15 for the specific mechanism that does
not reach a local file).

**M5 — Progressive autonomy.** The question this milestone answers is *"can
one live value change what the platform is allowed to do, with nothing else
touched?"* Stage 5 adds cert-manager, the Open Feature Operator (OFO) and
flagd — injected into the Policy Decision Service's own pod as a native
Kubernetes sidecar (`spec.initContainers[]`, `restartPolicy: Always`, not a
regular container), answering gRPC evaluation on `:8013` and OFREP on
`:8016`. The autonomy level the policy engine judges every `execute_payment`
call against is no longer a constant compiled into the PDS — it is resolved
per request from a `FeatureFlag` object, `northstar-autonomy`
(`deploy/platform/openfeature/featureflag.yaml`), through that sidecar. The
object **ships in its shipped, refusing state** on purpose
(`defaultVariant: assisted`, both targeting branches `assisted` — a
deliberate no-op, not an oversight): the whole point is that the demo's
flip is a one-word change to a *live* value, not a resource authored from
scratch on stage, and not a change to this repository's own tree at all.
`tests/e2e/test_progressive_autonomy.py` is the milestone's anti-vacuity
proof: it flips the live contract, submits the identical €6,200 claim under
the junior identity before and after, and asserts the claim is **refused**
under `settlement-agent.assisted.execute_limit` the first time and
**executed** under `settlement-agent.autonomous.execute_limit` the second —
with no reviewer-ui approval anywhere in the trace — then restores the
contract to its refusing state itself, verified against flagd's own
resolved answer rather than assumed. A companion test asserts, mechanically
against pod UIDs and image digests, that nothing in `northstar-agents` was
redeployed by the flip. Every flag evaluation now also carries two
telemetry signals rather than one: the `feature_flag.evaluation` **span
event** PydanticAI's OpenFeature hook already put on the `policy.decision`
span, and a genuine `feature_flag.evaluation` **log record** this milestone
adds end to end through the logs pipeline to OpenSearch — the audit
(`platform/eval-scorer`) holds its own, independently-derived answer to
"what does the contract currently say" rather than trusting either signal,
the same argument this project already made for the execute-limit table
itself (spec §5's anti-vacuity criterion, applied to the level, not just
the amount). **Measured: 34 pods, all Running.** Memory against the ~8 GB
budget (M1 spec §9, "Cluster and deployment", row 5): **5.24 GiB** cold
(a full `--stage 5` bring-up from nothing, 2026-09-14) and **5.66 GiB**
warm, re-measured while this task's own live tests were running against
the cluster — both comfortably inside budget. `./scripts/start-demo.sh` now
also forwards flagd's own metrics port (`18014 -> 8014`, via
`deployment/policy-decision-service` rather than a pod name, since flagd is
a sidecar with no Service of its own) — `/metrics` carries
`feature_flag_flagd_impression_total`, the one place in this system that
shows how many evaluations resolved `assisted` versus `autonomous` without
querying Prometheus or OpenSearch by hand.

**M4 — Observable.** The question this milestone answers is *"can we prove why
€5,800 was paid?"*, and the answer is a saved dashboard with **no new
application code behind it**. Stage 4 adds OpenSearch, OpenSearch Dashboards,
Prometheus and `eval-scorer`. Traces are teed into OpenSearch alongside Jaeger
and are queryable there; metrics reach Prometheus **over OTLP with every one of
the chart's ten default scrape jobs disabled**, so OTLP is the single ingestion
path. The **evidence view** (`deploy/observability/dashboards/evidence.ndjson`,
imported at stage 4) reconstructs both claims' audit trails out of spans the
system already emitted — who decided, under which rule, at which limit,
approved by whom, executed under which identity, and where the money went — and
every query it makes is asserted by `tests/e2e/test_evidence_view.py` rather
than eyeballed. **Policy-adherence scores** are written as OTel
spans inside the trajectory they judge, and a deliberately mis-routed run
scores `0.0 retry-after-refusal` where the compliant one scores `1.0` — the
anti-vacuity property that makes the other four scores worth reading. They are
**pull-only**: `platform/eval-scorer` scores a claim when something asks it to,
over `POST /score`, and nothing scores a settlement as it happens.
`./scripts/start-demo.sh` forwards it on 18300 at stage 4; `docs/architecture.md`'s
"See it yourself" step 10 is the walkthrough.

**What M4 measured and published rather than assumed.** Of the six GenAI
metrics M4's spec §6 puts in scope — a chosen subset of the **twelve** the
OpenTelemetry conventions define, and only three of the six are *agent*
metrics — exactly **one** is emitted by
anything in this system (`gen_ai.client.token.usage`, from PydanticAI); the
other five are published as gaps, with the source-level sweep in
[`docs/upstream-findings.md`](docs/upstream-findings.md) Finding 12 that says
why. And **OQ-1 is answered** (it had been open since M2.1): with
`NORTHSTAR_CAPTURE_CONTENT=false`, the evidence view loses **nothing** — not one
panel field changes, because every field it reads is emitted by the *platform*
rather than by the model. What is lost sits one step behind it: the agent's own
statement of the amount it proposed, which at capture-ON is the only
independent corroboration of the amount the policy engine judged. The default is
**unchanged and still ON**; see `docs/telemetry-contract.md`'s OQ-1 section for
the matched-pair measurement.

**What is deliberately still not decided in M4.** Gap 4 is not closed —
`northstar.claim.id` still stops at the MCP boundary, so the evidence view is
built *around* a two-step trace-id join rather than pretending a claim-id filter
works. There is no LLM-as-judge; the five scorers are deterministic functions
over the decision spans. And two carried gaps are now written down beside the
criteria they are the complement of, rather than quietly inherited: **network
policy stops at the agent plane** (of `northstar-platform`'s six Cilium
endpoints only the Policy Decision Service enforces anything, and nothing in the
cluster has egress enforcement outside `northstar-agents`), and **`claims-agent`
and `customer-ui` share one SPIFFE ID**. Both are pre-existing, both are
measured, and both are M5's. A third was M4's own: **`pytest` was pinned one
release behind on purpose** (9.0.3, against 9.1.1) across seven
`requirements.txt` files, deferred rather than drifted, and scheduled first
after M4 merges. See §9 of
[the M4 design spec](docs/superpowers/specs/2026-09-11-northstar-m4-observable-design.md).
**2026-09-15, whole-branch review (J6): this debt was discharged in M5's own
first commit** — Task 1 bumped all seven `requirements.txt` files (the five
in the docs/telemetry-contract.md pin table, `platform/eval-scorer`'s, and
the root `tests/requirements.txt`) to `9.1.1`, and every venv, including the
root one, now measures it. Left standing with this note rather than deleted,
per this document's own convention for a claim that was true when written
and has since been closed.

---

**M3 — Supervised.** A refused payment is no longer the end of the story. The
claim flows end to end and produces one correlated trace; agentgateway sits in
the MCP path, verifies a SPIRE-minted JWT-SVID on every call, and asks a Policy
Decision Service whether the call is allowed. The position is now **one hop
authorized, and one refusal a human can overturn**: submitting the pre-filled
€6,200 claim gets the payment **proposed, refused, and recorded as refused** —
the model narrates the refusal to the claimant and does not retry, and the
refusal becomes a row a human reviewer reads on the reviewer UI at
http://localhost:8081. Approving it there executes the payment under a
*second, more privileged identity* (`senior-settlement-agent`, the same image
under a different ServiceAccount), which the Policy Decision Service permits on
its own allowance row rather than on an override. The human's click is a
`review.decision` span, the approved execution is a
`settlement.execute_approved` span, and the two are joined to the original
refused trace by a **span link** — not a parent-child edge, because the human
decided minutes later in a different process. A second claim at €5,800 still
settles end to end with no human involved: a platform that asks a human about
everything is not supervision, it is a queue.

**What is deliberately still not decided**, so the "supervised" claim is not
overread. Approval is **one human, one click, once**: no second approver, no
timeout, no expiry, no re-proposal path back to the agent, and no
**authenticated** human — the reviewer id is a string the form supplies, and
the `review.decision` span's `review.reviewer.spiffe_id` is the reviewer UI's
*own* identity, so between them the span records an unverified claim and the
deployment that accepted it, never which person approved. There is a Reject
button and it is honest about doing nothing: it records a span and leaves the
payment exactly as refused. Authorization is still exactly one hop on the MCP call; the
browser → claims-agent, claims-agent → settlement-agent and
payments-mcp → payments-api hops carry no identity and no policy, and
payments-api still moves money for anything that can reach its Service —
including the approved execution, which is authorized at the gateway like any
other call and not by the ledger. See `docs/architecture.md`'s "What the system
deliberately does not do", which each milestone sharpens rather than deletes.

## Quick start

**Divergence from the spec's exported-env-var flow, noted explicitly (amendment
A2):** the spec's original acceptance criterion for this step was `git clone`
→ `export ANTHROPIC_API_KEY=...` → one command, no `.env` file. A2 replaced
that with a sourced, git-ignored `.env` (better for a repo with several
optional credentials), but no document had said so until now — a cold reader
following only the spec would hit `setup-kind.sh`'s very first failure. This
is that missing step:

```bash
cp .env.template .env
# edit .env: ANTHROPIC_API_KEY is REQUIRED now that M1.2 shipped — the
# settlement decision is a real Claude model call, not a stub. Leave it
# blank and setup-kind.sh will still bring up every pod Running and fully
# traced -- 7 application pods at stage 1, 9 at stage 2, where agentgateway
# and the Policy Decision Service join them, and 11 at stage 3, where
# reviewer-ui and senior-settlement-agent do (alongside SPIRE's four and
# Cilium's, which are platform rather than application), and 15 at stage 4,
# where eval-scorer, OpenSearch, OpenSearch Dashboards and Prometheus join
# them. Counted off live clusters with `kubectl get pods -A`, not incremented
# on paper; the stage-4 figure was counted 2026-09-14 during M4's final-review
# fix wave, on the same cluster the 5.06 GiB reading below came from. That line
# is what the review added: this comment stopped at stage 3 two lines above a
# `--stage 4` command. The
# key is deliberately optional at the Kubernetes level, but the
# FIRST claim submission fails at the model call — the one thing this
# milestone exists to demonstrate. setup-kind.sh prints SET/EMPTY for this
# variable; check for SET before going on stage. DASH0_AUTH_TOKEN and its
# three DASH0_* siblings are optional — see the comments in .env.template for
# exactly which variables are required.

./scripts/setup-kind.sh --stage 6
./scripts/smoke-test.sh
```

**`--stage 6` as of M6.** Stage 6 is the complete demo; every lower stage
exists to isolate one idea for a shorter talk, and
[`docs/demo-runbook.md`](docs/demo-runbook.md) §3 has the table that says
which stage to pick for which audience. Stages 4, 5 and 6 all run on **stage
3's cluster config unchanged**, so climbing 3 → 4 → 5 → 6 needs no cluster
recreate; only 1 → 2 does, because `networking.disableDefaultCNI` is a
create-time kind flag.

**Carried to M7, recorded rather than fixed here:**
`tests/unit/test_stage2_cilium_config.py::test_no_operator_facing_instruction_still_points_at_an_older_stage`
still has `CURRENT_STAGE = 5`. M6 shipped stage 6 without raising it, so the
guard that exists to catch exactly this line did not catch it — the **fifth**
recurrence of this class, and the first one the guard slept through rather
than caught. Raising the constant is a `GATE_PATHS` change (it reddens every
remaining `--stage 5` instruction across `scripts/*.sh`, README and the
telemetry contract), so it is not being slipped into a documentation pass
that would silently turn M6's **measured** gate row into a predicted one.
It is M7's first task.

**`--stage 4`, not `--stage 3`, as of M4 Task 1.** Same defect one stage later,
and the third time this class has recurred here. It said `--stage 2` for the
whole of M3, which was correct until Task 4 and false afterwards: stage 3
installs reviewer-ui and senior-settlement-agent on top of stage 2, and
`smoke-test.sh`'s own prerequisite check requires reviewer-ui to answer on
port 8081 — so following the old instruction rebuilt a cluster the very next
command then rejected. M4 Task 1 made stage 4 real and left this line at
`--stage 3`; the review caught it, and ruling M4-R7 assigned the correction
here rather than to the milestone's closing doc pass.
`tests/unit/test_stage2_cilium_config.py::test_no_operator_facing_instruction_still_points_at_an_older_stage`
now pins this across every script and this file, with its `CURRENT_STAGE`
raised to 4 — which is what turned this line, `smoke-test.sh` (twice) and
`rebuild-and-deploy.sh` red.

Stage 4 runs on **stage 3's cluster config unchanged**, so moving from stage 3
to stage 4 needs no cluster recreate: it adds OpenSearch, OpenSearch
Dashboards and Prometheus, restarts the collector against them, and imports
the evidence view into Dashboards from
`deploy/observability/dashboards/evidence.ndjson`.

`smoke-test.sh` additionally requires a repo-root Python venv, a venv per
Python service, and a Playwright Chromium install — see the actionable error
message it prints for each, or run the four commands it lists yourself
ahead of time:

```bash
uv venv --python 3.13 .venv && uv pip install -r tests/requirements.txt && .venv/bin/playwright install chromium
for svc in tools/payments-mcp agents/settlement-agent agents/claims-agent platform/policy-decision-service apps/reviewer-ui platform/eval-scorer; do
  (cd "$svc" && uv venv --python 3.13 .venv && uv pip install -r requirements.txt)
done
```

**That list and `scripts/smoke-test.sh`'s own prerequisite loop must hold the
same services, and a test now asserts it** —
`tests/unit/test_smoke_test_gate.py::test_the_gate_runs_every_per_service_suite_the_contract_publishes`
reads all three places (the contract's per-service row, the gate's loop, and
this code block) and fails if they disagree. Adding a service to the gate and
not here is one defect that has now happened four times: the Policy Decision
Service in M2.2, `apps/reviewer-ui` in M3 Task 5B, and `platform/eval-scorer`
in M4 Task 4, which added the venv requirement to the gate and left this block
behind — so a fresh clone that followed the README was stopped at
`Error: …/platform/eval-scorer/.venv not found`. The list is still written out
in two places, because both are things a human copies; what changed is that
the two copies are checked against each other rather than against nobody.

`apps/reviewer-ui` joined that loop in M3 Task 5B, and — exactly like the
Policy Decision Service one milestone earlier — it was an existing dependency
finally being declared rather than a new one. `tests/unit`'s per-service count
guard shells out to reviewer-ui's own suite through `apps/reviewer-ui/.venv`,
so the gate already could not pass without this venv; it just failed four
minutes in, inside a unit test, complaining about a published count. The gate
now requires it up front and runs reviewer-ui's suite alongside the rest.
(No count is written here. It read **50** and then **59**, and the measured
figure is **56** — `docs/telemetry-contract.md`'s per-service row is the
source, a unit test re-measures that row against the suite on every run, and a
third copy in this paragraph was only ever a way to be wrong in a place
nothing checks.)

**The Policy Decision Service is in that loop, and it is not optional.** An
earlier version of this section said it was "deliberately not in that loop"
because the gate does not run its suite. That was wrong, and wrong in the
direction that breaks a fresh clone: `agents/settlement-agent`'s own suite
imports `pds.server`, which imports the generated gRPC stubs, so
`smoke-test.sh` has always needed the PDS set up — it simply needed it silently
and got none of the PDS's tests in return (96 of them when that was written;
**105** as of M3, re-measured with `--collect-only`). As of the M2.2
final-review fix round the gate requires this venv like the others, generates the stubs
itself with `./gen-stubs.sh` before running any suite, and runs the PDS suite
alongside the rest.

The stubs stay git-ignored on purpose (ruling R22): committing them would let
the image and the repo disagree about a contract whose only source of truth is
the vendored `proto/ext_mcp.proto`, which the Dockerfile regenerates from at
build time. So they have to be generated once per clone, and the venv has to
come first, because `gen-stubs.sh` defaults to that venv's `python` for the
pinned `grpc_tools.protoc`. `smoke-test.sh` does this for you. If you want to
run the PDS suite on its own, or run `pytest tests/unit` from the repo root
before ever running the gate, do the generation yourself first — everything
that needs the stubs fails loudly rather than skipping when they are missing,
and prints this command:

```bash
(cd platform/policy-decision-service && ./gen-stubs.sh)
(cd platform/policy-decision-service && .venv/bin/python -m pytest tests -q)
```

Then open:

| Surface | URL |
|---|---|
| Customer UI | http://localhost:8080 |
| Reviewer UI (stage 3 and above) | http://localhost:8081 |
| Jaeger | http://localhost:16686 |
| OpenSearch Dashboards — the evidence view (stage 4) | http://localhost:5601 |
| Prometheus (stage 4) | http://localhost:9090 |
| Backstage — the catalog and the autonomy card (stage 6) | http://localhost:7007 |

The first three are kind host ports and need nothing else running. The last
three are port-forwards `./scripts/start-demo.sh` owns; `setup-kind.sh` says so
at the end of a stage-4 run. (The Backstage row was added by M6 Task 4, which
is also when `start-demo.sh` took ownership of that forward — until then stage
6 shipped reachable only by a `kubectl port-forward` the operator ran by hand,
so adding the row earlier would have named a surface the script could not
reach. `docs/architecture.md`'s equivalent table is the one a guard checks
against `start-demo.sh`'s own `#surface` lines, in both directions.) (This table listed only the first three, and called the
reviewer UI "stage 3 **only**", until M4's final review — `setup-kind.sh` gates
that UI on `-ge 3` and carries a comment recording that this exact wording was
corrected *there* in M4 Task 1. The README copy was missed, which is the
argument for `docs/architecture.md`'s equivalent table being the one a guard
now checks.)

The reviewer UI is where M3 is actually visible. Submit the pre-filled €6,200
claim in the customer UI, watch it come back refused, then open the reviewer UI:
the refusal is listed there with the rule that produced it, the limit it
exceeded, and the model's own narrative. Approve it and the payment executes
under `senior-settlement-agent`. Both the click and the execution land in
Jaeger — `review.decision` and `settlement.execute_approved` — in a trace that
carries a **link** back to the refused one.

Unlike the four ports the setup script forwards, 8081 is a NodePort mapped by
`deploy/kind/stage3.yaml`, so it works without a `kubectl port-forward` and
survives a redeploy.

Tear down with `./scripts/teardown.sh`.

### Running the tests directly

`./scripts/smoke-test.sh` is the supported way to run the suite. If you invoke
pytest yourself, know what you are paying for:

| Command | Cluster | Anthropic tokens |
|---|---|---|
| `.venv/bin/python -m pytest tests/unit -q` | not needed | none |
| `.venv/bin/python -m pytest tests/ -q` | **required** | none, since M4 Task 5 |
| `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` | required | yes — the full gate |

A bare `pytest tests/` is **not** cluster-independent — it needs the cluster and
the port-forwards `./scripts/start-demo.sh` owns, and without them the e2e
modules fail with `httpx.ConnectError` rather than skipping. That is deliberate;
see "The first run of `pytest tests/`" in `docs/telemetry-contract.md`.

It is, however, **free** as of M4 Task 5, which closed ruling M4-R15 by gating
the last three e2e files that drove a live settlement:
`test_settlement_agent_trace.py`, `test_claims_chain_trace.py` and
`test_claim_correlation.py`, one module-scoped driving fixture each, all at
€6,200. The cost of that is stated rather than hidden, and it turned out to be
larger than the first reading of it: a bare run no longer proves the A2A chain
end to end, because the three files that proved it are the three that were
paying for it — **and it no longer re-proves the GenAI metric inventory
either**, because the positive control every `gen_ai.*` absence in
`test_prometheus_metrics_ingest.py` is paired with is a histogram only a model
call produces. Ten of that file's fourteen tests ride the flag for that reason;
seven of the nine model-call ones were found by running the bare suite and
reading the failures, not by reasoning about them, and the tenth (the
reviewer-ui row of the per-service control) was found the same way, later,
during M5: it needs no model call, but nothing ungated ever drives reviewer-ui
traffic either, so it failed a bare run for a different reason from the other
nine. Set `RUN_BROWSER_TESTS=1` to get all of it back.

**Thirteen** modules sit behind `RUN_BROWSER_TESTS=1`, **75** tests in all —
and that figure is now *re-measured by a test* rather than by hand.
`tests/unit/test_telemetry_contract_counts.py` walks pytest's collected items,
reads each item's own `skipif` marker, and fails against this sentence and
against `docs/telemetry-contract.md`; before M4 Task 5 nothing did, so deleting
a gate from a settlement-driving file moved no published number and reddened no
test. The M4 Task 4 figure it replaces was seven modules and 41 tests. The
spread: `tests/e2e/test_settlement_llm_trace.py` (9), 10 of the 14 in
`tests/e2e/test_prometheus_metrics_ingest.py` (nine gated by M4 Task 5 — every
control whose metric exists only if a model call happened minutes earlier —
plus the reviewer-ui row of the per-service control, gated later during M5 for
its own reason: nothing ungated drives reviewer-ui's HTTP traffic either),
`tests/e2e/test_browser_root_span.py` (8),
`tests/e2e/test_settlement_agent_trace.py` (8, M4 Task 5),
`tests/e2e/test_claims_chain_trace.py` (7, M4 Task 5),
`tests/e2e/test_claim_correlation.py` (6, M4 Task 5),
`tests/e2e/test_gateway_trace.py` (6), 6 of the 7 in
`tests/e2e/test_supervised.py`, `tests/e2e/test_policy_scores.py` (5, M4 Task
4), 4 of the 6 in `tests/e2e/test_policy_denial.py`, 4 of the 4 in
`tests/e2e/test_evidence_view.py` (3, M4 Task 3, +1, M5 Task 6 — a fourth test
reads the same module-level gate rather than adding its own), and 1 of the 2
in `tests/e2e/test_progressive_autonomy.py` (M5 Task 6's own new module — the
half of it that drives a real settlement is gated the same way
`test_supervised.py`'s is; the half that only compares pod UIDs and image
digests is not, because it drives no model call and starts no browser), and
`tests/e2e/test_golden_path.py` (1, M6 Task 4 — the settlement-driving test
that flips the contract over Backstage's own HTTP route rather than
`kubectl`; the other five in that module drive no browser and spend no model
call, so they are not gated).
The four partly-gated ones are `test_supervised.py`, `test_policy_denial.py`,
`test_prometheus_metrics_ingest.py` and `test_progressive_autonomy.py`.
**3 of the 13 launch a browser** — `test_browser_root_span.py`,
`test_policy_denial.py` and `test_supervised.py` — and
**10 of the 13 start no browser**, riding the flag because they spend model
tokens or need something that does. That split is derived from
`chromium.launch(` in `tests/e2e` by the
same guard that checks the 74; it was published here and in two places in the
telemetry contract as "only one", by the pass whose job was to catch exactly
that.
`test_policy_denial.py`'s two ungated tests spend nothing: one reads source
structurally, and the other drives two direct MCP calls with different SPIFFE
identities and no model call at all. `test_supervised.py`'s one ungated test is
its criterion 7, which spends nothing for the same reason: it speaks MCP to the
gateway directly with a minted SVID for reviewer-ui's own identity, no browser
and no model call.

**Three ungated tests still write an `executed` row to the ledger, on purpose.**
Measured by ledger delta across live runs, not inferred:
`test_payments_api_trace.py` and `test_payments_mcp_trace.py` each execute one
€6,200 payment, neither of them through agentgateway, so no policy decision is
made on them at all; and `test_policy_denial.py`'s criterion 5 executes €100,
which is that test's whole assertion. Nothing gates them, because payments-api's
ledger is an in-memory, per-process store on a kind cluster — the row costs
nobody anything, and gating it would remove the only ungated coverage of the
money path from a bare run.

`tests/unit` is the hermetic subset: no cluster, no API key, no network. It is
not, however, self-sufficient on a fresh clone — its per-service count guard
shells out to the settlement-agent and policy-decision-service suites, both of
which need the generated ExtMCP stubs, so run `(cd
platform/policy-decision-service && ./gen-stubs.sh)` once first. It fails
loudly and names that command if you do not (limitation 10 in
`docs/telemetry-contract.md`).

### Mutation-testing a guard — use `scripts/mutate-guard.sh`

A guard in this repo is not trusted until it has been mutated and watched go
red **for the reason it names**. Many of the tests here exist because an
earlier guard passed while the thing it claimed to check was broken, so this
is the standing convention, not a nicety.

Do not hand-roll the edit/run/revert loop. Run:

```bash
./scripts/mutate-guard.sh <file> <old-text> <new-text> <test command...>
```

It restores the file on every exit path, refuses a replacement that changes
nothing, and — the part that matters — sets **`PYTHONDONTWRITEBYTECODE=1`** and
purges `__pycache__` around the run.

That last part is not hygiene. CPython validates a cached `.pyc` against
`(int(source_mtime), source_size)` and nothing else, so a mutation that keeps
the file's byte length (`6000` → `6500`, `==` → `!=`) and lands in the same
integer second as the cached write gives you **either** a false green, where
the mutant is on disk and the original bytecode runs, **or** a false red, where
the revert is on disk and the mutant bytecode runs and every later mutation in
the round reddens for the *previous* one's reason. Both directions were
reproduced from scratch during M4. The faster the suite, the likelier it is:
`platform/eval-scorer`'s runs in 0.15s, so the whole cycle fits inside a
second. pytest's rewritten test-module caches invalidate on the same pair, so
mutating a *test* file is exposed too; only non-Python targets (YAML, Markdown,
shell) are structurally immune.

`tests/unit/test_mutation_protocol.py` pins both halves of this, so the script
cannot quietly lose them.

## Stages

The demo has six stages; the setup script installs only what a stage needs, so
the laptop runs one stage at a time.

| Stage | Name | Adds |
|---|---|---|
| 1 | Connected | agents, MCP servers, systems of record, customer UI, collector, Jaeger |
| 2 | Governed | agentgateway, SPIFFE/SPIRE, Policy Decision Service, Kyverno, Cilium — **as of M2.2 `--stage 2` installs Cilium (M2.1 Task 3, with M2.2 Task 7 adding ingress and `toFQDNs` egress policies), agentgateway (M2.1 Task 3, in the MCP path between settlement-agent and payments-mcp), SPIRE (M2.1 Task 4, verifying a JWT-SVID at the gateway) and the Policy Decision Service (M2.2 Task 4, deciding every `tools/call` through the gateway's `mcpGuardrails` hook) — Kyverno is NOT installed yet**, on a cluster created with kind's default CNI disabled |
| 3 | Supervised | Dapr Workflow, reviewer UI, Keycloak — **as of M3 `--stage 3` installs the reviewer UI (Task 4, the page a human reads and clicks) and `senior-settlement-agent` (Task 3, the settlement-agent image in a second Deployment under a second ServiceAccount, so "senior" is an identity rather than a different program) on top of everything stage 2 installs — Dapr Workflow and Keycloak are NOT installed**, on a cluster created with a fourth host port reserved for the reviewer UI |
| 4 | Observable | OpenSearch + Agent Evals, Prometheus — **as of M4, `--stage 4` installs OpenSearch (single node, security plugin off), OpenSearch Dashboards, Prometheus and `eval-scorer` on top of everything stage 3 installs, adds an OpenSearch branch to the collector's traces pipeline, and routes the collector's metrics pipeline to Prometheus over OTLP. Stage 4 is complete: Agent Evals arrived with `platform/eval-scorer` in M4 Task 4** — this row said they were "NOT installed yet" until M4 Task 5's doc pass, which is the milestone's own headline feature going unannounced in the table a reader checks first. Prometheus is OTLP-ingest only: every one of the chart's ten default scrape jobs is disabled, so OTLP is its single ingestion path (M4 exit criterion 5). **Measured memory: 5.06 GiB** against the ~8 GB budget (M1 spec §9, "Cluster and deployment", row 5) — `docker stats northstar-control-plane`, three readings 3s apart on the complete stage-4 cluster with 30 pods Running, all of OpenSearch, Dashboards, Prometheus, eval-scorer and the demo workloads up: `5.062 / 5.061 / 5.061 GiB`. That is the kind node container's whole footprint, page cache included; the node cgroup's `anon` alone is 3.79 GiB, so 5.06 is the conservative end of the range and the budget holds either way. All of it runs on stage 3's cluster config unchanged: there is deliberately no `deploy/kind/stage4.yaml` (plan amendment A1), and both Dashboards and Prometheus are reached by port-forward via `./scripts/start-demo.sh` rather than a kind host port. M4 Task 3 adds the evidence view itself, imported into Dashboards from `deploy/observability/dashboards/evidence.ndjson` at this stage — see `docs/architecture.md`'s "See it yourself" step 9 |
| 5 | Progressive autonomy | OpenFeature / flagd — **as of M5, `--stage 5` installs cert-manager (a prerequisite for the Open Feature Operator's webhook certificates, not this project's own component), the Open Feature Operator, and the `northstar-autonomy` `FeatureFlag` on top of everything stage 4 installs. flagd itself is injected as a native Kubernetes sidecar into the Policy Decision Service's own pod (`spec.initContainers[]`, `restartPolicy: Always`), not a separate Deployment — the PDS resolves the `autonomy-level` flag against it over gRPC (`:8013`) per `execute_payment` request rather than reading a compiled-in constant.** The contract ships in its shipped, refusing state on purpose (`defaultVariant: assisted`, both targeting branches `assisted`) — see "M5 — Progressive autonomy" above for the anti-vacuity demo this makes possible and the measured pod/memory figures; nothing here is retyped from that section |
| 6 | Golden path | Backstage — **M5's own spec dropped Crossplane and Argo CD, reassigning the platform-API role to the `FeatureFlag` resource it introduced (`docs/superpowers/specs/2026-09-15-northstar-m6-golden-path-design.md` §1); this row said "Crossplane `Agent` CRD, Argo CD, Backstage" until M6 Task 1 corrected it. As of M6 Task 1, `--stage 6` deploys Backstage in `northstar-platform` on SQLite (`better-sqlite3`, `:memory:` — design §3: this cluster is torn down and rebuilt, not operated, and the catalog is re-read from committed `catalog-info.yaml` files on every start regardless), built and pushed through `scripts/rebuild-and-deploy.sh` like every other component. Its ServiceAccount is READ-ONLY (`get`/`list`/`watch` on Pods, Deployments, ReplicaSets, Services, ConfigMaps and the other object types `@backstage/plugin-kubernetes-backend` reads by default; no Secrets, no write verbs anywhere) — the separate ServiceAccount that can PATCH `northstar-autonomy` is a later task's. Reached by `kubectl -n northstar-platform port-forward svc/backstage 7007:7007` until a later task wires it into `./scripts/start-demo.sh`.** **Measured memory: 6.05 GiB** against the ~10 GB budget (M1 spec §9, row 6) — `docker stats northstar-control-plane`, three readings 3s apart on the complete stage-6 cluster with 35 pods Running: `6.048 / 6.047 / 6.049 GiB`. Stage 5's own measured baseline was 5.74 GiB across 34 pods (design §9), so Backstage's marginal cost is ~0.31 GiB — consistent with its own pod-level measurement (`kubectl exec deploy/backstage -- cat /proc/1/status`, steady-state `VmRSS` ~368 MiB) once page cache and the kind node's other overhead are folded in. Runs on stage 3's cluster config unchanged: there is deliberately no `deploy/kind/stage6.yaml` (amendment A1 again — Backstage needs no new host port) |

## Architecture (stage 1 workloads, on the stage-2 dataplane, with M3's approval path)

M2.1 Task 1 left this chain unchanged — Cilium sits underneath every hop of it,
and the trace was verified span-by-span against a pre-Cilium baseline to confirm
that. **M2.1 Task 3 added one hop**: agentgateway is interposed on the MCP
call. **M2.2 Task 4 added a branch off it**: before the gateway forwards a
`tools/call`, it asks the Policy Decision Service whether to. The whole chain
is still one distributed trace, and the decision is a span inside it (measured
— see `tests/e2e/test_gateway_trace.py`, `tests/e2e/test_policy_denial.py`, and
`docs/telemetry-contract.md`'s "agentgateway instrumentation, as measured" and
"The policy decision, as measured").

**M3 added a second chain, not a hop in the first one.** When a payment is
refused, that is the end of the automated trace; the approval that follows is a
separate request, started minutes later by a human in a different browser tab,
and it re-enters the *same* choke point — agentgateway, the same JWT-SVID
verification, the same Policy Decision Service — under a different identity.
The two traces are joined by a span link rather than a parent-child edge,
because neither caused the other in the way a parent causes a child (measured —
see `tests/e2e/test_supervised.py` and `docs/telemetry-contract.md`'s "Gap 2
(closed, M3)").

<pre class="mermaid">
flowchart LR
    br["browser · TS"] -->|A2A| ca["`claims-agent
      Python`"]
    ca -->|A2A| sa["`settlement-agent
      Python`"]
    sa -->|MCP| gw["`agentgateway
      Rust`"]
    gw -->|MCP| pm["`payments-mcp
      Python`"]
    pm -->|HTTP| pa["`payments-api
      Go`"]
    gw <-->|"`ExtMCP/gRPC — permit or deny,
      before forwarding`"| pds["`policy-decision-service
      Python`"]

    hb["browser · human"] --> ru["`reviewer-ui
      Python`"]
    ru -->|A2A| ss["`senior-settlement-agent · Python
      same image as settlement-agent,
      different ServiceAccount,
      own allowance row in the PDS`"]
    ss -->|MCP| gw
</pre>

The refused trace and the approved trace are two traces, linked:

<pre class="mermaid">
flowchart LR
    subgraph A["trace A — refused"]
        a1["browser → ... → settlement-agent"] --> a2["deny"]
    end
    subgraph B["trace B — approved"]
        b1["review.decision"] --> b2["settlement.execute_approved"]
    end
    b2 -.->|"span link"| a1
</pre>

The link's target is not the `policy.decision` span but the settlement-agent
span that was active when the refusal was written — its trace id *and* its span
id are persisted in the ledger's refusal record (`refusedTraceId`,
`refusedSpanId`) and read back at approval time. Both had to be stored: a
`Link` built with `span_id=0` is dropped by the OpenTelemetry SDK before any
exporter sees it, which M3 Task 3 found by reading `opentelemetry-sdk` 1.44.0's
`_is_valid_link` and then reproducing it in-process.

## Telemetry

Vendor-neutral: OTLP to an OpenTelemetry Collector, then Jaeger. Setting
`DASH0_AUTH_TOKEN` adds a second exporter with no application code change.

We use the telemetry the frameworks emit rather than inventing our own, and we
report the gaps we find. See [`docs/telemetry-contract.md`](docs/telemetry-contract.md).

## Documentation

- [`docs/demo-runbook.md`](docs/demo-runbook.md) — **the operator's document.** How the demo is brought up, the beat-by-beat run with what to say at each beat, every option available (which stage for which talk length, the environment switches, the three ways to flip the contract), the reset checklist between runs, and live triage. It deliberately prints almost no figures — it gives the command that produces each one live, because this repo's standing rule is *derive figures; never retype them*, and a number read on stage should have come off the cluster in front of the audience
- [`docs/architecture.md`](docs/architecture.md) — components, hops, protocols, and the trace, walked end to end
- [`docs/telemetry-contract.md`](docs/telemetry-contract.md) — verified instrumentation and convention gaps
- [`docs/upstream-findings.md`](docs/upstream-findings.md) — **fourteen** consolidated, filing-ready findings against `a2a-sdk`, FastMCP, `mcp`, `pydantic-ai-slim`, agentgateway v1.5.0, the OpenTelemetry Python/Go pair, and the `open-feature` Python packages, **none of them filed**: **thirteen** still open (one of those already fixed upstream but not adoptable here — Finding 7's HTTP-attribute half, merged but not yet released) and one closed on discovery. M5 added Findings 13 and 14: `openfeature-hooks-opentelemetry`'s `TracingHook` disagrees with its own sibling `openfeature-sdk` on which OTel signal carries a flag evaluation and on every shared attribute's spelling (13), and never emits two attributes it declares constants for, though the sibling package's own unused utility already reads them (14). M4 added Finding 12, whose principal subject is `pydantic-ai-slim`. (This sentence read "eleven … ten still open" and omitted `pydantic-ai-slim` until M4's final review. It is the **second** time this exact line has gone stale against the file it counts — the M3 correction is quoted immediately below — which is why the fix this time is to name the milestone that adds each finding, and why `docs/upstream-findings.md`'s own header carries the count a reader should trust.) M2.2 added three, all about agentgateway's ExtMCP policy hook. **M3 added one finding and one measurement.** The finding is Finding 11: `opentelemetry-sdk` 1.44.0 emits a W3C Trace Context **Level 2** flag byte (`-03`) that `go.opentelemetry.io/otel` v1.41.0 rejects outright, so the hop silently starts a second trace rather than failing — found while fixing it, proved with a one-hex-digit A/B control, and upstream's fix (otel-go v1.42.0, PR #7834) has since been taken: `payments-api` is on otel-go v1.46.0 and `otelhttp v0.71.0`, built on `golang:1.27.1-alpine`, with `go.mod` declaring `go 1.25.0` and pinning `toolchain go1.27.1`. The measurement is OQ-U1, "can a JSON-RPC *batch* smuggle a `tools/call` past the gateway's policy hook", now closed: it cannot, on this transport — agentgateway v1.5.0 rejects the batch at deserialization with HTTP 400 and no decision span is ever created. Nothing to file there, because failing closed is the right direction

  > **Corrected 2026-09-11.** This entry read *"ten consolidated, filing-ready findings … nine still open (one of those already fixed upstream but unreleased) and one closed on discovery … **M3 added none** — it added a measurement instead"* until Finding 11 was written up. M3's final fix round had already measured that finding and fixed it, recording it in source comments, `docs/architecture.md` and `docs/telemetry-contract.md` — every place except the ledger this line points at. The retired claim is quoted and dated rather than overwritten, the way the rest of this milestone's corrections do it.

  > **Corrected 2026-09-11 (second pass).** This entry then read *"ten still open (two of those already fixed upstream but not adoptable here)"* and closed Finding 11's clause with *"upstream's fix (otel-go v1.42.0, PR #7834) is blocked here on a Go 1.25 toolchain."* Both were true when Finding 11 was written and false about an hour later: commits `251ae83`..`01be011` took the toolchain bump and the upstream fix with it, so Finding 11 moved out of the "not adoptable" set, leaving only Finding 7's unreleased HTTP-attribute half in it — one, not two.
- [`docs/superpowers/specs/`](docs/superpowers/specs/) — designs
- [`docs/superpowers/plans/`](docs/superpowers/plans/) — implementation plans
