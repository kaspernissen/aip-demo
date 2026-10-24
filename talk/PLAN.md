# Slide plan: Verticalizing Platform Intelligence for Regulated Domains

KubeCon + CloudNativeCon North America 2026, Salt Lake City. **Wed 11 Nov, 11:00–11:30, Salt Palace
Level 2, 255 EF** (Platform Engineering, Advanced). The slot is 30 minutes: a 5–8 minute live demo, the rest slides. This is the **30-minute cut: 30 slides,
then five 0 s backup slides** after "Thank you"; the long cut's slides stay in the fragments as
`data-cut` and are listed at the end. For the new demo flow, "HTTP 200 means no" moved to the long cut.

**Speakers:** William Rizzo (Global Field CTO, Mirantis) and Kasper Borg Nissen (Principal Developer
Advocate, Dash0). William opens with the platform concepts: three of his KCD Sofia 2026 intro slides, set on Northstar,
then The Safe Operating Model and the auditor's questions, up to "Decide. Trace. Prove.". Kasper runs
the implementation in between: tracing, the tools, the live demo he built, and the evidence. William
closes, from "Three things to remember". The
*Who* column is read from each note's first line.

**Shape of the long cut:** opens on the auditor's questions (paid off at "From requirement to
artifact"); concept slides before every dense visual; a tools section (toolbox, SPIFFE/SPIRE,
agentgateway, the decision and live policy), each tagged with its ring; one contiguous live block, "The demo" table and then five steps,
each with a hidden transcript fallback; the "what you just saw" deep dives after it, cuttable; five backup slides after "Thank you", one per
ring. The table below is generated from the fragments' comments and notes by `python3 build/plan.py`,
so re-generate rather than edit.

**Subtitle / thesis:** *If you can't trace it, you can't govern it.*

> Agents change the nature of complexity, so the platform has to **decide** what an agent may do,
> and **prove** it afterwards from the **trace**. We already have the open-source pieces to do both.

**What "decide" means** is **The Safe Operating Model** (Francesco Beltramini, ControlPlane), as William
presents it: *rings of least privilege; each outer ring depends on the one inside it.* Bounded autonomy
is a property of the platform, not of the agent. The data ring (Ring 3) is left out of this talk:
Northstar does not build it. The rings structure Act 3,
and every tools-section slide carries its ring as margin furniture (`.ring`, top right):
**Core · Identity** (what is acting? SPIFFE/SPIRE), **Ring 1 · Reach** (what may it connect to? Linkerd for ingress and NetworkPolicy for egress,
`scripts/demo guard`, a backup slide), **Ring 2 · Capability** (what may it hold, and call? agentgateway, the decision service,
OpenFeature), and the **Keystone** (the agent can never edit what defines its authority: Kyverno, demo step 7).

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

**The live demo** is seven steps in two browser tabs (customer-ui on localhost:8080, reviewer-ui on
localhost:8081) and one terminal, against one Northstar cluster brought up by `scripts/setup-kind.sh`
(runbook §5, "The flow"): (1) the two UIs; (2) a claim in the customer's own words, read by
claims-agent's model, held, approved in the reviewer UI; (3) a claim that tells the model to pay
EUR 80,000, then `scripts/demo inject` (what the customer wrote, what the model read, the system
prompt, from the trace; then EUR 80,000 sent as settlement-agent: 403 from the gateway's CEL
ceiling); (4) `scripts/demo evidence` (per claim, with AgentEvals' four checks and the Jaeger
link); (5) `scripts/demo autonomy autonomous` and a claim under EUR 6,000 that settles unasked;
(6) `evidence` again; (7) `scripts/demo keystone` (RBAC, then Kyverno). The Live slide's note is
the run-sheet, 7:30 with narration. No slide shows a command during the demo; if a step fails,
its backup slide after "Thank you" carries the rule and the output.

