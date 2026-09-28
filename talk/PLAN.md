# Slide plan: Verticalizing Platform Intelligence for Regulated Domains

KubeCon + CloudNativeCon North America 2026, Salt Lake City. **Wed 11 Nov, 11:00–11:30, Salt Palace
Level 2, 255 EF** (Platform Engineering, Advanced). The slot is 30 minutes. This is the **long cut, 45:35 of content**, built on purpose so Kasper can cut
down to the slot himself (his call, 2026-09-28).

**Speakers:** William Rizzo (Global Field CTO, Mirantis) and Kasper Borg Nissen (Principal Developer
Advocate, Dash0). William bookends: he opens with the regulators' questions and Act 1 (up to "Decide.
Trace. Prove."; "Where this comes from" is shared), and closes from "From requirement to artifact".
Kasper runs Acts 2–4 in between: tracing, the tools, the live demo he built, and the evidence. The
*Who* column is read from each note's first line.

**Shape of the long cut:** opens on the auditor's questions (paid off at "From requirement to
artifact"); concept slides before every dense visual; a tools section (toolbox, SPIFFE/SPIRE,
agentgateway, the decision and live policy); a demo-setup slide before Beat 1; live beats 1, 2, 4 and 7,
each with a hidden fallback; three speaker-only appendix slides after "Thank you" (pre-flight, reset,
triage). This table is generated from the fragments' comments and notes, so re-generate rather than edit.

**Subtitle / thesis:** *If you can't trace it, you can't govern it.*

> Agents change the nature of complexity, so the platform has to **decide** what an agent may do,
> and **prove** it afterwards from the **trace**. We already have the open-source pieces to do both.

Source of every fact: the research book *If You Can't Trace It*. There are two editions: the study
edition (154 pp) and the full edition (303 pp), with the same chapter numbers. Ch 14 is the talk
plan this deck implements. This deck adds one thing to Ch 14: the slide "What a governed foundation needs" maps the abstract's five
building blocks, so the talk delivers what the CFP abstract promises.

**Origin tags:**
- `DID WHAT`: rebuilt from *Your Agent Did WHAT?* (Adriana Villela and Kasper, SRE Day London 2026).
- `TAMING`: rebuilt from *Taming Complexity* (Mauricio Salatino and Kasper, KubeCon EU).
- `NEW`: built from Northstar.

**Design:** the Broadsheet design system (`bs/`, see `bs/GUIDE.md`): paper ground, Source Serif 4,
cyan = traced or permitted, magenta = governed, refused, or the gateway. Most diagrams are the book's own
figures, lifted as inline SVG and recoloured by the `.bfig` layer in `build/talk-base-bs.css`.

**Live beats** run against the Northstar demo at stage 6. Every live beat (1, 2, 4, 7) has a screenshot fallback
slide right after it, marked `fallback`. It is hidden unless needed: skip it with → when the live beat works.

