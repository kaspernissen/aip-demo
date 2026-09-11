# Northstar Telemetry Contract

Status: M1.1, revised through M1.2 for the settlement-agent's model-driven
decision logic, through M2.1 for agentgateway's interposition and SPIRE's
workload identity, through M2.2 (Decided) for the Policy Decision
Service, the `policy.decision` span (which closes Gap 3), and agentgateway's
own guardrail telemetry, and through **M3 (Supervised)** for the two spans
this repo owns outright on the human-in-the-loop path —
`settlement.execute_approved` (with this project's first span link) and
`review.decision` — which between them close Gap 2. Revised at every
milestone.

## The governing rule

Use the telemetry the frameworks actually emit. Verify per component. Instrument
only our own code. Where a convention gap exists, report it upstream — never
paper over it with a bespoke convention.

## What the default suite proves, and what only the full run proves

A bare `pytest tests/` run (no environment variable set) skips
`tests/e2e/test_browser_root_span.py` entirely — that file's own `pytestmark`
is gated behind `RUN_BROWSER_TESTS=1` (it needs a real Chromium via
Playwright). That default run proves the chain from claims-agent down to
payments-api: claims-agent → settlement-agent → payments-mcp → payments-api,
one trace, no orphans, `northstar.claim.id` correlated as far as the boundary
below states. **It proves nothing about the browser.** It does not start a
browser, does not exercise CORS, and does not confirm the root span the
milestone's whole claim depends on — that customer-ui, not a test harness,
owns the trace root.

**Fix round (final-review.md SF-8): that default run needs the live cluster
by design (always has, since M1.1) and, since M1.2, is no longer
token-free.** `test_settlement_agent_trace.py` and `test_claims_chain_trace.py`
are the two ungated files that prove the chain described above; Tasks 2-3
turned settlement-agent's decision into a real three-turn Claude call, so
every bare `pytest tests/` invocation now drives two live model-driven
settlements as a side effect of proving that chain — genuine cross-task
drift Task 4's cost-gate rationale (below) did not account for, since it
only reasoned about the one new gated file. This is deliberately left
ungated rather than folded behind `RUN_BROWSER_TESTS=1`: gating them would
make a bare `pytest tests/` prove nothing about the backend chain at all,
which is the property this section exists to describe, in exchange for
token-freedom this run was never actually free of at the cluster-dependency
level. `scripts/smoke-test.sh` already spends these tokens unconditionally
via the gate below, so the practical cost is one bare `pytest tests/` run's
worth of tokens, not a new one — the fix here is documenting that reality
plainly rather than restructuring around it.

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

**M3 (Supervised), final-review fix round — the current figures.** A second
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
| Default (bypasses the gate) | `pytest tests/` (no env var) | `191 passed, 33 skipped, 1 xfailed` |
| The gate itself | `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` | `224 passed, 1 xfailed` |

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

Per-service unit suites (all seven run by `smoke-test.sh`, before any e2e test):
claims-agent 16, settlement-agent 108, payments-mcp 15, payments-api (Go) 31,
customer-ui (vitest) 6, policy-decision-service 105,
reviewer-ui 56 — all passing.
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
| settlement-agent | `a2a-sdk[telemetry]` + `opentelemetry-instrumentation-starlette` + `mcp` client | `a2a-sdk[telemetry]==0.3.7`, `mcp==1.28.1` (Task 7; was `1.9.4`), `opentelemetry-instrumentation-starlette==0.65b0` | Same shape as claims-agent (`POST /` ASGI entry span, `a2a.server.*` call-graph spans), plus outbound `httpx` CLIENT spans (`POST`/`GET`/`DELETE`) to payments-mcp, plus — as of M1.2 Task 8 — outbound `httpx2` CLIENT spans (`POST`) to `api.anthropic.com`, nested under each `chat` span | Only agent permitted to reach payments-mcp. Same `StarletteInstrumentor` fix as claims-agent for inbound extraction. **`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set** (`deploy/k8s/settlement-agent.yaml`), same reason as claims-agent — measured legacy-by-default, confirmed stable after the opt-in (`agents/settlement-agent/NOTES.md` §6); the same opt-in applies to both `HTTPXClientInstrumentor` and `HTTPX2ClientInstrumentor`, since both share one `get_tracer` call in the same module. |
| settlement-agent (model) | PydanticAI `Agent.instrument_all(InstrumentationSettings(...))` (`bootstrap.py`) | `pydantic-ai-slim==2.41.0`, `version=5` (the library default, pinned explicitly) | `invoke_agent settlement_agent` (internal, ×1/request), `chat claude-sonnet-5` (client, ×3/request — one per model turn: propose, execute, final narrative), `execute_tool propose_payment`, `execute_tool execute_payment` (internal, ×1 each) | As of M1.2: the settlement decision itself, not only its transport. **28 distinct `gen_ai.*` attribute names measured at ON (25 at OFF)** across these four span shapes — full inventory and per-span breakdown below. (This cell read a bare "25 distinct" from M1.2 until M2.1 Task 3 fix round 1: the OFF-mode figure, in a cell whose own next clause states the default is ON. M1.2's SF-3 fix round corrected the inventory 165 lines below to 28 and never touched this summary — the fifth time in this project that fixing an instance missed a sibling, and the first with the sibling in the same file. Every attribute count in this document now carries its mode — there are **six** mode-carrying such sites (**eight** assignments in total) — and `tests/unit/test_telemetry_contract_attribute_counts.py` fails if any is unqualified, disagrees with the inventory it summarises, or has the ON and OFF figures the wrong way round. The unit matters, because two defensible numbers exist here and they differ: a *site* is a published count anchored on `gen_ai.*` or on the word "names", which is what a reader would point at and call a claim, and there are six of those, all six naming a mode; the guard additionally holds to the right mode any bare integer that merely equals one of the two figures, which is what makes the second half of "28 at ON, 25 at OFF" checkable rather than decorative, and counting those as well gives eight assignments. "Site" is the unit this sentence is about, so six is the figure it publishes, with eight stated beside it so the two cannot later be mistaken for each other. (This read "there are nine of them" from M2.1 Task 4 until Task 5's fix round measured it — **nine was wrong in both units**. It was never guarded, and because it is the sentence that tells a reader how much of this document the guard covers, it propagated from here into four separate task briefs before anyone checked it: the counts it describes were correct the whole time, and only the count *of* them drifted, precisely because the site total was the one number with nothing asserting it. `test_the_documents_own_count_of_its_attribute_count_sites_is_right` now derives both figures from the checker's own functions rather than from anything typed by hand.) That last check arrived in fix round 2: the guard's first version tested which numbers appeared in a sentence rather than which mode each was assigned to, so it would have passed "25 at ON, 28 at OFF".) `include_content` is **not** hardcoded — it reads `NORTHSTAR_CAPTURE_CONTENT` (default ON; see "Content capture" below) — and `include_model_request_parameters` is left at its library default (`True`), never passed explicitly. |
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
milestone whose whole product is the telemetry these libraries emit. On the
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

## Export destinations: Jaeger and Dash0, and they are not symmetric

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
pipeline definitions — **the two pipelines are not symmetric**:

| Signal | Exporters |
|---|---|
| Traces | `otlp_grpc/jaeger`, `otlp_grpc/dash0`, `debug` |
| Metrics | `otlp_grpc/dash0`, `debug` |
| Logs | `otlp_grpc/dash0`, `debug` |

**Traces go to both Jaeger and Dash0; metrics and logs go to Dash0 only.**
Jaeger is a traces-only backend by design, so it was never a candidate for
the other two signals — this is not an oversight, it is what "traces to a
vendor-neutral OSS backend, everything else to whichever vendor is
configured" looks like when the OSS backend genuinely can't carry metrics or
logs. **Dash0 is therefore strictly richer than the on-stage Jaeger view**:
anyone looking at Dash0 sees the same traces plus metrics and logs that never
reach Jaeger at all.

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
The emitting library is `pydantic-ai-slim==2.41.0`
(`agents/settlement-agent/requirements.txt`).

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

**Four span shapes carry `gen_ai.*`**, emitted by scope `pydantic-ai` 2.41.0
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
pydantic-ai extension with no counterpart in the GenAI semantic conventions,
carrying per-run token roll-ups on the `invoke_agent` span; and
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
  settlement-agent  POST                                  client   (httpx, to agentgateway)
    agentgateway    tools/call                             server
      agentgateway  tools/call payments-mcp_propose_payment client
        payments-mcp  tools/call propose_payment            server
          payments-mcp  POST                                client
            payments-api  POST /payments/propose            server
```

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
  the gateway's own `tools/call` SERVER span for that call.
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

## agentgateway instrumentation, as measured (M2.1 Task 3)

`cr.agentgateway.dev/agentgateway:v1.5.0`, standalone (ConfigMap + Deployment +
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

Read off a live span; 22 keys, the complete set, nothing omitted:

```
duration                     e.g. "3ms"  (a string; see the note below)
gateway                      "default/default"
gen_ai.tool.name             "propose_payment" | "execute_payment"
http.host                    "agentgateway.northstar-platform.svc.cluster.local"
http.method                  "POST"
http.path                    "/mcp"
http.status                  200
http.version                 "HTTP/1.1"
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
span.id                      the span's own id, repeated as an attribute
src.addr                     e.g. "10.244.0.138:56988"
trace.id                     the trace's own id, repeated as an attribute
url.scheme                   "http"
```

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
and a hand-minted `traceparent`, then looking the trace up by that exact id
(`38d4634f1ae44908bbb75a06c6198086`). The gateway joined that trace rather
than starting one of its own, and emitted exactly one span. Its attribute
set, with Jaeger's own two excluded as described below —
20 keys, the complete set, nothing omitted:

```
duration                        "0ms"
error                           true
gateway                         "default/default"
http.host                       "127.0.0.1"
http.method                     "POST"
http.path                       "/mcp"
http.status                     401
http.version                    "HTTP/1.1"
listener                        "listener0"
network.protocol.version        "1.1"
otel.scope.name                 "agentgateway"
otel.status_code                "ERROR"
otel.status_description         "authentication failure: no bearer token found"
protocol                        "http"
reason                          "JwtAuth"
route                           "default/route0"
span.id                         "bcdaf74f718bd242"
src.addr                        "127.0.0.1:46138"
trace.id                        "38d4634f1ae44908bbb75a06c6198086"
url.scheme                      "http"
```

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
  and **`error = true`**. Neither appears on any accepted span.

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
   there.** `http.method`, `http.path`,
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
- It survives because `bootstrap.py` runs
  `HTTPXClientInstrumentor().instrument()` and the MCP streamable-HTTP
  transport is httpx-based, so every outbound MCP request **also** carries an
  auto-injected `traceparent` HTTP header. Measured against an echo server:
  `00-df60a027741941a16675b35edbcf0a32-5675a787e51b87e6-03`, with no manual
  injection anywhere in the call path.

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
call the policy refuses carries **26 keys** on the same span — those same 22
plus exactly four, and the four are the whole of the difference:

```
duration                     "1ms"
error                        true
gateway                      "default/default"
gen_ai.tool.name             "execute_payment"
http.host                    "agentgateway.northstar-platform.svc.cluster.local"
http.method                  "POST"
http.path                    "/mcp"
http.status                  200
http.version                 "HTTP/1.1"
jwt.sub                      "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
listener                     "listener0"
mcp.method.name              "tools/call"
mcp.resource.type            "tool"
mcp.session.id               (base64 JSON, same form as the accepted span)
mcp.target                   "payments-mcp"
network.protocol.version     "1.1"
otel.scope.name              "agentgateway"
otel.status_code             "ERROR"
otel.status_description      "mcp: mcpGuardrails rejected: requested 6200 EUR exceeds the 6000 EUR limit for this agent at autonomy level assisted"
protocol                     "mcp"
reason                       "MCP"
route                        "default/route0"
span.id                      "de60313895471382"
src.addr                     "10.244.0.149:38056"
trace.id                     "b05167ba11744cff81bcedce615cb379"
url.scheme                   "http"
```

The four that only a refusal carries are `error`, `otel.status_code`,
`otel.status_description` and `reason`. Five observations, all measured:

- **`http.status` is 200, not 4xx.** ExtMCP's refusal is a JSON-RPC error
  inside a successful HTTP response — measured on the wire in M2.2 Task 1 and
  confirmed here on the span. It matches neither the native CEL
  `mcpAuthorization` mechanism's 400 nor `jwtAuth`'s 401, both of which this
  project has also measured on v1.5.0. Anyone alerting on HTTP status alone
  will not see this refusal at all.
- **`reason = "MCP"`, and that is all the categorisation there is.** On the
  M2.1 jwtAuth refusal documented above the same key reads `"JwtAuth"`. So the
  key does separate an authentication refusal from a policy refusal, but it
  names the *layer*, not the rule — `"MCP"` is as specific as v1.5.0 gets.
- **`otel.status_description` carries the PDS's own reason string verbatim**,
  behind a fixed `mcp: mcpGuardrails rejected: ` prefix the gateway adds.
  `AuthorizationError.reason` is what lands there. This is the one place the
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
`tests/unit/test_stage2_pds_config.py` asserts against, so the source, the
guard and this contract cannot drift into three different lists. The `policy.`
prefix is deliberate and so is its absence of a `northstar.` one: a reader
scanning a span must be able to tell at a glance which attributes came from a
framework (`gen_ai.*`, `mcp.*`, `http.*`) and which this project invented, and
this whole section is the second kind.

**Read off a live span, not off that file.** The block below is the deny
decision from trace `b05167ba11744cff81bcedce615cb379`, dumped from Jaeger and
compared key-for-key against `telemetry.py`'s declared `ATTRIBUTES`. They
agree, and the check was worth making in that direction: the source declares
nine `policy.*` keys, and how many of them a given span actually carries
depends on the decision, because `server.py` writes the optional ones only
when it has a value (`if decision.limit is not None: span.set_attribute(...)`,
and likewise for the caller, tool, amount and currency). Absent reads as
absent; a zero amount would read as a request for nothing.

### Attributes on the `policy.decision` span

Ten keys on a limit-rule decision — the nine `policy.*` names plus the scope —
which is the complete set for the span below, nothing omitted, with Jaeger's
own `internal.span.format` and `span.kind` excluded by the same convention as
the agentgateway blocks:

```
otel.scope.name              "northstar.policy-decision-service"
policy.autonomy.level        "assisted"
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
rule that fired; and `policy.limit.amount` is **absent** whenever the rule that
fired has no limit to state. Concretely, from the two traces:

| Trace | Tool | `policy.rule` | `policy.decision` | `policy.limit.amount` | Keys |
|---|---|---|---|---|---|
| `b05167ba…` (denial) | `propose_payment` | `unlimited_tool` | `permit` | absent | 9 |
| `b05167ba…` (denial) | `execute_payment` | `settlement-agent.assisted.execute_limit` | `deny` | `6000` | 10 |
| `88cdfbcf…` (permit) | `propose_payment` | `unlimited_tool` | `permit` | absent | 9 |
| `88cdfbcf…` (permit) | `execute_payment` | `settlement-agent.assisted.execute_limit` | `permit` | `6000` | 10 |

So **two `policy.decision` spans appear per settlement**, one per tool call,
and the pair is the interesting artefact rather than either span alone: the
same caller, at the same autonomy level, is permitted to *propose* €6,200 and
refused permission to *execute* it. `propose_payment` is in the PDS's
`UNLIMITED_TOOLS` set because proposing is reversible and executing moves
money, and the trace shows that distinction being made rather than asserted.

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

`tests/e2e/test_supervised.py`'s criterion 5 asserts the decision-path
equivalent on every gate run, identified by **parentage** rather than by span
name: the approved-execution trace contains a second payments-api
`GET /payments/{paymentId}` span, from the `get_payment` MCP call
`execute_approved` makes one hop downstream, and only one of the two is a
child of reviewer-ui's own CLIENT span.

## Conventions adopted

- **GenAI semconv** for model calls, exercised as of M1.2 — see "PydanticAI
  instrumentation, as measured" above for the full attribute inventory.
- **MCP semconv** (`mcp.method.name`, `mcp.session.id`, `mcp.protocol.version`),
  with tool names in `gen_ai.tool.name`. `mcp.tool.name` does not exist.
- **HTTP semconv** for the Go system of record and A2A's transport spans.
- **MCP trace context via `params._meta`** (SEP-414), keys unprefixed. Not HTTP headers.

## Domain attributes we own

| Attribute | Set by | Propagation | Applies to |
|---|---|---|---|
| `northstar.claim.id` | claims-agent at intake | W3C baggage | See "Real, measured coverage" below — **not** every span everywhere |
| `northstar.policy.number` | customer-ui root span | none | The root span only |
| `policy.*` (nine keys, M2.2) | policy-decision-service, on its own `policy.decision` span | none — set locally, never propagated | That one span only. Keys declared in `platform/policy-decision-service/pds/telemetry.py`; measured inventory under "The policy decision, as measured (M2.2)" |
| `northstar.payment.refusal_cause` (M2.2 Task 4) | payments-api, on its own server span for a refused execute; two closed values, `amount_mismatch` and `already_executed` (`systems/payments-api/handlers.go`) | none | payments-api's execute span, and **only when it refuses** — absent on success, so its presence means "refused here". Read off a real recorded span by that service's own Go tests via an in-memory `tracetest.SpanRecorder`; it appears on **none** of the six M2.2 traces named in this document, because neither the denied nor the permitted scenario reaches a 409 |
| `northstar.payment.id`, `northstar.approval.reviewer_id` (M3 Task 3) | settlement-agent, on its own `settlement.execute_approved` span (`agents/settlement-agent/settlement.py`) | none | That span only, on the human-approved execution path. See "settlement-agent's own instrumentation" above, including this milestone's first span **link** and the same span's `northstar.claim.id` baggage (M3 Task 3 review, R16) |
| `review.*` (six keys, M3 Task 4) | reviewer-ui, on its own `review.decision` span (`apps/reviewer-ui/review.py`) | none | That span only, one per submitted approve/reject decision — reviewer-ui's second owned span, `review.queue` (M3 final-review fix round), deliberately carries **none** of them, and no attributes at all. Two of the six (`review.refused.trace_id`, `review.elapsed_ms`) are now set *after* the span opens rather than at creation, because the ledger read they come from moved inside it; a decision that never reaches a refused record therefore emits the other four and an ERROR status. See "reviewer-ui's own instrumentation" above |

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
`pydantic-ai-slim` 2.41.0, the line the review cited):
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
  not a false-quiet `xfailed`.

### Gap 5: FastMCP never emits `gen_ai.operation.name` on tool-call spans

- **Evidence:** FastMCP 4.0.3's `tools/call propose_payment`/`execute_payment`
  spans carry `gen_ai.tool.name` (e.g. `execute_payment`) correctly, but never
  `gen_ai.operation.name`, where the GenAI semconv specifies `execute_tool`
  for a tool invocation (`tools/payments-mcp/NOTES.md` §2). The sibling
  attribute is done properly; the operation classifier is simply absent.
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
`pytest==9.0.3` with its four-package closure, none of which `pds/server.py`
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
thing, and always has** — measured by reading all five Dockerfiles and all
five requirements files:

| Service | Dockerfile install | `pytest` in that file |
|---|---|---|
| `agents/settlement-agent` | `pip install --no-cache-dir -r requirements.txt` | `pytest==9.0.3`, `pytest-asyncio==1.4.0` |
| `agents/claims-agent` | same | `pytest==9.0.3`, `pytest-asyncio==1.4.0` |
| `tools/payments-mcp` | same | `pytest==9.0.3` |
| `platform/policy-decision-service` | same | `pytest==9.0.3` |
| `apps/reviewer-ui` (M3 Task 4) | same | `pytest==9.0.3`, `pytest-asyncio==1.4.0` |

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

### OQ-1: Content capture is a trade, not a settled answer

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

**The experiment this open question calls for, and has not yet run** (the
project owner's own framing, preserved verbatim as the shape of the test):

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

**OQ-1 stays open. `NORTHSTAR_CAPTURE_CONTENT` still defaults to `true`
(`deploy/k8s/settlement-agent.yaml`) and nothing below changes it.** What
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
