# Northstar Architecture — M1.1 (Connected)

Status: M1.1, revised through M1.2 for the settlement-agent's model-driven
decision logic. This is the walking-skeleton stage: one claim, five
components, one trace, no governance — M1.2 adds a real model call inside
that skeleton without changing its shape.

**Provenance (fixed by the M1.2 final-review fix round, SF-6):** this
document previously claimed every claim in it was checked against HEAD
`0a1a1f7` — a commit that is an ancestor of the M1.1 merge point
(`f104109`), predating every line of M1.2 behaviour this document describes
(the PydanticAI agent, the ledger/model split, Task 8's CLIENT span). That
was false for every M1.2-era claim below. The truth, from `git log --
docs/architecture.md`: this file's M1.1-era content was checked against
`0a1a1f7`; its M1.2 content was added and last checked against the code and
the live `kind-northstar` cluster as of commit `85882e9` ("instrument httpx2
so the model call gets its CLIENT span" — Task 8, the last commit to touch
this file). This fix round corrected the provenance claim itself; it did not
re-run a fresh verification pass over every claim in this document against
the current worktree HEAD (`8e1f030`) — see "See it yourself" for how to
reproduce each capture yourself if you want to check further.

This document does not duplicate [`docs/telemetry-contract.md`](telemetry-contract.md),
which is the authority on exactly what instrumentation is enabled, what it
emits, and the convention gaps found in it. This document is about the
system's shape: who talks to whom, over what protocol, and what a reader
follows through Jaeger to see it. Where the two overlap (coverage numbers,
span names), the numbers here were re-measured against a fresh live trace,
not copied — they agree, and any future disagreement between this file and
the contract should be treated as a bug in one of them, not resolved by
picking one silently.

## The chain, in one picture

```text
┌──────────┐  A2A/JSON-RPC   ┌─────────────┐  A2A/JSON-RPC   ┌──────────────────┐
│ browser  │────over HTTP───▶│ claims-agent│────over HTTP───▶│ settlement-agent │
│  (TS,    │  message/send   │  (Python,   │  message/send   │  (Python,        │
│ manual   │◀────────────────│  a2a-sdk)   │◀────────────────│   a2a-sdk)       │
│ root     │  JSON-RPC reply │             │  JSON-RPC reply │                  │
│ span)    │                 └─────────────┘                 └────────┬─────────┘
└──────────┘                                                          │
                                                          MCP over streamable
                                                          HTTP (tools/call,
                                                          params._meta carries
                                                          trace context)
                                                                       │
                                                                       ▼
                                                          ┌──────────────────┐
                                                          │   payments-mcp   │
                                                          │ (Python, FastMCP)│
                                                          └────────┬─────────┘
                                                                   │
                                                     plain HTTP/JSON (otelhttp
                                                     client ↔ otelhttp server)
                                                                   │
                                                                   ▼
                                                          ┌──────────────────┐
                                                          │   payments-api   │
                                                          │  (Go, otelhttp)  │
                                                          │ system of record │
                                                          └──────────────────┘
```

This agrees with `README.md`'s one-line version; this diagram just names the
transport and the payload shape at each arrow, which the README's version
doesn't attempt.

## The five components

Namespaces, ports, and service DNS names below are read directly from
`deploy/k8s/*.yaml`, not from memory or from the diagram above.

| Component | Language / framework | Namespace | Service (DNS) : port | Image | Role |
|---|---|---|---|---|---|
| `customer-ui` | TypeScript, Next.js 15.5.21 (`output: "export"`, static, served by nginx 1.27) | `northstar-agents` (**not** its own namespace — see note below) | `customer-ui.northstar-agents.svc.cluster.local:8080`, exposed as a `NodePort` (30080) mapped to host `8080` by kind's `extraPortMappings` | `localhost:5001/northstar/customer-ui:dev` | Owns the trace root. The only component a human touches directly. |
| `claims-agent` | Python 3.13, `a2a-sdk[telemetry]==0.3.7` + Starlette | `northstar-agents` | `claims-agent.northstar-agents.svc.cluster.local:9001` | `localhost:5001/northstar/claims-agent:dev` | A2A server (inbound, from the browser) and A2A client (outbound, to settlement-agent). Parses the claim, delegates, returns settlement-agent's JSON verbatim. |
| `settlement-agent` | Python 3.13, `a2a-sdk[telemetry]==0.3.7` + Starlette + `mcp==1.9.4` client + `pydantic-ai-slim==2.41.0` (M1.2) | `northstar-agents` | `settlement-agent.northstar-agents.svc.cluster.local:9002` | `localhost:5001/northstar/settlement-agent:dev` | A2A server; MCP client. As of M1.2, the settlement decision itself is a real model call (see "The decision logic" below) — the model chooses whether and how to propose and execute the payment via payments-mcp. |
| `payments-mcp` | Python 3.13, FastMCP 4.0.3 | `northstar-tools` | `payments-mcp.northstar-tools.svc.cluster.local:8000`, MCP endpoint at `/mcp` | `localhost:5001/northstar/payments-mcp:dev` | MCP server exposing two tools. An adapter over payments-api — it owns no state of its own (`server.py`'s own docstring: "This is an adapter over payments-api, not a system of record."). |
| `payments-api` | Go 1.24, `otelhttp v0.66.0` / otel core `v1.41.0` | `northstar-systems` | `payments-api.northstar-systems.svc.cluster.local:8080` | `localhost:5001/northstar/payments-api:dev` | The system of record for payments. In-memory store, sequential IDs, no persistence. |

**Namespace note, worth stating plainly:** `customer-ui` deploys into
`northstar-agents` — the same namespace as `claims-agent` and
`settlement-agent` — not a namespace of its own (confirmed in
`deploy/k8s/customer-ui.yaml`, `deploy/k8s/namespaces.yaml`; only four
namespaces exist: `observability`, `northstar-agents`, `northstar-tools`,
`northstar-systems`). A reader expecting one namespace per component (the
plane labels — `northstar.io/plane: agents/tools/systems` — encourage that
reading) would be wrong about `customer-ui` specifically; it shares the
agents' namespace even though it isn't itself an agent.

What each component is explicitly **not** responsible for:

- **customer-ui** does not talk to anything but claims-agent and the OTel
  Collector. It has no server-side logic beyond a static export served by
  nginx — no API routes, no session state, no auth.
- **claims-agent** does not assess or settle anything itself. It parses the
  claim (`intake.py`, deliberately strict JSON validation, no LLM), and
  delegates unconditionally to settlement-agent. It does not decide whether
  the delegation should happen — the allow-list below governs the target,
  not the decision.
- **settlement-agent** does not talk to payments-api directly and does not
  own payment state. As of M1.2 it *does* reason about the claim — a real
  model call decides whether and how to settle it — but the reply's amounts
  still come from what payments-api's ledger recorded, never from the
  model's own words (see "The decision logic" below).
- **payments-mcp** does not own payment state. It has no logic of its own
  beyond translating an MCP tool call into an HTTP call to payments-api and
  returning the result (`payments_client.py`).
- **payments-api** does not know anything about A2A, MCP, agents, or claims
  beyond the `claimId` string it is handed. It has no authorization of any
  kind — see "What the system deliberately does not do" below; this is
  stated in its own OpenAPI contract (`contracts/openapi/payments-api.yaml`):
  *"Deliberately tiny. In M1 it has no authorization of any kind: anything
  that can reach it can move money."*

## Hop 1 — browser → claims-agent: A2A over JSON-RPC 2.0

The browser is an A2A client. `apps/customer-ui/src/a2a.ts` builds a
JSON-RPC 2.0 `message/send` envelope by hand (it deliberately does not use
`@a2a-js/sdk` — see `apps/customer-ui/NOTES.md` §1: the SDK's current line
targets A2A protocol `1.0`, while claims-agent's agent card advertises
`protocolVersion: "0.3.0"`, and reconciling that skew for a demo UI wasn't
judged worth it) and POSTs it to `claims-agent`'s `/` endpoint.

The form (`apps/customer-ui/src/app/page.tsx`) pre-fills:

```json
{
  "claimId": "CLM-1842",
  "policyNumber": "POL-77120",
  "description": "Rear-end collision at a junction",
  "estimatedAmount": 6200
}
```

**Real request, captured from a live Playwright-driven submission against
the deployed pod** (`POST http://localhost:19001/`, i.e. the port-forwarded
claims-agent):

Headers of interest: `content-type: application/json`,
`traceparent: 00-ddf2747a385885345c0eeb25997d53ba-bd2489e913a5994b-01`
(injected by `injectContext` in `apps/customer-ui/src/telemetry.ts`, which
calls `propagation.inject(context.active(), headers)` — this is also where
`baggage` would ride if any were set at this point in the flow; none is,
since `northstar.claim.id` isn't minted until claims-agent's own intake).

```json
{
  "jsonrpc": "2.0",
  "id": "2b41f8d30d57401583e2039df1639b0d",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "messageId": "0fe94b7d0e5b41629bb32c1983d740d0",
      "parts": [
        { "kind": "text", "text": "{\"claimId\":\"CLM-1842\",\"policyNumber\":\"POL-77120\",\"description\":\"Rear-end collision at a junction\",\"estimatedAmount\":6200}" }
      ]
    }
  }
}
```

Real reply, same capture:

```json
{
  "id": "2b41f8d30d57401583e2039df1639b0d",
  "jsonrpc": "2.0",
  "result": {
    "kind": "message",
    "messageId": "373ae387-28cd-4e4b-b9c2-f52634547f0f",
    "parts": [
      { "kind": "text", "text": "{\"claimId\": \"CLM-1842\", \"policyNumber\": \"POL-77120\", \"paymentId\": \"PAY-000061\", \"amount\": 6200, \"currency\": \"EUR\", \"status\": \"executed\", \"narrative\": \"Claim CLM-1842 is covered. Settling at 6200 EUR against the assessed damage.\"}" }
    ],
    "role": "agent"
  }
}
```

The claim's `parts[0].text` is itself a JSON string (A2A wraps arbitrary
text; the claim payload happens to be JSON) — claims-agent's `intake.py`
parses that inner string, not the outer envelope, as the claim.

CORS: claims-agent's `CORSMiddleware` (`main.py`) allows origin
`http://localhost:8080` (from `CORS_ALLOWED_ORIGINS`) and explicitly allows
the `traceparent`, `tracestate`, and `baggage` headers — without that
allow-list entry the browser's preflight would strip them and the root span
would detach from everything downstream (`apps/customer-ui/NOTES.md` §4).
Separately, the browser's own OTLP export (the `claim.submit` span itself,
not the claims-agent call) goes to the OTel Collector at
`http://localhost:4318/v1/traces`, which needs its own, independent CORS
entry for `http://localhost:8080` (`deploy/observability/collector-config.yaml`)
— confirmed present.

