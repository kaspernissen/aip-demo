# Northstar Architecture — M2.1 (Interposed)

Status: **M2.1 (Interposed)**, on branch `m2.1-interposed`. The M2.1 content
below was written and checked against commit `b75b260` — the commit
immediately before the one that adds it — against both the source tree and
the live `kind-northstar` cluster at that point. M1.1 and M1.2 content keeps
its own, older provenance, stated in the next block; this document does not
claim a single verification pass over all of it.

M1.1 was the walking skeleton: one claim, five components, one trace, no
governance. M1.2 put a real model call inside that skeleton without changing
its shape. M2.1 changes the shape for the first time — a proxy and an
identity provider are now in the picture, and one hop authenticates its
caller cryptographically.

**The one-sentence version of M2.1, and the honest one:** *the model can
request; the platform now knows who is requesting.* It does not yet decide.
Every call from a caller the gateway can authenticate is still forwarded, and
no policy exists anywhere in this chain — see "What the system deliberately
does not do", which M2.1 **sharpens** from *no authorization anywhere* to
**authenticated but not authorized**, rather than deleting. That distinction
is precisely what M2.2 changes.

**Two claims a reader might expect to find here and will not, because they
are measurably false.**

1. *"One convention end to end."* Not true, and the true statement is
   stronger: **the trace holds together end to end across components that do
   not agree on attribute names.** agentgateway emits `http.method` /
   `http.path` / `http.status` — matching neither the stable HTTP semconv set
   this project's own services emit (`http.request.method`,
   `http.response.status_code`, `server.address`, via
   `OTEL_SEMCONV_STABILITY_OPT_IN=http`) nor the OTel legacy spelling
   (`http.target`, `http.status_code`) — and `jwt.sub` where semconv has
   `user.id`. Measured, pinned by `tests/e2e/test_gateway_trace.py` by exact
   set equality, and written up as Finding 7 in
   [`docs/upstream-findings.md`](upstream-findings.md). Trace context is what
   makes the chain one thing; attribute naming is what makes it queryable,
   and the two are separable. The real-world case is the more interesting one
   to demonstrate than a demo-world one would be.
2. *A whole-trace span total.* This document publishes none. The trace is
   non-deterministic: the `a2a-sdk`'s
   `a2a.server.events.event_queue.EventQueue.dequeue_event` polling span was
   measured varying **10–16** across identically-shaped runs. The "62 spans"
   figures further down are M1.1-era captures, kept with their own stated
   provenance rather than restated as current. What is stable, and therefore
   what gets published and asserted, is trace **shape** — service, operation,
   kind, parent.

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

**What M2.1 changed, and where each change is described.** Earlier revisions
listed these here as notes *outside* the hop-by-hop text, because that text
was deliberately a record of M1.1's shape. M2.1 Task 5 folded them in
instead, so the document describes the system that is actually deployed:

1. **Task 1** replaced kind's default CNI with **Cilium 1.20.1** (a
   create-time `disableDefaultCNI` in `deploy/kind/stage2.yaml`). It sits
   underneath every hop below and changes none of them: the trace survived
   the swap with all 61 distinct `(service, operation, kind, parent)` rows
   identical pre and post. `kubeProxyReplacement` is deliberately off, and
   `kube-proxy` is still running. **There is no `toFQDNs` egress policy** —
   only its `l7Proxy` prerequisite is confirmed present; egress denial is a
   later milestone's work and nothing in this system restricts outbound
   traffic today.
2. **Task 2** deployed **agentgateway v1.5.0** into `northstar-platform` as a
   plain Deployment with a ConfigMap-mounted `config.yaml` — **not** the Helm
   chart, which installs a Gateway API control plane with nowhere to put this
   milestone's policy block. See "The components".
3. **Task 3** interposed it on the MCP hop. `settlement-agent`'s
   `PAYMENTS_MCP_URL` now names the gateway, so what was Hop 3 is now two
   hops — **Hop 3a** and **Hop 3b** below. The chain remains one distributed
   trace, with the gateway a visible node in it rather than a fracture.
4. **Task 4** made workload identity real on that hop: **SPIRE 1.15.3**
   (charts `spiffe/spire-crds` 0.6.1 then `spiffe/spire` 0.30.1) issues
   `settlement-agent` a JWT-SVID and the gateway verifies it. See "Workload
   identity: what is real and what is deferred".
