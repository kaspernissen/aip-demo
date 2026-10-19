# Slide plan: Verticalizing Platform Intelligence for Regulated Domains

KubeCon + CloudNativeCon North America 2026, Salt Lake City. **Wed 11 Nov, 11:00–11:30, Salt Palace
Level 2, 255 EF** (Platform Engineering, Advanced). The slot is 30 minutes. This is the **long cut, 50:30 of content**, built on purpose so Kasper can cut
down to the slot himself (his call, 2026-09-28).

**Speakers:** William Rizzo (Global Field CTO, Mirantis) and Kasper Borg Nissen (Principal Developer
Advocate, Dash0). William bookends: he opens with the regulators' questions and Act 1 (up to "Decide.
Trace. Prove."; "Where this comes from" is shared), and closes from "From requirement to artifact".
Kasper runs Acts 2–4 in between: tracing, the tools, the live demo he built, and the evidence. The
*Who* column is read from each note's first line.

**Shape of the long cut:** opens on the auditor's questions (paid off at "From requirement to
artifact"); concept slides before every dense visual; a tools section (toolbox, SPIFFE/SPIRE,
agentgateway, the decision and live policy), each tagged with its ring; one contiguous live block, "The demo" table and then five steps,
each with a hidden transcript fallback; the "what you just saw" deep dives after it, cuttable; three speaker-only appendix slides after "Thank you" (pre-flight, reset,
triage). The table below is generated from the fragments' comments and notes by `python3 build/plan.py`,
so re-generate rather than edit.

**Subtitle / thesis:** *If you can't trace it, you can't govern it.*

> Agents change the nature of complexity, so the platform has to **decide** what an agent may do,
> and **prove** it afterwards from the **trace**. We already have the open-source pieces to do both.

**What "decide" means** is William's *Bounded Agent Autonomy* (OSFF 2026): *you don't trust an agent
to remember its own authority limits; you make the platform enforce them.* His rings structure Act 3,
and every tools-section slide carries its ring as margin furniture (`.ring`, top right):
**Core · Identity** (who it is: SPIFFE/SPIRE), **Ring 1 · Reach** (what it connects to: Cilium, step 1),
**Ring 2 · Capability** (what it may do: agentgateway, the decision service, OpenFeature), and the
**Keystone** (it can never edit what constrains it: Kyverno, step 4). Steps 1 and 4 need stage 7
(M7, `docs/superpowers/specs/2026-10-12-northstar-m7-bounded-design.md`).

Source of every fact: the research book *If You Can't Trace It*. There are two editions: the study
edition (154 pp) and the full edition (303 pp), with the same chapter numbers. Ch 14 is the talk
plan this deck implements. This deck adds one thing to Ch 14: the slide "What a governed foundation needs" maps the abstract's five
building blocks, so the talk delivers what the CFP abstract promises.

**Origin tags:**
- `DID WHAT`: rebuilt from *Your Agent Did WHAT?* (Adriana Villela and Kasper, SRE Day London 2026).
- `TAMING`: rebuilt from *Taming Complexity* (Mauricio Salatino and Kasper, KubeCon EU).
- `BOUNDED`: derived from William Rizzo's demo *Bounded Agent Autonomy* (OSFF 2026): the rings, the
  reach and keystone beats, and the "hide" layer.
- `NEW`: built from Northstar.

**Design:** the Broadsheet design system (`bs/`, see `bs/GUIDE.md`): paper ground, Source Serif 4,
cyan = traced or permitted, magenta = governed, refused, or the gateway. Most diagrams are the book's own
figures, lifted as inline SVG and recoloured by the `.bfig` layer in `build/talk-base-bs.css`.

**The live demo** is five steps (`./scripts/demo guard`; a claim a human approves, in two browser
tabs or with `claim 5800` and `approve last`; `autonomy`, `claim 5800`, `claim 6200`; `keystone`;
`evidence`), the live block's notes add up to 5:10 with narration (spec §4 says about 5:00),
run against Northstar at stage 7 (runbook §5, "The five-minute flow"). Each step slide is followed by
a hidden fallback, marked `fallback`: the real transcript of that step from a stage-7 run (2026-10-12 for guard and keystone, 2026-10-15 for steps 2 and 3, 2026-10-17 for evidence),
typeset verbatim and shortened only with …. Skip it with → when the step works live. The longer
demo's browser beats (human approval, the Backstage flip, the evidence dashboard, the scorer) are
ordinary slides now, outside the five-minute cut.

