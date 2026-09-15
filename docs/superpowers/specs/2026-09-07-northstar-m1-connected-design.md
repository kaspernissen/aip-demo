# Northstar — M1 "Connected" Design

**Date:** 2026-09-07
**Status:** Approved design, ready for implementation planning
**Project:** Agentic Internal Platform reference implementation (`aip-demo`)
**Talk:** *Verticalizing Platform Intelligence for Regulated Domains*

---

## 1. Context and goal

The talk argues that agentic workloads change the *nature* of complexity, not just its
amount, and that the cloud-native community already owns the primitives needed to govern
them: golden paths, guardrails, identity, gateways, durable execution, telemetry,
progressive delivery and GitOps. The demo must make that argument concrete using open
standards and existing open-source projects rather than a proprietary "AI platform".

The demo domain is **Northstar Insurance**. A customer files a vehicle-damage claim; a set
of agents collaborates to investigate and settle it. The platform decides whether those
interactions are permitted, whether a human must approve them, records what happened,
evaluates the quality of the trajectory, and gradually determines how much autonomy the
system should have.

This document specifies **M1 only**. M1's job is to build the system the rest of the talk
argues against: it works, and it is completely ungoverned.

### Predecessor

`pizza-vibe` (Go services + Quarkus/LangChain4j agents + MCP server + A2A + kind +
OTel→Jaeger/Dash0) is the direct predecessor. Northstar inherits its shape: monorepo, one
`setup-kind.sh`, per-service Dockerfiles and manifests, optional `DASH0_AUTH_TOKEN`
export. It does not inherit its runtime stack.

---

## 2. Milestone map (context for M1)

The demo's five on-stage stages are the milestones. Each is a vertical slice ending in
something demonstrable.

| # | Milestone | Adds | The line it earns on stage |
|---|---|---|---|
| **M1** | **Connected** | Domain, 4 agents, 4 MCP servers, 4 systems of record, customer UI, OTel→Jaeger, `setup-kind.sh` | *"It works. Would you put it in production?"* |
| M2 | Governed | agentgateway in every path, SPIFFE/SPIRE, Policy Decision Service, Kyverno, Cilium, cert-manager | *"The model can request. The platform decides if it becomes an effect."* |
| M3 | Supervised | Dapr Workflow, durable external-event wait, reviewer UI, Keycloak | *"The agent isn't the owner of the business process."* |
| M4 | Observable & evaluable | OpenSearch + Agent Evals, Prometheus, the trajectory payoff view | *"Can we prove why €5,800 was paid?"* |
| M5 | Progressive autonomy | OpenFeature/flagd autonomy profiles | *"We didn't change a prompt. We changed a contract."* |
| M6 | Golden path | Crossplane `Agent` CRD, Argo CD, Backstage template | *"The platform API is `Agent`. The rest is implementation detail."* |

**Corrected 2026-09-15 (M6 Task 1), row left as written above rather than
rewritten -- this document is a historical record, annotated per the repo's
standing convention (same as the M2.2 `failureMode` inversion):** M5's own
spec (`docs/superpowers/specs/2026-09-14-northstar-m5-progressive-autonomy-
design.md` §11) dropped Crossplane and Argo CD, reassigning the platform-API
role to the `FeatureFlag` resource it introduced. M6's actual design
(`docs/superpowers/specs/2026-09-15-northstar-m6-golden-path-design.md` §1-2)
builds Backstage over that resource instead -- no Crossplane `Agent` CRD, no
Argo CD, and no scaffolder template (deferred: this repo has no git remote for
it to publish to). The line this row earns still holds, told a different way:
the platform API is the `FeatureFlag` a human edits, and Backstage is the view
on it.

**M6 is deliberately last.** A platform API cannot abstract a platform that does not yet
exist. Building M1–M5 with honest hand-written manifests and then collapsing them behind
one CRD is both correct platform engineering and a better narrative. To keep M6 a collapse
rather than a rewrite, each agent's platform configuration lives in exactly one place per
agent from M1 onward.

### Components cut from the original architecture

- **Falco** — slide only. Costs laptop resources, proves nothing about the thesis.
- **External Secrets** — slide only. Made redundant by the design itself: if agents obtain
  provider credentials via SPIFFE→agentgateway, there is no interesting secret to sync.
  Saying that out loud is stronger than installing it.