5. **Task 5** proved the verification is load-bearing rather than
   decorative — `tests/e2e/test_caller_identity.py` — and wrote this
   revision. See "Identity with teeth".

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
                                                     Hop 3a: MCP over streamable
                                                     HTTP. Identity in an
                                                     `Authorization: Bearer`
                                                     JWT-SVID header; trace
                                                     context in BOTH a
                                                     `traceparent` header and
                                                     JSON-RPC `params._meta`
                                                                       │
                                                                       ▼
                                                          ┌──────────────────┐
                                                          │  agentgateway    │
                                                          │  (Rust, v1.5.0)  │
                                                          │  SVID verified   │
                                                          │  no policy yet   │
                                                          └────────┬─────────┘
                                                                   │
                                                     Hop 3b: MCP again, same
                                                     transport. The gateway
                                                     STRIPS the credential and
                                                     re-stamps `_meta` with its
                                                     own outbound span
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

## The components

M1.1 had five, all of them this project's own code. M2.1 adds platform
infrastructure this project runs but did not write, so the two are listed
separately — the distinction matters for the no-invented-telemetry rule,
which applies to the second table without exception.

### The five application components

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

### The platform components M2.1 adds

All of these live in `northstar-platform` and none is this project's code.
Images and versions below were read from the live cluster, enumerating over
every pod's `.spec.initContainers` **and** `.spec.containers` — not
`containerStatuses`, which omits init containers and would have missed two of
the six SPIRE sub-images, including the one carrying the hazard recorded under
"Deferred hardening" below.

| Component | What it is | Version / image | Role in this system |
|---|---|---|---|
| `agentgateway` | Rust MCP/A2A/LLM proxy. A **Linux Foundation** project — **not** CNCF | `cr.agentgateway.dev/agentgateway:v1.5.0` (note the leading `v`; the tag `1.5.0` does not exist on that registry, though the binary reports `1.5.0` as its internal version) | Interposed on the MCP hop. Verifies the caller's JWT-SVID; forwards to `payments-mcp`. Carries no policy. |
| SPIRE server / agent | SPIFFE runtime, chart `spiffe/spire` 0.30.1 from the hardened repo | appVersion SPIRE **1.15.3**; `ghcr.io/spiffe/spire-server:1.15.3`, `spire-agent:1.15.3` | Issues the JWT-SVID. Trust domain `northstar.local`, cluster name `northstar`. |
| SPIRE CRDs | chart `spiffe/spire-crds` 0.6.1, installed first | — | The `spire.spiffe.io` CRDs the `ClusterSPIFFEID` resources need. |
| `spire-controller-manager` | ships inside the SPIRE chart | `ghcr.io/spiffe/spire-controller-manager:0.7.0` | Reconciles `ClusterSPIFFEID` resources into real registration entries. |
| `spiffe-csi-driver` (+ `node-driver-registrar`) | ships inside the SPIRE chart | `ghcr.io/spiffe/spiffe-csi-driver:0.2.7`, `registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.15.0` | Bind-mounts the Workload API socket into `settlement-agent`'s pod at `/spiffe-workload-api/spire-agent.sock`. |
| `spiffe-oidc-discovery-provider` (+ `nginx` sidecar) | ships inside the SPIRE chart | `ghcr.io/spiffe/oidc-discovery-provider:1.15.3`, `docker.io/nginxinc/nginx-unprivileged:1.30.0-alpine` | Publishes SPIRE's JWT signing keys as a JWKS, which the gateway fetches. Plain HTTP inside the cluster — recorded as a gap, not hidden. |

**Not the Helm chart, for agentgateway, and that was a real decision.** The
gateway is a plain `Deployment` plus a ConfigMap-mounted `config.yaml`
(`deploy/platform/agentgateway/`), not `helm install agentgateway`. The chart
installs a Gateway API *control plane* — it expects `Gateway`/`HTTPRoute`
resources and a controller to translate them, and there is nowhere in that
model to put this milestone's single inline `jwtAuth` policy block. A
standalone binary reading one file is both the smaller thing and the more
legible one for a document like this to describe.

**Two versions of SPIRE, and they are not the same number.** The *chart* is
0.30.1 and the *product* is 1.15.3. `spiffe/spire` and `spiffe/spire-crds`
are independent tag series on the same repository, which is why
`deploy/platform/spire/values.yaml` pins two chart versions rather than one.

**What SPIRE contributes to the telemetry: metrics only.** SPIRE 1.15.3 has
no OpenTelemetry support of any kind — no OTLP exporter, no tracing, and its
telemetry configuration offers only Prometheus, DogStatsD, Statsd, M3 and an
in-memory sink. **So SPIRE emits no spans, and this document publishes no
SPIRE span attribute anywhere, because there are none to publish.** "It
cannot be traced" is the accurate statement. Its Prometheus metrics *are*
enabled and scraped into the collector as `service.name=spire-server` /
`spire-agent`; see
[`docs/telemetry-contract.md`](telemetry-contract.md)'s "SPIRE
instrumentation, as measured".

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
  configured URL actually is settlement-agent. **This is still true on Hop 2
  in M2.1.** SPIRE has arrived, but only Hop 3a uses it: the allow-list here
  is unchanged and still checks a string. See "Workload identity: what is
  real and what is deferred", hardening item 2.