| # | Fragment | Who | Start | secs | Slide | Origin | What it shows |
|---|---|---|---|---|---|---|---|
| 01 | 00-cover | W | 0:00 | 15 | Cover | NEW | Cover. The CFP title is fixed; the subtitle does the concrete work (Ch 14). |
| 02 | 10-open-act1 | W+K | 0:15 | 40 | Who? | NEW | who we are: the two speakers, portraits printed as plates (Kasper, 2026-10-19: an intro, not the lineage of talks) |
| 03 | 10-open-act1 | W | 0:55 | 20 | When an agent acts | NEW | regulated domains: four sectors where an agent acts, one line on what it moves (Ch 10, Ch 14 "Who is in the room") |
| 04 | 10-open-act1 | W | 1:15 | 60 | The auditor’s questions | NEW | the auditor's five questions, big type, each tagged with the requirement behind it (Ch 10; Ch 14 slide 35 table). The table itself comes at "From requirement to artifact". |
| 05 | 10-open-act1 | W | 2:15 | 25 | Not an answer | NEW | the stakes, as type: Ch 14 slide 09's title line |
| 06 | 10-open-act1 | W | 2:40 | 60 | The agent proposed | NEW | the cold open: a crop of the customer UI outcome block, €5,800 held for a person (M8: a hold, not a refusal) |
| 07 | 10-open-act1 | W | 3:40 | 10 | Act 1 — Agents change the nature of complexity | NEW | Act 1 divider |
| 08 | 10-open-act1 | W | 3:50 | 35 | The evolution of architectures | TAMING | evolution of architectures: book Figure 1.1, lifted; the FIXED row cut, the BROKE row and the crossing lines kept |
| 09 | 10-open-act1 | W | 4:25 | 45 | The model draws the call graph | TAMING | the model draws the call graph: book Figure 1.4 (one prompt, three runs), lifted and cropped |
| 10 | 10-open-act1 | W | 5:10 | 20 | What an agent is, in one sentence | NEW | concept: what an agent is, in one sentence (Ch 1, "the five terms") |
| 11 | 10-open-act1 | W | 5:30 | 45 | Anatomy of an agent | DID WHAT | anatomy of an agent: book Figure 1.3 (the agent loop, mermaid in the book) redrawn as SVG, inside Figure 1.2's harness |
| 12 | 10-open-act1 | W | 6:15 | 20 | What a protocol is for | NEW | concept: what a protocol is for; one JSON-RPC call and its answer (Ch 2, "In plain words") |
| 13 | 10-open-act1 | W | 6:35 | 60 | MCP, A2A and Skills | TAMING | MCP, A2A and Skills: book Figure 2.6 (vertical and horizontal) rebuilt with a skill folder, one dated fact each |
| 14 | 10-open-act1 | W | 7:35 | 45 | The decoder ring | DID WHAT | the decoder ring (book Figure 1.5, first two columns), set as type |
| 15 | 10-open-act1 | W | 8:20 | 45 | “We don’t know what the agent did” | NEW | FINRA 2026: scope and traceability, both lines verbatim from Ch 10 / Ch 14 |
| 16 | 10-open-act1 | W | 9:05 | 45 | Decide. Trace. Prove. | NEW | the thesis: three words, each with its gloss |
| 17 | 10-open-act1 | W | 9:50 | 50 | Bounded autonomy | BOUNDED | bounded autonomy, the definition of Decide: William's four rings as concentric bands, one question each (William Rizzo, Bounded Agent Autonomy, OSFF 2026); Northstar's piece per ring waits for Act 3 |
| 18 | 20-act2 | K | 10:40 | 10 | Act 2 — What tracing already gives us | NEW | Act 2 divider: what tracing already gives us |
| 19 | 20-act2 | K | 10:50 | 30 | What a span is | NEW | concept: what a span is, and how spans make a trace (Ch 3 Fig 3.2 and 3.3, values illustrative) |
| 20 | 20-act2 | K | 11:20 | 30 | Context gets lost | TAMING | context gets lost: one whole trace next to the same work split into two traces (Ch 3 parcel-label idea) |
| 21 | 20-act2 | K | 11:50 | 30 | traceparent, now also in MCP | TAMING | traceparent anatomy (Ch 3 Fig 3.4, cropped, labels shortened), then the two carriers: HTTP header and MCP params._meta (Ch 3 Fig 3.5 rows 1 and 4) |
| 22 | 20-act2 | K | 12:20 | 35 | What can gen_ai.* answer today? | DID WHAT | the "what can the conventions answer today" table (Ch 3 Fig 3.10, minus the gap row, which is slide 18); the four opt-in rows dimmed |
| 23 | 30-act3a | K | 12:55 | 10 | Act 3 — Northstar, live | NEW | Act 3 divider |
| 24 | 30-act3a | K | 13:05 | 50 | What a governed foundation needs | NEW | the abstract's five building blocks on Ch 11's blueprint (Figure 11.1, cropped; numbered 1–5 by the abstract's five building blocks, 4 = policy (4a) + evals (4b)); under it, where William's four rings land |
| 25 | 30-act3a | K | 13:55 | 45 | We've done this before | NEW | we've done this before: Ch 1 Figure 1.8 (what cloud native built, extended to agents), widened to six rows with the Northstar piece of each |
| 26 | 30-act3a | K | 14:40 | 55 | Connected is not governed | NEW | connected is not governed: the runbook's stage ladder (docs/demo-runbook.md §1, §3 Option 1; Ch 4 stage table), now seven stages with M7's Bounded, each with its one proof and the ring it adds (M7 design spec §1) |
| 27 | 30-act3a | K | 15:35 | 40 | Meet Northstar | NEW | Meet Northstar: Ch 4 Figure 4.5, claim path only |
| 28 | 30-act3a | K | 16:15 | 30 | The model can request. The platform decides. | NEW | the design rule: Ch 4 Figure 4.4 |
| 29 | 30-act3a | K | 16:45 | 30 | Enforcement point, decision point | NEW | concept: an enforcement point and a decision point (the bouncer and the manager), set before the tools |
| 30 | 30-act3a | K | 17:15 | 60 | The toolbox | NEW | the toolbox map: every tool on the claim path and around it, by job (docs/architecture.md components tables; deploy/*; Ch 2 Figure 2.12 for who governs what) |
| 31 | 30-act3a | K | 18:15 | 60 | Workload identity: SPIFFE/SPIRE | NEW | SPIFFE/SPIRE: how settlement-agent gets its JWT-SVID (deploy/platform/spire/*, architecture.md "Workload identity" and "Identity with teeth", agents/settlement-agent/svid.py) |
| 32 | 30-act3a | K | 19:15 | 45 | What agentgateway is | NEW | concept before the gateway config: what an agent gateway is, and why an API gateway is not one (deploy/platform/agentgateway/config.yaml: one mcp backend; tools/payments-mcp/server.py execute_payment) |
| 33 | 30-act3a | K | 20:00 | 60 | agentgateway: one choke point | NEW | agentgateway: the one choke point and its checks in order (deploy/platform/agentgateway/config.yaml; architecture.md "Hop 3") |
| 34 | 30-act3a | K | 21:00 | 45 | What OpenFeature is | NEW | concept before the live policy: what OpenFeature is, in four layers, and why a flag carries a policy knob (pds/autonomy.py; requirements.txt openfeature-sdk, -provider-flagd, -hooks-opentelemetry; deploy/platform/openfeature/*) |
| 35 | 30-act3a | K | 21:45 | 45 | The decision and the live policy | NEW | the decision and the live policy: pds/rules.py EXECUTE_LIMITS, pds/autonomy.py (flagd on localhost:8013), deploy/platform/openfeature/featureflag.yaml |
| 36 | 30-act3a | K | 22:30 | 370 | Live: Northstar | NEW | the demo: one dark sheet while the terminal is up; no commands on the face (Kasper, 2026-10-19). The run-sheet is the note. |
| 37 | 30-live | K | 28:40 | 40 | What you just saw | NEW | what you just saw: the five demo steps as outcomes, no commands (Kasper, 2026-10-19; replaces the five live-step slides and their transcript fallbacks) |
| 38 | 31-act3b | K | 29:20 | 45 | Identity is an input | NEW | deep dive after step 1: five 401s, and the caller as half of the lookup key (Ch 7, Figures 7.3 and 7.4) |
| 39 | 31-act3b | K | 30:05 | 45 | Reach has two layers | BOUNDED | optional: reach has two layers, after William's Linkerd reach beat, backed by the Linkerd spike's measured results (scratchpad linkerd-spike-report.md, 2026-10-12: criteria 1, 3, 4, 6, release channels). The demo runs Cilium only. |
| 40 | 31-act3b | K | 30:50 | 45 | One trace, and the absence | NEW | deep dive after step 2: the full deny trace from the customer UI, after Ch 5 Figure 5.5 and Ch 6 Figure 6.13 (trace a4007e49…) |
| 41 | 31-act3b | K | 31:35 | 45 | What agentgateway says about one governed call | NEW | the gateway's spans per governed call: Ch 6 Figure 6.7 tree with the measured key counts, plus Figure 6.15's four added keys |
| 42 | 31-act3b | K | 32:20 | 45 | HTTP 200 means no | NEW | HTTP 200 means no (Ch 6: Figure 6.15, the metrics Figure 6.24, Finding 7) |
| 43 | 31-act3b | K | 33:05 | 20 | Defence in depth | NEW | concept before the eight layers: defence in depth, as airport security |
| 44 | 31-act3b | K | 33:25 | 60 | Limiting access to MCP | NEW | the eight layers between an agent and a payment (Ch 7, Figure 7.1, lifted and re-set: "how Northstar builds it" became "what it stops") |
| 45 | 31-act3b | K | 34:25 | 40 | Two ways to say no | NEW | two ways to say no, measured: a gateway guardrail in CEL (config only; the real rule from deploy/platform/agentgateway/config.yaml) beside the decision service, the table from the M8 spec §5; the ceiling is on the deck, not in the live demo (tests/e2e/test_gateway_ceiling.py) |
| 46 | 31-act3b | K | 35:05 | 20 | Guardrails for model traffic | NEW | what agentgateway v1.6.0 ships for model traffic: read from crates/agentgateway/src/llm/policy/ at the v1.6.0 tag (mod.rs RequestGuardKind, pii/, webhook.rs, moderation.rs, bedrock_guardrails.rs, azure_content_safety.rs, google_model_armor.rs). Northstar's model calls do not go through the gateway, so none of it is in the demo (M8 spec §5) |
| 47 | 31-act3b | K | 35:25 | 40 | Hide or refuse | BOUNDED | hide or refuse, as two layers: HIDE, a static CEL allowlist (William's OSFF demo: mcp.tool.name in [...], the tool never in tools/list, HTTP 400 Unknown tool) beside REFUSE AND RECORD, Northstar's decision service (all four tools listed to settlement-agent's SVID, measured 2026-10-12; the €6,200 call depends on its arguments). Credits William; not a disagreement (bounded-ai/README.md; M7 design spec §1) |
| 48 | 31-act3b | K | 36:05 | 15 | Fail-closed vs fail-open | NEW | concept before fail-open: the door lock in a power cut |
| 49 | 31-act3b | K | 36:20 | 20 | Fail-open is one word away | NEW | failureMode: failClosed, the real config line (deploy/platform/agentgateway/config.yaml) |
| 50 | 31-act3b | K | 36:40 | 15 | A span link vs a parent edge | NEW | concept before the human approval: a span link vs a parent edge |
| 51 | 31-act3b | K | 36:55 | 45 | Maker and checker | NEW | maker and checker: trace A (the agent's work, ending at the hand-off), trace B (a person released it through the gateway, the resume, the push), the span link from B's resume back to A, and how long humans take (northstar_approval_wait_duration_seconds, docs/telemetry-contract.md; runbook §5 Beat 4) |
| 52 | 31-act3b | K | 37:40 | 30 | The same flip, with a UI | NEW | optional: the same flip, with a UI: backstage-autonomy-assisted/flipped + customer-ui-autonomous-executed + jaeger-autonomy-change (runbook §5 Beat 7, the longer version) |
| 53 | 31-act3b | K | 38:10 | 20 | The platform API is the resource | NEW | The platform API is the resource. Handover point (William Rizzo) |
| 54 | 40-act4 | K | 38:30 | 10 | Act 4 — How you prove it | NEW | Act 4 divider. The act title is Ch 14's alternative subtitle. |
| 55 | 40-act4 | K | 38:40 | 20 | Evidence vs logs | NEW | concept: evidence vs logs. A log line (Fig 3.1) against the five things the evidence view answers (Ch 9: who decided, rule, limit, human, ledger), all on spans, one join key |
| 56 | 40-act4 | K | 39:00 | 45 | Which refusals the trace can see | BOUNDED | which refusals the trace can see: one row per ring, measured in M7 (gateway 401 spans; Cilium drop has no span, Hubble not installed; northstar.policy.decision in the claim trace; Kyverno denial as its own trace, no traceparent from kubectl) |
| 57 | 40-act4 | K | 39:45 | 50 | The evidence view | NEW | the evidence view, in Jaeger (2026-10-19, replaces the OpenSearch dashboard capture): trace B of an approved claim, the northstar.review.decision span open with its link |
| 58 | 40-act4 | K | 40:35 | 30 | The decision contract is a published schema | NEW | The decision contract is a published schema (M9): a real excerpt of telemetry/registry/policy.yaml beside a real Jaeger screenshot of the same northstar.policy.decision span (jaeger-policy-decision-m9.png, 2026-10-17). Outside the live block. |
| 59 | 40-act4 | K | 41:05 | 20 | What an evaluation is | NEW | concept: what an evaluation is. The gen_ai.evaluation.result fields (Ch 3 table, Fig 3.13), parented to the judged span |
| 60 | 40-act4 | K | 41:25 | 50 | How the auditor works | NEW | How the auditor works: eval-scorer's loop, OpenTelemetry in and OpenTelemetry out (platform/eval-scorer/README.md; scorer/emit.py; scorer/opensearch.py TrajectoryTruncated; M4 design spec §5) |
| 61 | 40-act4 | K | 42:15 | 45 | A score that can go to zero | NEW | A score that can go to zero (longer-demo beat 6, as a slide): 1.0 vs 0.0 (Fig 9.3) plus jaeger-eval-scores.png |
| 62 | 40-act4 | K | 43:00 | 30 | And LLM-as-judge? | DID WHAT | "Enter LLM-as-judge", rebuilt as judge vs auditor on one output format (Ch 3 Fig 3.13, Ch 9 "What Northstar does not have") |
| 63 | 40-act4 | K | 43:30 | 45 | Two kinds of checks | NEW | two kinds of checks on the model itself: quality evals and topic guardrails (OpenLIT docs, 2026-10-19); neither is in the demo |
| 64 | 40-act4 | K | 44:15 | 45 | Content capture, a matched pair | NEW | Content capture as a matched pair: Fig 9.5 lifted (three layers, keys vs content) |
| 65 | 40-act4 | K | 45:00 | 30 | Never sample the decisions | TAMING | "More Telemetry ≠ Better Telemetry" (p.40), reworked: the 142 vs 139 defect |
| 66 | 40-act4 | W | 45:30 | 60 | From requirement to artifact | NEW | Requirement → artifact, Ch 14's six-row table; walk two rows (Art. 86 / C-203/22 and DORA) |
| 67 | 40-act4 | W | 46:30 | 30 | The deadline moved | NEW | The deadline moved: Fig 10.1 lifted (the Omnibus timeline), plus SR 26-2 |
| 68 | 40-act4 | W | 47:00 | 30 | Seventeen findings, none filed | NEW | Seventeen findings: Fig 12.1 lifted (governance home × kind of gap), plus F17 (agentgateway v1.6.0 deny as isError, docs/upstream-findings.md, after the book); chips tinted from Fig 12.2 |
| 69 | 40-act4 | W | 47:30 | 40 | Where AI observability stands | NEW | where AI observability stands: the L0–L5 ladder and the ecosystem's measured rung (Instrumenting AI Agents, Dash0 DevRel, Aug 2026, p. 10); replaces the old OTel-support maturity model, which has since changed |
| 70 | 50-close | W | 48:10 | 60 | What this does not do | NEW | Honest limitations: Ch 14's list (Backstage line kept exact in the note) plus the two abstract blocks the demo does not have (Ch 11.3; dashed like the blueprint's missing layers, Fig 11.1) |
| 71 | 50-close | W | 49:10 | 45 | Becoming a regulated-platform wizard | DID WHAT | "Becoming an AI observability wizard": the six steps as the book lists them (Ch 3 Fig 3.18), dimmed, plus Ch 14's three regulated ones |
| 72 | 50-close | W | 49:55 | 45 | What to do on Monday | NEW | The call to action: Ch 14's five verbs, ending on "Seventeen findings. Pick one." |
| 73 | 50-close | W | 50:40 | 30 | Three things to remember | NEW | Three things to remember: the layout of Figure 14.4 (study ed.; 14.9 full ed.), its text reworded after M8 for the talk's spine (guardrails, instrumented decisions, upstream projects), plus the callback. No QR: the book isn't hosted yet. |
| 74 | 90-thanks | W | — | 0 | Thank you |  |  |

**Check before presenting:** every fast-moving fact on a slide carries a `[verify: …]` line in its
speaker note. They map to Appendix B of the book.