## Hop 2 — claims-agent → settlement-agent: A2A again, behind an allow-list

claims-agent delegates by building the identical kind of JSON-RPC
`message/send` envelope (`delegation.py`) and POSTing it to whichever URL
`ClaimsAgentExecutor._delegate(name)` resolves. That resolution is gated by
an explicit, hardcoded allow-list, not by discovery:

```python
# delegation.py
ALLOWED_AGENTS = allowed_agents_from_env()   # NORTHSTAR_ALLOWED_AGENTS env var

# executor.py
def _delegate(self, name: str) -> A2ADelegate:
    try:
        return self._delegates[name]
    except KeyError:
        raise RuntimeError(
            f"claims-agent is not permitted to delegate to {name!r}; "
            f"permitted: {sorted(self._delegates)}"
        ) from None
```

In the deployed manifest, `NORTHSTAR_ALLOWED_AGENTS` has exactly one entry:
`settlement-agent=http://settlement-agent.northstar-agents.svc.cluster.local:9002`
(`deploy/k8s/claims-agent.yaml`). `delegation.py`'s own docstring states the
intent precisely: *"Which agents may be delegated to is configuration, never
discovery. In M1 that configuration is an environment variable; in M2 these
become agentgateway route names enforced at the gateway; in M6 Crossplane
generates them from the Agent CRD's `delegation.allowedAgents` field."*