- It says nothing about whether the delegated call itself should be allowed.
  Once a name is in the map, the amount, the claim, and the decision to pay
  are not checked against anything — see "What the system deliberately does
  not do" below.

This is the M1 stand-in for what M2's `agentgateway` is meant to enforce at
the network boundary instead of in application code.

## Hop 3 — settlement-agent → payments-mcp: now two hops, through agentgateway

**This was one hop in M1.1 and is two in M2.1.** `settlement-agent`'s
`PAYMENTS_MCP_URL` is
`http://agentgateway.northstar-platform.svc.cluster.local:3000/mcp`
(`deploy/k8s/settlement-agent.yaml`), so:

| | From → to | What is different about it |
|---|---|---|
| **Hop 3a** | `settlement-agent` → `agentgateway` | Carries the credential: `Authorization: Bearer <JWT-SVID>`. Trace context rides in *two* independent carriers (see below). This is the only authenticated hop in the system. |
| **Hop 3b** | `agentgateway` → `payments-mcp` | Same MCP transport, same tool call. The gateway has **stripped** the credential and **re-stamped** `params._meta` with its own outbound span. `payments-mcp` therefore verifies nothing and sees no identity. |

Both hops are MCP over streamable HTTP, and the reroute did not change the
protocol at either end: `payments-mcp` is unmodified and does not know a
proxy is in front of it.

**The gateway is a node in the trace, not a fracture** — measured shape, and
the only thing about the trace this document publishes, since a whole-trace
span total is not stable (see the header):

```
settlement-agent  execute_tool
  settlement-agent  POST                                  (httpx CLIENT)
    agentgateway    tools/call                             (SERVER)
      agentgateway  tools/call payments-mcp_<tool>         (CLIENT)
        payments-mcp  tools/call <tool>                    (SERVER)
          payments-mcp  POST                               (httpx CLIENT)
            payments-api  POST /payments/...               (SERVER)
```

One trace id throughout, no new roots and no orphans.
`tests/e2e/test_gateway_trace.py` is the runnable evidence and pins this
shape, the two span kinds, the parentage, and the gateway's exact attribute
vocabulary. Note that the tool name is **not** in the gateway's SERVER span
name — both tool calls in a settlement produce a span named exactly
`tools/call`, and telling them apart means reading the `gen_ai.tool.name`
attribute.

**Why the trace survives, stated precisely, because two mechanisms are very
easy to conflate here and conflating them produces a false claim.** The
gateway *ingests* trace context from the HTTP `traceparent` header and from
nowhere else; it *emits* SEP-414 `params._meta` on messages it forwards.
Those are independent directions, not a round trip:

- **Ingress.** `HTTPXClientInstrumentor` auto-injects a `traceparent`
  **HTTP header** on every outbound MCP request, because the MCP
  streamable-HTTP transport is httpx-based. The gateway reads that. It does
  **not** read `params._meta` on the way in — see Finding 6 in
  [`docs/upstream-findings.md`](upstream-findings.md).
- **Egress.** Separately, the gateway stamps its *own* outbound span into
  `_meta` on the messages it forwards, which is why `payments-mcp`'s
  `initialize` spans also joined the trace — those are not messages
  `settlement-agent` hand-injected.

MCP's own propagation convention (SEP-414) rides JSON-RPC `params._meta` and
never HTTP headers. So this project's own hand-injection in `mcp_tools.py` is
for FastMCP's benefit at the far end, and the header the gateway reads comes
free from an instrumentor. Nothing here should be read as agentgateway
supporting SEP-414 on ingress: it does not.

Never stdio. The design spec is explicit about why: *"Transport is
**streamable HTTP** throughout. stdio cannot traverse agentgateway in M2, so
choosing it now would guarantee a rewrite."* (`docs/superpowers/specs/2026-09-07-northstar-m1-connected-design.md`).
That prediction is now load-bearing rather than hypothetical, and Finding 6
is why: a stdio MCP client carries trace context *only* in `_meta`, which is
the one carrier this gateway does not read.
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
abstraction works."* Nothing reads this annotation yet — not in M1.1 and
still not in M2.1, because no policy engine exists in either. The gateway
records the tool name on its spans (`gen_ai.tool.name`) but makes no
decision from it. Today the annotation is inert metadata sitting in the tool
schema.
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

