# Northstar Architecture

Northstar is a claim-settlement system built from AI agents and wrapped in
platform controls. A customer files a claim, a claims agent hands it to a
settlement agent, and the settlement agent pays through an MCP tool. Every call
to that tool crosses a gateway that verifies who is calling and asks a policy
service whether the call is allowed. A payment above the agent's limit is
recorded as awaiting approval; a human releases it through the same gateway
under their own identity. Everything emits OpenTelemetry, and the audit trail is
read from what the platform decided, not from what the model said.

This document describes the system as deployed: who talks to whom, over which
protocol, what is enforced where, and what a reader follows through Jaeger.
[`telemetry-contract.md`](telemetry-contract.md) is the companion for the
telemetry itself (span names, attributes, conventions, known gaps). Where the two
overlap and disagree, one of them is wrong; fix it rather than choosing silently.

Three technical points to hold on to before reading the hops:

1. **The trace holds together end to end across components that do not agree on
   attribute names.** agentgateway, the Python services, the Go service and the
   browser each use their own spelling for some attributes. Trace context is what
   makes the chain one thing; attribute naming is what makes it queryable. The two
   are separable, and the attribute vocabulary of each component is pinned by
   tests.
2. **No whole-trace span total is published.** It is not stable: the A2A SDK's
   event-queue polling spans vary run to run, and a denied settlement
   short-circuits everything below the refused call. What is stable, and therefore
   asserted, is trace *shape*: service, operation, kind, parent.
3. **A refusal is not a red span.** The `northstar.policy.decision` span's status
   stays `Unset` on a deny: a refusal is the platform working. It is legible from
   the attributes (`northstar.policy.decision=deny`, the rule, the amount, the
   limit). agentgateway, which sees the proxied call fail, does mark its own span
   as an error.

## The chain, in one picture