**What this mechanism does protect against:** claims-agent's own code (or a
bug, or a name that arrives some other way) calling out to an agent name
that was never configured — the lookup raises before any network call is
attempted, and the error names exactly what's permitted.

**What this mechanism does not protect against — stated plainly, because
overstating it would be exactly the kind of thing this document is held not
to do:**

- It is enforced entirely in-process, in claims-agent's own Python. There is
  no gateway, mTLS, or Kubernetes `NetworkPolicy` anywhere in this cluster
  (`deploy/` has no `NetworkPolicy` resource at all, confirmed by search) —
  nothing stops any other pod in the cluster from calling settlement-agent's
  Service directly, bypassing claims-agent and this check entirely.
  Symmetrically, `mcp_tools.py`'s docstring states "settlement-agent is the
  only agent permitted to reach payments-mcp," but that, too, is a statement
  of intent enforced by nothing but which URL settlement-agent happens to be
  configured with — claims-agent (or anything else in the cluster) could
  reach `payments-mcp.northstar-tools.svc.cluster.local:8000` directly today.
- It checks a *name*, not an identity — there is no cryptographic or
  workload-identity verification that the process on the other end of the
  configured URL actually is settlement-agent. SPIFFE/SPIRE arrives in M2.
- It says nothing about whether the delegated call itself should be allowed.
  Once a name is in the map, the amount, the claim, and the decision to pay
  are not checked against anything — see "What the system deliberately does
  not do" below.