- **cert-manager, Argo CD** — installed, never clicked. Cheap, and they make the platform
  credible.

  **Corrected 2026-09-15 (M6 Task 1), cert-manager half only:** this claim is
  now doubly wrong, not merely stale. M5's own §11 already flagged it as
  wrong once -- "cert-manager is named ... as 'installed, never clicked' and
  has never been installed" -- and left the correction to "M6's or a doc
  pass's". Since that note, M5 Task 2 actually installed cert-manager
  (`--stage 5`, a real prerequisite for the Open Feature Operator's webhook
  TLS, not decorative) and something genuinely uses it. So the true history is
  *neither* of this sentence's two claims: not "installed, never clicked" (M1
  era) and not merely "never installed" (M5's own correction, true only until
  M5 Task 2) -- it is installed and load-bearing. Argo CD remains uninstalled
  and out of scope (M6's design §1-2 dropped it along with Crossplane).

---

## 3. Scope

### In scope for M1

Four agents, four MCP servers, four Go systems of record, a customer UI, an OTel
Collector, Jaeger, deterministic fixtures for four claims, a stage-profiled kind setup
script, a written telemetry contract, and tests at four layers including trajectory trace
assertions.

### Out of scope for M1

agentgateway, SPIFFE/SPIRE, Kyverno, Cilium, cert-manager, the Policy Decision Service,
Dapr, Keycloak, the reviewer UI, OpenSearch, Prometheus, evals, OpenFeature, Crossplane,
Argo CD, Backstage.

Model calls go **direct to Anthropic**. There is no gateway and no policy of any kind.
`execute_payment` will pay €6,200 because a language model asked it to.

---

## 4. Architecture (M1)

```text
                          browser (customer-ui)
                                   │
                        A2A message/stream (SSE)
                                   │
                                   ▼
                           ┌───────────────┐
                           │ claims-agent  │  Python · PydanticAI
                           └───────┬───────┘
                                   │  A2A
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     ┌─────────────────┐  ┌──────────────────┐  ┌──────────────────┐
     │  policy-agent   │  │ assessment-agent │  │ settlement-agent │
     │ Java · Quarkus  │  │ Python           │  │ Python           │
     └────────┬────────┘  └────────┬─────────┘  └────────┬─────────┘
              │ MCP                │ MCP                 │ MCP
              ▼                    ▼                     ▼
        policy-mcp          claims-mcp  risk-mcp     payments-mcp
              │                    │        │             │
              ▼                    ▼        ▼             ▼
        policy-api          claims-api  risk-api      payments-api
                                 (Go systems of record)
```

### Two structural invariants

**No agent touches another agent's MCP server.** `settlement-agent` owns payments;
`policy-agent` owns coverage. This boundary is what gives the A2A/MCP split meaning and is
precisely what agentgateway enforces in M2. Without it, "A2A for agents, MCP for tools" is
a slide rather than an architecture.

**MCP servers are separate deployables from the systems they front.** Eight processes for
four domains is the point: MCP is an *adapter over a system of record you do not own*, not
a replacement for it. Collapsing them would concede the most common enterprise
misconception about MCP, and would leave agentgateway nothing real to multiplex in M2. If
the laptop footprint becomes binding, four MCP servers may fold into two without weakening
the argument.

### The odd one out

`policy-agent` is **Java / Quarkus / LangChain4j / a2a-java**, reusing working patterns
from `pizza-vibe` (including its vendored `.deps/a2a-java`). It is a leaf agent talking to
one MCP server, so it is the lowest-risk place to demonstrate framework-agnosticism, and
it earns a line on stage: *"that one's Java, from my last demo. The platform didn't
notice."*

---

## 5. Repository layout

The layout mirrors the seven planes, so the repository itself teaches the architecture — a
property that matters for a reference implementation people clone after the talk.

```text
aip-demo/
├── contracts/              source of truth, reviewed in PRs
│   ├── openapi/            policy-api, claims-api, risk-api, payments-api
│   ├── agent-cards/        A2A agent cards, one per agent
│   └── mcp/                MCP tool schemas
├── agents/
│   ├── claims-agent/       Python · PydanticAI · orchestrator
│   ├── assessment-agent/   Python · PydanticAI
│   ├── settlement-agent/   Python · PydanticAI
│   └── policy-agent/       Java · Quarkus · LangChain4j · a2a-java
├── tools/                  MCP servers (FastMCP, streamable HTTP)
│   ├── policy-mcp/
│   ├── claims-mcp/
│   ├── risk-mcp/
│   └── payments-mcp/
├── systems/                systems of record (Go, deliberately tiny)
│   ├── policy-api/
│   ├── claims-api/
│   ├── risk-api/
│   └── payments-api/
├── apps/
│   ├── customer-ui/        Next.js — file a claim, watch it progress
│   └── reviewer-ui/        (M3)
├── platform/
│   └── policy-decision-service/   (M2) ExtMCP gRPC
├── deploy/
│   ├── kind/               cluster configs per stage
│   ├── k8s/                manifests grouped by namespace
│   └── observability/      collector, jaeger (M4 adds opensearch, prometheus)
├── fixtures/               deterministic demo data
├── docs/
│   ├── telemetry-contract.md
│   ├── demo-script.md      the five-stage stage runbook
│   └── superpowers/specs/
├── tests/
│   └── e2e/                smoke + trajectory trace assertions
└── scripts/                setup-kind.sh, setup-cloud.sh, rebuild-and-deploy.sh, smoke-test.sh
```

### Namespaces

`northstar-agents`, `northstar-tools`, `northstar-systems`, `northstar-platform`,
`observability`. The split is what makes the Cilium egress story legible in M2; in M1 it
costs nothing to adopt.

---

## 6. Domain and fixtures

### Entities

Customer, Policy (coverage terms, deductible, limits, validity period), Claim, Damage
assessment, Risk score, Settlement proposal, Payment.

### Determinism policy

Real LLMs are used, so generated prose will vary between runs. It is therefore **essential
that the data does not**. `risk-api` computes scores from a fixed formula over claim
history, with no randomness and no model involvement. Numbers on the slides match numbers
on stage every time, even when the wording differs.

### The cast

| Claim | Situation | Amount | Eventual path it exists to prove | **Outcome in M1** |
|---|---|---|---|---|
| **CLM-1842** | Rear-end collision, covered, risk 0.08 | €6,200 | **The hero.** Exceeds the €5,000 ceiling → human review (M3) → approved at €5,800 | **Paid in full, €6,200** |
| **CLM-1901** | Windscreen chip, covered, risk 0.05 | €800 | The M5 payoff — executes autonomously once the flag flips to `supervised` | Paid, €800 |
| **CLM-1755** | Damage outside coverage period | — | Denied at coverage. No settlement, no payment, short trace | Denied at coverage |
| **CLM-1888** | Third claim in 60 days, risk 0.71 | €4,100 | **Under** the ceiling but reviewed anyway | **Paid in full, €4,100** |

The final column matters for acceptance testing. In M1 there is no governance, so three of
the four claims are simply paid — including both claims that should have been stopped. Only
CLM-1755 is refused, and only because coverage logic lives in the domain rather than in the
platform. The M1 test suite asserts *these* outcomes; the expected paths in the previous
column become assertions in M2, M3 and M5 respectively.

CLM-1888 exists to prevent a reasonable audience member concluding that the entire
governance layer is a single `if amount > 5000`. With it, the Policy Decision Service
visibly has a policy model rather than a threshold.

The claims team's autonomy ceiling is **€5,000**.

### The M1 narrative beat

In M1, **CLM-1842 simply gets paid**. €6,200 leaves the company because a language model
decided it should — no gateway, no policy, no human, no record beyond a trace. This is the
"would you put this in production?" moment, and it only lands if M1 is built genuinely
ungoverned. The horror is a deliverable.

---

## 7. Protocol wiring

### A2A

Every agent is an A2A server. There is exactly one uniform inbound interface per agent,
which is what makes the `Agent` CRD in M6 an honest abstraction rather than a wrapper over
four different shapes.

**The customer UI speaks A2A directly.** A2A is JSON-RPC over HTTP with SSE streaming, so
the browser is a first-class A2A client via `@a2a-js/sdk`. This replaces `pizza-vibe`'s
bespoke WebSocket channel and buys a line on stage: *"there is no custom protocol here —
the UI is just another A2A client."*

Agent cards live in `contracts/agent-cards/` as the reviewable source and are served by
each agent.

**Delegation is configured, not discovered by magic.** Each agent's configuration names
who it may delegate to:

- M1: Kubernetes service DNS in environment configuration.
- M2: agentgateway route names, enforced at the gateway.
- M6: generated by Crossplane from `delegation.allowedAgents`.

  **Corrected 2026-09-15 (M6 Task 1):** M6 does not do this. M5's spec
  dropped Crossplane and Argo CD (`docs/superpowers/specs/
  2026-09-15-northstar-m6-golden-path-design.md` §1), so this stays the M2
  form -- `agents/claims-agent/delegation.py`'s own docstring was corrected in
  the same commit that adds this annotation, and `docs/architecture.md`'s Hop
  2 section, which quotes that docstring, was corrected with it.

One field, three levels of maturity until M2 — a clean through-line for the
talk, still true as far as M2; M6 does not add a third.

**Version pinning.** The well-known agent-card path changed between A2A spec versions
(`agent.json` → `agent-card.json`). The spec version is pinned in `contracts/` and
verified against the installed SDK during implementation, not assumed.

### MCP

Transport is **streamable HTTP** throughout. stdio cannot traverse agentgateway in M2, so
choosing it now would guarantee a rewrite.

| MCP server | Tools | Fronts |
|---|---|---|
| `policy-mcp` | `get_policy`, `check_coverage` | policy-api |
| `claims-mcp` | `get_claim_history` | claims-api |
| `risk-mcp` | `calculate_risk` | risk-api |
| `payments-mcp` | `propose_payment`, **`execute_payment`** | payments-api |

**Consequence is declared in the protocol.** MCP tool annotations already exist —
`readOnlyHint`, `destructiveHint`, `idempotentHint`. `execute_payment` declares
`destructiveHint: true` **in its own schema**, and the Policy Decision Service (M2) reads
that annotation rather than pattern-matching tool names. This yields a far better argument
than a hardcoded list of dangerous tools:

> The protocol already has a place to declare consequence. The platform's job is to act on it.

It also means a newly added consequential tool inherits governance automatically, which is
the real test of whether a platform abstraction works.

Tool schemas live in `contracts/mcp/` as the source of truth.

---

## 8. Telemetry contract

`docs/telemetry-contract.md` is an M1 deliverable and is treated as an interface.

### The governing rule

**Use the telemetry the frameworks actually emit. Verify per component. Instrument only
our own code. Where a convention gap exists, report the gap — do not paper over it with a
bespoke convention.**

This is both the honest engineering position and the stronger talk position: *"we didn't
invent telemetry — we turned on what exists, and found where the gaps are."*
The telemetry contract is therefore an **enablement and verification** document, not an
invention document.

### Verified upstream instrumentation

| Component | Instrumentation | Verified facts |
|---|---|---|
| PydanticAI agents | `Agent.instrument_all()` | Works with a plain OTel SDK and OTLP env vars; no Logfire account required. Follows OTel GenAI semconv. Emits `gen_ai.client.token.usage`, `gen_ai.usage.input_tokens`/`output_tokens`, `gen_ai.aggregated_usage.*` on agent-run spans, `operation.cost`, `gen_ai.client.operation.time_to_first_chunk`. Content capture controlled by `InstrumentationSettings`. Telemetry format has a `version` parameter — **pin it**. |
| A2A (Python) | `a2a-sdk[telemetry]` extra | SDK ships optional OTel instrumentation. Creates parent-child spans across the client/server boundary, follows OTel HTTP/gRPC semconv, propagates W3C Trace Context, covers client, server, agent execution, task store and event streaming. `DISABLE_OTEL_INSTRUMENTATION` takes precedence over other OTel config — **must not be set true**. OTel became an optional dependency in 0.3.0. |
| MCP servers | FastMCP native instrumentation | Active by default; no-ops until an SDK is configured. Implements the OTel MCP semantic conventions. `FASTMCP_TELEMETRY_MODE` = `native` (default) / `propagation_only` / `off`. Emits `fastmcp.*` attributes alongside the standard ones. **The TracerProvider must be set before importing FastMCP.** |
| Go systems of record | `otelhttp` | Standard HTTP semconv. Our code, standard instrumentation. |
| Java `policy-agent` | Quarkus OpenTelemetry extension + LangChain4j observability | To be verified against the installed versions during M1.3. |
| Browser UIs | OpenTelemetry JS (web) | Our code. |

### MCP semantic conventions — as they actually are

MCP attributes now live in the OTel **GenAI** semantic conventions
(`open-telemetry/semantic-conventions-genai`, published at
`opentelemetry.io/docs/specs/semconv/gen-ai/mcp/`). The real shape:

- `mcp.method.name` — e.g. `tools/call`, `initialize`
- `mcp.protocol.version`, `mcp.session.id`, `mcp.resource.uri`
- **Tool name is `gen_ai.tool.name`, not `mcp.tool.name`** — the latter does not exist
- `gen_ai.operation.name = execute_tool`
- `jsonrpc.request.id`, `network.transport`, `network.protocol.name`
- Span name follows `{mcp.method.name} {target}` → e.g. `tools/call execute_payment`
- MCP conventions are preferred over the generic RPC conventions

**Critical correction to an earlier assumption.** MCP trace context does **not** propagate
via HTTP headers. Per SEP-414 it propagates through the JSON-RPC `params._meta` property
bag, with `traceparent`, `tracestate` and `baggage` keys left **unprefixed** (unlike other
`_meta` keys, which are DNS-prefixed). FastMCP implements this extraction. Assuming HTTP
headers would have silently detached every tool-call span from its trace.

### What is ours to instrument

Only our own components: the browser UIs, the Go systems of record, and — from M2 — the
Policy Decision Service. `policy.decision` spans (outcome, rule id, autonomy profile) and
the M3 `human.review` record are ours to define, because no convention covers them.

### Domain correlation

`northstar.claim.id` is set once at intake into OTel **baggage** and propagates
automatically across A2A and MCP hops — A2A via W3C headers, MCP via `params._meta`.

Precisely how it lands on spans:

1. Spans created by our own code (browser UIs, Go systems of record, and from M2 the Policy
   Decision Service) set the attribute explicitly.
2. In our own service processes, the OTel baggage span processor is enabled where the
   language SDK provides one, so framework-emitted spans in those processes also carry the
   attribute. This is standard, documented enrichment of spans with a domain fact — not
   invention of a convention — and the telemetry contract records it as such.
3. No attributes are injected into telemetry emitted by third-party infrastructure we do
   not run in-process (agentgateway, Dapr, the collector). Correlation there is by trace
   ID.

The point of the mechanism is that no claim ID is threaded through function signatures.

### Content capture

Prompt and completion capture is **off by default** via
`InstrumentationSettings(include_content=False)` — a real, supported PydanticAI setting,
not something we build. Recording everything a model was told is itself a compliance
problem in insurance. Defaulting to off, then showing the setting that enables it for one
specific consequential tool, is a more credible position than capturing everything and
calling it observability.

### Convention gaps — and upstream contribution as a deliverable

Gaps are not talk decoration. **Where we find a gap, we attempt to fix it upstream**, and
the contribution is a tracked deliverable of the milestone that surfaced it. This is the
strongest available version of the talk's argument: the cloud-native community solves this
by extending its own standards, and here is the pull request.

**Gaps must be confirmed before they are proposed.** General web search does not index
GitHub issues or SIG meeting notes well, so a candidate gap is only real after checking
the `open-telemetry/semantic-conventions-genai` issue tracker and the GenAI SIG meeting
notes. Proposing something already merged or under active discussion would undercut the
point.

Current state of the three candidates, from initial research:

**1. Agent-to-agent identity in spans — likely a real gap, with a well-founded proposal
available.** GenAI semconv *does* define agent operations (`create_agent`, `invoke_agent`,
`execute_tool`), so agent spans are covered. What appears missing is cross-agent
correlation for delegation. The closest existing mechanism is `gen_ai.conversation.id`,
and the spec explicitly says instrumentations *should* populate it when an identifier is
readily available, and should *not* fall back to a new UUID, trace ID, or content hash.
A2A supplies exactly such readily-available identifiers: `contextId` and `taskId`. That
makes for a concrete, defensible contribution rather than a vague request — map A2A
`contextId` onto `gen_ai.conversation.id`, and propose representation for task identity.
Targets: the `a2a-python` SDK's instrumentation, and/or semconv-genai.

**2. Human-in-the-loop approval — no convention found.** Nothing surfaced covering an
approval, escalation, or human-interrupt step inside an agent trajectory. Adjacent
research (PROV-AGENT, agent audit-trail systems) handles identity, authorization and
delegation chains as *extensions* rather than core semconv, which suggests the space is
genuinely open. This is the gap most specific to regulated domains and the one where this
project has the most standing to contribute, since M3 will have a working implementation
to argue from.

**3. Policy and autonomy decisions — no convention found.** Same category as (2): our
`policy.decision` spans in M2 are ours to define because nothing covers them.

Volatility caveat: most GenAI semantic conventions remain experimental, so we pin versions
and keep a mapping layer between attribute keys and anything a human reads.

Contribution targets are not limited to OTel semconv. Also in scope where we find
something worth fixing: the `a2a-python` SDK, FastMCP / the MCP SDK, agentgateway
(a working ExtMCP Policy Decision Service is a plausible reference example for a project
whose ExtMCP documentation is new), OpenSearch Agent Evals, and AAIF's governed-MCP
reference architecture.

Process for each confirmed gap: check upstream for existing work → open an issue
describing the concrete use case → offer a PR where we have a working implementation to
back it → record the outcome in the telemetry contract, whether accepted, rejected or
still open. **An honest "we proposed this and it was rejected for these reasons" is
legitimate talk content**; a fabricated success is not.

---

## 9. Cluster and deployment

### Stage-profiled setup

Rough memory budget for the full component set by M6 is **~9–10 GB of cluster memory**;
OpenSearch, Keycloak, Backstage and Argo CD are the expensive tenants. That fits a 16 GB
Mac only barely. So `./scripts/setup-kind.sh --stage N` installs only what stage N needs.

| Stage | Adds | Approx. memory |
|---|---|---|
| 1 | agents, tools, systems, customer UI, OTel Collector, Jaeger | ~3 GB |
| 2 | agentgateway, SPIRE, Kyverno, Cilium, cert-manager, PDS | ~4.5 GB |
| 3 | Dapr, Keycloak, reviewer UI, Postgres | ~6 GB |
| 4 | OpenSearch + Dashboards, Prometheus | ~8 GB |
| 5 | flagd | ~8 GB |
| 6 | Crossplane, Argo CD, Backstage | ~10 GB |

**Corrected 2026-09-15 (M6 Task 1):** stage 6 adds Backstage only -- no
Crossplane, no Argo CD (`docs/superpowers/specs/
2026-09-15-northstar-m6-golden-path-design.md` §1-2). Measured after Task 1's
cluster bring-up rather than carried forward from this row's own guess: see
`README.md`'s stage-6 row for the actual figure, following the same
measure-don't-retype convention stage 4's row already set.

The profiles map 1:1 onto the talk, so the stage being demonstrated is the only thing
running, and the memory ceiling stays visible instead of ambushing the speaker.

Only M1 (stage 1) is implemented in this milestone; the flag and its dispatch table are
established so later stages slot in.

### Collector

One OTel Collector. Jaeger exporter always on; a second OTLP exporter is added when
`DASH0_AUTH_TOKEN` is present, with no application code change. This preserves vendor
neutrality on stage while keeping the Dash0 path exercised.

### Cluster networking note (forward-looking)

Cilium's kube-proxy replacement is **not** required. What the demo needs is DNS-aware
egress denial (`toFQDNs` blocking direct model-provider endpoints), which is ordinary
`CiliumNetworkPolicy` and works with kube-proxy in place. The kind config therefore sets
`disableDefaultCNI: true` and leaves `kubeProxyMode` alone, avoiding the cgroup-v2, kernel
version and API-server chicken-and-egg problems. Recorded here so M1's kind config is
written with M2 in mind.

### Model configuration

M1 calls Anthropic directly. Model selection is configuration, not code, so that M2 can
move it behind agentgateway model aliases (and M6 behind the CRD's model capability
field). Default agent model: Claude Sonnet 5, with Claude Opus 5 available for the
orchestrator if reasoning depth demands it.

---

## 10. Phases

### M1.1 — Walking skeleton, zero LLM

Deploy the narrowest path that still crosses every protocol and language boundary:

```text
browser ──A2A──► claims-agent ──A2A──► settlement-agent ──MCP──► payments-mcp ──HTTP──► payments-api
  (TS)              (Python)              (Python)                 (Python)              (Go)
```

Four hops, three protocols, three languages, all logic hardcoded. Plus kind stage-1, OTel
Collector, Jaeger, and a deliberately ugly one-form UI.

The browser is in the skeleton on purpose. Browser-as-A2A-client is the most novel
assumption in this design, and browser→backend propagation has a specific known trap: the
server must allow `traceparent` and `baggage` in CORS `Access-Control-Allow-Headers`, or
the root span silently detaches and the result is two traces instead of one.

**Exit criterion:** one trace in Jaeger, rooted in the browser, `northstar.claim.id`
present, correct span kinds, no orphan spans. Nothing else in M1 begins until this holds.

### M1.2 — Intelligence

Replace stub bodies with PydanticAI agents on Claude — real tool selection, real
reasoning, `gen_ai.*` spans appearing inside the trace shape already proven in M1.1. Any
failure is now unambiguously agent logic, never propagation.

**Exit criterion:** CLM-1842 runs end-to-end LLM-driven and executes a €6,200 payment with
no governance.

### M1.3 — Fan out

Add `policy-agent` (Java), `assessment-agent`, the remaining three MCP servers and three Go
systems, all four claim fixtures, and a presentable customer UI.

**Exit criterion:** all four claims reach their expected outcomes, the full A2A fan-out is
visible in a single trace, and the Java hop preserves trace context. That last point is
the real test of M1.3 — and the moment the framework-agnosticism claim either holds or
does not.

---

## 11. Testing

**Unit tests, TDD per component.** pytest, Go stdlib, Quarkus JUnit, jest — following
`pizza-vibe` conventions.

**Agent tests that burn no tokens.** PydanticAI ships `TestModel` and `FunctionModel` for
deterministic agent testing, so agent logic is unit-testable and CI needs no API key.
Designed in from the start; retrofitting testability into prompt-driven code is miserable.

**Contract tests.** Every Go service validated against its OpenAPI spec, every MCP tool
against its schema, every agent card against the A2A schema. This is what keeps
`contracts/` a source of truth rather than stale documentation.

**Trajectory trace assertions.** `tests/e2e/test_trajectory.py` files a claim, queries the
Jaeger API, and asserts on **trace shape**: required spans present, parent-child
relationships correct, `northstar.claim.id` present, span kinds correct, zero orphans.

This is the highest-value test in the project and almost nobody writes it. The talk's
central claim is *"if you can't trace it, you can't fix it"* — so a regression that
silently breaks propagation is a regression that breaks the talk, and it would be invisible
to every other layer of testing. It is also a demonstrable practice worth a slide.

---

## 12. Acceptance criteria

M1 is done when:

1. `git clone` → `export ANTHROPIC_API_KEY=…` → `./scripts/setup-kind.sh --stage 1`
   produces a working demo, one command, from clean, on a 16 GB Mac.
2. All four claim fixtures reach their expected outcomes.
3. `./scripts/smoke-test.sh` passes, trace assertions included.
4. One trace per claim spanning browser → 4 agents → 4 MCP servers → 4 systems, no orphan
   spans, `northstar.claim.id` present throughout.
5. `docs/telemetry-contract.md` exists, records the verified per-component instrumentation
   and any confirmed convention gaps, and every service conforms — enforced by the trace
   test, not by hope.
6. CLM-1842 executes a €6,200 payment with zero governance.
7. An optional `DASH0_AUTH_TOKEN` adds a second exporter with no code change.
8. Teardown works.

---

## 13. Risks and open items

| Risk | Handling |
|---|---|
| Browser→backend trace propagation blocked by CORS not allowing `traceparent`/`baggage` | Validated in M1.1, before anything depends on it |
| A2A spec version and well-known agent-card path (`agent.json` vs `agent-card.json`) | Pin the spec version in `contracts/`; verify against installed SDK |
| Trace context propagation through `a2a-java` in the Java agent | Deferred to M1.3; the Java hop is that phase's exit criterion |
| Quarkus pod memory footprint on a kind node | Measured in M1.3; fold MCP servers 4→2 if the budget binds |
| A2A SDK may emit only HTTP/gRPC semconv, without agent/task identity | Confirm and report as a convention gap; do not invent replacements |
| PydanticAI telemetry `version` parameter changes span format between versions | Pin explicitly in configuration |
| FastMCP requires TracerProvider set before import | Documented in the telemetry contract; covered by the trace test |

### Resolved by spike, recorded for M2/M4

- **agentgateway CEL cannot see MCP tool arguments.** Only `jwt.*`, `mcp.tool.name`,
  `mcp.tool.target` and `request.*` are available. The €5,000 threshold decision therefore
  requires **ExtMCP** (MCP guardrails): an external gRPC service receiving the JSON-RPC
  method, target backend and request/response params, returning Pass / Mutate / Deny with
  `failureMode: FailClosed`. This becomes the **Policy Decision Service** — which also
  hosts the OpenFeature autonomy-profile lookup, collapsing M2 and M5 into one decision
  point, and whose Mutate outcome lets the gateway enforce the reviewer's approved amount
  at the boundary rather than trusting it from the agent. Because agentgateway resolves the
  tool name before request-phase processors and does not re-resolve a mutated identifier,
  the PDS must be the single authority; enforcement must not be split across CEL and
  ExtMCP.
- **agentgateway SPIFFE integration is real.** A `spiffe` block on
  `AgentgatewayParameters` sources mTLS identity and trust bundle from the SPIFFE Workload
  API (CSI driver socket preferred over hostPath), SVIDs rotate without restart, startup
  fails closed, and peer identity is exposed to policy as the `source.spiffeId` CEL
  attribute with per-listener/backend opt-in.
- **OpenSearch Agent Evals is viable.** `opensearch-genai-observability-sdk-py` provides
  `score(name, value, trace_id, span_id, label, explanation)` emitted as OTel spans through
  the same OTLP pipeline, plus `evaluate()`, `Benchmark` and `OpenSearchTraceRetriever`.
  Because scores ride OTLP, the optional Dash0 export receives them at no extra cost.

  **Correction, 2026-09-13 (M4 Task 4), measured against the installed
  package.** The conclusion held — the SDK is real, the scores do ride the same
  OTLP pipeline, and M4 Task 4 ships them — but the **signature written above is
  wrong**, and wrong in a way that does not merely read oddly: calling it as
  written raises. At `0.2.8` it is

  ```
  score(name, value=None, *, trace_id=None, span_id=None, label=None,
        explanation=None, response_id=None, attributes=None) -> None
  ```

  Only `name` and `value` are positional; everything after the `*` is
  keyword-only, and there is an eighth parameter (`attributes`) this record does
  not mention, which is the one that carries `northstar.claim.id` onto the score
  span. It also returns `None` rather than a span or an id. Three further
  behaviours were measured because each one can silently produce a score that
  looks right and is not: `explanation` is truncated to 500 characters; passing
  `trace_id` *without* `span_id` derives a parent span id from the lower 64 bits
  of the trace id, which attaches the score to a span that may not exist; and an
  id the SDK cannot parse as hex is not an error — it logs one WARNING and emits
  the span with **no trace context at all**, so the scores land in the index,
  still saying "compliant", attached to nothing.
  `platform/eval-scorer/scorer/emit.py` therefore always passes both ids and
  refuses non-W3C ones before the SDK sees them
  (`UnattachableScore`), and `platform/eval-scorer/tests/test_emit.py` asserts
  the real signature *and* that this record's six-positional call shape raises
  `TypeError` — so the correction is pinned by a test rather than only by this
  note.

---

## 14. Decisions log

| Decision | Rationale |
|---|---|
| Five demo stages become the milestones | Every milestone ends in something demonstrable; no half-built platform with nothing to show |
| M6 (golden path) last | Cannot abstract a platform that does not exist yet; also the better narrative |
| Python/PydanticAI, plus one Java agent | Best GenAI semconv emission and A2A SDK maturity; the Java agent makes framework-agnosticism visible rather than asserted |
| Walking skeleton before intelligence | Trace continuity across 4 languages and 3 protocols is the project's largest risk; validate it in days, not weeks |
| MCP over streamable HTTP | stdio cannot traverse agentgateway in M2 |
| MCP servers separate from systems of record | MCP is an adapter over systems you do not own; also gives agentgateway something real to multiplex |
| Browser as A2A client | One uniform agent interface makes the M6 CRD honest; removes a bespoke protocol |
| Consequence declared via MCP `destructiveHint` | The protocol already has the field; new consequential tools inherit governance automatically |
| Use framework-emitted telemetry only | Realism, and a stronger claim: gaps get reported, not invented around |
| Content capture off by default | Recording everything a model was told is itself a compliance problem in a regulated domain |
| Deterministic fixtures, real LLMs | Stage numbers stay stable even when generated prose varies |
| Vendor-neutral OSS backends, Dash0 opt-in | Appropriate for a CNCF-style audience; keeps the export path exercised |
| Stage-profiled setup script | ~9–10 GB by M6 does not fit a laptop comfortably; boot only the stage being demonstrated |
| Trajectory trace assertions as a first-class test layer | A silent propagation regression breaks the talk and is invisible to every other test |
