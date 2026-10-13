# Northstar Telemetry Contract

Status: **M8 (Human in the loop)** -- current milestone, branch
`m8-human-in-the-loop`, on top of **M7 (Bounded)** (branch `m7-bounded`);
before it **M6 (Golden path)** (branch `m6-golden-path`, tagged `m6-verified`);
M5 merged 2026-09-15 at `e62238f`. Full revision history follows.

Originally M1.1, revised through M1.2 for the settlement-agent's model-driven
decision logic, through M2.1 for agentgateway's interposition and SPIRE's
workload identity, through M2.2 (Decided) for the Policy Decision
Service, the `policy.decision` span (which closes Gap 3), and agentgateway's
own guardrail telemetry, and through **M3 (Supervised)** for the two spans
this repo owns outright on the human-in-the-loop path —
`settlement.execute_approved` (with this project's first span link) and
`review.decision` — which between them close Gap 2, and through **M4
(Observable)** for the metrics this system actually emits (one of six GenAI
agent metrics, the rest published as gaps), the trace-ingest path into
OpenSearch, the evidence view's queries, the policy-adherence score spans, and
**OQ-1, answered**, and through **M5 (Progressive autonomy)** for the
`feature_flag.evaluation` span event and log record the OpenFeature/flagd
sidecar now emits on every autonomy-level resolution, and the export
destinations those log records travel to (see the export-destinations table
and its recurrence note below). Revised at every milestone.

**This line stopped at M3 for the whole of M4** until Task 5's doc pass, while
five M4 sections were added below it. Recorded rather than silently fixed: it
is the same class the count-and-inventory guards in
`tests/unit/test_telemetry_contract_counts.py` and
`tests/unit/test_telemetry_contract_attribute_counts.py` exist for, in the one
part of the file no guard reads.

**2026-09-15, whole-branch review (J5): it happened again, second consecutive
recurrence.** This line stopped at M4 for the whole of M5 — no mention of M5,
flagd, OpenFeature, the contract or the logs pipeline — while the document
grew +569 net lines below it, including the section this note now sits
inside. Recorded rather than silently fixed, for the same reason as the note
above: this is the one part of the file the count-and-inventory guards do not
read, and it has now failed twice running.

## The governing rule

Use the telemetry the frameworks actually emit. Verify per component. Instrument
only our own code. Where a convention gap exists, report it upstream — never
paper over it with a bespoke convention.

## What the default suite proves, and what only the full run proves

**M4 Task 5 changed what this section describes, so the current state comes
first and the overturned reasoning is kept below it rather than deleted.**

A bare `pytest tests/` run (no environment variable set) now costs **no model
tokens at all**. Thirteen modules sit behind `RUN_BROWSER_TESTS=1`, of which
**3 of the 13 launch a browser** and **10 of the 13 start no browser** — they
ride the flag because they spend Anthropic tokens, and as of Task 5 that set is
closed: every e2e file that drives a
settlement is gated. (That split is derived, not typed: it was published as
"only one" in three places at once, in the *same* pass that was supposed to end
stale prose, and `tests/unit/test_telemetry_contract_counts.py` now greps
`tests/e2e` for `chromium.launch(` and fails this sentence against the answer.)
What a bare run therefore no longer proves is the A2A
chain — claims-agent → settlement-agent → payments-mcp → payments-api, one
trace, no orphans, `northstar.claim.id` correlated to the boundary below —
because the three files that prove it are the three that were driving the
settlements. It proves nothing about the browser either, and never did.

**Ruling M4-R15 (amended), discharged in M4 Task 5.** The cost of being wrong
each way is what decided it. Gate them, and a developer's quick run covers less
and they set one environment variable to get it back. Leave them, and every
bare `pytest tests/` — including a newcomer's, following this repo's own
README — silently spends three settlements' worth of tokens on someone's
account. The second is worse, and `scripts/smoke-test.sh` sets the flag
unconditionally, so the gate is unchanged. The measured before-and-after
figures, and the separate question of which ungated tests move the *ledger*
(three, deliberately still ungated, because an in-memory ledger row costs
nobody anything), are in the M4 Task 5 block below.

---

*Overturned by the ruling above; kept because the reasoning it replaced was
explicit and a later reader should be able to see what changed.*

~~**Fix round (final-review.md SF-8): that default run needs the live cluster
by design (always has, since M1.1) and, since M1.2, is no longer
token-free.**~~ `test_settlement_agent_trace.py` and `test_claims_chain_trace.py`
were the two ungated files that proved the chain described above; M1.2's Tasks
2-3 turned settlement-agent's decision into a real three-turn Claude call, so
every bare `pytest tests/` invocation then drove two live model-driven
settlements as a side effect of proving that chain — genuine cross-task
drift M1.2 Task 4's cost-gate rationale did not account for, since it
only reasoned about the one new gated file. It was ~~deliberately left
ungated~~ rather than folded behind `RUN_BROWSER_TESTS=1`, on the argument that
gating them would make a bare `pytest tests/` prove nothing about the backend
chain at all, in exchange for token-freedom this run was never actually free of
at the cluster-dependency level. That argument survives as a *cost*, and M4
Task 5 paid it knowingly: the bare run does now prove nothing about the backend
chain, and what it buys is that the bare run costs nothing.

**Correction, M4 Task 3 fix round 1: "two" above is "three".**
`test_claim_correlation.py` is a third ungated file that drives a settlement —
its module-scoped `correlated_trace` fixture POSTs the same A2A
`message/send` with `estimatedAmount: 6200` — and it was left out of the count
when that paragraph was written. Measured by reading all three files'
module-scoped fixtures while checking which tests a bare run actually pays
for; at the time, none of the three carried a `RUN_BROWSER_TESTS` gate. All
three carry one now.

### The first run of `pytest tests/`, and why it is left failing

**Ruled on in M4 Task 5 rather than carried as a concern.** With no cluster and
no port-forwards, a bare `pytest tests/` does not skip — it fails, with
`httpx.ConnectError` and `kubectl` errors across the e2e modules. That is not a
regression, and it is deliberately not being turned into a skip. A suite that
skips when it cannot reach the thing it asserts on is this repo's single most
recurrent defect class — rulings M4-R9 and M4-R14 both exist because of it —
and the cure for an ugly first run cannot be to introduce that class at the top
level for tidiness. **A red run saying "I could not reach the cluster" is the
correct answer to "are these assertions true?" when the cluster is not there.**

What was worth fixing was not the failure but its legibility, and that is what
this task did. The remedy — `./scripts/start-demo.sh`, which owns every
port-forward the suite needs — is now named in the README's own test table
beside the command that produces the failure; and `scripts/smoke-test.sh` has
probed all five surfaces with a real HTTP request, before running a single
test, since M2.2 (ruling R20: a dead `kubectl port-forward` keeps its listening
socket, so `lsof` alone calls it healthy). The supported entry point is the
gate. A developer who invokes pytest directly gets a failure that names the one
command which fixes it.

`tests/unit` is the subset that needs none of this: no cluster, no API key, no
network — and as of this task, no model tokens are spent by the wider `tests/`
run either.

**`./scripts/smoke-test.sh` — the actual M1.1 gate — has no default mode that
skips this.** It runs one unconditional invocation,
`RUN_BROWSER_TESTS=1 pytest tests -v`, over the whole `tests/` tree, every
time it is run. There is no environment variable, flag, or code path that
lets a caller run the gate itself and skip the browser hop — that is the
entire point of A22. A future reader who sees `./scripts/smoke-test.sh` finish
green has confirmed the browser hop; a reader who instead runs a bare
`pytest tests/` by hand — bypassing the gate — gets the weaker, default
result below, and should not conclude the top of the chain was checked.

Measured counts. **Provenance, restated for M3 rather than carried
forward — and the ordering of this section is load-bearing, so it is stated
outright: the CURRENT figures come first, and every earlier milestone's
figures below them carry their own milestone label.**
`tests/unit/test_telemetry_contract_counts.py` reads the *first*
`` `N passed, M xfailed` `` and the first
`` `N passed, M skipped, K xfailed` `` in this file and requires each to sum to
what `pytest --collect-only tests/` actually collects; a historical figure
moved above a current one would therefore be checked in its place. Keep the
current run first.

**2026-09-15 (M5 whole-branch review, fix wave Parts A+B) — the current
figures, and the gate row is PREDICTED, NOT MEASURED.**
The whole-branch review
(`.superpowers/sdd/2026-09-14-m5-progressive-autonomy/whole-branch-review.md`)
returned READY AFTER FIXES against the Task 6 gate commit `ae29785`. Part A
(code) closed six of its items with new tests: J1 (`start-demo.sh`'s "All
surfaces verified up." banner omitted `EVAL_SCORER_AVAILABLE` and
`FLAGD_AVAILABLE`, +1 test), J2 (the deny branch of the `(settlement-agent,
autonomous)` row had no rule-name assertion, +1 PDS test), J3 (the targeting
branches' only guard was a coupling constant scheduled to be rewritten, +2
tests moved into `tests/unit/test_autonomy_contract.py`), N6 (`unknown_caller`
named the wrong dimension for a known caller at an unknown level, +1 PDS test
for the split), M5-R33 (the root `.venv` had no pin guard, +7 tests in the new
`tests/unit/test_pins.py`), and carry-in 3 (the inert `_FAKE_CURL` promoted to
the shared, port-aware `tests/unit/_start_demo_fakes.py::port_aware_fake_curl`,
+1 test for ruling R20's previously-untested branch). Carry-in 5 (the "four of
the six" retyped count) was deleted from `test_stage2_cilium_config.py`'s
docstring, adding no test. Part B (this document, its guards, and the two
other documents below) closes C1 and everything else the review filed as
Major or Minor.

That takes the collected count from **368 to 379**: `tests/unit` gains
**11** (`test_pins.py`'s seven, two in `test_autonomy_contract.py` for J3, two
in `test_stage4_config.py` for J1 and carry-in 3), re-measured with
`.venv/bin/python -m pytest --collect-only -q tests/`. The
policy-decision-service's own suite gains **2** (J2's deny-branch test, N6's
unrecognised-identity test), taking it from 127 to **129** — re-measured with
`.venv/bin/python -m pytest --collect-only -q tests` from that service's own
directory, not derived by arithmetic on the row below.

Part B adds **5** more, all under `tests/unit`, taking the collected count from
**379 to 384**: C1's own derivation guard,
`tests/unit/test_telemetry_contract_export_destinations.py` (2 tests — the
table-vs-config check, and the logs-summary-sentence check); J5's status-line
guard, `tests/unit/test_doc_status_lines_name_the_current_milestone.py` (2
tests); and J7's extension of this very file, one new test comparing the
per-module gated spread published below against `_gated_test_count()`'s own
`per_file` dict. Re-measured the same way, not carried forward by arithmetic —
a guard that checks a count is itself a test, and this document's own history
(J4, this fix wave) is the argument against forgetting that a guard's own
collection weight needs re-deriving too.

M6 Task 1 adds six more, all under `tests/unit`, taking the
collected count from 384 to 390 (still within this same current block --
this addition is prose continuing the M5-review heading above, not a new
`**<date> ` heading, so `_current_figures_block()`'s boundary slice still
finds this text).
`tests/unit/test_stage6_backstage_config.py` (five tests)
pins design §4's read-only constraint on `deploy/k8s/backstage.yaml`'s
`backstage-read-only` ClusterRole -- no verb outside `get`/`list`/`watch`, no
wildcard verb/resource/apiGroup, no access to Secrets, and the Deployment and
ClusterRoleBinding both naming the `backstage` ServiceAccount -- parsed from
the manifest with `yaml.safe_load_all`, never grepped. One more,
`test_stage_six_creates_the_cluster_from_stage3_yaml_when_cold` (added to
`tests/unit/test_stage2_cilium_config.py`), is the cold-path proof M5-R27
demands: the real laptop cluster was already at stage 5 when this task ran,
so every real `--stage 6` run took the "cluster already exists" branch and
never exercised create-time `KIND_CONFIG` selection: this test runs the real
script hermetically (fake `kind`/`kubectl`/`docker`/`helm`, no real cluster,
registry or network) with no cluster present and asserts `kind create
cluster --config .../stage3.yaml` is what a cold `--stage 6` actually
invokes. Re-measured with
`.venv/bin/python -m pytest --collect-only -q tests/`, not carried forward by
arithmetic, for the same reason every prior figure in this section gives.

M6 Task 2 adds three more, all under `tests/unit`
(`tests/unit/test_catalog_matches_the_tree.py` — the guard that the software
catalog's eight `catalog-info.yaml` files are derived from a directory walk
over `agents/`, `apps/`, `platform/`, `systems/` and `tools/`, not a
hand-maintained list), taking the collected count from **390 to 393**,
re-measured the same way, prose continuing this same current block for the
same reason M6 Task 1's own addition above does.

M6 Task 3 adds twenty-five more: **ten** under `tests/unit`
(`tests/unit/test_autonomy_writer_rbac.py`, parsing `deploy/k8s/
backstage.yaml` the same way `test_stage6_backstage_config.py` parses Task
1's read-only ClusterRole — the write Role's verbs are exactly `get`/`list`/
`patch`, `resourceNames` is exactly `[northstar-autonomy]`, no wildcard
anywhere, neither Backstage RBAC object grants Secrets, the write
ServiceAccount is separate from the read-only one, and the Deployment mounts
its token read-only while keeping its own `serviceAccountName` as the
read-only identity) and **fifteen** under `tests/e2e`
(`tests/e2e/test_autonomy_writer_live_rbac.py`, the live half this task's own
brief names by name — `kubectl auth can-i --as=` proving the positive grant,
the negative half design §4 asks for by name (a different FeatureFlag denied,
`delete` denied, Secrets unreadable), a namespace/cluster-wide-access control,
and a control that M6-R5 still holds: the read-only ServiceAccount still
cannot touch `featureflags` at all). Not gated behind `RUN_BROWSER_TESTS=1`,
for the same reason `test_egress_policy.py` and `test_gateway_config_drift.py`
give for their own live-cluster-but-no-browser suites: no model tokens, no
browser, a dozen `kubectl` calls under a second. Taking the collected count
from **393 to 418**, re-measured the same way.

**M6 Task 3's fix round** (closing the opus review's NOT APPROVED verdict)
adds **two** more under `tests/e2e`
(`tests/e2e/test_backstage_image_matches_head.py` — M6-R9's durable fix:
nothing in this repo asserted the running image contains the committed
code, which is how Task 3's own security boundary was reported as verified
against an image that predated the commit under review; this compares the
image's baked-in `org.opencontainers.image.revision` label against
`git rev-parse HEAD`). Not gated behind `RUN_BROWSER_TESTS=1`, same reasons
as the fifteen above. Taking the collected count from **418 to 420**,
re-measured the same way.

**M6 Task 4 adds nine more, taking the collected count from 420 to 429.**
**Two** under `tests/unit`, both in `tests/unit/test_stage4_config.py`:
`start-demo.sh`'s own "All surfaces verified up." banner omitted
`BACKSTAGE_AVAILABLE` from its gate condition — the same J1 shape the
whole-branch review found for `EVAL_SCORER_AVAILABLE`/`FLAGD_AVAILABLE` one
milestone earlier, now fixed for the surface this task adds — plus the
matching skip-path test. **Seven** under `tests/e2e`, all in the new
`tests/e2e/test_golden_path.py` — design §5's anti-vacuity e2e: an
authenticated/unauthenticated HTTP control on the port-forward this file
establishes itself; a live assertion that the running code is the committed
code (M6-R9, restated as a route only Task 3's plugin serves, rather than
repeating `test_backstage_image_matches_head.py`'s own image-label check);
the catalog checked against the same tree walk
`test_catalog_matches_the_tree.py` already derives, not a retyped list; the
card's read on the refusing contract; a no-redeploy check that also proves
the card's HTTP write reaches the live `FeatureFlag` object (an independent
`kubectl get`, not merely the HTTP response); the milestone's own claim —
driving the card's write over HTTP flips a refused settlement to executed,
with no redeploy, then reverts; and design §10's own instruction ("Task 4's
e2e should assert the span lands, the way `test_opensearch_trace_ingest.py`
already does for `policy.decision`") — a live OpenSearch query for the
`northstar.autonomy_contract.change` span design §10 already names as the
one durable signal of the three the write produces, gated on stage 4's own
presence rather than assumed. Re-measured the same way.

**Whole-branch review fix wave adds one more, all under `tests/unit`, taking
the collected count from 429 to 430** (still within this same current block,
for the reason M6 Task 1's own addition above already states): M7's fix to
`tests/unit/test_rebuild_and_deploy_guard.py`, asserting `.catalog-stage/`'s
actual contents rather than the byte-identical stub fixture that let a
typo'd `cp` source substitute the wrong file silently. Not gated (no model
call, no browser -- a sandboxed run of `scripts/rebuild-and-deploy.sh` with
docker/kubectl faked, same as its siblings in that file). Re-measured the
same way.

**The internal-platform pass adds eight, all under `tests/unit`, taking
the collected count from 430 to 438** (still within this same current block,
for the reason the two additions above already state): the new guard
`tests/unit/test_every_component_has_techdocs.py`, which walks the component
tree — reusing `test_catalog_matches_the_tree.py`'s own `_component_dirs`
rather than reimplementing it — and asserts the three separate things that
each have to hold for a component's Docs tab to render anything: `mkdocs.yml`
beside its `catalog-info.yaml`, a `docs/index.md` inside it, and
`backstage.io/techdocs-ref: dir:.` on the Component entity. A component can
satisfy any two of the three and still show an empty tab, which is why it is
three assertions and not one. A fifth guard covers the System entity: the nine components each
document themselves, and until this pass nothing rendered what they add
up to -- the portal could describe `settlement-agent` but not Northstar.
It asserts the same three links at the repo root, where the System's own
`dir:.` resolves.

The eighth parses every catalog file the way Backstage parses it, rejecting
duplicate mapping keys. PyYAML keeps the last value and reports success; the
JS parser Backstage uses raises and drops every entity in the file. A second
`providesApis:` added during this pass cost four entities -- two Components
and both agent-card APIs -- while every local check stayed green. A fourth was added after two of the nine
`mkdocs.yml` files written for this pass turned out not to parse -- an
unquoted `site_description` containing a colon -- which the existence check
above was green for, and which surfaces only in the build log streamed into
the Docs tab at the moment someone clicks it.

The other two are `tests/unit/test_catalog_links_are_absolute_urls.py`, written
against a defect this same pass shipped and the running system reported almost
silently: a relative `links[].url` makes Backstage's entity policy reject the
**whole entity**, so `policy-decision-service` was absent from the catalog
entirely — no page, no Docs tab, no Kubernetes card — while the backend logged
one `warn`, the pod stayed Ready, and both sibling catalog guards stayed green
because each reads the repo rather than what the catalog ingested. None of these
are gated (no model call, no browser — a directory walk and a YAML parse).
Re-measured the same way.

**The mermaid pass adds eight, all under `tests/unit`, taking the collected
count from 438 to 446** (still within this same current block, for the reason
the additions above already state): `tests/unit/test_mermaid_diagrams.py`,
which asserts the three separate things that each have to hold for a diagram
to reach a reader. The diagram has to parse — handed to mermaid itself through
`platform/backstage/packages/app/scripts/parse-mermaid.mjs` rather than to a
reimplementation of its grammar, on the same reasoning that gave
`test_catalog_links_are_absolute_urls.py` a strict YAML loader: a checker that
disagrees with the consumer reports success for input the consumer rejects.
The diagram has to be written as a raw `<pre class="mermaid">` block and not
as the idiomatic ```mermaid fence — a fence needs MkDocs to be told about it,
which needs a `!!python/name:` tag in `mkdocs.yml`, and **Backstage parses
`mkdocs.yml` itself, in JavaScript, before MkDocs ever sees it**, refusing
every Python YAML tag outside a hard-coded allowlist of three
(`ALLOWED_PYTHON_YAML_TAGS` in `@backstage/plugin-techdocs-node`). That was
found the expensive way: a green suite, a Ready pod, and every page of the
System's documentation 404ing behind a red banner — so a sixth test asserts
that no `mkdocs.yml` carries a tag Backstage rejects. The raw block then
produced a second defect of its own, and a seventh test for it: the contents
of a raw HTML block **are HTML**, so the `<br/>` the diagrams used for line
breaks was an element by the time the renderer read the block, and
`textContent` dropped it — mermaid drew `browser["browserTS, manual root
span"]`, with no line break and no error, on a page that looked fine. A guard
that reads the file was validating a different string than the one mermaid
received. The Backstage app has to list the renderer among its `features`.
And the renderer has to hold no colour of its own: mermaid's `dark` theme
draws near-black boxes with white rules, which on a Backstage page reads as a
screenshot from somewhere else, so the diagrams use mermaid's `base` theme
with a palette read off the live MUI theme at render time — and an eighth test
asserts there is not one colour literal in the module, which is the same
decision `App.tsx` records about the app as a whole. All three fail the same quiet way: the page
loads, the pod stays Ready, and a human sees a wall of `flowchart TD` where a
picture should be. None is gated (no model call, no browser — a YAML parse, a
text match, and a node subprocess against a jsdom DOM). Re-measured the same
way.
**The page-banner pass adds eight, all under `tests/unit`, taking the collected
count from 446 to 454** (still within this same current block, for the reason
the additions above already state): `tests/unit/test_page_banner.py`, which
guards the module that puts Backstage's own page-theme gradient across the top
of every page rather than only the two that were never migrated off the classic
`<Page>`/`<Header>` pair. Four separate things have to hold, and **every one of
them fails silently** — the app builds, the pod goes Ready, the suite stays
green, and the page simply looks the way it did before. The module has to be
listed in `App.tsx`'s `features` array, because `createApp` ignores an
imported-but-unlisted feature without a word (checked with comments stripped,
both forms, for the reason the mermaid guard above records). The override has
to claim the id `app/layout`, which is assembled from the module's `pluginId`
and the extension's `name` in two different files — either one wrong and the
module is well-formed, type-checks, builds, and attaches to nothing. The CSS
selector has to keep its trailing `__`: `[class*="bui-PluginHeader"]` without
it also matches every descendant Backstage UI names, and the gradient is then
redrawn from each one's own origin. And every `themeId` has to name a real
built-in page theme, because `getPageTheme` resolves an unknown id to the
*default* theme rather than raising — so `documentaton` renders teal instead of
pink and says nothing about it; the list of ten it is checked against is itself
cross-checked against the installed `@backstage/theme`, so the copy cannot rot.
An eighth test asserts there is not one colour literal in the module, the same
decision, for the same reason, as the mermaid module and as `App.tsx`'s own
note. None is gated (no model call, no browser — a text match and a regex over
two source files). Re-measured the same way.

**And the page-banner pass adds a fourteenth gated module, taking the collected
count from 454 to 456**: `tests/e2e/test_backstage_page_bands.py`, which is the
proof the unit guard above is explicitly not. All four of that guard's checks
can hold while nothing is painted, because what decides whether the gradient
lands is a CSS cascade inside a build — Backstage UI ships its component styles
in `@layer components`, this module's rules are unlayered, and unlayered wins.
Nothing in the repo can see that; the browser can. The test opens each of ten
routes on the running portal, reads the computed `background-image` off the
element a human is looking at, and asserts both that it carries a
`linear-gradient` at all and that the gradient's colours are the ones
`@backstage/theme` defines for that route's page theme — parsed out of the
installed package at test time, never typed into the test, because a literal
there would be a second place the palette lives. **One** of its two tests is
gated (the palette parse needs no browser and no cluster; the page walk needs
both), and the gated one launches a browser, which is what moves the browser
split below. It costs no model call and no settlement: ten page loads, about
five seconds.

**M7 (Bounded) takes the collected count from 456 to 489, and moves no gated
figure.** Thirty-three new tests: `tests/unit/test_pins.py` three (Kyverno's chart
and app version, read back out of `scripts/setup-kind.sh`'s assignment lines),
`tests/unit/test_stage7_keystone_config.py` nineteen (the keystone policy and
the over-grant fixture, parsed with `yaml.safe_load`, so no comment can supply
an assertion; plus the gate's and the pre-flight's stage-7 checks, comment-stripped),
and `tests/e2e/test_keystone.py` ten (RBAC refuses the agent;
Kyverno refuses it even when RBAC allows it; a human can still patch and it is
reverted; an agent cannot create a `PolicyException`; the denial reaches Jaeger
as a standalone Kyverno trace; and, parametrized, that an over-granted agent is
refused on a pod running as `senior-settlement-agent`, a cluster-wide Cilium
policy, a SPIRE `ClusterSPIFFEID` and a legacy Kyverno `ClusterPolicy`, plus a
`serviceaccounts/token` request). The last file needs the live stage-7 cluster and
spends no model tokens, so it carries no `RUN_BROWSER_TESTS` gate and is not in
the gated spread below: on a bare run with the cluster up it passes, and on a
cluster below stage 7 it skips (only when Kyverno's CRD is absent; if Kyverno is
installed and the policy is not, it fails, which a mutation run verified).
`CURRENT_STAGE` in `tests/unit/test_stage2_cilium_config.py` moved from 5 to 7 in
the same milestone, a value change that adds no test. The thirty-third is
`tests/unit/test_stage4_config.py`'s third-party collision test, added after
M7's first gate run (at `f87e950`: `483 passed, 4 failed, 1 xfailed`) found
Kyverno's `admission.request.kind` / `admission.request.kind.*` keys colliding
in OpenSearch; it pins the renames on the OpenSearch branch.

**M7's five-minute demo flow (2026-10-12) takes the collected count from 489 to
507, and moves the gated figure from 76 to 83.** Eighteen new tests.
`tests/e2e/test_demo_flow.py` adds **seven**, and **all seven are gated**: it runs
`scripts/demo` end to end (`guard`, `claim 6200` refused, `autonomy autonomous`,
`claim 6200` executed, `autonomy assisted`, `keystone`, `evidence`, `reset`), and
the two `claim` steps are real model calls, one of which executes a real
EUR 6,200 payment with the flag flipped. It starts no browser, so the browser
split below becomes **4 of the 15** (it was 4 of the 14), a fifteenth gated
module that does not launch one. `tests/unit/test_demo_driver.py` adds **eleven**,
none gated: they compare every fact the driver restates (reach URLs, the keystone
policy's name, the over-grant's object names, the flag path) with the file that
owns it, and exercise `evidence`'s bounded wait for OpenSearch ingest lag. No existing test was added, removed or changed in what it asserts: the
shared helpers (`_submit_claim`, `mint_svid`, `call_tool_directly`,
`gateway_url`, `evidence_for`, `_kubectl`, `_opensearch`) moved into
`tests/harness/live.py` so the tests and the driver run the same code.

**The gate row below is PREDICTED, NOT MEASURED again, as expected.** It was
MEASURED at `a9a99a5`; this change touches `scripts/` (the driver, the shared
reach probe `scripts/reach-probe.py`, and `scripts/beat-reach.sh`, which now
reads that probe) and `tests/`, both `GATE_PATHS` entries, so the row describes a
tree this change has since altered. What has to happen to make it MEASURED: one
`./scripts/smoke-test.sh` after review, with `test_demo_flow.py` joining it
(`RUN_BROWSER_TESTS=1` is set by the gate, so it runs unskipped). The new file was
run on its own against the live stage-7 cluster, 7 passed in 55.8s, which is not
the gate having run.

Bare-suite row, derived from that same collection: 507 collected − 83 gated −
**0** xfailed = `424 passed, 83 skipped, 0 xfailed`.

The gated figure was **76** before that change and is **83** now — re-measured the same way as every figure
below it, a `--collect-only` run with a plugin that counts items carrying a
`skipif` whose reason names `RUN_BROWSER_TESTS` and whose condition is true
with the variable unset — because none of Part A's eleven new `tests/unit`
tests, Part B's five new `tests/unit` tests, M6 Task 1's six new `tests/unit`
tests, M6 Task 2's three new `tests/unit` tests, M6 Task 3's twenty-five new
tests, M6 Task 3's fix-round two new tests, or the two new PDS tests carries
one (PDS tests are not collected by this measure at all, `tests/unit`-only);
M6 Task 4 adds exactly **one** — `test_golden_path.py`'s own settlement-driving
test, the only one of its seven that spends a model call and a browser; the
whole-branch review fix wave's own addition (M7's `test_rebuild_and_deploy_guard.py`
test) is `tests/unit`-only and carries no `skipif`, so it adds none, and the
internal-platform pass's eight new `tests/unit` tests, the mermaid pass's eight
and the page-banner pass's eight `tests/unit` tests carry none either; the
page-banner pass's own e2e file adds exactly **one** — and
reports 83 of 507, with the per-module spread and the browser split both
moving for the first time this milestone. Full spread:
`test_settlement_llm_trace.py` 9,
`test_prometheus_metrics_ingest.py` 10, `test_browser_root_span.py` 8,
`test_settlement_agent_trace.py` 8, `test_claims_chain_trace.py` 7,
`test_claim_correlation.py` 6, `test_gateway_trace.py` 6,
`test_supervised.py` 6, `test_policy_scores.py` 5,
`test_demo_flow.py` 7, `test_evidence_view.py` 4, `test_policy_denial.py` 4,
`test_backstage_page_bands.py` 1, `test_golden_path.py` 1,
`test_progressive_autonomy.py` 1. **The browser split changes twice this
milestone, having not moved since M4 Task 5.** First a thirteenth gated module
joined without launching its own browser (`tests/e2e/test_golden_path.py`
imports `_drive_browser` from `test_policy_denial.py` rather than calling
`chromium.launch(` itself, the same reason `test_progressive_autonomy.py` was
never one of the three either). Then the page-banner pass added a fourteenth
that *does* call `chromium.launch(` — it has no page to drive, only pages to
look at — so the split was 4 of the 14 launching a browser and 10 of the 14
starting none, until the demo-flow file made it a fifteenth that starts none:
**4 of the 15 launch a browser** and **11 of the 15 start no browser**.

Gate row: **PREDICTED, NOT MEASURED** — M8 Task 0 bumps agentgateway to v1.6.0 and
changes the deny path, the gateway's span vocabulary, the collector's OpenSearch
rename list and the settlement-agent image, all `GATE_PATHS` files, so the row
below describes a tree this branch has since altered. The collected count is
unchanged at 507 (no test was added to `tests/`; two were renamed or rewritten in
place). What has to happen to make it MEASURED: one `./scripts/smoke-test.sh` at
the end of M8. The files this task touches were run on their own against the live
stage-7 cluster: `test_policy_denial`, `test_gateway_trace`,
`test_opensearch_trace_ingest`, `test_caller_identity`,
`test_gateway_config_drift` and `test_gateway_is_the_only_route`, 29 passed in
116s, which is not the gate having run. The last measured row follows, from M7:
M7's final gate run was at commit `d4bed6e`,
`./scripts/smoke-test.sh`, run by the orchestrator on 2026-10-12 against the
stage-7 cluster with Backstage rebuilt from that clean tree and warmed, exit
**0**: `506 passed, 1 xfailed`, in 643.67s (10m43s), zero skips (`grep -c
SKIPPED` over the full output returns 0). That is the figure the collection
predicted (507 collected). The run before it, at `a9a99a5`, was
`./scripts/smoke-test.sh` (which sets `RUN_BROWSER_TESTS=1` itself), run by the
orchestrator on 2026-10-12 against a stage-7 cluster with Backstage rebuilt from
that clean tree, exit **0**: 488 passed, 1 xfailed, in 597.16s (9m57s), which
was the figure the collection predicted that day. Two earlier M7 runs are part of the
record: at `f87e950`, `483 passed, 4 failed` (Kyverno's colliding span keys
rejected by OpenSearch, twice; criterion 4's accounted set; a Backstage image
from an older commit), fixed in `6419ac8`; at `6419ac8`, `486 passed, 2 failed`
(the Backstage image marked `-dirty` by uncommitted, unrelated `talk/` files,
and a cold-pod band-walk flake that passed on a warm pod), cleared by
committing those files in `a9a99a5` and warming Backstage before the run.

The gate run before it, M6's, is kept for the record. It was at
`6588570`,
`RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh`, run by the orchestrator,
exit **0**, 429 passed and 1 xfailed in 597.91s (9m57s), with zero skips
(`grep -c SKIPPED` over the run's full output returned 0). 429 + 1 = 430, which
was the full collection that day. It is **488 today**, and the fifty-eight-test gap
is itself the evidence that this row is stale rather than merely unre-run.

**What demoted it, 2026-09-15.** A visual pass over the two demo surfaces —
`apps/customer-ui` (restyled, plus a real input defect fixed: the amount field
held a `number`, so clearing it made `Number("") === 0` and the controlled
input could never be emptied, turning a hand-typed `5800` into `05800`) and
`apps/reviewer-ui/render.py` (the same institutional identity, hand-mirrored
because that service ships no frontend toolchain). Four files under `apps/`,
which is a `GATE_PATHS` entry. **No test count changed** — the collection was
still 430, `tests/unit` still 260, reviewer-ui's own suite still 56 — so this
block's arithmetic was untouched; what is untouched and what is *measured* are
different claims, and this row makes the smaller one.

**What widened the gap, same day.** The internal-platform pass over Backstage:
a Northstar theme and logo replacing the scaffold's, TechDocs generated inside
the pod (`builder: local`, `generator.runIn: local`, python3 +
`mkdocs-techdocs-core` installed in the image), a `mkdocs.yml` and
`docs/index.md` written for each of the eight components plus Backstage
itself, enriched `catalog-info.yaml` entities, and the new guard above. This
touches `platform/`, `apps/`, `agents/`, `systems/`, `tools/`, `scripts/` and
`tests/` — seven `GATE_PATHS` entries — and it **does** move the collection,
430 → 438. The mermaid pass that followed moved it again, 438 → 446, across
`platform/`, `tests/` and the documentation itself. So this row is now stale on
both counts: not re-run, and no longer arithmetically consistent with the block
above it.

What has to happen before this reads MEASURED again: one
`RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` against the rebuilt images. The
three browser-driving suites (`test_browser_root_span.py`,
`test_policy_denial.py`, `test_supervised.py`) are the ones with standing in
this change — they address the restyled markup by
`input >> nth=0`, `input[type=number]`, `button[type=submit]`,
`h2:text-is('Outcome') + pre`, `form[action="/review/…"]`,
`input[name="reviewer_id"]` and `button.approve`. Every one of those was
preserved deliberately and checked against the built output, and the
`h2 + pre` adjacency was proved by rendering the real component with a stubbed
result through `scripts/mutate-guard.sh`; none of that is the same as the gate
having run.

**This run is the first in the series to also execute the Backstage jest
suites.** The whole-branch review's Critical finding was that all six of M6's
Backstage test files — 744 lines, including the jest guards for every one of
Task 3's four correctness bugs — were run by **nothing**: `grep -ci backstage
scripts/smoke-test.sh` returned **0** and `_PER_SERVICE_SUITES` had eight rows.
Each of those guards had been shown going green-to-red exactly once, by hand, in
a transcript. The gate now runs them (`Test Suites: 7 passed`, `Tests: 37
passed`) and `_PER_SERVICE_SUITES` has a ninth row, so the per-service line
below reads **all nine**, not all eight.

**The ordering is deliberate and is M5's most expensive lesson.** M5 ran its
gate *before* its whole-branch review; the review's fix wave then changed twelve
`GATE_PATHS` files, the measured row was demoted to `PREDICTED, NOT MEASURED`,
and M5 merged on a row that described a tree two commits and a fix wave old.
This run happens **after** M6's whole-branch review and after its eleven-fix
wave, so `6588570` is the tree that merges. Nothing in `GATE_PATHS` has changed
since.

`tests/unit` alone collects **260** (222 before the M5 whole-branch review's
fix wave, 233 after Part A, 238 after Part B, 244 after M6 Task 1's own
six-test addition, 247 after M6 Task 2's own three-test addition, 257 after
M6 Task 3's own ten-test addition, 259 after M6 Task 4's own two-test
addition, 260 after the whole-branch review fix wave's own one-test
addition).

---

**2026-09-15 (M5 Task 6) — superseded by the row above, and kept the way every
earlier row is: it was the current figure at the time it was written.**
Task 6 flips the `northstar-autonomy` contract live (against the deployed
`FeatureFlag` object, never the committed `deploy/platform/openfeature/featureflag.yaml`
— see ruling M5-R29/M5-R31's `CURRENT_LEVEL` coupling, which this design
sidesteps rather than fights) and adds the milestone's anti-vacuity proof:
one new e2e file, `tests/e2e/test_progressive_autonomy.py`, two tests — one
drives a real settlement twice, once refused at `assisted` and once settled
at `autonomous`, with nothing else about the request changed between the
two calls; the other confirms no pod was redeployed by the flip (same UIDs,
same image digests, before and after). Only the first is gated (it spends
model tokens); the second inspects the live cluster's own pod metadata and
needs no browser and no model call. One new shared module,
`tests/harness/contract.py`, is the one place that reads, flips and verifies
the live contract — it is not itself collected (no `test_` prefix) and adds
nothing to any count below. Two existing e2e fixtures
(`test_policy_denial.py::denial_run`, `test_supervised.py::denial_run`) each
gained one defensive precondition line, `contract.assert_assisted(...)`,
converting their previously-incidental protection (alphabetical file
collection order happening to run before any flip) into an explicit, tested
one; neither fixture gained or lost a test. One new test lands in
`tests/e2e/test_evidence_view.py`
(`test_the_contract_signals_reach_the_decision_panel`), reading the two
contract keys now on `evidence_for()`'s dict.

That takes the collected count from **365 to 368**, re-measured with
`.venv/bin/python -m pytest --collect-only -q tests/`.

Bare-suite row, derived from that same collection: 368 collected − 74 gated −
**0** xfailed = `294 passed, 74 skipped, 0 xfailed`.

The gated figure moves from **72 to 74** — measured the same way every
figure below it was, a `--collect-only` run with a plugin that counts items
carrying a `skipif` whose reason names `RUN_BROWSER_TESTS` and whose
condition is true with the variable unset — and reports 74 of 368, with the
spread changed in exactly two places: `test_evidence_view.py` goes from 3 to
**4** (the new test reads the module-level `pytestmark` gate the same way
its three siblings already do, rather than adding its own), and
`test_progressive_autonomy.py` is a new entry at **1** (only the
settlement-driving test is gated; the pod-UID/image-digest comparison is
not). Full spread: `test_settlement_llm_trace.py` 9,
`test_prometheus_metrics_ingest.py` 10, `test_browser_root_span.py` 8,
`test_settlement_agent_trace.py` 8, `test_claims_chain_trace.py` 7,
`test_claim_correlation.py` 6, `test_gateway_trace.py` 6,
`test_supervised.py` 6, `test_policy_scores.py` 5,
`test_evidence_view.py` 4, `test_policy_denial.py` 4,
`test_progressive_autonomy.py` 1. The browser split moves from **3 of the
11**/**8 of the 11** to **3 of the 12 launch a browser** and **9 of the 12
start no browser**: the twelfth gated module starts no browser, so the
numerator is unchanged and only the denominator (and its complement) moved.

Gate row: **MEASURED** — this branch's gate run was at commit `ae29785`,
`RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh`, run by the orchestrator,
exit **0**: `367 passed, 1 xfailed`, in 509.96s (8m29s), with **zero skips**
(`grep -c SKIPPED` over the run's full output returns 0 — `RUN_BROWSER_TESTS=1`
ungates all 74 gated tests, so the gate really does execute the whole of
`tests/`: 367 + 1 xfailed = the 368 collected). This matches the figure the
collection predicted, which is the point of publishing the prediction first.
**M5-R19 is discharged explicitly rather than by implication:**
`test_supervised.py::test_criterion_4_the_pds_permits_under_the_same_rule_for_a_different_caller`
**PASSED** in this run — the criterion whose asserted rule literal Task 3
changed and could not verify live. Both anti-vacuity tests also passed live:
`test_the_same_claim_refuses_then_settles_when_only_the_contract_changes` and
`test_no_workload_was_redeployed_by_the_flip`.

The one xfail (`tests/e2e/test_claim_correlation.py`) is inside a gated module,
so it still skips rather than xfails on a bare run — the bare row's `0 xfailed`
above is not a typo.

**Dated note, 2026-09-15: the first attempt at this run failed, and what it
found is worth keeping.** It exited **1** in Step 1 on
`tools/payments-mcp`'s `test_pytest_is_pinned_clear_of_pysec_2026_1845` —
`assert '9.0.3' == '9.1.1'`. Ruling M5-R20 named **two** stale service venvs
(`agents/settlement-agent`, `platform/eval-scorer`) and those two had been
recreated; in fact Task 1's pytest bump touched seven `requirements.txt` files,
and **four** venvs were still stale — `agents/claims-agent`, `apps/reviewer-ui`,
`tools/payments-mcp`, **and the repo-root `.venv` itself**, which is pinned by
`tests/requirements.txt` but guarded by no `test_pins.py` at all, which is
exactly why it went unnoticed. All seven venvs now measure `pytest 9.1.1`. The
failed attempt reached no live settlement, so it consumed none of the
milestone's single-gate-run budget. **The lesson is the repo's own: a count in a
ruling is a claim, and claims get re-derived — `find . -name .venv` and compare
each against its pin, rather than trusting the number written down.**

`tests/unit` alone collects **222**, unchanged: nothing this task added lives
under `tests/unit` (`test_kube_helper.py`'s one-line allowlist addition is an
edit to an existing test's data, not a new test).

**2026-09-14 (M5 Task 4) — superseded by the row above, and kept the way every
earlier row is: it was the current figure at the time it was written.**
(J4, whole-branch review: this heading claimed the gate row was still
PREDICTED after the row above had already measured it. Reworded rather than
silently corrected, per the standing rule two blocks above.)
Task 4 gives every flag evaluation a second signal: a genuine
`feature_flag.evaluation` log record (`pds/autonomy.py::emit_evaluation_event`,
sent through the logs pipeline `pds/telemetry.py` now wires end to end to
OpenSearch), alongside the span event `TracingHook` already put there. Two
new tests land under `tests/unit` (`tests/unit/test_autonomy_contract.py`):
one guards that no deprecated feature-flag attribute spelling is ever emitted,
with the deprecated set *derived* from the installed
`opentelemetry.semconv._incubating.attributes.feature_flag_attributes` module
rather than retyped; one cross-checks that derived set against the four
literal spellings this task's own plan named, so a future semconv bump that
changes the deprecated set is caught rather than silently trusted. One new
e2e file, `tests/e2e/test_flag_evaluation_telemetry.py`, adds five tests
against the live cluster: the parity guard (collector-accepted log records
must equal what OpenSearch holds, for the same reason `test_opensearch_trace_ingest.py`
already asserts this for spans — loss looks exactly like sampling from
outside); that the live event is the semconv event with the hook's two gaps
filled; that no fabricated `version`/`set_id` is invented where the flag
carries none; that the live event's own timestamp is real, not the epoch (see
the fix round two paragraphs below); and that the collector rejected nothing
during the run. None of the five carries a `RUN_BROWSER_TESTS` `skipif` — the
gated figure below is unchanged.

That takes the collected count from **355 to 362**, re-measured with
`.venv/bin/python -m pytest --collect-only -q tests/`.

**Dated note, 2026-09-14 (M5-R27, after Task 4):** the figures in this section
moved again after the sentence above was written, and the rows below are the
current ones. Closing the cold-bring-up ordering defect added **three** tests to
`tests/unit/test_rebuild_and_deploy_guard.py` (a NotFound companion is skipped;
any other probe failure aborts; a RED demonstration against the naive
`|| continue`), taking collection **362 → 365**. None is gated, so the gated
figure is untouched. The "355 to 362" above is left standing as Task 4's dated
record, the way the M2.2 plan's inverted `failureMode` default is.

**Dated note, 2026-09-14 (fixing an unclaimed pre-existing e2e failure, found
before Task 6): the gated figure below moves from 71 to 72.**
`test_prometheus_metrics_ingest.py`'s reviewer-ui row of the per-service
positive control
(`test_each_service_demonstrably_gets_a_metric_all_the_way_to_prometheus
[reviewer-ui]`) was asserting presence unconditionally although nothing
ungated ever drives reviewer-ui traffic: every e2e file that reaches its
`GET /` or `POST /review/{id}` (`test_evidence_view.py`,
`test_policy_scores.py`, `test_supervised.py` criteria 1-6) is itself gated
behind `RUN_BROWSER_TESTS=1`, and reviewer-ui's own readiness probe
deliberately makes no ledger call. The row failed every bare run for that
reason (`assert []`, not staleness) -- confirmed pre-existing (no file it or
its traffic sources depend on changed between `0ae64f9`'s parent and this
note). It now carries its own gate, `_REVIEWER_UI_TRAFFIC_REQUIRED`, taking
`test_prometheus_metrics_ingest.py`'s own count from 9 to 10. The rows below
are the updated ones; the "71" figures above this note are left standing as
Task 4/M5-R27's dated record.

Bare-suite row, derived from that same collection: 365 collected − 72 gated −
**0** xfailed = `293 passed, 72 skipped, 0 xfailed`.

The gated figure is **72** (all seven of Task 4's new tests — two in
`tests/unit`, five in `tests/e2e` — and all three of M5-R27's carry no
`skipif` naming `RUN_BROWSER_TESTS`, so none of those ten is gated; the one
new gate is the reviewer-ui row above) — and reports 72 of 365, with the same
per-module spread as the row beneath this one. The browser split is
unchanged: **3 of the 12 launch a browser** and **9 of the 12 start no
browser** -- the module was already gated and already browser-less, so this
moved a test count within it, not a module count.

Gate row: **PREDICTED, NOT MEASURED**, unchanged from the row below -- this
branch's gate run was at commit `b215426` (the M4 final-review fix wave
further below), and it is measured against no later tree than that one. This
task changes files under `gate_paths` (`platform/`, `deploy/`, `tests/`,
`docs/`) without a new gate run to back it. The prediction from collection
(365 − 1 xfailed = 364) is `364 passed, 1 xfailed`; nothing claims that
figure was measured against this tree.

**A bare `pytest tests/` does not currently reach that prediction**, and the
gap is not this section's doing: two e2e tests fail against the live stage-5
cluster — `test_policy_denial.py`'s criterion 4 and
`test_prometheus_metrics_ingest.py`'s reviewer-ui assertion. Both were found
failing by M5 Task 4, both predate it, and neither touches a file Task 4 or
M5-R27 modified. They are recorded here rather than left for the gate run to
discover, and they are **unclaimed** — see the ledger.

`tests/unit` alone collects **222** (219 after Task 4, 217 before it).

**A defect this task found and fixed, worth stating here rather than only in
`pds/autonomy.py`'s own comment: the first live-driven `feature_flag.evaluation`
log record measured in OpenSearch carried `"@timestamp": "1970-01-01T00:00:00Z"`.**
The installed SDK (`opentelemetry-sdk==1.44.0`) sets `LogRecord.timestamp`
verbatim from whatever is passed — unlike `observed_timestamp`, it does not
default to `time_ns()` when `None` — and the emitter's first version passed
nothing. Fixed by passing `timestamp=time_ns()` explicitly; both the hermetic
guard test above and the live e2e test
(`test_the_live_events_carry_a_real_timestamp_not_the_epoch`) assert a real
timestamp now, and the live record was re-measured after the fix.

**One tension this task's plan asked to be decided, not papered over, stated
here rather than only in code:** `resolution.version` and `.set_id` measure
`None` for every real evaluation against this deployment
(`featureflag.yaml`'s `autonomy-level` flag defines no `flag_metadata`, per
Task 3's own measurement). Both are omitted from the log record when `None`,
per this repository's standing "missing value is absent, never invented"
rule — the same convention `server.py` already applies to the
`policy.decision` span's `feature_flag.version` attribute. Consequence for
Task 5, stated plainly: reading `feature_flag.version` off this log event
will find it absent today, same as the span; ruling M5-R6 already routes the
audit's correctness around this by resolving the current contract version
from flagd directly rather than depending on either event carrying it — this
log event is the human-readable parity target, not a scoring dependency.

---

**2026-09-14 (M5 Task 1 fix round 1) — superseded by the row above, and kept
the way every earlier row is: it was the current figure at the time it was
written.**
(J4, whole-branch review: this heading claimed the gate row was still
PREDICTED after a later row had already measured it. Reworded rather than
silently corrected, per the standing rule two blocks above.)
The Task 1 review (Important finding) named `scripts/start-demo.sh`'s V11 fix
as the one item in that task's whole batch shipped with zero regression
test. Closed with one new hermetic suite,
`tests/unit/test_start_demo_v11.py`, one test covering both changed
behaviours (a healthy foreign port-forward is accepted; a genuine refusal on
one surface does not tear down forwards already established for the
others) against the real script run with faked `kubectl`/`lsof`/`curl`.
Nothing else this fix round touched adds or removes a test.

That takes the collected count from **354 to 355**, re-measured with
`.venv/bin/python -m pytest --collect-only -q tests/`.

Bare-suite row, derived from that same collection: 355 collected − 71 gated −
**0** xfailed = `284 passed, 71 skipped, 0 xfailed`.

The gated figure is unchanged at **71** (the new test lives under
`tests/unit` with no `skipif`, so it is not gated) — and reports 71 of 355,
with the same per-module spread as the row beneath this one. The browser
split is unchanged too: **3 of the 12 launch a browser** and **9 of the 12
start no browser**.

Gate row: **PREDICTED, NOT MEASURED**, unchanged from the row below -- this
branch's gate run was at commit `b215426` (the M4 final-review fix wave
further below), and it is measured against no later tree than that one. This
fix round changes a file under `gate_paths` (`tests/`) without a new gate run
to back it. The prediction from collection (355 − 1 xfailed = 354) is `354
passed, 1 xfailed`; nothing claims that figure was measured against this
tree.

`tests/unit` alone collects **217** (216 before this fix round).

---

**2026-09-14 (M5 Task 2) — superseded by the row above, and kept the way every
earlier row is: it was the current figure at the time it was written.**
(J4, whole-branch review: this heading claimed the gate row was still
PREDICTED after a later row had already measured it. Reworded rather than
silently corrected, per the standing rule two blocks above.)
Task 2 stands up stage 5's machinery (cert-manager, the Open Feature
Operator, flagd, the `northstar-autonomy` contract) and adds one new hermetic
suite, `tests/unit/test_autonomy_contract.py`, two tests: the contract's
`defaultVariant` is fail-closed, and every variant it can hand back has a row
in `pds/rules.py`'s policy table. Nothing else this task touched adds or
removes a test.

That takes the collected count from **352 to 354**, re-measured with
`.venv/bin/python -m pytest --collect-only -q tests/`.

Bare-suite row, derived from that same collection: 354 collected − 71 gated −
**0** xfailed = `283 passed, 71 skipped, 0 xfailed`.

The gated figure is unchanged at **71** (re-measured the same way as every
figure below it, `tests/unit/_count_gated_plugin.py` against a bare
`RUN_BROWSER_TESTS`-unset collection — both new tests live under
`tests/unit` with no `skipif`, so neither is gated) — and reports 71 of 354,
with the same per-module spread as the row beneath this one. The browser
split is unchanged too: **3 of the 12 launch a browser** and **9 of the 12
start no browser**.

Gate row: **PREDICTED, NOT MEASURED**, unchanged from the row above -- this
branch's gate run was at commit `b215426` (the M4 final-review fix wave
below), and it is measured against no later tree than that one. Task 2 did
not run `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` (reserved for one run
at the end of the milestone, per M5's own standing rule), and this task's
commit changes files under `gate_paths` (`scripts/`, `tests/`, `deploy/`)
without a new gate run to back them. The prediction from collection (354 − 1
xfailed = 353) is `353 passed, 1 xfailed`; nothing claims that figure was
measured against this tree.

`tests/unit` alone collects **216** (214 before this task).

---

**2026-09-14 (M5 Task 1) — superseded by the row above, and kept the way every
earlier row is: it was the current figure at the time it was written.**
M4 §9 scheduled six behaviour-neutral fixes and three version bumps as one
carried batch, because every one of them lands inside the staleness guard's
`gate_paths` below and each alone would have re-labelled the gate row
`PREDICTED, NOT MEASURED` and demanded its own fresh gate run. This task
closed that batch in one commit, which is exactly the case the label exists
for.

Of the nine items, two change what `pytest --collect-only -q tests/`
collects, both added while mutating this task's own guard fixes rather than
scoped in advance:

* V9 added a vacuity control,
  `tests/unit/test_kube_helper.py::test_the_walk_detects_a_known_offender`,
  asserting the repo walk that guards ruling M4-R14 still finds a source
  written to offend it — the walk finding nothing forever if its own
  detector silently stopped matching is indistinguishable from a passing
  gate.
* V2's own mutation round found a real gap: removing `"systems", "tools"`
  from the widened path list left every test in
  `tests/unit/test_telemetry_contract_counts.py` GREEN, because this task's
  working tree always has other `gate_paths` files changed too, so
  `test_a_predicted_gate_row_is_labelled_as_one` cannot tell the difference
  -- it only asks "did anything change". `test_gate_paths_widened_list_actually_sees_systems_and_tools`
  closes that gap by checking the list's membership directly and by
  encoding Step 4's manual, real-commit verification (`716d70c`) as a
  standing test rather than a one-off.

Neither of the other seven items (one other guard fix, the three pins, and
the four citation/prose corrections) touches a test body or a fixture. That
takes the collected count from 350 to **352**, re-measured with
`.venv/bin/python -m pytest --collect-only -q tests/`.

Bare-suite row, derived from that same collection: 352 collected − 71 gated −
**0** xfailed = `281 passed, 71 skipped, 0 xfailed`.

The gated figure is unchanged at **71** (both new tests live under
`tests/unit`, not behind `RUN_BROWSER_TESTS=1`) — measured the same way as
every figure below it, and reports 71 of 352, with the same per-module
spread as the row beneath this one. The browser split is unchanged too:
**3 of the 12 launch a browser** and **9 of the 12 start no browser**.

Gate row: **PREDICTED, NOT MEASURED** — this branch's gate run was at commit `b215426`
(the M4 final-review fix wave below). This task's commit changes files
under `gate_paths` (`scripts/`, `tests/`, plus the pinned
`requirements.txt`/`test_pins.py`/chart-version sites `deploy/` and the
services carry, all reached by the gate or its image build) without a new
`RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` run to back them. The prediction
from collection (352 − 1 xfailed = 351) is `351 passed, 1 xfailed`; nothing
claims that figure was measured against this tree. M5's own scheduled gate
run is what turns this back into a measurement.

`tests/unit` alone collects **214** (212 before this task).

**2026-09-14 (M4 final-review fix wave) — superseded by the row above, and kept
the way every earlier row is: it was the current figure at the time it was
written. Ruling M4-R22 bought this milestone a second run, because the
first one no longer measured a build that exists.**
(J4, whole-branch review: this heading is M4's, not M5's, and the file grew
+569 net lines below it while this one sat unreworded. Reworded rather than
silently corrected, per the standing rule two blocks above.)
The fix wave added **7** tests under `tests/`, taking the collected count from
343 to **350**, re-measured with
`.venv/bin/python -m pytest --collect-only -q tests/`. Every one is a unit test
guarding something the final review demonstrated was unguarded:

* `tests/unit/test_telemetry_contract_counts.py` — **+3**.
  `test_the_published_browser_split_is_what_the_source_says` derives the
  browser/no-browser split of the gated modules from `chromium.launch(` in
  `tests/e2e` instead of from a reader (finding M1: that figure had been wrong
  twice in three tasks, in three documents at once).
  `test_the_bare_rows_xfail_term_accounts_for_gating` derives the bare row's
  `xfailed` term from whether any module *containing* an xfail is ungated
  (finding M3). `test_a_predicted_gate_row_is_labelled_as_one` is the third,
  and it exists because of this very block: see the gate row below.
* `tests/unit/test_smoke_test_gate.py` — **+2**, pinning the gate's new
  refusal to pass a run that skipped anything, and pinning the *order* of that
  check after the run it inspects, so it cannot be satisfied by a log that was
  never written.
* `tests/unit/test_kube_helper.py` — **+2**.
  `test_the_shared_pipeline_reader_has_no_local_copies_either` is the
  `pipeline_exporters` half of ruling M4-R14's "one definition", which had no
  repo walk while the weaker half had one (finding L4). The second is a
  parametrised case pinning the ruling on suspected finding S3: a kubectl
  message that quotes a name but is not `NotFound` stays a failure.

Bare-suite row, derived from that same collection: 350 collected − 71 gated −
**0** xfailed = `279 passed, 71 skipped, 0 xfailed`. **The xfailed term is 0,
not 1, and the row below this one had it wrong**: the tree's only
`pytest.xfail` is in `tests/e2e/test_claim_correlation.py`, which M4 Task 5
gated. On a bare run that test *skips*, so it is already inside the 71, and
subtracting it a second time counted one test twice — which is precisely why
the old row still summed to its collected total while being wrong. The term is
now derived by a test rather than carried forward.

The gated figure is unchanged at **71** — no e2e module was added or gated by
this wave — measured the same way every figure below it was, a `--collect-only`
run with a plugin that counts items carrying a `skipif` whose reason names
`RUN_BROWSER_TESTS` and whose condition is true with the variable unset, and
reports 71 of 350, spread `test_settlement_llm_trace.py` 9,
`test_prometheus_metrics_ingest.py` 9, `test_browser_root_span.py` 8,
`test_settlement_agent_trace.py` 8, `test_claims_chain_trace.py` 7,
`test_claim_correlation.py` 6, `test_gateway_trace.py` 6, `test_supervised.py`
6, `test_policy_scores.py` 5, `test_policy_denial.py` 4,
`test_evidence_view.py` 3. Of those gated modules (twelve as of M5 Task 6),
**3 of the 12 launch a browser** (`test_browser_root_span.py`,
`test_policy_denial.py`, `test_supervised.py` — derived from
`chromium.launch(`, not recalled) and **9 of the 12 start no browser** — the
flag gates
*tokens*, not Chromium, and that split is now derived from the sources rather
than typed.

Gate row: **measured, not derived** — this branch's second and final
gate run was at commit `b215426`, and `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh`
on 2026-09-14 ran to completion in **436.03s** and finished
`349 passed, 1 xfailed`, exit 0, final line
`Northstar M4 smoke test passed.`, with **zero skipped** — the first run under
the new zero-skip check, which would have failed the gate on any skip at all.
The prediction from collection (350 − 1 xfailed = 349) and the run agree, and
they are recorded as two separate facts because a milestone where they
*disagreed* would be the interesting one.

**Why there are two gate runs in one milestone, and why the first one was
retired** (ruling M4-R22). The first ran at `43f0c72` over a tree that collected
343, finishing `342 passed, 1 xfailed` in 417.83s with zero skipped. This fix
wave then added seven tests *and* changed live `eval-scorer` scoring behaviour
(the H1 truncation refusal) that the cluster's running image predated — so the
`43f0c72` row measured a build that no longer existed. The milestone's "one gate
run" budget buys one **valid** run, not one attempt, and a milestone that merges
on a predicted row is the unmeasured-claim shape this repo bans. Before the
second run, `./scripts/rebuild-and-deploy.sh eval-scorer` replaced the image
(`sha256:afe5597…` → `sha256:77cd4974…`) and the new build was confirmed to be
*serving*, not merely rolled out: a live `POST /score` over a seven-day window
answered **HTTP 502** with the truncation refusal, which the previous image had
no code to produce.

`test_a_predicted_gate_row_is_labelled_as_one` compares the working tree against
the commit named here — not `HEAD` against it, which the fix wave would itself
have fooled — and requires a `PREDICTED, NOT MEASURED` label whenever any file
the gate runs has changed since it, and forbids the label once none has. It is
what turned this row back into a measurement rather than letting it be edited
into one.

`tests/unit` alone collects **212** (205 before this wave).
`platform/eval-scorer`'s own suite moves **128 → 135**; none of those is
collected by `pytest tests/`, so they do not appear in the figures here.

**2026-09-13 (M4 Task 5) — superseded by the row above, and kept the way every
earlier row is: it was the current figure, with a MEASURED gate row, at the
time it was written.**
Task 5 added **34** tests under `tests/`, taking the collected count from 309 to
**343**, re-measured with `.venv/bin/python -m pytest --collect-only -q tests/`.
The gated figure moved much further than the collected one — **41 → 71** — and
that is the ruling below, not an accident of the new tests. (This block first
published that move as 41 → 64; the last seven were found by the bare run
recorded further down, and the correction is kept visible rather than
overwritten.)

Twenty-nine of the thirty-four are there because the task went looking for
*published facts and reconciled rulings that nothing re-checks*, and found
three:

* `tests/unit/test_mutation_protocol.py` — **+3** (7 → 10), and the shape of
  the file changed more than the count did. One substring assertion became four
  tests that *run* `scripts/mutate-guard.sh`: that it refuses a mutation whose
  text is absent, that it refuses one identical to the original, that it refuses
  a pattern matching more than one line, and — the positive control, without
  which a script that refused everything would pass the other three — that a
  unique pattern really is on disk while the test command runs and really is
  restored afterwards. The non-unique refusal is the fix for the defect M4 Task
  4 hit: the script replaced the **first** match silently, a mutation landed in
  a line the test command never ran, and the round recorded GREEN — the tool
  built to stop false GREENs produced one. The behavioural form is deliberate:
  the old test matched the literal `if old not in text`, which is exactly the
  condition the fix had to rewrite, so it would have reddened for a correct
  change while a rewrite that deleted the check and kept the words stayed green.

* `tests/unit/test_stage4_config.py` — **+2** (23 → 25), both guards on
  operator-facing prose that had already gone stale.
  `test_the_gates_banner_names_the_milestone_it_actually_proves` **derives** the
  current milestone from the newest design spec in `docs/superpowers/specs/` and
  checks `scripts/smoke-test.sh`'s banner and sign-off against it. It went red
  on the commit that added it: the gate still announced `=== Northstar M3 smoke
  test ===` while running four new files of M4 assertions — the **third**
  recurrence of a rename whose own in-file comment ended "No test pins either
  string". One does now, and nothing has to be retyped next milestone.
  `test_the_bare_run_failure_names_the_command_that_fixes_it` pins the remedy the
  first-run ruling above rests on: leaving a bare `pytest tests/` failing is
  defensible only while `./scripts/start-demo.sh` is named where the reader is.

* `tests/unit/test_telemetry_contract_counts.py` — **+1**, and it is the figure
  in the paragraph you are reading. This document and `README.md` both publish
  how many tests sit behind `RUN_BROWSER_TESTS=1`, and until this task **nothing
  re-measured it**: the two rows the existing guard checks each sum to the
  *collected* total, which deleting a `pytestmark` does not move. So the gate
  that this repo's entire cost story rests on could be removed from a
  settlement-driving file and every test would stay green. It is measured now
  the same way the prose says it is — the counting plugin lives at
  `tests/unit/_count_gated_plugin.py` and is run by the test — with
  `RUN_BROWSER_TESTS` *removed* from the child environment rather than set
  falsy, because this test also runs inside the gate, where the variable is `1`
  and every condition would evaluate false and report zero gated tests.

* `tests/unit/test_kube_helper.py` — **+15**, new. Ruling M4-R14 reconciled two
  skip shapes into one definition (`tests/harness/kube.py::absent_or_fail`) on
  the argument that the strict one is worth keeping, and then asserted that
  argument nowhere: every caller is itself skipped below stage 4, so a
  regression widening it back to "any non-zero exit means absent" would show up
  as a green run. The rows are real `kubectl` failure texts — connection
  refused, unknown context, Forbidden, dial timeout, a non-zero exit with empty
  stderr — each required to *raise* rather than return absent. **One of them
  found a live defect rather than confirming a belief**: the helper matched a
  bare, case-insensitive `not found` anywhere in stderr, so `kubectl: command
  not found` — a machine with no kubectl at all — read as "the object does not
  exist" and skipped the milestone's assertions. It now requires kubectl's own
  shape, a quoted object name followed by `not found`, or the `NotFound` API
  reason. A last test walks `tests/e2e` and fails if any module defines its own
  copy, because "one definition" is a property of the repo and was previously
  maintained by attention.

* `tests/unit/test_collector_pipeline_helper.py` — **+13**, new, and behind it a
  third caller that had been left holding a shape this repo had already measured
  inert. `test_prometheus_metrics_ingest.py` records in its own docstring that
  `EXPORTER_ID in <the config text>` stays **true** after the pipeline entry
  that routes the signal is deleted, because the id also occurs under
  `exporters:` and in the comment above it. That finding was fixed in two of the
  three stage gates that ask this question and not in the third —
  `test_opensearch_trace_ingest.py`, the oldest, still read the substring until
  this task. Re-measured here on the live ConfigMap: `forward/opensearch` occurs
  **4** times in the deployed text, and deleting the one occurrence that routes
  anything leaves 3. All three now call one function,
  `tests/harness/collector.py::pipeline_exporters`, which parses
  `service.pipelines` and matches the signal on the pipeline name's leading
  segment so that `traces` covers the `traces/opensearch` branch (and does not
  cover `metrics/traces`). The mutation that defeated the substring form is a
  unit test now, so it stays defeated with no cluster.

**The skip-path mutation pass, run against the live cluster on 2026-09-14.**
Every cluster-dependent test this milestone added is guarded by a module-scoped
`stage_4_or_a_reason` fixture, and in all four the discriminator is a *different
object* than the thing under test: the OpenSearch or Prometheus Service plus the
deployed collector's pipelines, never the index, the documents, the series or
`eval-scorer` — so a stage-4 cluster whose data never arrives reddens rather
than skips, and a missing `eval-scorer` on a stage-4 cluster is a failure with
no skip path at all. To show that is true rather than intended, the deployed
`otel-collector-config` ConfigMap had the two lines that route the signals —
`- forward/opensearch` from the `traces` pipeline and `- otlp_http/prometheus`
from `metrics` — deleted and re-applied, one signal at a time and then both.
Result, in each case a **failure naming the missing route**, never a skip:

| File | Mutation | Outcome |
|---|---|---|
| `test_opensearch_trace_ingest.py` | tee removed from `traces` | `ERROR … assert exports` — "traces pipelines do not export to both `forward/opensearch` … and `opensearch/trace`" |
| `test_prometheus_metrics_ingest.py` | exporter removed from `metrics` | `ERROR … tests/e2e/test_prometheus_metrics_ingest.py:226` |
| `test_evidence_view.py` | tee removed from `traces` | `ERROR … "does not carry BOTH halves of the tee"` |
| `test_policy_scores.py` | tee removed from `traces` | `ERROR … tests/e2e/test_policy_scores.py:200` |

The same mutated text, read both ways, is why this pass was worth running:
`'opensearch/trace' in <text>` → **True**, `pipeline_exporters(text, 'traces')
⊇ {tee, exporter}` → **False**. The old gate would have passed. The collector
itself was never restarted (`restarts=0` throughout), so only the ConfigMap the
gates read was affected, and the original was re-applied and diffed
byte-identical after each mutation.

**One real failure was found and fixed, and it would have failed this gate.**
`test_policy_denial.py::test_criterion_4_no_policy_logic_lives_in_settlement_agent_or_payments_mcp`
compares the production change surface in `agents/settlement-agent` and
`tools/payments-mcp` since `m2.1-verified` against an allow-list, and M4 Task 2
added a MeterProvider to `tools/payments-mcp/bootstrap.py` without extending it.
Nothing caught it for three tasks because that assertion lives in `tests/e2e`
and no task in this milestone was permitted a gate run — four green `pytest
tests/unit` reports could not see it. The file is now on the allow-list with its
reason (an OTel SDK MeterProvider and an OTLP exporter, the same shape
`tests/unit/test_metrics_wiring.py` asserts is identical across five services,
and nothing else); parts 1–3 of that test still read the same file's AST for
policy vocabulary, allowance literals and money comparisons on every run, so
only the change-surface clause was widened. Its failure message now derives the
file count from the allow-list as well — it read "the six files … each of the
five" and was therefore wrong in one half whichever number a later edit
corrected.

**Ruling M4-R15 (amended), discharged: the three remaining model-spending e2e
files are now gated, and the money-moving ones deliberately are not.** Measured
in this task rather than reasoned about:

* Three ungated files drove a real settlement on every bare `pytest tests/` —
  `test_claim_correlation.py`, `test_claims_chain_trace.py` and
  `test_settlement_agent_trace.py`, one module-scoped driving fixture each, all
  at €6,200. That is three settlements' worth of Anthropic tokens, on someone's
  account, from the command this repo presents as the ordinary way to run the
  suite. All three now carry the same `pytestmark` the other cost-gated modules
  carry. `RUN_BROWSER_TESTS=1` is this repo's **cost** gate, not a browser gate:
  **8 of the 11** gated modules started no browser at the time; **3 of the 11
  launch a browser** — `test_browser_root_span.py`, `test_policy_denial.py`
  (in `_drive_browser`) and `test_supervised.py` (in its approval fixture).
  (M5 Task 6 added a twelfth gated module, `test_progressive_autonomy.py`,
  itself no-browser; see the current figures at the top of this document for
  today's 3-of-12/9-of-12 split.) This
  number has now been wrong twice in three tasks. It read "six of the ten"
  (M4 Task 4's figure) until M4 Task 5's stale-prose pass, which moved the
  denominator to eleven, moved the numerator to ten, and **re-derived
  neither** — the pre-M4 README text it replaced had the browser count right.
  The final review caught the second error and the fix is not a third typing:
  `tests/unit/test_telemetry_contract_counts.py::test_the_published_browser_split_is_what_the_source_says`
  greps `tests/e2e` for `chromium.launch(`, intersects that with the gated
  module list pytest reports, and fails this sentence and `README.md`'s against
  the answer.
* **Three ungated tests still write an `executed` row to the ledger, and that is
  the intended state.** Measured by ledger delta over live runs, not inferred:
  `pytest tests/e2e/test_payments_api_trace.py tests/e2e/test_payments_mcp_trace.py`
  moved the ledger 138 → 140 with two `executed` rows of €6,200 — neither file
  goes through agentgateway, so no policy decision is made on them at all — and
  `test_policy_denial.py::test_criterion_5_the_decision_changes_when_the_callers_spiffe_id_changes`
  moved it 140 → 142, executing €100, which is that test's whole assertion.
  Those cost nothing: payments-api's ledger is an in-memory, per-process store
  on a kind cluster (ruling 32), so "money moved" is a demo fiction. Gating them
  would buy a developer nothing and would remove the only ungated coverage of
  the money path from a bare run. **The gate's second-order effect had to be
  measured too, and the first reading of it was wrong.** Gating those three
  files removed the settlement traffic that
  `test_prometheus_metrics_ingest.py`'s positive controls had been living on by
  accident. Two items were gated in response, from the failure list available at
  the time; a full bare run of that file on 2026-09-14 (`7 failed, 10 passed, 2
  skipped in 3178.17s`, alongside `test_opensearch_trace_ingest.py`) then showed
  **seven more**, every one of them `_assert_the_genai_path_is_live` unable to
  find `gen_ai_client_token_usage_*` in its 15-minute window — the three tests
  fix round 1 attached that control to. Nine of that file's fourteen items now
  carry the flag, and the gated figure is **71**, not the 64 this document
  published an hour earlier. An absence whose positive control cannot run is
  unproven by that file's own design, so this is the gate declaring a real
  precondition rather than a test being hidden: the five items needing no model
  call still run bare, and did pass. **Tokens are the externality; tokens are what
  this flag gates.**

Bare-suite row, derived from that same collection: 343 collected − 71 gated −
1 xfailed = `271 passed, 71 skipped, 1 xfailed`. **That derivation is wrong and
is left standing as written, because it is what this row published; the
correct figure is `272 passed, 71 skipped, 0 xfailed`** — see finding M3 in the
current block above, and the guard that now derives the term. The gated figure is measured
the same way every figure below it was — a `--collect-only` run with a plugin
that counts items carrying a `skipif` whose reason names `RUN_BROWSER_TESTS`
and whose condition is true with the variable unset — and reports 71 of 343,
spread `test_settlement_llm_trace.py` 9,
`test_prometheus_metrics_ingest.py` 9, `test_browser_root_span.py` 8,
`test_settlement_agent_trace.py` 8, `test_claims_chain_trace.py` 7,
`test_claim_correlation.py` 6, `test_gateway_trace.py` 6, `test_supervised.py`
6, `test_policy_scores.py` 5, `test_policy_denial.py` 4,
`test_evidence_view.py` 3. **That measurement is no longer a one-off
performed by hand**: `tests/unit/test_telemetry_contract_counts.py::test_the_contracts_gated_figure_is_what_pytest_would_actually_skip`
re-derives it from pytest on every unit run and fails against this paragraph
and against `README.md`. It is what caught 64 → 71 above. Gate row: **measured,
not derived** — `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` on 2026-09-14 ran
to completion in **417.83s** and finished `342 passed, 1 xfailed`, exit 0, final
line `Northstar M4 smoke test passed.`, with **zero skipped**. The prediction
from collection (343 − 1 xfailed = 342) and the run agree, and they are recorded
as two separate facts because a milestone where they *disagreed* would be the
interesting one: a gate that silently skips lands short of its own collection
and still prints green.

`tests/unit` alone collects **205** (171 before this task): every one of the 34
new tests is a unit test.

**2026-09-13 (M4 Task 4, including its review fix round 1) — superseded by the
row above, and kept the way every earlier row is: it was the current figure,
with a DERIVED gate row, at the time it was written.**
Task 4 added **6** tests under `tests/` and its fix round added **7** more,
taking the collected count from 296 to **309**, re-measured with
`.venv/bin/python -m pytest --collect-only -q tests/`:

* `tests/e2e/test_policy_scores.py` — **5**. The policy-adherence scores of
  M4 spec §5, asserted against the live cluster: that a compliant supervised
  settlement scores 1.0 on all five; that a deliberately non-compliant
  trajectory scores **differently** (spec §7.4's anti-vacuity criterion, which
  is the task's exit criterion rather than a nicety); that the violating run
  moved no money, read from the payments ledger; that the score spans land in
  OpenSearch inside the trace they judge, parented to that trajectory's own
  root; and that they are findable by `northstar.claim.id` while the
  `policy.decision` spans they were computed from are not — which is Gap 4
  stated as an assertion instead of a caveat. The whole file is behind
  `RUN_BROWSER_TESTS=1`, for cost rather than for a browser, the same way
  `test_evidence_view.py` is: it drives two settlements' worth of model calls,
  one of them a real €6,200 execution under the senior identity. It follows
  `test_evidence_view.py`'s stage gate by importing it rather than copying it,
  and eval-scorer's *absence* on a stage-4 cluster is a failure, not a skip.

* `tests/unit/test_smoke_test_gate.py` — **+1** (3 → 4).
  `test_the_gate_runs_every_per_service_suite_the_contract_publishes`: every
  service in `test_telemetry_contract_counts.py`'s `_PER_SERVICE_SUITES` must
  actually be *run* by `scripts/smoke-test.sh`, not merely counted by it. It
  exists because this task mutation-tested its own work and found nothing:
  deleting the eval-scorer suite's invocation from the gate left `tests/unit`
  entirely green. That is this repo's most recurrent defect — a suite nothing
  runs — for the third time (policy-decision-service at M2.2, reviewer-ui at
  M3), and the first time it was caught by mutating rather than by a human
  re-reading the script. The new guard reddens on either historical case when
  replayed: removing reviewer-ui's invocation fails it by name.

* `tests/unit/test_mutation_protocol.py` — **+7** (the fix round's whole
  addition under `tests/`). This repo's convention is that a guard is not
  trusted until it has been mutated and watched go red for the reason it
  names, and M4 Task 4 found that the naive edit/run/revert loop is unsound:
  CPython validates a cached `.pyc` on `(int(source_mtime), source_size)`
  alone, so a length-preserving mutation reverted inside the same integer
  second leaves the *mutant* bytecode executing against reverted source. Both
  error directions — a false green and a false red — were reproduced from
  scratch. `scripts/mutate-guard.sh` is the protocol as a tool rather than a
  paragraph, and this file pins the two lines that make it sound
  (`PYTHONDONTWRITEBYTECODE=1` on the run itself, and a `__pycache__` purge on
  both sides of it), the restore-on-every-exit-path trap, the refusal of a
  no-op mutation, and the README section that tells anyone it exists. They
  look like boilerplate, which is how they would be lost.

The per-service row moves: `platform/eval-scorer` is the **eighth** suite, **128**
tests, added to that row and to `scripts/smoke-test.sh` in this same task. None
of those 128 is collected by `pytest tests/`, so they do not appear in the
figures here. (It was 106 before the fix round, which added 21 tests for the
HTTP surface — `Handler` had no test of any kind, including the `emit`-must-be-
a-boolean guard and the 502-vs-500 split that the source's own comments call
load-bearing — and one for `anchor()`'s fallback branch, which a review showed
the test named for it never reached.)

Bare-suite row, derived from that same collection: 309 collected − 41 gated −
1 xfailed = `267 passed, 41 skipped, 1 xfailed`. The gated figure is measured
the same way the 36 below was — a `--collect-only` run with a plugin that
counts items carrying a `skipif` whose reason names `RUN_BROWSER_TESTS` and
whose condition is true with the variable unset — and reports 41 of 309, spread
`test_settlement_llm_trace.py` 9, `test_browser_root_span.py` 8,
`test_gateway_trace.py` 6, `test_supervised.py` 6, `test_policy_scores.py` 5,
`test_policy_denial.py` 4, `test_evidence_view.py` 3. Five of this task's
thirteen new tests are gated and the other eight are unit tests, so the
skipped figure moved by five and the rest went to the bare-run *passed*
figure. Gate row: 309 collected − 1 xfailed = `308 passed, 1 xfailed`.
This is arithmetic, not a live gate run, with the same standing as every row
below it: **Task 5 ran the milestone's one gate; the MEASURED figure is the current
block at the top of this section and the gate row in the table below.** What it rests on: Task 4 added a new deployment that reads
OpenSearch and writes spans of its own, and changed no span, no attribute and
no behaviour of any service the gate already asserts on — the one shared file
it touches is `scripts/setup-kind.sh`, inside the existing `STAGE -ge 4` block.

`tests/unit` alone collects **171** (163 before this task): five of the thirteen
new tests are e2e; the other eight are the gate guard above and
`test_mutation_protocol.py`'s seven. The SPIFFE registration
ruling R22 requires went into *existing*
`tests/unit/test_stage2_spire_config.py` tests rather than a new one — its
`REGISTERED_CLUSTER_SPIFFEIDS` map and its published-ID allow-list both grew by
one entry.

**The residual named below is unchanged and this task did not widen it.** A
bare `pytest tests/` still drove live model-driven settlements from three
ungated files; `test_policy_scores.py` was not a fourth. (M4 Task 5 gated all
three and closed that residual — ruling M4-R15 amended, discharged; see the
current block at the top of this section.)

**2026-09-13 (M4 Task 3, including its review fix round 1) — superseded by the
row above, and kept the way every earlier row is: it was the current figure,
with the same DERIVED gate row, at the time it was written.**
Task 3 added **6** tests and its fix round added **3** more, taking the
collected count from 287 to **296**, re-measured with
`.venv/bin/python -m pytest --collect-only -q tests/`:

* `tests/e2e/test_evidence_view.py` — **3**. The evidence view's *queries*,
  asserted rather than its pixels (M4 spec §4 puts screenshots explicitly out
  of scope): that one claim-id lookup returns both stories with the fields the
  saved dashboard renders, that both claims' `execute_payment` decisions name
  the **same** rule at the limits each identity holds, and that the trace-id
  half of the join is what reaches the `payments-api` spans — asserted from
  both sides, so collapsing the two-step join into a single claim-id filter
  reddens a test instead of silently emptying two panels. The file drives both
  settlements itself (two model calls, no browser: a direct A2A POST to
  claims-agent and a plain form POST to reviewer-ui) and bounds every query to
  the window it opened, because `CLM-1842` is a fixed id and the index holds
  one set of traces per run of it. It follows the repo's one stage-gate shape —
  `tests/harness/kube.py`'s `absent_or_fail`, imported rather than copied — and
  skips only on a cluster coherently below stage 4; a missing index on a
  stage-4 cluster is a **failure**, verified by mutation. (That helper lived in
  `test_prometheus_metrics_ingest.py` until M4 Task 5 discharged ruling M4-R14:
  it was the stricter of two shapes the milestone carried, and Task 5 moved it
  to the harness and retired the looser one in
  `test_opensearch_trace_ingest.py` against it, so there is one definition and
  not a copy per caller.) The fix round put the whole file behind
  `RUN_BROWSER_TESTS=1` (no browser is started; it rides the flag for the same
  reason `test_settlement_llm_trace.py` does — it drives two settlements'
  worth of model calls and executes a real €6,200 payment under the senior
  identity), which is why the gated count below moves 33 → 36.
* `tests/unit/test_stage4_config.py` — **+6** (17 → 23; 20, then +3 in the fix
  round). That `deploy/observability/dashboards/evidence.ndjson` is a complete,
  importable export (no dangling reference, every dashboard panel resolvable, the
  index-pattern naming the ss4o trace index with its cached field list intact);
  that its header panel still records the choice M4 had to make about the
  permitted claim's id, since NDJSON cannot hold a comment and that panel is
  the only place the record lives; and that `scripts/setup-kind.sh` imports the
  committed file, inside the `STAGE -ge 4` block, checking the HTTP response
  rather than curl's exit code. The dashboard's id is cross-checked against
  `scripts/setup-kind.sh` and `docs/architecture.md` so a renamed dashboard
  reddens a test instead of leaving two `#/view/<id>` links that 404. The fix
  round closed the gap that undercut the task's own premise (review finding
  F4): the e2e file asserts the queries it *re-implements in Python*, and
  nothing read the queries out of the shipped export at all, so the dashboard
  could ship querying a field no test names. The three new guards read the
  NDJSON itself — every panel's aggregated/controlled fields and its exact KQL
  query, cross-checked both against the attribute-name constants the e2e files
  declare (parsed out of their ASTs, so a renamed constant reddens here) and
  against the index-pattern's own cached field list; the exact ordered list of
  the six panels the dashboard ships; and that every `http://localhost:NNNN`
  surface in `docs/architecture.md`'s table is actually printed by
  `scripts/start-demo.sh`, which is what lets that document derive the list
  rather than restate it.

The per-service row is unchanged at **114**: Task 3 added no test outside
`tests/`.

Bare-suite row, derived from that same collection: 296 collected − 36 gated −
1 xfailed = `259 passed, 36 skipped, 1 xfailed`. The 36 gated were 33 before
this task's fix round; `tests/e2e/test_evidence_view.py`'s three tests are the
other three, gated for cost rather than for a browser (see its bullet above).
The gated figure is measured, not assumed: a `--collect-only` run with a plugin
that counts items carrying a `skipif` whose reason names `RUN_BROWSER_TESTS`
and whose condition is true with the variable unset reports 36 of 296, spread
`test_browser_root_span.py` 8, `test_settlement_llm_trace.py` 9,
`test_gateway_trace.py` 6, `test_supervised.py` 6, `test_policy_denial.py` 4,
`test_evidence_view.py` 3. Gate row: 296 collected − 1 xfailed =
`295 passed, 1 xfailed`. (This figure first went in wrapped *inside* its
backticks, and `tests/unit/test_telemetry_contract_counts.py` — which read a
space, not any whitespace — skipped past it and checked M4 Task 2's historical
figure in its place, reporting drift against a number this edit never touched.
The guard now tolerates the wrap; the hazard is recorded because it is the
same one the guard's own docstring describes for a historical figure moved
above a current one.)
This is arithmetic, not a live gate run, with the same standing as
the two rows below it: **Task 5 ran the milestone's one gate and converted this
row to MEASURED.**

`tests/unit` alone collects **163** (157 before this task).

**A residual this task did not fix, left for Task 5 to rule on.** Gating
`test_evidence_view.py` narrows but does not close the repo-level gap the fix
round's review named: a bare `pytest tests/` still drives live model-driven
settlements, from the ungated chain files described at the top of this
section. That gap predates this task and is unchanged by it — beyond the one
file this task itself added, the fix round was told explicitly not to touch
the others — but it means the sentence at the top of this section, that a bare
run gets "the weaker, default result", should not be read as "the free one".
Measuring it while gating this task's file turned up one correction to that
paragraph, recorded there: the ungated settling files are **three**, not two.

**2026-09-13 (M4 Task 2, including its review fix round 1) — superseded by the
row above, and kept the way every earlier row is: it was the current figure,
with the same DERIVED gate row, at the time it was written.** Task 2 added
**34** tests and its fix round added **6** more, taking the collected count
from 247 to **287**, re-measured with
`.venv/bin/python -m pytest --collect-only -q tests/`:

* `tests/unit/test_metrics_wiring.py` — **22** (20, then +2 in the fix
  round). That every one of the five
  Python services *calls* the metrics wiring in `configure()` (not merely
  defines it — the first draft bound to the definition and stayed green when
  the call was deleted); that the five copies of that wiring have not
  drifted; that nothing anywhere configures a `View`, a `DropAggregation`, a
  `preferred_temporality`, a `preferred_aggregation` or an `attribute_keys`
  that would discard a series (walked over the AST, not grepped, because the
  substring version reddened on a docstring that *names* those knobs while
  explaining why none is set); that the collector's `metrics` pipeline
  filters nothing and exports to Prometheus; that the exporter's endpoint
  matches the Service the chart actually publishes; that the chart version
  agrees between `scripts/setup-kind.sh` and
  `deploy/observability/prometheus/values.yaml`; that the values file enables
  the OTLP receiver and disables all ten scrape jobs; and that the collector's
  deprecated `otlphttp` alias is not used anywhere. The fix round added the
  two halves of "never sample" that an AST walk over five Python files
  structurally cannot see (review findings F5 and F6): that no `deploy/k8s/`
  manifest sets one of the five environment variables that change what the SDK
  records from outside the source — `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE`
  and `OTEL_METRIC_EXPORT_INTERVAL` among them, both of which would falsify a
  number this document publishes as measured — and that no service under
  `agents/`, `apps/`, `platform/`, `tools/` or `systems/` *authors* an
  instrument under a namespace an instrumentation library owns, which is the
  only layer that can see §6's hand-rolling prohibition at all.
* `tests/unit/test_stage4_config.py` — **17** (13, then +4 in the fix round),
  closing review findings F1 and F2. The stage-<4 strip guard had a traces half
  and no metrics half, so ruling M4-R8 was unenforced for
  `otlp_http/prometheus`: the strip was correct and nothing said so. There is
  now a per-config assertion that the stripped `metrics` pipeline names no
  Prometheus exporter, still exports to `debug`, keeps its `prometheus/spire`
  receiver (which is *not* stage-4-only, so the broad word "prometheus" would
  false-positive), and declares everything it names. The third is structural:
  the operator-facing message the script prints when it strips must name every
  backend the strip actually removes, **derived by diffing the two configs
  rather than restated** — which is what turns the next bracketed region into a
  red test instead of another stale sentence. Sweeping for more of that class
  found one the review had not: the `Stage 4 (Observable) is up: ...`
  completion message still named two of the three Helm releases stage 4
  installs. The fourth guard derives that list too, from the
  `helm upgrade --install` lines inside the script's own `STAGE -ge 4` block.
* `tests/e2e/test_prometheus_metrics_ingest.py` — **14**. The §6 inventory,
  kept honest: an OTLP-only ingestion control (SPIRE series in a Prometheus
  with zero scrape targets), a per-service positive control for each of the
  four services that record instruments, the one GenAI metric that does
  arrive, the five that do not (one test each), and two tests that would
  catch a GenAI metric arriving under a name this file did not predict or a
  hand-rolled metric invented to fill a framework gap. Every *positive*
  assertion is bounded to a 900-second window via `/api/v1/series`'s
  `start`/`end`, so a stale TSDB cannot keep them green; the *absence*
  assertions are deliberately left unbounded, because a longer window only
  strengthens a negative. Like every other `tests/e2e` file these need the
  live cluster, and they skip on a cluster below stage 4. The fix round added
  no test here but changed three of the fourteen (finding F3): the three that
  assert an absence and asserted no presence — and so passed against a
  completely empty Prometheus — now each begin by asserting the control the
  module docstring always claimed they had. It also narrowed the skip (finding
  F7): "below stage 4" now means the API server answered `NotFound`, and every
  other non-zero `kubectl` exit is a hard failure, so an unreachable cluster
  can no longer remove this file from the gate run silently.

The per-service row below moves with them: `agents/settlement-agent` gained
`tests/test_telemetry.py` (**4**), 110 → **114**. Those four are the only
new tests outside `tests/`.

Bare-suite row, derived from that same collection: 287 collected − 33 gated −
1 xfailed = `253 passed, 33 skipped, 1 xfailed`. The 33 gated are the
`RUN_BROWSER_TESTS=1` browser tests, unchanged; the fourteen new e2e tests are
not among them. Gate row: 287 collected − 1 xfailed = `286 passed, 1 xfailed`.
This is arithmetic, not a live gate run, for the same reason and with the same
standing as the M4 Task 1 row below: **Task 5 ran the milestone's one gate
and converts this row to MEASURED.** What it rests on: Task 2 added a
`MeterProvider` to five services and changed no span, no attribute and no
application behaviour the gate already asserts on.

`tests/unit` alone collects **157** (151 before the fix round; 131 before this
task).

**2026-09-13 (M4 Task 1, after its two review fix rounds) — superseded by the
row above, and kept the way every earlier row is: it was the current figure,
with the same DERIVED gate row, at the time it was written.** Task 1 and its
two fix rounds added **19** tests in total, taking the collected count from
228 to **247**, re-measured with
`.venv/bin/python -m pytest --collect-only -q tests/`:

* `tests/unit/test_stage4_config.py` — **13**. Two shipped with Task 1; fix
  round 1 rewrote both (the review found each one inert against the edit it
  claimed to catch) and added nine more, covering the stage-conditional
  collector-config strip, the marker convention it depends on, the attribute
  collision set, the OpenSearch-branch confinement of the rename, and
  `start-demo.sh`'s behaviour on a cluster without the stage-gated Services.
  Fix round 2 added two: that the config **as shipped** declares every
  component its pipelines name (including a connector used on both sides),
  and — folded into the existing marker test — that markers never nest.
  Two of the thirteen run the real `start-demo.sh` against fake binaries, so
  they are slower than a text assertion but not cluster-dependent.
* `tests/e2e/test_opensearch_trace_ingest.py` — **5**. The fix round's
  headline deliverable (ruling M4-R6): span-set parity between Jaeger and
  OpenSearch for one driven trace, the `policy.decision` span being queryable
  out of OpenSearch, Jaeger keeping the canonical attribute names, no
  uncovered attribute-key collision among the keys the driven trace actually
  carries (fix round 2, which is the half that sees third-party library
  keys), and zero `Exporting failed` events read over the whole collector-log
  window. These need the live cluster like every other `tests/e2e` file, and
  they drive one denial through the gateway with no model call and no
  browser. They **skip** on a cluster coherently below stage 4 (no OpenSearch
  Service *and* no `opensearch/trace` in the deployed collector config) and
  **fail** on the incoherent middle — a collector configured to export to an
  OpenSearch that is not installed.
* `tests/unit/test_smoke_test_gate.py` — **+1** (2 → 3). Ruling M4-R9: the
  gate itself must refuse to run below stage 4, so those five e2e assertions
  cannot be skipped into a green run.

Bare-suite row, derived from that same collection: 247 collected − 33 gated −
1 xfailed = `213 passed, 33 skipped, 1 xfailed`. The 33 gated are the
`RUN_BROWSER_TESTS=1` browser tests, unchanged; the five new e2e tests are not
among them. Gate row: 247 collected − 1 xfailed = `246 passed, 1 xfailed`.

This is arithmetic, not a live `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh`
run. **Task 5 ran the milestone's one gate; the MEASURED figure is the current
block at the top of this section and the gate row in the table below.** The expectation the gate row rests on, stated as exactly that:
Task 1's changes touched no application code and no span the gate already
asserts on. The stage-4 requirement is no longer an expectation — as of ruling
M4-R9 the gate checks for `svc/opensearch-cluster-master` in its prerequisite
block and exits non-zero below stage 4, so a gate run that reports these
numbers has necessarily run the five e2e assertions rather than skipped them.
That is the same "derived" / "measured" distinction the M3 rows below draw on
their own.

`tests/unit` alone collects **131** (119 before the fix rounds).

**2026-09-11 (M3, remaining dependency bumps) — superseded by the row above,
kept the same way every earlier row is: it was the current figures, and the
gate row was MEASURED, at the time it was written.** The three bumps of this
round (`mcp` → 2.2.0,
`pydantic-ai-slim`/`pydantic-graph` → 2.42.0, `a2a-sdk` held at 0.3.7) added
no tests and removed none, so the collected total is unchanged at **228**,
re-measured with `.venv/bin/python -m pytest --collect-only -q tests/`.
`RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` was run to completion on the
long-running `kind-northstar` **stage 3** cluster (no `teardown.sh`, no
cluster recreation), with settlement-agent and payments-mcp rebuilt and
redeployed from this round's own tree first. Result: **`227 passed, 1
xfailed in 346.46s (0:05:46)`**, exit 0, followed by "M3 smoke test
passed." — **zero skips anywhere in the run**, checked by grepping the whole
log rather than by trusting the summary line. Bare-suite row, still derived
from the same collection: 228 collected − 33 gated − 1 xfailed = `194
passed, 33 skipped, 1 xfailed`.

**It took two runs, and saying so is the point (again).** The first attempt
came back `1 failed, 226 passed, 1 xfailed in 347.42s`, exit 1, on
`tests/e2e/test_policy_denial.py::test_criterion_4_no_policy_logic_lives_in_settlement_agent_or_payments_mcp`
— its change-surface assertion, which named five production files in
settlement-agent and payments-mcp as accounted for since `m2.1-verified` and
found a sixth, `agents/settlement-agent/bootstrap.py`. The guard was right
to fire and the criterion's substance was never in question: the diff is
comment-only (the module docstring corrected for `mcp` 2.2.0 moving the MCP
client onto `httpx2`, a note beside `HTTPX2ClientInstrumentor().instrument()`,
and a re-dated `_instrument_pydantic_ai` docstring at 2.42.0 — no executable
line moved), and that file is the OTel bootstrap, which holds no threshold
and no branch a payment passes through. The allowlist gained the file and
its written reason, and the gate was re-run once, which is the measured run
above. **The superseded claim, recorded rather than deleted:** this block
used to say the gate row was "derived, not measured against a live run" at
`227 passed, 1 xfailed`. The derivation turned out to be arithmetically
right; it is now measured, which is a different thing and the reason the
distinction is drawn at all.

**M3 (Supervised), final-review fix round — superseded by the row above.**
(It was also, until 2026-09-11, the last run that actually exercised the
gate; the row above has now taken that over as well.) A second
gate run was authorised (controller ruling R37) because that round changed
span parentage in a live service, and certifying that on the strength of the
pre-change run would have made the certification false. Run the same way as
the one below it: `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` to
completion, on the long-running `kind-northstar` **stage 3** cluster, no
`teardown.sh`, with `reviewer-ui` redeployed from the fix round's own tree
first. Result: `224 passed, 1 xfailed` in **353.77s**, final line
"`224 passed, 1 xfailed, 2 warnings in 353.77s (0:05:53)`". The whole `tests/` tree collects
**225** at this tree, measured with `.venv/bin/python -m pytest
--collect-only -q tests/`; the four net-new cases are itemised in the M3
table further down. The gated set is unchanged at **33** — re-measured by
walking pytest's own collected items and reading each item's `skipif`
marker, not by counting decorators: `test_browser_root_span.py` 8,
`test_settlement_llm_trace.py` 9, `test_gateway_trace.py` 6,
`test_policy_denial.py` 4, `test_supervised.py` 6. Both of the round's new
e2e cases are deliberately ungated, for the reason
`tests/e2e/test_caller_identity.py` has never been gated: neither spends a
model token.

**M3 (Supervised), Task 5B — superseded by the row above, kept because it is
the run that certified the pre-fix tree.** The gate row then was anchored on
a run of `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` to completion, on the
long-running `kind-northstar` **stage 3** cluster — not on a freshly created
one, and `teardown.sh` was not run — by M3 Task 5B, against that task's own
tree: `220 passed, 1 xfailed` in **343.14s**, final line "M3 smoke test
passed." The whole `tests/` tree collected **221** at that tree, re-measured
with `.venv/bin/python -m pytest --collect-only -q tests/` and itemised per
file in the M3 table further down. The arithmetic behind each row, and which
of the two is measured and which is derived, is in the bullet list further
down this section — the same place it has always been stated.

**It took two runs, and saying so is the point.** The first ran the gate end
to end and came back `1 failed, 219 passed, 1 xfailed` in 330.85s, exit 1, on
`tests/e2e/test_payments_mcp_trace.py::test_tool_schema_matches_the_committed_contract`
— a test that had been failing since M3 Task 3, because that task added
`refuse_payment` and `get_payment` to `payments-mcp` without updating
`contracts/mcp/payments-mcp.json`. Nothing to do with the documentation pass
that run was meant to certify; the milestone had simply never run its own gate
since Task 2. The contract was regenerated from the served `tools/list` and
the gate re-run once. Both runs are stated because a published figure whose
provenance is "the second attempt" and does not say so is exactly the kind of
number this section exists to prevent.

Three operational facts about that run, because the next person to reproduce
it will hit all three: the gate does **not** create its own port-forwards — it
checks that **five** ports are already listening (19001 claims-agent, 19002
settlement-agent, 18000 payments-mcp, 18080 payments-api, and — since M3 Task
5A — 8081 reviewer-ui) and exits in about a second if any is missing; the
first four are what `setup-kind.sh` normally leaves behind and the fifth comes
from kind's own host-port mapping in `deploy/kind/stage3.yaml`; and
`localhost:8080` and `localhost:16686` come from the same kind node port
mappings rather than from forwards, so those two need nothing.

(**M2.2's figures, kept for the audit trail.** M2.2 Task 8's run of the gate,
against the tree at `50fac07` plus that task's documentation work — which
added and removed no tests, so its 213 collected was the same 213 that commit
carries — finished 212 passed with 1 xfailed in 283.60s and exited 0. Its
final-review fix round added one test to the `tests/` tree
(`tests/e2e/test_gateway_config_drift.py`, adjudication 1) and added the
Policy Decision Service's own suite to the gate's per-service loop (finding
1), so both of that milestone's rows moved by one and the gate was re-run in
full: 213 passed with 1 xfailed in 284.44s, exit 0, against 214 collected. The
M2.1 rows those replaced were measured on a freshly created stage-2 cluster,
`kind delete cluster --name northstar && ./scripts/setup-kind.sh --stage 2`;
an earlier preamble claimed for them the teardown-and-rebuild-at-stage-1
provenance that belonged to the *M1.1* numbers, and a review caught that. The
counts are cluster-independent — they count tests, not spans — but a published
number carrying provenance it does not have is this repo's most-repeated
defect class, so each command is stated as it was actually run.)

| Run | Command | Result |
|---|---|---|
| Default (bypasses the gate) | `pytest tests/` (no env var) | `271 passed, 71 skipped, 1 xfailed` (re-derived 2026-09-14, M4 Task 5, from a re-measured `pytest --collect-only -q tests/` → 343 collected and a **measured** 71 gated — the gated half is now re-measured by a test on every unit run, not by hand) |
| The gate itself | `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` | **`342 passed, 1 xfailed in 417.83s (0:06:57)`, exit 0, final line `Northstar M4 smoke test passed.` — MEASURED 2026-09-14, M4 Task 5, the milestone's one gate run, on the long-running stage-4 `kind-northstar` cluster (not recreated; `teardown.sh` not run). **Zero skips**, which is the figure to read first: a skip is invisible in a green gate (ruling M4-R9), and this milestone gated eleven modules, so "342 passed" would mean nothing without it.** |

(Second final-review round, finding 3: these two rows previously read `49
passed, 6 skipped, 1 xfailed` and `55 passed, 1 xfailed` — stale from before
a commit added seven tests to the suite without updating the one document
whose entire purpose is stating these numbers.
`tests/unit/test_telemetry_contract_counts.py` now guards this: it runs
`pytest --collect-only` and fails loudly if either row's sum ever again
diverges from what pytest actually collects.)

M1.2 note: the skip count in the default row grew from 7 to 15 (Task 4)
because `tests/e2e/test_settlement_llm_trace.py` (8 tests) is gated behind
the same `RUN_BROWSER_TESTS=1` flag — it needs the live cluster and spends
real model tokens on every run, so a bare `pytest tests/` must not trigger
it, while the gate (which sets that flag unconditionally) always does. Task 5
grew it again, 15 to 16: `tests/e2e/test_browser_root_span.py` gained one
test (`test_the_browser_path_settles_the_expected_amount`, at the time asserting the
€6,200 outcome through the browser path specifically — Task 4 review,
Finding 2), and that file shares the same gate. **That test's assertion has
since been rewritten, and this sentence described it wrongly until M2.2 Task 8
re-read it.** It used to say "asserting the €6,200 *outcome*". M2.2's
remediation commit `477e7fb` rewrote the test to assert the **refusal** of that
payout. €6,200 is still the amount the model *proposes*, and the ledger really
does record the proposal — `propose_payment` is permitted unconditionally
(`UNLIMITED_TOOLS`) — but the outcome the test now pins is
`status == "refused"`, not an execution. The figure did not move; the claim
about it did, and a document that kept calling a denial an outcome would be
describing the previous milestone. M1.2 Task 5's fix round 1 grew the skip
count once more, 16 to 17: `tests/e2e/test_settlement_llm_trace.py` gained
`test_tool_call_arguments_carries_the_settled_amount`, pinning
`gen_ai.tool.call.arguments` (fix-round Finding 2). **These are counts only;
M1.2 Task 5 also revises this document's substantive content** — including
what content capture does and does not redact now that it is configuration
rather than a hardcoded constant, which
`tests/e2e/test_settlement_llm_trace.py` measures rather than assumes.
Task 6 grew the **passed** count in both rows by one, 74→75 and 57→58: it
strengthened `tests/unit/test_telemetry_contract_counts.py` with a second
test (see below) that needs no cluster and no browser, so it runs — and
passes — in a bare `pytest tests/` too, not only behind the gate's flag.

M2.1 Task 1 grew the **passed** count in both rows by sixteen, 75→91 and
58→74, and its fix round 1 grew them by six more, to 97 and 80, leaving the
skip count at 17 throughout: `tests/unit/test_stage2_cilium_config.py`
(22 tests) guards the stage-2 Cilium configuration — the create-time
`disableDefaultCNI` flag, the pinned chart version, the two schema defects the
plan's own values sketch contained, and the ordering that keeps Cilium ready
before any workload is scheduled. Six of those twenty-two arrived in the fix
round: five run the real `setup-kind.sh` against fake `kind`/`kubectl` binaries
on `PATH` and assert on what it actually invoked (a review's mutation testing
found three of the original text-grep guards stayed green while the behaviour
they named was deleted, because the string each searched for also appeared in a
comment), and one pins that no operator-facing script or doc still instructs
`--stage 1`. **That last one was widened in M3 Task 5B** — to every stage below
the current one, driven by a single constant, after it stayed green through six
sites that had gone on saying `--stage 2` once stage 3 became the current
cluster. Its name changed with its scope
(`test_no_operator_facing_instruction_still_points_at_an_older_stage`); the
count did not. Like every other `tests/unit/` module they read files or run a
script with fakes rather than touching a cluster, so they need no cluster, no
browser and no model tokens, and they run in both rows.

M2.1 Task 3 grew the collected total 98 → 103, and the skip count 17 → 22:
`tests/e2e/test_gateway_trace.py` (5 tests) proves the MCP hop now traverses
the interposed agentgateway inside one trace, and pins the gateway's own
measured span names, kinds, parentage and attribute vocabulary. It shares the
same `RUN_BROWSER_TESTS=1` gate as the other two live-model files — it drives a
real settlement — so all five of its tests land in the skip count of a bare
`pytest tests/` and in the passed count of the gate.

M2.1 Task 3 fix round 1 grew the collected total 103 → 106, leaving the skip
count at 22: `tests/unit/test_telemetry_contract_attribute_counts.py` (3 tests)
guards this document's published *attribute* counts, the blind spot that let a
stale `gen_ai.*` count survive a whole milestone (see the settlement-agent
model row in the instrumentation table for the full history). Like every other
`tests/unit/` module it reads a file and needs no cluster, no browser and no
model tokens, so it runs in both rows.

M2.1 Task 4 grew the collected total 106 → 131 and the skip count 22 → 23.
Twenty-four of the twenty-five are `tests/unit/test_stage2_spire_config.py`,
which guards the workload-identity configuration: the nested-not-flat trust
domain (a flat `trustDomain:` in that umbrella chart's values is silently
ignored and the install falls back to `example.org`, which looks exactly like
success), the two pinned chart versions, the `className` every
`ClusterSPIFFEID` needs or it is never reconciled at all, the CSI mount
agreeing with the configured socket path, and the gateway's SPIFFE ID being one
identical string in all four files that name it. Four of those twenty-four run
the real `setup-kind.sh` against fake `helm`/`kubectl`/`kind`/`docker` and
assert on what it actually invoked — the CRDs chart before the SPIRE chart,
SPIRE before the workloads that mount its socket, and no SPIRE and no
cert-manager at stage 1 — because those orderings cannot be checked by reading
the file. Like every other `tests/unit/` module they touch no cluster, so they
run in both rows. The twenty-fifth is one more test in
`tests/e2e/test_gateway_trace.py`, pinning that the gateway records the
caller's attested SPIFFE ID as `jwt.sub` on its SERVER spans and does not carry
it onto the CLIENT ones; it shares the same `RUN_BROWSER_TESTS=1` gate as the
rest of that file, which is the whole of the skip-count change.

Task 4's fix round 1 grew the collected total 131 → 134, leaving the skip count
at 23. All three are always-run unit tests, and all three exist because review
found the same defect class in a third disguise: a guard narrower than its own
name. Two close index-narrowness in the module above (a gateway check that read
`binds[0].listeners[0]`, so a second listener with no `jwtAuth` left the suite
green; and an audience check that read module text and took the first match, so
a comment quoting the old value kept it green). The third guards this
document's own published `jwt.sub` value, which had nothing checking it —
swapping it to `.../sa/default` kept the suite green, and `.../sa/default` is
precisely the identity SPIRE minted for that pod before it was given a
ServiceAccount of its own. A regression to the failure the task exists to
prevent would have shipped green, with the document that would have exposed it
already rewritten to agree.

M2.1 Task 5 grew the collected total 134 → 140 and left the skip count at 23.
All six are `tests/e2e/test_caller_identity.py`, which proves the JWT
verification on Hop 3a is load-bearing: one accepted-path control that really
executes `propose_payment` through the gateway, and five refusals — no token,
a non-JWT bearer value, two crafted well-formed tokens from an untrusted
issuer, and a genuine SPIRE-minted JWT-SVID whose only defect is a wrong
`aud`. **It is deliberately not behind `RUN_BROWSER_TESTS=1`, which is why
the skip count did not move.** That gate exists for tests that spend real
model tokens, and this file spends none: it speaks MCP to the gateway
directly instead of driving `settlement-agent`, which is both free and the
stronger test, since routing through the agent would exercise the agent's
error handling rather than the gateway's verification. It does need the live
cluster, and like `tests/e2e/test_payments_api_trace.py` it fails rather than
skips without one — so all six land in the passed count of *both* rows.

**Provenance of these numbers, precisely.** Where a component is derived it says
so, and says what it is derived from:

Task 5's fix round 1 grew the collected total 140 → 143, leaving the skip
count at 23. All three are always-run unit tests in
`tests/unit/test_telemetry_contract_attribute_counts.py`, and all three exist
because review found the *class* of a defect still open after Task 5 had fixed
its instance: the attribute-block check located a block's key count only on
the line immediately adjacent to its fence, and its `>= 2` floor could not
notice a block being deleted. The reviewer demonstrated it by making the old
guard green with a published `19` against a measured `20`. Where that check
was one test it is now three — one per proposition (each block agrees with its
own claim; no published block has gone missing; no attribute block sits
outside the sections checked) — and a fourth new test guards the document's own
count of how many attribute counts it publishes, the number that was wrong at
"nine" for two milestones with nothing asserting it. Net +3, since the four
replace one.

Task 5's fix round 2 grew it once more, 143 → 144: a single always-run unit
test, `test_every_agentgateway_vocabulary_claim_names_the_pinned_version`, in
the same module. It exists because the version-scoping fix of round 1 landed at
three of the **ten** sites that assert agentgateway's attribute vocabulary
diverges from semconv — nine prose paragraphs across three documents and one
test docstring, plus Finding 7's own heading; six were already qualified and
four were not, and the full enumeration is in the task report — and because an
unqualified one of those does not merely go stale when the image is bumped past
v1.5.0: upstream PR #3182 is already merged, so it becomes *false*, and
`test_gateway_trace.py`'s set equality would fail on the attribute names while
saying nothing about the prose describing them. The new test requires every
such claim to name the pinned version in its own paragraph or in the heading it
sits under, whitespace-normalised so a hard-wrapped claim is still found.

The final-review fix round grew it once more, 144 → 145: a single always-run
unit test, `test_no_unqualified_service_count_undercuts_the_documents_own_ordinal_claim`,
in the same module. It exists because final review's finding 1 (HIGH) found
this document's "trace shape" paragraph publishing a stale total — five
services, unqualified — eleven lines below its own claim that agentgateway
was added as a sixth participant, and the only existing guard covering that
trace (`test_all_five_services_are_in_one_trace`) asserts a *subset*, so it
stays green whether the trace has five services or six. The new test is a
hermetic self-consistency check: it reads this
document's own highest ordinal service/participant claim as a floor and fails
if any unqualified "N services" total elsewhere falls below it. Same round's
fix to `test_every_agentgateway_vocabulary_claim_names_the_pinned_version`
(closing a substring escape — see the mutation table in that guard's own
docstring) modified an existing test rather than adding one, so it does not
move this count.

**M2.2 (Decided), the whole milestone in one place, re-derived by M2.2 Task 8
from a live per-file collection rather than from any earlier task's reported
delta.** The chain from M2.1's close (145) to today's 214 is six moves — five
from the milestone and one from its final-review fix round — and every
component below was measured by `.venv/bin/python -m pytest tests/<file>
--collect-only -q`, the first five at `50fac07` and the sixth in the fix round
itself, not read out of a report:

| Commit | What it added | Collected |
|---|---|---|
| `8c0e980` (M2.1 close) | — | 145 |
| `d17309c` | `tests/unit/test_stage2_network_policy.py` (10) and `tests/e2e/test_gateway_is_the_only_route.py` (4) — the payments-mcp **ingress** policy that makes the gateway a choke point rather than a convention | 159 |
| Tasks 3–4 | `tests/unit/test_stage2_pds_config.py` (22) — the PDS's manifest, its SPIRE registration, and the gateway's `mcpGuardrails` block, all read from files | 181 |
| Task 6 | `tests/e2e/test_policy_denial.py` (6) — one test per exit criterion 1–6 | 187 |
| `477e7fb` | **minus one**: `test_payment_was_executed_with_no_governance` retired | 186 |
| `50fac07` (Task 7) | `tests/e2e/test_egress_policy.py` (27) — the Cilium `toFQDNs` egress half of criterion 7 | 213 |
| final-review fix round | `tests/e2e/test_gateway_config_drift.py` (1) — the live gateway ConfigMap against the committed file, `yaml.safe_load`-parsed on both sides (adjudication 1) | 214 |

**The single subtraction is the one number in this table that is not growth,
and it is a milestone marker rather than a cleanup.**
`tests/e2e/test_claims_chain_trace.py::test_payment_was_executed_with_no_governance`
asserted, under the docstring "M1's deliberate horror: nothing stopped the
payment", that `tools/call execute_payment` reached payments-mcp and that
payments-api answered `200`. M2.2 makes both false on purpose: the PDS denies
`execute_payment` above the €6,000 allowance and the execution never leaves the
gateway. The test could not be re-pointed at a smaller amount without becoming
green for the opposite reason to the one it was written for, so `477e7fb`
retired it **visibly** — a comment block stands where it was, quoting its
assertions verbatim and naming `tests/e2e/test_policy_denial.py` as its
successor at both ends of the same rule. That is the whole of the 187 → 186
step, recorded here so the jump in the table above is not left unexplained.

Two of M2.2's five moves also change the **skip** count, and one deliberately
does not. `tests/e2e/test_policy_denial.py` is the only new file that is
partly gated: four of its six tests carry `@browser_test` (criteria 1, 2, 3
and 6 all drive a real browser submission), and criteria 4 and 5 do not —
criterion 4 is a hermetic AST walk over `settlement-agent`'s and
`payments-mcp`'s own source, and criterion 5 speaks MCP to the gateway
directly with two different SPIRE-minted identities, spending no model tokens.
So the skip count moves 23 → 27 and no further: neither
`tests/unit/test_stage2_network_policy.py`, nor
`tests/e2e/test_gateway_is_the_only_route.py`, nor
`tests/unit/test_stage2_pds_config.py`, nor `tests/e2e/test_egress_policy.py`
is behind `RUN_BROWSER_TESTS=1`, for the same reason
`tests/e2e/test_caller_identity.py` never was — that gate exists for tests that
spend real model tokens, and none of those four does.

**M2.2 Task 8 also re-measured the gated set directly rather than assuming it
was still three modules.** It was then four:
`tests/e2e/test_browser_root_span.py` (8), `tests/e2e/test_settlement_llm_trace.py`
(9), `tests/e2e/test_gateway_trace.py` (6) and the four `@browser_test` cases
in `tests/e2e/test_policy_denial.py` — 27, which was then exactly the skip
count in the default row and exactly the difference between the two rows. M3
makes it five and 33; see immediately below.

**M3 (Supervised), the whole milestone in one place, measured the same way.**
The chain from M2.2's close (214) to today's 221 is **one** move, and the
starting figure was re-measured rather than trusted: a throwaway `git
worktree` at `main` (`b472c89`) collects `214 tests collected`, which is
exactly what this document published, so the M2.2 table above needed no
correction.

| Commit | What it added | Collected |
|---|---|---|
| `b472c89` (M2.2 close, re-measured at `main`) | — | 214 |
| `ed2dcf4` (M3 Task 5A) | `tests/e2e/test_supervised.py` (7) — one test per spec §7 exit criterion | 221 |
| M3 final-review fix round | `tests/e2e/test_caller_identity.py` +1 (OQ-U1's regression guard, ungated — see Minor 6 below) and `tests/unit/test_rebuild_and_deploy_guard.py` +3 (the companion-deployment restart, run behaviourally against a sandbox with recording fake `docker`/`kubectl`) | 225 |

The fix round also **changed** three files under `tests/` without changing
their counts, stated here rather than left to be inferred: criterion 5 of
`tests/e2e/test_supervised.py` gained the assertions that reviewer-ui's own
ledger read is a hop of the approval trace, criterion 6 gained a subject
assertion on its own token and criterion 2 gained an ordering note;
`tests/e2e/test_policy_denial.py`'s criterion-4 docstring and failure message
were brought up to what M3 actually changed; and
`tests/unit/test_stage2_cilium_config.py`'s stale-stage guard widened its
file set to `deploy/kind/*.yaml` — still 22, because that guard is
deliberately parameter-free so widening it cannot move this table.

M3 Task 5A's one file was the whole of that task's repo-root move, and the
*absence* of other moves was measured rather than assumed. `git diff --stat
main..HEAD -- tests/` showed this branch had edited **seven** files under
`tests/`. One is
`test_supervised.py` itself, which did not exist at `main`. The other six
still collect exactly what they collected there, because what changed in them
was content, not cases — a SPIFFE allow-list extended inside a test
parametrised over documents (`63729e1`), a stale assertion repaired
(`test_policy_denial.py`, R28), an inverted ingress guard rewritten in place
(`test_egress_policy.py`), a docstring struck off a list, and two guard
constants widened. Every one of the six was collected **per file at `main` and
again at `HEAD` and compared**, not reasoned about:

| File | at `main` | at `HEAD` |
|---|---|---|
| `tests/unit/test_stage2_cilium_config.py` | 22 | 22 |
| `tests/unit/test_stage2_spire_config.py` | 27 | 27 |
| `tests/unit/test_telemetry_contract_attribute_counts.py` | 8 | 8 |
| `tests/unit/test_telemetry_contract_counts.py` | 2 | 2 |
| `tests/e2e/test_policy_denial.py` | 6 | 6 |
| `tests/e2e/test_egress_policy.py` | 27 | 27 |

**The gated set is now five modules, 33 tests, re-measured rather than
incremented.** `tests/e2e/test_supervised.py` is the second partly-gated file:
**six** of its seven tests carry `@browser_test` (criteria 1–6 each drive a
real browser submission, and criterion 3 drives a real approval through the
reviewer UI), and criterion 7 does not — it speaks MCP to the gateway directly
with `reviewer-ui`'s own SPIRE-minted identity and spends no model tokens, the
same reason `tests/e2e/test_caller_identity.py` has never been gated. So the
skip count moves 27 → 33: `tests/e2e/test_browser_root_span.py` (8),
`tests/e2e/test_settlement_llm_trace.py` (9),
`tests/e2e/test_gateway_trace.py` (6), the four `@browser_test` cases in
`tests/e2e/test_policy_denial.py`, and the six in
`tests/e2e/test_supervised.py`. Measured by walking pytest's own collected
items and reading each item's `skipif` marker, not by counting decorators by
eye: 8 + 9 + 6 + 4 + 6 = 33, which is exactly the skip count in the default
row and exactly the difference between the two rows.

**Still 33 after the final-review fix round, re-measured the same way rather
than assumed unchanged.** Both cases that round added are ungated — the OQ-U1
batch guard in `tests/e2e/test_caller_identity.py` (no model tokens, no
browser, the same reason nothing else in that file is gated) and three
hermetic unit tests — so the gated per-file breakdown is byte-identical to
the one above while the tree total moved 221 → 225.

**One correction M3 Task 5B's re-measure turned up in the *M2.1* history
above, left standing with this note rather than rewritten.** That history
states `tests/unit/test_stage2_spire_config.py` gained 24 tests at M2.1 Task 4
and 2 more in that task's fix round 1 — 26. The file collects **27**, measured
at `main` and at `HEAD`. Every other per-file figure in the M2.1/M2.2
narrative reconciles exactly with a live per-file collection
(`test_gateway_trace.py` 6, `test_telemetry_contract_attribute_counts.py` 8,
`test_stage2_cilium_config.py` 22, `test_stage2_network_policy.py` 10,
`test_stage2_pds_config.py` 22, `test_egress_policy.py` 27), and every
published *tree total* reconciles too, so the discrepancy is one off-by-one in
a prose delta rather than in any total. The likeliest cause is visible in the
file: two of its tests are `@pytest.mark.parametrize`d and expand to two cases
each, so one narrative line almost certainly counted an expansion as a single
test. It is **not** silently corrected, because the correct historical
attribution cannot be recovered from this document and guessing at it would
replace a known-wrong number with an unverified one.

- **Measured (M3 final-review fix round):** `.venv/bin/python -m pytest
  --collect-only -q tests/` → `225 tests collected`, and the per-file
  breakdown in the M3 table above accounts for the whole delta from `main`'s
  re-measured 214. This is the number both published rows are anchored on
  today.
- **Measured (M3 final-review fix round), gate row:** `RUN_BROWSER_TESTS=1
  ./scripts/smoke-test.sh` run in full, once, on the live stage-3
  `kind-northstar` cluster with `reviewer-ui` redeployed from this round's own
  tree first — the whole `tests/` tree, real browser, real settlements, a real
  human-approved execution, all **seven** per-service suites including
  `reviewer-ui`'s 59 — returning `224 passed, 1 xfailed`, 0 skipped, in
  353.77s, final line
  "`224 passed, 1 xfailed, 2 warnings in 353.77s (0:05:53)`". Authorised as a
  *second* gate run for this milestone by controller ruling R37, overriding
  the plan's "the gate runs once": this round changed span parentage inside a
  live service, and certifying that on the strength of the pre-change run
  would have made the certification false. It cross-checks against the
  collected total by the second route this document has always used: 225
  collected − 0 skipped − 1 xfailed = 224. The same exit-code qualification
  the Task 5B bullet below states applies here too.
- **Derived (M3 final-review fix round), default row, components shown:** 225
  collected − 33 gated − 1 xfailed = **`191 passed, 33 skipped, 1 xfailed`**.
  Each component is measured above; the label is "derived" for the reason the
  Task 5B bullet below gives, unchanged — a bare `pytest tests/` still drives
  real model-driven settlements, so re-running it to re-observe a number two
  measured figures already determine would spend Anthropic tokens for no
  additional evidence.
- **Measured (M3 Task 5B) — superseded by the two bullets above, kept as the
  record of the pre-fix tree:** `.venv/bin/python -m pytest --collect-only -q
  tests/` → `221 tests collected`, and that task's per-file breakdown
  accounted for the whole delta from `main`'s re-measured 214.
- **Measured (M3 Task 5B), gate row:** `RUN_BROWSER_TESTS=1
  ./scripts/smoke-test.sh` run in full on the live stage-3 `kind-northstar`
  cluster — the whole `tests/` tree, real browser, real settlements, a real
  human-approved execution, all **seven** per-service suites including
  `reviewer-ui`'s 50 — returning `220 passed, 1 xfailed`, 0 skipped, in
  343.14s, final line "M3 smoke test passed." A direct observation of the
  whole gate, including the two count guards that check this very table. It
  cross-checks against the collected total by the second route this document
  has always used: 221 collected − 0 skipped − 1 xfailed = 220.

  Two qualifications, both stated rather than smoothed over. This was the
  **second** run; the first failed on a stale MCP tool contract that predated
  this task (see the M3 provenance paragraph above). And the exit *code* is
  reported here as unobserved rather than as 0: the passing invocation was
  piped through `tail`, so the status captured was the pipeline's. What is
  directly observed is the final line, and `scripts/smoke-test.sh` runs under
  `set -euo pipefail` with that `echo` as its last statement, so no failing
  run can reach it. The earlier milestones' rows below do state a measured
  exit code, and the difference is deliberate.
- **Derived (M3 Task 5B), default row, components shown:** 221 collected − 33
  gated − 1 xfailed = **`187 passed, 33 skipped, 1 xfailed`**. Every component
  is itself measured above (the 33 is the five-module gated count, read off
  pytest's own markers; the 1 xfail is the FastMCP-baggage one below, which is
  not gated and so appears in both rows). Labelled derived rather than
  measured for the reason this row has always carried: a bare `pytest tests/`
  is **not** free — it skips the gated modules but still runs the ungated e2e
  files, which drive real model-driven settlements — so re-running it to
  re-observe a number two measured figures already determine would spend
  Anthropic tokens for no additional evidence.

- **Measured (final-review fix round):** `.venv/bin/python -m pytest
  --collect-only -q tests/` → `214 tests collected`, and the per-file breakdown
  above sums to the same figure. This is the number both published rows are
  anchored on. It was `213` through M2.2 Task 8; the fix round added
  `tests/e2e/test_gateway_config_drift.py` (1).
- **Measured (final-review fix round), gate row:** `RUN_BROWSER_TESTS=1
  ./scripts/smoke-test.sh` run once, in full, on the live stage-2
  `kind-northstar` cluster — the whole `tests/` tree, real browser, real
  settlements, all six per-service suites including the PDS's 96 —
  returning `213 passed, 1 xfailed`, 0 skipped, in 284.44s, exit code 0, final
  line "M2.2 smoke test passed." A direct observation of the whole gate,
  including the two count guards that check this very table. It cross-checks
  against the collected total by the second route this document has always
  used: 214 collected − 0 skipped − 1 xfailed = 213. (M2.2 Task 8's run of the
  same gate, against 213 collected and without the PDS suite in it, returned
  212 passed with 1 xfailed in 283.60s.)
- **Derived (final-review fix round), default row, components shown:** 214
  collected − 27 gated − 1 xfailed = **`186 passed, 27 skipped, 1 xfailed`**.
  Every component
  is itself measured above (the 27 is the four-module gated count, collected
  per file; the 1 xfail is the FastMCP-baggage one below, which is not gated
  and so appears in both rows). Labelled derived rather than measured for the
  reason this row has always carried: a bare `pytest tests/` is **not** free —
  it skips the gated modules but still runs the ungated e2e files, which drive
  real model-driven settlements — so re-running it to re-observe a number two
  measured figures already determine would spend Anthropic tokens for no
  additional evidence.

- **Measured (final-review fix round):** `.venv/bin/python -m pytest
  --collect-only -q tests/` → `145 tests collected`. The four previous
  figures, `144` (Task 5 fix round 2), `143` (fix round 1), `140` (Task 5) and
  `134` (Task 4 fix round 1), were measured the same way.
- **Measured (M2.1 Task 5):** the three `RUN_BROWSER_TESTS`-gated modules,
  `tests/e2e/test_browser_root_span.py` (8),
  `tests/e2e/test_settlement_llm_trace.py` (9) and
  `tests/e2e/test_gateway_trace.py` (6), collected → `23`. They are still the
  only gated modules in the tree; `tests/e2e/test_caller_identity.py` is
  deliberately not one of them (see above), which is why this figure did not
  move when six tests were added.
- **Measured (M2.1 Task 5), the gate at 140 collected:**
  `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` → `139 passed, 1 xfailed` in
  146.75s, 0 skipped, on the SPIRE-issuing, JWT-verifying deployment with the
  gateway config restored to its committed state after that task's mutation
  testing. A direct observation of the whole gate, including the per-service
  suites and the count guards that check this very table.
- **Derived (M2.1 Task 5 fix rounds 1–2), gate row, components shown:**
  139 + 3 + 1 = **`143 passed, 1 xfailed`**. The `+ 3` is round 1's net-new
  always-run unit tests and the `+ 1` is round 2's; all four were run directly
  and pass, and they touch no cluster, no browser and no model. The gate was
  **not** re-run in either round, because both changed only guards and
  documents — no service code, no manifest, no cluster state — and the gate
  drives live model-driven settlements. It cross-checks against the collected
  total by a second route: 144 collected − 0 skipped − 1 xfailed = 143.
- **Derived (final-review fix round), gate row, components shown:** 143 + 1 =
  **`144 passed, 1 xfailed`**. The `+ 1` is the new service-count guard,
  run directly and passing; it touches no cluster, no browser and no model.
  The gate was **not** re-run, for the same reason as the two rounds above —
  only a guard and three documents changed. Cross-checks by the same second
  route: 145 collected − 0 skipped − 1 xfailed = 144.
- **Measured (M2.1 Task 5 fix round 1), default row:** a bare
  `.venv/bin/python -m pytest tests/ -q` was in fact run once during round 1's
  verification and returned exactly the row published at the time,
  `119 passed, 23 skipped, 1 xfailed`, in 83.54s. That is recorded here because
  it means the derivation below has been checked against a real observation of
  this row at least once, rather than only against arithmetic.
- **Derived (final-review fix round), default row, components shown:** 145
  collected − 23 skipped − 1 xfailed = **`121 passed, 23 skipped, 1
  xfailed`**. Every component is itself measured above. Labelled derived
  rather than measured for a reason worth being explicit about: a bare
  `pytest tests/` is **not free** — it skips the three gated modules but still
  runs the ungated e2e files, which drive real settlements through the model,
  so re-running it to re-observe a number three measured figures already
  determine would spend Anthropic tokens for no additional evidence. The one
  xfail is the same FastMCP-baggage one described below; it is not gated, so
  it appears in both rows.
- **Measured (M2.2, network policy), default row:** a bare
  `.venv/bin/python -m pytest tests/ -q` was run directly after adding the
  fourteen network-policy tests and returned `134 passed, 23 skipped, 1
  xfailed` in 88.12s, alongside the single expected failure of *this* guard,
  which was stale at 145 while the tree had grown to 159. With that guard
  itself fixed the row is **`135 passed, 23 skipped, 1 xfailed`**, and
  `pytest --collect-only tests/` reports 159 collected, which is the sum.
- **Derived (M2.2, network policy), gate row, components shown:** 159 collected
  − 0 skipped − 1 xfailed = **`158 passed, 1 xfailed`**. The fourteen new tests
  are ten unit guards over `deploy/platform/cilium-policies.yaml` and four e2e
  checks that attempt the bypass against the live cluster; none of the fourteen
  drives a settlement, so none changes the gate's cost. Labelled derived, not
  measured: the gate was not re-run for this change, because a re-run spends
  Anthropic tokens to re-observe a figure that the collected total and the
  measured default row already determine. Cross-checks by the second route:
  135 default-row passes + 23 that the gate un-skips = 158.
- **Historical, kept for the audit trail:** the previous gate figure,
  `133 passed, 1 xfailed`, was itself derived (`129 + 1 + 3`) from an M2.1
  Task 4 gate run that reported `1 failed, 129 passed, 1 xfailed` in 152s —
  the one failure being this very row, stale at 106 while the tree had grown
  to 131 — plus three always-run unit tests added afterwards. That derivation
  was labelled as such at the time. This revision replaces it with a direct
  observation.

Historical note, kept because it explains why this section is written the way
it is: before M2.1 Task 3 the gate row (`97 passed, 1 xfailed`) was itself
*derived* — `91 + 6`, from a gate run on 92 collected tests plus six always-run
unit tests added afterwards, because the gate drives live model-driven
settlements and a fix round touching only tests and docs did not justify
re-running it. That derivation was labelled as such. This revision replaces it
with a direct observation because Task 3 changes service configuration
(`PAYMENTS_MCP_URL`), so the gate genuinely had to be re-run anyway.

`tests/unit/test_smoke_test_gate.py` (2 tests, counted in both rows above)
pins this distinction directly: it reads `scripts/smoke-test.sh`'s own source
and fails if the gate's pytest target ever reverts to `tests/e2e` (dropping
the 19 other `tests/unit/` harness-correctness tests silently) or if
`RUN_BROWSER_TESTS=1` ever becomes something the gate reads from the caller's
environment rather than forcing itself (A19 — this is the guard for exactly
the plan defect described two paragraphs up).

Per-service unit suites (all nine run by `smoke-test.sh`, before any e2e test):
claims-agent 16, settlement-agent 119, payments-mcp 16, payments-api (Go) 31,
customer-ui (vitest) 6, policy-decision-service 134,
reviewer-ui 56, eval-scorer 160, backstage (jest) 37 — all passing.
(**2026-10-13, M8 Task 0 (agentgateway v1.6.0): settlement-agent 114 → 119,
policy-decision-service 129 → 134.** The deny now travels as tagged text in an
`isError` tool result: the PDS gains `tests/test_deny_text.py` (4) and one
reason/`mcp_error` agreement test (1); the agent gains
`tests/test_policy_denial_text.py` (6) and the `_unwrap` denial tests replace the
`MCPError` fixtures, net of the contract-test rewrite. Re-measured with
`.venv/bin/python -m pytest --collect-only -q tests` from each service's own
directory.)
(**2026-09-15, whole-branch review fix wave Part B: backstage (jest) row
added, at 37.** C1: `platform/backstage`'s six autonomy-contract plugin test
files (`evaluate`, `k8sClient`, `router`, `telemetry`, `index` under
`packages/backend`, `AutonomyContractPage` under `packages/app`) plus the two
untouched scaffold-default suites those workspaces ship with, were run by
nothing -- not `scripts/smoke-test.sh` (zero matches for `backstage`), not
this row. Each carried a jest guard for one of Task 3's review-round
correctness fixes, verified GREEN-to-RED exactly once, by hand, in a
transcript nothing ever re-ran. Re-measured with
`CI=true node_modules/.bin/backstage-cli repo test --silent --json` from
`platform/backstage`, parsing `numTotalTests` (7 suites, 37 tests -- 35 at
C1's own wiring, +1 for this same fix wave's M2 addition to
`evaluate.test.ts`, +1 for M6's retry-then-degrade addition to
`index.test.ts`). `CI=true` is not
optional: without it, `backstage-cli repo test` (a jest wrapper) defaults to
watch mode and the process hangs forever.)
(**2026-09-15, whole-branch review fix wave Part A: policy-decision-service
127 → 129.** J2's deny-branch rule-name test and N6's unrecognised-identity
test both land in `platform/policy-decision-service/tests/test_rules.py`.
Re-measured with `.venv/bin/python -m pytest --collect-only -q tests` from
that service's own directory.)
(**2026-09-14, M5 Task 5 fix round 1: eval-scorer 152 → 160.**
Re-measured with `.venv/bin/python -m pytest --collect-only -q tests` from
that service's own directory. Task 5's opus review returned NOT APPROVED (1
Critical, 4 Important); this closes all four, plus a required ruling on a
fifth. **C1 (Critical):** `Decision.autonomy` was `str`, and `from_span`
parsed an absent `policy.autonomy.level` to `""` -- falsy, so
`if execution.autonomy and execution.autonomy != level:` skipped the
mismatch check entirely for a decision recording no level at all, and the
money check alone could then score it 1.0 compliant (measured pre-M5-Task-5:
0.0 unknown-identity for the identical case). `Decision.autonomy` is now
`str | None` with no `or ""`; absence is its own finding,
`no-level-recorded`, checked before the mismatch and money checks rather
than folded into either. One test, mutated (`if execution.autonomy is
None:` -> `if False:` reddens it). **I1:** the temporal guard's production
wiring (`resolve_level`'s default lambda) was untested -- the only test
exercising the refusal injected a double, so mutating
`now_version=CURRENT_CONTRACT_VERSION` to `now_version=version` left all
152 tests GREEN. One test now drives the real default lambda with a
fixture `version="v2"` and no override; the same mutation now reddens.
**I2:** route 2's dormancy (M5-R29) was hoped for, not enforced --
`pds/server.py` writes `feature_flag.version` unconditionally, so adding a
flag `metadata` block to `featureflag.yaml` (route 1, left for later) would
make every decision carry a version while `CURRENT_CONTRACT_VERSION` stayed
`None`, refusing every trajectory. One build-time test in
`tests/test_contract.py`, parsed from the file (not retyped), asserts no
flag `metadata` block exists while `CURRENT_CONTRACT_VERSION is None`;
adding one to `featureflag.yaml` reddens it (confirmed, then restored --
the live resource and `featureflag.yaml` are both untouched by this fix
round). **I4:** the third S-b instance, `opensearch.py`'s
`aggregation.get("doc_count_error_upper_bound") or 0`, eight lines below
`sum_other_doc_count`'s own M5-Task-5 fix, in the same method -- closed the
same way, and folded in Minor M1 (a present-but-null value, for both
fields, must also raise: a presence check alone is not enough). Three
tests, two mutations (the shared presence check, and the shared
numeric-type check), both reddening for both fields.

**I3, handed over as a decision rather than left as a note (ledger ruling
M5-R32):** `TrajectoryUnscorable` answered HTTP 500 on a
`# pragma: no cover` branch while its own docstring claimed 502 parity with
`TrajectoryTruncated`, and it suppressed all five scores, not the one
dimension it concerned. `scorer/scores.py::scores_for` now runs each
scorer independently and omits only the one whose `TrajectoryUnscorable`
it catches; `scorer/server.py`'s `/score` response gains an `unscorable`
field naming any such dimension explicitly, so a caller cannot mistake
four scores for a complete five. `scorer/server.py` also answers 502 for
`TrajectoryUnscorable` directly, as defense in depth for a path that
should no longer be reachable in the designed flow. Re-measured the
review's own mutation K (`CURRENT_CONTRACT_VERSION = None` -> a real
string, simulating the contract and the deployed flag going out of step):
**before** this fix, 21 failed, 131 passed, including every
`test_server.py` test -- the whole service went dark. **After**, the
identical mutation: 17 failed, 142 passed, 1 skipped -- only tests that
depend on `identity_had_authority` fail; every other `test_server.py` test
(window pass-through, anchor-span reporting, emit/flush ordering) now
passes, because `score_claim` still returns a row. Two tests added for
this specifically.)
(**2026-09-14, M5 Task 5: eval-scorer 135 → 152.**
Re-measured with `.venv/bin/python -m pytest --collect-only -q tests` from
that service's own directory. `score_identity_had_authority` used to look
the published execute allowance up under `policy.autonomy.level` -- the
`policy.decision` span's OWN self-reported level -- so a PDS (or an
attacker who reached the span) claiming "autonomous" sent it to the
EUR 1,000,000 row and it agreed all the way down: decoration, by spec §5's
own criterion. A new module, `scorer/contract.py`, holds the audit's
independent answer to "what level does the contract actually say", the same
pattern `scorer/authority.py`'s docstring argues for the limit table, and
`scores.py` now consults it instead of trusting the span. Seventeen tests:
eleven new in `tests/test_contract.py` for `contract_level_at`'s temporal
guard (all four presence/absence quadrants, not just the two the plan's
sketch considered) and `current_level`'s build-time parity with
`featureflag.yaml`; two new in `tests/test_scores.py` reproducing the exact
vacuity scenario (a span claiming "autonomous" against a contract that says
"assisted") and proving `TrajectoryUnscorable` propagates out of the scorer
uncaught; one new in `tests/test_authority_matches_the_policy.py` extending
the build-time check to `featureflag.yaml`'s declared variants; three new in
`tests/test_opensearch.py` closing S-b (M4 debt list) -- both truncation
guards read a genuinely absent field (`sum_other_doc_count`,
`hits.total.value`) the same as an honest zero/match, which is "I could not
look" recorded as "there was nothing to find". All of it was run through
`scripts/mutate-guard.sh` and confirmed RED before being left green,
including the temporal guard, the level-mismatch check, both S-b guards,
and the build-time YAML parity check (mutating `featureflag.yaml`'s
targeting branch itself, then confirming the file was restored to its
M5-R8 refusing state afterward).

**The `feature_flag.version` STOP, decided here rather than in the plan's
sketch.** Measured against the live cluster on 2026-09-14:
`kubectl -n northstar-platform get featureflag northstar-autonomy -o
jsonpath='{.spec.flagSpec.flags.autonomy-level}'` returns no `metadata`
block at all, so `AutonomyResolution.version` (and this span's
`feature_flag.version`) is `None` for every real decision -- the plan's
sketch (raise whenever `decision_version` is `None`) would refuse to score
every trajectory, always. `scorer/contract.py`'s module docstring records
the two routes considered and why route 2 was taken: treat "no version on
either side" as agreement, not divergence, and refuse only when the two
disagree while at least one is actually present. This keeps the module
callable today and is honest that its temporal half is dormant until
something publishes a real version -- it does not touch `featureflag.yaml`,
so M5-R8's ship-in-refusing-state is untouched by this task.)
(**2026-09-14, M5 Task 4: policy-decision-service 124 → 127.**
Re-measured with `.venv/bin/python -m pytest --collect-only -q tests` from
that service's own directory. Three tests in `tests/test_autonomy.py` close
the guard `pds/autonomy.py::emit_evaluation_event` (Task 4's second-signal
log record) had none of on its own: one drives a real `AutonomyResolution`
through a hermetic `LoggerProvider`/`InMemoryLogRecordExporter` and asserts
every field that can be present is, spelled with the current semconv keys;
one asserts the emitted record's own `timestamp` is a real wall-clock value,
not the SDK's `None`-passthrough default (the epoch-timestamp defect this
task found and fixed against the live cluster, before this hermetic guard
existed to catch it); one asserts a `None` field (`version`/`set_id`/
`variant`/`provider_name`) is omitted from the record's attributes rather
than invented as `null` or `""`, mirroring the same convention Task 3 already
proved on the `policy.decision` span. All three were run through
`scripts/mutate-guard.sh` and confirmed RED before being left green.)
(**2026-09-14, M5 Task 3 fix round 1: policy-decision-service 119 → 124.**
Re-measured with `.venv/bin/python -m pytest --collect-only -q tests` from
that service's own directory. The task-3 review found the milestone's
central wiring (`server.py:303`, `autonomy_level=resolution.level`) had no
guard — reverting it to the M2 constant left every suite green — and found
two Minor gaps genuinely unguarded, plus one M5-R23 substitute that was
described as run but left no artifact. Five tests close them: one in
`tests/test_servicer.py` drives a real `autonomous` resolution through
`PolicyServicer` and asserts level, rule, limit and permit together (C1);
one in `tests/test_autonomy.py` proves `TracingHook`, once registered, does
put a `feature_flag.evaluation` event on the active span, using a real
`OpenFeatureClient` and `InMemoryProvider` rather than the double that
bypasses hooks by design (M2); one in `tests/test_servicer.py` proves
`pds.server`'s own module-scope client is the one actually carrying that
hook, not merely that the hook class works (M2's other half); one in
`tests/test_servicer.py` drives a `flag_metadata` version through to the
span, closing the dead `feature_flag.version` branch (M1); and one in
`tests/test_rules.py` parses `tests/e2e/test_supervised.py` criterion 4's own
asserted rule-name literal via `ast` (not retyped) and compares it against
`decide()`'s output for the senior identity at EUR 6,200 (M5-R23).
`test_autonomy.py` also replaced a tautological assertion with one that
forces the fail-closed level down even when the fake provider hands back
`"autonomous"` (I2) — that changes what one existing test asserts, not the
count.)
(**2026-09-14, M5 Task 3: policy-decision-service 105 → 119.** Re-measured
with `.venv/bin/python -m pytest --collect-only -q tests` from that service's
own directory. Fourteen tests for resolving the autonomy level from flagd
instead of a compiled-in constant: nine in the new
`tests/test_autonomy.py` (the fail-closed path, the seven-field
`AutonomyResolution` record, the evaluation-context shape, and the two
M5-R4 behavioural proofs that the client is configured against the port
`featureflagsource.yaml` actually names and that it resolves over RPC, not
in-process or file), three in `tests/test_pins.py` for the task's three new
pins (`openfeature-sdk`, `openfeature-provider-flagd`,
`openfeature-hooks-opentelemetry`), and two in `tests/test_rules.py` -- one
confirming an autonomy-level variant absent from `EXECUTE_LIMITS` denies
through the existing table with no new branch, one asserting the
`(settlement-agent, autonomous)` row's rule name is no longer the
pre-existing mislabel (Step 8's mutation round found this row had no
positive assertion of its own until this suite added one).)
(**2026-09-14, M4 final-review fix wave: eval-scorer 128 → 135.** Seven tests
for finding H1's truncation refusal — `trace_ids_for_claim` now reads the terms
aggregation's `sum_other_doc_count` and `doc_count_error_upper_bound` and
refuses to score a trajectory it cannot see all of. Two of the seven are
positive controls, because a guard that refuses is worth nothing without proof
that it still accepts.)
(**2026-09-13, M4 Task 4: the eighth entry, `platform/eval-scorer` 128**,
measured with `.venv/bin/python -m pytest --collect-only -q tests` from that
service's own directory. It is added in the task that added the service, which
is what the sixth and seventh entries' own notes below say should happen and
what neither of them did — both arrived a milestone late, and in the
reviewer-ui case that meant 50 shipped tests nothing ran. The venv is required
by `scripts/smoke-test.sh`'s prerequisite loop and the suite is run in its
Step 1/2, in the same change.)
(**2026-09-13, M4 Task 2: settlement-agent 110 → 114.**
`agents/settlement-agent/tests/test_telemetry.py`, four tests, re-measured
with `.venv/bin/python -m pytest --collect-only -q tests` from that service's
own directory: that `configure()` installs an SDK `MeterProvider`, that the
provider exports over OTLP on a `PeriodicExportingMetricReader`, that the
endpoint comes from the standard `OTEL_EXPORTER_OTLP_ENDPOINT` env var, and
that no `View` drops an instrument. settlement-agent carries these for all
five services because it is the copy the shared wiring's rationale lives in;
`tests/unit/test_metrics_wiring.py` is what holds the other four to it.)
(**2026-09-11, M3 remaining-bumps: settlement-agent 108 → 109 → 110**, one test
per step, both in `agents/settlement-agent/tests/test_pins.py` and both for the
same reason — a bump introduced a second distribution that must not drift away
from the first, and nothing else in this repo would have said so. Step 1's
`test_mcp_types_is_pinned_in_lock_step_with_mcp`: v2 split the MCP wire types
into their own `mcp-types` distribution, which `mcp.types` now aliases, so a
drift there would be a drift in every request settlement-agent builds. Step 2's
`test_pydantic_graph_is_pinned_in_lock_step_with_pydantic_ai_slim`:
`pydantic-graph` ships from the same repository and release as
`pydantic-ai-slim`, and the two were pinned independently. Net +2; no test was
removed.)
(**The seventh entry arrived in M3 Task 5B, and it is the same defect
as the sixth, one milestone later.** `reviewer-ui` shipped in M3 Task 4 with
its own `requirements.txt`, its own `.venv`, its own `pytest.ini` and 50 tests
of its own — the component that carries the human decision this whole
milestone is named for — and was in neither the gate's per-service loop, nor
this row, nor `_PER_SERVICE_SUITES`. Unlike the PDS's case there was no hidden
dependency doing half the work: nothing ran those 50 tests at all, so the gate
could go green with `apps/reviewer-ui` broken. All three sites are now closed
together, in the same task, which is the lesson the sixth entry's own note
below already spells out. Its figure is measured by the same route as the
other six, `cd apps/reviewer-ui && .venv/bin/python -m pytest --collect-only
-q tests` → `56 tests collected`, and the suite runs in 0.24s with no cluster,
no network and no model. It read **50** from Task 5B until the final-review
fix round, which added nine: five in a new `tests/test_bootstrap.py` pinning
the trace-flags workaround from both sides, three in `tests/test_main.py`
covering `review.queue`'s span, its ERROR status on a ledger outage and
`GET /healthz` still emitting nothing, and one in `tests/test_review.py`
pinning that `ledger.get` runs inside `review.decision`. Two existing tests in
that file changed their assertions in the same round and are counted in the
50 as well as the 59 — see `review.py`'s own correction note.
**2026-09-11 (M3): 59 → 56.** It read **59** until payments-api moved to
`go.opentelemetry.io/otel v1.46.0` and the trace-flags workaround was deleted
with it. `tests/test_bootstrap.py`'s five workaround tests went; two replaced
them, pinning the state that workaround used to prevent — that the registered
trace-context propagator is upstream's own class and not a subclass of it,
and that the injected flag byte is now `03`. Net −3. The file itself stays,
and its docstring names all five retired tests rather than deleting them
without trace.) (The sixth
entry arrived in the M2.2 final-review fix round: `policy-decision-service` was
in neither the gate's loop nor this row until finding 1 showed both already
*depended* on that component's generated stubs while collecting none of its
tests. `scripts/smoke-test.sh` now runs `gen-stubs.sh` ahead of every suite and
then runs the PDS's, and `_PER_SERVICE_SUITES` has a sixth row, so that figure
is measured by the same route as the other five rather than re-typed — it was
96 when that round wrote this sentence and is 105 today, re-measured for M3
Task 5B. See
limitation 10 below for what remains open. This row was stale at 19 for
settlement-agent from before M1.2 Tasks 1-4 added the PydanticAI agent's own
tests — `tests/unit/test_telemetry_contract_counts.py` cannot see per-service
rows, only the whole-tree total, so nothing caught the drift until Task 5
re-measured it directly: `cd agents/settlement-agent && .venv/bin/python -m
pytest tests -q` → 31 passed, including this task's own 7-test
`tests/test_instrumentation_settings.py`. Grown again to 33 by the M1.2 final
fix round: SF-2 added two pin assertions to `tests/test_pins.py`, pinning
`pytest`/`pytest-asyncio` themselves against the silent-downgrade gap the
final review found — re-measured the same way, not typed by hand. Grown
again by M2.1 Task 6, spec §11: claims-agent 14→16 and payments-mcp 7→8,
both from adding that same service's own `tests/test_pins.py` -- the pin
guard this row's settlement-agent history already carried, now closed for
the two services that carried M1.2's advisory-gate gap instead. Every count
in this row, including the three left unchanged, was re-measured for this
edit by invoking each service's own runner directly -- `.venv/bin/python -m
pytest --collect-only -q tests` for the three Python services, `GOTOOLCHAIN=local
GOPROXY=off go test ./... -list '.*'` for payments-api, `node_modules/.bin/vitest
list` for customer-ui -- not re-typed from memory of the prior row. Grown again
by M2.1 Task 4, settlement-agent 33→49, and re-measured the same way: the new
`tests/test_svid.py` (9) covers the JWT-SVID fetch-and-cache seam branch by
branch -- including the refresh margin *at* the boundary, with a frozen clock,
because a `>` that should be `>=` there is a token that expires mid-flight;
`tests/test_mcp_tools.py` gained 4, pinning that the SVID travels as an
`Authorization: Bearer` header, that it is minted for the gateway's audience,
that no transport is opened at all when no SVID is available, and that the
identity and trace carriers stay separate; and `tests/test_pins.py` gained 3
for the new `spiffe` dependency and its two transitives, one of which imports
`WorkloadApiClient` so a compiled-wheel mismatch fails as a test rather than at
pod start. Grown again by M2.2's unplanned Task D1, which threads the payment
amount through to the `execute_payment` tool call so the execute step is
governable at the gateway's policy hook and confirmable by payments-api at all:
settlement-agent 49→52 and payments-mcp 8→9, re-measured with each service's
own runner as above. The three new settlement-agent tests pin that the amount
reaches `PaymentsTools.execute` from the model's tool call, that it lands in the
`tools/call` arguments dict — the only thing the policy hook sees — and that
omitting it is a `TypeError` rather than a silent default; payments-mcp's new
test pins `amount` as *required* in the advertised input schema, since an
optional amount is one the model can leave out and both checks then quietly
stop applying. payments-api 7→15 in that same task's second commit, the one
that makes the threaded amount load-bearing: the execute handler now confirms
the stated amount and currency against the recorded proposal and refuses on
disagreement, so the new Go tests cover a matching amount executing, a
mismatched amount and a mismatched currency each refused with 409 *and* leaving
the payment proposed, a missing body and an amount-less body each refused with
400, the half-cent epsilon absorbing float representation noise, and that same
epsilon still refusing a one-cent difference. Re-measured with `GOTOOLCHAIN=local
GOPROXY=off go test ./... -list '.*'` → 15, not counted by hand. Grown again
by M2.2 Task 4, payments-api 15→20, for rulings R23/R18: that task added one
`northstar.payment.refusal_cause` span attribute naming which of this
service's two 409 causes refused an execution, because the amount-mismatch
refusal added by D1 and the already-executed refusal are indistinguishable in
a trace otherwise — both are `http.response.status_code=409`, and a 4xx leaves
the server span `Unset`, so neither the code nor the status separates them.
The five new Go tests read the attribute off a real recorded span via an
in-memory `tracetest.SpanRecorder` rather than trusting the call site, and
cover: each of the two causes recorded correctly, the two values being
distinct at all, the attribute being ABSENT on a successful execution (so its
presence means 'refused here'), and the `northstar.` prefix that marks it as
ours rather than a semantic convention. Re-measured the same way → 20.
Grown again by M2.2 Task 5, settlement-agent 52→73, re-measured with that
service's own runner as above (`tests/test_mcp_tools.py` 18→33,
`tests/test_settlement.py` 9→15). That task makes a policy denial terminal at
the agent: `mcp_tools.PolicyDenial` is a `pydantic_ai.ToolFailed`, so the model
sees the refusal and narrates it, and is deliberately *not* a
`pydantic_ai.ModelRetry`, which the framework would hand back to the model as
"try again". The 21 tests are weighted towards the two properties a
"a deny happened" assertion would not catch: that the denial is recognised by
the structured marker `data.decision == "deny"` and never by the reason prose,
that a transport failure and a denial-shaped message with no marker both stay
transport failures, and that exactly one execute attempt reaches the payment
system — counted, with a reactive `FunctionModel` that retries if and only if
the framework asks it to, so making `PolicyDenial` a `ModelRetry` turns that
test red rather than leaving it green on a loop that eventually gives up.
Grown again by that task's fix round 1, settlement-agent 73→79, re-measured
the same way: the new `tests/test_policy_deny_contract.py` (6) closes a
producer/consumer contract that had nothing asserting it. The deny marker was
hand-duplicated -- written in `pds/server.py::build_result`, copied as a
literal into `agents/settlement-agent/tests/test_mcp_tools.py` -- and it was
measured that changing the PDS to emit `"denied"` instead of `"deny"`
reddened two tests in the PDS suite and **zero** in the agent's, even though
the agent is the thing that stops narrating a refusal when the marker moves.
The new file copies nothing: it drives the real chain (`parse_call` ->
`decide` -> `build_result` -> the gateway's measured error mapping -> a real
`McpError` -> `mcp_tools._policy_denial`) with a **test-only** import of the
PDS, guarded by a test that fails if any production module in the service ever
grows a `pds` import. Same mutation now reddens four tests in
`agents/settlement-agent`. **M2.2 Task 8 re-measured all five figures with each
service's own runner, the same way, and none of them moved** — every test M2.2
added after that fix round went under the repo-root `tests/` tree, so Task 5's
fix round 1 remains the last change to this row. Re-measuring rather than
reasoning that nothing can have changed is the point: this row's entire history
is a number that stayed correct until the moment nobody checked it. Grown again
by M3 Task 1 Part A: `platform/policy-decision-service/tests/test_pins.py` (1)
closes the same "no advisory gate" mechanism gap for the fourth and last Python
service, pinning `pytest==9.0.3` via `importlib.metadata` the same way the
other three already do (settlement-agent directly, claims-agent and
payments-mcp since M2.1 Task 6) — policy-decision-service 96→97, re-measured
with `.venv/bin/python -m pytest --collect-only -q tests` the same way as
every prior entry in this history, not carried forward from the row above.)
Grown again by M3 Task 2, payments-api 20→28: the ledger side of a human
reviewer seeing why a payout was refused. `store.go` gained `StatusRefused`
and a `refusal` record (`rule`, `limit`, `requested`, `narrative`,
`refusedTraceId`, and a `refusedAt` the ledger stamps itself, not supplied by
the caller); `handlers.go` gained `POST /payments/{paymentId}/refuse` and
`GET /payments?status=`. The eight new Go tests cover: a refused proposed
payment reaching `refused` with every field stored and persisted; refusing an
already-executed payment rejected with the same 409 shape as the execute
handler's own already-executed conflict (both trace back to the store's one
`ErrExecuted`); an unknown payment id refused with 404; the status-filtered
list returning only the matching payments and none of the others; a missing
`rule` and a malformed body each rejected with 400; an unfiltered list
returning every payment; and — the property this task turns on —
`TestARefusedPaymentCanStillBeExecutedAfterwards`, proving `refused` is
terminal-until-approved rather than a dead end: nothing in this service's
execute path was touched, so the same payment reaches `executed` afterwards
exactly as it always could. Re-measured with `GOTOOLCHAIN=local GOPROXY=off
go test ./... -list '.*'` → 28, not counted by hand.
Grown again by M3 Task 3, the colleague with higher approval authority:
policy-decision-service 97→101 (one `EXECUTE_LIMITS` row for
`senior-settlement-agent`, and `refuse_payment` joining `propose_payment` in
`UNLIMITED_TOOLS` — four new tests in `tests/test_rules.py`, `decide()` itself
untouched); payments-mcp 9→12 (a `refuse_payment` MCP tool and
`PaymentsClient.refuse()`, exercised in `tests/test_tools.py` and
`tests/test_payments_client.py`); settlement-agent 79→101 (`mcp_tools.
PaymentsTools.refuse()`; `decide_settlement`'s refused branch now records the
refusal through it, with a recording failure logged and swallowed rather than
surfaced to the claimant; the new `execute_approved()` entry point and its
`_refusal_link()` helper, in the new `tests/test_execute_approved.py`; and
`executor.py`'s approval dispatch in `tests/test_executor_reply.py`, which
also fails loudly — never defaults to executing — on five shapes of malformed
approval payload). Every count in this paragraph was re-measured the same way
as every entry before it: `.venv/bin/python -m pytest --collect-only -q
tests` from each service's own directory, not carried forward from memory of
a prior figure.
Grown again after M3 Task 3, by that task's own review fix rounds and by Task
4. **Every figure in the row above was re-measured from scratch for M3 Task
5B**, with each service's own runner from its own directory, exactly as every
entry before it — `.venv/bin/python -m pytest --collect-only -q tests` for the
four Python services and `apps/reviewer-ui`, `GOTOOLCHAIN=local GOPROXY=off go
test ./... -list '.*'` for payments-api, `node_modules/.bin/vitest list` for
customer-ui. Which commit moved which suite was read from `git show --stat`,
not from memory: `605ba8c` (the ledger check `execute_approved` performs before
it executes, plus the PDS ingress lockdown) added tests to settlement-agent,
payments-mcp and policy-decision-service; `5d56374` (refuse rejects a second
refusal and confirms the requested amount, R12) to payments-api's
`handlers_test.go`; `b394117` (the claim-id baggage on
`settlement.execute_approved`, R16) to settlement-agent's
`tests/test_execute_approved.py`; and `6690e49` created the whole seventh
suite, `apps/reviewer-ui/tests/` (7 files). **The intermediate per-commit
figures are deliberately not published here**, because they were not
measured — reconstructing them would need a venv per service at each of those
commits, and this row has been burned before by a number that was reasoned to
rather than observed. What is published is what was observed: today's seven
figures, and which commits touched which suite. claims-agent 16 and
customer-ui 6 did not move, and that was measured the same way rather than
inferred from the diff.
customer-ui's suite includes its own
A19 regression test for the traceparent-across-await fix described below
(`apps/customer-ui/NOTES.md` §2) — worth naming explicitly, since it's the
one unit suite that pins a real, previously-silent defect in this project's
own code, not only in a third-party library's behavior.

**The sixth per-service suite was outside that row for the whole of M2.2, and
the M2.2 final-review fix round brought it in. The reason it had been left out
was false, which is why this paragraph is rewritten rather than deleted.**
M2.2 Task 4 added `platform/policy-decision-service/tests/` — **96 tests**
(measured for this edit by `cd platform/policy-decision-service &&
.venv/bin/python -m pytest tests -q --collect-only` → `96 tests collected`, not
carried forward from any task report; it was 95 through M2.2 Task 8, and the
fix round added `tests/test_proto_pin.py`, the sha256 guard on the vendored
ExtMCP proto — finding 6, ruling R45).

Through M2.2 this document said the suite stayed out of both the gate and the
count guard because it needs generated gRPC stubs (`gen-stubs.sh`,
git-ignored), and neither `scripts/smoke-test.sh`'s per-service loop nor the
hermetic `tests/unit` was willing to take on a generation step. **The final
whole-branch review demonstrated that premise false.** Both already *required*
those stubs: `agents/settlement-agent/tests/test_policy_deny_contract.py`
imports `pds.rules` and `pds.server`, which import `ext_mcp_pb2`, so moving the
stubs aside aborts the settlement-agent suite at collection (0 of 79 tests run)
and reddens `test_per_service_counts_match_what_each_suites_own_runner_collects`
itself (110 passed, 1 failed). The gate was paying the entire cost of the
dependency and collecting none of the 96 tests in return, while `README.md`
told a new operator the PDS setup was not needed for the gate — so a fresh
clone following the README verbatim could not run `smoke-test.sh` at all.

What the fix round changed, in three places and without committing the stubs
(that is what ruling R22 exists to prevent): `scripts/smoke-test.sh` requires
the PDS venv alongside the other three, runs `./gen-stubs.sh` **before any
suite** — which is also what the settlement-agent suite has silently needed all
along — and runs the PDS's own suite; `_PER_SERVICE_SUITES` gained a sixth row;
and the published row above gained `policy-decision-service 96`. The count is
now measured by exactly the same route as the other five, and the gate's real
prerequisites match its documented ones. What remains open is narrower and is
restated as limitation 10 below.

**M1.2 Task 6 closed that blind spot rather than re-stating the number a
fourth time.** The stale-at-19 defect above was the third recurrence of the
same failure mode (a published count drifting because nothing automated
checks it), and the first two recurrences both went through
`test_telemetry_contract_counts.py`'s whole-tree check — which is exactly
why that check exists and why this row, structurally invisible to it, kept
drifting instead.
`test_per_service_counts_match_what_each_suites_own_runner_collects`
(same file) now parses this exact row and, for each service, calls that
service's own test runner in collect/list mode — `pytest --collect-only`
for the three Python services, `go test ./... -list '.*'` (with
`GOPROXY=off`) for payments-api, and `node_modules/.bin/vitest list` for
customer-ui, invoked directly rather than via `npx vitest run` (the command
`scripts/smoke-test.sh` itself uses to *run* that suite) — just asking what
exists rather than running it. Verified by deliberately corrupting
`settlement-agent 31` to `settlement-agent 30` in this file: the new test
went **RED** (`settlement-agent: the contract says 30, but its own test
runner collects 31 in agents/settlement-agent`), confirming it actually
reads this row and actually compares against a live count, not a cached or
hand-maintained one; reverted immediately, `__pycache__` swept, and
confirmed green again before this document was committed. **Fix round
(Task 6 review, Finding C):** the per-service check originally shelled to
`npx vitest list`, which on a cold machine with no local `node_modules`
resolves `vitest` over the network (or, worse, silently through a stale
global `~/.npm/_npx` cache from an unrelated earlier install, as reproduced
in the review) — a hidden network dependency this guard's own docstring
claimed not to have. It now asserts the service's own
`node_modules/.bin/vitest` exists first, the same way the Python counters
assert their venv exists, and invokes that binary directly; `_go_test_count`
now also sets `GOPROXY=off` so a cold Go module cache fails loudly instead
of fetching. A missing toolchain is a hard failure in every counter, never a
skip.

The one remaining xfail in both runs is
`tests/e2e/test_claim_correlation.py::test_claim_id_is_on_every_mcp_hop_span`,
documenting the FastMCP baggage gap below (Gap 4). It is deliberately narrow —
pinned to that one assertion, via a manual `pytest.xfail()` inside a
`try/except AssertionError`, not `@pytest.mark.xfail(strict=True)` (S3,
final-review.md — a decorator on this test would also swallow a failure
during the module-scoped fixture's own setup, e.g. the MCP hop dropping the
trace connection entirely, as a quiet `xfailed` indistinguishable from this
test's own documented condition; the manual form lets that surface as a loud
`ERROR` instead). Its behaviour is `strict=True`-equivalent in the direction
that matters: the `else` branch calls `pytest.fail` if the assertion ever
stops failing, so the day this gap closes upstream, the run fails until the
marker is removed, rather than staying silently green.

## Enabled instrumentation, as verified

| Component | Instrumentation | Version | Verified span names | Notes |
|---|---|---|---|---|
| customer-ui | OpenTelemetry JS web, manual root span | `@opentelemetry/sdk-trace-web` 2.8.0, `@opentelemetry/context-zone` 1.30.0, `@opentelemetry/exporter-trace-otlp-http` 0.219.0, `@opentelemetry/resources` 2.8.0 | `claim.submit` | Owns the trace root. Requires `traceparent`/`tracestate`/`baggage` in claims-agent's own CORS `allow_headers` (`agents/claims-agent/main.py`) so the browser's request survives preflight — the collector's own CORS requirement is different: it needs `http://localhost:8080` in `allowed_origins` (its `allowed_headers` is already wildcarded), since the browser's OTLP export to the collector carries no `traceparent` header of its own. Requires a *static* import of the injection helper — a dynamic `import()` (or any other `await`) before reading the active span loses it under `ZoneContextManager` (`apps/customer-ui/NOTES.md` §2; pinned by that service's own vitest regression test). |
| claims-agent | `a2a-sdk[telemetry]` + `opentelemetry-instrumentation-starlette` | `a2a-sdk[telemetry]==0.3.7`, `opentelemetry-instrumentation-starlette==0.65b0` | ASGI entry span `POST /`; `a2a.server.request_handlers.jsonrpc_handler.JSONRPCHandler.on_message_send` and the rest of the `a2a.server.*` call-graph spans (`DefaultRequestHandler.on_message_send`, `EventQueue.enqueue_event`/`.dequeue_event`/`.close`, `EventConsumer.consume_all`, etc.) | `DISABLE_OTEL_INSTRUMENTATION` must remain unset; `bootstrap.configure()` raises if it is `true`. Inbound W3C trace-context extraction comes from `StarletteInstrumentor` wrapping the built ASGI app (A18) — `a2a-sdk` itself extracts nothing (see Gap 1). **`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set** (`deploy/k8s/claims-agent.yaml`) — OTel Python's HTTP instrumentation defaults to the legacy spelling (`http.method`/`http.url`/`http.status_code`) on both the ASGI entry span and the outbound httpx span; without this, this service alone would mix conventions with the Go hop (`agents/claims-agent/NOTES.md` §7, which also records a measurement mistake made while proving the opt-in was genuinely needed, not assumed). |
| settlement-agent | `a2a-sdk[telemetry]` + `opentelemetry-instrumentation-starlette` + `mcp` client (**which now instruments itself**) | `a2a-sdk[telemetry]==0.3.7`, `mcp==2.2.0` (2026-09-11; was `1.28.1`, and `1.9.4` before that), `opentelemetry-instrumentation-starlette==0.65b0` | Same shape as claims-agent (`POST /` ASGI entry span, `a2a.server.*` call-graph spans), plus outbound `httpx2` CLIENT spans (`POST`/`GET`/`DELETE`) to payments-mcp, plus — as of M1.2 Task 8 — outbound `httpx2` CLIENT spans (`POST`) to `api.anthropic.com`, nested under each `chat` span, plus — **new with `mcp` 2.2.0** — the SDK's own CLIENT spans `MCP send initialize` and `MCP send tools/call <tool>`, one per JSON-RPC request it sends | Only agent permitted to reach payments-mcp. Same `StarletteInstrumentor` fix as claims-agent for inbound extraction. **`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set** (`deploy/k8s/settlement-agent.yaml`), same reason as claims-agent — measured legacy-by-default, confirmed stable after the opt-in (`agents/settlement-agent/NOTES.md` §6); the same opt-in applies to both `HTTPXClientInstrumentor` and `HTTPX2ClientInstrumentor`, since both share one `get_tracer` call in the same module. **2026-09-11 correction: the outbound spans to payments-mcp used to come from `HTTPXClientInstrumentor` and now come from `HTTPX2ClientInstrumentor`.** `mcp` 2.x's streamable-HTTP client is built on `httpx2`, a distribution separate from `httpx`; the `httpx2` instrumentor was already applied here (M1.2 Task 8, for `anthropic`), so nothing had to be added, but the attribution in this cell was wrong the moment the bump landed. See "the `mcp` client's own telemetry, as measured" below for the new spans' full inventory. |
| settlement-agent (model) | PydanticAI `Agent.instrument_all(InstrumentationSettings(...))` (`bootstrap.py`) | `pydantic-ai-slim==2.42.0` (2026-09-11; `2.41.0` until then), `version=5` (the library default, pinned explicitly) | `invoke_agent settlement_agent` (internal, ×1/request), `chat claude-sonnet-5` (client, ×3/request — one per model turn: propose, execute, final narrative), `execute_tool propose_payment`, `execute_tool execute_payment` (internal, ×1 each) | As of M1.2: the settlement decision itself, not only its transport. **28 distinct `gen_ai.*` attribute names measured at ON (25 at OFF)** across these four span shapes — full inventory and per-span breakdown below. (This cell read a bare "25 distinct" from M1.2 until M2.1 Task 3 fix round 1: the OFF-mode figure, in a cell whose own next clause states the default is ON. M1.2's SF-3 fix round corrected the inventory 165 lines below to 28 and never touched this summary — the fifth time in this project that fixing an instance missed a sibling, and the first with the sibling in the same file. Every attribute count in this document now carries its mode — there are **six** mode-carrying such sites (**eight** assignments in total) — and `tests/unit/test_telemetry_contract_attribute_counts.py` fails if any is unqualified, disagrees with the inventory it summarises, or has the ON and OFF figures the wrong way round. The unit matters, because two defensible numbers exist here and they differ: a *site* is a published count anchored on `gen_ai.*` or on the word "names", which is what a reader would point at and call a claim, and there are six of those, all six naming a mode; the guard additionally holds to the right mode any bare integer that merely equals one of the two figures, which is what makes the second half of "28 at ON, 25 at OFF" checkable rather than decorative, and counting those as well gives eight assignments. "Site" is the unit this sentence is about, so six is the figure it publishes, with eight stated beside it so the two cannot later be mistaken for each other. (This read "there are nine of them" from M2.1 Task 4 until Task 5's fix round measured it — **nine was wrong in both units**. It was never guarded, and because it is the sentence that tells a reader how much of this document the guard covers, it propagated from here into four separate task briefs before anyone checked it: the counts it describes were correct the whole time, and only the count *of* them drifted, precisely because the site total was the one number with nothing asserting it. `test_the_documents_own_count_of_its_attribute_count_sites_is_right` now derives both figures from the checker's own functions rather than from anything typed by hand.) That last check arrived in fix round 2: the guard's first version tested which numbers appeared in a sentence rather than which mode each was assigned to, so it would have passed "25 at ON, 28 at OFF".) `include_content` is **not** hardcoded — it reads `NORTHSTAR_CAPTURE_CONTENT` (default ON; see "Content capture" below) — and `include_model_request_parameters` is left at its library default (`True`), never passed explicitly. |
| agentgateway | native, `frontendPolicies.tracing` in its own config | `cr.agentgateway.dev/agentgateway:v1.5.0` (Resource `service.version=1.5.0`; scope `agentgateway` with **no** `otel.scope.version`; Resource `telemetry.sdk.language=rust`, `telemetry.sdk.name=opentelemetry`, `telemetry.sdk.version=0.32.1`) | Two spans per MCP message: a SERVER span named after the JSON-RPC method (`initialize`, `notifications/initialized`, `tools/call`) or, for non-JSON-RPC HTTP, after method-and-route (`GET /*`, `DELETE /*`); and a CLIENT span named `<method> <target>` (`initialize payments-mcp`), which for tool calls becomes `tools/call <target>_<tool>` (`tools/call payments-mcp_propose_payment`) | Interposed by M2.1 Task 3; **verifies a SPIRE-minted JWT-SVID as of Task 4** (`jwtAuth`, `mode: strict`); and, **as of M2.2, calls out to the Policy Decision Service on every `tools/call`** (`mcpGuardrails`, `kind: remote`, `failureMode: failClosed`) and refuses the call when the answer is deny. That third job is visible in the trace as one extra span per tool call, a CLIENT span named `Guardrail` — see "agentgateway's guardrail telemetry, as measured" below; it is agentgateway's own span and nothing here invented it. The verification is visible in the trace, not only in a 401: its SERVER spans gained one attribute, `jwt.sub`, carrying the verified token's subject — for a JWT-SVID, the caller's SPIFFE ID (measured `spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent`). The gateway derives it from the token it validated; nothing in this project sets it. Measured **absent** from the CLIENT spans, consistent with the gateway stripping the credential on the outbound leg (`if !self.preserve_token { self.location.remove(req)?; }`, `crates/agentgateway/src/http/jwt.rs`) — so payments-mcp receives no identity to check. Emits **a third HTTP attribute vocabulary**, neither stable nor OTel-legacy — see "agentgateway instrumentation, as measured" below, which is the one place this document's HTTP-convention claim has an exception. Exports OTLP/**gRPC** to the collector on 4317, the only component here that does not use OTLP/HTTP on 4318. Task 2 shipped it with no `tracing` block at all, i.e. emitting nothing; Task 3 added one, which is why this row did not exist before. |
| payments-mcp | FastMCP native | `fastmcp==4.0.3` | `tools/call propose_payment`, `tools/call execute_payment` | TracerProvider does **not** need to be installed before importing fastmcp on this version — `fastmcp.telemetry.get_tracer()` calls `get_tracer_provider()` fresh on every span, not once at import (measured; the brief's premise here was wrong for 4.0.3 — kept as a code convention anyway, not a correctness requirement). `FASTMCP_TELEMETRY_MODE=native` is set explicitly even though it is already the default. **`OTEL_SEMCONV_STABILITY_OPT_IN=http` is also set** (`deploy/k8s/payments-mcp.yaml`) — this governs only the *outbound httpx* span to payments-api (FastMCP's own native MCP spans are unaffected; this knob is httpx-instrumentation-specific), measured legacy-by-default, confirmed stable after (`tools/payments-mcp/NOTES.md` §3). |
| policy-decision-service | OTel Python SDK, applied by this project's own `pds/telemetry.py` | `opentelemetry-sdk==1.44.0` (Resource carries `service.name`, `service.instance.id`, `telemetry.sdk.language=python`, `telemetry.sdk.name=opentelemetry`, `telemetry.sdk.version=1.44.0` — and **no** `service.version`; scope `northstar.policy-decision-service` with no `otel.scope.version`) | one span, `policy.decision`, SERVER kind, one per `tools/call` the gateway sends for a decision | **The one component in this system whose span is entirely this project's own** — no framework emits a policy decision, which is what Gap 3 below was about. It opens the span by hand because there is nothing wrapping it that would: the gRPC servicer is the top of its own call stack. Everything below that name is the real SDK — `TracerProvider`/`BatchSpanProcessor`/OTLP-HTTP exporter, endpoint and protocol from the standard `OTEL_EXPORTER_OTLP_*` env vars, and a `CompositePropagator(TraceContextTextMapPropagator, W3CBaggagePropagator)` registered explicitly. It joins the caller's trace because agentgateway forwards `traceparent` to the guardrail hook (measured, M2.2 Task 1 run C), so the decision span is a genuine **child of the gateway's `tools/call` SERVER span** — a sibling of the `Guardrail` CLIENT span, not its child, because the gateway propagates the context it received rather than its own client span's. **The span status stays `Unset` on a deny, deliberately:** a refusal is this service working, and an ERROR span for the one thing this milestone exists to do would read as a fault. Attribute inventory below. |
| payments-api | `otelhttp` | `otelhttp v0.71.0`, otel core `v1.46.0` (**2026-09-11 (M3): was `otelhttp v0.66.0`, otel core `v1.41.0`.** The bump is what retired the reviewer-ui trace-flags workaround — see Finding 11 and the section below) | `POST /payments/propose`, `POST /payments/{paymentId}/execute`, `GET /payments/{paymentId}` | HTTP attribute spelling as emitted by the pinned version: **stable** — `http.request.method` / `http.response.status_code` / `http.route`, not the legacy `http.method`/`http.status_code`. `network.protocol.name` is **not** emitted by this otelhttp version (only `network.protocol.version` is); do not assert its presence. No `OTEL_SEMCONV_STABILITY_OPT_IN` is set or needed — this version emits the stable spelling by default (A12), unlike every Python service in this table. |
| reviewer-ui (M3 Task 4; corrected M3 final-review fix round) | OTel Python SDK, applied by this project's own `bootstrap.py`; `HTTPXClientInstrumentor` for outbound calls | `opentelemetry-sdk==1.44.0` | `review.decision`, SERVER kind, one per submitted approve/reject decision; `review.queue`, SERVER kind, one per `GET /` | **Two** owned spans, and no framework instrumentation: no `opentelemetry-instrumentation-fastapi`/`-starlette` is applied, the same way `policy-decision-service`'s own row above is "entirely this project's own". Both are hand-created (`review.py`, `main.py`) and each is the root of its own trace, matching apps/customer-ui's `claim.submit` pattern rather than PDS's (a child of the caller's propagated context). `GET /healthz` still produces nothing. `HTTPXClientInstrumentor` runs, so the outbound calls to payments-api and to senior-settlement-agent's A2A endpoint get CLIENT spans and carry `traceparent`. **This cell read "`review.decision`, the only span" and "`GET /` and `GET /healthz` produce no spans at all" from Task 4 until the final-review fix round (R36), and both halves were wrong — `GET /` always produced the outbound CLIENT span this same cell then went on to describe, and that span, having no parent, rooted a trace of its own.** **2026-09-11 (M3): this cell used to end "`bootstrap.py` additionally registers `_Level1CompatibleTraceContextPropagator`, which masks the injected trace-flags byte to `SAMPLED`".** It no longer does, and there is no longer anything to mask: payments-api moved to otel-go v1.46.0, whose extractor accepts a Level 2 `-03` flag byte, so `bootstrap.py` registers the stock `TraceContextTextMapPropagator` like every other service here and reviewer-ui's outbound `traceparent` goes out as `…-03`. Measured inside the deployed pod: `get_global_textmap()._propagators` is `['TraceContextTextMapPropagator', 'W3CBaggagePropagator']` and an injection from a real sampled span produced `00-4fd1495b2de7214c10469a745ba986aa-cdb290f086f73d06-03`. The removal is under "reviewer-ui's own instrumentation" below. Attribute inventory below. |

**Operational fact that keeps *this project's own services* on one HTTP
convention — and, as of M2.1 Task 3, the one component it cannot reach:**
`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set on all three Python services
(claims-agent, settlement-agent, payments-mcp) and on none of the others.
Without it, OTel Python's HTTP instrumentation defaults to the legacy
attribute spelling (`http.method`/`http.url`/`http.status_code`) while
payments-api's Go `otelhttp v0.71.0` (v0.66.0 until 2026-09-11) emits the stable spelling by default
with no opt-in available to change that — measured, not assumed, for each
service independently (`agents/claims-agent/NOTES.md` §7,
`agents/settlement-agent/NOTES.md` §6, `tools/payments-mcp/NOTES.md` §3),
each showing the legacy capture before the opt-in and the stable capture
after. This single non-default env var is the only reason this project's
five own services agree on one HTTP semantic convention instead of silently
splitting across languages — exactly the class of breakage A13 exists to
catch. Any service added later without it would reintroduce a mixed-convention
chain invisibly, since both spellings are individually well-formed telemetry.

**M2.1 Task 3 correction: the chain is no longer all one HTTP convention, and
this project has no lever that would make it so.** Until the gateway was
interposed, "five services agree" described the whole trace. It now describes
the five services this project *builds*. agentgateway is a sixth participant
this project only configures, and **in the pinned `v1.5.0`, and only there,** it
emits `http.method` / `http.path` /
`http.status` / `http.host` / `http.version` — measured off live spans, not
inferred — which is neither the stable spelling the other five converge on nor
OTel's legacy spelling (that one is `http.status_code` / `http.url`). The
version qualifier is load-bearing, not pedantry: upstream PR #3182 is already
merged and moves the access log — and these span attributes with it — onto
stable semconv behind an opt-in, so this sentence becomes **false** at the next
image bump rather than merely stale. There is
no `OTEL_SEMCONV_STABILITY_OPT_IN` equivalent to set: those are raw string keys
in its own Rust source, not routed through a semconv layer that could be
switched. So this is stated as a measured property of the deployed system
rather than fixed, and it is pinned by
`tests/e2e/test_gateway_trace.py::test_gateway_span_attributes_are_exactly_what_was_measured`
so the day it changes upstream, a test says so. Full detail in the section
below.

**OTel library version skew, flagged as a decision, not an accident (N2/N3,
final-review.md):** the table above pins each service's own OTel version
independently, and they do not agree. On the Python side, payments-mcp runs
`opentelemetry-sdk==1.29.0`/`opentelemetry-instrumentation-httpx==0.50b0`
while both agents run `1.44.0`/`0.65b0`, and the repo-root `tests/`
harness pins `1.29.0` — three Python processes on two OTel generations, in a
milestone whose whole product is the telemetry these libraries emit.
(**2026-09-11 (M3): the Python half of this skew is closed.** `payments-mcp`
and the repo-root `tests/` harness are both on `1.44.0`/`0.65b0` now, so every
Python process in this repo runs one OTel generation — the current PyPI
releases, published 2026-07-16. The paragraph's closing argument, that
harmonizing the pins "carries real regression risk for zero measured
benefit", was answered rather than ignored: it **did** carry a measured
benefit, since `payments-mcp`'s 1.29.0 turned out to be load-bearing by
accident (Finding 11), and the re-verification discipline it asks for was
performed — every affected service rebuilt and redeployed, every suite re-run,
and live traces re-captured. The browser-side skew is untouched and still
stands as written.) On the
browser side, `@opentelemetry/context-zone` sits at `1.30.0` alongside
`sdk-trace-web`/`resources` at `2.8.0`, and the top-level
`@opentelemetry/semantic-conventions` pin (`1.28.0`) differs from what
transitives resolve (`1.43.0`). Neither skew is a live defect today: A13
requires measuring each service's own emitted spelling independently rather
than assuming one service's pin carries over, which is exactly why the skew
hasn't silently broken anything; and the lockfile carries exactly one
`@opentelemetry/core` (`2.8.0`, confirmed by `package-lock.json`), so no
vulnerable 1.x `core` rides along the browser's `context-zone` pin (A21a
holds). Left unreconciled deliberately in this fix round rather than bumped:
harmonizing three services' dependency pins this late, with no A21-style
published-CVE forcing function, carries real regression risk for zero
measured benefit, and would need the same re-verification discipline A21/A21a
already require for a version bump (rebuild every affected service, re-run
the full suite, re-capture a live trace to confirm span shape is unchanged).
Recorded here so M1.2's planner inherits an explicit, considered choice
rather than rediscovering the skew as an accident.

**Architectural fact, as it stood through M1.1 and M1.2, and with M2.2's one
deliberate exception stated immediately after it:** none of the components
above creates a span of its own. Every span in this milestone comes from
library instrumentation — `StarletteInstrumentor`, `a2a-sdk`'s own `@trace_class`/
`@trace_function` decorators, `HTTPXClientInstrumentor`, FastMCP's native
tracer, `otelhttp`, and customer-ui's one hand-written `claim.submit` span
(the sole exception — it is the root, and nothing upstream of the browser
could have created it instead). Besides that one exception's own two files
(`apps/customer-ui/src/telemetry.ts`, `apps/customer-ui/src/app/page.tsx`)
and one docstring mention in `tools/payments-mcp/bootstrap.py`, there is no
`get_tracer`/`start_span`/`start_as_current_span` anywhere in this project's
own `agents/`, `tools/`, or `systems/` source; the only other hits anywhere in
the repo are both test files — a harness test probe
(`tests/e2e/test_harness.py`) and customer-ui's own regression test for the
A19 traceparent-across-await fix (`apps/customer-ui/src/a2a.test.ts`, which
opens a real span to drive the same code path `page.tsx` uses — N8,
final-review.md, an earlier version of this sentence named only the harness
probe). This predicts where M1.2 changes shape: PydanticAI is expected to be
the first library that gives this project's own code spans to open directly,
rather than only ever wrapping calls from the outside.

**M2.2 adds the second hand-written span in this project, and the reason is
the same shape as the browser's.** `platform/policy-decision-service/pds/`
calls `start_as_current_span("policy.decision")` directly, because — exactly
like customer-ui's root — nothing else could have created it. The PDS is a
gRPC servicer at the top of its own call stack, and no instrumentation
library anywhere emits "a policy decided this", which is the substance of
Gap 3 below. The grep claim in the paragraph above is unchanged and was
re-run for M2.2: there is still no `get_tracer`/`start_span`/
`start_as_current_span` in `agents/`, `tools/` or `systems/`. The new site is
in `platform/`, a directory that did not exist when that sentence was
written, and it is named here rather than allowed to make the sentence
quietly false by living outside the three directories it enumerates.

That prediction held, and this is where M1.2 changes it: PydanticAI's own
`invoke_agent`/`chat`/`execute_tool` spans are the first spans in this project
opened by a library specifically to represent *our own agent's reasoning*,
not a network call our code happened to make.

## Export destinations: Jaeger, OpenSearch and Dash0, and they are not symmetric

This has been live and correct since before M1.2 started, and was previously
undocumented — recorded here rather than changed, because nothing about it
needed changing. `scripts/setup-kind.sh`'s "Step 7/9: Installing observability
backbone" switches the collector's own config on whether `DASH0_AUTH_TOKEN` is
present (this used to cite "around line 159" as well; M2.1 Task 1 inserted the
Cilium install above it and moved that line, so the fragile line number is
dropped in favour of the step label, which is stable):

```bash
if [[ -n "${DASH0_AUTH_TOKEN:-}" ]]; then
  COLLECTOR_CONFIG="$ROOT/deploy/observability/collector-config-dash0.yaml"
  # ...creates dash0-secrets, then the collector loads this config...
else
  # exports to Jaeger only — the vendor-neutral default
fi
```

Read directly from `deploy/observability/collector-config-dash0.yaml`'s own
pipeline definitions, re-read after M4 Task 1 changed them — **the pipelines
are not symmetric**:

| Pipeline | Exporters |
|---|---|
| `traces` | `otlp_grpc/jaeger`, `forward/opensearch`, `otlp_grpc/dash0`, `debug` |
| `traces/opensearch` (stage 4 only) | `opensearch/trace` |
| `metrics` | `otlp_http/prometheus` (stage 4 only), `otlp_grpc/dash0`, `debug` |
| `logs` | `opensearch/logs` (stage 4 only), `otlp_grpc/dash0`, `debug` |

**Traces go to Jaeger, OpenSearch and Dash0; metrics go to Prometheus and
Dash0; logs go to OpenSearch and Dash0.** Jaeger is a traces-only backend by design, so
it was never a candidate for the other two signals — this is not an oversight,
it is what "traces to a vendor-neutral OSS backend, everything else to whichever
vendor is configured" looks like when the OSS backend genuinely can't carry
metrics or logs. Prometheus and OpenSearch are the vendor-neutral OSS backends
for the two signals Jaeger cannot take — Prometheus for metrics (added at M4
Task 2), OpenSearch for logs (added at M5 Task 4, alongside the trace branch
M4 Task 1 already gave it) — both stage-4 only; below stage 4 the `metrics`
row is `otlp_grpc/dash0`, `debug` again and the `logs` row is likewise
`otlp_grpc/dash0`, `debug` alone, because `scripts/setup-kind.sh` strips the
stage-4-only region and a collector must not name a backend its cluster does
not have (ruling M4-R8).

**The metrics row read `otlp_grpc/dash0`, `debug` and this paragraph read
"metrics and logs go to Dash0 only" until M4 Task 2**, which is the second time
this exact table has been falsified by a change to the file it claims to be read
from — review finding F4 recorded the first, one task earlier. The lesson stands
where it was written: "read directly from" is a provenance claim, and it decays
the moment the file it names changes. **Dash0 is still strictly richer than the
on-stage Jaeger view**: anyone looking at Dash0 sees the same traces plus
metrics and logs that never reach Jaeger at all.

**2026-09-15, whole-branch review C1: the `logs` row read `otlp_grpc/dash0`,
`debug` and this section concluded "logs go to Dash0 only" until this fix
wave**, which is the **third** time this exact table has been falsified by a
change to the file it claims to be read from — the two paragraphs above
record the first and second. M5 Task 4 added `opensearch/logs` to the logs
pipeline in both collector configs
(`deploy/observability/collector-config-dash0.yaml:203-213` and
`deploy/observability/collector-config.yaml`'s equivalent pipeline), and the
table went unfixed for the rest of the milestone: this document's own
`:228-230` (also Task 4's) already said the record reaches OpenSearch, and
`README.md` agreed, so the table contradicted the rest of its own file the
whole time. Per the standing rule, correcting the row a third time is not
enough — **the guard below derives this table from the two collector configs
themselves**, so a fourth recurrence reddens a test instead of waiting for a
fourth review to find it by reading.

**This table said "Traces: `otlp_grpc/jaeger`, `otlp_grpc/dash0`, `debug`" and
concluded "Traces go to both Jaeger and Dash0" until M4 Task 1's fix round.**
That was true until M4 Task 1 added the OpenSearch branch to this very file,
in the same commit that edited this document for the test counts — review
finding F4, and the reason it is worth recording rather than silently
correcting: "read directly from" is a provenance claim, and it decays the
moment the file it names changes.

**Three things about the OpenSearch branch a reader needs, none of them
symmetric with the others:**

* It is **stage 4 only**. `scripts/setup-kind.sh` strips every region of both
  collector configs bracketed by `# BEGIN stage-4-only` / `# END
  stage-4-only` when `--stage` is below 4, because OpenSearch is installed
  only at stage 4 and a collector exporting to a backend that does not exist
  fails permanently and silently (review finding F10, ruling M4-R8).
* It is a **tee, not a fourth backend on the main pipeline**. The table above
  does list `forward/opensearch` among the `traces` pipeline's exporters, and
  that is not a contradiction: a connector is *declared* in an exporter list
  and is not a destination. It hands the main `traces` pipeline's output to
  the second pipeline, which is what stops the OpenSearch branch drifting from
  what Jaeger and Dash0 receive. (This bullet read "not a fourth *exporter*"
  until M4 Task 1's second fix round, which contradicted the table thirty
  lines above it — re-review finding N8.)
* It is the **only** place in this system where an attribute is renamed in
  flight. `traces/opensearch` runs `attributes/opensearch-compat`, which
  rewrites `policy.decision.retryable` to `policy.decision_retryable` **for
  OpenSearch only**. OpenSearch expands dotted keys into nested objects at
  index time, so a document carrying both `policy.decision` (a string) and
  `policy.decision.retryable` (a bool) would need `attributes.policy.decision`
  to be a scalar and an object at once; it answers `can't merge a non object
  mapping [attributes.policy.decision] with an object mapping` and rejects the
  document permanently. Jaeger and Dash0 export upstream of the tee and keep
  the canonical name this document defines. See "The policy decision, as
  measured (M2.2) — Gap 3, closed" for the attribute itself, and
  `tests/e2e/test_opensearch_trace_ingest.py` for the guard that asserts both
  halves against the live cluster.

Verified live, not inferred, 2026-09-08: `dash0-secrets` present with all
four keys; the collector pod carries all four `DASH0_*` env vars; the
collector pod itself **0 restarts**, running 5h31m at measurement time; zero
export failures and zero dropped spans across 4,000 collector log lines. All
five services appear in the Dash0 dataset with the dependency chain
correctly inferred (`customer-ui` → `claims-agent` → `settlement-agent` →
`payments-mcp` → `payments-api`), `customer-ui` classified as RUM/browser,
and the M1.2 model spans (`invoke_agent settlement_agent`,
`chat claude-sonnet-5` ×3, both `execute_tool` spans) arriving with full
token-usage attributes intact. The ~1.9% error rate briefly visible on
claims-agent and settlement-agent in that same window is the Task 4
reviewer's own deliberate severance experiments (payments-mcp scaled to
zero, the MCP URL mis-targeted) landing in the same Dash0 dataset, not a
production defect — recorded here so a future reader does not go looking for
a bug that was a review technique. **This paragraph is transcribed from this
project's own progress ledger's 2026-09-08 entry ("Dash0 export — already
live, verified rather than assumed"), not re-measured for this revision** —
the ledger (`.superpowers/sdd/2026-09-08-m1.2-intelligence/progress.md`) is
git-ignored working history, not a file distributed with this repository, so
cite it as the dated ledger entry it is rather than a path a reader can
necessarily open.

This matters beyond this milestone: M4 ("Observable & evaluable") needs
somewhere for metrics and eval scores to live, and this dual-export path is
already that home — it does not need to be built later, only pointed at. The
on-stage narrative stays vendor-neutral regardless of this: Jaeger is what
the talk shows, because it is what anyone can stand up locally with no
account; Dash0's presence is additive, opt-in via one env var
(`DASH0_AUTH_TOKEN`), and never a dependency of the demo working at all.

## PydanticAI instrumentation, as measured (M1.2)

`agents/settlement-agent/bootstrap.py` applies
`Agent.instrument_all(InstrumentationSettings(include_content=capture_content_enabled(), version=5))`.
`version` is pinned at the library default, `5`; `include_model_request_parameters`
is never passed and stays at its library default (`True`) — see "Content
capture" below for exactly what each of those two independent settings gates.
The emitting library is `pydantic-ai-slim==2.42.0`
(`agents/settlement-agent/requirements.txt`) — `2.41.0` until 2026-09-11.

**The 2.41.0 → 2.42.0 bump changed nothing in this section, and that is a
measurement, not an assumption.** The published inventory below is the reason
this bump got its own step: if the emitted attribute set had moved, this
document would have been wrong the moment the pod restarted. It was re-measured
either side of the bump against the same scenario (a refused €6,200 settlement,
content capture ON) by reading every `pydantic-ai`-scoped span's whole attribute
set out of Jaeger and diffing: trace `e086b1efd1ae4fba819455c5d9f5fa75` at
2.41.0 against trace `e5e160bd3e3d4189a951419b52df8bc7` at 2.42.0. The two
differ in exactly one key across the whole comparison — `otel.scope.version`,
`2.41.0` → `2.42.0`. Same 28 `gen_ai.*` names, same four span shapes, same
per-span attribute sets, same counts per request. Upstream's own v2.42.0 release
notes list one compatibility note, one feature and four bug fixes, none of them
touching instrumentation — but the diff above is what this section rests on, not
that list.

**Fix round (final-review.md SF-3): this inventory previously published the
OFF-mode figure as though it were the whole vocabulary — "25 names...
complete" — and said "two further attributes are content-gated."** Both
halves were wrong. The 25 were measured under Task 4's `include_content=False`
default; Task 5 flipped the shipped default to ON and nobody re-derived the
set, so the document contradicted itself 30-odd lines later (`gen_ai.system_
instructions`'s own gating section, below) without anyone reconciling the two.

**The complete `gen_ai.*` attribute vocabulary at the shipped default
(`NORTHSTAR_CAPTURE_CONTENT` ON) — 28 names, re-measured for this fix round
off two live traces in Jaeger, identical in both:**
`21ca441e8fec4a8f89bb8ec51cc6e1ca` (2026-09-08 16:03 UTC, direct-A2A
scenario) and `18372fc00318f9ca75d4ae3e58bf9db7` (2026-09-08 15:34 UTC,
browser-rooted scenario) — every name below was read off those two spans
sets, none predicted:

```
gen_ai.agent.call.id
gen_ai.agent.name
gen_ai.aggregated_usage.details.cache_creation_input_tokens
gen_ai.aggregated_usage.details.cache_read_input_tokens
gen_ai.aggregated_usage.details.thinking_tokens
gen_ai.aggregated_usage.input_tokens
gen_ai.aggregated_usage.output_tokens
gen_ai.conversation.id
gen_ai.input.messages
gen_ai.operation.name
gen_ai.output.messages
gen_ai.provider.name
gen_ai.request.model
gen_ai.response.finish_reasons
gen_ai.response.id
gen_ai.response.model
gen_ai.system
gen_ai.system_instructions
gen_ai.tool.call.arguments
gen_ai.tool.call.id
gen_ai.tool.call.result
gen_ai.tool.definitions
gen_ai.tool.name
gen_ai.usage.details.cache_creation_input_tokens
gen_ai.usage.details.cache_read_input_tokens
gen_ai.usage.details.thinking_tokens
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
```

**Three of these are content-gated** and disappear when
`NORTHSTAR_CAPTURE_CONTENT` is OFF — `gen_ai.tool.call.arguments` and
`gen_ai.tool.call.result` on the `execute_tool` spans, and
`gen_ai.system_instructions` on the `chat`/`invoke_agent` spans (see its own
gating section below for the source). With those three removed, the
remaining 25 are exactly Task 4's original OFF-mode measurement — that
number was never wrong, only mislabeled as "complete" when it was one mode's
subset.

**Four span shapes carry `gen_ai.*`**, emitted by scope `pydantic-ai` 2.42.0
(settlement-agent) and scope `fastmcp` (payments-mcp, pre-existing from
M1.1, unaffected by any of this):

| Span | Kind | Count/request | Carries |
|---|---|---|---|
| `invoke_agent settlement_agent` | internal | 1 | `gen_ai.agent.call.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `gen_ai.operation.name=invoke_agent`, the five `gen_ai.aggregated_usage.*` roll-ups. Also the non-`gen_ai` `agent_name`, `model_name`, `logfire.msg`, `logfire.json_schema`, and `pydantic_ai.all_messages` (the full conversation, redacted to shape only unless capture is ON). |
| `chat claude-sonnet-5` | client | 3 (one per model turn: propose, execute, final narrative) | `gen_ai.agent.call.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `gen_ai.operation.name=chat`, `gen_ai.provider.name`/`gen_ai.system` (both `anthropic` — `gen_ai.system` is the deprecated spelling; `version=5` emits both), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.tool.definitions`, the five `gen_ai.usage.*` fields (`details.thinking_tokens` only on turns where the model actually thought — deliberately not asserted, since that is the model's behaviour, not our wiring). Also `server.address=api.anthropic.com`, `operation.cost` (e.g. `0.002894`), and `model_request_parameters` (see "Content capture"). **As of M1.2 Task 8**, each of these three spans has exactly one httpx-instrumented CLIENT child (`otel.scope.name=opentelemetry.instrumentation.httpx`, `POST`, stable HTTP semconv — see Gap 7 below), the model's own wire-level hop. |
| `execute_tool propose_payment` / `execute_tool execute_payment` | internal | 1 each | `gen_ai.agent.call.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `gen_ai.operation.name=execute_tool`, `gen_ai.tool.call.id`, `gen_ai.tool.name`, plus `gen_ai.tool.call.arguments`/`gen_ai.tool.call.result` when capture is ON. |
| `tools/call propose_payment` / `tools/call execute_payment` (payments-mcp) | server | 1 each | `gen_ai.tool.name` only — pre-existing FastMCP behaviour from M1.1 (Gap 5 below covers what's still missing from it). |

Two things worth recording because the governing rule is to write down what
was measured, not what was expected: `gen_ai.aggregated_usage.*` is a
pydantic-ai extension with no counterpart in the GenAI semantic conventions
(`open-telemetry/semantic-conventions-genai` — pydantic-ai's own source agrees,
calling it "a custom namespace, not part of the OpenTelemetry Semantic
Conventions"), carrying per-run token roll-ups on the `invoke_agent` span; and
`gen_ai.tool.definitions` is **not** redacted by `include_content=False` —
see "Content capture" for what it carries and why that means neither
`include_content` nor `include_model_request_parameters` is, on its own, a
complete redaction switch.

**For M3:** `version=5` (the default, and what this project pins) stops
`CallDeferred`/`ApprovalRequired` from marking their span `ERROR` — treated
as control flow instead of a failure, per the installed library's own
`version` docstring (verified against `pydantic_ai/_instrumentation.py`;
cross-checked in this project's own working notes, dated 2026-09-08
("m1.2-pydanticai-verification") — git-ignored working history, not a file
distributed with this repository, so cite it as the dated note it is rather
than a path a reader can necessarily open).
This matters directly to the human-approval milestone: an approval step that
pauses execution should not read as a span-level error in this project's
trace, and pinning `version=5` (rather than a later default drifting under
us) already buys that for free, with no code of our own needed. `version=6`
would additionally emit tool results as `role='tool'` messages instead of
`role='user'` — opt-in, not adopted here, and not needed for M3's purposes.

**M2.1 Task 3: the shape below now has a proxy hop in the middle of it.**
`payments-mcp`'s `tools/call <tool>` SERVER span is no longer a direct child of
settlement-agent's `execute_tool <tool>` span. The measured chain is:

```
settlement-agent  execute_tool propose_payment            internal
  settlement-agent  MCP send tools/call propose_payment   client   (mcp 2.2.0's own span)
    settlement-agent  POST                                client   (httpx2, to agentgateway)
      agentgateway    tools/call                           server
        agentgateway  tools/call payments-mcp_propose_payment client
          payments-mcp  tools/call propose_payment          server
            payments-mcp  POST                              client
              payments-api  POST /payments/propose          server
```

**2026-09-11: one level was added to this chain by a dependency bump, not by
this project.** The block above had seven rows, `execute_tool` straight to the
httpx `POST`, until `mcp` went 1.28.1 → 2.2.0. 2.x ships OpenTelemetry on by
default and opens its own `MCP send tools/call <tool>` CLIENT span around each
request, so the httpx span is now that span's child — and it is an `httpx2`
span, because 2.x's transport is built on `httpx2`. Re-measured off trace
`e086b1efd1ae4fba819455c5d9f5fa75`, not edited to match a changelog. The two
e2e assertions described immediately below are unaffected: both are ancestry
checks, not parent-of checks.

One trace id throughout, every span parented on the one directly above it,
`agentgateway` added as a **sixth service**. Two pre-existing e2e assertions
were made true again rather than loosened for this:
`test_settlement_agent_trace.py`'s outbound-target needle now names the
gateway, and `test_settlement_llm_trace.py::test_mcp_hop_still_carries_the_trace`
moved from "direct child of `execute_tool`" to "descends from `execute_tool`
**and** its immediate parent is an agentgateway span" — ancestry plus a new
constraint, so net strictness is unchanged. The rest of this section's shape
claims are unaffected: still one root through the browser path, still zero
orphans beyond A17's known synthetic one, and still no published span total.

**The trace's shape, with a model in the path.** This paragraph's original
figures (M1.2, verified independently twice, by the implementer and by the
reviewer, on two different live runs — task-4-report.md §7 and
task-4-review.md §7 **of M1.2's own working history**, not M2.1's) were **5
services** (`customer-ui`, `claims-agent`, `settlement-agent`, `payments-mcp`,
`payments-api`), before the gateway existed. **M2.1 correction, re-derived
live rather than carried forward:** querying Jaeger directly
(`GET /api/traces?service=customer-ui&operation=claim.submit&lookback=48h`)
returns 4 browser-rooted traces; the 3 post-reroute ones each show **6
services** (`agentgateway` interposed between `settlement-agent` and
`payments-mcp`, matching the "sixth service" correction above), **1 root**
(`customer-ui:claim.submit`, matching the span id observed in the browser's
own `traceparent` header), **0 orphans**, and 20 `agentgateway` spans; the one
remaining pre-reroute trace still shows the original 5-service, 0-gateway-span
shape. Both shapes contain `invoke_agent settlement_agent` ×1,
`chat claude-sonnet-5` ×3, both `execute_tool` spans, both `tools/call` spans,
and payments-api's propose (`201`) and execute (`200`). **No exact span count
is published here, deliberately:** the M1.2 implementer measured 88 spans on
the browser exit-criterion run and the M1.2 reviewer measured 86 on an
identical scenario; re-derived for M2.1 across the three post-reroute traces
above, the whole-trace totals were 111, 117 and 113. Same shape every time,
different total — real per-run variance (whether a given turn produced a
`thinking` part, MCP session bookkeeping, the `a2a-sdk` polling span), not a
defect, and not a number this document commits to. The shape above — which
services, which operations, one root, zero orphans — is the property this
project actually tests and guarantees; a specific span total is not, and
publishing one here would only become the
next stale number this contract has already had to correct twice.

**M2.2 correction: `policy-decision-service` is a seventh service in this
trace, and it is in the denied trace as well as the permitted one.** Measured
by querying Jaeger for browser-rooted traces directly
(`GET /api/traces?service=customer-ui&operation=claim.submit&lookback=48h`)
and reading each one's service set, roots, orphans and `policy.decision`
attributes — four such traces carry all seven services: the denials
`6eed5ffa79e3eb758b4367eb75cb5a0d` and `4ccb95666e75dafc0700639baf37ead0`
(€6,200, `policy.decision=deny` on `execute_payment`) and the permits
`3b78201273e80fed79ff01264892ca78` and `59850646e3c7d3d35b74e5072ccc23c0`
(€5,800, permitted and executed). All four show **1 root**
(`customer-ui:claim.submit`) and **0 orphans**. The shape claim, and still
only the shape:

- **`policy.decision` ×2 per settlement**, one per tool call, each a child of
  the gateway's own `tools/call` SERVER span for that call. (**N5,
  whole-branch review: this is the M2.2-era count, from before M3's
  `refuse_payment` call existed** — it is pre-`refuse_payment`, describing the
  two-call `propose_payment`/`execute_payment` shape only. See "So **two or
  three `policy.decision` spans appear per settlement**" further below for
  the current figure, which accounts for `refuse_payment`'s third call.)
- **`Guardrail` ×2 per settlement**, agentgateway's own CLIENT span for the
  policy hop, sibling to the `policy.decision` span under the same parent.
- **`payments-api` is in the denied trace too**, with the propose (`201`) and
  **no** execute. That is the precise sense in which "no payment is executed"
  is a claim about the execute leg: the proposal really is recorded, and only
  the execution is refused.
- One root, zero orphans, one trace id throughout, exactly as before.

**Still no whole-trace span total, and M2.2 hardens the reason rather than
softening it.** Across the four browser-rooted traces above the totals are
125, 123, 119 and 117 — same shape every time. The two direct-A2A reference
traces hold 67 (denied) and 92 (permitted), and Task 7's own script-driven
permit run measured 116. Seven numbers, one shape. Two independent sources of
variance are now stacked: the `a2a-sdk` `dequeue_event` polling span
(measured 10–16) that this document has always declined to publish a total
against, and — new in M2.2 — a denial short-circuiting the second tool call's
entire subtree, so a denied run is genuinely smaller than a permitted one by a
margin that itself varies. Publishing a total here would be publishing a
number that changes with the verdict.

## The `mcp` client's own telemetry, as measured (M3 remaining-bumps, step 1)

**New as of 2026-09-11, and not authored here.** `mcp` 1.28.1 shipped no
OpenTelemetry code at all — `grep -rl opentelemetry` over the installed 1.28.1
package tree returns nothing. `mcp` 2.2.0 ships three such modules
(`mcp/shared/_otel.py`, `mcp/server/_otel.py`,
`mcp/shared/jsonrpc_dispatcher.py`), and upstream's v2.0.0 release notes say so
outright: "OpenTelemetry tracing ships on by default". So settlement-agent
gained spans it does not create, from a library bump, which is exactly the
class of change this document exists to record rather than discover later.

Measured off a live refused settlement (trace
`e086b1efd1ae4fba819455c5d9f5fa75`, 145 spans, six services, one orphan and
that orphan the test's own synthetic root):

| Span name | Kind | Count per settlement | Attributes, verbatim |
|---|---|---|---|
| `MCP send initialize` | CLIENT | 3 (one per `_call`, because this client opens a fresh session per tool call) | `mcp.method.name=initialize`, `jsonrpc.request.id=1` |
| `MCP send tools/call <tool>` | CLIENT | 3 (`propose_payment`, `execute_payment`, `refuse_payment`) | `mcp.method.name=tools/call`, `jsonrpc.request.id=2` |

Both carry `otel.scope.name=mcp-python-sdk` with **no** `otel.scope.version`,
and both carry `northstar.claim.id` — that last one is *this project's*
`ClaimIdSpanProcessor` stamping every span in the process, not the SDK.

Where they sit: `execute_tool <tool>` (PydanticAI) → **`MCP send tools/call
<tool>`** → `POST` (the httpx2 CLIENT span) → agentgateway. They are a new
level in the chain, not a replacement for one, and the hop still survives them
— the parent chains further down this section were re-measured after the bump.

**Two convention gaps, recorded as they appear rather than as they should be**
(same rule as the agentgateway section below):

- The span name is `MCP send {method} {target}`, not the OTel MCP semantic
  conventions' `{mcp.method.name} {target}` that payments-mcp's own server-side
  spans follow (`tools/call execute_payment`). The `MCP send ` prefix is a
  literal in `jsonrpc_dispatcher.py`.
- No `gen_ai.tool.name`. The tool name appears only inside the span *name*,
  so it is not queryable as an attribute on the client side — where
  payments-mcp's server span does carry it. The attribute set is the two keys
  in the table and nothing else.

**And one behaviour that is not a gap but changes what this repo's own code is
for.** `mcp` 2.2.0's dispatcher injects W3C trace context into `params._meta`
itself, immediately after opening the span above:

```python
span_name = f"MCP send {method}{f' {target}' if isinstance(target, str) else ''}"
with otel_span(span_name, kind=SpanKind.CLIENT, attributes={...}):
    # SEP-414: inject W3C trace context; `_meta` stays on the wire even with a no-op tracer.
    inject_trace_context(out_meta)
```

`agents/settlement-agent/mcp_tools.py` has hand-injected exactly that since
M1.1 Task 7, because at the time nothing else would. It still does, and the
SDK's own injection now overwrites it with a strictly deeper parent (the `MCP
send` span) in the same trace — so the wire is correct either way and nothing
was broken by leaving it. **It is redundant, and that is a finding, not a
silent deletion:** see Gap 6 below, and `docs/upstream-findings.md` Finding 4.
Removing it is its own change with its own proof, deliberately not folded into
a dependency bump.

## agentgateway instrumentation, as measured (M2.1 Task 3)

`cr.agentgateway.dev/agentgateway:v1.6.0` (bumped from v1.5.0 in M8 Task 0),
standalone (ConfigMap + Deployment +
Service, not the Helm chart), in `northstar-platform`, with
`deploy/platform/agentgateway/config.yaml`'s `frontendPolicies.tracing`
exporting OTLP/gRPC to `otel-collector.observability.svc.cluster.local:4317`.
`settlement-agent`'s `PAYMENTS_MCP_URL` names it, so every MCP call the model
makes crosses it.

**Every name in this section was read off a live span in Jaeger and dumped in
full before being written down. None was predicted from agentgateway's source
or its documentation.** agentgateway is not this project's component, so the
no-invented-telemetry rule applies to it without exception. In-tree evidence:
`tests/e2e/test_gateway_trace.py`, which asserts exact set equality on the
attribute keys below in both directions — a key appearing is a failure as much
as a key disappearing.

**Re-measured on v1.6.0, 2026-10-13.** v1.6.0 moved the gateway's SERVER spans
to the stable HTTP semantic conventions (upstream PR
[`#3182`](https://github.com/agentgateway/agentgateway/pull/3182)). Six
attribute keys left and six arrived; the tables below are the v1.6.0 dumps, and
`tests/e2e/test_gateway_trace.py` pins them. The span shape, the CLIENT spans and
the Guardrail span are unchanged, and the Guardrail span still carries the
legacy `http.host`, `http.method` and `http.path` (see its own section).
Anything below that names `http.status`, `http.method`, `http.path`,
`http.host`, `http.version` or `src.addr` on a SERVER span was measured on
v1.5.0 and says so.

### Span shape

Two spans per MCP message — one for the request received, one for the request
made upstream:

| Span | Kind | Parent | Count/request |
|---|---|---|---|
| `initialize` | server | settlement-agent's httpx `POST` CLIENT span | 2 (one MCP session per tool call) |
| `initialize payments-mcp` | client | agentgateway `initialize` | 2 |
| `notifications/initialized` | server | settlement-agent's httpx `POST` | 2 |
| `notifications/initialized payments-mcp` | client | agentgateway `notifications/initialized` | 2 |
| `GET /*` | server | settlement-agent's httpx `GET` | 2 |
| `GET payments-mcp` | client | agentgateway `GET /*` | 2 |
| `tools/call` | server | settlement-agent's httpx `POST` | 2 (one per tool the model called) |
| `tools/call payments-mcp_<tool>` | client | agentgateway `tools/call` | 1 each for `propose_payment`, `execute_payment` |
| `DELETE /*` | server | settlement-agent's httpx `DELETE` | 2 |
| `DELETE payments-mcp` | client | agentgateway `DELETE /*` | 2 |

Three things in that table are worth stating outright because they are easy to
get wrong from the outside:

- **The SERVER span for a tool call is named just `tools/call`** — the JSON-RPC
  method, with no tool name in it. An operator filtering Jaeger by operation
  name therefore sees two identically-named `tools/call` spans per settlement
  and must read `gen_ai.tool.name` to tell `propose_payment` from
  `execute_payment`. Only the CLIENT span carries the tool in its name.
- **`payments-mcp_<tool>` is agentgateway's own multiplexing prefix**
  (`<target>_<tool>`), not a name payments-mcp would recognise. payments-mcp's
  own span for the same call is `tools/call propose_payment`.
- **`GET /*` and `DELETE /*` fall back to method-and-route naming** because
  those two HTTP verbs of the streamable-HTTP transport carry no JSON-RPC
  method to name the span after; `/*` is the configured route's match pattern.

### Attributes on the `tools/call` SERVER span

Read off a live span on v1.6.0; 22 keys, the complete set, nothing omitted
(Jaeger's own `span.kind` and `internal.span.format` excluded, as below):

```
client.address               e.g. "10.244.0.15"
duration                     e.g. "3ms"  (a string; see the note below)
gateway                      "default/default"
gen_ai.tool.name             "propose_payment" | "execute_payment"
http.request.method          "POST"
http.response.status_code    200
jwt.sub                      "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
listener                     "listener0"
mcp.method.name              "tools/call"
mcp.resource.type            "tool"
mcp.session.id               (base64 JSON, see the note below)
mcp.target                   "payments-mcp"
network.protocol.version     "1.1"
otel.scope.name              "agentgateway"
protocol                     "mcp"
route                        "default/route0"
server.address               "agentgateway.northstar-platform.svc.cluster.local"
server.port                  3000
span.id                      the span's own id, repeated as an attribute
trace.id                     the trace's own id, repeated as an attribute
url.path                     "/mcp"
url.scheme                   "http"
```

The v1.5.0 set had `http.host`, `http.method`, `http.path`, `http.status`,
`http.version` and `src.addr` where this one has the six stable keys. `src.addr`
was `ip:port`; `client.address` is the bare IP.

**`jwt.sub` is the newest of those and the only one M2.1 Task 4 added** — this
block held one fewer key when Task 3 measured it, and the key appeared because
Task 4 gave the route a `jwtAuth` policy, not because the gateway was upgraded.
It carries the `sub` claim of the token the gateway verified, which for a
SPIRE-minted JWT-SVID is the caller's SPIFFE ID. Two properties of it are
measured rather than assumed, and both are asserted in
`tests/e2e/test_gateway_trace.py`: the value is the **caller's** identity
(`spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent`) and not
the audience the token was minted for, and the key is **absent from the CLIENT
span** below — the gateway strips the credential before making the upstream
call, so on the outbound leg there is no verified token to attribute anything
to. The practical consequence, recorded plainly because it is a real limit of
this design: payments-mcp learns nothing about who called it.

### Attributes on the `tools/call payments-mcp_<tool>` CLIENT span

Six keys, and that is all — no HTTP attributes, no duration, no session id:

```
agentgateway.outbound.kind      "Primary"
agentgateway.outbound.subtype   "Mcp"
gen_ai.tool.name                "propose_payment" | "execute_payment"
mcp.method.name                 "tools/call"
mcp.target                      "payments-mcp"
otel.scope.name                 "agentgateway"
```

### Attributes on a *refused* call's SERVER span (M2.1 Task 5)

Every span above comes from a call the gateway **accepted**. A refused call
produces a span too, and it is a different shape — worth publishing because
"can you see the refusal in the trace?" is the question this whole project
exists to answer, and here the answer is yes.

Measured by sending an MCP `initialize` with **no** `Authorization` header
and a hand-minted `traceparent`, then looking the trace up by that exact id.
The gateway joined that trace rather than starting one of its own, and emitted
exactly one span. Re-measured on v1.6.0 on 2026-10-13 (trace
`0e23d152d01236e676d13f8b3fbc160e`); its attribute set, with Jaeger's own two
excluded as described below — 21 keys, the complete set, nothing omitted:

```
client.address                  "127.0.0.1"
duration                        "0ms"
error                           true
error.type                      "JwtAuth"
gateway                         "default/default"
http.request.method             "POST"
http.response.status_code       401
listener                        "listener0"
network.protocol.version        "1.1"
otel.scope.name                 "agentgateway"
otel.status_code                "ERROR"
otel.status_description         "authentication failure: no bearer token found"
protocol                        "http"
reason                          "JwtAuth"
route                           "default/route0"
server.address                  "localhost"  (the Host header the probe sent)
server.port                     18777  (the probe's port-forward)
span.id                         "f7a0162c65f6dbd2"
trace.id                        "0e23d152d01236e676d13f8b3fbc160e"
url.path                        "/mcp"
url.scheme                      "http"
```

(On v1.5.0 the same span had 20 keys: the legacy `http.*` six, `src.addr`, and
no `error.type`. `error.type` is new in v1.6.0 and repeats `reason`. It also
makes `error` and `error.type` a scalar-and-object pair that OpenSearch cannot
map; the collector's `attributes/opensearch-compat` processor now renames
`error.type` to `error_type`, see `docs/upstream-findings.md`.)

Four things differ from the accepted path, and each matters diagnostically:

- **The span is named `POST /*`, not `initialize`.** The `jwtAuth` policy runs
  as an HTTP filter before the MCP backend is consulted, so the request is
  never parsed as JSON-RPC and there is no method name to name the span
  after. Consequently **there are no `mcp.*` attributes at all** — no
  `mcp.method.name`, no `mcp.session.id`, no `mcp.target`, no
  `gen_ai.tool.name`. An operator cannot tell *which tool* was refused from
  this span, only that a POST to `/mcp` was rejected.
- **No `jwt.sub`.** Nothing was verified, so there is no subject to attribute,
  and a refused call is correctly anonymous in the trace. That is the honest
  behaviour and also a real limitation: the trace shows *that* an
  unauthenticated caller was refused, never *who* tried.
- **`otel.status_code = ERROR`, with `otel.status_description` carrying the
  reason verbatim** — the same `TokenError` text the HTTP response body
  returns. This is the one place the refusal reason is machine-readable from
  telemetry alone.
- **`reason = "JwtAuth"`**, a coarse category naming the policy that refused,
  and **`error = true`** (from v1.6.0 also `error.type = "JwtAuth"`). None of
  these appears on any accepted span.

There is **no CLIENT span**, which is the trace-level evidence that the
refusal was a refusal rather than a failed forward.
`tests/e2e/test_caller_identity.py` asserts the behavioural half of this (no
tool result comes back); this section records the telemetry half. Deliberately
*not* asserted by a test: pinning `otel.status_description` would pin an
upstream error string with no added safety, since the behavioural assertions
already cover the property.

### What this project does *not* claim about those spans

- **`internal.span.format` and `span.kind` are Jaeger's, not
  agentgateway's**, and this was measured rather than assumed: in the
  reference trace they appear on 20/20 agentgateway spans *and* on 52/52
  settlement-agent, 6/6 payments-mcp and 2/2 payments-api spans — every span
  from every service. The first is Jaeger's record of the receive format; the
  second is its representation of the OTel `SpanKind` this document's Kind
  column already states. Neither is listed above.
- **The gateway sets no `otel.scope.version`** — 0 of 20 spans, against
  settlement-agent 49/52, payments-mcp 2/6, payments-api 2/2. The version is on
  the Resource (`service.version=1.5.0`), not the scope.

### Findings, in the same shape as Finding 1 (`a2a-sdk`)

1. **A third HTTP attribute vocabulary — in the pinned v1.5.0, and only
   there. Superseded at v1.6.0 (2026-10-13): the SERVER span now speaks stable
   semconv, see the re-measured tables above; kept below as history.** `http.method`, `http.path`,
   `http.status`, `http.host`, `http.version` — not stable semconv
   (`http.request.method`, `url.path`, `http.response.status_code`,
   `server.address`), and not OTel legacy either (`http.status_code`,
   `http.url`). There is no URL attribute at all. Two keys *are* correct stable
   semconv, `url.scheme` and `network.protocol.version`, and that is exactly
   what v1.5.0's source predicts: those two are the only keys it routes through
   its own `semconv` module
   (`crates/agentgateway/src/telemetry/trc.rs` — the `PROTOCOL_VERSION` and
   `URL_SCHEME` `Key` constants); everything else is a raw string key. Not
   worked around: nothing in this repo rewrites the gateway's attributes.
   **This is version-scoped on purpose.** Upstream PR
   [`#3182`](https://github.com/agentgateway/agentgateway/pull/3182) renames
   all six for traces and OTLP logs and is already merged — but on 2026-09-01,
   after v1.5.0 shipped on 2026-08-27, so none of it is in the pinned image.
   `tests/e2e/test_gateway_trace.py`'s exact set equality is what will go red
   on the next image bump, which is the intended way for this project to find
   out rather than discovering it from a stale document.
2. **`span.id`, `trace.id` and `duration` are emitted as span attributes**,
   duplicating information the span already carries structurally, and `duration`
   as a human-formatted string (`"3ms"`) rather than a number. Measured on 10 of
   20 spans — the SERVER spans only. Harmless, but it is per-span payload spent
   restating the envelope.
3. **`mcp.session.id` changes format mid-session.** On the `initialize` span it
   is a plain UUID (`0ef21c0f-8a03-4ec8-92f3-78073ae2b74c`); on every later span
   in the same session it is a base64-encoded JSON object naming the target and
   an upstream session id
   (`eyJ0IjoibWNwIiwicyI6W3sidCI6InBheW1lbnRzLW1jcCIsInMiOiI5MjRhYWUxOWFlY2E0NTQyOTc0NTBjMWM0NzdlY2NjYSJ9XX0`,
   which decodes to `{"t":"mcp","s":[{"t":"payments-mcp","s":"924aae19…"}]}`).
   So the same attribute key does not join the `initialize` span to the rest of
   its own session. Recorded, not worked around.
4. **`host.name` on the Resource is `default.default.svc.cluster.local`** — not
   the pod's hostname and not a name that resolves to anything in this cluster.
   It is the gateway's own default identity string, unset by our config.
   Cosmetic here (Jaeger keys on `service.name`), but it would misattribute the
   gateway's spans in any view that groups by host.
5. **A benign gain, and the only unexpected one: payments-mcp's `initialize`
   spans now join the trace, and did not before.** Pre-reroute traces contain
   no `payments-mcp:initialize` span at all;
   `agents/settlement-agent/mcp_tools.py` injects `params._meta` only on the
   tool call, so FastMCP had no context to extract on `initialize`.
   Post-reroute there are two, parented under
   `agentgateway:initialize payments-mcp` — because agentgateway's
   `stamp_trace_context` (`crates/agentgateway/src/mcp/upstream/mod.rs`, added
   by its PR #2520 for SEP-414) copies `traceparent` into the outbound `_meta`
   of **every** MCP message it forwards, not just tool calls. The interposed
   proxy incidentally closes a correlation gap this project had. Note the
   asymmetry that makes it work: agentgateway *writes* SEP-414 `_meta` on
   egress but only *reads* HTTP headers on ingress (see below).
6. **`jwt.sub` is not a semconv attribute name** (M2.1 Task 4). The
   authenticated principal has a stable spelling — `user.id` — and the
   gateway does not use it; it names the attribute after the JWT claim it read.
   This is the same class of divergence as finding 1, one attribute wide, and
   is recorded rather than worked around for the same reason: nothing in this
   repo rewrites the gateway's attributes, and inventing a `user.id` alias
   would make this document describe telemetry that does not exist. It also
   means a query written against `user.id` finds every service here except the
   one that actually authenticates anything.

### Why the trace survives this hop, stated so the two mechanisms are not conflated

This is the load-bearing mechanism of M2.1 and it is genuinely two independent
carriers, not one:

- This repo's own MCP trace-context convention is **SEP-414**: `traceparent` /
  `tracestate` / `baggage` as unprefixed keys inside JSON-RPC `params._meta`,
  never HTTP headers (see "Conventions adopted"). `mcp_tools.py` injects that
  by hand.
- **agentgateway does not read that.** v1.5.0's `TraceParent::from_request`
  (`crates/agentgateway/src/telemetry/trc.rs`) reads the HTTP `traceparent`
  header and nothing else — it never inspects the request body. Read in
  isolation that predicts a fractured trace, and the M2 spike's first pass
  concluded exactly that, wrongly.
- It survives because `bootstrap.py` instruments the httpx client the MCP
  streamable-HTTP transport is built on, so every outbound MCP request
  **also** carries an auto-injected `traceparent` HTTP header. Measured
  against an echo server:
  `00-df60a027741941a16675b35edbcf0a32-5675a787e51b87e6-03`, with no manual
  injection anywhere in the call path. (**2026-09-11:** this said
  "`HTTPXClientInstrumentor().instrument()`" specifically. Since `mcp` 2.2.0 the
  transport is built on `httpx2`, so the instrumentor that does this is
  `HTTPX2ClientInstrumentor` — also already applied in the same file. The
  mechanism and the conclusion are unchanged; the name was not.)

So nothing here should be read as agentgateway supporting SEP-414 on ingress.
It does not, and a future MCP client carrying context in `_meta` *alone* — a
stdio transport with no HTTP layer, or a client library that does not
auto-instrument httpx — would fragment at this hop. settlement-agent is not
that client, which is the entire reason this works.

One measured sampling consequence of the same header-only design, from
v1.5.0's `TraceSampler::trace_sampled`
(`crates/agentgateway/src/telemetry/log.rs`): a request arriving **with** an
incoming `traceparent` is sampled by default, and a request arriving
**without** one is dropped by default. `randomSampling: true` is set in our
config so a headerless caller is visible too rather than silently untraced;
it changes nothing for settlement-agent's own path.

## agentgateway's guardrail telemetry, as measured (M2.2)

**Every name in this section was read off a live span in Jaeger and dumped in
full before being written down, exactly as the M2.1 section above was.**
agentgateway is not this project's component, so the no-invented-telemetry rule
applies to it without exception, and that rule is why this section records
`reason = "MCP"` rather than the more informative string a designer would have
picked. The reference trace is `b05167ba11744cff81bcedce615cb379`, a real
denial of `CLM-1842` at €6,200 on the pinned
`cr.agentgateway.dev/agentgateway:v1.5.0`, re-read span-by-span for this
section in Task 8 rather than copied from any earlier task's report. **It is a
direct-A2A run, not a browser one** — stated because it matters for one thing
only: it carries no `customer-ui` root and its one "orphan" is the
script-minted parent of `settlement-agent`'s `POST /`, exactly the artifact
this document already describes for script-driven runs. Nothing about the
gateway's own attributes depends on how the settlement was started, and the
browser-rooted denial `6eed5ffa79e3eb758b4367eb75cb5a0d` carries the identical
key sets — checked, not assumed.

M2.2 adds one policy hop to the gateway's configuration —
`mcpGuardrails.processors[0]`, `kind: remote`, `methods: {"tools/call":
request}`, `host: policy-decision-service.northstar-platform.svc.cluster.local:9100`,
`failureMode: failClosed` — and that hop is visible in the trace as one extra
span per tool call plus four extra attributes on the SERVER span of a call
that gets refused. Nothing else about the gateway's telemetry changed.

### Attributes on the `Guardrail` span, the gateway's own view of the policy hop

A CLIENT span named `Guardrail`, one per `tools/call`, a **child of the
gateway's `tools/call` SERVER span**. Nine keys — the complete set, nothing
omitted, with Jaeger's own two excluded as described under "What this project
does *not* claim about those spans" above:

```
agentgateway.outbound.kind      "Policy"
agentgateway.outbound.subtype   "Guardrail"
grpc.status                     0
http.host                       "policy-decision-service.northstar-platform.svc.cluster.local"
http.method                     "POST"
http.path                       "/agentgateway.dev.ext_mcp.ExtMcp/CheckRequest"
mcp.method.name                 "tools/call"
mcp.target                      "payments-mcp"
otel.scope.name                 "agentgateway"
```

Four things follow from that list, and each is a measurement rather than a
reading of the source:

- **`http.path` is literally the ExtMCP gRPC method.** An operator who has
  never seen this config can tell from one span that the gateway asked an
  external service for a decision, and which RPC it used.
- **`grpc.status = 0` reports the transport, not the decision.** The gRPC call
  succeeded; the deny is in the payload. That distinction is diagnostically
  load-bearing under `failureMode: failClosed`: a refusal caused by an
  *unreachable* PDS produces a different `grpc.status` here, so "the policy
  said no" and "the policy service was down" are separable in the trace rather
  than both surfacing as a blocked call.
- **The span appears on a permitted call too**, with the identical key set —
  the decision is not in this span at all. Confirmed on the permit trace
  `88cdfbcf5720487c9597ba573c02c33d` (€5,800, executed).
- **Two attributes are absent and are recorded as absent rather than guessed:**
  nothing names the guardrail *processor* or the `metadata` keys it was
  configured with, and nothing carries the JSON-RPC error code or the
  `mcp_error` payload the PDS returned. If you want the decision, you read the
  `policy.decision` span below, or the parent's `otel.status_description`.

### Attributes on a policy-denied `tools/call` SERVER span

The accepted-call block earlier in this document lists 22 attribute names. A
call the policy refuses carries **27 keys** on the same span on v1.6.0 — those
same 22 plus exactly five, and the five are the whole of the difference
(v1.5.0 carried 26: the same four without `error.type`). Re-measured
2026-10-13, trace `a835b2c045184df4b6288d3822f14c81`:

```
client.address               "10.244.0.15"
duration                     "4ms"
error                        true
error.type                   "MCP"
gateway                      "default/default"
gen_ai.tool.name             "execute_payment"
http.request.method          "POST"
http.response.status_code    200
jwt.sub                      "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
listener                     "listener0"
mcp.method.name              "tools/call"
mcp.resource.type            "tool"
mcp.session.id               (base64 JSON, same form as the accepted span)
mcp.target                   "payments-mcp"
network.protocol.version     "1.1"
otel.scope.name              "agentgateway"
otel.status_code             "ERROR"
otel.status_description      "mcp: mcpGuardrails rejected: requested 6200 EUR exceeds the 6000 EUR limit for this agent at autonomy level assisted [policy-denial rule=settlement-agent.assisted.execute_limit limit=6000.00 requested=6200.00]"
protocol                     "mcp"
reason                       "MCP"
route                        "default/route0"
server.address               "agentgateway.northstar-platform.svc.cluster.local"
server.port                  3000
span.id                      "c1121277ac4b582c"
trace.id                     "a835b2c045184df4b6288d3822f14c81"
url.path                     "/mcp"
url.scheme                   "http"
```

The five that only a refusal carries are `error`, `error.type`,
`otel.status_code`, `otel.status_description` and `reason`. Observations, all
measured:

- **`http.response.status_code` is 200, not 4xx.** On v1.6.0 ExtMCP's refusal is
  an MCP **tool result marked `isError`** inside a successful HTTP response
  (upstream PR #3526; on v1.5.0 it was a JSON-RPC error, code `-32001`, with the
  PDS's body under `error.data`, which v1.6.0 no longer sends). It matches
  neither the native CEL `mcpAuthorization` mechanism's 400 nor `jwtAuth`'s 401.
  Anyone alerting on HTTP status alone will not see this refusal at all. The
  rule, the limit and the requested amount now reach the client only as the
  `[policy-denial ...]` tag at the end of the result's text
  (`pds/deny_text.py::format_deny`).
- **`reason = "MCP"`, and that is all the categorisation there is.** On the
  M2.1 jwtAuth refusal documented above the same key reads `"JwtAuth"`. So the
  key does separate an authentication refusal from a policy refusal, but it
  names the *layer*, not the rule — `"MCP"` is as specific as v1.5.0 and
  v1.6.0 get. v1.6.0 repeats it as `error.type`.
- **`otel.status_description` carries the PDS's own reason string verbatim**,
  behind a fixed `mcp: mcpGuardrails rejected: ` prefix the gateway adds,
  followed on v1.6.0 by the `[policy-denial rule=... limit=... requested=...]`
  tag. `AuthorizationError.reason` is what lands there. This is the one place the
  refusal's *reason* is machine-readable from gateway telemetry alone, and it
  is a free-text string, not a code.
- **Unlike the M2.1 jwtAuth refusal, every `mcp.*` attribute is present.** The
  guardrail hook runs after the message has been parsed as JSON-RPC, so
  `mcp.method.name`, `mcp.session.id`, `mcp.target` and — critically —
  `gen_ai.tool.name` are all on the span. An operator can tell *which tool* was
  refused, which the authentication refusal could not tell them. `jwt.sub` is
  present for the same reason: the caller was authenticated, then refused.
- **There is no CLIENT span to payments-mcp**, and no payments-mcp span for
  this tool call anywhere in the trace. That absence is the trace-level
  evidence that the execution never happened rather than happening and failing.

`tests/e2e/test_policy_denial.py` asserts the behavioural half of all of this
and pins the gateway's child-span shape by exact equality on the sorted
`(service, operation, kind)` triples of the SERVER span's children — measured
as three, not one: `Guardrail` (CLIENT, agentgateway) and `policy.decision`
(SERVER, policy-decision-service) are **siblings** under it, because the
gateway propagates the context it received to the hook rather than its own
client span's context.

Deliberately *not* asserted by any test: the exact text of
`otel.status_description`. Pinning it would pin a concatenation of an upstream
prefix with this project's own reason string, and both halves are already
covered — the prefix by the child-shape assertion, the reason by the PDS's own
unit tests.

## The policy decision, as measured (M2.2) — Gap 3, closed

Gap 3 below records that no OpenTelemetry semantic convention exists for a
policy or autonomy decision. That is still true; what changed in M2.2 is that
this project now emits such a span, so the gap moves from "deferred" to
"closed by defining our own names, in the open, and saying so".

**The attribute keys are defined in exactly one place —
`platform/policy-decision-service/pds/telemetry.py` — and this document cites
that file rather than re-typing them**, which is the whole reason they were
put in a module-level `ATTRIBUTES` tuple: the same tuple is what
`platform/policy-decision-service/tests/test_servicer.py`'s
`test_every_attribute_we_emit_is_declared_and_visibly_ours` asserts against,
so the source, the guard and this contract cannot drift into three
different lists.
**Dated note, 2026-09-14 (M5 Task 3): corrected — the previous reference
here, `tests/unit/test_stage2_pds_config.py`, asserts nothing about
`ATTRIBUTES`; verified by reading it. `test_servicer.py`, not a line number
within it, is the reference this document now cites.** The `policy.`
prefix is deliberate and so is its absence of a `northstar.` one: a reader
scanning a span must be able to tell at a glance which attributes came from a
framework (`gen_ai.*`, `mcp.*`, `http.*`) and which this project invented, and
this whole section is the second kind.

**Read off a live span, not off that file.** The block below is the deny
decision from trace `c6666319b97c70487bccf8af1ba73b8e`, dumped from Jaeger and
compared key-for-key against `telemetry.py`'s declared `ATTRIBUTES`. They
agree, and the check was worth making in that direction: the source declares
ten `policy.*` keys, and how many of them a given span actually carries
depends on the decision, because `server.py` writes the optional ones only
when it has a value (`if decision.limit is not None: span.set_attribute(...)`,
and likewise for the caller, tool, amount and currency). Absent reads as
absent; a zero amount would read as a request for nothing.

**Dated note, 2026-09-14 (M5 Task 3):** the source now declares **ten**
`policy.*` keys, not nine — this task's commit added `policy.autonomy.reason`
alongside `policy.autonomy.level`. Measured, not retyped:
`len(telemetry.INVENTED_ATTRIBUTES) == 10` in
`platform/policy-decision-service/pds/telemetry.py`.

**Dated note, 2026-09-15 (M5 Task 6): re-derived, not flagged again.** The
"nine keys"/`b05167ba…`/`88cdfbcf…` inventory the note above left in place has
now been replaced below with a fresh pull against the live cluster
(Jaeger's own `GET /api/traces/{traceID}` HTTP query API), closing ruling
M5-R21. Two traces anchor it: `c6666319b97c70487bccf8af1ba73b8e`, an
assisted-mode denial produced the same way the M2.2 reference trace was, and
`08d797e48e8b41e67a022f84dd38a732`, an autonomous-mode permit produced by
this task's own anti-vacuity e2e (`tests/e2e/test_progressive_autonomy.py`)
flipping the live contract — the first trace this project has that shows
`policy.autonomy.level` as anything other than `"assisted"`.

### Attributes on the `policy.decision` span

Eleven keys on a limit-rule decision — the ten `policy.*` names plus the
scope — which is the complete set for the span below, nothing omitted, with
Jaeger's own `internal.span.format` and `span.kind` excluded by the same
convention as the agentgateway blocks:

```
otel.scope.name              "northstar.policy-decision-service"
policy.autonomy.level        "assisted"
policy.autonomy.reason       "TARGETING_MATCH"
policy.caller.spiffe_id      "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
policy.decision              "deny"
policy.decision.retryable    false
policy.limit.amount          6000
policy.request.amount        6200
policy.request.currency      "EUR"
policy.rule                  "settlement-agent.assisted.execute_limit"
policy.tool.name             "execute_payment"
```

**What varies, measured across both reference traces rather than reasoned
about.** `policy.decision` is `"permit"` or `"deny"`; `policy.rule` names the
rule that fired; `policy.autonomy.level` is whatever the flag resolved to for
that call; and `policy.limit.amount` and the two `policy.request.*` keys are
each **absent** whenever the rule or the tool has nothing to state for them.
Concretely, from the two traces:

| Trace | Tool | `policy.rule` | `policy.autonomy.level` | `policy.decision` | `policy.limit.amount` | Keys |
|---|---|---|---|---|---|---|
| `c6666319…` (deny) | `propose_payment` | `unlimited_tool` | `assisted` | `permit` | absent | 10 |
| `c6666319…` (deny) | `refuse_payment` | `unlimited_tool` | `assisted` | `permit` | absent | 8 |
| `c6666319…` (deny) | `execute_payment` | `settlement-agent.assisted.execute_limit` | `assisted` | `deny` | `6000` | 11 |
| `08d797e4…` (permit) | `propose_payment` | `unlimited_tool` | `autonomous` | `permit` | absent | 10 |
| `08d797e4…` (permit) | `execute_payment` | `settlement-agent.autonomous.execute_limit` | `autonomous` | `permit` | `1000000` | 11 |

So **two or three `policy.decision` spans appear per settlement**, one per
tool call the settlement agent makes, and the set is the interesting artefact
rather than any one span alone. In the denial trace there are three: the
agent is permitted to *propose* €6,200, refused permission to *execute* it,
and then permitted to call `refuse_payment` — its terminal, no-money branch
tool that tells the customer why — which is why that row's key count (8) is
the smallest: `refuse_payment` carries neither `policy.request.*` key nor
`policy.limit.amount` because it moves nothing. The permit trace has only
two, because there is no refusal branch to take. `propose_payment` and
`refuse_payment` are both in the PDS's `UNLIMITED_TOOLS` set because neither
moves money; `execute_payment` is the one tool a limit rule ever fires
against, whether the limit that rule states is €6,000 (assisted) or
€1,000,000 (autonomous) — the trace shows that distinction being made rather
than asserted.

Four properties worth stating because they are easy to get wrong from the
outside:

- **`policy.request.amount` is a float**, `6200` rather than `"6200"` or
  `6200.0` as a string. The amount arrives at the PDS as a JSON number inside
  `mcp_request` (measured, M2.2 Task 1: `{"name":"propose_payment",
  "arguments":{"claim_id":"CLM-1842","amount":6200.0,"currency":"EUR"}}`), and
  nothing stringifies it on the way to the span.
- **`policy.decision.retryable` is always `false`**, and is on the span rather
  than only in the response body because it is the field the agent keys its
  terminal branch off. A denial the agent may retry is a denial it can sit on
  stage retrying.
  **It is also the one attribute in this system that any backend has refused
  to store** (M4 Task 1): its key is a dotted prefix of `policy.decision`'s,
  and OpenSearch expands dotted keys into nested objects at index time, so a
  single document cannot hold both. It reaches Jaeger and Dash0 under the name
  above — the name this contract defines — and reaches OpenSearch as
  `policy.decision_retryable`, rewritten by the `attributes/opensearch-compat`
  processor in the collector's OpenSearch-only branch. Of the 114 distinct
  span attribute keys Jaeger held across all ten services when this was
  measured (2026-09-13), it is the only such collision; nothing else is
  renamed for any destination. See "Export destinations" above.
- **`policy.caller.spiffe_id` is the *caller's* attested identity**, not the
  PDS's own and not the audience — the same string the gateway records as
  `jwt.sub` one span up, handed to the PDS through the guardrail's
  `metadata: {caller: 'jwt.sub'}` CEL expression rather than re-verified. The
  PDS validates no token itself and presents none: it decides on an identity
  the gateway already established.
- **`policy.autonomy.level` is a constant today** (`assisted`), and is on the
  span anyway. It is the field M5 turns into a feature flag; recording it now
  means the traces M5 changes the behaviour of already say what the behaviour
  was keyed on.
  **Dated note, 2026-09-14 (M5 Task 3): no longer true.** This is exactly the
  commit that stopped it being a constant — `policy.autonomy.level` is now
  resolved per decision from the `autonomy-level` flag through
  `pds/autonomy.py::resolve_autonomy`, not read off a compiled-in module
  constant. The sentence above describes the pre-M5 (M2.2) shape; left in
  place as the historical reason the field exists, not as the field's
  current behaviour.
  **Dated note, 2026-09-15 (M5 Task 6): observed, not just resolvable.**
  Trace `08d797e48e8b41e67a022f84dd38a732` (above) is this project's first
  live span with `policy.autonomy.level = "autonomous"` — the live
  `northstar-autonomy` `FeatureFlag` object was targeted to `autonomous` for
  the duration of one settlement (`tests/harness/contract.py`'s
  `set_targeted_branch`), the trace was captured, and the flag was reverted
  to its shipped, refusing (`assisted`) state before this task finished.

**No `policy.*` attribute is set anywhere except in the PDS.** In particular
nothing in `settlement-agent`, `payments-mcp` or `payments-api` writes one, and
nothing rewrites agentgateway's spans to add one — the enrichment boundary
described under "Domain attributes we own" applies here unchanged.

## SPIRE instrumentation, as measured (M2.1 Task 4)

**SPIRE 1.15.3 emits no spans.** Not "we did not wire tracing up" — there is no
tracing to wire up. Its telemetry configuration accepts Prometheus, DogStatsD,
Statsd, M3 and an in-memory sink, and nothing else; there is no OTLP exporter,
no trace exporter of any kind, and no `traceparent` handling anywhere in the
Workload API path. So the JWT-SVID fetch that now precedes every MCP call
appears in this system's traces only as latency inside settlement-agent's own
`execute_tool` span, not as a hop of its own. That is stated here rather than
quietly omitted, because a reader of this document's other sections would
reasonably expect a span per hop, and no attribute below was invented to fill
the gap.

What SPIRE does emit is Prometheus, and the chart ships it **off** by default.
`spire-server.telemetry.prometheus.enabled` and the matching `spire-agent` key
are both set to `true` in `deploy/platform/spire/values.yaml`, two ClusterIP
Services in `deploy/platform/spire/metrics.yaml` expose the two ports (the
chart publishes none), and the collector scrapes them through a
`prometheus/spire` receiver in both collector configs. Without that last piece
the metrics are exposed and thrown away — the same seam M2.1 Task 3 found in
agentgateway, which had tracing available and unconfigured for a whole task.

**How the metrics arrive, read off the collector's own debug exporter.** The
`job_name` becomes `service.name`, which is what puts SPIRE in the same service
vocabulary as everything else here. Four consecutive lines of
`kubectl -n observability logs deployment/otel-collector`, at the committed
`exporters.debug.verbosity: normal`, with the tab separators the collector
writes rendered as spaces and nothing else altered:

```
2026-09-09T14:25:02.633Z  info  Metrics  {"resource": {"service.instance.id": "b9237a32-4649-4154-b0c2-bdd49f2b040f", "service.name": "otelcol-contrib", "service.version": "0.158.0"}, "otelcol.component.id": "debug", "otelcol.component.kind": "exporter", "otelcol.signal": "metrics", "resource metrics": 1, "metrics": 91, "data points": 95}
2026-09-09T14:25:02.633Z  info  ResourceMetrics #0 service.name=spire-agent server.address=spire-agent-metrics.northstar-platform.svc.cluster.local service.instance.id=spire-agent-metrics.northstar-platform.svc.cluster.local:9988 server.port=9988 url.scheme=http
ScopeMetrics #0 github.com/open-telemetry/opentelemetry-collector-contrib/receiver/prometheusreceiver@0.158.0
spire_agent_runtime_total_gc_pause_ns{host=northstar-control-plane} 7.2153768e+07
```

An earlier revision of this block showed only the two middle lines with the
timestamps stripped, which made it not reproducible from the command that
produced it. Two details of the full form are the collector's rather than
SPIRE's and are worth naming so it is not misread: the `"resource"` object on
the first line describes the **collector** (`service.name: otelcol-contrib`) —
it is the logger's own context, not the scraped target's, whose resource is the
`ResourceMetrics #0` line below it — and `ScopeMetrics #0` names the Prometheus
receiver, because SPIRE carries no OTel SDK to name a scope of its own. The
same four-line shape appears with `service.name=spire-server`.

**The series that make this task's mechanism visible**, quoted verbatim from
`/metrics` on the running pods rather than paraphrased. From spire-agent, the
workload attestation that turns a socket connection into an identity, and the
JWT-SVID path specifically:

```
spire_agent_workload_api_workload_attestor{attestor="k8s",host="northstar-control-plane",status="OK"} 388
spire_agent_workload_api_workload_attestation{host="northstar-control-plane",status="OK"} 388
spire_agent_workload_api_discovered_selectors_sum{host="northstar-control-plane"} 7296
spire_agent_rpc_workload_api_fetch_jwtsvid{host="northstar-control-plane",status="OK"} 2
spire_agent_jwt_svid_cache_size{host="northstar-control-plane"} 1
```

`attestor="k8s"` is the label that says the identity came from the kubelet-based
workload attestor — the pod's namespace and service account — rather than from
anything the workload asserted about itself. From spire-server, the mint:

```
spire_server_rpc_svid_v1_svid_new_jwtsvid{host="spire-server-0",status="OK"} 1
spire_server_server_ca_sign_jwt_svid{host="spire-server-0"} 1
spire_server_entry_cache_reload{host="spire-server-0",status="OK"} 663
```

Two counts in there are worth reading carefully rather than as round numbers,
because they say something about the design. `fetch_jwtsvid` at the agent is
**2** while `new_jwtsvid` at the server is **1**: the agent caches, so a repeat
fetch for the same audience is answered locally and never reaches the server —
which is also why `svid.py` keeps its own in-process cache with a refresh
margin rather than treating a fetch as free. And `workload_attestation` in the
hundreds against a JWT-SVID count in single digits is the chart's own OIDC
discovery provider holding a long-lived X509-SVID stream open, not this
project's traffic; attestation happens per Workload API connection, not per
token.

No absolute value above should be read as a fixture. These are counters on a
long-running cluster: they were rising while this was written, and a re-run
produces different numbers. What is stable, and what this section actually
claims, is the *series* — the metric names and their label keys, which is the
part a dashboard or an alert would be built on.

## Metrics, as measured (M4 Task 2) — and the five that nothing emits

**Until M4 Task 2 this system emitted no metric at all.** Measured during the
M4 design (spec §6) and true of every commit before this one: no `MeterProvider`
in any Python or Go service, no `OTEL_METRICS_*` in any manifest, and a
collector `metrics` pipeline exporting to `debug` alone — so SPIRE's scrape
(the section above) was received and discarded, and nothing else was produced.

M4 Task 2 installs a `MeterProvider` in all five Python services then in the
repo — `platform/eval-scorer` arrived in Task 4 and has none, by the argument in
limitation 2 below — and routes the
collector's `metrics` pipeline to Prometheus over OTLP. That is the wiring. The
*finding* is what turned up once the wiring existed, and it is a gap report
rather than a dashboard full of green — which spec §6 predicted in advance, in
writing, so this is not a disappointment being dressed up as a result.

### Where the conventions live now

The GenAI semantic conventions are **no longer in the main
`open-telemetry/semantic-conventions` repository**. They live in
[`open-telemetry/semantic-conventions-genai`](https://github.com/open-telemetry/semantic-conventions-genai),
which also carries the **MCP** conventions this document cites elsewhere. This
is not an inference from where the files happen to be: the old location says so
itself. `docs/gen-ai/README.md` and `docs/gen-ai/gen-ai-metrics.md` in the main
repo are now both stubs titled "Moved: Generative AI semantic conventions",
carrying one sentence and no definitions (fetched 2026-09-13):

> GenAI semantic conventions have moved to the
> [OpenTelemetry GenAI semantic conventions repository](https://github.com/open-telemetry/semantic-conventions-genai).
> This page has moved and is no longer maintained in this repository.

Worth knowing because the old URLs still return **200**, not 404 — a reader
following a stale link lands on a page that looks authoritative and is
explicitly unmaintained. Every convention claim in this section was read from
the new repository at `main` (`0c87594975`, 2026-09-10), with
`docs/gen-ai/gen-ai-metrics.md` last changed at `8d3e4a0f3c34`, 2026-08-10.

`open-telemetry/semantic-conventions-genai` defines **twelve** GenAI metrics.
**Every one is Development stability**, and that qualifier is load-bearing
rather than decorative: a Development metric can be renamed upstream, and a
sentence written without it becomes false at the next bump with nothing here to
catch it. Spec §6 asks about six of the twelve; the other six
(`gen_ai.client.operation.time_to_first_chunk`,
`gen_ai.client.operation.time_per_output_chunk`, `gen_ai.server.request.duration`,
`gen_ai.server.time_per_output_token`, `gen_ai.server.time_to_first_token`,
`gen_ai.invoke_workflow.duration`) are named here so a reader knows the six
below are a chosen subset, not the whole convention.

### The §6 inventory, per metric, with the layer each verdict was verified at

| Metric (OTel name) | Type | Unit | Stability | Emitted? | Verified at |
|---|---|---|---|---|---|
| `gen_ai.invoke_agent.duration` | Histogram | `s` | Development | **No** | SDK — created by nothing |
| `gen_ai.invoke_agent.inference_calls` | Histogram | `{inference_call}` | Development | **No** | SDK — created by nothing |
| `gen_ai.invoke_agent.tool_calls` | Histogram | `{tool_call}` | Development | **No** | SDK — created by nothing |
| `gen_ai.execute_tool.duration` | Histogram | `s` | Development | **No** | SDK — created by nothing |
| `gen_ai.client.token.usage` | Histogram | `{token}` | Development | **Yes** | Prometheus — series present |
| `gen_ai.client.operation.duration` | Histogram | `s` | Development | **No** | SDK — created by nothing |

**One of six.** "Created by nothing" is the precise claim and it is the
strongest of the available ones: the metric is not merely absent from
Prometheus, it is never brought into existence in any process in this system,
so no reader, exporter, collector pipeline or Prometheus receiver is a candidate
explanation. Measured 2026-09-13 by reading the four installed frameworks'
source — not one of those five names appears anywhere in `pydantic-ai-slim`
2.42.0, `a2a-sdk` 0.3.7, `fastmcp` 4.0.3 or `mcp` 2.2.0, and the last three
contain no `get_meter(` call at all. The full sweep, and the argument for why
this is a framework gap rather than a scope boundary, is
[`docs/upstream-findings.md`](upstream-findings.md) **Finding 12**.

### What PydanticAI 2.42.0 actually creates — all three of them

Read out of the deployed image rather than a local venv, by constructing this
project's own `InstrumentationSettings` inside the running pod and enumerating
the instruments it built:

```
$ kubectl -n northstar-agents exec deploy/senior-settlement-agent -- python -c '...'
pydantic_ai version: 2.42.0
provider: opentelemetry.sdk.metrics._internal.MeterProvider
INSTRUMENTS PydanticAI CREATED:
    ('cost_histogram', 'operation.cost', '{USD}', '_Histogram')
    ('time_to_first_chunk_histogram', 'gen_ai.client.operation.time_to_first_chunk', 's', '_Histogram')
    ('tokens_histogram', 'gen_ai.client.token.usage', '{token}', '_Histogram')
```

| Instrument | Unit | In the conventions? | Reaches Prometheus? |
|---|---|---|---|
| `gen_ai.client.token.usage` | `{token}` | Yes — Development | **Yes** |
| `gen_ai.client.operation.time_to_first_chunk` | `s` | Yes — Development | **No**, and not because anything is broken |
| `operation.cost` | `{USD}` | **No** — PydanticAI's own name, in no OTel namespace | **Yes** |

Two of those rows need their reason stated rather than left to be guessed at.

**`gen_ai.client.operation.time_to_first_chunk` is created and never recorded
into.** `record_metrics` guards it with `if time_to_first_chunk is not None`
(`pydantic_ai/models/instrumented.py`), and that value is only produced for a
*streaming* request. This demo streams nothing, so the histogram takes no
measurement, and the OTel SDK exports no data point for an instrument with
none. **Created is not emitted**, and this row is the reason that distinction is
drawn explicitly everywhere in this section: a survey that equated the two
would have published this metric as supported, and a dashboard built on it
would have been permanently empty.

**`operation.cost` is a real series and a non-convention name.** It carries the
model call's monetary cost (the same figure the `chat` span's `operation.cost`
attribute carries, recorded above under "PydanticAI instrumentation"). It is
published here because it is measured and present, and flagged because a reader
building on it should know it is PydanticAI's invention, not a convention, and
carries none of a convention's stability promise — PydanticAI's own source says
as much about its sibling `gen_ai.aggregated_usage.*` namespace: *"a custom
namespace, not part of the OpenTelemetry Semantic Conventions. It may be updated
if OTel introduces an official convention."*

### `gen_ai.client.token.usage`, as it actually arrives

The one row of §6's table that is not a gap. Read from Prometheus's
`/api/v1/series`, one series verbatim, whitespace only reformatted:

```
{"__name__": "gen_ai_client_token_usage_sum",
 "gen_ai_operation_name": "chat",
 "gen_ai_provider_name": "anthropic",
 "gen_ai_request_model": "claude-sonnet-5",
 "gen_ai_response_model": "claude-sonnet-5",
 "gen_ai_system": "anthropic",
 "gen_ai_token_type": "input",
 "instance": "98dec6ce-a261-4647-8614-2532c564cee6",
 "job": "settlement-agent"}
```

34 series in total across the histogram's `_bucket`/`_sum`/`_count` families and
the two `gen_ai.token.type` values (`input`, `output`). `gen_ai_system` is the
**deprecated** spelling of `gen_ai_provider_name` and both appear, exactly as
they do on the `chat` span — `version=5` emits both, which this document already
records for spans and which is now also true of this metric's attributes.

**Names: OTel's and Prometheus's are not the same, and both are published here
deliberately.** Prometheus v3.14.0's default OTLP translation strategy is
`UnderscoreEscapingWithSuffixes` (`config/config.go:280`), which this cluster
leaves at its default. So the dotted OTel name becomes underscores plus a
histogram suffix, and dotted *attributes* become underscored *labels*. The OTel
name is what the SDK emits and what a convention claim is about; the Prometheus
name is what a query has to use. A document publishing only one of them would
mislead whichever reader needed the other.

| OTel name | Prometheus series |
|---|---|
| `gen_ai.client.token.usage` | `gen_ai_client_token_usage_bucket`, `_sum`, `_count` |
| `operation.cost` | `operation_cost_bucket`, `_sum`, `_count` |

### Which services emit anything at all, and why that question is the whole method

**A "no such metric" result is worthless unless the path it would have taken is
known to work.** M4 Task 1 shipped a milestone-blocking defect of exactly this
shape — "no errors" concluded from a 200-line tail of a 17,173-line log — so
every absence above is paired with a presence measured in the same window.
Measured 2026-09-13, per `job` label (Prometheus's rendering of OTLP's
`service.name`):

| `job` | Distinct metric names | Control — what proves its path works |
|---|---|---|
| `spire-server`, `spire-agent` | 271 `spire_*` plus 40 Go-runtime | Collector-scraped, pushed over OTLP; **no Python service involved**, so it proves the collector→Prometheus hop on its own |
| `settlement-agent` | 20 | `gen_ai_client_token_usage_*` and `operation_cost_*` from real model calls |
| `claims-agent` | 14 | `http_server_*`/`http_client_*` from the ASGI and httpx instrumentations |
| `payments-mcp` | 4 | `http_client_request_duration_seconds_*` on every call to payments-api |
| `reviewer-ui` | 4 | `http_client_duration_milliseconds_*` on every ledger read |
| `policy-decision-service` | 1 | **No metric of its own exists to find**, so a probe was sent instead — see below |

`payments-mcp`'s row is the one that decides `gen_ai.execute_tool.duration`:
tool execution happens in that process, under FastMCP, so that is where the
metric would be recorded. Its MeterProvider demonstrably gets other metrics all
the way to Prometheus in the same window, which is what makes the absence
FastMCP's rather than ours.

**The two services with nothing to find got a sent probe rather than a found
one**, because an empty result from a service that records nothing is
indistinguishable from an empty result from a broken exporter. A counter named
`northstar.meterprovider.probe` was recorded inside each pod, through that
service's own telemetry module, and arrived:

```
{"__name__": "northstar_meterprovider_probe_total", "job": "policy-decision-service",
 "northstar_probe_reason": "prove-pds-export-path", "instance": "0b212e42-..."}
{"__name__": "northstar_meterprovider_probe_total", "job": "settlement-agent",
 "northstar_probe_reason": "prove-senior-agent-export-path", "instance": "856688d9-..."}
```

Those two series are in the cluster's Prometheus and are named for what they
are. They are a measurement artefact, not telemetry this system emits, and
nothing queries them.

**This evidence is point-in-time, and deliberately not turned into a standing
test** (M4 Task 2 review finding F8). The probes were sent by hand on
2026-09-13 and age out of Prometheus's 15-day retention
(`--storage.tsdb.retention.time=15d`, read off the running container), so after
2026-09-28 the two series above will not be in the store and nothing will go
red about it. That is the honest state of this row rather than an oversight:
`tests/e2e/test_prometheus_metrics_ingest.py::test_each_service_demonstrably_gets_a_metric_all_the_way_to_prometheus`
parametrises the **four** services that record an instrument, and it cannot
parametrise these two, because the thing being proved is precisely that they
record nothing. The standing alternative would be a test that `kubectl exec`s
a counter into two pods on every gate run — a suite that otherwise only reads
the cluster would start writing to it, to re-prove a property the source
already states and `tests/unit/test_metrics_wiring.py` already guards
(`_configure_metrics` exists and is called in all five services). What a reader
should take from this row is what it says: on 2026-09-13, both export paths
carried a metric end to end. If that needs re-establishing later, re-send the
probe — do not read an expired series as an absence.

**`senior-settlement-agent` has no `job` of its own, by design.**
`deploy/k8s/senior-settlement-agent.yaml` sets `OTEL_SERVICE_NAME=settlement-agent`
deliberately — the demo's point is that the code is byte-identical and the
platform tells the two identities apart by *identity*, on the `policy.decision`
span, not by service name. So its probe above lands under
`job="settlement-agent"` with its own `instance`, which is the expected result
and not a misconfiguration.

### Two gaps in our own configuration, found by measuring metrics

Neither is an upstream defect; both are this project's own, recorded here
rather than quietly fixed or quietly ignored.

**1. `reviewer-ui` emits the LEGACY HTTP metric name.** It is the only service
whose Deployment sets no `OTEL_SEMCONV_STABILITY_OPT_IN`, measured
2026-09-13 across all four manifests:

```
reviewer-ui       | OTEL_SEMCONV_STABILITY_OPT_IN = None
settlement-agent  | OTEL_SEMCONV_STABILITY_OPT_IN = http
claims-agent      | OTEL_SEMCONV_STABILITY_OPT_IN = http
payments-mcp      | OTEL_SEMCONV_STABILITY_OPT_IN = http
```

The consequence was invisible while this system emitted no metrics and is
visible now: `reviewer-ui` reports `http_client_duration_milliseconds_*`
(legacy, milliseconds) where every other service reports
`http_client_request_duration_seconds_*` (stable, seconds). A panel summing
"HTTP client latency" across services would silently mix units. **Not fixed in
M4 Task 2**, deliberately: setting that variable also changes reviewer-ui's
*span* attributes, which this document pins elsewhere and which several e2e
assertions read, so it is a telemetry-contract change that belongs in its own
task rather than as a side effect of adding a MeterProvider.

**2. Two services still have no `MeterProvider`: `systems/payments-api` (Go)
and `platform/eval-scorer` (Python).** M4 Task 2 covered the five Python
services that existed then. payments-api emits spans and no metrics, so the
money path has no metric of its own; `eval-scorer` (M4 Task 4) installs a
`TracerProvider` and deliberately no `MeterProvider`, the reasoning in its own
`scorer/emit.py::configure`. None of §6's six metrics is a concern for either —
every one is GenAI, and no agent runs in either — so this does not affect the
inventory above, but the spec's "no `MeterProvider` in any Python **or Go**
service" is only half-closed and should not be read as fully closed.

(This entry named payments-api alone until M4 Task 4's fix round. The commit
that added the second one said so in `scorer/emit.py` and updated neither this
limitation nor `tests/unit/test_metrics_wiring.py`'s docstring, which also said
"the one service".)

### How this is kept true

`tests/e2e/test_prometheus_metrics_ingest.py` re-verifies the whole inventory
against the live cluster, and is written so that a false negative cannot pass
quietly: every absence assertion is paired with a positive control **in the
same test** (it was "in the same run" until review finding F3 showed three
tests passing against a completely empty store), the controls are bounded to a
recent window (an unbounded check would stay green for Prometheus's full 15-day
retention after everything stopped emitting), a test fails if any `gen_ai` name
turns up that the file does not already account for, and "below stage 4" now
means the API server answered `NotFound` rather than any non-zero `kubectl`
exit, so an unreachable cluster cannot skip the whole file quietly (finding
F7). If a framework implements one of the five, that file goes red and this
table is what has to change.

`tests/unit/test_metrics_wiring.py` covers what the cluster cannot show: that
all five services install the provider, that the five copies of the wiring have
not drifted, that nothing configures a view or temporality that would discard
data, that the chart pin and the collector's endpoint agree with each other,
that no `deploy/k8s/` manifest changes the temporality, the export interval or
the exporter from outside the Python that was measured, and that nothing under
`agents/`, `apps/`, `platform/`, `tools/` or `systems/` *authors* an instrument
under a namespace an instrumentation library owns — the last two added in the
fix round, and the second of them is the only layer that can see who wrote a
metric at all.

## settlement-agent's own instrumentation: `execute_approved` (M3 Task 3)

M3 Task 3 adds one span this repo owns outright -- not agentgateway's, not
a2a-sdk's, not the Policy Decision Service's -- created by
`agents/settlement-agent/settlement.py`'s `execute_approved` itself, around
the human-approved execution. Named `settlement.execute_approved`, one per
approved execution, on both settlement Deployments (the same code, run under
two identities).

### Attributes on the `settlement.execute_approved` span

Two keys, both `northstar.`-prefixed because this is domain data no
OpenTelemetry semantic convention covers, set only by this repo's own code
and nowhere else:

```
northstar.payment.id             "PAY-000001"
northstar.approval.reviewer_id   "alex.mercer"
```

`northstar.payment.id` is the ledger's own payment id, read off the approval
payload -- not derived from a model call, because `execute_approved` makes
none. `northstar.approval.reviewer_id` is the reviewer's identity exactly as
the approval payload states it: a claim carried on the span, not a verified
credential -- the M3 Task 3 review fix round's ledger check
(`_verify_refusal`) constrains what this span's *execution* can cause; it
does not turn this string into an authenticated identity, and this document
does not claim otherwise.

**This span also carries this project's first span link.** Every other span
named in this document relates to its neighbours by parent/child alone.
`execute_approved` additionally links to the settlement-agent span that was
active in `decide_settlement` when the refusal was recorded -- read back off
Task 2's ledger fields `refusedTraceId`/`refusedSpanId` and built into a real,
valid remote `SpanContext` in `settlement.py`'s `_refusal_link` (see that
function's own docstring for the measurement: a `SpanContext` built with
`span_id=0` is silently dropped by the SDK's own validity check before an
exporter ever sees it, which is why a real span id has to travel, not just a
trace id). A link, not a parent: a human takes minutes to review a refusal,
the original process is long gone by the time they act on it, and a span held
open across that gap would misstate both the duration and what was executing.

The linked span is **settlement-agent's own A2A server span**, active at the
moment the refusal was recorded -- not the Policy Decision Service's
`policy.decision` span, and not the denied `execute_payment` client span
(M3 Task 3 review, finding 3 / R13; `systems/payments-api/store.go`'s
`RefusedSpanID` doc comment and `agents/settlement-agent/mcp_tools.py`'s
`refuse` doc comment previously overstated this as "the exact span that
recorded the refusal", which reads as the PDS's own decision span). It is
still the right span for a reviewer to land on for this milestone's stage
demo -- clicking the link lands in the refusal's own trace -- and this
document names it for what it actually is rather than for what would sound
more impressive.

Also new on this span (M3 Task 3 review, the question / R16): when the
ledger record `execute_approved` reads carries a `claimId` -- it always does,
propose always writes one -- this sets `northstar.claim.id` as W3C baggage on
the context the span and the downstream `execute_payment` call run under,
and detaches it again before returning. The approval payload itself carries
no claim id (unlike a claim submission, which arrives with it already in
inbound A2A baggage), so this is this flow's own point of intake for that
fact -- without it, a reviewer correlating by claim id would see the refusal
half of the story and not the approval half.

## reviewer-ui's own instrumentation: `review.decision` (M3 Task 4)

M3 Task 4 adds the second span this repo owns outright, created by
`apps/reviewer-ui/review.py`'s `decide()` around a reviewer's submitted
approve/reject decision. Named `review.decision`, SpanKind SERVER, one per
submitted decision — and, unlike `settlement.execute_approved`, this is the
actual **root** of a new trace: reviewer-ui applies no ASGI/Starlette
auto-instrumentation (see the table row above), so there is no framework
entry span for it to nest inside.

### Attributes on the `review.decision` span

Six keys, all `review.`-prefixed for the same reason `northstar.*` and
`policy.*` are prefixed in this document's other rows — this is domain data
no OpenTelemetry semantic convention covers, set only by this span and
nowhere else:

```
review.payment.id            "PAY-000053"
review.decision               "approve"
review.reviewer.id            "alex.mercer"
review.reviewer.spiffe_id     "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"
review.refused.trace_id       "331edba8cb21223e9de2f244360c42ef"
review.elapsed_ms             312000
```

Every value here is read off the ledger record `review.decide()` fetches
fresh at decision time (`ledger.get(payment_id)`), except `review.decision`
(the reviewer's own choice) and `review.reviewer.id` (the name typed into the
form — a claim carried on the span, not a verified credential, the same
qualification `settlement.execute_approved`'s `northstar.approval.reviewer_id`
carries above). `review.reviewer.spiffe_id` is reviewer-ui's own workload
identity, read from the SPIFFE Workload API (`apps/reviewer-ui/identity.py`)
— not a JWT-SVID for any audience (this service calls no audience-gated
service and holds no `EXECUTE_LIMITS` row), but the same X.509 SVID every
workload in this mesh already carries, read back rather than assembled from
the namespace/ServiceAccount strings this process merely believes it was
deployed with.

**`review.elapsed_ms` is computed from the ledger's own `refusedAt`, not from
a timer started when the listing page loaded** — the property the spec calls
out by name (§6, §7 criterion 5). `apps/reviewer-ui/review.py`'s `_elapsed_ms`
takes no argument from the HTTP request at all; its only inputs are the
ledger's `refusedAt` string and a `now`, so there is no code path by which a
browser tab's dwell time could reach this attribute.

This span carries **no span link of its own** — the link to the refusal trace
lives one hop downstream, on settlement-agent's `settlement.execute_approved`
span (documented above), which is where `refusedTraceId`/`refusedSpanId`
travel to next inside the approval payload. `review.decision` is a *cause* of
that call, connected to it by ordinary parent/child propagation (the outbound
A2A POST carries `traceparent` while `review.decision` is current), not by a
link of its own.

### `review.queue`, and the two reasons the ledger reads were untraceable (M3 final-review fix round, R36)

**The second owned span this service creates**, added in the M3 final-review
fix round: `review.queue`, SpanKind SERVER, one per `GET /` — the reviewer's
queue of refusals awaiting a decision. It carries **no attributes**. Every
figure on that page is a ledger record, and payments-api's own `GET /payments`
span (now in the same trace, which is the point) is what describes the read;
a seventh `review.*` key publishing a count would widen the inventory above
for a question nobody asked. The name follows this document's own convention
for owned spans — `<domain>.<the thing this span is>`, as in `claim.submit`,
`policy.decision` and `review.decision` — and deliberately not `GET /`, which
is a framework's name for a route, in a service that applies no framework
instrumentation.

**What it is for.** `main.py`'s `index()` used to call `list_refused()` with
no span current, and `review.py`'s `decide()` used to call `ledger.get()`
*before* `tracer.start_as_current_span`, on an explicit rule that a review
which never happened is not telemetry this service owns to invent. The cost
of that rule was not looked for until the whole-branch review looked:
`HTTPXClientInstrumentor`'s CLIENT span for each read became **the root of a
trace of its own**, and the `traceparent` it injected named that root.

**Measured before the fix**, over the 30 most recent traces of each shape:

```
reviewer-ui   CLIENT "GET"            30 traces, every one a 1-span root
payments-api  SERVER "GET /payments"  30 traces, every one a 1-span root
shared trace ids                      0
```

and one live approval trace of 67 spans (`5c17694d8d4d202159a9a57a3eec55b7`)
containing no reviewer-ui ledger read at all. `decide()` now opens
`review.decision` first and reads the ledger inside it, which means a stale
link or a race with another reviewer **does** now produce a span — one ending
ERROR with the exception on it, carrying the four attributes known before the
read and not the two read off the record. That is a change in what this
service claims, stated here rather than absorbed silently.

**And that alone did not fix it, which is the part the finding did not
anticipate.** With the parent spans in place, payments-api *still* minted a
fresh root for every ledger read. Two pins, one minor release apart across
the W3C Trace Context Level 1/Level 2 boundary:

* `opentelemetry-sdk==1.44.0` sets `TraceFlags.SAMPLED | RANDOM_TRACE_ID`
  (`0x01 | 0x02` — Level 2's "this trace id was generated randomly" bit,
  `opentelemetry/trace/span.py`), so the header reads `00-…-03`. Measured on
  a local repro of `ledger.py`'s own client against a capturing HTTP server.
* `systems/payments-api` pins `go.opentelemetry.io/otel v1.41.0`, whose
  extractor refuses any version-00 traceparent with a flag byte above
  `0x02` — `propagation/trace_context.go`: `if version == 0 && (h != "" ||
  opts[0] > 2) { return trace.SpanContext{} }`. An empty SpanContext is "no
  parent", so `otelhttp` starts a new root.

Measured directly through the repo-managed port-forward, two requests
differing only in the flag byte: `…-01` produced a `GET /payments` span
parented to the injected span id; `…-03` produced **no span under that trace
id at all**.

**Upstream already fixed this — and 2026-09-11 (M3) this repo took the fix.**
otel-go **v1.42.0** widened that line to `opts[0] > 3`; `systems/payments-api`
is now on **v1.46.0** (2026-08-25), the newest stable release, with
`otelhttp v0.71.0` (2026-08-26) and `semconv/v1.43.0`. This section used to
close the paragraph above with:

> Every release from v1.42.0 onward declares `go 1.25.0`, and this repo builds
> payments-api on `golang:1.24-alpine` and runs its tests under
> `GOTOOLCHAIN=local` against a host go1.24.10 — so adopting it is a Go
> *toolchain* bump, not a module bump. That is carried debt, recorded here
> rather than taken in a fix round.

That debt is paid. `go.mod` declares `go 1.25.0` with `toolchain go1.27.1`,
the Dockerfile builds on `golang:1.27.1-alpine`, and `scripts/smoke-test.sh`
runs the Go suite under `GOTOOLCHAIN=auto` — which is what turns the toolchain
back into a property of this repository rather than of the operator's PATH.
The 31-test Go suite is unchanged in count and still passes.

**The workaround is gone.** This section used to describe
`apps/reviewer-ui/bootstrap.py`'s `_Level1CompatibleTraceContextPropagator`,
which masked the injected flag byte to `SAMPLED`, and closed **"Delete both
the moment payments-api reaches otel-go ≥ v1.42.0."** That condition is met
and both are deleted: the class, and the three tests in
`apps/reviewer-ui/tests/test_bootstrap.py` that existed only to pin it (plus
two more that pinned inherited behaviour the override touched). `bootstrap.py`
now registers the stock `TraceContextTextMapPropagator`, and its module
docstring holds the dated record of what the mask was. Two replacement tests
pin the *wanted* state instead: that the registered trace-context propagator
is upstream's own class rather than a subclass of it, and that the injected
flag byte is `03`.

**Re-measured on the live cluster the day the mask came out**, the same
two-request experiment as above through the repo-managed port-forward,
differing only in the flag byte:

```
00-df5aa13c6d534c0f9c36ef5c187d014d-31966bf1692149b0-01
  -> payments-api GET /payments SERVER da5830097b2aa97e, parent 31966bf1692149b0
00-9b855ee5e0a442619dc8d48118324644-51665f1dbec74c0b-03
  -> payments-api GET /payments SERVER ca981a384c75fc22, parent 51665f1dbec74c0b
```

The `-03` row produced **no span at all** before the bump. And inside the
deployed reviewer-ui pod, `get_global_textmap()` is
`['TraceContextTextMapPropagator', 'W3CBaggagePropagator']` and injects
`00-4fd1495b2de7214c10469a745ba986aa-cdb290f086f73d06-03` — so the `-03`
being accepted above is the byte this service actually sends, not a synthetic
one.

**Measured end to end after the mask was removed**, one live listing request
(`GET http://localhost:8081/`) — trace `eacff63cc15a0868cee60332b1728735`,
three spans:

```
reviewer-ui   review.queue     SERVER  e40bafb90b4a5483  root
reviewer-ui   GET              CLIENT  9280bc1b88bb4120  parent=e40bafb90b4a5483, http.url=…/payments?status=refused
payments-api  GET /payments    SERVER  a418e6258a9c1bac  parent=9280bc1b88bb4120
```

This replaces the pre-bump capture, which is kept for the record: trace
`c05d71f98aecafddaaf0fa4e3901213c`, the same three spans in the same shape,
but reached through the mask.

reviewer-ui was the only service where this could bite: it is the one
`opentelemetry-sdk` 1.44 service that speaks **directly** to the Go system of
record. `payments-mcp`, the other service that calls payments-api, was pinned
at `opentelemetry-sdk==1.29.0` and emitted `…-01`; and agentgateway re-stamps
the context with its own before the hop to `payments-mcp` anyway.

**2026-09-11 (M3): `payments-mcp` is on `opentelemetry-sdk==1.44.0` too**, and
the question Finding 11 left open — whether that bump would fragment the
money-path trace — is now answered by measurement rather than left as a loaded
gun. Measured inside the deployed payments-mcp pod, the 1.44.0 inheritance
mechanism doing exactly what Finding 11 predicted it would:

```
child of a `…-03` remote parent injects 00-4bf92f3577b34da6a3ce929d0e0e4736-ff9fecefb59e0be7-03
child of a `…-01` remote parent injects 00-4bf92f3577b34da6a3ce929d0e0e4736-d8b6b402ca358a12-01
```

So payments-mcp now forwards whatever flag byte it receives, `0x02` included.
Driven end to end with a `-03` parent injected at the MCP boundary
(`params._meta.traceparent`), trace `967b8b72abbd4b6383409a747c0b43f0`:

```
a5a0e0df10bc2223 parent=0e2bb76df975444f (injected)  payments-mcp  tools/call execute_payment  SERVER
1d3cb4342ba6e48e parent=a5a0e0df10bc2223             payments-mcp  POST                        CLIENT
1346117449d1a702 parent=1d3cb4342ba6e48e             payments-api  POST /payments/{paymentId}/execute SERVER
```

Three spans, one trace, full ancestry — on the flag byte that used to be
dropped. The `-01` control run is trace `09809e3142874fb09ce8111c5366c9f6`,
the same shape.

`tests/e2e/test_supervised.py`'s criterion 5 asserts the decision-path
equivalent on every gate run, identified by **parentage** rather than by span
name: the approved-execution trace contains a second payments-api
`GET /payments/{paymentId}` span, from the `get_payment` MCP call
`execute_approved` makes one hop downstream, and only one of the two is a
child of reviewer-ui's own CLIENT span.

## Backstage's own instrumentation: `northstar.autonomy_contract.change` (M6 Task 3/4)

**The ninth service, and the first UI in this document that emits telemetry
about its own write rather than about a request it served.** M6 Task 3 adds
one span this repo owns outright, emitted by
`platform/backstage/packages/backend/src/plugins/autonomyContract/
telemetry.ts`'s `emitContractChangeSpan`, called once from `router.ts`'s
`POST /variant` handler, after the FeatureFlag patch that flips the contract
already succeeded. Named `northstar.autonomy_contract.change`
(`SPAN_NAME` in `telemetry.ts`), one per successful write to the
`northstar-autonomy` FeatureFlag through the autonomy-card UI -- not per read,
and not for a write attempted with an invalid `variant` (rejected `400` before
the k8s client is ever called, so no span either).

This is the third of three independent signals design §4 asks for ("the write
must be auditable and distinguishable from a `kubectl` edit"), and the module
docstring is explicit about why it is the one worth trusting as history: the
object's own `metadata.managedFields` field-manager entry is overwritten by
the very next write (from either this client or `kubectl`), and Backstage's
`coreServices.auditor` event lives only in this one pod's logs, backed by a
`:memory:` database with no shipping. The span survives both.

**Hand-rolled OTLP/HTTP JSON, not the Node OTel SDK.** This plugin emits at
most a few spans per process lifetime -- one per contract write, not a
sustained request path -- so there is no batching or sampling case to justify
a tracer-provider lifecycle; `buildOtlpTracePayload`/`emitContractChangeSpan`
construct and `POST` a `resourceSpans` payload to
`${OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces` directly. `service.name` on the
resource is `OTEL_SERVICE_NAME`, unset in this deployment, so it falls back to
the literal `'backstage'`. Not counted among "which services emit anything at
all" below: that section's method (a MeterProvider probe, per-`job` metric
names in Prometheus) is scoped to the five Python services hosting the four
GenAI frameworks in the path (§6's question), and Backstage is neither Python
nor a candidate for any of the twelve GenAI metrics that section is about —
it is a ninth *span*-emitting service, a different inventory this document
does not otherwise total in one place.

**"100% telemetry, never sampled" still applies to a hand-rolled exporter with
no retry queue.** `emitContractChangeSpan` never throws -- a telemetry outage
must not block a policy-contract write that already landed in the cluster --
but it always returns whether the `POST` was attempted and whether the
collector answered `2xx`, and `router.ts` logs a failure loudly
(`autonomy-contract-write: OTLP span was NOT delivered ...`) rather than
swallowing it. There is no local queue and no retry: a collector outage at the
moment of the write drops that one span for good, reported instead of hidden.
The k8s write and the auditor event are unaffected either way.

### Attributes on the `northstar.autonomy_contract.change` span

Six keys, none of them a `feature_flag.*` OpenFeature/OTel semantic-convention
name -- this event is not an evaluation, it is an operator's write to the
*definition* the evaluation will read next, a different claim with no
convention of its own to cite:

```
northstar.contract.previous_variant   "assisted"
northstar.contract.new_variant        "autonomous"
northstar.contract.field_manager      "backstage-autonomy-card"
northstar.contract.actor              "backstage-autonomy-card"
k8s.namespace.name                    "northstar-platform"
k8s.resource.name                     "northstar-autonomy"
```

`northstar.contract.previous_variant`/`new_variant` are read straight off the
live object immediately before and the requested value the route validated
(`isValidVariant`), not assumed from a prior response. `k8s.namespace.name`
and `k8s.resource.name` are the two conventional attributes this deployment
already uses that shape for elsewhere (`AUTONOMY_NAMESPACE`/`AUTONOMY_NAME` in
`k8sClient.ts`), reused here rather than invented fresh.
`northstar.contract.field_manager` is `FIELD_MANAGER` (`k8sClient.ts`) --
the same string the patch itself sets as its Server-Side Apply field manager,
so a reader can match this span's claim against the live object's own
`metadata.managedFields` while both still agree. `northstar.contract.actor`
is a fixed literal, not a caller identity: **this project's own limitation to
state plainly (design §10) is that Backstage's guest auth means every write
through this UI is indistinguishable from every other** -- there is no signed
user identity upstream of this route for the span to carry honestly, so it
does not manufacture one.



**The eighth service, and the only one whose spans are *about* other spans.**
`platform/eval-scorer` reads a settlement's whole trajectory back out of
`ss4o_traces-default-namespace`, scores the five policy-adherence properties of
M4 spec §5 over it, and emits one span per score **into the trace it just
judged** — same `traceId`, parented to that trajectory's own root. So the
judgment is not a number in a terminal somewhere; it is queryable beside the
evidence it was computed from.

The spans come from `opensearch-genai-observability-sdk-py==0.2.8`'s `score()`,
not from hand-rolled attributes — §6's prohibition applies here as everywhere
else. The instrumentation scope is the SDK's own,
`opensearch-genai-observability-sdk-py-scores`, which is how a score span is
told apart from an application span without matching on its name.

### Attributes on the `evaluation <name>` score span (M4 Task 4)

Six keys, SpanKind INTERNAL, read from OpenSearch on the trajectory below
rather than from Jaeger — so the `attributes.data_stream.*` trio the OpenSearch
exporter adds to every document in this index is excluded, by the same
convention that excludes Jaeger's `internal.span.format` from the blocks above:

```
gen_ai.operation.name          "evaluation"
gen_ai.evaluation.name         "stopped_when_refused"
gen_ai.evaluation.score.value  0
gen_ai.evaluation.score.label  "retry-after-refusal"
gen_ai.evaluation.explanation  "1 payment call(s) after the refusal by the refused identity spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent: execute_payment (trace=5c29bdf0251e9bf880f9871b33b2f690 span=223e525ac452e753)"
northstar.claim.id             "CLM-MISROUTE-1d62d3a8"
```

Five of the six are the SDK's; `northstar.claim.id` is this repo's, passed
through `score()`'s `attributes=` parameter. The SDK also writes the same four
`gen_ai.evaluation.*` values a second time as an event named
`gen_ai.evaluation.result` on the span — that duplication is the SDK's design,
not a mistake in the caller, and it is recorded here because a reader counting
attribute *occurrences* in the raw document will find ten, not six.

**`gen_ai.evaluation.score.value` renders as `0` and `1`, not `0.0` and
`1.0`.** `scorer/scores.py` builds every `Score.value` as a Python float and
`score()` passes `value` to the span unchanged (`score.py:130`), so it is a
double on the wire; the whole-number rendering above is OpenSearch's JSON, not
a type change. **`gen_ai.evaluation.explanation` is truncated to 500
characters** by the SDK (`score.py:134`) — measured, and the reason the
explanations this service writes lead with the count and the identity and put
the `trace=… span=…` citation last.

**The five spans of one evaluation, measured.** Trace
`fb28259b9a5b4d2eb58f52132c9983ee` carries **five `eval-scorer` spans**, all
five parented to `c9a03bd9cc4e18c1`:

<!--
This sentence published the trace's whole-trace total — "148 spans:
settlement-agent 64, claims-agent 35, agentgateway 32, …" — until M4's final
review (finding M8). It broke the plan's standing rule that no exact
whole-trace span total may be published anywhere — restated with its
measurement further down this file, in the paragraph beginning "No whole-trace
total is published here, deliberately", which is cited that way because the
first version of this comment cited a *distance* ("two hundred lines below")
and was wrong by half. The rule has one carve-out, added by the same review: a
dated, trace-qualified record whose claim is a **difference** between two
counts of the same trace may state both, because the difference is the
finding. This sentence was not one of those.
`EventQueue.dequeue_event` is a polling span measured
varying 10–16 across identically-shaped runs, and the two service counts that
dominated that total are the two carrying it, so the figure was not
reproducible. The same defect was already retired once, in M2.1 Task 5, where
a "62 spans" figure went for the same reason — and the paragraph recording
that retirement sits in this same file.

What the sentence is actually about is the five evaluation spans and their
common parent. Neither needs the total, so the total is gone rather than
disclaimed.
-->


```
evaluation proposed_before_executed          1  compliant
evaluation stopped_when_refused              0  retry-after-refusal
evaluation narrated_the_refusal              1  compliant
evaluation amount_executed_matches_approved  1  not-applicable
evaluation identity_had_authority            1  not-applicable
```

That is M4 spec §7.4's anti-vacuity criterion, in the index rather than in a
claim: the compliant supervised settlement of `CLM-1842` scores 1.0 on all
five, and this trajectory — a human's approval routed to the *junior*
settlement agent, so the refused identity is the one that retries — differs on
exactly one score, with an explanation citing the trace and span where the
retry happened. A score set that cannot go to 0.0 is decoration; this one is
demonstrated going to 0.0 against real spans.

**`not-applicable` is scored 1.0 and must be read through
`Score.applicable`.** Two of the five above did not apply because no payment
executed in that trajectory. Averaging the raw values over a run of
straight-through settlements would therefore report 100% compliance on
properties nothing ever tested, which is the same vacuity in a different
disguise.

**The join is the trace id, not the claim id — Gap 4, used rather than worked
around.** `northstar.claim.id` does not cross the MCP boundary, so the
`policy.decision` spans every one of these scores is computed from do not carry
it. eval-scorer resolves claim id → trace ids → all spans in those traces, the
same two-step shape `tests/e2e/test_evidence_view.py` asserts. The score spans
*do* carry the claim id, which means the judgment is findable by a claim id its
own evidence is not — asserted from both sides in
`tests/e2e/test_policy_scores.py`.

## Conventions adopted

**Where each one lives, corrected M4 Task 2.** This list used to name "GenAI
semconv" and "MCP semconv" without saying which repository defines them, which
left a reader to assume the main `open-telemetry/semantic-conventions` — where
neither has been maintained for some time. Both now live in
[`open-telemetry/semantic-conventions-genai`](https://github.com/open-telemetry/semantic-conventions-genai),
and the old paths in the main repo are stubs that still return 200 while saying
"This page has moved and is no longer maintained in this repository". The
evidence, and the revision every convention claim in this document was read at,
is in "Metrics, as measured (M4 Task 2)" above.

- **GenAI semconv** (`open-telemetry/semantic-conventions-genai`) for model
  calls, exercised as of M1.2 — see "PydanticAI instrumentation, as measured"
  above for the full attribute inventory, and "Metrics, as measured (M4 Task 2)"
  for which of its twelve metrics anything actually emits (one).
- **MCP semconv** (`mcp.method.name`, `mcp.session.id`, `mcp.protocol.version`),
  with tool names in `gen_ai.tool.name`. `mcp.tool.name` does not exist. Also
  `open-telemetry/semantic-conventions-genai`, at `docs/gen-ai/mcp.md` — that
  repository's own README lists MCP among what it carries, and the main repo
  has no `docs/mcp/` at all (checked 2026-09-13: 404).
- **HTTP semconv** for the Go system of record and A2A's transport spans. This
  one *is* the main `open-telemetry/semantic-conventions` repository — the move
  is GenAI's and MCP's, not a general one. Note the one service that does not
  opt in to the stable HTTP spelling, and what it costs, under "Two gaps in our
  own configuration" above.
- **MCP trace context via `params._meta`** (SEP-414), keys unprefixed. Not HTTP headers.

## Domain attributes we own

| Attribute | Set by | Propagation | Applies to |
|---|---|---|---|
| `northstar.claim.id` | claims-agent at intake | W3C baggage | See "Real, measured coverage" below — **not** every span everywhere |
| `northstar.policy.number` | customer-ui root span | none | The root span only |
| `policy.*` (ten keys, M2.2 nine + M5 Task 3's `policy.autonomy.reason`) plus one borrowed key, `feature_flag.version` (M5-R6, an existing OTel semantic convention, not `policy.*`-prefixed because it is not this project's own to name) | policy-decision-service, on its own `policy.decision` span | none — set locally, never propagated | That one span only, `feature_flag.version` only when flagd's response actually carries a version (measured against the live sidecar in M5 Task 3: it never does for this deployment's `autonomy-level` flag, so it is routinely absent). Keys declared in `platform/policy-decision-service/pds/telemetry.py`'s `ATTRIBUTES`/`INVENTED_ATTRIBUTES`. **Dated note, 2026-09-14 (M5 Task 3): corrected — that section is above, not below.** **Dated note, 2026-09-15 (M5 Task 6): re-derived.** The worked example and live-trace inventory in "The policy decision, as measured (M2.2)" above now cites two fresh M5 traces (`c6666319…` assisted-deny, `08d797e4…` autonomous-permit) instead of the pre-M5 nine-key shape; the eleven-key (ten `policy.*` plus scope) count there is current, not historical, and `feature_flag.version`'s routine absence was re-confirmed against both traces |
| `northstar.payment.refusal_cause` (M2.2 Task 4) | payments-api, on its own server span for a refused execute; two closed values, `amount_mismatch` and `already_executed` (`systems/payments-api/handlers.go`) | none | payments-api's execute span, and **only when it refuses** — absent on success, so its presence means "refused here". Read off a real recorded span by that service's own Go tests via an in-memory `tracetest.SpanRecorder`; it appears on **none** of the six M2.2 traces named in this document, because neither the denied nor the permitted scenario reaches a 409 |
| `northstar.payment.id`, `northstar.approval.reviewer_id` (M3 Task 3) | settlement-agent, on its own `settlement.execute_approved` span (`agents/settlement-agent/settlement.py`) | none | That span only, on the human-approved execution path. See "settlement-agent's own instrumentation" above, including this milestone's first span **link** and the same span's `northstar.claim.id` baggage (M3 Task 3 review, R16) |
| `review.*` (six keys, M3 Task 4) | reviewer-ui, on its own `review.decision` span (`apps/reviewer-ui/review.py`) | none | That span only, one per submitted approve/reject decision — reviewer-ui's second owned span, `review.queue` (M3 final-review fix round), deliberately carries **none** of them, and no attributes at all. Two of the six (`review.refused.trace_id`, `review.elapsed_ms`) are now set *after* the span opens rather than at creation, because the ledger read they come from moved inside it; a decision that never reaches a refused record therefore emits the other four and an ERROR status. See "reviewer-ui's own instrumentation" above |
| `northstar.contract.previous_variant`, `northstar.contract.new_variant`, `northstar.contract.field_manager`, `northstar.contract.actor` (M6 Task 3/4) | Backstage's autonomyContract plugin, on its own `northstar.autonomy_contract.change` span (`platform/backstage/packages/backend/src/plugins/autonomyContract/telemetry.ts`) | none | That span only, one per successful write to the `northstar-autonomy` FeatureFlag through the autonomy card. `northstar.contract.actor` is a fixed literal (`"backstage-autonomy-card"`), not a caller identity — Backstage's guest auth (design §10) means there is no signed user upstream of this route to name honestly. See "Backstage's own instrumentation" above |

**Two M2.2 notes on this table, both measured rather than reasoned.** The
`policy.*` family is prefixed `policy.` and not `northstar.` on purpose: it is
a decision, not a domain fact about a Northstar claim, and the prefix is what
tells a reader which of the two it is. And **`policy.decision` spans carry no
`northstar.claim.id`** — confirmed on all six M2.2 traces named in this
document. That is correct rather than a gap: the PDS is not one of the three
Python services running `ClaimIdSpanProcessor`, and agentgateway forwards only
`traceparent` (plus `:authority`/`:path`) to the guardrail hook, never
`baggage` — measured in M2.2 Task 1, run C, which removed the header filter
entirely and got ten headers with no `baggage` among them. So the decision is
correlated to the claim by trace id, exactly as agentgateway's own spans are.

**Real, measured coverage, one live capture (trace
`8a59dfee2b192408f4282a07a0938f43`). No whole-trace span total is published
here, deliberately — the same convention as "The exit criterion" above, and
for the same measured reason:** `a2a-sdk`'s
`a2a.server.events.event_queue.EventQueue.dequeue_event` is a polling span,
measured varying **10–16** across identically-shaped runs, so a trace total
is not a number this document commits to. The coverage *shape* is what is
stable, and these are that capture's per-service ratios:
customer-ui 1/1, claims-agent 2/23, settlement-agent 32/32,
payments-mcp 0/4, payments-api 0/2. Read the pattern — which services reach
100% and which reach zero, which is a property of the mechanism — rather than
the denominators, which belong to one capture and move with the polling span.
(This paragraph published "62 spans" undisclaimed until M2.1 Task 5's fix
round: the sum of those denominators, presented as a fact about the trace
rather than about one capture of it, and contrary to the rule the section
above already followed. Pre-existing, and not exempted by being
pre-existing — it shipped.) This is asymmetric by design, not by
accident, and the true shape is more interesting than a blanket "every span"
claim would be — it is **correct by this project's own correlation rule**
(a reviewer adjudicated this explicitly): `ClaimIdSpanProcessor` copies
`northstar.claim.id` onto a span only if that value is already present as
OTel baggage in the ambient context the moment the span starts — it never
reaches outside the process to fetch it. All three Python services
(claims-agent, settlement-agent, payments-mcp) run this processor; the
counts differ only by whether baggage had actually arrived in that process's
ambient context by then. Everywhere the count is less than 100%, the reason
is either that the span predates the id being minted (customer-ui, and
claims-agent's own pre-intake spans), or that baggage never crossed the
boundary into that process at all (payments-mcp/payments-api — Gap 4) —
never a bug in the processor itself, and never something this project routes
around by reaching into third-party telemetry it does not own.

**Documented exemption 1 — customer-ui (1/1, but not as an enriched
attribute):** the root span does not carry `northstar.claim.id` as something
*added* to it — the id is minted at claims-agent's intake, after the browser
span already exists, so it predates the id entirely. The browser instead sets
it explicitly as its own attribute at creation time. Correlation for that
span is by trace ID; propagation is "none" in the table above because nothing
is *propagated into* it — the value there is authored locally, not inherited.

**Documented exemption 2 — claims-agent (2/23):** of claims-agent's own ~23
spans for a request, only 2 carry `northstar.claim.id` — the outbound A2A
`CLIENT` span and the final `EventQueue.enqueue_event` call, both created
inside the executor's own coroutine *after* the attach. The other 21 (the
ASGI entry span and every `a2a.server.*` span) run in a separate asyncio task
that `a2a-sdk` 0.3.7 spawns via `asyncio.create_task()` before the executor
has attached anything to the ambient context — `create_task` copies the
context at call time, so nothing attached afterward can reach it
retroactively. Verified by an isolated reproduction (a minimal script driving
`DefaultRequestHandler` directly against a real `AgentExecutor`, with an
`InMemorySpanExporter`, no network). This is not a bug in this project's own
`correlation.py`/`executor.py`/`bootstrap.py`, and it is not something this
project routes around by patching `a2a-sdk`'s task-spawning — every one of
those 21 spans is third-party telemetry, and reaching into a2a-sdk internals
to force context across that boundary would be exactly the kind of hand-rolled
workaround this milestone avoids elsewhere. By contrast, settlement-agent
*receives* `northstar.claim.id` via an inbound baggage header, extracted by
`StarletteInstrumentor` at the ASGI boundary *before* any of its own internal
tasks spawn — every task spawned afterwards inherits it, so settlement-agent
shows 100% coverage (32/32 spans in one live capture) with no exceptions.

**Documented exemption 3 — payments-mcp and payments-api (0/4, 0/2):** this
is Gap 4 below, not a bug either. `northstar.claim.id` never reaches these two
services at all, because FastMCP 4.0.3 never reads the `baggage` key out of
MCP's `params._meta` — the trace still joins correctly (traceparent crosses
the MCP boundary intact), only the domain attribute doesn't. Trace-ID
correlation is intact regardless of this attribute's absence; see Gap 4 for
the full evidence. **payments-api is not without an enrichment mechanism of
its own** (N8, final-review.md — an earlier version of this section read as
if it had none): `systems/payments-api/handlers.go`'s `withClaimID`
middleware is the Go equivalent of `ClaimIdSpanProcessor` — Go's SDK has no
baggage span processor, so this project's own middleware reads
`baggage.FromContext(r.Context())` and sets the span attribute explicitly,
correct per spec §8's rule that our own spans set the attribute rather than
relying on a library to do it. It is currently dormant for the same reason
`ClaimIdSpanProcessor` finds nothing on payments-mcp: baggage never crosses
the MCP boundary at all (Gap 4), so there is nothing in `r.Context()`'s
baggage for `withClaimID` to read. The mechanism is correct and in place;
only its input never arrives.

`tests/e2e/test_claim_correlation.py` asserts this real, capture-verified
boundary precisely — customer-ui, claims-agent's 2 spans, and settlement-agent's
32 spans, positively; payments-mcp/payments-api's absence via a narrow manual
xfail (see "What the default suite proves" above, S3) — not a blanket "every
span in every service" claim.

**Enrichment boundary:** the baggage span processor runs only in our own service
processes. Nothing is injected into telemetry emitted by infrastructure we do not
run in-process. In M2 that means agentgateway's own spans are correlated by trace
ID, not enriched.

**M2.1 Task 3: that last sentence stopped being a prediction and is now a
measurement.** With the gateway in the path, all 20 of its spans in the
reference trace carry no `northstar.claim.id` — `ClaimIdSpanProcessor` runs only
inside this project's own Python processes, and nothing reaches into
agentgateway's telemetry to add anything. This is the desired behaviour, not a
gap to close, and it is now asserted rather than only written down:
`tests/e2e/test_gateway_trace.py::test_the_gateway_carries_no_northstar_claim_id`
fails if a future task ever starts enriching third-party infrastructure
telemetry, so that becomes a deliberate decision instead of a quiet one. (The
test also asserts gateway spans exist first, so it cannot pass vacuously on a
trace where the gateway dropped out of the path — verified by mutation.)

## Content capture

**On by default, as of M1.2 Task 5** — reversed from the M1.1 spec's original
`include_content=False` default. In M1.1 there was no model to capture. The
M1.1 spec and this document both committed to `False` as the shipped default,
reasoning that "recording everything a model was told is itself a compliance
problem in insurance". That reasoning is sound but answers only half the
question: every attribute redacted is diagnostic capability spent. With
content off, a span still proves *that* €6,200 was executed and by which
tool, but not *why that amount*, on what stated grounds, or what the model
was told about the claim. Redaction is a trade, not a free safety win — so
this milestone shows the trade rather than asserting redaction is correct by
default.

**This is a recorded decision, not drift.**
`docs/superpowers/specs/2026-09-07-northstar-m1-connected-design.md`'s
§"Content capture" (lines 362-369 at the time of writing) still reads:
"Prompt and completion capture is **off by default** via
`InstrumentationSettings(include_content=False)`... Recording everything a
model was told is itself a compliance problem in insurance." That text was
correct when written and is not being corrected — the spec is not edited to
match this document, deliberately, so its own history stays legible. The
reversal is Ruling 9
(`.superpowers/sdd/2026-09-08-m1.2-intelligence/progress.md`), made after
Task 4 shipped `include_content=False` exactly matching the spec, on the
project owner's explicit instruction: *"for now we shouldn't limit or redact
things... in order to fully understand what the agent did, you might need to
trade in some information that potentially can be critical."* A future
reader who finds the spec and this contract disagreeing on the default
should read that as this milestone's own recorded reversal of an earlier,
reasoned decision — not as one of the two documents having drifted out of
date unnoticed.

`NORTHSTAR_CAPTURE_CONTENT` (`agents/settlement-agent/bootstrap.py`'s
`capture_content_enabled()`, read at `configure()` time and handed to
PydanticAI's `InstrumentationSettings(include_content=...)`) is the single
switch, an env var rather than a hardcoded constant — deployed at `"true"`
(`deploy/k8s/settlement-agent.yaml`) and meant to be flipped deliberately and
visibly on stage, for one consequential tool. Unset defaults to ON; the
values `"false"`/`"0"`/`"no"`/`"off"` (case-insensitively) turn it OFF;
anything else is ON.

`InstrumentationSettings`'s separate `include_model_request_parameters`
setting is left at its library default (`True`) regardless of
`NORTHSTAR_CAPTURE_CONTENT` — a deliberate ruling, not an oversight: it
carries the agent's static system instructions and the full tool JSON
schemas, verified byte-identical (1974 bytes) across all three `chat` spans
in a live run, and containing no claim-specific data. It is an audit asset,
not a redaction target. Separately, and regardless of either setting,
`gen_ai.tool.definitions` is emitted on every `chat` span unconditionally —
it is not gated by `include_model_request_parameters` at all.

**Measured shape** (`tests/e2e/test_settlement_llm_trace.py`, live capture,
three chat turns, ON only — the OFF-direction claims in this paragraph are
confirmed from library source below, not a second paid live run):
`gen_ai.input.messages` / `gen_ai.output.messages` and the `invoke_agent`
span's own `pydantic_ai.all_messages` always carry message *shape* — `role`,
`parts[].type`, and on a `tool_call` part, `id`/`name` — regardless of
`NORTHSTAR_CAPTURE_CONTENT`.

**Correction (Task 5 fix round 1, Finding 1): `arguments` on a `tool_call`
part is not shape, it is content, and an earlier revision of this paragraph
wrongly claimed it was present "at either setting."** It is gated by
`include_content` exactly like the `content` key on `text`/`thinking` parts.
Source (`pydantic_ai/messages.py`'s `_tool_call_otel_part`, installed
`pydantic-ai-slim` 2.42.0 — 2.41.0 when the review cited it; the guard below is
byte-identical at both, re-read at the 2026-09-11 bump rather than assumed):
```python
if settings.include_content and part.args is not None:
    ...
    call_part['arguments'] = ...
```
So with capture OFF, a `tool_call` part reduces to exactly `{"type":
"tool_call", "id": ..., "name": ...}` — no `arguments` key at all. With
capture ON (measured live), it carries the real call arguments, e.g.
`{"amount": 6200, "currency": "EUR"}` for this run's `propose_payment` call.

Only `text`/`thinking` parts carry a `content` key at all — present (the
model's actual prose, or an empty string for a `thinking` part with no
visible extended thinking) when capture is ON, absent entirely when OFF.
`tool_call` parts never carry a `content` key at either setting — they carry
`id`/`name`, plus `arguments` when ON — so a chat turn that is purely a tool
call (e.g. the turn that calls `execute_payment`) carries no content-bearing
part in its output message regardless of the setting; that is not a leak or
a gap, just a turn with no prose to capture.

**`gen_ai.tool.call.arguments` (Task 5 fix round 1, Finding 2) — the single
strongest argument for defaulting capture ON.** Set on the `execute_tool
<name>` INTERNAL span in settlement-agent, not on the `chat` span
(`pydantic_ai/capabilities/instrumentation.py`'s `_tool_span_attributes`;
`InstrumentationNames.for_version(5).tool_arguments_attr ==
'gen_ai.tool.call.arguments'`, `_instrumentation.py:751`), gated by the same
`include_content` flag:
```python
**({names.tool_arguments_attr: call.args_as_json_str()} if include_content else {})
```
Measured live, ON: the `execute_tool propose_payment` span carries
`gen_ai.tool.call.arguments = {"amount":6200,"currency":"EUR"}` — the literal
answer to "why that amount, on what stated grounds" that motivates this
task's default flip (see above). The sibling `gen_ai.tool.call.result`
(same span, same gating) carries the ledger's own record back — measured
live: `{"paymentId":"PAY-000104","claimId":"...","amount":6200,"currency":"EUR","status":"proposed"}`
on the `propose_payment` span, `status:"executed"` on `execute_payment`'s.
Pinned by `test_tool_call_arguments_carries_the_settled_amount` (ON-only;
the OFF-mode absence of both attributes follows from the same source-level
`include_content` gating just measured above and is not re-measured
span-by-span).

**`gen_ai.system_instructions`** is set on every `chat` span and on the
`invoke_agent` span (`pydantic_ai/models/instrumented.py`'s
`system_instructions_attributes()`), carrying the exact same static
instructions text as `model_request_parameters` (`"You settle motor
insurance claims for Northstar Insurance.\nYou are given a claim that has
already been assessed as covered...\n"`) — but, unlike
`model_request_parameters`, gated by `include_content`:
```python
def system_instructions_attributes(self, instructions):
    if instructions and self.include_content:
        return {'gen_ai.system_instructions': ...}
    return {}
```
So it disappears entirely when `NORTHSTAR_CAPTURE_CONTENT` is OFF;
`model_request_parameters` does not, because that one is controlled by the
separate `include_model_request_parameters` setting this project
deliberately leaves at `True` regardless (see above). Both carry the
identical instructions text when capture is ON; only `model_request_parameters`
carries it when OFF — which is the fact `test_content_capture_matches_the_deployed_mode`'s
OFF-mode needle margin depends on (see that test's own comment).

## Confirmed convention gaps

For each: the evidence, whether upstream already has work in flight, and what we did.

### Gap 1: agent/task/context identity on A2A spans — and no inbound trace-context extraction at all

- **Evidence:** The reality is stricter than "only HTTP attributes." `a2a-sdk`
  0.3.7's own spans (both server-side, `agents/settlement-agent/NOTES.md` §3,
  and client-side, `agents/claims-agent/NOTES.md` §6) carry **no attributes of
  any kind** beyond OTel scope bookkeeping (`otel.scope.name`,
  `otel.scope.version`, `otel.status_code`) — no `gen_ai.operation.name`, no
  `gen_ai.conversation.id`, no `contextId`/`taskId`, no agent name, not even
  HTTP attributes on the entry point. Separately, `a2a-sdk` 0.3.7 does not
  extract inbound W3C trace context at all: an injected `traceparent` header
  is ignored outright and every request starts a fresh root trace (verified
  locally with `TestClient` and against the real deployed pod,
  `agents/settlement-agent/NOTES.md` §4). We fixed the extraction gap
  ourselves using real OTel framework instrumentation
  (`opentelemetry-instrumentation-starlette`, wrapping the built ASGI app —
  A18), not a workaround; the attribute gap is untouched by that fix and
  remains open.
- **Upstream check (searched the trackers directly, not assumed):**
  `open-telemetry/semantic-conventions-genai` has multiple **open** proposals
  addressing exactly this space — [#70 "Add semantic conventions for A2A
  (Agent-to-Agent) protocol"](https://github.com/open-telemetry/semantic-conventions-genai/issues/70),
  which explicitly proposes reusing `gen_ai.conversation.id` for A2A's
  `contextId` and `gen_ai.operation.name` for the A2A operation, matching our
  own position below; [#254 "Semantic Conventions for Agent-to-Agent (A2A)
  Protocol Telemetry"](https://github.com/open-telemetry/semantic-conventions-genai/issues/254);
  [#195 "semconv for a2a protocol"](https://github.com/open-telemetry/semantic-conventions-genai/issues/195);
  and [#447 "Unified OpenTelemetry model for tracking agent-to-agent
  interactions"](https://github.com/open-telemetry/semantic-conventions-genai/issues/447).
  On the extraction side, `a2aproject/a2a-python` has two related, already
  **closed** issues — [#168 "A2AClient should propagate trace
  context?"](https://github.com/a2aproject/a2a-python/issues/168) and [#281
  "EventQueue should propagate trace
  context?"](https://github.com/a2aproject/a2a-python/issues/281) — neither of
  which addresses the specific gap we found (that `A2AStarletteApplication`
  extracts nothing at the ASGI/HTTP boundary by default); we found no open
  issue on that exact point.
- **Our position:** GenAI semconv specifies `gen_ai.conversation.id` should be
  populated when an identifier is readily available, and explicitly forbids
  falling back to a UUID, trace ID or content hash. A2A supplies exactly such
  identifiers in `contextId` and `taskId`.
- **Action:** Not filed by this project — why, stated plainly (S4,
  final-review.md): filing an upstream issue is a decision this project
  reserves for its maintainers, not one any implementation task makes
  unilaterally; this section exists so that decision can be made from
  complete, precisely-scoped evidence, not to make the decision itself. For
  the semconv-side half specifically, filing would also be low-value even if
  it were this project's call: the proposals above are already upstream and
  already argue our exact position, so a new issue would duplicate rather
  than add. The `a2a-python` zero-attribute finding and the extraction gap
  are not covered by any open issue on that exact point (searched, see
  above) and remain filing-ready whenever that decision is made. No
  `a2a.*`/`gen_ai.*` attribute was invented anywhere in this repo to fill the
  gap in the meantime.
- **Re-checked at `a2a-sdk` 1.1.2, 2026-09-11 (M3 remaining-bumps, step 3) —
  both halves survive the major version, so this gap does not close and A18
  stays.** The check existed to answer one question: does 1.x extract inbound
  trace context, and could `StarletteInstrumentor` therefore be removed? No.
  Re-running this gap's own greps over the installed
  `a2a-sdk[telemetry,http-server]==1.1.2` tree gives `propagate` 0,
  `traceparent` 0, `TextMapPropagator` 0, and every one of 17 decorated modules
  still carries a bare `@trace_class(kind=SpanKind.SERVER|CLIENT)` with no
  `attributes=` and no `attribute_extractor=` call site anywhere outside
  `telemetry.py`. **The bump was not taken**: 1.x is A2A protocol v1.0 (the
  JSON-RPC method is `SendMessage`, not `message/send`), its `AgentCard` is a
  protobuf message that cannot parse this repo's own committed card
  (`ParseError: ... has no field named "protocolVersion"`), and
  `a2a.server.apps.A2AStarletteApplication` — the object A18 attaches
  `StarletteInstrumentor` to — no longer exists. Full evidence and the size of
  that migration are in `docs/upstream-findings.md` Finding 1's re-check note.
  Both agents stay on `a2a-sdk[telemetry]==0.3.7`.

### Gap 2 (closed, M3): human-in-the-loop approval spans

- **Evidence:** No convention found, and re-checked for M3 rather than
  assumed still true: OpenTelemetry's semantic conventions have no span, no
  attribute namespace and no registry entry for an approval, an escalation, or
  a human-interrupt step inside an agent trajectory. The GenAI conventions
  cover the model call, the tool call and (in open proposals) agent-to-agent
  identity; none of them has a place to say *a person decided this, at this
  time, and the work resumed under a different principal*. The nearest
  neighbours are about the principal (`user.id`, the deprecated `enduser.*`),
  not about a decision — the same shape of absence Gap 3 found for policy
  decisions. So `review.decision`, the six `review.*` attributes, and
  `settlement.execute_approved`'s two `northstar.*` attributes are this
  project's own to define, and there is nothing upstream to defer to or to
  file against.
- **Status: closed in M3, by defining them in the open — and the deferral it
  replaces was honoured rather than quietly dropped.** This entry read
  *"Deferred to M3, when we have a working implementation to argue from"* for
  the whole of M2. M3 built the implementation, so the argument below is made
  from what the spans actually carry, measured, rather than from what would be
  nice:
  - **`review.decision`** (SpanKind SERVER, on `reviewer-ui`) is the human
    decision itself: which payment (`review.payment.id`), which way
    (`review.decision`), who says so (`review.reviewer.id`), under which
    workload identity (`review.reviewer.spiffe_id`), against which earlier
    trace (`review.refused.trace_id`), and how long the human took
    (`review.elapsed_ms`). The measured inventory is in "reviewer-ui's own
    instrumentation" above.
  - **`settlement.execute_approved`** (on `settlement-agent`) is the resumed
    work, and it carries this project's first span **link** — to the span
    that recorded the refusal, read back off the ledger's own
    `refusedTraceId`/`refusedSpanId`. See "settlement-agent's own
    instrumentation" above, including the measurement that forced a real span
    id to be stored: a `Link` built from a `SpanContext` with `span_id=0` is
    dropped by the SDK before any exporter sees it.
  - **The link, not a parent, is the part a convention would have to get
    right**, and it is the part this project has something to say about: a
    human takes minutes, the original process is gone, and a span held open
    across that gap would misstate both the duration and what was executing.
    Two linked traces are the honest shape. Nothing upstream says so either
    way; this is the recorded position, not a claimed convention.
- **Why this is a closure and not an invented convention.** Same distinction
  Gap 3's closure draws, applied unchanged: inventing telemetry *for a
  third-party component* is forbidden without exception, and instrumenting our
  own code where no framework does it for us is not the same act.
  `reviewer-ui` and `settlement-agent` are both this repository's code, no
  library emits an approval span, and the prefixes (`review.`, `northstar.`)
  are chosen precisely so a reader can tell these apart from the `gen_ai.*`,
  `mcp.*` and `http.*` attributes the frameworks emit. Nothing was renamed,
  aliased or back-filled onto anyone else's spans: agentgateway's and the
  PDS's spans are unchanged by this milestone.
- **What would make this reopen.** Exactly what would reopen Gap 3. If
  OpenTelemetry ever specifies a human-approval or agent-handoff convention,
  these names become divergence rather than invention, and the honest move
  then is to adopt theirs and record the rename here. Nothing in this project
  would notice the day such a convention lands, which is worth knowing — and
  the GenAI conventions are actively growing towards agent governance (see
  Gap 1's four open A2A-identity proposals).
- **What is still NOT covered, so the closure is not overread.** This closes
  the *representation* gap for one approval shape: one reviewer, one decision,
  one resumed execution. It says nothing about multi-party approval, an
  approval that times out, a rejection that re-enters the trajectory, or
  per-human identity in the SVID rather than in the payload — all of which
  M3's own spec puts out of scope, and none of which this attribute set would
  be sufficient for.

### Gap 3 (closed, M2.2): policy and autonomy decision spans

- **Evidence:** No convention found, and re-checked for M2.2 rather than
  assumed still true: OpenTelemetry's semantic conventions have no span, no
  attribute namespace and no registry entry for a policy, authorization or
  autonomy decision. The nearest neighbours are all about the *principal*
  (`user.id`, the deprecated `enduser.*`), not about a verdict. So
  `policy.decision` and the nine `policy.*` attributes are this project's own
  to define, and there is nothing upstream to defer to or to file against.
- **Status: closed in M2.2, by defining them in the open.** The span is
  emitted by `platform/policy-decision-service`, its attribute keys are
  declared once in `platform/policy-decision-service/pds/telemetry.py` (which
  is the source this document cites rather than re-typing), and the full
  measured inventory — read off two live traces, with the conditional keys
  identified by measurement rather than from the source's declared tuple —
  is in "The policy decision, as measured (M2.2)" above.
- **Why this is a closure and not an invented convention.** The distinction
  this project's governing rule draws is between inventing telemetry *for a
  third-party component* — forbidden, without exception — and instrumenting
  our own code where no framework does it for us. A policy decision is the
  second: no library emits one, so either this project names the attributes or
  the decision is invisible. The names are prefixed `policy.` precisely so a
  reader can tell them apart from the `gen_ai.*`, `mcp.*` and `http.*`
  attributes the frameworks emit, and this document says outright that they
  are ours. Nothing was renamed, aliased or back-filled onto anyone else's
  spans to make the story neater.
- **What would make this reopen.** If OpenTelemetry ever specifies a
  policy-decision convention, these names become divergence rather than
  invention, and the honest move then is to adopt theirs and record the
  rename here. That is a real possibility rather than a formality: the GenAI
  semantic conventions are actively growing towards agent governance, and the
  same repository already carries four independent open proposals for A2A
  identity (see Gap 1). Nothing in this project would notice the day such a
  convention lands, which is worth knowing.

### Gap 4: baggage propagation across the MCP boundary

- **Evidence:** Neither. `northstar.claim.id` reaches payments-mcp on **0** of
  its tool-call spans, and payments-api on **0** of its spans, in a live
  capture of the full chain, asserted by
  `tests/e2e/test_claim_correlation.py::test_claim_id_is_on_every_mcp_hop_span`.
  (Originally recorded as "Where `northstar.claim.id` lands" in the **M1.1**
  milestone's Task 7 report — a git-ignored SDD working artifact, cited as
  provenance rather than as a document you can open; the runnable test above
  is the evidence. Note that M1.2 has its own, unrelated Task 7.)
  The `traceparent`/`tracestate` crossing is intact — this is a
  correlation gap, not a broken trace: FastMCP 4.0.3's own
  `extract_trace_context()`/`inject_trace_context()` (`fastmcp/telemetry.py`)
  copy only `traceparent`/`tracestate` out of `params._meta`; `baggage` is
  never read or written as a `_meta` key anywhere in the installed package
  (`grep -rn baggage` over the package tree returns exactly **one** match,
  `fastmcp/telemetry.py:297` — a code comment about preserving already-active
  baggage on extract, not a `_meta` key read or write). SEP-414 (which this
  project's own MCP trace-context convention already follows) documents
  `baggage` as one of the three unprefixed `_meta` keys, alongside
  `traceparent`/`tracestate`.
- **Upstream check:** `PrefectHQ/fastmcp#3998` ("OpenTelemetry: FastMCP client
  does not propagate trace context on initialize and list methods", open)
  **already reports the client side of exactly this gap** — its own body
  states the server "does not receive `traceparent` / `baggage` through MCP
  `_meta`" and asks that FastMCP's *client* requests "propagate
  `_meta.traceparent` and `_meta.baggage`" on `initialize`/`list` calls. Its
  premise is that `tools/call` (the method this project's chain actually
  uses) already propagates baggage correctly — which our own live capture
  shows is not true for the `baggage` key specifically, only for
  `traceparent`/`tracestate`. No open issue reports that the **server** side
  never reads `baggage` out of `_meta` at all, which is our precise finding;
  [#4358](https://github.com/PrefectHQ/fastmcp/issues/4358) is on the same
  file's trace-context handling but does not mention `baggage` anywhere. A
  gap independently reported by a third party (even on a narrower slice of
  it) is stronger evidence that this is real than if we were the only ones to
  have found it. The MCP spec repo has a related but distinct open proposal,
  [SEP-2028 "Automatic `_meta` to HTTP header forwarding for distributed
  tracing"](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/2028),
  about forwarding `_meta` context (including `baggage`) to *downstream HTTP
  calls a server makes*, not about a server reading `baggage` out of `_meta`
  into its own OTel context in the first place — adjacent motivation,
  different gap.
- **Action:** Not filed by this project — same reason as Gap 1 (S4,
  final-review.md): filing upstream is a decision this project reserves for
  its maintainers, not one an implementation task makes unilaterally. Unlike
  Gap 1's semconv half, filing here would not be low-value if it were this
  project's call — this is a precisely-scoped, previously-unreported gap
  (the client-side half is independently corroborated by #3998, but no open
  issue covers the server-side omission this project found) — so it is
  recorded complete and filing-ready: installed version, exact file and
  function, what is read versus not, and how it differs from #3998's and
  #4358's adjacent territory. Recorded here as upstream-contribution evidence
  (`tools/payments-mcp/NOTES.md` §6, `agents/settlement-agent/NOTES.md`
  §11). A natural upstream fix would add a `baggage` key to both
  `extract_trace_context()` and `inject_trace_context()`, symmetric with how
  they already handle `traceparent`/`tracestate` — the same fix #3998 is
  already asking for on the client side. Not worked around: no code in this
  repo reaches into FastMCP's own `_meta` handling to bridge this gap. Pinned
  by `tests/e2e/test_claim_correlation.py::
  test_claim_id_is_on_every_mcp_hop_span` — a manual `pytest.xfail()`, not
  `@pytest.mark.xfail(strict=True)` (S3; see "What the default suite proves"
  above for why), narrowed to this one assertion per a review finding — a
  fixture-setup failure upstream of it now surfaces as an ordinary `ERROR`,
  not a false-quiet `xfailed`. **Pinned from the other side too, since M4
  Task 3:** `tests/e2e/test_evidence_view.py::
  test_the_trace_id_join_is_what_reaches_the_money_hops` asserts that a
  claim-id filter over the OpenSearch trace index reaches neither
  `payments-api` nor the Policy Decision Service, and that the trace-id join
  does. That is not a second recording of the same fact — it is the
  constraint the M4 evidence view is *built around*, so if this gap is ever
  closed, that test names itself, the saved dashboard's two-step layout and
  this section as the three things that have to change together.

### Gap 5: FastMCP never emits `gen_ai.operation.name` on tool-call spans

- **Evidence:** FastMCP 4.0.3's `tools/call propose_payment`/`execute_payment`
  spans carry `gen_ai.tool.name` (e.g. `execute_payment`) correctly, but never
  `gen_ai.operation.name`, where the GenAI semconv
  (`open-telemetry/semantic-conventions-genai`) specifies `execute_tool`
  for a tool invocation (`tools/payments-mcp/NOTES.md` §2). The sibling
  attribute is done properly; the operation classifier is simply absent.
  **M4 Task 2 found the metrics-side counterpart of this gap**: the same
  FastMCP 4.0.3 records no `gen_ai.execute_tool.duration` either — indeed no
  metric of any kind, since the package contains no `get_meter(` call at all.
  See Gap 8 below.
- **Upstream check:** Already filed and open —
  [`PrefectHQ/fastmcp#3534` "Support GenAI semantic convention in open
  telemetry"](https://github.com/PrefectHQ/fastmcp/issues/3534), which asks
  for exactly `gen_ai.operation.name = execute_tool` on these spans (it also
  asks for `gen_ai.tool.name`, which 4.0.3 already emits — the issue predates
  or is unaware of that half being fixed).
- **Action:** No action needed from this project — the gap is already
  upstream, filed by someone else, and open. No bespoke `gen_ai.operation.name`
  was added to `server.py` to paper over it while waiting.

### Gap 6: `mcp==1.9.4`'s high-level client has no route to attach `params._meta`

- **Evidence:** this project's own finding, previously recorded only in
  `agents/settlement-agent/NOTES.md` §10 and omitted from this list until now
  (S4, final-review.md). `mcp==1.9.4`'s `ClientSession.call_tool(name,
  arguments, read_timeout_seconds, progress_callback)` has no `_meta`
  parameter at all and always builds `CallToolRequestParams(name=name,
  arguments=arguments)` internally with no way to set `params._meta`
  (verified by reading `mcp/client/session.py`'s signature directly). The
  only way to attach `_meta` to a tool call on this version is to hand-build
  a `CallToolRequestParams(..., _meta=types.RequestParams.Meta(**carrier))`
  and call the lower-level `send_request(ClientRequest(CallToolRequest(...)),
  CallToolResult)` instead — duplicating `call_tool()`'s own
  name/arguments/result-type/timeout/progress-callback plumbing. This is
  exactly the technique `agents/settlement-agent/mcp_tools.py` (Task 7) and
  `tests/e2e/test_payments_mcp_trace.py` (A14a) both use for the same reason.
- **Upstream check (searched the tracker directly, not assumed):**
  [`modelcontextprotocol/python-sdk#1216` "Expose `_meta` in `ClientSession`
  methods"](https://github.com/modelcontextprotocol/python-sdk/issues/1216)
  proposes exactly this — an optional `meta: dict | None` parameter on
  `call_tool()`, mirroring what `CallToolRequestParams` already supports
  internally, which is precisely the natural fix this project's own finding
  would have asked for. **It is already resolved**: closed
  2025-10-17 (`stateReason: COMPLETED`) via
  [`#1231` "Expose `RequestParams._meta` in
  `ClientSession.call_tool`"](https://github.com/modelcontextprotocol/python-sdk/pull/1231),
  merged the same day. That fix landed in an `mcp` release newer than
  `1.9.4` — this project's pin (`tests/requirements.txt`,
  `agents/settlement-agent/requirements.txt`) — so the gap this project found
  independently is real for the version actually running here, and already
  fixed for anyone on a current `mcp` release.
- **Action:** Nothing to file — the fix already exists upstream and merged
  before this section was written. No workaround was added to route around
  it: `mcp_tools.py`'s `send_request()` approach is the documented, correct
  way to reach `params._meta` on `mcp==1.9.4`, not a patch over a bug. Bumping
  the `mcp` pin to pick up `#1231`'s simpler `call_tool(meta=...)` interface
  is a real, available simplification but is deliberately out of scope for
  this documentation-accuracy pass — verifying a new `mcp` version against
  the rest of this project's pin set is A21 territory (a version bump with
  its own re-verification), not something to fold unexplained into a
  contract-wording fix.
- **Update (Task 7, M1.2):** the version bump described above as future work
  has happened — `mcp` moved `1.9.4` -> `1.28.1`, clearing four pip-audit
  advisories (PYSEC-2026-1617/-1618/-3482/-3483), isolated from every other
  M1.2 change so a propagation regression would have exactly one suspect
  (`tests/e2e/test_payments_mcp_trace.py`, re-verified green at `1.28.1`,
  including a deliberately-broken `_meta` key proven to fail first). `1.28.1`
  does carry `#1231`'s merged `call_tool(meta=...)`, confirmed directly
  against the installed distribution's signature — but `mcp_tools.py` was
  deliberately left unchanged, byte-for-byte, in the same task that bumped
  the pin: adopting the simpler call path is still available future work,
  now recorded in `docs/upstream-findings.md` Finding 4, not decided here.
- **Update (2026-09-11, M3 remaining-bumps step 1): `mcp` is now 2.2.0, and
  the gap is not merely fixed, it is obsolete.** The SDK no longer needs to be
  *asked* to attach trace context to `params._meta`: its JSON-RPC dispatcher
  injects W3C trace context into `_meta` on every outbound request
  unconditionally, with a source comment naming SEP-414
  (`mcp/shared/jsonrpc_dispatcher.py`, read from the installed 2.2.0 — quoted
  in "The `mcp` client's own telemetry, as measured" above). Measured live, not
  read off a changelog: the injection is what parents payments-mcp's server
  span under the client's new `MCP send tools/call <tool>` span.
  **Consequence for this repo, stated rather than acted on:**
  `mcp_tools.py`'s hand-built `send_request` path — the one piece of production
  code here that exists because of this gap — is now redundant. It is not
  harmful (the SDK's own injection overwrites the same keys with a deeper
  parent in the same trace, and the hand-built path is still what the unit
  tests pin), and it was **left in place**: deleting it is a behavioural change
  that deserves its own step and its own live proof, not a passenger on a
  dependency bump. Recorded here and in `docs/upstream-findings.md` Finding 4
  so the next reader finds a decision rather than an accident.

### Gap 7 (closed, M1.2 Task 8): the model's own outbound HTTP hop had no CLIENT span — already fixed upstream, and now applied here too

- **Evidence:** `anthropic==1.4.0` requires `httpx2<3,>=2.0.0` — a distribution
  genuinely separate from `httpx` — confirmed by reading the installed
  package (`anthropic/_base_client.py:41`:
  `from httpx2 import URL, Proxy, HTTPTransport, AsyncHTTPTransport`;
  `pydantic_ai/providers/anthropic.py` types its client as
  `httpx2.AsyncClient`). `bootstrap.py` applies only `HTTPXClientInstrumentor()`
  (`_module_name="httpx"`); the sibling `HTTPX2ClientInstrumentor`
  (`_module_name="httpx2"`), present in the **same already-installed**
  `opentelemetry-instrumentation-httpx==0.65b0` package, is never called.
  Measured consequence: 12 httpx-instrumented spans in a captured trace, all
  of them settlement-agent's or payments-mcp's calls to payments-mcp or
  payments-api — none to `api.anthropic.com` (task-4-report.md §6.4,
  task-4-review.md §5). The only trace of the Anthropic call anywhere is
  `server.address=api.anthropic.com` on pydantic-ai's own `chat` span.
- **What is lost:** HTTP status code (a 429 that succeeded on retry, a 529
  overload, a malformed 400 are all indistinguishable from a slow success);
  the Anthropic SDK's own internal retry count (retries collapse into one
  `chat` span's duration); wire-versus-logical latency (the `chat` span's
  duration mixes SDK serialization, retry backoff and streaming assembly
  with actual network time, with no way to separate them); and
  connection-level failure detail (DNS/TLS/connect-timeout failures surface
  only as an exception on the `chat` span, without the peer address/port an
  httpx span would carry).
- **What survives:** the hop's existence, position and duration in the
  trace; `server.address`; request/response model; response id; finish
  reasons; token counts; even `operation.cost`. The causal chain — that the
  model was called, in this position, with this outcome — is intact via
  pydantic-ai's own `chat` span. Only the transport-layer diagnostic view is
  gone.
- **Correction to how this gap was first framed in this milestone's own
  working notes:** it was initially treated as a candidate *upstream*
  finding — a library not supporting `httpx2`. **That framing does not
  survive a tracker search.** `httpx2` is `pydantic/httpx2`, a
  Pydantic-maintained, API-compatible fork of `httpx` created after concerns
  about `httpx`'s own maintenance (see
  [`open-telemetry/opentelemetry-python-contrib#4635`](https://github.com/open-telemetry/opentelemetry-python-contrib/issues/4635)
  for the history). Support for it was requested in that same issue, **fixed
  in [`#4730`](https://github.com/open-telemetry/opentelemetry-python-contrib/pull/4730)
  ("Feature/httpx2 instrumentor")**, and **shipped in the project's own
  changelog under "Version 1.44.0/0.65b0 (2026-07-16)"** — *the exact
  `opentelemetry-instrumentation-httpx` version already pinned and installed
  in this repository* (`docs/telemetry-contract.md`'s own instrumentation
  table, above). The library's installed docstring even documents the fix
  directly: "When instrumenting `httpx2` clients, use `HTTPX2ClientInstrumentor`."
  So there is no upstream gap to report here at all — the fix shipped before
  this milestone ever measured the absence.
- **Where the actual gap lived:** entirely in this project's own
  `bootstrap.py`, which called `HTTPXClientInstrumentor()` and not also
  `HTTPX2ClientInstrumentor()`. **Closed by M1.2 Task 8**: `bootstrap.py` now also
  calls `HTTPX2ClientInstrumentor().instrument()` alongside the existing
  `HTTPXClientInstrumentor()` call — the one-line addition this section
  originally predicted, with the version already installed, no dependency
  bump, no upstream wait.
- **Measured after the fix (M1.2 Task 8, live capture, before/after diff against a
  saved pre-fix trace dump):** each `chat claude-sonnet-5` span now has
  exactly one httpx-instrumented CLIENT span nested directly under it — three
  such spans in a three-turn conversation, matching the three `chat` spans
  one-for-one, no duplicates. This project's httpx instrumentation runs with
  `OTEL_SEMCONV_STABILITY_OPT_IN=http`
  (`deploy/k8s/settlement-agent.yaml`), so the new span carries the
  **stable** HTTP semconv spelling already used for this service's MCP-bound
  httpx spans, not the legacy one — read from a live attribute dump, not
  assumed: `http.request.method=POST`, `http.response.status_code=200`,
  `server.address=api.anthropic.com`, `server.port` absent (HTTPS, no
  explicit port in the URL), `url.full=https://api.anthropic.com/v1/messages?beta=true`,
  `network.peer.address=api.anthropic.com`,
  `network.protocol.version=1.1`, `otel.scope.name=opentelemetry.instrumentation.httpx`,
  `otel.scope.version=0.65b0`. `HTTPXClientInstrumentor` and
  `HTTPX2ClientInstrumentor` are two classes in the same
  `opentelemetry.instrumentation.httpx` module and share its
  `get_tracer(__name__, ...)` call, so `otel.scope.name` alone cannot
  distinguish an httpx span from an httpx2 span — the model-bound span is
  identified by `server.address == "api.anthropic.com"` instead. Pinned by
  `tests/e2e/test_settlement_llm_trace.py::test_anthropic_client_span_exists_and_is_parented_to_chat`,
  which replaces the predecessor test named below (retired, per its own
  docstring's instruction, the day this fix landed).
- **No duplicate span.** The concern going in was that pydantic-ai's own
  `chat` span might already be the raw HTTP request, and adding
  `HTTPX2ClientInstrumentor` would double-count that hop's latency. Measured:
  it is not — `chat` is a separate, outer CLIENT span (pydantic-ai's logical
  "call the model" operation, carrying `gen_ai.*` attributes) with the new
  httpx CLIENT span nested one level inside it (carrying `http.*`
  attributes). Two CLIENT-kind spans at two different nesting depths,
  representing two different things, is the expected and desired shape, not
  a duplicate.
- **Existing upstream activity (verified live, read-only, 2026-09-08, `gh`
  against the real repositories — no comment or issue posted anywhere):**
  the httpx2-instrumentation gap itself is closed and merged, as above. Two
  related, still-**open** issues on the same package show the httpx2 side of
  this instrumentor is actively maintained but not yet complete for every
  provider:
  [`open-telemetry/opentelemetry-python-contrib#4953`](https://github.com/open-telemetry/opentelemetry-python-contrib/issues/4953)
  ("`openai` 3.x: `server.address`/`server.port` are never recorded
  (`isinstance` check misses `httpx2.URL`)") and its companion fix-in-progress
  [`#4954`](https://github.com/open-telemetry/opentelemetry-python-contrib/pull/4954)
  — both about OpenAI's client specifically, not Anthropic's, and about a
  narrower attribute-extraction bug within the already-applied instrumentor,
  not about the instrumentor's existence. Two other observability vendors
  show the same "httpx2 exists, our tracer doesn't cover it yet" gap
  independently — [`DataDog/dd-trace-py#18780`](https://github.com/DataDog/dd-trace-py/issues/18780)
  ("[FEATURE]: Support httpx2", open) and
  [`elastic/apm-agent-python#2706`](https://github.com/elastic/apm-agent-python/issues/2706)
  ("feat: add httpx2 instrumentation", open) — corroborating that the
  httpx-to-httpx2 migration this finding sits on top of is a real,
  ecosystem-wide instrumentation transition, not a quirk unique to this
  project's dependency tree. No open issue was found on
  `anthropics/anthropic-sdk-python`, `pydantic/pydantic-ai`, or
  `open-telemetry/opentelemetry-python-contrib` asking specifically for the
  Anthropic client's own transport hop to gain a CLIENT span — this
  project's specific instance of the gap is unreported, because the generic
  fix that would close it is already shipped and merely unapplied here.
- **Status: closed (M1.2 Task 8), and there was never anything to file.** Unlike
  Findings 1 and 3 in `docs/upstream-findings.md`, this was never
  filing-ready material — filing would have asked upstream to do something it
  already did. The one remaining action was local — apply
  `HTTPX2ClientInstrumentor()` in this project's own `bootstrap.py` — and
  M1.2 Task 8 did exactly that; the omission was this project's, never an
  upstream gap. See `docs/upstream-findings.md` Finding 5 for the full
  write-up in that document's format, updated to match.

### Gap 8 (M4 Task 2): five of the six §6 GenAI metrics are emitted by nothing

- **Evidence:** with a `MeterProvider` installed in all five Python services
  that existed at M4 Task 2 (`platform/eval-scorer` arrived in Task 4 with
  none — limitation 2) and Prometheus receiving OTLP directly, exactly **one**
  of the six metrics M4
  spec §6 asks about reaches it — `gen_ai.client.token.usage`, from
  PydanticAI. `gen_ai.invoke_agent.duration`,
  `gen_ai.invoke_agent.inference_calls`, `gen_ai.invoke_agent.tool_calls`,
  `gen_ai.execute_tool.duration` and `gen_ai.client.operation.duration` are not
  merely absent from Prometheus: **not one of those names appears anywhere in
  `pydantic-ai-slim` 2.42.0, `a2a-sdk` 0.3.7, `fastmcp` 4.0.3 or `mcp` 2.2.0**,
  and the last three contain no `get_meter(` call at all, so they create no
  instrument of any kind. The full per-metric table, the per-service positive
  controls that make the negatives readable, and the two probes sent to prove
  the export path of the services that record nothing, are under "Metrics, as
  measured (M4 Task 2)" above.
- **Upstream check:** the conventions are not in draft — all twelve GenAI
  metrics, these six included, are published in
  `open-telemetry/semantic-conventions-genai` at Development stability. So this
  is a framework-coverage gap against a published convention, not this project
  asking for something unspecified. The trackers were searched; see
  `docs/upstream-findings.md` **Finding 12** for what was found and what was
  not.
- **Action:** nothing hand-rolled. §6's standing rule is that no metric is
  invented to fill a hole in a framework's coverage, and none was — a
  `gen_ai.*`-named instrument authored here would make this project's telemetry
  disagree with what a future, fixed framework emits natively, which is the
  same argument Gap 5 makes for not hand-setting `gen_ai.operation.name` on
  FastMCP's spans. **Two tests enforce that, because it takes two layers** —
  and the split is stated because an earlier version of this bullet cited only
  the first and claimed more for it than it can do (M4 Task 2 review finding
  F5). `tests/e2e/test_prometheus_metrics_ingest.py::
  test_no_metric_was_invented_to_fill_a_framework_gap` asserts that Prometheus
  holds no `gen_ai.*` name beyond the one PydanticAI emits; it reads a store,
  so it can see what a series is *called* and never who authored it.
  `tests/unit/test_metrics_wiring.py::
  test_no_service_authors_a_metric_under_a_convention_namespace` is the half
  that sees origin — it walks the AST of every Python file under `agents/`,
  `apps/`, `platform/`, `tools/` and `systems/` for an instrument created under
  a namespace an instrumentation library owns, and runs without a cluster.

## Known limitations, recorded rather than fixed (M2.2)

Every item below is a **deliberate scope boundary**, not a defect discovered
late: items 1–9 were each found and argued during M2.2 Tasks 3, 5 or 7,
decided against fixing in that task, and logged for this section. Task 8
re-verified every one of them against the source and — where it is a runtime
property — against the live cluster, rather than transcribing the list it was
handed. That re-check earned its keep twice: **one item on the list did not
survive verification and is corrected in place below** (item 5, which had been
attributed to the wrong service), and **item 10 is not on the list at all** —
it was found by the re-check itself. **Item 11 is later still**: it was found
by the final whole-branch review, after Task 8 closed, and item 10 was rewritten
in the same round because the review demonstrated that its stated cause was
false.

**M3 (Supervised) re-read all eleven and added none — but amended three, and
one of those amendments is a finding rather than an update.** Item 3 said
narrowing `_latch` was "M3's problem"; M3 sidestepped it instead, so the item
stays open and now says which case is still exposed. Item 10's published PDS
test count and its "what the gate does not run" list both moved, and it gained
the M3 recurrence of its own defect (`reviewer-ui`, same omission, one
milestone later). **Item 11's attribution turned out to be false**: it blames
ruling R4 for the Policy Decision Service's image shipping `pytest`, and
measuring all five Python Dockerfiles shows every service in this repository
does the same and always has — the cause is a repo-wide convention, not that
ruling, and the item describes five images rather than one. The correction is
appended to the item rather than swapped into it. Everything else here was
re-checked against the source or the live cluster and is unchanged.

None of these is a telemetry-invention issue and none of them makes a
published claim in this document false. They are the places a reader who
pushes on this system will find its edges.

**1. The policy limit is currency-blind.** `platform/policy-decision-service/pds/rules.py`
keys `EXECUTE_LIMITS` on `(SPIFFE ID, autonomy level)` and compares
`req.amount > limit` with no reference to `req.currency` at all — the currency
reaches `decide()` and is used only to render the refusal text (`_money`). So
a request for 6,200 of any unit is judged against a limit denominated in
euros. It is bounded today by two things outside the PDS, and by nothing
inside it: payments-api's execute handler confirms the stated currency against
the recorded proposal (`e062c10`), and `contracts/openapi/payments-api.yaml`
puts `enum: [EUR]` on the *propose* body, so a non-EUR proposal is refused
before it can be executed. Both of those are real and both are somewhere else.
The honest statement is that the platform, as a whole, is EUR-only; the policy
engine, on its own, does not know what a euro is. Fixing it properly means
either a per-currency limit table or an explicit refusal of any currency the
table is not denominated in — a design decision, and M5's autonomy work is
where the limit table becomes configurable anyway.

**2. Unbounded attacker-supplied strings reach a span attribute.**
`decide()`'s refusal texts interpolate values that came off the wire:
`f"unknown tool {req.tool!r}"` echoes the tool name, and `_money()` echoes the
currency. `parse_call` type-checks both (a non-string becomes `None` or `""`)
but does not length-check either. That string becomes
`AuthorizationError.reason`, which agentgateway v1.5.0 copies verbatim into
`otel.status_description` on its `tools/call` SERVER span — measured, see
"agentgateway's guardrail telemetry, as measured" above. So a caller that can
reach the gateway and pass authentication can put an arbitrary-length string
of its choosing into this system's telemetry. The blast radius is bounded by
who can get that far — only a workload holding a valid JWT-SVID for the
gateway's audience, which today is `settlement-agent` alone — and the failure
mode is a fat span rather than an injection, since it lands as an OTLP
attribute value and not in a query or a shell. Recorded because "telemetry is
a sink for untrusted input" is exactly the kind of thing that is obvious once
stated and invisible until then.

**3. `_latch` latches the whole run, not the refused call.**
`agents/settlement-agent/settlement.py`'s `_latch` exists so that a model
cannot sit on stage retrying a payment it will never be allowed to make: after
any refusal, *no further payment call* leaves the process for that claim, and
the second attempt is logged and handed the same terminal refusal. The
granularity is deliberately coarse for M2.2, where the only correct next move
after a refusal is to narrate it. It becomes wrong the moment a future
milestone wants a re-propose at a compliant amount, or an escalate-then-retry
path after a human approves — both of which are legitimate follow-on calls
that this latch would refuse. Narrowing it to the specific refused call is the
fix, and it is M3's problem, not a bug today.

**M3 update, and it is a sidestep rather than the fix this item asked for.**
M3 did not narrow `_latch`; it is byte-identical. The escalate-then-retry path
this item predicted would collide with it does not, and the reason is
structural: `_latch` is called from exactly two places, the bodies of the two
`@settlement_agent.tool` functions, and `execute_approved` **is not a model
run** — it makes no model call, builds no `RunContext`, and calls
`PaymentsTools.execute` directly, so the latch is not on that code path at
all. (It also runs in a different process under a different identity,
`senior-settlement-agent`, but that is not what saves it; a same-process
approval would be equally unlatched.) The collision was avoided by the
milestone's architecture rather than by a change here. **The item stays open
exactly as written**: a *re-propose at a compliant amount by the original
agent*, on the same claim, is still refused by this latch, and that is still
the case a future milestone will hit. What has changed is only that M3 is no
longer the milestone that was going to fix it (M3 Task 5B, re-read against
`settlement.py`, not inferred).

**4. `_leaf_exception` can drop a denial-bearing `McpError` in a mixed
exception group.** `agents/settlement-agent/mcp_tools.py`'s unwrapper prefers a
`ToolCallError` leaf anywhere in the group and otherwise takes `leaves[0]`.
`McpError` — which is what a policy denial arrives as (measured, M2.2 Task 1)
— is not a `ToolCallError`, so a group holding both would surface the
`ToolCallError` and lose the denial, and the `except McpError` clause below
does not help because the group clause wins first. Not reachable today: every
exception group measured on this path has held exactly one leaf. Left alone
because the preference is *correct* for the case it was written for (M1.1's
tool-failure text) and changing it needs a real mixed-group case to reason
from rather than a hypothetical.

**5. `settlement-agent` configures no logging, so its own `logger.info` calls
are inert in-pod. The Policy Decision Service does not have this problem —
this item was carried into Task 8 attributed to the wrong service, and the
re-check is what caught it.** Measured on the live cluster: `settlement-agent`
has no `logging.basicConfig`, `logging.config` or `dictConfig` anywhere in its
source, so its root logger sits at WARNING with no handlers and only
`logging.lastResort` reaches stderr. `decide_settlement`'s refusal line and
`executor.py`'s reply line are both INFO and print nothing; `_latch`'s WARNING
does appear, which is what makes the absence of the other two real evidence
rather than an assumption about log levels. (The uvicorn `INFO:` lines visible
in that pod's log are uvicorn configuring its own logger, not this project's.)
The PDS, by contrast, calls `logging.basicConfig(level=os.environ.get("LOG_LEVEL",
"INFO"))` in `serve()` — and its pod log carries one
`decision=… rule=… tool=… caller=…` line per decision, read live. The levels
in `settlement-agent` were left at INFO rather than inflated to make them
visible, which is the correct level for a normal-operation event; the decision
that is genuinely open is whether that service gets a logging config at all.

**6. Criterion 4's structural guard keys its allowlist on file basename.**
`tests/e2e/test_policy_denial.py`'s `ALLOWED_MONEY_COMPARISONS` is keyed
`(component, path.name, source)`. `path.name` is the basename, so a future
subpackage file sharing a basename with an allowlisted one — a second
`settlement.py` under `agents/settlement-agent/something/` — would silently
inherit its two entries and could carry an unargued money comparison past the
guard. The guard already walks recursively (`rglob("*.py")`), so the file
would be *scanned*; only the allowlist lookup is too generous. The fix is one
line, keying on the path relative to the component root instead, and it is
suggested rather than required now because no such subpackage exists in either
governed component.

**7. Criterion 4's structural guard reads only `*.py`.** Same test, same
`_source_files`: a threshold expressed anywhere other than Python — a
Dockerfile `ENV`, a ConfigMap, a Helm value, a YAML default — is invisible to
the vocabulary, literal and comparison checks alike. Entirely latent today,
because the €6,000 allowance lives in `rules.py` and nowhere else and neither
governed component has a config file that could carry a number like it. It
becomes relevant at **M5**, when the execute allowance becomes a feature flag
and therefore configuration: at that point a limit could legitimately be
expressed outside Python, and this guard would stop covering the thing it is
named for. Recorded here so that is a decision M5 makes rather than a hole M5
walks into.

**8. `test_gateway_is_the_only_route.py`'s bypass probe is now enforced at a
different layer than the one it was written to test.**
`test_an_unprivileged_workload_cannot_reach_the_tool_directly` probes
`claims-agent → payments-mcp:8000` and asserts it fails. It was written for
`d17309c`'s **ingress** policy on payments-mcp. Task 7's
`claims-agent-egress-allowlist` gives `claims-agent` no egress to
`payments-mcp` at all, so the connection is now dropped at **egress**, before
the ingress policy it was written for is ever consulted. The assertion still
passes and still fails loudly if *either* layer breaks — which is defence in
depth working, and is why the test was not changed — but it no longer
attributes the block to the ingress half on its own. Task 8 corrected that
test's docstring to say which layer is actually being exercised; the
assertions are untouched. The same asymmetry, from the other side, is recorded
in Task 7's own mutation 4: adding a `payments-mcp:8000` egress allowance left
the behavioural probe green because the ingress policy was still dropping the
packet, and only the BPF-map assertion caught it.

**9. There is no Hubble flow evidence anywhere in this project, and no
document should imply otherwise.** `enable-hubble: false` in this cluster's
`kube-system/cilium-config` — read live, not from the chart default. Cilium
emits Prometheus metrics and Hubble flows; only the first is available here.
Every network claim in this repository therefore rests on one of two things:
reads of the BPF policy map (`cilium-dbg bpf policy get <endpoint>`, which is
what pins the egress allowlists by set equality) or TCP probes run from inside
the pods. No Cilium telemetry is asserted as a span anywhere, and none was
invented — which is the same rule this document applies to agentgateway and
SPIRE, applied to a third component that also has no OTel story.

**10. `tests/unit` is no longer self-sufficient on a fresh clone: it needs one
generated artifact that only a script outside it produces.** Rewritten, not
retired, in the M2.2 final-review fix round — and the rewrite is the point, so
what it replaced is stated first.

**What this item used to say, and why it was wrong.** Through M2.2 it read
*"The Policy Decision Service's own 95-test suite is run by neither the gate
nor any drift guard"*, and gave as the cause that running that suite *"needs a
generation step that neither the gate's per-service loop nor the hermetic
`tests/unit` subset is willing to take on"*. **That cause was false in its
premise.** The final whole-branch review demonstrated (by moving the stubs
aside) that both already *require* the generated stubs, because
`agents/settlement-agent/tests/test_policy_deny_contract.py` imports
`pds.server` — the settlement-agent suite aborts at collection, 0 of 79 tests
run, and `tests/unit`'s own per-service count guard goes red. So the trade the
item described had already been made and was collecting none of its benefit,
and `README.md` compounded it by telling a new operator the PDS setup was
*deliberately* not needed for the gate, which meant a fresh clone following the
README verbatim could not run `scripts/smoke-test.sh` at all. That half is
fixed: the gate now requires the PDS venv, runs `./gen-stubs.sh` ahead of every
suite, and runs the PDS's suite (96 tests when that round wrote this sentence;
105 today, re-measured for M3 Task 5B); `_PER_SERVICE_SUITES` has a sixth row;
the README says what is actually true. The stubs were **not** committed — that
re-opens the image/repo divergence ruling R22 exists to prevent.

**M3 Task 5B note: the same omission recurred one milestone later, with
`reviewer-ui`, and was closed the same way.** M3 Task 4 shipped
`apps/reviewer-ui` with 50 tests of its own, in neither the gate's per-service
loop nor `_PER_SERVICE_SUITES` nor this document's per-service row. Unlike the
PDS's case there was no hidden dependency running any of them, so the gate
could have gone green with that component's suite entirely broken. All three
sites now carry a seventh row. It is recorded here rather than only in the
per-service row because *this* is the item a reader checking "what is the gate
not running" would come to.

**The real remaining gap, which is narrower and worth keeping.** This document
calls `tests/unit` "the hermetic subset: no cluster, no API key, no network",
and that is still true of every resource it names — but it is not the same as
self-sufficient. `pytest tests/unit` on a clone where `gen-stubs.sh` has never
been run fails, because its per-service count guard shells out to the
settlement-agent and policy-decision-service suites and both need
`ext_mcp_pb2.py`, which is git-ignored. The failure is loud and actionable —
the PDS's `conftest.py` raises rather than skipping and prints the exact
command — and that is R22 working as designed rather than a defect. But it is a
**precondition documented in prose rather than enforced by the thing that needs
it**: nothing in `tests/unit` generates the stubs, and nothing warns before the
subprocess fails. The gate no longer has this problem (it generates them
itself); a developer running only the hermetic subset still does.

Closing it properly means one of two things, both of which are choices rather
than oversights: have the count guard invoke `gen-stubs.sh` itself, which makes
a "no network, no side effects" test write files into a service directory; or
have `tests/unit` skip the two Python suites that need stubs when the stubs are
absent, which trades a loud failure for a silent hole in exactly the guard this
project has already been burned by weakening. Neither is obviously right, so
the precondition is recorded here instead.

**Coverage was never the issue and still is not.** The decision this milestone
is named for is covered end to end from the repo-root tree —
`tests/e2e/test_policy_denial.py` drives a real denial through the deployed
PDS, and `test_policy_deny_contract.py` drives the real `decide`/`build_result`
chain with a test-only PDS import.

**11. The deployed Policy Decision Service image carries its own build and test
tooling.** Found by the final whole-branch review (finding 8), and a
consequence of ruling R4 rather than an oversight in it.
`platform/policy-decision-service/requirements.txt` is deliberately **one**
file for runtime and test — R4 resolved a single transitive closure for Tasks 2
and 3 together instead of two possibly-incompatible ones — and
`platform/policy-decision-service/Dockerfile:4` installs that whole file with
`pip install --no-cache-dir -r requirements.txt`. So the running image contains
`grpcio-tools==1.83.1` (plus its `setuptools==84.0.0` build dependency) and
`pytest==9.1.1` (**J6, whole-branch review, 2026-09-15: was `9.0.3` here until
M5 Task 1's bump; corrected rather than left to drift a second time**) with
its four-package closure, none of which `pds/server.py`
imports: `grpcio-tools` is needed only at build time, by the `RUN
./gen-stubs.sh python` layer six lines below, and `pytest` is needed only by a
developer running the suite from the source tree. The service's ingress is
otherwise carefully minimised — its own Cilium document admits exactly two
sources to 9100, agentgateway and the node's kubelet probe (ruling R6), and
nothing else in the cluster — so this is the one place the component is broader
than it needs to be. It is recorded rather than fixed because every fix is a real
restructuring with its own cost: a multi-stage build that generates the stubs
in a builder image and copies them forward would re-open the image/repo
divergence R22 exists to prevent unless the copy is provably from the same
vendored proto, and splitting `requirements.txt` into runtime and test halves
re-opens exactly the two-closures problem R4 closed. Neither is a five-line
change, and neither belongs in a milestone whose subject is the decision. What
is *not* claimed here: that this is exploitable. No measurement of that was
made. What is measured: the image runs as a non-root uid
(`useradd --uid 10001 app` / `USER app` in the same Dockerfile), and the
serving path spawns no subprocess at all — `grep -rn 'subprocess\|os.system'
platform/policy-decision-service/pds/` returns nothing.

**M3 Task 5B correction, left standing with this note rather than rewritten:
this item's attribution is wrong, and it is wrong in the direction that makes
the problem look smaller than it is.** It presents the shipped test tooling as
"a consequence of ruling R4", i.e. as specific to the Policy Decision Service
and to the decision that gave it one requirements file for runtime and test.
That is not the cause. **Every Python service in this repository does the same
thing, and always has** — measured 2026-09-15 (whole-branch review, J6) by
reading all six Dockerfiles and all six requirements files, re-dated from the
M3 Task 5B measurement below, which read five of each and predates
`platform/eval-scorer`:

| Service | Dockerfile install | `pytest` in that file |
|---|---|---|
| `agents/settlement-agent` | `pip install --no-cache-dir -r requirements.txt` | `pytest==9.1.1`, `pytest-asyncio==1.4.0` |
| `agents/claims-agent` | same | `pytest==9.1.1`, `pytest-asyncio==1.4.0` |
| `tools/payments-mcp` | same | `pytest==9.1.1` |
| `platform/policy-decision-service` | same | `pytest==9.1.1` |
| `apps/reviewer-ui` (M3 Task 4) | same | `pytest==9.1.1`, `pytest-asyncio==1.4.0` |
| `platform/eval-scorer` (M4 Task 4) | same | `pytest==9.1.1` |

**J6, whole-branch review: this table published `pytest==9.0.3` for all five
original rows, and was missing the sixth (`eval-scorer`) entirely, until this
fix wave.** M5 Task 1 bumped all seven of this repo's `requirements.txt` files
(the six above, plus the root `tests/requirements.txt`) to `9.1.1`, clearing
PYSEC-2026-1845; every venv, including the root one `tests/unit/test_pins.py`
now guards (M5-R33), measures `pytest 9.1.1` today. The preamble's own
provenance claim — "measured by reading all five Dockerfiles and all five
requirements files" — decayed the same way the export-destinations table's
did (C1): the moment the files it named moved, without the table it produced
moving with them. Corrected here rather than silently, per this document's
own standing convention.

So the pattern is this repository's uniform one-requirements-file convention,
not R4; R4 only decided that the PDS would not be the exception. The `pytest`
half of this item therefore describes five images, including the one M3 added.
The **`grpcio-tools`/`setuptools`** half *is* PDS-specific and *is* a
consequence of the `gen-stubs.sh` build layer, exactly as written — no other
service generates anything at build time. And the sentence "this is the one
place the component is broader than it needs to be" is true read narrowly (the
PDS's ingress is minimised and this is where *that component* is broad) and
misleading read at the speed a reader reads it, which is how it survived a
whole-branch review.

Not fixed, for the reason the item already gives and one more: splitting
`requirements.txt` per service re-opens R4's two-closures problem five times
over rather than once, and the same thing is still not claimed here that was
not claimed before — nobody has measured whether any of it is exploitable.
What is now accurate is the *scope*.

## Open questions

### OQ-1: Content capture is a trade — ANSWERED 2026-09-13 (M4 Task 5), default unchanged

**Answered, not acted on.** `NORTHSTAR_CAPTURE_CONTENT` still defaults to `true`
in `deploy/k8s/settlement-agent.yaml` and
`deploy/k8s/senior-settlement-agent.yaml`, and M4 changed neither. What follows
is the deliverable M4 spec §7.7 asked for: *exactly* which claims the evidence
view can no longer support at `NORTHSTAR_CAPTURE_CONTENT=false`. The four-step
experiment's shape is preserved below, along with everything M2.1 Task 3
measured; this section is the answer on top of it.

#### The answer, stated as narrowly as it was measured

**Zero. The evidence view loses nothing.** Every field of every panel is
populated identically at OFF.

Measured as a matched pair on this cluster, both runs the same supervised
`CLM-1842` story at €6,200 — refused by `settlement-agent`, escalated,
approved by a human, executed by `senior-settlement-agent` — and read back
through `tests/e2e/test_evidence_view.py::evidence_for`, which is the function
whose docstring maps each key to the panel of `evidence.ndjson` that renders
it. So this is the dashboard's own query path, not a paraphrase of it. Mode was
read off the **running pods** with `jsonpath` before each run, never off the
Deployment spec (`kubectl diff` cannot prove a pod is serving a value; M2.1's
step 5 records why).

| Panel field | ON (`5ba013da…`, `69b3186f…`) | OFF (`6084d46e…`, `8e3be791…`) |
|---|---|---|
| 1 · trace ids | 2 | 2 |
| 2 · `policy_decision` / `rule` | `deny` / `settlement-agent.assisted.execute_limit` | identical |
| 2 · `limit` / `requested` | 6000 / 6200 | identical |
| 3 · `reviewer` / `review_decision` | present / `approve` | present / `approve` |
| 2 · `executing_identity` | `spiffe://northstar.local/ns/northstar-agents/sa/senior-settlement-agent` | identical |
| 2 · `executing_rule` | `settlement-agent.assisted.execute_limit` | identical |
| 4 · `money_hops` | propose → refuse → GET → GET → execute | identical |
| spans in the join | 214 | 216 |

**That result is the finding, and it is a statement about the design rather
than about redaction.** Not one of the evidence view's panels reads a `gen_ai.*`
attribute — every field it aggregates on is `attributes.policy.*`,
`attributes.review.*`, `attributes.northstar.claim.id`, `traceId`, `name` or
`resource.service.name`, all of them emitted by the platform (the PDS, the
reviewer UI, payments-api) and none of them gated on `include_content`. The
audit trail this milestone's headline rests on is reconstructed from what the
*platform decided*, not from what the *model said*. Content capture is
orthogonal to it.

#### What is lost, one step behind the view

The same pair, same traces, measured on the `gen_ai.*` layer an operator
reaches by following a trace id out of panel 1 — 28 distinct keys at ON, 25 at
OFF, reproducing M2.1 Task 3's figures on this milestone's own runs and index:

1. **"Did the agent ask for the amount the platform judged?"**
   `gen_ai.tool.call.arguments` carries `{"amount":6200,"currency":"EUR"}` on
   `execute_tool propose_payment` and
   `{"payment_id":"PAY-000137","amount":6200,"currency":"EUR"}` on
   `execute_tool execute_payment` at ON; at OFF the key is absent from the whole
   trace. Panel 2 still shows `requested: 6200` — that is the PDS's own record
   of the request — so what is lost is not the number but the **independent
   corroboration** of it. At OFF, the platform's account of what was asked for
   is the only account there is, and nothing in telemetry can contradict it.
   This is the one genuinely load-bearing loss, and it is an audit property,
   not a debugging convenience.
2. **"What was the agent told it could do about *this* claim?"**
   `gen_ai.system_instructions` absent at OFF.
   `model_request_parameters` survives (1,974 characters, identical in both
   modes) and holds the static instructions and both tool schemas — what the
   agent was told it could do *in general*. The claim-specific half is gone.
3. **"What did the ledger say back?"** `gen_ai.tool.call.result` absent at OFF —
   so neither what the model proposed nor what the ledger recorded survives on
   the `execute_tool` spans.

Plus the gutting that key-level survival hides, re-measured on this pair:
`gen_ai.input.messages` text/thinking parts carrying `content` **5 of 5 → 0 of
5**; its `tool_call` parts carrying `arguments` **3 of 3 → 0 of 3**;
`gen_ai.output.messages` **2 of 2 → 0 of 2** and **2 of 2 → 0 of 2**;
`pydantic_ai.all_messages` **1,857 → 657 characters**.

**Restored and verified.** Both Deployments were returned by
`kubectl apply -f` of the committed manifests, and the serving pods read back
`NORTHSTAR_CAPTURE_CONTENT=true` via `jsonpath` — with the pod-template hashes
back at `58bc5d8988` and `559785b65b`, the same hashes as before the
experiment, which is independent proof the spec is byte-identical to what was
committed rather than merely carrying the same value.

#### The working alternative, cited as a measured example rather than a hypothesis

The all-or-nothing switch is not the only shape available, and the middle
ground this section has called for since M2.1 already exists in the project
owner's own `otel-platform-demo`: the **traces** pipeline there carries an
`attributes/redact` processor with `action: hash` on `user.email`
(`infrastructure/collector/deployment-values.yaml:166-169`). That is redaction
in the collector, per attribute, after the span is emitted — so the SDK keeps
emitting everything, the backend never sees the sensitive field, and the choice
is made once in the platform rather than once per service in an env var. It is
the concrete answer to the "narrower middle ground" bullet below, and M4 records
it as an existing, running example rather than proposing it. Building it here is
not M4's (spec §2 rejected redaction-as-a-demo-beat to keep the milestone at
three things); knowing it works is.

#### Why this closes as *answered* rather than as *decided*

The trade is now sized in the one place that matters for this demo: the
governance story — who decided, under which rule, at which limit, approved by
whom, executed under which identity, and where the money went — survives full
content redaction intact. The **model's own account of itself** does not. An
insurer that wants the first and not the second can run OFF today and lose
nothing this repo puts on a dashboard. Whether it *should* is still a decision
its owner makes, and this document does not make it: the default stays ON,
because on this demo the second question ("why that amount?") is the one the
talk is about.

---

*The original statement of the question, preserved.*

Task 5 flips the default to ON (see "Content capture" above), on the
reasoning that every attribute redacted is diagnostic capability spent. That
reasoning is not the end of the discussion — it is the honest statement of
one side of a trade this project makes deliberately and visibly, not a claim
that ON is correct for every deployment. The open question this milestone
leaves to be answered with more than a demo's worth of evidence
(**originally written as "for M2/M3 to answer"; M2 and M3 have both closed
without answering it, and M3's own spec §8 moved it explicitly to M4, "where
evals make the trade-off concrete" — restated here rather than left naming
milestones that have already passed**):

- Whether a real insurer would run this ON by default in production, given
  what capture ON actually puts in a telemetry backend as plaintext, for
  however long that backend retains spans:
  - the one templated settlement sentence the model is actually given
    (`settlement.py:122`, built only from `{claimId, amount, currency}` —
    **not** "the full claim narrative": the claim's free-text description
    never reaches the model at all, a fact `test_content_capture_matches_the_deployed_mode`'s
    own docstring relies on and an earlier revision of this paragraph
    contradicted, corrected in Task 5 fix round 1, Finding 3),
  - the full text the model produced, and
  - **the strongest item on this list**: `gen_ai.tool.call.arguments` on
    every `execute_tool` span — the literal payment amount and currency the
    model chose to propose and execute (see "Content capture" above), which
    is the concrete answer to "why that amount?" that this task's whole
    default-flip argument rests on.
- Whether a narrower middle ground exists — e.g. redacting only fields a
  policy engine (M2's Policy Decision Service) marks as sensitive, rather
  than an all-or-nothing switch — that this milestone's one env var
  deliberately does not attempt to build.

Recorded here, rather than resolved, because resolving it needs a governance
model this milestone does not yet have (see settlement.py's own "no €5,000
check" note — M1 is ungoverned by design, and this switch inherits that same
honesty: it is a lever, not a policy).

**The trade, stated concretely, now that the facts are known.** With capture
ON, `gen_ai.tool.call.arguments` on `execute_tool propose_payment` carries
`{"amount":6200,"currency":"EUR"}` — "why that amount?" is directly
answerable from the span, no reconstruction needed. With capture OFF, the
same tool-call part reduces to exactly `{"type":"tool_call","id":...,
"name":...}` (`pydantic_ai/messages.py`'s `_tool_call_otel_part`, gated on
`include_content`) and that question is unanswerable from spans — not
harder, unanswerable, because no other attribute recovers it.
`model_request_parameters` survives regardless of `NORTHSTAR_CAPTURE_CONTENT`
(gated only by the separate, always-`True` `include_model_request_parameters`)
and defines the floor of what this project retains even at maximum
redaction: the static system instructions and both tool schemas, never the
claimant's own data. That floor tells an auditor what the agent was *told it
could do*; it does not tell them what the agent was told about *this claim*,
or what it chose to do with it. Those two are genuinely different facts, and
only one of them survives full redaction.

**The experiment this open question called for** (the project owner's own
framing, preserved verbatim as the shape of the test). M4 Task 5 ran the
OFF/ON comparison at step 4's level of detail — see "The answer" above — on a
*successful* run rather than an induced failure, which is the one departure
from this shape and is named as such:

1. Induce a genuine settlement failure or a wrong-amount run.
2. Attempt root-cause diagnosis from the trace with capture **off**. Record
   exactly what cannot be determined.
3. Repeat with capture **on**. Record what became determinable, and what
   claimant data that required retaining to determine it.
4. The deliverable is the difference — the specific diagnostic questions
   answerable only by retaining data a regulated insurer would rather not
   keep. That difference is the argument, not an assertion that ON or OFF is
   simply correct.

#### What M2.1 Task 3 measured, and what it deliberately did not decide

**Superseded as the answer by M4 Task 5's section above; kept because every
figure below is still the measurement it was, and because the attribute-level
detail here is what the M4 answer rests on rather than repeats.** At the time
it was written, OQ-1 was open. `NORTHSTAR_CAPTURE_CONTENT` still defaults to
`true` (`deploy/k8s/settlement-agent.yaml`) and nothing below changes it —
that part has not changed and M4 did not change it either.** What
follows sizes the trade; it does not resolve it. The four-step experiment above
is still unrun — steps 1 and 2 need an *induced failure*, and nothing here
induced one. What was run is the narrower, cheaper half that was worth doing on
the back of a live run this task already paid for: **the same successful
scenario twice, once at the shipped default `true` and once at `false`,
comparing which `gen_ai.*` attribute keys survive.**

**The two runs this section quotes, named once so every figure below has one
owner** (fix round 1, Finding 2 — an earlier revision of this section mixed
figures from three traces while claiming two, which is this project's signature
defect: the measurements were real and the labels on them were not):

| Mode | Trace | Services in it |
|---|---|---|
| ON (`NORTHSTAR_CAPTURE_CONTENT=true`, the shipped default) | `a227b3e224e6407e9846e014476456e2` | agentgateway, payments-api, payments-mcp, settlement-agent |
| OFF (`=false`, temporary, reverted) | `d52806ff6e6147df9609d5fc6265be0d` | agentgateway, payments-api, payments-mcp, settlement-agent |

Both are the same A2A settlement of €6,200, both post-reroute (through
agentgateway — verified by the service lists above, not assumed), both
`status: executed`. Mode read back off the *running* pod with `jsonpath`, not
off the Deployment spec, before each run. **Every ON-vs-OFF figure below comes
from one of those two traces**, and every figure states which.

One other trace appears further down, named at each mention and used only to
corroborate the ON key count — it never supplies half of a comparison:
`ebccbc73929c42b195e4d778a053e61b`, the **pre-reroute** run that
proved `tests/e2e/test_gateway_trace.py` RED, also measures 28 distinct
`gen_ai.*` keys at ON. It contains no agentgateway spans, so it is deliberately
not the ON trace named above — the gateway is irrelevant to what
`NORTHSTAR_CAPTURE_CONTENT` gates, but a like-for-like ON/OFF pair should differ
in exactly one variable, and this one differs in two.

**Measured: 28 distinct `gen_ai.*` keys at ON, 25 at OFF. Exactly three
disappear:**

| Key | ON | OFF | Span(s) it lives on |
|---|---|---|---|
| `gen_ai.system_instructions` | present | **absent** | `chat claude-sonnet-5` ×3, `invoke_agent settlement_agent` |
| `gen_ai.tool.call.arguments` | present | **absent** | `execute_tool propose_payment`, `execute_tool execute_payment` |
| `gen_ai.tool.call.result` | present | **absent** | `execute_tool propose_payment`, `execute_tool execute_payment` |

That is exactly the three this document's own inventory section above already
listed as content-gated, now confirmed by direct OFF-mode measurement rather
than from library source alone. The other 25 are the identical set in both
modes: `gen_ai.agent.call.id`,
`gen_ai.agent.name`, the five `gen_ai.aggregated_usage.*`, `gen_ai.conversation.id`,
`gen_ai.input.messages`, `gen_ai.operation.name`, `gen_ai.output.messages`,
`gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.finish_reasons`,
`gen_ai.response.id`, `gen_ai.response.model`, `gen_ai.system`,
`gen_ai.tool.call.id`, `gen_ai.tool.definitions`, `gen_ai.tool.name`, and the
five `gen_ai.usage.*`.

**Key-level survival understates the redaction, and that is the most important
thing this measurement found.** Three keys vanish, but two of the survivors are
gutted in place:

- `gen_ai.input.messages` / `gen_ai.output.messages` survive at both settings
  and still carry every part type (`text`, `thinking`, `tool_call`,
  `tool_call_response`), but at OFF **no part carries a `content` key and no
  `tool_call` part carries an `arguments` key** — measured across all three
  chat turns of each run, on the two traces named above:

  | Surface | ON (`a227b3e2…`) | OFF (`d52806ff…`) |
  |---|---|---|
  | `gen_ai.input.messages`, `text`/`thinking` parts carrying `content` | 8 of 8 | **0 of 5** |
  | `gen_ai.input.messages`, `tool_call` parts carrying `arguments` | 3 of 3 | **0 of 3** |
  | `gen_ai.output.messages`, `text`/`thinking` parts carrying `content` | 4 of 4 | **0 of 2** |
  | `gen_ai.output.messages`, `tool_call` parts carrying `arguments` | 2 of 2 | **0 of 2** |
  | `pydantic_ai.all_messages`, `text`/`thinking` parts carrying `content` | 5 of 5 | **0 of 3** |
  | `pydantic_ai.all_messages`, `tool_call` parts carrying `arguments` | 2 of 2 | **0 of 2** |

  The denominators differ between the two columns because the model produced a
  different number of parts on each run — these are two live runs of one
  scenario, not one run replayed. That is why the load-bearing statement is
  "every one at ON, none at OFF" rather than a ratio comparison. Denominators
  count only the part types that can carry the key at all: `text`/`thinking`
  for `content`, `tool_call` for `arguments` (a `tool_call` part never carries
  `content` at either setting, so counting it in that denominator would
  understate ON).
- `pydantic_ai.all_messages` on the `invoke_agent` span — the full transcript,
  and the single highest-risk surface in the trace — survives too, and shrank
  from **1599** characters at ON (`a227b3e2…`) to **657** at OFF
  (`d52806ff…`). Same part types, no content. (For completeness, since the
  corroborating pre-reroute ON trace is a different run: on `ebccbc73…` the
  same attribute is 1318 characters. Run-to-run variation in how much the model
  wrote — not a mode effect, which is exactly why the ON figure published here
  is the one from the named ON trace.)

So a reader who checks only "which keys are present" would conclude that
redaction removed 3 of 28 attributes, roughly a tenth. What it actually removed
is every piece of model-authored and model-received prose plus every tool
argument and result, while leaving the skeleton that proves the calls happened.

**`model_request_parameters` is the floor, and its name is not what the
prediction said it was.** This task was handed a falsifiable prediction derived
from reading pydantic-ai 2.41.0's source, and recording where it was wrong
matters more than where it was right:

- ✅ **Right:** `gen_ai.tool.call.arguments` is `include_content`-gated.
  Confirmed — absent at OFF.
- ✅ **Right:** `gen_ai.system_instructions` is `include_content`-gated.
  Confirmed — absent at OFF.
- ✅ **Right:** `gen_ai.tool.definitions` is emitted regardless. Confirmed —
  present in both sets.
- ✅ **Right:** 28 distinct `gen_ai.*` keys at the shipped default. Confirmed
  independently on this milestone's own live run, matching the previous
  milestone's count.
- ❌ **Wrong on the name.** The prediction called it
  `gen_ai.model_request_parameters`. The attribute pydantic-ai actually emits is
  `model_request_parameters`, with **no `gen_ai.` prefix** — so it appears in
  neither the ON set nor the OFF set, because it is not a `gen_ai.*` key at all.
  (This document's own earlier sections already used the unprefixed spelling;
  the prediction's prefixed form does not exist anywhere in a live trace.)
- ❌ **Wrong on "the sole OFF-mode survivor of the content-bearing keys."**
  Measured: `model_request_parameters` does survive OFF — 1974 characters on
  every `chat` span, identical in both modes, which is the same figure this
  document already records — so the *gating* half of that bullet holds. But it
  is not the sole survivor. `gen_ai.tool.definitions` survives fully, and
  `gen_ai.input.messages`, `gen_ai.output.messages` and
  `pydantic_ai.all_messages` all survive in redacted form. Calling it the sole
  survivor would have made OFF mode sound emptier than it is.
- ➕ **Missed by the prediction:** `gen_ai.tool.call.result` is content-gated
  too. The prediction named two content-gated keys; there are three. (This
  document's inventory section above already had all three, from the previous
  milestone — so the gap was in the prediction handed to this task, not in the
  project's own record. Worth stating because the missed one is the attribute
  carrying the ledger's record back, e.g.
  `{"paymentId":"PAY-…","amount":6200,…}`: at OFF, *neither* what the model
  proposed nor what the ledger recorded survives on the `execute_tool` spans.)

**What this does and does not settle.** It sizes the trade precisely: at maximum
redaction this project still retains what the agent was *told it could do*
(`model_request_parameters`, `gen_ai.tool.definitions`), that it called both
tools and in what order (`gen_ai.tool.name`, `gen_ai.tool.call.id`, message
shape), what it cost (`gen_ai.usage.*`, `operation.cost`), and the full trace
topology. It retains nothing about *this claim* or *this amount*. The
diagnostic question "why €6,200?" is, as this section already said,
unanswerable rather than merely harder — now confirmed by measurement in both
directions rather than from source reading alone. Whether that trade is
acceptable for a regulated insurer is still a decision this project's owner
makes, not one a measurement makes, and it is still open.

This is not academic bookkeeping: M2's Policy Decision Service and a later
reviewer UI both need to know *what the agent was told* to do their own
jobs — a policy engine cannot weigh a decision it cannot see the grounds for,
and a human reviewer cannot approve or reject what they were never shown.
Whichever way this trade is eventually resolved constrains how those two
components get designed, not only how this milestone's one env var happens
to default.

**M3 update: that reviewer UI now exists, and the prediction above is half
right in a way worth recording rather than claiming as a hit.** `reviewer-ui`
does show the human what the model said — the refusal page renders the
`narrative` verbatim — but it reads it from **payments-api's ledger**, not from
telemetry: `settlement.py`'s refused branch POSTs the model's own
`result.output` to `/payments/{id}/refuse`, the ledger stores it, and
`apps/reviewer-ui/ledger.py` fetches it back over plain HTTP. So the reviewer's
view of "what the agent said" is entirely independent of
`NORTHSTAR_CAPTURE_CONTENT`: flipping capture OFF would strip
`gen_ai.output.messages`' content and leave the reviewer page unchanged. That
is not a refutation of the paragraph above — the reviewer still cannot see
*what the agent was told*, only what it concluded, and "why €6,200?" is exactly
as unanswerable to the human as it is to the trace at OFF. But the coupling the
paragraph predicted did not materialise as a coupling: M3 routed the one piece
of model prose a human needs through the ledger, where the honesty rule already
governs it, instead of through spans. Recorded because the prediction is the
kind that quietly gets read as confirmed.

### Ruling 5: retired (M2.1 Task 6), not applied a second milestone running

M1.2's final review recorded, as observation O-1, that Ruling 5 — settling on
one asyncio test style rather than mixing explicit `@pytest.mark.asyncio`
markers with `pytest.ini`'s `asyncio_mode = auto` inside the same service —
was decided and never carried out. M2's own design spec broadened that same
open item to the repo-wide shape it actually has today: two genuinely
different asyncio test styles coexist, not just mixed markers within one
suite. Measured directly (`grep -rln "async def test_\|pytest.mark.asyncio"`
vs. a suite-by-suite check for the absence of `async def test_` alongside
the presence of `anyio.run`/`asyncio.run`):

- **`async def test_*` under `asyncio_mode = auto`** — `agents/claims-agent/tests/test_delegation.py`,
  `agents/settlement-agent/tests/test_executor_reply.py`,
  `agents/settlement-agent/tests/test_mcp_tools.py` (no explicit markers,
  relying on `auto` alone — the exact O-1 shape), and
  `agents/settlement-agent/tests/test_settlement.py` (explicit markers,
  also the O-1 shape).
- **Synchronous tests wrapping `anyio.run()`/`asyncio.run()`** —
  `tests/e2e/test_payments_mcp_trace.py` and every test in
  `tools/payments-mcp/tests/`. Neither directory defines any `async def
  test_*` function and neither carries a `pytest.ini`/`pyproject.toml`
  `asyncio_mode` setting — `pytest-asyncio` is not in the execution path for
  either at all.

**Retired rather than applied**, for the reason the M1.2 Task 7 review
already established and this task did not need to re-derive: converting the
synchronous-wrapper suites to `async def test_*` would buy nothing but
cosmetic consistency, while the wrapper style already has zero exposure to a
`pytest-asyncio` version bump — Task 7's own review confirmed pytest-asyncio
is not on `tests/requirements.txt`'s or `tools/payments-mcp/requirements.txt`'s
dependency list at all, so bumping `pytest-asyncio` anywhere else in this
repo "carries zero risk for these suites regardless" (verbatim from that
review). That is a genuine argument in the mixed styles' favour, not a
defect papered over: the two styles exist along exactly the boundary between
"a service with its own `pytest-asyncio` plugin already in its dependency
closure" and "a suite with no such plugin, driving its async calls by hand."
Converting either direction crosses that boundary for no functional gain and
real risk of breaking a suite that currently has none of pytest-asyncio's
own version-compatibility surface to worry about. A decision recorded twice
and applied never is worse than a decision withdrawn once, cleanly, with the
reasoning attached — this is that withdrawal. No further action is expected
of a future milestone on this point.

### N7, carried: retyped counts published in two or more places, not closed by this fix wave

**2026-09-15, whole-branch review, fix wave Part B.** C1, J4, J5, J6, J7, J8,
N2, N4 and N5 are closed above, each with its own dated note (and, where the
finding was a guard gap rather than a one-time correction, a new test —
`test_telemetry_contract_export_destinations.py` for C1,
`test_doc_status_lines_name_the_current_milestone.py` for J5, and an
extension to `test_telemetry_contract_counts.py` for J7 — each mutated and
confirmed RED for its own reason). **N7 itself is explicitly not required to
be closed in full** (per the fix-wave brief) and is not: it is large, spans
many sites across two documents, and some of it predates M5. Left open,
dated, so it is carried rather than lost:

- **114 distinct span attribute keys**, cited at this document's own
  "measured 2026-09-13" paragraph and again in
  `deploy/observability/collector-config.yaml`'s comment on the same
  measurement. Two sites, currently in agreement; neither is guarded.
- **Ten `policy.*` keys**, cited at four sites in this document, one of which
  says "Measured, not retyped" immediately before retyping the figure three
  more times in the same section. The irony is the reason to close this one
  first, whenever it is picked up.
- **Six `review.*` keys**, cited at four sites in this document.
- **The GenAI metric counts** (twelve/six/one, in various framings), cited at
  eleven sites across this document and README.md — the largest single count
  in N7 by site count.
- **Ten Prometheus scrape jobs**, cited at seven sites.
- **`28`/`25` attribute names** in
  `agents/settlement-agent/requirements.txt:122-123`, outside every guard.
- **The `125`/`123`/`119`/`117` whole-trace totals**, cited at five and two
  sites respectively — notable because this document argues at length,
  elsewhere in its own text, that whole-trace totals must not be published,
  then publishes four of them twice regardless.
- **This file's own inlined snapshot** at the per-service-suite guard's
  module docstring (`tests/unit/test_telemetry_contract_counts.py`'s
  "as of M3 Task 5B" comment) — four of seven figures there have already
  drifted from the six-suite table above them, and `eval-scorer` is missing
  from it entirely. Dated, and therefore self-mitigating, but not corrected.

None of these carries the guard C1 got (derive-from-source) or the
correction J4–J8 got (reword-and-date). Closing them is future work, in the
same style as everything above: derive the figure from something that
already computes it, or extend an existing test by the smallest number of
assertions that makes the duplication visible, rather than retype either
copy by hand.