This is the M1 stand-in for what M2's `agentgateway` is meant to enforce at
the network boundary instead of in application code.

## Hop 3 — settlement-agent → payments-mcp: MCP over streamable HTTP

Never stdio. The design spec is explicit about why: *"Transport is
**streamable HTTP** throughout. stdio cannot traverse agentgateway in M2, so
choosing it now would guarantee a rewrite."* (`docs/superpowers/specs/2026-09-07-northstar-m1-connected-design.md`).
`payments-mcp/server.py` runs `mcp.run(transport="http", ...)`; settlement-agent's
`mcp_tools.py` connects via `mcp.client.streamable_http.streamablehttp_client`.

Two tools, exact signatures and annotations
(`contracts/mcp/payments-mcp.json`, matching `tools/payments-mcp/server.py`):

```json
propose_payment(claim_id: string, amount: number, currency: string = "EUR") -> dict
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }

execute_payment(payment_id: string) -> dict
  annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: false }
```

**Why `destructiveHint: true` is load-bearing, not decorative:** the design
spec's own argument is *"Consequence is declared in the protocol... the
Policy Decision Service (M2) reads that annotation rather than pattern-matching
tool names. ... It also means a newly added consequential tool inherits
governance automatically, which is the real test of whether a platform
abstraction works."* In M1.1 nothing reads this annotation yet — no policy
engine exists — so today it is inert metadata sitting in the tool schema.
That is precisely the point being set up: the annotation is already true and
already correct, waiting for M2's Policy Decision Service to key off it
instead of a hardcoded list of "dangerous tool names," which is the weaker
design this project is deliberately avoiding.

## Hop 4 — payments-mcp → payments-api: plain HTTP/JSON

`payments_client.py`'s `httpx.Client` calls payments-api directly; no MCP,
no A2A. Verified live against the deployed payments-api (port-forwarded),
all four endpoints (`systems/payments-api/handlers.go`,
`contracts/openapi/payments-api.yaml`):

| Method & path | Request body | Response | Status codes |
|---|---|---|---|
| `GET /healthz` | — | `{"status":"ok"}` | `200` |
| `POST /payments/propose` | `{"claimId","amount","currency"}` | `{"paymentId","claimId","amount","currency","status":"proposed"}` | `201` (created), `400` (malformed JSON, missing `claimId`, negative `amount`) |
| `POST /payments/{paymentId}/execute` | — | `{"paymentId","claimId","amount","currency","status":"executed"}` or `{"error":"..."}` | `200` (executed), `404` (`{"error":"payment not found"}`, unknown id), `409` (`{"error":"payment already executed"}`, already executed) |
| `GET /payments/{paymentId}` | — | Payment record, or `{"error":"payment not found"}` | `200`, `404` |

Verbatim from a live capture against the deployed pod (port-forward, exact
sequence: propose, execute, execute again, execute-unknown):

```
POST /payments/propose  -> 201  {"paymentId":"PAY-000060","claimId":"CLM-DOC-TEST","amount":100,"currency":"EUR","status":"proposed"}
POST /payments/PAY-000060/execute -> 200  {"paymentId":"PAY-000060",...,"status":"executed"}
POST /payments/PAY-000060/execute -> 409  {"error":"payment already executed"}
POST /payments/PAY-999999/execute -> 404  {"error":"payment not found"}
```

