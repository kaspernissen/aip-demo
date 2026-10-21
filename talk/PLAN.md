# Slide plan: Verticalizing Platform Intelligence for Regulated Domains

KubeCon + CloudNativeCon North America 2026, Salt Lake City. **Wed 11 Nov, 11:00–11:30, Salt Palace
Level 2, 255 EF** (Platform Engineering, Advanced). The slot is 30 minutes: a 5–8 minute live demo, the rest slides. This is the **30-minute cut, about 25
slides** (2026-10-20, after the William alignment call); the long cut's slides stay in the fragments as
`data-cut` and are listed at the end.

**Speakers:** William Rizzo (Global Field CTO, Mirantis) and Kasper Borg Nissen (Principal Developer
Advocate, Dash0). William opens with the platform concepts: three of his KCD Sofia 2026 intro slides, set on Northstar,
then The Safe Operating Model and the auditor's questions, up to "Decide. Trace. Prove.". Kasper runs
the implementation in between: tracing, the tools, the live demo he built, and the evidence. William
closes. The
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

**What "decide" means** is **The Safe Operating Model** (Francesco Beltramini, ControlPlane), as William
presents it: *rings of least privilege; each outer ring depends on the one inside it.* Bounded autonomy
is a property of the platform, not of the agent. The data ring (Ring 3) is left out of this talk:
Northstar does not build it. The rings structure Act 3,
and every tools-section slide carries its ring as margin furniture (`.ring`, top right):
**Core · Identity** (what is acting? SPIFFE/SPIRE), **Ring 1 · Reach** (what may it connect to? Cilium,
step 1), **Ring 2 · Capability** (what may it hold, and call? agentgateway, the decision service,
OpenFeature), and the **Keystone** (the agent can never edit what defines its authority: Kyverno, step 4). Steps 1 and 4 need stage 7
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
- `WILLIAM`: William's KCD Sofia 2026 keynote, *The boundary problem is a platform problem*, set on Northstar.
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
| 01 | 00-cover | W | 0:00 | 15 | Cover | NEW | Cover. The CFP title is fixed; the subtitle does the concrete work (Ch 14). Art (2026-10-20): the agent inside three rings of guardrails, one door through each, a trace leaving through the doors with a span at every crossing; blocked attempts stop at the inner wall. |
| 02 | 10-open-act1 | W+K | 0:15 | 40 | Who? | NEW | who we are: the two speakers, portraits printed as plates (Kasper, 2026-10-19: an intro, not the lineage of talks) |
| 03 | 10-open-act1 | W | 0:55 | 30 | What its tools let it do | WILLIAM | the opening claim, from William's KCD Sofia 2026 keynote (The boundary problem is a platform problem, slide 2) |
| 04 | 10-open-act1 | W | 1:25 | 50 | The agent cannot hold its own boundary | WILLIAM | intent is not capability, from William's KCD Sofia 2026 keynote (slide 5), William's visual (99 green squares, one red; the instruction dashed, the tool binding solid), told as a generic payout agent that plants the demo's seeds (a €6,000 line, execute_payment, a human above it) without naming Northstar |
| 05 | 10-open-act1 | W | 2:15 | 40 | An agent is a workload | WILLIAM | an agent is a workload, from William's KCD Sofia 2026 keynote (slide 6) |
| 06 | 10-open-act1 | W | 2:55 | 55 | Put it in a box | WILLIAM | the agent in a box: the walls are the platform's guardrails, the things it could damage sit outside; one door, checked and recorded; every other path stops at the wall. The dashed inner box is a sandbox for what the agent's own code may touch (files, processes): not in this demo, the next layer if it runs code. Generic, plants the demo's seeds (payments through one gate, the agent can't edit its own rules) |
| 07 | 10-open-act1 | W | 3:50 | 70 | The Safe Operating Model | BOUNDED | The Safe Operating Model (Francesco Beltramini, ControlPlane), as William presents it: rings of least privilege, one question each, the keystone as the clasp beneath them. The data ring (Ring 3) is left out of this talk (William and Kasper, 2026-10-20): Northstar does not build it. The evidence line under the keystone is our addition (Kasper, 2026-10-20), marked as such in the credit. |
| 08 | 10-open-act1 | W | 5:00 | 45 | Autonomy is earned | NEW | autonomy is earned: trust as a dial the evidence turns, one notch at a time, and back. Generic; plants the demo's assisted (€0) and autonomous (€6,000) levels and the live flip |
| 09 | 10-open-act1 | W | 5:45 | 60 | The auditor’s questions | NEW | the auditor's five questions, big type, each tagged with the requirement behind it (Ch 10; Ch 14 slide 35 table). Answered after the demo, at "How the auditor works". |
| 10 | 10-open-act1 | W | 6:45 | 45 | The model draws the call graph | TAMING | the model draws the call graph: book Figure 1.4 (one prompt, three runs), lifted and cropped |
| 11 | 10-open-act1 | W | 7:30 | 45 | Meet MCP and A2A |  |  |
| 12 | 10-open-act1 | W | 8:15 | 50 | MCP and A2A, turned over | NEW | the cards, turned over: how each protocol works and where they differ (call a function vs hand over a job); flips on arrival |
| 13 | 10-open-act1 | W | 9:05 | 45 | Decide. Trace. Prove. | NEW | the thesis: three words, each with its gloss |
| 14 | 20-act2 | K | 9:50 | 55 | OpenTelemetry and Weaver | NEW | OpenTelemetry, and Weaver: the one standard every service speaks (SDK, Collector, semantic conventions), and the registry that keeps our own names honest: telemetry/registry (northstar.* on semconv v1.44.0), scripts/check-registry.sh (weaver v0.28.0 registry check + generated docs), deploy/observability/weaver-live-check.yaml (live-check on a copy of every signal, fail_on = violation) |
| 15 | 20-act2 | K | 10:45 | 35 | What can gen_ai.* answer today? | DID WHAT | the "what can the conventions answer today" table (Ch 3 Fig 3.10, minus the gap row, which is slide 18); the four opt-in rows dimmed |
| 16 | 30-act3a | K | 11:20 | 40 | Meet Northstar | NEW | Meet Northstar: Ch 4 Figure 4.5, claim path only |
| 17 | 30-act3a | K | 12:00 | 30 | The model can request. The platform decides. | NEW | the design rule: Ch 4 Figure 4.4 |
| 18 | 30-act3a | K | 12:30 | 60 | The toolbox | NEW | the toolbox map: every tool on the claim path and around it, by job (docs/architecture.md components tables; deploy/*; Ch 2 Figure 2.12 for who governs what) |
| 19 | 30-act3a | K | 13:30 | 50 | What SPIFFE and SPIRE are | NEW | concept before the passport: what SPIFFE and SPIRE are (the standard and the issuer), and how a pod gets an identity without a secret: deploy/platform/spire/registration.yaml (k8s:ns + k8s:sa selectors, the SPIFFE CSI driver socket) |
| 20 | 30-act3a | K | 14:20 | 75 | Workload identity: SPIFFE/SPIRE | NEW | SPIFFE/SPIRE: how settlement-agent gets its JWT-SVID (deploy/platform/spire/*, architecture.md "Workload identity" and "Identity with teeth", agents/settlement-agent/svid.py) |
| 21 | 30-act3a | K | 15:35 | 60 | agentgateway: one choke point | NEW | agentgateway: the one choke point and its checks in order (deploy/platform/agentgateway/config.yaml; architecture.md "Hop 3") |
| 22 | 30-act3a | K | 16:35 | 55 | CEL, and when to delegate | NEW | what CEL decides, and when the gateway delegates: the gateway's three checks in order (jwtAuth -> the route's CEL authorization -> mcpGuardrails `kind: remote`, the ExtMCP gRPC call to the decision service, failClosed), from deploy/platform/agentgateway/config.yaml; CEL's reach measured on agentgateway v1.6.0 (2026-10-20 spike) |
| 23 | 30-act3a | K | 17:30 | 45 | The decision and the live policy | NEW | the decision and the live policy: pds/rules.py EXECUTE_LIMITS, pds/autonomy.py (flagd on localhost:8013), deploy/platform/openfeature/featureflag.yaml |
| 24 | 30-act3a | K | 18:15 | 370 | Live: Northstar | NEW | the demo: one dark sheet while the terminal is up; no commands on the face (Kasper, 2026-10-19). The run-sheet is the note. |
| 25 | 30-live | K | 24:25 | 40 | What you just saw | NEW | what you just saw: the five demo steps as outcomes, no commands (Kasper, 2026-10-19; replaces the five live-step slides and their transcript fallbacks) |
| 26 | 31-act3b | K | 25:05 | 45 | One trace, and the absence | NEW | deep dive after step 2: the full deny trace from the customer UI, after Ch 5 Figure 5.5 and Ch 6 Figure 6.13 (trace a4007e49…) |
| 27 | 31-act3b | K | 25:50 | 45 | HTTP 200 means no | NEW | HTTP 200 means no (Ch 6: Figure 6.15, the metrics Figure 6.24, Finding 7) |
| 28 | 31-act3b | K | 26:35 | 45 | Maker and checker | NEW | maker and checker: trace A (the agent's work, ending at the hand-off), trace B (a person released it through the gateway, the resume, the push), the span link from B's resume back to A, and how long humans take (northstar_approval_wait_duration_seconds, docs/telemetry-contract.md; runbook §5 Beat 4) |
| 29 | 40-act4 | K | 27:20 | 50 | How the auditor works | NEW | How the auditor works: eval-scorer's loop, OpenTelemetry in and OpenTelemetry out (platform/eval-scorer/README.md; scorer/emit.py; scorer/opensearch.py TrajectoryTruncated; M4 design spec §5) |
| 30 | 40-act4 | K | 28:10 | 50 | One violation, three graders | NEW | one violation, three graders: AgentEvals 0.11.0 per trace (vacuous PASS), AgentEvals grouped by claim (FAIL, caught), eval-scorer following the span link (FAIL, with rule and identity). Measured 2026-10-21 on synthetic Northstar fixtures (scratchpad agentevals-trial/), not the live cluster; caveats on the face |
| 31 | 40-act4 | K | 29:00 | 80 | Two kinds of checks | NEW | two kinds of checks on the model itself: quality evals and topic guardrails (OpenLIT docs, 2026-10-19); neither is in the demo |
| 32 | 50-close | W | 30:20 | 30 | Three things to remember | NEW | Three things to remember: the layout of Figure 14.4 (study ed.; 14.9 full ed.), its text reworded after M8 for the talk's spine (guardrails, instrumented decisions, upstream projects), plus the callback. No QR: the book isn't hosted yet. |
| 33 | 90-thanks | W | — | 0 | Thank you |  |  |
| — | hidden | | | 0 | 53 slides carry data-cut and are not built: When an agent acts (10-open-act1); Verticalizing (10-open-act1); Not an answer (10-open-act1); The agent proposed (10-open-act1); Act 1 — Agents change the nature of complexity (10-open-act1); The evolution of architectures (10-open-act1); What an agent is, in one sentence (10-open-act1); Anatomy of an agent (10-open-act1); What a protocol is for (10-open-act1); MCP, A2A and Skills (10-open-act1); The decoder ring (10-open-act1); “We don’t know what the agent did” (10-open-act1); Act 2 — What tracing already gives us (20-act2); What a span is (20-act2); Context gets lost (20-act2); traceparent, now also in MCP (20-act2); Act 3 — Northstar, live (30-act3a); What a governed foundation needs (30-act3a); We've done this before (30-act3a); Connected is not governed (30-act3a); Enforcement point, decision point (30-act3a); What agentgateway is (30-act3a); What OpenFeature is (30-act3a); Identity is an input (31-act3b); Reach has two layers (31-act3b); What agentgateway says about one governed call (31-act3b); Defence in depth (31-act3b); Limiting access to MCP (31-act3b); Two ways to say no (31-act3b); Guardrails for model traffic (31-act3b); Hide or refuse (31-act3b); Fail-closed vs fail-open (31-act3b); Fail-open is one word away (31-act3b); A span link vs a parent edge (31-act3b); The same flip, with a UI (31-act3b); The platform API is the resource (31-act3b); Act 4 — How you prove it (40-act4); Evidence vs logs (40-act4); Which refusals the trace can see (40-act4); The evidence view (40-act4); The decision contract is a published schema (40-act4); What an evaluation is (40-act4); A score that can go to zero (40-act4); And LLM-as-judge? (40-act4); Content capture, a matched pair (40-act4); Never sample the decisions (40-act4); From requirement to artifact (40-act4); The deadline moved (40-act4); Seventeen findings, none filed (40-act4); Where AI observability stands (40-act4); What this does not do (50-close); Becoming a regulated-platform wizard (50-close); What to do on Monday (50-close) | | |

**Check before presenting:** every fast-moving fact on a slide carries a `[verify: …]` line in its
speaker note. They map to Appendix B of the book.
