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
| 03 | 10-open-act1 | W | 0:55 | 40 | Verticalizing |  |  |
| 04 | 10-open-act1 | W | 1:35 | 60 | The auditor’s questions | NEW | the auditor's five questions, big type, each tagged with the requirement behind it (Ch 10; Ch 14 slide 35 table). The table itself comes at "From requirement to artifact". |
| 05 | 10-open-act1 | W | 2:35 | 25 | Not an answer | NEW | the stakes, as type: Ch 14 slide 09's title line |
| 06 | 10-open-act1 | W | 3:00 | 60 | The agent proposed | NEW | the cold open: a crop of the customer UI outcome block, €5,800 held for a person (M8: a hold, not a refusal) |
| 07 | 10-open-act1 | W | 4:00 | 10 | Act 1 — Agents change the nature of complexity | NEW | Act 1 divider |
| 08 | 10-open-act1 | W | 4:10 | 45 | The model draws the call graph | TAMING | the model draws the call graph: book Figure 1.4 (one prompt, three runs), lifted and cropped |
| 09 | 10-open-act1 | W | 4:55 | 45 | Anatomy of an agent | DID WHAT | anatomy of an agent: book Figure 1.3 (the agent loop, mermaid in the book) redrawn as SVG, inside Figure 1.2's harness |
| 10 | 10-open-act1 | W | 5:40 | 45 | Meet MCP and A2A |  |  |
| 11 | 10-open-act1 | W | 6:25 | 45 | Decide. Trace. Prove. | NEW | the thesis: three words, each with its gloss |
| 12 | 10-open-act1 | W | 7:10 | 50 | Bounded autonomy | BOUNDED | bounded autonomy, the definition of Decide: William's four rings as concentric bands, one question each (William Rizzo, Bounded Agent Autonomy, OSFF 2026); Northstar's piece per ring waits for Act 3 |
| 13 | 20-act2 | K | 8:00 | 10 | Act 2 — What tracing already gives us | NEW | Act 2 divider: what tracing already gives us |
| 14 | 20-act2 | K | 8:10 | 30 | What a span is | NEW | concept: what a span is, and how spans make a trace (Ch 3 Fig 3.2 and 3.3, values illustrative) |
| 15 | 20-act2 | K | 8:40 | 30 | Context gets lost | TAMING | context gets lost: one whole trace next to the same work split into two traces (Ch 3 parcel-label idea) |
| 16 | 20-act2 | K | 9:10 | 35 | What can gen_ai.* answer today? | DID WHAT | the "what can the conventions answer today" table (Ch 3 Fig 3.10, minus the gap row, which is slide 18); the four opt-in rows dimmed |
| 17 | 30-act3a | K | 9:45 | 10 | Act 3 — Northstar, live | NEW | Act 3 divider |
| 18 | 30-act3a | K | 9:55 | 50 | What a governed foundation needs | NEW | the abstract's five building blocks on Ch 11's blueprint (Figure 11.1, cropped; numbered 1–5 by the abstract's five building blocks, 4 = policy (4a) + evals (4b)); under it, where William's four rings land |
| 19 | 30-act3a | K | 10:45 | 40 | Meet Northstar | NEW | Meet Northstar: Ch 4 Figure 4.5, claim path only |
| 20 | 30-act3a | K | 11:25 | 30 | The model can request. The platform decides. | NEW | the design rule: Ch 4 Figure 4.4 |
| 21 | 30-act3a | K | 11:55 | 60 | The toolbox | NEW | the toolbox map: every tool on the claim path and around it, by job (docs/architecture.md components tables; deploy/*; Ch 2 Figure 2.12 for who governs what) |
| 22 | 30-act3a | K | 12:55 | 60 | Workload identity: SPIFFE/SPIRE | NEW | SPIFFE/SPIRE: how settlement-agent gets its JWT-SVID (deploy/platform/spire/*, architecture.md "Workload identity" and "Identity with teeth", agents/settlement-agent/svid.py) |
| 23 | 30-act3a | K | 13:55 | 60 | agentgateway: one choke point | NEW | agentgateway: the one choke point and its checks in order (deploy/platform/agentgateway/config.yaml; architecture.md "Hop 3") |
| 24 | 30-act3a | K | 14:55 | 45 | The decision and the live policy | NEW | the decision and the live policy: pds/rules.py EXECUTE_LIMITS, pds/autonomy.py (flagd on localhost:8013), deploy/platform/openfeature/featureflag.yaml |
| 25 | 30-act3a | K | 15:40 | 370 | Live: Northstar | NEW | the demo: one dark sheet while the terminal is up; no commands on the face (Kasper, 2026-10-19). The run-sheet is the note. |
| 26 | 30-live | K | 21:50 | 40 | What you just saw | NEW | what you just saw: the five demo steps as outcomes, no commands (Kasper, 2026-10-19; replaces the five live-step slides and their transcript fallbacks) |
| 27 | 31-act3b | K | 22:30 | 45 | One trace, and the absence | NEW | deep dive after step 2: the full deny trace from the customer UI, after Ch 5 Figure 5.5 and Ch 6 Figure 6.13 (trace a4007e49…) |
| 28 | 31-act3b | K | 23:15 | 45 | HTTP 200 means no | NEW | HTTP 200 means no (Ch 6: Figure 6.15, the metrics Figure 6.24, Finding 7) |
| 29 | 31-act3b | K | 24:00 | 40 | Two ways to say no | NEW | two ways to say no, measured: a gateway guardrail in CEL (config only; the real rule from deploy/platform/agentgateway/config.yaml) beside the decision service, the table from the M8 spec §5; the ceiling is on the deck, not in the live demo (tests/e2e/test_gateway_ceiling.py) |
| 30 | 31-act3b | K | 24:40 | 45 | Maker and checker | NEW | maker and checker: trace A (the agent's work, ending at the hand-off), trace B (a person released it through the gateway, the resume, the push), the span link from B's resume back to A, and how long humans take (northstar_approval_wait_duration_seconds, docs/telemetry-contract.md; runbook §5 Beat 4) |
| 31 | 40-act4 | K | 25:25 | 10 | Act 4 — How you prove it | NEW | Act 4 divider. The act title is Ch 14's alternative subtitle. |
| 32 | 40-act4 | K | 25:35 | 50 | The evidence view | NEW | the evidence view, in Jaeger (2026-10-19, replaces the OpenSearch dashboard capture): trace B of an approved claim, the northstar.review.decision span open with its link |
| 33 | 40-act4 | K | 26:25 | 50 | How the auditor works | NEW | How the auditor works: eval-scorer's loop, OpenTelemetry in and OpenTelemetry out (platform/eval-scorer/README.md; scorer/emit.py; scorer/opensearch.py TrajectoryTruncated; M4 design spec §5) |
| 34 | 40-act4 | K | 27:15 | 45 | Two kinds of checks | NEW | two kinds of checks on the model itself: quality evals and topic guardrails (OpenLIT docs, 2026-10-19); neither is in the demo |
| 35 | 40-act4 | K | 28:00 | 45 | Content capture, a matched pair | NEW | Content capture as a matched pair: Fig 9.5 lifted (three layers, keys vs content) |
| 36 | 40-act4 | K | 28:45 | 30 | Never sample the decisions | TAMING | "More Telemetry ≠ Better Telemetry" (p.40), reworked: the 142 vs 139 defect |
| 37 | 40-act4 | W | 29:15 | 60 | From requirement to artifact | NEW | Requirement → artifact, Ch 14's six-row table; walk two rows (Art. 86 / C-203/22 and DORA) |
| 38 | 50-close | W | 30:15 | 60 | What this does not do | NEW | Honest limitations: Ch 14's list (Backstage line kept exact in the note) plus the two abstract blocks the demo does not have (Ch 11.3; dashed like the blueprint's missing layers, Fig 11.1) |
| 39 | 50-close | W | 31:15 | 45 | What to do on Monday | NEW | The call to action: Ch 14's five verbs, ending on "Seventeen findings. Pick one." |
| 40 | 50-close | W | 32:00 | 30 | Three things to remember | NEW | Three things to remember: the layout of Figure 14.4 (study ed.; 14.9 full ed.), its text reworded after M8 for the talk's spine (guardrails, instrumented decisions, upstream projects), plus the callback. No QR: the book isn't hosted yet. |
| 41 | 90-thanks | W | — | 0 | Thank you |  |  |
| — | hidden | | | 0 | 35 slides carry data-cut and are not built: When an agent acts (10-open-act1); The evolution of architectures (10-open-act1); What an agent is, in one sentence (10-open-act1); What a protocol is for (10-open-act1); MCP, A2A and Skills (10-open-act1); The decoder ring (10-open-act1); “We don’t know what the agent did” (10-open-act1); traceparent, now also in MCP (20-act2); We've done this before (30-act3a); Connected is not governed (30-act3a); Enforcement point, decision point (30-act3a); What agentgateway is (30-act3a); What OpenFeature is (30-act3a); Identity is an input (31-act3b); Reach has two layers (31-act3b); What agentgateway says about one governed call (31-act3b); Defence in depth (31-act3b); Limiting access to MCP (31-act3b); Guardrails for model traffic (31-act3b); Hide or refuse (31-act3b); Fail-closed vs fail-open (31-act3b); Fail-open is one word away (31-act3b); A span link vs a parent edge (31-act3b); The same flip, with a UI (31-act3b); The platform API is the resource (31-act3b); Evidence vs logs (40-act4); Which refusals the trace can see (40-act4); The decision contract is a published schema (40-act4); What an evaluation is (40-act4); A score that can go to zero (40-act4); And LLM-as-judge? (40-act4); The deadline moved (40-act4); Seventeen findings, none filed (40-act4); Where AI observability stands (40-act4); Becoming a regulated-platform wizard (50-close) | | |

**Check before presenting:** every fast-moving fact on a slide carries a `[verify: …]` line in its
speaker note. They map to Appendix B of the book.