Every route except `/healthz` is wrapped in `otelhttp.NewHandler(...)`
individually so the span name is the route template (e.g.
`POST /payments/{paymentId}/execute`), not the raw path — low-cardinality
span names per HTTP semantic conventions. `/healthz` is deliberately
unwrapped: `handlers.go`'s own comment records that the readiness probe
polling it produced "162 GET /healthz traces against 44 POST
/payments/propose traces in one measured hour" before this fix — the probe
was polluting the trace view, so it was excluded from tracing entirely
rather than filtered downstream.

## The decision logic — M1.2: a real model now decides, the ledger still states the amounts

M1.1's `decide_settlement` was the deterministic stub its own docstring
promised to become: *"In M1.2 the body becomes a PydanticAI agent; this
signature and return shape stay the same so nothing downstream changes."*
That happened at M1.2 Tasks 2–4. `agents/settlement-agent/settlement.py` now
validates the same three fields as before — `claimId` present, `amount`
present, `amount >= 0` — but the settlement decision after that validation
is a real PydanticAI `Agent` call against `anthropic:claude-sonnet-5`, not a
fixed narrative string:

```python
settlement_agent = Agent(
    os.environ.get("NORTHSTAR_SETTLEMENT_MODEL", DEFAULT_MODEL),
    deps_type=SettlementDeps,
    instructions=(
        "You settle motor insurance claims for Northstar Insurance.\n"
        "You are given a claim that has already been assessed as covered.\n"
        "Settle it by calling propose_payment with the assessed amount, then "
        "execute_payment with the paymentId you get back.\n"
        "Then reply with a short narrative for the claimant.\n"
        "Settle the assessed amount. Do not round it, discount it or "
        "renegotiate it. Do not state amounts in your narrative."
    ),
)

@settlement_agent.tool
async def propose_payment(ctx, amount, currency="EUR") -> dict: ...

@settlement_agent.tool
async def execute_payment(ctx, payment_id) -> dict: ...
```

**The model chooses the tool sequence.** `decide_settlement` no longer calls
`propose`/`execute` itself — it runs `settlement_agent.run(prompt, deps=...)`
with one templated sentence (`f"Claim {claim_id} is assessed as covered for
{amount:.0f} {currency}. Settle it."`, built only from `{claimId, amount,
currency}` — the claim's free-text description never reaches the model at
all) and lets the model decide when, whether, and in what order to call
`propose_payment` and `execute_payment`. Both tools still call
`mcp_tools.PaymentsTools` exactly as before — the MCP call path, the
verified `params._meta` trace-context propagation, and payments-mcp's own
error handling are all unchanged from M1.1. The model chooses; the same
M1.1-proven code calls.