**Aligned with William's deck** (KCD Sofia 2026, *The boundary problem is a platform problem*): one
model across both speakers, the Safe Operating Model's names and questions. Core · identity, *what is
acting?*: "What SPIFFE and SPIRE are", the identity line on "The guardrails are config", backup
"Who is acting"; in the demo, the SVID behind step 3's 403 and the caller identity in step 4.
Ring 1 · reach, *what may it connect to?*: the toolbox, the config slide, backup "What it can reach"
(`scripts/demo guard`, off the flow). Ring 2 · capability, *what may it hold, and call?*:
agentgateway, "CEL, and when to delegate", "The decision and the live policy"; demo steps 2 (the
human), 3 (the CEL ceiling: his "when intent and capability disagree, capability wins") and 5 (the
live level: his "only the rule changed" becomes "only the flag changed"); backups "What it may call"
and "Only the flag changed". Keystone: demo step 7, backup "It cannot rewrite its own bounds".
Evidence, what this talk adds to the rings ("evidence is a byproduct"): demo steps 4 and 6, then
the deep dives. The backup slides follow his ring-slide pattern: the question as the title, a
one-line rule, *The rule* (committed config) beside *Recorded on the demo cluster*, one sentence.
The demo note opens on his rings; William's close echoes his "The boundary will not come from the
agent."

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
| 11 | 10-open-act1 | W | 7:30 | 55 | Meet MCP and A2A |  |  |
| 12 | 10-open-act1 | W | 8:25 | 45 | Decide. Trace. Prove. | NEW | the thesis: three words, each with its gloss |
| 13 | 20-act2 | K | 9:10 | 55 | OpenTelemetry and Weaver | NEW | OpenTelemetry, and Weaver: the one standard every service speaks (SDK, Collector, semantic conventions), and the registry that keeps our own names honest: telemetry/registry (northstar.* on semconv v1.44.0), scripts/check-registry.sh (weaver v0.28.0 registry check + generated docs), deploy/observability/weaver-live-check.yaml (live-check on a copy of every signal, fail_on = violation) |
| 14 | 30-act3a | K | 10:05 | 40 | Meet Northstar | NEW | Meet Northstar: Ch 4 Figure 4.5, claim path only |
| 15 | 30-act3a | K | 10:45 | 30 | The model can request. The platform decides. | NEW | the design rule: Ch 4 Figure 4.4 |
| 16 | 30-act3a | K | 11:15 | 60 | The toolbox | NEW | the toolbox map: every tool on the claim path and around it, by job (docs/architecture.md components tables; deploy/*; Ch 2 Figure 2.12 for who governs what) |
| 17 | 30-act3a | K | 12:15 | 60 | What SPIFFE and SPIRE are | NEW | concept before the passport: what SPIFFE and SPIRE are (the standard and the issuer), and how a pod gets an identity without a secret: deploy/platform/spire/registration.yaml (k8s:ns + k8s:sa selectors, the SPIFFE CSI driver socket) |
| 18 | 30-act3a | K | 13:15 | 60 | agentgateway: one choke point | NEW | agentgateway: the one choke point and its checks in order (deploy/platform/agentgateway/config.yaml; architecture.md "Hop 3") |
| 19 | 30-act3a | K | 14:15 | 55 | CEL, and when to delegate | NEW | what CEL decides, and when the gateway delegates: the gateway's three checks in order (jwtAuth -> the route's CEL authorization -> mcpGuardrails `kind: remote`, the ExtMCP gRPC call to the decision service, failClosed), from deploy/platform/agentgateway/config.yaml; CEL's reach measured on agentgateway v1.6.0 (2026-10-20 spike) |
| 20 | 30-act3a | K | 15:10 | 45 | The decision and the live policy | NEW | the decision and the live policy: pds/rules.py EXECUTE_LIMITS, pds/autonomy.py (flagd on localhost:8013), deploy/platform/openfeature/featureflag.yaml |
| 21 | 30-act3a | K | 15:55 | 45 | The guardrails are config | NEW | the guardrails are config: the exact lines in five files that restrain the agent, one per ring. Generated from scripts/demo.py GUARDRAIL_CONFIG (the same lines `demo config` prints); tests/unit/test_guardrail_config.py holds every line on this slide to its file |
| 22 | 30-act3a | K | 16:40 | 450 | Live: Northstar | NEW | the demo: one dark sheet while two browser tabs and the terminal are up; no commands on the face (Kasper, 2026-10-19). The run-sheet is the note: seven steps, docs/demo-runbook.md §5 "The flow" |
| 23 | 30-live | K | 24:10 | 50 | What you just saw | NEW | what you just saw: the demo's steps as outcomes, each under the ring it landed (The Safe Operating Model, as William presents it), no commands; Core · identity runs under all of them. The backup slides after Thank you carry each ring's rule and output |
| 24 | 31-act3b | K | 25:00 | 45 | One trace, and the absence | NEW | deep dive after the demo: the held claim's full trace from the customer UI, after Ch 5 Figure 5.5 and Ch 6 Figure 6.13 (trace a4007e49…) |
| 25 | 31-act3b | K | 25:45 | 45 | Maker and checker | NEW | maker and checker: trace A (the agent's work, ending at the hand-off), trace B (a person released it through the gateway, the resume, the push), the span link from B's resume back to A, and how long humans take (northstar_approval_wait_duration_seconds, docs/telemetry-contract.md; runbook §5 Beat 4) |
| 26 | 40-act4 | K | 26:30 | 50 | How the auditor works | NEW | How the auditor works: AgentEvals reads the agents' own traces, four code evaluators, grades written back as gen_ai.evaluation.result (deploy/observability/agentevals.yaml; deploy/observability/agentevals/evaluators/) |
| 27 | 40-act4 | K | 27:20 | 50 | Grade the claim, not the trace | NEW | grade the claim, not the trace: AgentEvals 0.11.0 per trace (vacuous PASS) against grouped by claim (FAIL, caught). Measured 2026-10-21 on synthetic Northstar fixtures (scratchpad agentevals-trial/); the live demo sets gen_ai.conversation.id = claim id (one AgentEvals session per claim); caveats on the face |
| 28 | 40-act4 | K | 28:10 | 50 | Two kinds of checks | NEW | two kinds of checks on the model itself: quality evals and topic guardrails (OpenLIT docs, 2026-10-19); neither is in the demo |
| 29 | 50-close | W | 29:00 | 35 | Three things to remember | NEW | Three things to remember: the layout of Figure 14.4 (study ed.; 14.9 full ed.), its text reworded for the talk's spine (guardrails, instrumented decisions, upstream projects), plus the callback. No QR: the book isn't hosted yet. |
| 30 | 90-thanks | W | — | 0 | Thank you |  |  |
| 31 | 90-thanks | K | — | 0 | Backup: Who is acting | BOUNDED | backup for the demo, after Thank you, 0 s: Core · identity in the pattern of William's ring slides (the rule, recorded on the demo cluster, one sentence). Rule: deploy/platform/spire/registration.yaml; recorded: scripts/demo guard, part 3, 2026-10-24 smoke-test run (.demo/m13-gate.log) |
| 32 | 90-thanks | K | — | 0 | Backup: What it can reach | BOUNDED | backup, 0 s: Ring 1 · reach. Rule: deploy/platform/reach/linkerd-policies.yaml (payments-mcp Server, gateway-only AuthorizationPolicy) and network-policies.yaml (egress by address range); recorded: scripts/demo guard, part 2, 2026-10-24 smoke-test run |
| 33 | 90-thanks | K | — | 0 | Backup: What it may call | BOUNDED | backup for demo step 3, 0 s: Ring 2 · capability, per call. Rule: deploy/platform/agentgateway/config.yaml (jwtAuth strict, the CEL ceiling, the remote guardrail failClosed); right panel: scripts/demo inject as it prints (runbook §5 step 3); the 403 body "authorization failed" is asserted by tests/e2e/test_gateway_ceiling.py |
| 34 | 90-thanks | K | — | 0 | Backup: Only the flag changed | BOUNDED | backup for demo steps 5 and 6, 0 s: Ring 2 · the live level, in the shape of William's "Only the rule changed". Rule: deploy/platform/openfeature/featureflag.yaml and platform/policy-decision-service/pds/rules.py EXECUTE_LIMITS; right panel: scripts/demo autonomy and evidence as they print (runbook §5 steps 5 and 6) |
| 35 | 90-thanks | K | — | 0 | Backup: It cannot rewrite its own bounds | BOUNDED | backup for demo step 7, 0 s: the keystone. Rule: deploy/platform/kyverno/keystone-policy.yaml (ValidatingPolicy, CEL); recorded: scripts/demo keystone, 2026-10-24 smoke-test run (.demo/m13-gate.log), elided with … only |
| — | hidden | | | 0 | 56 slides carry data-cut and are not built: When an agent acts (10-open-act1); Verticalizing (10-open-act1); Not an answer (10-open-act1); The agent proposed (10-open-act1); Act 1 — Agents change the nature of complexity (10-open-act1); The evolution of architectures (10-open-act1); What an agent is, in one sentence (10-open-act1); Anatomy of an agent (10-open-act1); What a protocol is for (10-open-act1); MCP and A2A, turned over (10-open-act1); MCP, A2A and Skills (10-open-act1); The decoder ring (10-open-act1); “We don’t know what the agent did” (10-open-act1); Act 2 — What tracing already gives us (20-act2); What a span is (20-act2); Context gets lost (20-act2); traceparent, now also in MCP (20-act2); What can gen_ai.* answer today? (20-act2); Act 3 — Northstar, live (30-act3a); What a governed foundation needs (30-act3a); We've done this before (30-act3a); Connected is not governed (30-act3a); Enforcement point, decision point (30-act3a); Workload identity: SPIFFE/SPIRE (30-act3a); What agentgateway is (30-act3a); What OpenFeature is (30-act3a); Identity is an input (31-act3b); Reach has two layers (31-act3b); What agentgateway says about one governed call (31-act3b); HTTP 200 means no (31-act3b); Defence in depth (31-act3b); Limiting access to MCP (31-act3b); Two ways to say no (31-act3b); Guardrails for model traffic (31-act3b); Hide or refuse (31-act3b); Fail-closed vs fail-open (31-act3b); Fail-open is one word away (31-act3b); A span link vs a parent edge (31-act3b); The same flip, with a UI (31-act3b); The platform API is the resource (31-act3b); Act 4 — How you prove it (40-act4); Evidence vs logs (40-act4); Which refusals the trace can see (40-act4); The evidence view (40-act4); The decision contract is a published schema (40-act4); What an evaluation is (40-act4); And LLM-as-judge? (40-act4); Content capture, a matched pair (40-act4); Never sample the decisions (40-act4); From requirement to artifact (40-act4); The deadline moved (40-act4); Seventeen findings, none filed (40-act4); Where AI observability stands (40-act4); What this does not do (50-close); Becoming a regulated-platform wizard (50-close); What to do on Monday (50-close) | | |

**Check before presenting:** every fast-moving fact on a slide carries a `[verify: …]` line in its
speaker note. They map to Appendix B of the book.