| # | Fragment | Who | Start | secs | Slide | Origin | What it shows |
|---|---|---|---|---|---|---|---|
| 01 | 00-cover | W | 0:00 | 15 | Cover | NEW | Cover. The CFP title is fixed; the subtitle does the concrete work (Ch 14). |
| 02 | 10-open-act1 | W | 0:15 | 20 | When an agent acts | NEW | regulated domains: four sectors where an agent acts, one line on what it moves (Ch 10, Ch 14 "Who is in the room") |
| 03 | 10-open-act1 | W | 0:35 | 60 | The auditor’s questions | NEW | the auditor's five questions, big type, each tagged with the requirement behind it (Ch 10; Ch 14 slide 35 table). The table itself comes at "From requirement to artifact". |
| 04 | 10-open-act1 | W | 1:35 | 25 | Not an answer | NEW | the stakes, as type: Ch 14 slide 09's title line |
| 05 | 10-open-act1 | W | 2:00 | 60 | This is a success | NEW | the puzzle: a crop of the customer UI outcome block, €6,200 refused |
| 06 | 10-open-act1 | W+K | 3:00 | 60 | Where this comes from | NEW | two earlier talks feed this one; speaker portrait printed as plates; beginners welcome |
| 07 | 10-open-act1 | W | 4:00 | 10 | Act 1 — Agents change the nature of complexity | NEW | Act 1 divider |
| 08 | 10-open-act1 | W | 4:10 | 35 | The evolution of architectures | TAMING | evolution of architectures: book Figure 1.1, lifted; the FIXED row cut, the BROKE row and the crossing lines kept |
| 09 | 10-open-act1 | W | 4:45 | 45 | The model draws the call graph | TAMING | the model draws the call graph: book Figure 1.4 (one prompt, three runs), lifted and cropped |
| 10 | 10-open-act1 | W | 5:30 | 20 | What an agent is, in one sentence | NEW | concept: what an agent is, in one sentence (Ch 1, "the five terms") |
| 11 | 10-open-act1 | W | 5:50 | 45 | Anatomy of an agent | DID WHAT | anatomy of an agent: book Figure 1.3 (the agent loop, mermaid in the book) redrawn as SVG, inside Figure 1.2's harness |
| 12 | 10-open-act1 | W | 6:35 | 20 | What a protocol is for | NEW | concept: what a protocol is for; one JSON-RPC call and its answer (Ch 2, "In plain words") |
| 13 | 10-open-act1 | W | 6:55 | 60 | MCP, A2A and Skills | TAMING | MCP, A2A and Skills: book Figure 2.6 (vertical and horizontal) rebuilt with a skill folder, one dated fact each |
| 14 | 10-open-act1 | W | 7:55 | 45 | The decoder ring | DID WHAT | the decoder ring (book Figure 1.5, first two columns), set as type |
| 15 | 10-open-act1 | W | 8:40 | 45 | “We don’t know what the agent did” | NEW | FINRA 2026: scope and traceability, both lines verbatim from Ch 10 / Ch 14 |
| 16 | 10-open-act1 | W | 9:25 | 45 | Decide. Trace. Prove. | NEW | the thesis: three words, each with its gloss |
| 17 | 20-act2 | K | 10:10 | 10 | Act 2 — What tracing already gives us | NEW | Act 2 divider: what tracing already gives us |
| 18 | 20-act2 | K | 10:20 | 30 | What a span is | NEW | concept: what a span is, and how spans make a trace (Ch 3 Fig 3.2 and 3.3, values illustrative) |
| 19 | 20-act2 | K | 10:50 | 30 | Context gets lost | TAMING | context gets lost: one whole trace next to the same work split into two traces (Ch 3 parcel-label idea) |
| 20 | 20-act2 | K | 11:20 | 30 | traceparent, now also in MCP | TAMING | traceparent anatomy (Ch 3 Fig 3.4, cropped, labels shortened), then the two carriers: HTTP header and MCP params._meta (Ch 3 Fig 3.5 rows 1 and 4) |
| 21 | 20-act2 | K | 11:50 | 40 | Competing vocabularies, one processor | DID WHAT | three dialects for "which model" funnel into one Collector processor (Ch 3 Fig 3.15 and 3.16, redrawn; OpenLLMetry row updated 2026-09-28: it emits gen_ai.* since v0.53.4) |
| 22 | 20-act2 | K | 12:30 | 35 | What can gen_ai.* answer today? | DID WHAT | the "what can the conventions answer today" table (Ch 3 Fig 3.10, minus the gap row, which is slide 18); the four opt-in rows dimmed |
| 23 | 20-act2 | K | 13:05 | 25 | We can prove what, not why | DID WHAT | statement from the quote bank: we can prove what, not why; the missing attribute; bridge into Act 3 |
| 24 | 30-act3a | K | 13:30 | 10 | Act 3 — Northstar, live | NEW | Act 3 divider |
| 25 | 30-act3a | K | 13:40 | 40 | What a governed foundation needs | NEW | the abstract's five building blocks on Ch 11's blueprint (Figure 11.1, cropped; numbered 1–5 by the abstract's five building blocks, 4 = policy (4a) + evals (4b)) |
| 26 | 30-act3a | K | 14:20 | 45 | We've done this before | NEW | we've done this before: Ch 1 Figure 1.8 (what cloud native built, extended to agents), widened to six rows with the Northstar piece of each |
| 27 | 30-act3a | K | 15:05 | 45 | Connected is not governed | NEW | connected is not governed: the runbook's six-stage ladder (docs/demo-runbook.md §1, §3 Option 1; Ch 4 stage table), each stage with its one proof |
| 28 | 30-act3a | K | 15:50 | 40 | Meet Northstar | NEW | Meet Northstar: Ch 4 Figure 4.5, claim path only |
| 29 | 30-act3a | K | 16:30 | 30 | The model can request. The platform decides. | NEW | the design rule: Ch 4 Figure 4.4 |
| 30 | 30-act3a | K | 17:00 | 30 | Enforcement point, decision point | NEW | concept: an enforcement point and a decision point (the bouncer and the manager), set before the tools |
| 31 | 30-act3a | K | 17:30 | 60 | The toolbox | NEW | the toolbox map: every tool on the claim path and around it, by job (docs/architecture.md components tables; deploy/*; Ch 2 Figure 2.12 for who governs what) |
| 32 | 30-act3a | K | 18:30 | 60 | Workload identity: SPIFFE/SPIRE | NEW | SPIFFE/SPIRE: how settlement-agent gets its JWT-SVID (deploy/platform/spire/*, architecture.md "Workload identity" and "Identity with teeth", agents/settlement-agent/svid.py) |
| 33 | 30-act3a | K | 19:30 | 45 | What agentgateway is | NEW | concept before the gateway config: what an agent gateway is, and why an API gateway is not one (deploy/platform/agentgateway/config.yaml: one mcp backend; tools/payments-mcp/server.py execute_payment) |
| 34 | 30-act3a | K | 20:15 | 60 | agentgateway: one choke point | NEW | agentgateway: the one choke point and its checks in order (deploy/platform/agentgateway/config.yaml; architecture.md "Hop 3") |
| 35 | 30-act3a | K | 21:15 | 45 | What OpenFeature is | NEW | concept before the live policy: what OpenFeature is, in four layers, and why a flag carries a policy knob (pds/autonomy.py; requirements.txt openfeature-sdk, -provider-flagd, -hooks-opentelemetry; deploy/platform/openfeature/*) |
| 36 | 30-act3a | K | 22:00 | 45 | The decision and the live policy | NEW | the decision and the live policy: pds/rules.py EXECUTE_LIMITS, pds/autonomy.py (flagd on localhost:8013), deploy/platform/openfeature/featureflag.yaml |
| 37 | 30-act3a | K | 22:45 | 60 | The demo | NEW | the demo set-up: docs/demo-runbook.md §1 (story), §2 (the two claims, three ways to succeed), §6 (surfaces and ports) |
| 38 | 30-act3a | K | 23:45 | 120 | Beat 1 — the refusal is the success | NEW | Beat 1 live: Ch 4 Figure 4.1 |
| 39 | 30-act3a | K | — | 0 | Fallback — Beat 1 (refused, then executed) | NEW | Beat 1 fallback: customer-ui-refused.png + customer-ui-executed.png, cropped to the outcome |
| 40 | 30-act3a | K | 25:45 | 90 | Beat 2 — one trace, and the absence | NEW | Beat 2 live: the deny tree after Ch 5 Figure 5.5 and Ch 6 Figure 6.13 (trace a4007e49…) |
| 41 | 30-act3a | K | — | 0 | Fallback — Beat 2 (deny trace in Jaeger) | NEW | Beat 2 fallback: jaeger-deny-trace.png, jaeger-policy-decision.png, jaeger-guardrail-span.png, cropped |
| 42 | 30-act3a | K | 27:15 | 45 | What agentgateway says about one governed call | NEW | the gateway's spans per governed call: Ch 6 Figure 6.7 tree with the measured key counts, plus Figure 6.15's four added keys |
| 43 | 30-act3a | K | 28:00 | 45 | HTTP 200 means no | NEW | HTTP 200 means no (Ch 6: Figure 6.15, the metrics Figure 6.24, Finding 7) |
| 44 | 31-act3b | K | 28:45 | 20 | Defence in depth | NEW | concept before the eight layers: defence in depth, as airport security |
| 45 | 31-act3b | K | 29:05 | 60 | Limiting access to MCP | NEW | the eight layers between an agent and a payment (Ch 7, Figure 7.1, lifted and re-set: "how Northstar builds it" became "what it stops") |
| 46 | 31-act3b | K | 30:05 | 30 | Why not a CEL rule | NEW | CEL vs ExtMCP: CEL's blind spot beside the PDS decision ladder (Ch 7, Figures 7.5 and 7.6) |
| 47 | 31-act3b | K | 30:35 | 15 | Fail-closed vs fail-open | NEW | concept before fail-open: the door lock in a power cut |
| 48 | 31-act3b | K | 30:50 | 20 | Fail-open is one word away | NEW | failureMode: failClosed, the real config line (deploy/platform/agentgateway/config.yaml) |
| 49 | 31-act3b | K | 31:10 | 45 | Identity is an input | NEW | beat 3 as a slide: five 401s, and the caller as half of the lookup key (Ch 7, Figures 7.3 and 7.4) |
| 50 | 31-act3b | K | 31:55 | 15 | A span link vs a parent edge | NEW | concept before beat 4: a span link vs a parent edge |
| 51 | 31-act3b | K | 32:10 | 105 | Beat 4: a human overturns it | NEW | Beat 4 live: the approval as a second governed caller, joined by a span link (Ch 8, Figure 8.4, lifted) |
| 52 | 31-act3b | K | — | 0 | Fallback — Beat 4 | NEW | Beat 4 fallback: reviewer-ui.png + jaeger-approval-link.png |
| 53 | 31-act3b | K | 33:55 | 90 | Beat 7: one value changes it | NEW | Beat 7 live: the one-word diff on the FeatureFlag's targeting (deploy/platform/openfeature/featureflag.yaml) |
| 54 | 31-act3b | K | — | 0 | Fallback — Beat 7 | NEW | Beat 7 fallback: backstage-autonomy-assisted/flipped + customer-ui-autonomous-executed + jaeger-autonomy-change |
| 55 | 31-act3b | K | 35:25 | 20 | The platform API is the resource | NEW | The platform API is the resource. Handover point (William Rizzo) |
| 56 | 40-act4 | K | 35:45 | 10 | Act 4 — How you prove it | NEW | Act 4 divider. The act title is Ch 14's alternative subtitle. |
| 57 | 40-act4 | K | 35:55 | 20 | Evidence vs logs | NEW | concept: evidence vs logs. A log line (Fig 3.1) against the five things the evidence view answers (Ch 9: who decided, rule, limit, human, ledger), all on spans, one join key |
| 58 | 40-act4 | K | 36:15 | 50 | The evidence view | NEW | The evidence view (beat 5 as a slide): opensearch-evidence.png, cropped |
| 59 | 40-act4 | K | 37:05 | 20 | What an evaluation is | NEW | concept: what an evaluation is. The gen_ai.evaluation.result fields (Ch 3 table, Fig 3.13), parented to the judged span |
| 60 | 40-act4 | K | 37:25 | 45 | A score that can go to zero | NEW | A score that can go to zero (beat 6 as a slide): 1.0 vs 0.0 (Fig 9.3) plus jaeger-eval-scores.png |
| 61 | 40-act4 | K | 38:10 | 30 | And LLM-as-judge? | DID WHAT | "Enter LLM-as-judge", rebuilt as judge vs auditor on one output format (Ch 3 Fig 3.13, Ch 9 "What Northstar does not have") |
| 62 | 40-act4 | K | 38:40 | 45 | Content capture, a matched pair | NEW | Content capture as a matched pair: Fig 9.5 lifted (three layers, keys vs content) |
| 63 | 40-act4 | K | 39:25 | 30 | Never sample the decisions | TAMING | "More Telemetry ≠ Better Telemetry" (p.40), reworked: the 142 vs 139 defect |
| 64 | 40-act4 | W | 39:55 | 60 | From requirement to artifact | NEW | Requirement → artifact, Ch 14's six-row table; walk two rows (Art. 86 / C-203/22 and DORA) |
| 65 | 40-act4 | W | 40:55 | 30 | The deadline moved | NEW | The deadline moved: Fig 10.1 lifted (the Omnibus timeline), plus SR 26-2 |
| 66 | 40-act4 | W | 41:25 | 30 | Sixteen findings, none filed | NEW | Sixteen findings: Fig 12.1 lifted (governance home × kind of gap); chips tinted from Fig 12.2 |
| 67 | 40-act4 | W | 41:55 | 40 | Where AI observability stands | NEW | where AI observability stands: the L0–L5 ladder and the ecosystem's measured rung (Instrumenting AI Agents, Dash0 DevRel, Aug 2026, p. 10); replaces the old OTel-support maturity model, which has since changed |
| 68 | 50-close | W | 42:35 | 60 | What this does not do | NEW | Honest limitations: Ch 14's list (Backstage line kept exact in the note) plus the two abstract blocks the demo does not have (Ch 11.3; dashed like the blueprint's missing layers, Fig 11.1) |
| 69 | 50-close | W | 43:35 | 45 | Becoming a regulated-platform wizard | DID WHAT | "Becoming an AI observability wizard": the six steps as the book lists them (Ch 3 Fig 3.18), dimmed, plus Ch 14's three regulated ones |
| 70 | 50-close | W | 44:20 | 45 | What to do on Monday | NEW | The call to action: Ch 14's five verbs, ending on "Sixteen gaps. Pick one." |
| 71 | 50-close | W | 45:05 | 30 | Three things to remember | NEW | Three things to remember: Figure 14.4 (study ed.; 14.9 full ed.) lifted verbatim, plus the callback. No QR: the book isn't hosted yet. |
| 72 | 90-thanks | W | — | 0 | Thank you |  |  |
| 73 | 90-thanks | K | — | 0 | Appendix — Pre-flight | NEW | appendix (speaker-only, 0 s): pre-flight T-60…T-5 (aip-demo docs/demo-runbook.md §4) |
| 74 | 90-thanks | K | — | 0 | Appendix — Reset between runs | NEW | appendix (speaker-only, 0 s): reset between runs (runbook §7) |
| 75 | 90-thanks | K | — | 0 | Appendix — When it breaks | NEW | appendix (speaker-only, 0 s): live triage (runbook §8) |

**Check before presenting:** every fast-moving fact on a slide carries a `[verify: …]` line in its
speaker note. They map to Appendix B of the book.