**The reply's amounts come from the ledger, never from the model's own
words.** `decide_settlement` reads `deps.record.executed` — the actual
`propose`/`execute` results returned by payments-mcp, captured by the tool
functions themselves as a side effect — and builds the reply's
`amount`/`currency`/`status`/`paymentId` from *that*, not from anything the
model said. Only the reply's `narrative` field is the model's own text
(`result.output`, the agent's final free-form turn). Concretely:

```python
return {
    "amount": executed["amount"],       # from payments-api's ledger
    "currency": executed["currency"],   # from payments-api's ledger
    "status": executed["status"],       # from payments-api's ledger
    "paymentId": executed["paymentId"], # from payments-api's ledger
    "narrative": result.output,         # the model's own prose — nothing else
}
```

This is deliberately testable and tested:
`test_reply_amount_comes_from_the_ledger_not_from_the_model` (Task 2) drives
a scripted model that narrates one amount while the mocked ledger returns
another, and asserts the reply carries the ledger's figure — proving the
separation is real, not incidental. If the model finishes a run without
ever calling `execute_payment`, `decide_settlement` raises a `RuntimeError`
naming the claim and whatever was proposed, rather than silently reporting
success; and any `ToolCallError` from a failed payments-mcp call propagates
uncaught out to the caller as a JSON-RPC error, so a real failure is never
smoothed over into friendly prose.

A reader watching this trace in Jaeger sees the same two real network calls
(propose, execute) M1.1 always showed, plus — new in M1.2 — one
`invoke_agent settlement_agent` span, three `chat claude-sonnet-5` spans
(one model turn each: propose, execute, final narrative), and two
`execute_tool` spans, all nested inside the same request
(`docs/telemetry-contract.md`'s "PydanticAI instrumentation, as measured"
section has the full attribute inventory). Where M1.1's narrative was a
fixed string and this document had to say so plainly, M1.2's narrative is
genuinely the model's own words about a decision the model itself made —
the trajectory a reader follows in Jaeger now really did happen the way it
reads, not merely look like it did.

**M1.2 Task 8: each `chat claude-sonnet-5` span now has its own HTTP CLIENT
child.** Tasks 1–7 left the model's own outbound hop untraced below the
`chat` span — `anthropic==1.4.0` speaks `httpx2`, a distribution
`HTTPXClientInstrumentor` does not cover, so the request to
`api.anthropic.com` produced no HTTP status, retry count or wire-latency
detail of its own (`docs/telemetry-contract.md`'s former Gap 7). Task 8
closed this by additionally instrumenting `HTTPX2ClientInstrumentor`
(`bootstrap.py`) — the sibling class already present in the same installed
`opentelemetry-instrumentation-httpx==0.65b0` package. A reader now sees, per
model turn, a `POST` CLIENT span nested directly under that turn's `chat`
span, carrying the same stable HTTP semconv attributes every other outbound
hop in this project carries (`http.request.method`, `http.response.status_code`,
`server.address`, `url.full`) — measured at `POST` / `200` /
`api.anthropic.com` / `https://api.anthropic.com/v1/messages?beta=true`.
Exactly one such span appears per `chat` span, confirmed by diffing a live
capture taken before this change against one taken after
(`tests/e2e/test_settlement_llm_trace.py::test_anthropic_client_span_exists_and_is_parented_to_chat`).

**No amount check, clamp, threshold or guard rail exists anywhere in this
path**, confirmed by reading `settlement.py`, `executor.py`, `mcp_tools.py`,
`tools/payments-mcp/*.py` and `systems/payments-api/*.go` — the only numeric
checks anywhere are `_validate`'s pre-model `amount is None`/`amount < 0`
guards, both inherited unchanged from M1.1 and both about the *inbound*
claim, not the model's proposal. `settlement.py`'s own module docstring
states the intent as plainly as M1.1's did: *"There is no €5,000 check here,
and that is the point: M1 is ungoverned on purpose. The model can propose
any amount and nothing stops it. That hole is what M2's Policy Decision
Service fills."* Three independent live runs (a direct A2A measurement, the
gate's own assertions, and a real browser submission) all settled exactly
the assessed €6,200 — a fact about this model on this prompt, not a property
anything in this chain enforces.

## What the system deliberately does not do

**There is no authorization anywhere in this chain.** Concretely, verified
by reading every hop's code, not inferred:

- claims-agent's delegate allow-list (Hop 2) checks a target *name*, not
  whether the delegation should happen for this claim.
- settlement-agent executes the payment unconditionally — as of M1.2, the
  model itself decides to call `propose_payment` then `execute_payment`,
  with no approval step, no check against a policy, and no human in the
  loop anywhere in that decision. `executor.py`'s own module docstring
  states it plainly, updated for the model now being the one that chooses:
  *"There is no policy check here. That is the point: EUR 6,200 leaves the
  company because a message asked for it, and the model itself chose to
  spend it. M2 puts a policy decision in the path."*
- payments-mcp performs no check of its own before calling payments-api.
- payments-api's own OpenAPI contract states it outright: *"Deliberately
  tiny. In M1 it has no authorization of any kind: anything that can reach
  it can move money."* There is no auth header, no API key, no mTLS anywhere
  in this chain — confirmed by reading every HTTP client construction site
  in the repo (`delegation.py`, `mcp_tools.py`, `payments_client.py`); none
  sets an `Authorization` header or any credential.

Submitting the default form (`CLM-1842`, `€6,200`) moves €6,200 through a
real Go system of record because a JSON-RPC message asked for it. This is
the deliberate argument the rest of the demo builds against, not an
oversight — `README.md`'s own Status line says so, and the code bears it
out exactly.

## Determinism worth knowing: payment IDs

`systems/payments-api/store.go`'s `store` is an in-memory
`map[string]Payment` with a `nextID int` counter starting at `1`, formatted
as `PAY-%06d`. There is no persistence layer. This means:

- IDs are strictly sequential within one process's lifetime.
- **They reset to `PAY-000001` on every pod restart** — a fresh
  `newStore()` on every process start, deliberately, per the code's own
  comment: *"Deterministic sequential IDs matter: the demo's numbers must be
  identical on every run."* A live capture taken for this document (against
  a pod that had already handled many prior test and demo runs since its
  last restart) shows `PAY-000059` through `PAY-000061` — proof the counter
  is genuinely cumulative across calls, not proof of what it reads right
  after a restart, which this exercise did not trigger (restarting the live
  demo pod was avoidable and unnecessary to confirm behaviour already clear
  from the source).

## The trace, hop by hop

Numbers below were re-measured live for this document (a fresh Playwright
claim submission against the deployed cluster, trace
`edd4efea69542d6d0f401291917f5d5c` / a second capture with request+response
bodies, trace `ddf2747a385885345c0eeb25997d53ba`) and match
`docs/telemetry-contract.md`'s own captured numbers exactly (62 spans,
identical per-service split) — see that document for the full instrumentation
table and the convention gaps; this section only walks the propagation
mechanism at each boundary.