<pre class="mermaid">
flowchart TD
    browser["`browser
      TS, manual root span`"]
    claims["`claims-agent
      Python, a2a-sdk`"]
    settle["`settlement-agent
      Python, a2a-sdk`"]
    gw["`agentgateway
      Rust, v1.6.0
      SVID verified · policy consulted`"]
    pds["`policy-decision-service
      Python, gRPC ExtMCP`"]
    mcp["`payments-mcp
      Python, FastMCP`"]
    api["`payments-api
      Go, otelhttp
      system of record`"]

    browser <-->|"`Hop 1 — A2A/JSON-RPC over HTTP
      message/stream, task events`"| claims
    claims <-->|"`Hop 2 — A2A/JSON-RPC over HTTP
      message/send, JSON-RPC reply`"| settle
    settle -->|"`Hop 3a — MCP over streamable HTTP
      identity in an Authorization: Bearer JWT-SVID header
      trace context in BOTH a traceparent header
      and JSON-RPC params._meta`"| gw
    gw <-->|"`Hop 3p — gRPC, on EVERY tools/call, BEFORE the
      call reaches payments-mcp. failureMode: failClosed.
      A deny stops here.`"| pds
    gw -->|"`Hop 3b — MCP again, same transport, ONLY if the policy
      permitted. The gateway STRIPS the credential and
      re-stamps _meta with its own outbound span`"| mcp
    mcp -->|"`Hop 4 — plain HTTP/JSON
      otelhttp client to otelhttp server`"| api
</pre>

The autonomy lookup happens inside one box of that picture, not between two, so it
is drawn separately. It is a localhost call inside the policy service's pod, not a
network hop between pods:

<pre class="mermaid">
flowchart TD
    subgraph pod["one pod — no Service, no second Deployment"]
        pds["`policy-decision-service
      Python, gRPC ExtMCP
      pds/autonomy.py`"]
        flagd["`flagd
      sidecar, OFO-injected
      provider: kubernetes`"]
        pds -->|"`Hop 3f — gRPC, localhost:8013, INSIDE the same pod.
      flagd is an initContainer the Open Feature Operator
      injects into this pod's spec.`"| flagd
    end
    ff[["`FeatureFlag/northstar-autonomy
      a Kubernetes object`"]]
    flagd -->|"watches — a real watch, not a poll"| ff
</pre>

Every `tools/call` the policy service decides makes this lookup once:
`pds/autonomy.py::resolve_autonomy` asks flagd for the `autonomy-level` variant,
keyed on the caller's SPIFFE ID, and the answer becomes
`northstar.policy.autonomy.level` on the `northstar.policy.decision` span. This is
the one hop that changes behaviour without a code deploy: the `northstar-autonomy`
`FeatureFlag` object can be patched live, and flagd's Kubernetes watch means the
next lookup sees it. No pod restarts and no image is rebuilt;
`tests/e2e/test_progressive_autonomy.py` compares pod UIDs and image digests
before and after a flip to prove it.

The human's approval path is a second picture because it starts minutes later, from
a different browser tab, in a different trace:

<pre class="mermaid">
flowchart TD
    human["`human
      browser`"]
    rui["`reviewer-ui
      Python, FastAPI`"]
    api["`payments-api
      the same one`"]
    gw["`agentgateway
      same gateway, same jwtAuth, same PDS —
      different jwt.sub, different rule`"]
    mcp["payments-mcp"]
    ca["claims-agent"]

    human <-->|"plain HTML — form POST, rendered HTML"| rui
    rui -->|"`REST — list the queue, read a payment, reject`"| api
    rui -->|"`MCP — execute_payment under reviewer-ui's own
      SPIFFE ID`"| gw
    gw -->|"Hop 3p, then permit only"| mcp
    mcp --> api
    rui -->|"`A2A message/send — resume the waiting task`"| ca
</pre>

reviewer-ui reads the ledger directly but has no route to payments-api's
`execute`: the release goes through the gateway, so the human override is decided
by the same mechanism as everything else. It is not a side door.

Hop 3p is drawn as a branch off the gateway because the decision is not a hop the
claim travels through; it is a question the gateway asks and a verdict it acts
on. In Jaeger the gateway's `Guardrail` CLIENT span and the policy service's
`northstar.policy.decision` SERVER span are both children of the gateway's
`tools/call` SERVER span, siblings of each other, and the onward
`tools/call payments-mcp_<tool>` CLIENT span is their sibling too: present on a
permit, absent on a deny.

## The components

Namespaces, ports and DNS names below are read from `deploy/k8s/*.yaml`. The
cluster's namespaces: `observability`, `northstar-agents`, `northstar-tools`,
`northstar-systems` (declared in `deploy/k8s/namespaces.yaml`) and
`northstar-platform` (created by `scripts/setup-kind.sh` for infrastructure this
project configures but does not own), plus the namespaces of the installed
controllers (`linkerd`, `kyverno`, `cert-manager`, `open-feature-operator-system`,
SPIRE's). `customer-ui` and `reviewer-ui` live in `northstar-agents`, beside the
agents, even though they are not agents.

### Application components

| Component | Language / framework | Service : port | Role |
|---|---|---|---|
| `customer-ui` | TypeScript, Next.js (static export) served by a small Python Starlette server (`apps/customer-ui/server`) | `customer-ui:8080`, NodePort 30080 mapped to host `8080` | Owns the trace root in the browser. Submits claims, shows the waiting state, and receives the outcome pushed by claims-agent (`POST /a2a/push`), relaying it to the browser over `GET /claims/events`. |
| `claims-agent` | Python 3.13, `a2a-sdk` + Starlette | `claims-agent:9001` | A2A server (inbound from the browser) and A2A client (outbound to settlement-agent). Parses a JSON claim with no model; reads a free-text claim with an intake model and the same validator. Holds the A2A task while a claim waits. |
| `settlement-agent` | Python 3.13, `a2a-sdk`, `mcp` client, `pydantic-ai-slim` | `settlement-agent:9002` | A2A server and MCP client. A model call decides how to propose and execute the payment through payments-mcp. |
| `payments-mcp` | Python 3.13, FastMCP | `payments-mcp:8000`, endpoint `/mcp` | MCP server exposing `propose_payment`, `execute_payment`, `refuse_payment` and `get_payment`. An adapter over payments-api; it owns no state. |
| `payments-api` | Go, `otelhttp` | `payments-api:8080` | System of record for payments. In-memory store, sequential ids. Records facts and decides nothing. |
| `reviewer-ui` | Python 3.13, FastAPI, server-rendered HTML, no build step | `reviewer-ui:8080`, NodePort 30081 mapped to host `8081` | The page a human reads and clicks. Lists payments awaiting approval from payments-api, and on approval releases the payment itself through the gateway. |

What each is explicitly not responsible for:

- **customer-ui** talks only to claims-agent, its own server and the collector. No
  auth and no session state.
- **claims-agent** does not assess or settle. A free-text claim is read by a model
  (`extraction.py`), but the model only drafts: the same validator that checks a
  JSON claim decides whether the draft is a claim, and the code, not the model,
  decides what is missing. A valid claim is delegated to settlement-agent.
- **settlement-agent** does not talk to payments-api and owns no payment state.
  A model reasons about the claim, but the reply's amounts come from what the
  ledger recorded, never from the model's words.
- **payments-mcp** does no checking of its own: it translates an MCP call into an
  HTTP call to payments-api.
- **payments-api** knows nothing about A2A, MCP or agents beyond the `claimId`
  string. It has no authorization (see "What the system deliberately does not
  do"); it confirms that an instruction agrees with the proposal it refers to, and
  rejects only self-contradictory instructions.
- **reviewer-ui** decides nothing. It has no limit row, no MCP client of its own
  beyond the release call, and no route to the policy service. It reads the
  ledger, renders it and forwards a click.

### Platform components

Mostly third-party infrastructure this project configures but does not write, so
the no-invented-telemetry rule applies: only what the component itself emits is
used. Exact chart and image versions live in `scripts/setup-kind.sh` and
`deploy/`; they are not retyped here.

| Component | What it is | Role in this system |
|---|---|---|
| `linkerd` | CNCF service mesh; a proxy sidecar in each meshed pod | Authorizes **ingress** to the money path by workload identity (a counted 403). Egress is NetworkPolicy, not Linkerd. Proxy tracing is on. See "Reach". |
| `agentgateway` | Rust MCP/A2A/LLM proxy; a Linux Foundation project. A plain Deployment plus a ConfigMap-mounted `config.yaml` in `deploy/platform/agentgateway/`, not the Helm chart (the chart installs a Gateway API control plane with nowhere to put one inline `jwtAuth` policy) | Verifies the caller's JWT-SVID (`jwtAuth`), asks the policy service about every `tools/call` (`mcpGuardrails`), and forwards to payments-mcp only on permit. Also carries one CEL ceiling rule (see "Two ways to say no"). |
| `policy-decision-service` | **This project's own code** (Python, `grpcio`), listed here because it is platform machinery. Implements agentgateway's `agentgateway.dev.ext_mcp.ExtMcp` contract from a vendored proto | Answers permit or deny on caller identity, autonomy level and ledger state. Emits the `northstar.policy.decision` span. Holds no RBAC; nothing in `northstar-agents` can reach it. |
| SPIRE (server, agent, controller-manager, CSI driver, OIDC discovery provider) | SPIFFE runtime from the hardened chart | Issues the JWT-SVID. Trust domain `northstar.local`. The discovery provider publishes SPIRE's signing keys as a JWKS the gateway fetches. |
| Open Feature Operator, `cert-manager` | Operator that injects a `flagd` sidecar; cert-manager signs the operator's webhook certificate | Put flagd inside the policy service's pod. |
| `flagd` | Injected sidecar (an `initContainer` with `restartPolicy: Always`) | Watches the `northstar-autonomy` `FeatureFlag` (`provider: kubernetes`) and answers evaluations over gRPC on `localhost:8013`. Its OFREP REST port is how test harnesses confirm a live flip resolved. |
| Kyverno | Admission controller; one `ValidatingPolicy` (`policies.kyverno.io/v1`, CEL), `agents-cannot-edit-their-own-authority` | The keystone. See "The rings, and the keystone". |
| `backstage` | Third-party framework plus one plugin of this project's own (`autonomyContract/`) | Serves the software catalog, TechDocs and the autonomy-contract card, the one UI with a write path: it flips `northstar-autonomy` through a dedicated ServiceAccount that may patch that one object and nothing else. |
| OpenTelemetry Collector, Jaeger, Prometheus | Evidence pipeline | Everything is OTLP to the collector, then Jaeger (traces) and Prometheus (metrics). Sampling is always 100%. |
| Weaver live-check | OpenTelemetry Weaver receiving a copy of the telemetry | Checks emitted telemetry against the registry in `telemetry/registry/`. |
| AgentEvals | Scores what the agent did from its own spans | Run on demand with `scripts/demo evals`. |

**SPIRE contributes metrics only.** The SPIRE version in use has no OpenTelemetry
support: no OTLP exporter and no tracing. It adds no spans and none are invented
for it. Its Prometheus metrics are scraped into the collector like any other
metric; see [`telemetry-contract.md`](telemetry-contract.md).

## The waiting claim: A2A's own task lifecycle

The customer-ui submits a claim with `message/stream`, and the claim is an A2A task
with real states: `submitted → input-required → completed` when a payment is routed
to a human, or `submitted → completed` when the settlement agent settles it.

- **The claim stream** returns at `input-required` (or a terminal state) and the
  browser closes it. Its first event carries the task id. The status message is one
  JSON text part with `claimId`, `policyNumber`, `paymentId`, `amount`, `currency`,
  `status`, `narrative`, `rule`, `limit` and `decision`.
- **The first `execute()` records `input-required` and returns.** Trace A, the
  agent's work, ends at the hand-off. Nothing is parked and nothing polls while the
  claim waits. The parked span context and reply ride in the status update's task
  metadata (`northstar.a2a.parked_span_context`, `northstar.a2a.parked_reply`; A2A
  metadata, not telemetry). The `TaskStore` is in memory, so a claims-agent restart
  loses waiting claims.
- **The person's work is trace B.** reviewer-ui records `northstar.review.decision`,
  releases the payment through agentgateway under rule `human-approved.execute`, and
  sends a blocking `message/send` with the same task id. claims-agent does not trust
  that message: a non-user-role resume is refused, it reads the reply from the
  metadata it wrote itself, and it reads the payment from payments-api (one
  `GET /payments/{id}`) and reports what the ledger says. A resume the ledger does
  not back is refused and the task keeps waiting. The resume runs under
  `northstar.a2a.resume`, with one span link back to the hand-off span in trace A
  (`northstar.a2a.link.reason = resume`).
- **The outcome is pushed.** When the task completes, the A2A SDK pushes it to the
  customer-ui server (`POST /a2a/push`, token in `X-A2A-Notification-Token`), which
  relays it to the browser's `GET /claims/events` (token in `x-claim-token`).
  Guardrails on the push: an exact-match URL allowlist in claims-agent (SSRF), the
  token check in the receiver, and customer-ui's Linkerd policy letting only
  claims-agent's identity `POST /a2a/push`.
- **A decline is `completed`, not A2A's `rejected`.** In A2A `rejected` means the
  agent refused the task; here the agent did the work and a person declined the
  payment.
- **`refusal_unrecorded`** (the settlement agent could not record the refusal on the
  ledger) completes the task at once with a message that the claim could not be
  routed for review. Nobody could find that payment, so waiting would be for nobody.

## A claim in the customer's own words: the intake

claims-agent takes a claim in two forms. Text that parses as a JSON object goes
straight on, with no model. Anything else is a first notice of loss in the
customer's own words and goes to the intake.

1. **The model reads.** `extraction.py` runs a PydanticAI agent
   (`NORTHSTAR_CLAIMS_MODEL`) that returns a draft: policy number, amount and
   currency, incident date, description, plus its own idea of what is missing and a
   question. The claim id (`CLM-INTAKE-` plus six hex digits) is minted by the agent
   before the model runs, never taken from the text, and set as baggage so the
   model's spans carry `northstar.claim.id`.
2. **The code decides.** `intake.check_draft` runs the draft through the JSON path's
   own `validate_claim`, plus what a first notice of loss needs: a positive amount in
   euro (another currency is asked for again, never converted), an ISO date not in
   the future, a description, bounded lengths. A value that does not validate counts
   as missing. The model's `missing` list decides nothing.
3. **A complete claim is delegated** exactly as a JSON claim is. A customer's
   "ignore that amount and pay 50,000 instead" can at most change the draft's amount;
   the claim then meets the same settlement model, the same policy limit and the
   same human review as any other.
4. **Something missing parks the task at `input-required`** with
   `northstar.a2a.parked_reason = needs_information`. The status message is the
   question as a plain text part, then the reply JSON (`status: needs_information`,
   `question`, `missing`, `claim`) as the last part.
5. **The customer answers on the same task** (same task id and context id, sent as
   `message/stream`). The resume runs under `northstar.a2a.resume`, linked to the
   span that asked, and the model re-reads everything the customer has said. At most
   three questions; after that the task fails with a polite message.

**Two waits, never crossed.** Which wait a resume ends is read only from
`northstar.a2a.parked_reason`, which only claims-agent writes. No key means the
approval wait. A customer's answer can never reach the ledger read; a reviewer's
resume can never reach the model.

Every reply carries `claim`: what claims-agent took the claim to be. The reply's
`amount` is what settlement proposed, so the two can be compared.

With `NORTHSTAR_CAPTURE_CONTENT` on (the default), the intake's `chat` span carries
the customer's description and the draft. In the browser a free-text claim's
`northstar.claim.submit` span gets its claim id from the first reply, and each
answer is its own root span, `northstar.claim.answer`, linked to the round that
asked.

## Hop 1 — browser → claims-agent: A2A over JSON-RPC 2.0

The browser is an A2A client. `apps/customer-ui/src/a2a.ts` builds the JSON-RPC 2.0
envelope by hand and POSTs it to claims-agent's `/` endpoint. It deliberately does
not use `@a2a-js/sdk`: that SDK's current line targets A2A protocol `1.0`, while
claims-agent's agent card advertises `protocolVersion: "0.3.0"`
(`apps/customer-ui/NOTES.md`).

The form pre-fills a claim (`CLM-1842`, `POL-77120`, a rear-end collision, €6,200).
The claim travels as a JSON string inside `params.message.parts[0].text`; A2A wraps
arbitrary text and the payload happens to be JSON, so claims-agent's `intake.py`
parses that inner string, not the outer envelope.

Trace context is injected by hand (`injectContext` in
`apps/customer-ui/src/telemetry.ts`) because nothing auto-instruments the browser.
claims-agent's `CORSMiddleware` allows origin `http://localhost:8080` and explicitly
allows the `traceparent`, `tracestate` and `baggage` headers; without that, the
preflight would strip them and the root span would detach from everything
downstream. The browser's own OTLP export (the `northstar.claim.submit` span) goes
to the collector at `http://localhost:4318/v1/traces`, which needs its own CORS
entry for the same origin (`deploy/observability/collector-config.yaml`).

## Hop 2 — claims-agent → settlement-agent: A2A behind an allow-list

claims-agent delegates by building the same kind of JSON-RPC `message/send`
envelope (`delegation.py`) and POSTing it to whichever URL
`ClaimsAgentExecutor._delegate(name)` resolves. Resolution is gated by an explicit
allow-list read from the `NORTHSTAR_ALLOWED_AGENTS` environment variable, not by
discovery. The deployed manifest has exactly one entry:
`settlement-agent=http://settlement-agent.northstar-agents.svc.cluster.local:9002`.

What the allow-list protects against: claims-agent's own code calling an agent name
that was never configured. The lookup raises before any network call, and the error
names what is permitted.

What it does not protect against:

- It is enforced in-process. Network reach to settlement-agent is restricted by
  Linkerd (only claims-agent's identity may call `settlement-agent:9002`), but the
  allow-list itself checks a *name*, not an identity.
- It says nothing about whether the delegated call should be allowed. Once a name is
  in the map, the amount, the claim and the decision to pay are checked by nothing
  at this hop.

## Hop 3 — settlement-agent → payments-mcp, through agentgateway

settlement-agent's `PAYMENTS_MCP_URL` is
`http://agentgateway.northstar-platform.svc.cluster.local:3000/mcp`, so the hop is
three legs:

| | From → to | What is different about it |
|---|---|---|
| **Hop 3a** | `settlement-agent` → `agentgateway` | Carries the credential: `Authorization: Bearer <JWT-SVID>`. Trace context rides in two independent carriers (below). The only authenticated hop in the A2A/MCP chain, and the only authorized one. |
| **Hop 3p** | `agentgateway` → `policy-decision-service` | gRPC, `agentgateway.dev.ext_mcp.ExtMcp/CheckRequest`, on every `tools/call`, at the REQUEST phase, before the call reaches payments-mcp. Not on the claim's path: a question the gateway asks. |
| **Hop 3b** | `agentgateway` → `payments-mcp` | Same MCP transport and tool call, **only if Hop 3p permitted**. The gateway has stripped the credential (`jwtAuth.preserveToken` is off) and re-stamped `params._meta` with its own outbound span, so payments-mcp verifies nothing and sees no identity. |

Both MCP legs are streamable HTTP. `payments-mcp/server.py` runs
`mcp.run(transport="http", ...)`; settlement-agent connects with the `mcp` client's
streamable-HTTP transport. Never stdio: stdio cannot traverse a gateway, and an
stdio MCP client carries trace context only in `_meta`, which the gateway does not
read on the way in.

payments-mcp is unmodified by the gateway and policy: it does not know a proxy is in
front of it. The tool is governed without being told it is being governed.

**On a refusal, payments-mcp sees nothing.** There is no `payments-mcp` span for the
refused `tools/call` and no `payments-api` execute span. The refusal is not a call
that failed; it is a call that never happened.

The gateway is a node in the trace, not a fracture:

<pre class="mermaid">
flowchart TD
    s1["`settlement-agent · **execute_tool**`"]
    s2["`settlement-agent · **POST**
      httpx CLIENT`"]
    s3["`agentgateway · **tools/call**
      SERVER`"]
    s4["`agentgateway · **Guardrail**
      CLIENT`"]
    s5["`policy-decision-service · **northstar.policy.decision**
      SERVER`"]
    s6["`agentgateway · **tools/call payments-mcp_#60;tool#62;**
      CLIENT`"]
    s7["`payments-mcp · **tools/call #60;tool#62;**
      SERVER`"]
    s8["`payments-mcp · **POST**
      httpx CLIENT`"]
    s9["`payments-api · **POST /payments/...**
      SERVER`"]

    s1 --> s2 --> s3
    s3 --> s4
    s3 --> s5
    s3 -->|"permit only"| s6
    s6 --> s7 --> s8 --> s9
</pre>

One trace id throughout, no new roots and no orphans. The three spans under the
gateway's `tools/call` are **siblings**: `northstar.policy.decision` is not a child
of `Guardrail`, because agentgateway propagates the context it *received* to the
guardrail hook, not its own client span's. `tests/e2e/test_gateway_trace.py` pins
the shape, the span kinds, the parentage (by exact equality on the sorted
`(service, operation, kind)` triples of that span's children) and the gateway's
attribute vocabulary. The tool name is **not** in the gateway's SERVER span name:
every tool call produces a span named exactly `tools/call`; read
`gen_ai.tool.name` to tell them apart.

**Why the trace survives, and why two mechanisms must not be conflated.** The
gateway *ingests* trace context from the HTTP `traceparent` header and from nowhere
else; it *emits* SEP-414 `params._meta` on the messages it forwards. Those are
independent directions, not a round trip.

- **Ingress.** The httpx instrumentation auto-injects `traceparent` on every
  outbound MCP request, because the MCP streamable-HTTP transport is httpx-based
  (`HTTPX2ClientInstrumentor`, since the `mcp` 2.x transport is built on `httpx2`).
  The gateway reads that. It does not read `params._meta` on the way in
  ([`upstream-findings.md`](upstream-findings.md) Finding 6).
- **Egress.** The gateway stamps its own outbound span into `_meta` on the messages
  it forwards. That is why payments-mcp parents under the gateway, and why its
  `initialize` spans join the trace too.

MCP's own propagation convention (SEP-414) rides `params._meta` and never HTTP
headers. settlement-agent's hand-injection into `_meta` (`mcp_tools.py`) is for
FastMCP's benefit at the far end; the header the gateway reads comes free from the
instrumentor. Nothing here means agentgateway supports SEP-414 on ingress.

### The MCP tools

Four tools (`contracts/mcp/payments-mcp.json`, matching `tools/payments-mcp/server.py`;
`tests/e2e/test_payments_mcp_trace.py` diffs the served tool list against the
contract):

```
propose_payment(claim_id: string, amount: number, currency: string = "EUR") -> dict
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }

execute_payment(payment_id: string, amount: number, currency: string = "EUR") -> dict
  annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: false }

refuse_payment(payment_id, rule, limit, requested, narrative,
               refused_trace_id, refused_span_id) -> dict
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }

get_payment(payment_id: string) -> dict
  annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false }
```

**The annotations are declared but read by nothing.** The policy service does not
read `destructiveHint`: agentgateway's guardrail hook hands it only the JSON-RPC
`params` of the call (`{"name": ..., "arguments": {...}}`), with no tool schema or
annotations. `pds/rules.py` decides from frozensets of tool *names*
(`UNLIMITED_TOOLS`, `LIMITED_TOOLS`) and a `CALLER_SCOPED_TOOLS` mapping. The
consequence is debt, not nuance: a newly added tool falls into neither set, so
`decide()` refuses it under the rule `unknown_tool`. That fails closed, which is the
right direction, but every new tool needs a `rules.py` edit rather than inheriting
governance from what it declares about itself.

## Hop 4 — payments-mcp → payments-api: plain HTTP/JSON

`payments_client.py`'s `httpx.Client` calls payments-api directly; no MCP, no A2A.
The routes (`systems/payments-api/handlers.go`, `contracts/openapi/payments-api.yaml`):

| Method & path | Purpose | Status codes |
|---|---|---|
| `GET /healthz` | Probe. Deliberately not wrapped in `otelhttp`: a readiness probe polling it would flood the trace view. | `200` |
| `POST /payments/propose` | Record a proposal (`claimId`, `amount`, `currency`) as `proposed`. | `201`; `400` for malformed JSON, missing `claimId`, negative amount |
| `POST /payments/{id}/execute` | Execute. The body **must state the amount and currency** being paid and is checked against the proposal. | `200`; `400` for an absent body or absent amount; `404`; `409` for a stated amount or currency that does not match the proposal, for an already executed payment, or for a rejected one |
| `POST /payments/{id}/refuse` | The agent records why it did not pay (`rule`, `limit`, `requested`, `narrative`, `refusedTraceId`, `refusedSpanId`); the payment becomes `awaiting_approval`. Only `rule` is required. The ledger stamps `awaitingSince` itself. | `200`; `400`; `404`; `409` for a mismatched amount or a payment already executed, rejected or awaiting approval |
| `POST /payments/{id}/reject` | A human declines an `awaiting_approval` payment; final. | `200`; `404`; `409` |
| `GET /payments` | List, sorted by id. `?status=` filters; an unrecognised status matches nothing and returns `[]`. | `200` |
| `GET /payments/{id}` | Read one payment. | `200`; `404` |

**Ledger state machine.** `proposed → executed` (the agent, inside its band) or
`proposed → awaiting_approval` (the agent was refused and recorded why);
`awaiting_approval → executed` (reviewer-ui, through the gateway) or
`awaiting_approval → rejected` (final). `executed` and `rejected` are terminal;
executing a rejected payment is a 409 whose `northstar.payment.refusal_cause` is
`rejected`.

Two properties worth knowing:

- The two `409`s on `execute` come from different checks (confirmation of intent,
  and the terminal-state guard). A mismatched *currency* is refused by the same
  comparison as a mismatched amount, not by schema validation, which is why the
  OpenAPI contract does not put `enum: [EUR]` on the execute body: the field is a
  claim to be checked, not an instruction to be validated.
- The ledger stamps `awaitingSince` with its own clock. The elapsed time a human
  reviewer is measured against must not be a figure a caller can supply.

Every route except `/healthz` is wrapped in `otelhttp.NewHandler(...)`
individually, so the span name is the route template (`POST
/payments/{paymentId}/execute`), not the raw path. `GET /payments` carries no
claim-id baggage annotation: it lists across claims, so there is no single claim id
to read.

**The ledger is not the authorization point.** payments-api is a record, not a gate;
the gateway and the policy service are the gate.

**Payment ids are deterministic and reset.** The store is an in-memory map with a
counter formatted `PAY-%06d`, starting at 1 on every process start, so ids are
strictly sequential within one pod's life and the demo's numbers are identical on
every run. Nothing else mints or rewrites them: not the gateway, not SPIRE. Anything
that proposes a payment advances the counter (demo runs, the e2e suite, probes), so
nothing in the repo asserts an absolute record count or a hardcoded next id;
assertions are relative to their own claim id.

## The settlement decision: a model chooses, the ledger states the amounts

`agents/settlement-agent/settlement.py` validates the inbound claim (`claimId`
present, `amount` present and non-negative), then runs a PydanticAI `Agent` against
the configured model (`NORTHSTAR_SETTLEMENT_MODEL`). Its instructions are to settle
the assessed amount by calling `propose_payment` and then `execute_payment` with the
returned `paymentId` and the same amount, and to reply with a short narrative
without stating amounts. The prompt is one templated sentence built only from
`{claimId, amount, currency}`; the claim's free-text description never reaches the
settlement model.

**The model chooses the tool sequence; the code states the result.** The reply's
`amount`, `currency`, `status` and `paymentId` come from what payments-mcp returned
(captured by the tool functions as a side effect), never from the model's words.
Only `narrative` is the model's. If the model finishes without calling
`execute_payment`, `decide_settlement` raises rather than reporting success, and a
failed payments-mcp call propagates as an error rather than being smoothed into
prose. `test_reply_amount_comes_from_the_ledger_not_from_the_model` drives a scripted
model that narrates one amount while the mocked ledger returns another.

**A reader watching Jaeger sees:** the two real network calls (propose, execute),
plus one `invoke_agent settlement_agent` span, one `chat <model>` span per model turn
(propose, execute, final narrative), and `execute_tool` spans, with a `POST` CLIENT
span nested under each `chat` span for the model's own HTTP hop
(`HTTPX2ClientInstrumentor`). The attribute inventory is in
[`telemetry-contract.md`](telemetry-contract.md).

**There is no limit, threshold or guard rail in the agent.** The only numeric checks
in `settlement.py`, `executor.py`, `mcp_tools.py` and `tools/payments-mcp/*.py` are
the inbound-claim guards (`amount is None`, `amount < 0`). The limit lives in the
platform, at a choke point the agent cannot reach around.
`tests/e2e/test_policy_denial.py`'s criterion-4 test walks both components' source
with `ast` and fails on any policy vocabulary, any literal equal to a policy
allowance, or any unallowlisted comparison involving money.

## The decision the platform makes

The policy service (`platform/policy-decision-service`, `pds/rules.py`) is a gRPC
service implementing agentgateway's `ExtMcp` contract. The rules, in full, because
there are few enough that hiding them would be worse than printing them:

| Caller | Autonomy level | `execute_payment` is permitted |
|---|---|---|
| `settlement-agent` | `assisted` | never (limit €0) |
| `settlement-agent` | `autonomous` | up to the `EXECUTE_LIMITS` figure (€6,000), and only a payment the ledger shows `proposed` (else rule `settlement-agent.payment_not_proposed`, or `settlement-agent.ledger_unreadable` when the ledger cannot be read) |
| `reviewer-ui` (in `HUMAN_APPROVERS`) | none | only when payments-api shows the payment `awaiting_approval`, under rule `human-approved.execute` |
| any other caller | any | never (`unknown_caller`) |

The policy service reads the ledger status for every `execute_payment` (an
unreadable ledger denies), so the maker cannot release a payment it routed to a
human, even at `autonomous` and under the limit. Only reviewer-ui is in
`HUMAN_APPROVERS`, and the Kyverno keystone refuses an agent the verbs to run a pod
as, or mint a token for, that ServiceAccount.

Other tools: `UNLIMITED_TOOLS` (`propose_payment`, `refuse_payment`, `get_payment`)
move no money and reverse nothing; `refuse_payment` is further scoped to the
settlement-agent identity (`CALLER_SCOPED_TOOLS`), so a holder of any other valid
SVID cannot author the refusal record a human reads. Any other tool, caller or
level, and any amount that is not finite and positive: denied. The limit test is
`amount > limit`, so `NaN`, zero and negatives would pass it; they are refused
separately under `invalid_amount`, so an audit trail distinguishes "the limit
considered this and said yes" from "this was never a payable amount".

Properties argued and kept:

- **Identity is structurally required.** The allowance table is keyed on
  `(SPIFFE ID, autonomy level)`, so a decision cannot be reached without a caller.
  Changing the identity changes the verdict; the e2e tests show this with a second
  real SPIRE-minted JWT-SVID, not a mocked one.
- **Proposing and executing are different acts.** The trace shows both decisions: the
  same caller is permitted to propose €6,200 and refused permission to execute it.
- **A parse failure is a denial, never a gRPC error.** `parse_call` never raises; it
  returns a `ToolCall` with holes in it, which `decide` refuses. agentgateway routes
  a gRPC error through `failureMode` and reports it as a policy *outage*, a
  different sentence on stage and in a trace.
- **Only `tools/call` is decided.** `initialize`, `notifications/initialized` and the
  transport's own `GET`/`DELETE` carry no tool and no amount. The service denies any
  other method if handed one, so widening the config fails loudly.

**`failureMode: failClosed` is set explicitly.** agentgateway's `FailureMode`
defaults to `failClosed` (measured against the pinned image: an omitted key behaved
byte-for-byte like `failClosed`, and only `failOpen` let calls through). The line
stays because fail-open is one word away and invisible in a healthy cluster; stating
the value makes the intent reviewable. `tests/unit/test_pds_config.py` fails if the
deployed config ever says `failOpen` or drops the key.

**What the model does with a refusal.** `mcp_tools.PolicyDenial` is a
`pydantic_ai.ToolFailed`, so the framework hands the refusal to the model as a
result, not as a retry instruction (a `ModelRetry` would say "try again"). The
denial is recognised by the structured marker `data.retryable: false` /
`data.decision: "deny"` the policy service puts in the JSON-RPC error's `data`,
never by pattern-matching the reason prose. `settlement.py`'s `_latch` makes the
guarantee independent of the model's cooperation: after a refusal, no further payment
call leaves the process for that claim.

**A refusal comes back as a reply, not an error.** The agent records the refusal on
the ledger (`refuse_payment`, which makes the payment `awaiting_approval`) and
returns a normal A2A reply with the model's narrative. The claimant is owed an
explanation rather than a stack trace, and the platform refusing is the platform
working.

## Reach: Linkerd for ingress, NetworkPolicy for egress

Everything above is enforced at the application layer, by a proxy an agent chooses to
talk to. The network layer makes that not a choice, in both directions, with two
mechanisms each doing the half it is good at:

- **Ingress (who gets in) is Linkerd**, at the destination's proxy, by workload
  identity. `deploy/platform/reach/linkerd-policies.yaml` holds `Server`s for
  payments-mcp:8000, policy-decision-service:9100, settlement-agent:9002,
  payments-api:8080 and customer-ui:8080, with their `HTTPRoute`s,
  `AuthorizationPolicy`s and `MeshTLSAuthentication`s (plus `NetworkAuthentication`s
  for the probe and catch-all routes). The identity is
  `<serviceaccount>.<namespace>.serviceaccount.identity.linkerd.cluster.local`, taken
  from the caller's mTLS certificate, which is why each workload has a dedicated
  ServiceAccount: a policy cannot name `default`.
- **Egress (who gets out) is core NetworkPolicy**, enforced in the node by kindnet
  (kind's default CNI). `deploy/platform/reach/network-policies.yaml` holds one
  document per `northstar-agents` workload with an egress inventory. Linkerd cannot
  do egress alone: its `EgressNetwork` is namespace-wide, matches on SNI, and any pod
  that skips its proxy skips it. NetworkPolicy works whether or not the workload
  cooperates.

Meshed: the three app namespaces, agentgateway, policy-decision-service and the
OpenTelemetry collector. Not meshed: the rest of observability, SPIRE, Kyverno,
flagd, Backstage, `kube-system`. Linkerd's default inbound policy is
`all-unauthenticated`, so only a port with a `Server` is restricted. Proxy tracing is
on: every proxy sends a span per traced HTTP request to the collector over OTLP gRPC
and mTLS, which is why the collector is meshed. One meshed hop gains four proxy
spans; the e2e harness skips them when it walks parents.

| Direction | Policy | What it makes impossible |
|---|---|---|
| Ingress | `payments-mcp:8000` and the policy service admit agentgateway's identity and nothing else | A workload reaching the tool or the decision point without crossing the gateway |
| Ingress, per route | payments-api authorizes each route for the identities that call it; `execute` is payments-mcp's alone, reviewer-ui may list, get and reject only. customer-ui authorizes `POST /a2a/push` for claims-agent alone | reviewer-ui or claims-agent POSTing `/payments/{id}/execute` straight at the ledger, skipping the gateway and the policy service |
| Ingress | `settlement-agent:9002` admits claims-agent's identity | Anything else calling an A2A port that can execute a proposed payment |
| Egress | One document per workload. settlement-agent may reach DNS, agentgateway:3000, the collector, the Linkerd control plane and, by `ipBlock`, the model provider's published range; claims-agent may reach DNS, settlement-agent:9002, payments-api:8080, customer-ui:8080 (the push), the collector, the control plane and the same range (its intake model); customer-ui and reviewer-ui have short lists | An agent reaching the model provider (all but the two that need it), the ledger, the tool or the policy service directly |

Behaviour worth knowing when probing it:

- **A Linkerd refusal is a visible HTTP 403**, empty body, counted as
  `inbound_http_authz_deny_total{srv_name, route_name, client_id}` on the destination
  proxy's `:4191/metrics`. A request that matches no route on a Server that has
  routes is a 404 from the proxy. A gRPC `PERMISSION_DENIED` at the policy service is
  counted the same way.
- **A meshed pod's TCP connect proves nothing**: its own outbound proxy accepts every
  connect locally. The egress verdict shows only above TCP (a TLS handshake or HTTP
  request to an allowed address completes; to a denied one it is reset or answered
  504). From an *unmeshed* pod a connect timeout is still the oracle.
- **Every egress rule to a meshed peer must allow the service port and TCP 4143** (the
  peer proxy's inbound port). 4143 is the whole gate: a meshed client that may reach
  a peer's 4143 can reach any non-loopback port on that peer pod. Which ports answer
  is decided by the peer's Linkerd Servers. The Linkerd control plane needs TCP 8080
  (identity), 8086 (destination) and 8090 (policy) in namespace `linkerd`.
- **`kubectl port-forward` bypasses the proxy**, so a port-forward to a port behind a
  denying Server is not refused. No reach test probes through one; the demo's deny
  counter is read through the API server's pod proxy.
- **A Server with routes refuses kubelet probes** unless a probe route is authorized
  (all networks). A Server without routes authorizes `GET` on the probe path for
  every caller, so a probe path must never be an API path. `tcpSocket` probes pass
  behind a denying Server.

Trade-offs, stated plainly:

- **Egress is by address range, not by name.** The model provider's range is static
  and "will not change without notice". Any other host in that range is also
  reachable from settlement-agent, and a provider address change shows up as a TLS
  reset, not a policy message. The host `api.anthropic.com` is configured nowhere in
  this repository (it is the client's own base URL); the range is written into
  `network-policies.yaml`. If either moves, the policy fails closed.
- **Egress between meshed pods is per pod, not per port.**
- **Egress enforcement is kind's.** The policy objects are portable; the enforcement
  is not. A production cluster needs a CNI that enforces NetworkPolicy.
- **claims-agent can reach more of customer-ui than the push route.** The Linkerd rule
  restricts who may call `POST /a2a/push`; a catch-all route (the browser and the
  probe need it) keeps the rest of customer-ui open to everyone, claims-agent
  included.
- **More moving parts:** a proxy in each meshed pod, a control plane, and a
  certificate chain generated by `setup-kind.sh` into a git-ignored directory. OSS
  Linkerd ships edge releases only; the version is pinned in `setup-kind.sh`.

What it gives: refusals you can show (a 403 and a counter per policy); reach keyed by
the same identity (namespace and ServiceAccount) SPIRE attests, not by pod labels
anyone with a Deployment can set; mTLS between meshed workloads; and host-network pods
have no mesh identity.

**One identity, two credentials.** A settlement-agent pod holds Linkerd's certificate
(may it *connect*) and the SPIRE SVID (may it *call this tool*). They do not wrap each
other: agentgateway checks the SVID JWT in the request; Linkerd checks the TLS peer.

No reach telemetry is asserted as a span. Refusals are counted by the proxy, not
traced, and an egress drop is silent; the evidence is an HTTP status plus the deny
counter, or a probe from inside a pod.

## Workload identity

Hop 3a authenticates its caller cryptographically: settlement-agent carries a SPIFFE
JWT-SVID issued by SPIRE, and the gateway refuses the call without one.

Pinned in `deploy/platform/spire/`: the `spiffe/spire-crds` and `spiffe/spire` charts,
trust domain `northstar.local`, cluster name `northstar`. Identities are rendered from
`spiffe://{{ .TrustDomain }}/ns/{{ .PodMeta.Namespace }}/sa/{{ .PodSpec.ServiceAccountName }}`.
Explicit `ClusterSPIFFEID` resources (`registration.yaml`) cover:

```
spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent
spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui
spiffe://northstar.local/ns/northstar-platform/sa/agentgateway
spiffe://northstar.local/ns/northstar-platform/sa/policy-decision-service
```

Each is attested on namespace and Kubernetes service account, which is why each
workload has a dedicated ServiceAccount: an identity whose service-account component
reads `default` is not something a policy can decide on. A `northstar-platform`
workload needs an explicit entry because the chart's catch-all fallback excludes that
namespace, so a workload placed there with no entry gets no identity at all, silently.

**Which pods mount the Workload API socket.** settlement-agent mounts it and fetches a
JWT-SVID. reviewer-ui mounts it as well, for two uses: its release call to the gateway
carries its own JWT-SVID, and it reads its own SPIFFE ID back for the
`northstar.review.decision` span (`northstar.review.reviewer.spiffe_id`). The gateway
and the policy service mount nothing and present no SVID; their entries exist because
the gateway's ID is the **audience** an agent's token is minted for (that string has
to name a real registered identity), and the policy service's entry is what makes its
own certificate possible if the ExtMCP hop is ever hardened. An identity nothing
registers is a string, not an identity.

`tests/unit/test_spire_config.py` checks every `spiffe://` string in this document and in `telemetry-contract.md`
and fails on any this repository does not configure. It checks membership, never
cardinality, so no count of identities is written here.

**How the credential gets into the pod.** No secret, key or token is in any manifest.
The `spiffe-csi-driver` bind-mounts the spire-agent's Workload API socket at
`/spiffe-workload-api/spire-agent.sock`, and the agent's Kubernetes workload attestor
identifies the calling container via kubelet before answering. `svid.py` asks that
socket for a JWT-SVID scoped to one audience and caches it until 30 s before expiry;
`mcp_tools.py` presents it as `Authorization: Bearer` on the MCP transport. The
socket answers with the attested identity of whichever pod is asking, so no
configuration selects the ID.

**What the gateway checks.** `jwtAuth` in `mode: strict`: issuer, audience, signature
(against SPIRE's JWKS, fetched from the OIDC discovery provider) and `exp`. It is
verification, not authorization; authorization is the external gRPC hook
(`mcpGuardrails`), a deliberate choice so policy lives in a service this project
writes and tests rather than in a proxy's config language. Losing either makes the
other meaningless: a policy without authentication decides on a self-asserted name,
and authentication without a policy permits everything it can authenticate.

**Why a bearer token and not mTLS.** mTLS is stronger (a peer certificate cannot be
replayed from a copy; a JWT-SVID can), but threading a SPIFFE TLS context through the
MCP client's transport was not worth the risk. A JWT-SVID keeps the identity SPIFFE
issued, signed by SPIRE and verified via JWKS, at the cost of one header. The weakness
is bounded: the audience scopes each token to this one gateway, and the TTL is one
hour (SPIRE's `default_jwt_svid_ttl`).

**Known identity gaps, in order of importance:**

1. mTLS instead of (or under) the bearer token, per the above.
2. The other hops carry no SVID: browser → claims-agent, claims-agent →
   settlement-agent and payments-mcp → payments-api. Network controls restrict who can
   reach them (see "Reach"); that is not an identity check.
3. The JWKS endpoint is plain HTTP inside the cluster
   (`spiffe-oidc-discovery-provider.tls.spire.enabled: false`). Signature
   verification does not depend on transport security (the keys are public), but an
   attacker able to intercept the fetch could serve a key set of their own choosing.
4. payments-mcp checks nothing. The gateway removes the credential after validating
   it, so the MCP server behind it sees no identity and would accept a direct call
   that skipped the gateway; only the reach policy prevents one.

**Image tags the chart pins.** The SPIRE chart's sub-images resolve to exact tags and
nothing floats, but several trail upstream's latest. They are the chart's own choices
and are deliberately not overridden in `values.yaml`: overriding sub-image tags means
running a combination the chart's maintainers never tested. If they need bumping, the
move is a newer chart.

**Cluster-recreate hazard.** The OIDC discovery provider's `ensure-alternate-names`
init container is pinned by digest against a rolling `:latest` tag on Chainguard's
free namespace, which may garbage-collect the digest. The failure is an image pull
failure on a cluster recreate, and it fails closed: the provider never becomes ready,
the gateway cannot fetch the JWKS, every MCP call is refused. Before a recreate,
pre-pull and `kind load` the image or mirror it into the shared `kind-registry`.

### Identity with teeth

An identity that is issued, sent and verified but never decides anything is
decoration. `tests/e2e/test_caller_identity.py` is the negative half: it speaks MCP
to the gateway directly (no model tokens) and asserts each of these is refused with
HTTP **401** and a `text/plain` body carrying agentgateway's own `TokenError` text
(`jwtAuth` runs as an HTTP filter before the MCP backend is consulted):

| Presented | Refused with |
|---|---|
| no token | `authentication failure: no bearer token found` |
| `Bearer not-a-real-svid` | `authentication failure: the token header is malformed: Error(InvalidToken)` |
| a well-formed JWT from an untrusted issuer, own `kid` | `authentication failure: token uses the unknown key "…"` |
| the same, wearing SPIRE's current `kid` | `authentication failure: the token is invalid or malformed: Error(InvalidSignature)` |
| a genuine SPIRE-minted JWT-SVID for a different audience | `authentication failure: the token is invalid or malformed: Error(InvalidAudience)` |

The last row is the one that isolates a claim check: the token is minted by the real
SPIRE server, signed by the real key in the JWKS the gateway fetches, and carries the
real issuer and settlement-agent's own subject. Only `aud` differs, so only the
`audiences` list can explain the refusal: a token issued for one SPIFFE service in
this trust domain cannot be replayed at another.

**What is not proven.** The tests do not show that agentgateway's `iss` string
comparison fired. The gateway looks the signing key up by the token's `kid` and only
then decodes, which verifies the signature before validating `iss` and `aud`; an
untrusted issuer is always caught earlier, by key trust. The `iss` comparison is
independently testable only in a multi-issuer configuration, which is not deployed.

## The rings, and the keystone

The platform bounds an agent in layers, an organisation taken from William Rizzo's
*Bounded Agent Autonomy* demo (OSFF 2026). The argument is that you do not trust an
agent to remember its own authority limits; you make the platform enforce them.

| Ring | Question | Northstar's answer |
|---|---|---|
| Core · Identity | who is it | SPIRE JWT-SVIDs, verified by the gateway on every call |
| Ring 1 · Reach | what can it connect to | Linkerd (ingress, by identity) and NetworkPolicy (egress): the gateway is the only way into `payments-mcp` |
| Ring 2 · Capability | what may it do | agentgateway → ExtMCP → policy-decision-service, `failClosed`; the autonomy flag |
| Keystone | can it edit what constrains it | Kyverno `ValidatingPolicy`: no |

Northstar differs from that demo in two places. SPIRE, not Linkerd, issues identity
(Linkerd here is reach only, and its certificate answers a different question than the
SVID the gateway verifies). And capability is a decision service rather than a static
CEL allowlist: "hide" versus "refuse and record" are two layers, not a disagreement.

### The keystone policy

`deploy/platform/kyverno/keystone-policy.yaml`, a `policies.kyverno.io/v1`
`ValidatingPolicy` (CEL). Kyverno deprecates the legacy `kyverno.io` `ClusterPolicy`;
this uses the current type.

- **Who is denied:** any request whose `request.userInfo.username` starts with
  `system:serviceaccount:northstar-agents:` or `system:serviceaccount:northstar-tools:`.
  A prefix, not a list, so a new agent is covered without editing the policy. Humans,
  CI and `backstage-autonomy-writer` (in `northstar-platform`) do not match. The match
  is a `matchCondition`, which Kyverno copies into the webhook configuration, so the
  API server filters before calling Kyverno.
- **What is protected** (CREATE, UPDATE, DELETE; `serviceaccounts/token` is CREATE
  only): every ring's configuration plus the routes to a different identity.
  - Capability: `FeatureFlag` and `FeatureFlagSource` in `northstar-platform`; the
    `agentgateway-config` ConfigMap.
  - Reach: `NetworkPolicy`, every `policy.linkerd.io` resource, Gateway API
    `HTTPRoute`, and the namespaces (the mesh annotation lives there).
  - Identity: SPIRE's `ClusterSPIFFEID`, `ClusterStaticEntry` and
    `ClusterFederatedTrustDomain`.
  - RBAC: `Role`, `RoleBinding`, `ClusterRole`, `ClusterRoleBinding`.
  - Kyverno itself: every kind in the `policies.kyverno.io` and `kyverno.io` groups,
    because mutating, generating and namespaced policies, legacy `ClusterPolicy` and
    either `PolicyException` are each a way to bend or bypass this one.
  - Workloads anywhere: `Pod`, `Deployment`, `ReplicaSet`, `StatefulSet`, `DaemonSet`,
    `Job`, `CronJob`. A pod created with another workload's `serviceAccountName` is
    issued that identity's SVID, so creating one is editing your own identity with no
    RBAC object touched.
  - `serviceaccounts/token`: minting a token for another ServiceAccount.

  Controllers (`system:serviceaccount:kube-system:*`), humans and CI are never matched.
  No agent creates a workload or holds any RBAC to; no RoleBinding names an agent
  ServiceAccount and no agent imports a Kubernetes client. Each protected object has a
  live test in `tests/e2e/test_keystone.py`.
- **What Kyverno cannot cover, so it is RBAC-only:** `ValidatingWebhookConfiguration`
  and `MutatingWebhookConfiguration` (the API server never sends admission for webhook
  configurations to webhooks, so an agent able to write one could remove Kyverno's
  registration; agents hold no such grant); anything else RBAC happens to grant an
  agent; the policy service's execute limits, which are in code and change only by a
  reviewed commit and a rebuild; and writes in `kube-system` and `kyverno`, which
  Kyverno's default webhook `namespaceSelector` excludes.
- **Fail closed, with an exception.** `failurePolicy: Fail`. Measured by taking Kyverno
  away two ways:

  | Kyverno | Backstage's writer | Over-granted agent | Humans |
  |---|---|---|---|
  | **Unreachable, webhook still registered** | allowed | **refused** (`failed calling webhook ... connect: connection refused`) | allowed |
  | **Stopped gracefully** (`scale --replicas=0`) | allowed | **ALLOWED** (fail-open) | allowed |

  Backstage's flip is unaffected by a Kyverno outage because the identity condition is
  evaluated by the API server before the webhook is called. The caveat is the second
  row: a graceful stop makes Kyverno delete its own webhook configurations, so the
  keystone silently stops enforcing while the policy object still exists. The
  guarantee is "fail closed while Kyverno is registered", not "whenever Kyverno is
  down". `scripts/preflight.sh` and the gate check the webhook registration and the
  endpoints, not only the policy's `Ready`.
- **Message,** shown live: `DENIED: agent identity '<username>' may not change the
  objects that define its own authority (<kind>/<name>). Authority changes go through a
  reviewed commit, never a runtime decision by the agent.` The API server prefixes it
  with the webhook name and `Policy agents-cannot-edit-their-own-authority failed:`,
  which is what the tests assert on so an RBAC `Forbidden` cannot pass for it.

**Two lines, not one.** RBAC already denies every agent ServiceAccount a write to the
flag, so on a correct cluster an agent sees an RBAC `Forbidden`: the first line. The
keystone is the line that holds when RBAC is wrong. Northstar ships no standing
over-grant; `scripts/demo keystone` applies `deploy/demo/keystone-overgrant.yaml`,
shows Kyverno refuse the same patch, and deletes it. `setup-kind.sh` never applies that
file (pinned by a unit test), and `scripts/preflight.sh` refuses if it is left behind.

**Readiness.** The policy only reports Ready once Kyverno can read every kind it
matches, including the `serviceaccounts/token` subresource and `*` over Kyverno's own
groups. `deploy/platform/kyverno/values.yaml` grants the admission and reports
controllers read-only access to them, and `setup-kind.sh` waits on `ready` rather than
on the object existing.

### Which refusals the trace can see

Kyverno exports OTLP traces to the collector's gRPC receiver (`4317`).

| Question | Finding |
|---|---|
| Does a denial produce a span in Jaeger? | Yes, service `kyverno-admission-controller`: one small trace per denial (`POST` → `ADMISSION POST /vpol/<policy>` → `VALIDATE ...`), sub-millisecond. |
| Attributes on the denial span | `admission.response.allowed = False`, `admission.response.result.message` (carries the policy name), `admission.request.user.name`, `admission.request.operation`, `admission.request.kind.*`, `admission.request.name`, `admission.request.namespace`, `admission.request.roles` (the over-grant is visible). The policy name is not an attribute of its own. |
| Is anything sampled? | No. Kyverno builds the tracer provider with no sampler, so the SDK default applies and a root is always sampled. |
| Is it part of the claim's trace? | No, a standalone trace: `kubectl` sends no `traceparent` and the API server does not propagate trace context to webhooks. |
| Noise | Kyverno traces its own webhook self-checks several times a minute. Search by operation `VALIDATE ...` or by `admission.request.user.name`. |

So: the gateway's decision is in the claim's trace; Kyverno's refusal is in Jaeger,
attributable but standalone; Linkerd's refusals are a counted 403
(`inbound_http_authz_deny_total`); an egress drop is silent, because NetworkPolicy
counts nothing here. `tests/e2e/test_keystone.py::test_the_denial_reaches_jaeger_as_a_standalone_kyverno_trace`
fails if the trace ever stops being standalone.

## Two ways to say no: the gateway's ceiling and the policy service

The gateway can refuse a payment by itself. One CEL rule, an `authorization` **allow**
rule on the route in `deploy/platform/agentgateway/config.yaml`, caps `execute_payment`
at a fixed ceiling (read it from that file) for every caller and refuses a zero or
negative amount. It is off the live demo (`scripts/demo ceiling`).

| | agentgateway CEL ceiling | Policy Decision Service |
|---|---|---|
| Code to write | none: one line of config | a service |
| Sees tool arguments | yes, route level, v1.6.0 and later only | yes |
| Changes live | config reload, about 0.36 s | flag flip, nothing reloads |
| Says why | `403 text/plain "authorization failed"` and nothing else | rule, limit, a reason the model can explain |
| In the trace | one gateway span, `POST /*`, `error.type=Authorization` | a `northstar.policy.decision` span with rule and limit |

Properties of the rule, each guarded in `tests/e2e/test_gateway_ceiling.py`:

- **It is an allow rule, on purpose.** A `deny:` rule fails open on a value it cannot
  evaluate (a string amount `"60000"` went through and executed); an allow rule that
  errors denies.
- **It must let everything else through.** Two guards open the rule:
  `request.method != "POST"` lets the MCP client's own `GET` and `DELETE` on `/mcp`
  through, and `mcp.methodName != "tools/call"` keeps `initialize` and `tools/list`
  working for every caller.
- **It only works on v1.6.0 and later.** On v1.5.0 the route policy has no MCP context
  and the same rule denies everything.
- **Its 403 leaves no `northstar.policy.decision` span**, because the policy service is
  never asked. The refusal is visible but is one generic span with no rule and no limit.
- **A JSON-RPC batch is still refused**: a batch (an array) is a `POST` with no
  `mcp.methodName`, so the allow rule errors and denies.
- **A claim above the ceiling reaches no human.** The ceiling answers `execute_payment`
  with a `403`, not a "no" tool result, so the agent's tool client fails the call: the
  claim's task ends `failed` and the payment stays `proposed` (never
  `awaiting_approval`, so it is not in the reviewer's queue). By design nobody, a
  human included, may release it through this gateway: reviewer-ui's release is the
  same call on the same route. Pinned by
  `test_a_claim_over_the_ceiling_fails_and_nobody_is_asked`.

## What the system deliberately does not do

**What is authorized.**

- **agentgateway does authorization as well as authentication.** `config.yaml` carries
  `jwtAuth` and `mcpGuardrails` as siblings on the same route: the first establishes who
  is calling, the second what that caller may do. Of the two mechanisms examined, the
  external `ExtMCP` gRPC hook is used; the native CEL `mcpAuthorization` is absent,
  because the rule needs an amount comparison, an identity table and a ledger read
  rather than an expression.
- **The decision is made where the agent cannot reach around.** The policy service is
  not reachable from `northstar-agents`, the tool is not reachable except through the
  gateway, and the agent contains no limit to disable. Removing the governance means
  changing the platform, not the agent.
- **The human's override is authorized like everything else.** The approved execution
  goes through the gateway with reviewer-ui's own SVID and is decided by the same
  service under `human-approved.execute`, and only when the ledger shows the payment
  `awaiting_approval`. reviewer-ui has no limit row and no autonomy level, and any
  other identity is `unknown_caller`.

**What is not authorized, and none of it is an oversight.**

- **Only `tools/call` is decided.** Other MCP methods carry no tool and no amount.
- **Hops 1, 2 and 4 carry no identity and no policy.** Hop 2's allow-list checks a
  target *name*; customer-ui reaches claims-agent with no credential. Linkerd restricts
  who can reach settlement-agent and payments-api by identity, but what reaches them
  presents no application-layer credential.
- **payments-mcp performs no check of its own** and sees no identity, because the
  gateway strips the credential on the outbound leg.
- **payments-api has no authorization.** Its OpenAPI contract says so: anything that
  can reach it can move money, as long as it states the right amount. The execute
  handler's amount and currency confirmation is a check that an instruction agrees with
  itself, not a decision about whether the caller may pay. The reach policy is what
  limits who can call `execute`.
- **Approval is one human, one click, once.** No second approver, no quorum, no expiry
  on a pending approval, no re-proposal path back to the agent, and **no authenticated
  human**. A reject is final and recorded, but the reviewer id is a string the form
  supplies and `northstar.review.reviewer.spiffe_id` is reviewer-ui's *own* SPIFFE ID,
  so the span records an unverified claim and the deployment that accepted it, never
  which person approved. There is no login and no session.
- **Nothing in the policy knows a human approved anything.** The policy service sees a
  caller in `HUMAN_APPROVERS` and a ledger status of `awaiting_approval`. The platform
  enforces who may pay how much and from which ledger state; it does not establish who
  the human was.
- **The span link between the two traces is not a guarantee.** It survives because the
  ledger persists both the trace id and the span id of the refusal; nothing reconciles
  the traces if the ledger record is lost (the store is in memory).
- **The `_latch` does not cover the human-release path**; see the limitation in
  [`telemetry-contract.md`](telemetry-contract.md).
- **Backstage is unauthenticated.** The guest login is a real round trip but asks
  nothing of the caller. Anyone who can reach port 7007 can open the catalog and flip
  the autonomy contract. That is acceptable for a laptop cluster whose host ports
  listen on `127.0.0.1` only (no Ingress, no LoadBalancer) and nowhere else. What keeps
  it honest is the narrowness of the one write: `backstage-autonomy-writer` holds
  `get`/`list`/`patch` on `resourceNames: [northstar-autonomy]` only, separate from the
  read-only ServiceAccount the Kubernetes plugin uses. A sharper design, not built, is
  a separate Deployment fronting the write, so a Backstage compromise yields the
  ability to *ask* rather than a credential to *keep*.
- **No scaffolder is demonstrated.** The platform API is a real object with a real UI,
  but nothing here generates a new agent from it.
- **The catalog is static.** It reflects the `catalog-info.yaml` files committed to the
  repo and staged into the image at build time, not a discovered repository.
  `tests/unit/test_catalog_matches_the_tree.py` catches a component added without one
  (see [`upstream-findings.md`](upstream-findings.md) Finding 15 for why a git-less
  location does not work with `$text`).

## Trace propagation, hop by hop

| Boundary | Mechanism | `traceparent`/`tracestate` | `baggage` (`northstar.claim.id`) |
|---|---|---|---|
| browser → claims-agent | HTTP header, injected by hand (`telemetry.ts`'s `injectContext`) | Yes | N/A, not minted yet |
| claims-agent (inbound) | Starlette instrumentation extracts it. **`a2a-sdk` itself extracts nothing**: an injected `traceparent` is ignored by the SDK, so the instrumentation on the built app is the fix | Yes | Not yet: the claim id is minted inside the executor, after extraction |
| claims-agent → settlement-agent | `HTTPXClientInstrumentor` on the plain `httpx.AsyncClient` `delegation.py` uses | Yes | Yes, for spans created after intake |
| settlement-agent (inbound) | Same Starlette instrumentation | Yes | Yes, extracted at the ASGI boundary before internal tasks spawn |
| settlement-agent → agentgateway (Hop 3a) | Two independent carriers, both present, neither redundant: the `traceparent` header the httpx instrumentation injects (what the gateway reads), and `params._meta` hand-built in `mcp_tools.py` (SEP-414; for FastMCP's benefit downstream). `mcp_tools.py` builds the request itself so `_meta` is set explicitly; see Gap 6 in [`telemetry-contract.md`](telemetry-contract.md) | Yes, twice | Yes in `_meta` |
| agentgateway (inbound) | The HTTP header only. `TraceParent::from_request` reads `traceparent` and never inspects the body; with no header it would mint a new root | Yes (header) | Not consumed: the gateway has no baggage concept and its spans carry no `northstar.claim.id` (asserted as a negative in `test_gateway_trace.py`) |
| agentgateway → policy service (Hop 3p) | The `traceparent` header on the gRPC `CheckRequest`, forwarded to the guardrail hook and extracted by the service from the ExtMCP `headers` field. The forwarded context is the one the gateway *received*, which is why the decision span is a sibling of `Guardrail` | Yes | No: baggage is not forwarded |
| agentgateway → payments-mcp (Hop 3b) | The gateway starts its own outbound CLIENT span, injects it into the outbound headers, and copies the W3C keys into `params._meta`. So `_meta` names the *gateway's* span, and payments-mcp parents under the gateway | Yes (re-stamped) | The `baggage` key is copied if present |
| payments-mcp (inbound) | FastMCP's `extract_trace_context()` reads `traceparent`/`tracestate` from `params._meta` | Yes | **No**: FastMCP never reads `baggage` out of `_meta`. This is where `northstar.claim.id` stops |
| payments-mcp → payments-api | `HTTPXClientInstrumentor` | Yes | No |
| payments-api (inbound) | `otelhttp`, standard W3C extraction | Yes | No |
| browser → reviewer-ui | Nothing: a plain HTML form POST with no JavaScript. So `northstar.review.decision` is a trace root | No | No |
| reviewer-ui → payments-api | `HTTPXClientInstrumentor`, inside reviewer-ui's own spans (`northstar.review.queue`, `northstar.review.decision`) so the CLIENT span has a parent to inject | Yes | No |
| reviewer-ui → agentgateway (release) | Same as Hop 3a | Yes | The claim id is set as baggage by reviewer-ui from the ledger record |
| reviewer-ui → claims-agent (resume) | `HTTPXClientInstrumentor` on the A2A `message/send`. The join back to trace A is a span link, built from ids the ledger persisted (`refusal.refusedTraceId`, `refusedSpanId`) | Yes | Yes |

**Trace A and trace B.** A refused claim and its approval are two traces, joined by
span links rather than a parent-child edge, because the human decides minutes later in
a different process and holding a span open across that wait would misstate both the
duration and what was executing:

<pre class="mermaid">
flowchart LR
    subgraph A["trace A — refused"]
        a1["browser → ... → settlement-agent"] --> a2["deny"]
    end
    subgraph B["trace B — approved"]
        b1["northstar.review.decision"] --> b2["execute_payment via the gateway"] --> b3["northstar.a2a.resume"] --> b4["push to customer-ui"]
    end
    b1 -.->|"span link"| a1
    b3 -.->|"span link"| a1
</pre>

Trace A ends at the hand-off and does not grow during the wait. Trace B's root is
`northstar.review.decision` in reviewer-ui; under it are the ledger read, the MCP calls
through the gateway, the policy service's `northstar.policy.decision` (rule
`human-approved.execute`, `northstar.policy.ledger.status = awaiting_approval`),
payments-mcp and payments-api's execute, and claims-agent's resume. It carries one span
link to the refusal span in trace A and the claim id as both attribute and baggage;
the resume span carries one span link to the hand-off span. A reader looking for one
trace that contains both the deny and the approved execution will not find it, and
should not.

**How long humans take is a metric.** reviewer-ui records the histogram
`northstar.approval.wait.duration` (seconds; attributes
`northstar.review.decision` and `northstar.policy.autonomy.level`, never the reviewer)
at each decision that took effect, measured from the ledger's `awaitingSince`. It is
not sampled and reaches Prometheus as `northstar_approval_wait_duration_seconds_*`.

**`northstar.claim.id` is set once**, at claims-agent's intake (`correlation.py`'s
`set_claim_id`), and rides in W3C baggage from there. Each of the three Python services
runs the upstream `BaggageSpanProcessor` with a predicate admitting `northstar.claim.id`
and no other baggage key; it copies the value onto a span only if it is already baggage
in the ambient context when the span starts. Coverage is therefore a property of the
mechanism, not of a capture: customer-ui's root and claims-agent's pre-intake spans
predate the id (the SDK spawns the executor in a separate asyncio task before anything
is attached to the ambient context); payments-mcp and payments-api never receive it
(FastMCP gap above); the gateway and policy service carry none, which is the intended
enrichment boundary. Those spans are correlated by trace id.
[`telemetry-contract.md`](telemetry-contract.md) has the per-exemption reasoning.

**Our services create few spans of their own.** Every other span comes from library
instrumentation wrapping our code from the outside. The exceptions are the output of a
command, not a retyped list, because a "verified by grep" claim that a grep falsifies is
the worst form of stale prose:

```bash
grep -rnE '(start_as_current_span|startActiveSpan)\(' \
  agents apps platform systems tools \
  --include='*.py' --include='*.ts' --include='*.tsx' \
  | grep -vE '/\.venv/|/node_modules/|/tests/|/test_|\.test\.'
```

The trailing `\(` matters: without it the grep also returns prose mentions in comments.
The call sites cover the browser's `northstar.claim.submit` trace root, reviewer-ui's
`northstar.review.queue` and `northstar.review.decision`, the policy service's
`northstar.policy.decision` (named through `telemetry.SPAN_NAME`, so a grep for the
literal string misses it), and the spans the registry in `telemetry/registry/` defines.

## See it yourself

| Surface | URL |
|---|---|
| Customer UI | http://localhost:8080 |
| Reviewer UI | http://localhost:8081 |
| Jaeger | http://localhost:16686 |
| Prometheus | http://localhost:9090 |
| AgentEvals | http://localhost:18001 |
| Backstage | http://localhost:7007 |

Each is a kind host port onto a NodePort Service, mapped when the cluster is created
(`deploy/kind/cluster.yaml`, listening on `127.0.0.1` only), so none needs a
`kubectl port-forward` and all survive a redeploy. `scripts/setup-kind.sh` ends by
checking each answers, and `./scripts/preflight.sh` repeats that check.
`tests/unit/test_cluster_config.py` keeps the kind config, the NodePort Services and
the preflight's list in agreement. The operator's script is
[`demo-runbook.md`](demo-runbook.md); what follows is what to look for in the traces.

1. **Submit a claim above the agent's limit.** In the Customer UI the form is
   pre-filled with `CLM-1842`, €6,200. Submit; the page shows "Awaiting human approval"
   with a timer. The payment was proposed and then refused by the platform and
   recorded as `awaiting_approval`; the model explains, in its own words (different
   every run), that the settlement could not be paid. A claim inside the band (`5800`
   at `autonomous`) comes back `executed`. Seeing both is the point: a platform that
   refused everything would look identical on the first run alone.
2. **In Jaeger,** search service `customer-ui`, operation `northstar.claim.submit`. The
   single result is your submission (read the response's `data`, not `total`, which is
   often `0`). Or search by tag: service `settlement-agent` or `claims-agent`, tag
   `northstar.claim.id=CLM-1842`. The same tag search against `payments-mcp` or
   `payments-api` returns nothing, by design, even though those spans are in the same
   trace by trace id. Ignore `harness-probe*` services: they are the e2e suite's
   synthetic trace ids.
3. **Spans worth opening:**
   - `customer-ui`'s `northstar.claim.submit`: the trace root, no parent; its attributes
     are set directly in `page.tsx`.
   - `claims-agent`'s outbound `POST` to settlement-agent: no code in `delegation.py`
     injects a header by hand; the instrumentation does it because the ambient context
     is correct at call time.
   - `payments-mcp`'s `tools/call propose_payment`: `gen_ai.tool.name` is present,
     `gen_ai.operation.name` is not (a gap in the contract), and `northstar.claim.id` is
     absent. In an over-limit run there is no span for `execute_payment`: the gateway
     refused it at the policy hook, and its absence is itself the evidence.
   - agentgateway's `tools/call` SERVER span: `jwt.sub` is the caller's attested SPIFFE
     ID, set by the gateway from the token it verified; `gen_ai.tool.name` is where the
     tool name lives; the HTTP attributes use the stable semantic conventions. Its child
     CLIENT span, `tools/call payments-mcp_<tool>`, has no `jwt.sub`: the credential was
     stripped.
   - `policy-decision-service`'s `northstar.policy.decision`: on the over-limit
     `execute_payment`, `northstar.policy.decision = deny`, `northstar.policy.rule =
     settlement-agent.assisted.execute_limit`, `northstar.policy.request.amount`,
     `northstar.policy.limit.amount`, and `northstar.policy.caller.spiffe_id`. Status is
     not ERROR. Compare the `propose_payment` decision in the same trace: a `permit`
     under `unlimited_tool` with no limit attribute.
   - agentgateway's `Guardrail` CLIENT span: `grpc.status = 0` says the *call* succeeded;
     the deny is in the payload, which is how "the policy said no" stays distinct from
     "the policy service was down".
   - `payments-api`'s `POST /payments/{paymentId}/execute`: the stable HTTP spelling,
     and the one place real money state changes. Comparing its attribute names with the
     gateway's, in the same trace, is the fastest way to see the naming divergence.
4. **Approve the waiting payment.** Open the Reviewer UI. The page lists payments
   awaiting approval with the rule that fired, the limit, the amount and the model's
   narrative, read back out of the ledger rather than out of the trace (turn
   `NORTHSTAR_CAPTURE_CONTENT` off and the page is unchanged). Type any reviewer id (the
   field is required but unverified) and click **Approve**; the customer tab flips to
   "Settled" without a reload. Then in Jaeger, search service `reviewer-ui`, operation
   `northstar.review.decision`. That span is a trace root (reviewer-ui does not
   instrument FastAPI, and the browser sends a plain form). Its attributes include
   `northstar.review.payment.id`, `northstar.review.decision`,
   `northstar.review.reviewer.id`, `northstar.review.reviewer.spiffe_id` and
   `northstar.review.wait.duration`. Its span link opens the original refused trace.
   The `northstar.policy.decision` span in this trace reads rule `human-approved.execute`
   and `permit`: nothing was overridden; a different caller was asked about, and the
   ledger said `awaiting_approval`.
5. **See the refusals without spending a model call:**

   ```bash
   .venv/bin/python -m pytest tests/e2e/test_caller_identity.py -v
   .venv/bin/python -m pytest tests/e2e/test_policy_denial.py -v -k "criterion_4 or criterion_5"
   ```

   The first mints real JWT-SVIDs and asserts both that a valid one executes a tool end
   to end and that invalid ones are refused. Criterion 4 is a hermetic AST walk proving
   no policy logic lives in settlement-agent or payments-mcp; criterion 5 speaks MCP to
   the gateway directly with two different SPIRE-minted identities and shows the verdict
   changing with the caller. The remaining tests in those files drive a real browser and
   model and sit behind `RUN_BROWSER_TESTS=1`.
6. **Prove why the money moved, from telemetry alone.** `scripts/demo evidence` reads
   Jaeger and prints, per claim, who decided, under which rule and limit, who approved,
   and how long the human took, with a link to the trace. Every field is an attribute on
   a span the platform already emits; none comes from the model. The join is claim id
   first, then trace id, because `northstar.claim.id` stops at the MCP boundary and never
   reaches the policy service. `scripts/demo evals` runs the AgentEvals checks.
7. **Backstage.** Open http://localhost:7007 and click **Enter** under Guest (there is no
   username or password to type; that absence is itself worth noticing). **Catalog**
   lists one `Component` per directory with a `catalog-info.yaml`, plus Backstage itself.
   **Autonomy contract** (a standalone page at `/autonomy-contract`) reads the live
   `FeatureFlag` per identity and can flip it. Flip it to `autonomous` and resubmit the
   same claim: it is now `executed`, through the same choke point, with nothing
   redeployed. Flip it back; the demo's resting state is refusing. In Jaeger, service
   `backstage`, operation `northstar.autonomy_contract.change` is one span per flip made
   through the card (not through `kubectl`, which never reaches this code path); it
   survives the flag being reverted and the pod being recycled, which the object's own
   `managedFields` does not.