## Workload identity: what is real and what is deferred

**M2.1 Task 4.** One hop in this system now authenticates its caller
cryptographically. Hop 3 — settlement-agent → agentgateway — carries a
SPIFFE JWT-SVID issued by SPIRE, and the gateway refuses the call without one.

What is installed, pinned (`deploy/platform/spire/`):

| Piece | Version | Role |
|---|---|---|
| `spiffe/spire-crds` chart | 0.6.1 | the `spire.spiffe.io` CRDs, installed first |
| `spiffe/spire` chart | 0.30.1 (appVersion SPIRE 1.15.3) | server, agent, CSI driver, controller-manager, OIDC discovery provider |
| trust domain | `northstar.local` | every SPIFFE ID in this system |
| cluster name | `northstar` | the k8s node attestor's cluster |

Two identities are registered, declaratively, as `ClusterSPIFFEID` resources:

```
spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent
spiffe://northstar.local/ns/northstar-platform/sa/agentgateway
```

Both are attested on namespace **and** Kubernetes service account
(`k8s:ns:...` plus `k8s:sa:...` selectors on the entry, alongside the
controller-manager's own `k8s:pod-uid:`), which is why each workload got a
dedicated ServiceAccount in this task: without one, both attested as
`.../sa/default`, and an identity whose service-account component reads
"default" is not something a policy can decide on.

**How the credential gets into the pod.** No secret, key or token is in any
manifest. The `spiffe-csi-driver` bind-mounts the spire-agent's Workload API
socket into settlement-agent's pod at `/spiffe-workload-api/spire-agent.sock`,
and the agent's Kubernetes workload attestor identifies the calling container
via kubelet before answering. `agents/settlement-agent/svid.py` asks that
socket for a JWT-SVID scoped to one audience, caches it until 30s before
expiry, and `mcp_tools.py` presents it as `Authorization: Bearer` on the MCP
transport.

**What the gateway checks.** `jwtAuth` in `mode: strict` — issuer, audience,
signature (against SPIRE's JWKS, fetched from the OIDC discovery provider) and
`exp`. Verification only: there is no `mcpAuthorization` and no `authorization`
rule in the gateway's config, so every call it can authenticate it still
permits. Deciding what an authenticated caller may do is a later milestone;
this task's job was to make the "who" real so there is something to decide on.

**Why a bearer token and not mTLS.** The spec permits either, and mTLS is the
stronger choice: an mTLS peer certificate cannot be replayed by whoever
obtains a copy of it, and a JWT-SVID can. It was not taken here because it
would mean threading a SPIFFE TLS context through `streamablehttp_client`'s
transport — real risk inside the one file M2 is allowed to modify, and the file
the surviving Task 3 trace was measured against. A JWT-SVID keeps the identity
genuinely SPIFFE-issued (a `spiffe://` subject, signed by SPIRE, verified via
JWKS) at the cost of one HTTP header. The weakness is bounded rather than
eliminated: the audience scopes each token to this one gateway, and the TTL is
one hour (SPIRE's `default_jwt_svid_ttl`).

**Recorded as later hardening, in order of how much they matter:**

1. **mTLS instead of (or under) the bearer token**, per the above. This is the
   real fix, and it is deferred rather than declined.
2. **The other hops are still unauthenticated.** Hop 1 (browser →
   claims-agent), Hop 2 (claims-agent → settlement-agent) and Hop 4
   (payments-mcp → payments-api) carry no SVID at all. Hop 2's allow-list
   still checks a *name*, exactly as described at Hop 2 above. Anything in the
   cluster can still call settlement-agent's Service directly.
3. **The JWKS endpoint is plain HTTP inside the cluster.**
   `spiffe-oidc-discovery-provider.tls.spire.enabled` is set to `false`, so the
   provider serves the key set over HTTP on port 80 via the chart's own nginx
   sidecar. The chart's default is HTTPS with a SPIRE-issued serving
   certificate, which agentgateway would have to be taught to trust in order
   to fetch a *public* key set. Signature verification does not depend on
   transport security here — the keys are public and the tokens are signed —
   but an attacker able to intercept that fetch could serve a key set of their
   own choosing, so this is a genuine gap on a real network.
4. **`payments-mcp` does not check anything.** The gateway removes the
   credential from its original location after validating it — agentgateway
   v1.5.0's `preserveToken` defaults to false, and
   `crates/agentgateway/src/http/jwt.rs` acts on it directly
   (`if !self.preserve_token { self.location.remove(req)?; }`) — so the MCP
   server behind it sees no identity, and would accept a direct call that
   skipped the gateway entirely. Removing it is the right default; the gap is
   that nothing re-establishes identity on the second leg.

### Two more things with no owner until now

Both were surfaced by an earlier task and had nowhere to be recorded, which
is how findings get lost. Neither is a defect in this project's own work.

**5. Four of the SPIRE chart's sub-image tags are behind upstream — and the
standing decision is to leave them alone.** All six sub-images the
`spiffe/spire` 0.30.1 chart brings resolve to an exact tag or digest and
nothing floats (verified by enumerating every pod's
`.spec.initContainers` *and* `.spec.containers`; two of the six *are* init
containers, which is why `containerStatuses` is the wrong thing to
enumerate). Four are nonetheless older than what upstream ships today, each
checked against the upstream release list rather than a local index:

| Sub-image | Chart pins | Latest upstream (checked 2026-09-09) |
|---|---|---|
| `ghcr.io/spiffe/spiffe-csi-driver` | 0.2.7 | v0.2.13 |
| `registry.k8s.io/sig-storage/csi-node-driver-registrar` | v2.15.0 | v2.18.0 |
| `docker.io/nginxinc/nginx-unprivileged` | 1.30.0-alpine | 1.31.5-alpine (and 1.30.4-alpine within its own line) |
| `docker.io/library/busybox` | 1.37.0-uclibc | 1.38.0-uclibc |

**These are the chart's own choices and are deliberately NOT overridden.**
`deploy/platform/spire/values.yaml` sets no `image`, `tag`, `repository` or
`digest` key anywhere — confirmed by grep, and the reason is a judgement
rather than an omission: overriding a chart's internal sub-image tags means
running a combination the chart's maintainers never tested, and nothing in
this repository's test suite would catch an incompatibility between, say, a
newer CSI node-driver-registrar and the driver the chart expects. The
"nothing floats" rule is satisfied — every tag is exact — and "exactly pinned
by the chart" is a different property from "newest available". If these ever
need bumping, the right move is a newer *chart*, not a values override.

**6. A stage-preparation hazard: one init image is pinned by digest against a
rolling tag, and it fails closed.** The
`spiffe-oidc-discovery-provider`'s `ensure-alternate-names` init container
runs:

```
cgr.dev/chainguard/bash:latest@sha256:90041f375e30f41aa7e0390075d8a69dc61900771d52fa98d63ee5d03d866a58
```

A digest pinned against `:latest` is reproducible right up until the digest
is garbage-collected, and Chainguard's free `cgr.dev/chainguard` namespace is
where that is most likely to happen — it generally keeps only the current
`latest`. **Measured 2026-09-09: the pinned digest still resolves (HTTP 200),
but `:latest` now points at a different manifest entirely
(`sha256:6250d4dc…`).** So the tag has already moved on and the pinned digest
is now an untagged manifest whose retention this project does not control.

Why this is a *stage-preparation* hazard specifically, and not a background
worry: the failure mode is an image pull failure on a **cluster recreate**,
which is exactly the thing that happens shortly before a talk. It fails
closed — the init container cannot start, so the discovery provider never
becomes ready, so the gateway cannot fetch the JWKS, so every MCP call is
refused. The symptom at the front of the room would be a demo where the
model asks and nothing happens, and the cause would be four layers down in a
chart's init container.

**Mitigation to run before a talk, not after:** mirror the images this
cluster needs into the shared `kind-registry` and point the install at that,
or — simpler and enough for a demo — pre-pull them and `kind load` them into
the `northstar` node so the pull never leaves the machine. Either way the
check is cheap: recreate the cluster once, on the day before, from a network
you do not need.

**Why the trace is unaffected.** Identity and trace context cross this
boundary in two different carriers, deliberately: the SVID in an HTTP
`Authorization` header, the trace context in the JSON-RPC `params._meta` bag
(plus the `traceparent` header `HTTPXClientInstrumentor` injects, which is
what the gateway actually reads). Neither substitutes for the other — trace
context is attacker-supplied and is not a credential; an SVID says nothing
about which trace a call belongs to.

**What SPIRE itself contributes to the telemetry: metrics only, no spans.**
SPIRE 1.15.3 has no OpenTelemetry support of any kind — no OTLP exporter and
no tracing. Its telemetry configuration offers Prometheus, DogStatsD, Statsd,
M3 and an in-memory sink and nothing else. So SPIRE adds no spans to the
trace, and none are invented for it. Its Prometheus metrics *are* enabled and
scraped into the collector; see
[`docs/telemetry-contract.md`](telemetry-contract.md)'s "SPIRE
instrumentation, as measured".

## Identity with teeth: what was proven, and what was not

**M2.1 Task 5.** An identity that is issued, sent and verified but never
*decides* anything is decoration. Task 4 made the platform know who is
calling and put the verified subject in the trace; on its own that proves
less than it looks like, because a gateway that recorded `jwt.sub` from any
token it was handed — or forwarded regardless — would satisfy every one of
those assertions. `tests/e2e/test_caller_identity.py` is the negative half.
It speaks MCP to the gateway directly rather than through
`settlement-agent`, which spends no model tokens and tests the gateway's
verification rather than the agent's error handling.

**What is proven.** Each case is refused with HTTP **401** and a `text/plain`
body carrying agentgateway's own `TokenError` text — measured, and not the
JSON-RPC error body an earlier spike had predicted, because `jwtAuth` runs as
an HTTP filter before the MCP backend is consulted at all:

| Presented | Refused with |
|---|---|
| no token | `authentication failure: no bearer token found` |
| `Bearer not-a-real-svid` | `authentication failure: the token header is malformed: Error(InvalidToken)` |
| a well-formed JWT from an untrusted issuer, own `kid` | `authentication failure: token uses the unknown key "…"` |
| the same, wearing SPIRE's current `kid` | `authentication failure: the token is invalid or malformed: Error(InvalidSignature)` |
| **a genuine SPIRE-minted JWT-SVID for a different audience** | `authentication failure: the token is invalid or malformed: Error(InvalidAudience)` |

**The last row is the one that matters, and it is the only one that isolates
a claim check.** That token is minted by the real SPIRE server, signed by the
real JWT signing key SPIRE publishes in the JWKS the gateway fetches, and
carries the real issuer and `settlement-agent`'s own attested subject. Only
`aud` differs — it names `payments-mcp`'s SPIFFE ID instead of the
gateway's. So its refusal cannot be explained by malformedness, an untrusted
signer, or expiry; the only remaining explanation is the `audiences` list in
the gateway's config. That is the property the config file had until now only
*claimed* in prose: a token issued for one SPIFFE service in this trust
domain cannot be replayed at another.

**What is not proven, stated plainly because the obvious summary would
overstate it.** The tests do **not** demonstrate that agentgateway's `iss`
*string comparison* fired. With the deployed single-issuer configuration that
check is not independently reachable, and the reason is upstream's code
rather than this project's choices: `Jwt::validate_claims`
(`crates/agentgateway/src/http/jwt.rs`) looks the signing key up by the
token header's `kid` and only then calls `jsonwebtoken::decode`, which
verifies the signature before validating `iss` and `aud`. A token bearing an
untrusted issuer is therefore always caught earlier — by key trust, which is
the check that actually matters, and which is what an attacker minting their
own token runs into. The `iss` comparison becomes independently testable only
in a multi-issuer (`providers`) configuration, which this project does not
deploy.

**These tests have been made to fail.** A test that has never failed proves
nothing, so three mutations were applied to the live gateway config, each
derived from a distinct decision path in the code being checked rather than
from edits a human might plausibly make, and each predicted before it was
run:

| Mutation | Predicted and observed |
|---|---|
| remove the `jwtAuth` block entirely | all five refusals red; the accepted-path control stays green |
| `mode: strict` → `mode: optional` | **only** the no-token test red — `optional` still validates a token it is handed, it just permits a missing one |
| point `audiences` at `payments-mcp`'s SPIFFE ID | the audience test red (it is now accepted) **and** the control red (its correct-audience token is now rejected) |

The third is the one that matters for guard quality: it is a *swap*, so it
fails only if the audience test asserts an assignment rather than merely
"something got refused". The config was then restored and the live value read
back with `jsonpath` and compared by checksum against the repo file — a
`kubectl diff` cannot prove a revert, since it is a three-way merge against
`last-applied-configuration`.

## What the system deliberately does not do

**M2.1 sharpens this section rather than deleting it. The position is now
"authenticated but not authorized".** One hop knows *who* is calling and
refuses a caller it cannot verify. No hop anywhere decides *whether the
caller may do what it asked*. Those are different properties, an identity
that decides nothing is decoration, and the whole point of M2.1 is to make
the "who" real so that M2.2 has something to decide on.

**There is still no authorization anywhere in this chain.** Concretely,
verified by reading every hop's code, not inferred:

- **agentgateway performs authentication only.** Its `config.yaml` carries a
  `jwtAuth` block and deliberately **no** `mcpAuthorization` and no
  `authorization` rule. Both policy mechanisms this project examined — the
  native CEL `mcpAuthorization` and the external `ExtMCP` gRPC hook — are
  absent on purpose. Every call it can authenticate, it forwards. A verified
  `settlement-agent` asking to move any amount for any claim gets through,
  and the `destructiveHint: true` annotation on `execute_payment` is read by
  nothing.
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
  it can move money."* Still exactly true in M2.1: nothing was added to
  payments-api, and anything in the cluster that can reach its Service can
  move money without presenting anything.

**One claim in this section was true through M1.2 and is now false, so it is
corrected rather than left standing.** Earlier revisions said *"There is no
auth header, no API key, no mTLS anywhere in this chain — confirmed by
reading every HTTP client construction site in the repo (`delegation.py`,
`mcp_tools.py`, `payments_client.py`); none sets an `Authorization` header or
any credential."* As of M2.1 Task 4, **`mcp_tools.py` does set one** — an
`Authorization: Bearer <JWT-SVID>` header on every MCP call. The other two
construction sites are unchanged and still set no credential, so the accurate
statement is: one of this chain's three HTTP client construction sites now
presents a credential, and it is the one on Hop 3a.

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

**Does interposing the gateway change any of this? No — and it is worth
saying, because "a proxy is now in the path" sounds like it might.** Payment
IDs are minted by `payments-api`'s counter and by nothing else. agentgateway
does not generate, rewrite or cache them, and it plays no part in the
`propose → execute` correlation beyond forwarding the tool calls that carry
them. SPIRE does not touch them either. The counter advances by one per
`propose_payment` that reaches payments-api, exactly as in M1.1.

**What does affect the number a reader sees — not new in M2.1, but with one
new source.** Anything that proposes a payment advances the counter: demo
runs, the e2e suite, and probes. M2.1 adds
`tests/e2e/test_caller_identity.py`, whose accepted-path control really
executes `propose_payment` through the gateway on every run — deliberately,
because a refusal test whose green and red look alike proves nothing. This is
why nothing in this repo asserts an absolute payments-api record count or a
hardcoded next ID: the store is in-memory and every probe writes to it.
Assertions are relative or filtered — a test asserts that *its own* call came
back `proposed` for *its own* claim id, never that the ledger holds N rows.

## The trace, hop by hop

**Provenance of the numbers in this section, corrected for M2.1.** They were
measured live for the *M1.2* revision of this document — a Playwright claim
submission against the then-deployed cluster, trace
`edd4efea69542d6d0f401291917f5d5c`, plus a second capture with
request+response bodies, trace `ddf2747a385885345c0eeb25997d53ba` — and at
that time matched `docs/telemetry-contract.md` exactly (62 spans, identical
per-service split). **They were not re-measured for M2.1, and the span total
is not a number this project publishes as current any more** (see the
header: the `a2a-sdk` polling span varies 10–16 run to run, so no whole-trace
total is stable). They are kept, with this provenance, because the
*propagation mechanism* each row describes is what this section is about and
that has been re-checked — the mechanism table below includes M2.1's two new
gateway rows. Treat the counts as an M1.2-era illustration of the coverage
asymmetry, not as a measurement of the deployed system today. The
propagation table itself is current.

| Boundary | Mechanism | Carries `traceparent`/`tracestate`? | Carries `baggage` (→ `northstar.claim.id`)? |
|---|---|---|---|
| browser → claims-agent | HTTP header, injected by hand (`telemetry.ts`'s `injectContext`, since the browser is not wrapped by any auto-instrumentation) | Yes | N/A — not yet minted at this point |
| claims-agent (inbound) | ASGI/Starlette instrumentation (`opentelemetry-instrumentation-starlette`, wrapping the built app) extracts it. **`a2a-sdk` 0.3.7 itself extracts nothing** — an injected `traceparent` is ignored by the SDK on its own; this is the fix this project applied (A18), not something the SDK does natively. | Yes (via the fix) | Not yet — the claim id is minted inside the executor, after this extraction has already happened |
| claims-agent → settlement-agent (outbound) | `HTTPXClientInstrumentor`, instrumenting the plain `httpx.AsyncClient` `delegation.py` uses — free, no hand-written injection code | Yes | Yes, for the two claims-agent spans created after intake (see coverage below) |
| settlement-agent (inbound) | Same ASGI/Starlette instrumentation as claims-agent | Yes | Yes — extracted at the ASGI boundary, before any of settlement-agent's internal tasks spawn, so it reaches every subsequent span in that process |
| settlement-agent → agentgateway (**Hop 3a**, M2.1) | **Two independent carriers, both present, neither redundant.** (i) JSON-RPC `params._meta` (SEP-414, unprefixed `traceparent`/`tracestate`/`baggage`): `mcp==1.9.4`'s high-level client (`ClientSession.call_tool()`) has no way to set `_meta`, so `mcp_tools.py` drops to `ClientSession.send_request()` and hand-builds the request, injecting via `opentelemetry.propagate.inject(carrier)`. (ii) An HTTP `traceparent` **header**, auto-injected by `HTTPXClientInstrumentor` because the transport is httpx-based | Yes, twice over | Yes in `_meta`; yes in the `baggage` header |
| agentgateway (inbound) | **The HTTP header, and only that.** `TraceParent::from_request` (v1.5.0, `crates/agentgateway/src/telemetry/trc.rs:690-696`) reads the `traceparent` header and never inspects the request body. It does **not** read `params._meta` — Finding 6. With no header it would mint a new trace root | Yes (via the header) | Not consumed as baggage — the gateway has no baggage concept and its spans carry no `northstar.claim.id`, which is the intended enrichment boundary and is asserted as a negative in `tests/e2e/test_gateway_trace.py` |
| agentgateway → payments-mcp (**Hop 3b**, M2.1) | The gateway starts its own outbound CLIENT span, injects that span into the outbound **headers**, and then copies the three W3C keys from those headers into `params._meta` (`crates/agentgateway/src/mcp/upstream/mod.rs:146` then `:110-122`). So `_meta` reaching payments-mcp names the *gateway's* span, not settlement-agent's — which is why payments-mcp parents under the gateway and not under the agent | Yes (re-stamped) | The `baggage` key is copied too, if the header is present |
| payments-mcp (inbound) | FastMCP 4.0.3's own `extract_trace_context()` (`fastmcp/telemetry.py`) reads `traceparent`/`tracestate` out of `params._meta` | Yes | **No — FastMCP never reads the `baggage` key out of `_meta` at all.** This is the one place `northstar.claim.id` stops, and interposing the gateway did not change that. |
| payments-mcp → payments-api | Ordinary HTTP header, via `HTTPXClientInstrumentor` | Yes | No — nothing to propagate; baggage never entered this process's ambient context |
| payments-api (inbound) | `otelhttp` (Go), standard W3C header extraction | Yes | No, for the same reason |

**`northstar.claim.id` is set exactly once**, at claims-agent's intake
(`correlation.py`'s `set_claim_id`, called from `executor.py` right after
`parse_claim`), and rides in W3C baggage from there. It is **not** an
attribute added after the fact to every span by hand — each of the three
Python services runs a `ClaimIdSpanProcessor` (`correlation.py`, identical
in all three) that copies the value onto a span *only if it is already
present as baggage in the ambient context the moment that span starts*.
Coverage from the M1.2-era capture described above (trace
`edd4efea69542d6d0f401291917f5d5c`, 62 spans, identical at the time to the
counts in `docs/telemetry-contract.md`). Read the *pattern*, not the
absolute numbers: the totals are not re-measured for M2.1 and the span count
is not stable run to run, but which services reach 100% and which reach zero
is a property of the mechanism, not of one capture, and that is unchanged —
the gateway adds spans of its own with no `northstar.claim.id` on any of
them, which is the intended enrichment boundary rather than a regression:

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
   - **agentgateway's `tools/call` (SERVER)** — new in M2.1, and the most
     informative span in the trace. Three things to look at: `jwt.sub`,
     which is the caller's attested SPIFFE ID
     (`spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent`)
     and is set by the gateway from the token it verified, not by anything
     this project wrote; `gen_ai.tool.name`, which is where the tool name
     lives, because the span's *name* is just `tools/call` for both tools;
     and `http.method` / `http.path` / `http.status`, which are the third
     HTTP vocabulary described in the header and in Finding 7. Then look at
     its child CLIENT span, `tools/call payments-mcp_<tool>`, and note
     `jwt.sub` is **absent** there — the gateway strips the credential on
     the outbound leg, so there is no verified token to attribute.
   - **`payments-api`'s `POST /payments/{paymentId}/execute`** — the last
     hop, stable HTTP semantic-convention spelling
     (`http.request.method`/`http.response.status_code`), and the one place
     in the whole trace where real money state changed. Comparing this
     span's attribute names with the gateway's, in the same trace, is the
     fastest way to see the naming divergence for yourself.
6. **See the refusal, without spending a model call.** This costs nothing and
   needs no Anthropic tokens:

   ```bash
   .venv/bin/python -m pytest tests/e2e/test_caller_identity.py -v
   ```

   That file port-forwards to the gateway itself (on an ephemeral local port
   it asks `kubectl` for, so it cannot collide with the four repo-managed
   forwards), mints real JWT-SVIDs from SPIRE, and asserts both that a valid
   one executes a tool end to end and that four invalid ones are refused.
   See "Identity with teeth" above for the exact wire shape of each refusal.