| Boundary | Mechanism | Carries `traceparent`/`tracestate`? | Carries `baggage` (→ `northstar.claim.id`)? |
|---|---|---|---|
| browser → claims-agent | HTTP header, injected by hand (`telemetry.ts`'s `injectContext`, since the browser is not wrapped by any auto-instrumentation) | Yes | N/A — not yet minted at this point |
| claims-agent (inbound) | ASGI/Starlette instrumentation (`opentelemetry-instrumentation-starlette`, wrapping the built app) extracts it. **`a2a-sdk` 0.3.7 itself extracts nothing** — an injected `traceparent` is ignored by the SDK on its own; this is the fix this project applied (A18), not something the SDK does natively. | Yes (via the fix) | Not yet — the claim id is minted inside the executor, after this extraction has already happened |
| claims-agent → settlement-agent (outbound) | `HTTPXClientInstrumentor`, instrumenting the plain `httpx.AsyncClient` `delegation.py` uses — free, no hand-written injection code | Yes | Yes, for the two claims-agent spans created after intake (see coverage below) |
| settlement-agent (inbound) | Same ASGI/Starlette instrumentation as claims-agent | Yes | Yes — extracted at the ASGI boundary, before any of settlement-agent's internal tasks spawn, so it reaches every subsequent span in that process |
| settlement-agent → payments-mcp | **JSON-RPC `params._meta`, not an HTTP header** (SEP-414, unprefixed `traceparent`/`tracestate`/`baggage` keys). `mcp==1.9.4`'s high-level client (`ClientSession.call_tool()`) has no way to set `_meta`, so `mcp_tools.py` drops to the lower-level `ClientSession.send_request()` and hand-builds the request, injecting via `opentelemetry.propagate.inject(carrier)` into that `_meta` dict | Yes (once injected this way) | Injected into `_meta`, but **not read on the other end** — see below |
| payments-mcp (inbound) | FastMCP 4.0.3's own `extract_trace_context()` (`fastmcp/telemetry.py`) reads `traceparent`/`tracestate` out of `params._meta` | Yes | **No — FastMCP never reads the `baggage` key out of `_meta` at all.** This is the one place `northstar.claim.id` stops. |
| payments-mcp → payments-api | Ordinary HTTP header, via `HTTPXClientInstrumentor` | Yes | No — nothing to propagate; baggage never entered this process's ambient context |
| payments-api (inbound) | `otelhttp` (Go), standard W3C header extraction | Yes | No, for the same reason |

**`northstar.claim.id` is set exactly once**, at claims-agent's intake
(`correlation.py`'s `set_claim_id`, called from `executor.py` right after
`parse_claim`), and rides in W3C baggage from there. It is **not** an
attribute added after the fact to every span by hand — each of the three
Python services runs a `ClaimIdSpanProcessor` (`correlation.py`, identical
in all three) that copies the value onto a span *only if it is already
present as baggage in the ambient context the moment that span starts*.
Real, measured coverage from the live capture taken for this document
(trace `edd4efea69542d6d0f401291917f5d5c`, 62 spans — identical to the
counts in `docs/telemetry-contract.md`):

```
customer-ui        1/1
claims-agent        2/23
settlement-agent   32/32
payments-mcp        0/4
payments-api        0/2
```

Everywhere the count is below 100%, the cause is one of: the span predates
the id being minted (customer-ui's own root span, and claims-agent's
pre-intake spans — `a2a-sdk` 0.3.7 spawns a separate asyncio task for the
executor before it attaches anything to the ambient context, so 21 of
claims-agent's 23 spans never see it), or baggage never crossed a boundary
at all (payments-mcp and payments-api, both zero, because of the FastMCP gap
above). `docs/telemetry-contract.md` has the full per-exemption reasoning
and the isolated reproduction that confirmed the claims-agent task-boundary
explanation; it is not repeated here.

**Our services create no spans of their own.** Verified by grep, not
assumed: there is no `get_tracer`, `start_span`, or `start_as_current_span`
anywhere in this project's own `agents/`, `tools/`, or `systems/` source,
except customer-ui's own `apps/customer-ui/src/telemetry.ts` /
`src/app/page.tsx` (the hand-written `claim.submit` root span — the one
necessary exception, since nothing upstream of the browser could have
created it) and one docstring mention in `tools/payments-mcp/bootstrap.py`.
Every other span in this chain — every `a2a.server.*`/`a2a.client.*` span,
every `StarletteInstrumentor`/`HTTPXClientInstrumentor` span, every FastMCP
`tools/call` span, every `otelhttp` span — comes from library
instrumentation wrapping our code from the outside, not from our code
calling the tracer directly.

## See it yourself

| Surface | URL |
|---|---|
| Customer UI | http://localhost:8080 |
| Jaeger | http://localhost:16686 |

1. Open the Customer UI. The form is pre-filled with claim `CLM-1842`,
   policy `POL-77120`, €6,200. Click "Submit claim." Within a few seconds
   you should see an "Outcome" block with `"status": "executed"` and a
   `paymentId` like `PAY-0000NN` (not necessarily `PAY-000001` — the counter
   is cumulative since the pod's last restart, see "Determinism" above).
2. In Jaeger: service `customer-ui`, operation `claim.submit`, "Find Traces."
   The single result is your submission (Jaeger's `total` field in its own
   API response is often `0` even when traces are returned — a documented
   API quirk, not a sign nothing came back; check `data`, not `total`).
3. Alternatively, search by tag: service `settlement-agent` (or
   `claims-agent`), tag `northstar.claim.id=CLM-1842`. This is where the
   coverage asymmetry above becomes visible directly — the same tag search
   against `payments-mcp` or `payments-api` returns nothing, by design (see
   above), even though those services' spans are still part of the same
   trace by trace ID.
4. Ignore any `harness-probe`/`harness-probe-*` entries in Jaeger's service
   dropdown (a verified-live fact, `GET /api/services`: 9 such entries were
   present in this cluster's Jaeger at the time of writing, one bare
   `harness-probe` plus 8 with a random-hex suffix) — these are artifacts of
   this project's own e2e test suite injecting synthetic trace IDs to test
   the harness itself, not components of the running system.
5. Spans worth clicking into, and what each demonstrates:
   - **`customer-ui`'s `claim.submit`** — the trace root. Note it has no
     parent, and its own two attributes (`northstar.claim.id`,
     `northstar.policy.number`) are set directly in `page.tsx`, not
     propagated from anywhere.
   - **`claims-agent`'s outbound `POST` (client span, target
     `settlement-agent...:9002`)** — this is the entire mechanism by which
     Hop 2's trace context crosses: no code in `delegation.py` injects a
     header by hand, `HTTPXClientInstrumentor` does it as a side effect of
     the ambient context being correct at call time.
   - **`payments-mcp`'s `tools/call execute_payment`** — check its
     attributes: `gen_ai.tool.name = execute_payment` is present;
     `gen_ai.operation.name` is not (Gap in `docs/telemetry-contract.md`).
     Also confirm `northstar.claim.id` is absent here specifically — this is
     the span the coverage table above and the contract's Gap 4 are about.
   - **`payments-api`'s `POST /payments/{paymentId}/execute`** — the last
     hop, stable HTTP semantic-convention spelling
     (`http.request.method`/`http.response.status_code`), and the one place
     in the whole trace where real money state changed.
