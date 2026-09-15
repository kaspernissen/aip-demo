# Northstar M2 "Governed" — design

Status: design approved 2026-09-09, not yet planned or implemented.
Base: `main` at tag `m1.2-verified`.
Predecessor spec: `docs/superpowers/specs/2026-09-07-northstar-m1-connected-design.md`.

**The line this milestone earns on stage:**

> *"The model can request. The platform decides if it becomes an effect."*

---

## 1. What M2 is

M1 ended with a system that works and should frighten you: a language model chose to
execute a €6,200 payment, and nothing in the platform could have stopped it. `execute_payment`
paid because a model asked. M2 interposes a platform between the request and the effect.

The demonstration is a **policy denial of the payout**. The model proposes €6,200 and asks to
execute it; the platform refuses. The audience sees the request present in the trace and the
effect absent — the line made literal rather than asserted.

Three alternatives were considered and rejected as the *primary* moment: Cilium egress denial
(strong infrastructure story, weak tie to "becomes an effect"), identity refusal of an
unauthorized caller (identity doing real work, but a narrower point), and all three layered
(most complete, most to go wrong live). Cilium egress is still built — see §7 — as a second
defence, not as the headline.

### What M2 governs

M2 governs the system that exists, not the system the M1 spec projected. M1.1 and M1.2 built
**one chain** — `claims-agent` → `settlement-agent` → `payments-mcp` → `payments-api` — and
the M1.3 fan-out to four agents was deferred until after M2. This is deliberate and good: the
governance patterns get proven on one path before being multiplied by four.

---

## 2. Architecture

```text
        browser (customer-ui)
                 │  HTTP
                 ▼
           claims-agent
                 │  A2A (JSON-RPC over HTTP)
                 ▼
         settlement-agent ─────────────► api.anthropic.com
                 │                        (Cilium denies direct egress;
                 │  MCP tools/call         the gateway is the only path)
                 ▼
       ┌──────────────────┐
       │   agentgateway   │  ExtMCP (gRPC)   ┌──────────────────┐
       │ (the choke point)│ ───────────────► │  Policy Decision │
       │                  │ ◄─────────────── │     Service      │
       └──────────────────┘  permit / deny   └──────────────────┘
                 │                                    │
                 │  forwarded only on permit          │ emits
                 ▼                                    ▼
           payments-mcp                      policy.decision span
                 │  HTTP                     (same trace as the model's
                 ▼                            reasoning — see §6)
           payments-api

  SPIRE   issues SPIFFE SVIDs to agentgateway, the PDS and the agents.
          The caller's SVID is a required input to every decision.
```

The gateway sits in the MCP path. On every `tools/call` it consults the PDS before forwarding.
On deny, the call never reaches `payments-mcp`.

### The decisive property: both endpoints stay unmodified

`settlement-agent` and `payments-mcp` are not changed to accommodate policy. This is the whole
argument. If the tool checks policy itself, the *application* is deciding and a reviewer can
fairly call it an if-statement with extra steps. If the agent checks, then an agent that can
choose to ask permission can choose not to. Only interposition makes *"the platform decides"*
literally true.

One exception, forced by a measured fact rather than by design: `mcp_tools.py` must learn to
turn a denial into a terminal tool result. See §5.

---

## 3. Components

| Component | Role in M2 | Notes |
|---|---|---|
| **agentgateway** | The choke point. Proxies MCP `tools/call`, calls the PDS, forwards or refuses. | **Linux Foundation project** (not CNCF — an earlier draft of this project's notes had that wrong). ~11.2 MiB idle. Install is a Docker image plus YAML config. |
| **Policy Decision Service (PDS)** | Answers permit/deny on (caller identity, tool, amount). Ours to build. | Emits the `policy.decision` span — see §6. |
| **SPIFFE/SPIRE** | Issues workload identity. The caller's SVID is a required PDS input. | agentgateway needs an **external** Workload API; it does not bring its own issuer. It identifies callers via JWT/JWKS or mTLS SPIFFE SVID. |
| **cert-manager** | Certificate plumbing where SPIRE requires it. | Included only to the extent SPIRE actually needs it; not a feature in its own right. |
| **Cilium** | DNS-aware egress denial (`toFQDNs`), blocking direct model-provider endpoints. | Forces a **cluster recreate**; see §7. |

**Deferred to a later milestone: Kyverno.** It governs what gets deployed, not what a running
agent may do, so it is off the path for this milestone's line.

### A documentation note, learned the hard way

agentgateway's documentation site was found thin and unreliable during the spike. The
`examples/` directory in its GitHub repository was the trustworthy source and matched real
behaviour exactly. Implementation should treat the repo, not the docs site, as authoritative.

---

## 4. The policy

**Rule shape:**

> `settlement-agent`, at autonomy level `assisted`, may execute payments up to **€6,000**.

**Inputs:** caller SPIFFE identity, tool name, requested amount.

**Why identity is an input rather than decoration.** With amount alone the rule still denies on
stage, but SPIRE becomes scenery and M5 has no contract to change. Making the SPIFFE ID
load-bearing means identity does real work in the decision.

**Why the autonomy level is named now and constant now.** M5's line is *"We didn't change a
prompt. We changed a contract."* If the rule already reads *"this agent, at this autonomy
level, may execute up to X"*, then M5 flips one value and the sentence is true. If the rule is
just a threshold, M5 needs a redesign to earn its own line. The level is a constant in M2 and
becomes an OpenFeature/flagd flag in M5.

**Why €6,000.** M4's line is *"Can we prove why €5,800 was paid?"*, and M2 denies €6,200, so
the threshold sits between them. €6,000 is the round number in that gap and keeps both
milestones' fixtures coherent.

**Deliberately excluded:** claim type, customer tier, fraud signals. More realistic, but more
fixtures to build and more than an audience can absorb in the seconds available. Human approval
state is also excluded — that is M3's line and merging it here costs M3 its own moment.

---

## 5. The denial path

The model must **narrate** the denial: *"I proposed €6,200 but I'm not authorised to execute
above €6,000."* The moment is an agent visibly respecting a constraint it cannot bypass.

### The hazard, measured and load-bearing

The spike observed that a denied call returns **HTTP 400 carrying a JSON-RPC error**, and the
pinned `mcp==1.9.4` client raises a non-2xx response as a **transport exception**, not as a
clean `isError` result. `settlement-agent`'s current exception unwrapping does not handle that
path.

So the denial does not arrive as a tidy tool result for free. `mcp_tools.py` needs a deliberate
branch that recognises a policy denial and converts it into a **structured, explicitly
non-retryable** tool result.

**This must not be retryable.** PydanticAI hands tool results back to the model. If a denial
reads as a retryable tool error, the agent can sit on stage retrying a payment it will never be
allowed to make. The implementation gets an explicit test for *the absence of a retry loop*,
not merely for the presence of a deny.

**Caveat on the evidence:** the HTTP 400 shape was observed against agentgateway's **native CEL
`mcpAuthorization`** mechanism, which returned `{"code":-32602,"message":"Unknown tool:
execute_payment"}`. The `ExtMCP` deny shape was **not exercised live** — it was confirmed from
the proto contract and documentation only. Establishing ExtMCP's actual on-the-wire deny
response is therefore the **first implementation task**, not an assumption this spec rests on.

---

## 6. Telemetry — where this milestone earns its keep

`policy.decision` is **Gap 3** in `docs/telemetry-contract.md`: no OpenTelemetry convention
exists for a policy or autonomy decision span. It is ours to define, and M2 defines it.

The PDS emits its own span carrying, at minimum: the decision, the rule that fired, the caller
identity, the requested amount, and the autonomy level in force. Exact attribute names are to
be measured and recorded in the telemetry contract as they are implemented — **not** predicted
in this spec. This project's standing constraint applies with full force:

> *"You are not allowed to invent telemetry for the tools we are using. I want this to be as
> real as possible. Of course of our custom components, we can add custom telemetry, but we
> should use frameworks and output the real telemetry."*

The PDS is our own component, so custom attributes are legitimate there. agentgateway's spans
are its own and must be recorded as they actually appear.

### The trace survives the gateway — measured, not assumed

The spike verified end to end that inserting agentgateway does **not** fragment the trace:

```
caller → agentgateway "tools/call"
       → agentgateway "tools/call payments-mcp_propose_payment"
       → payments-mcp "tools/call propose_payment"
       → payments-mcp "POST"
       → payments-api "POST /payments/propose"
```

One trace, correctly parented, gateway as a real node in the middle.

**Why this was in doubt, and the correction worth remembering.** A first spike run concluded the
trace *fragments*. That conclusion was wrong, and the reason is instructive: the test client sent
`traceparent` only in `params._meta`, following `mcp_tools.py`'s docstring — *"Nothing is injected
into HTTP headers by hand."* The operative words are **by hand**. `HTTPXClientInstrumentor` is
active and `streamablehttp_client` runs on httpx, so the real client's MCP POSTs carry a
`traceparent` header automatically. Verified directly against an echo server rather than inferred
from documentation. With a faithful client the trace holds.

**The residual true finding, narrower than the original claim:** agentgateway reads trace context
from HTTP headers only, never from `params._meta`, and mints a new trace root when no header is
present. Its SEP-414 `_meta` stamping is one-directional — write-out only, built for stdio targets
that have no HTTP headers — and it unconditionally overwrites any existing `_meta.traceparent`.
This would fragment the trace for a header-less MCP client (pure stdio transport, or a client
library that does not auto-instrument its HTTP layer). It does not affect `settlement-agent` as
built. **This belongs in `docs/upstream-findings.md` as Finding 6**, held locally, not filed —
consistent with this project's standing decision on upstream contributions.

### The payoff screen

Because the gateway joins the trace, the PDS's decision span sits in the **same trace** as the
model's reasoning and the tool call it refused. One screen shows *the model asked for €6,200*
and *the platform said no*, adjacent. That is the talk's thesis in a single view.

---

## 7. Cilium and the cluster recreate

Cilium provides DNS-aware egress denial via `toFQDNs`, blocking direct model-provider endpoints
so the only path to a model is through the gateway. The M1 spec already anticipated this:

> *"What the demo needs is DNS-aware egress denial (`toFQDNs` blocking direct model-provider
> endpoints), which is ordinary `CiliumNetworkPolicy` and works with kube-proxy in place."*

Cilium's kube-proxy replacement is **not** required, which avoids the cgroup-v2, kernel-version
and API-server chicken-and-egg problems. `disableDefaultCNI: true` is a **create-time** kind
flag, so stage 2 needs a new cluster from a new `deploy/kind/stage2.yaml`, not an upgrade.

The five-namespace split adopted in M1 (`northstar-agents`, `northstar-tools`,
`northstar-systems`, `northstar-platform`, `observability`) exists precisely to make this egress
story legible.

### Sequencing requirement

**Cilium lands first, as its own isolated step, with the full trace re-verified against the M1.2
baseline immediately afterwards.** A new CNI underneath the exact property M1.2 spent eight
tasks defending is the single largest regression risk in this milestone. This is the same
isolation discipline that made M1.2's `mcp` 1.9.4 → 1.28.1 bump safe: change one thing, prove
the trace, then move. If the trace breaks, there is exactly one suspect.

---

## 8. Implementation sequence

The order is chosen so that each step has one suspect if it fails.

1. **Cilium + cluster recreate.** New `stage2.yaml`, `disableDefaultCNI`. Re-verify the full
   M1.2 trace against its recorded baseline before anything else is added.
2. **Establish ExtMCP's real deny shape.** Run the hook live, observe the actual on-the-wire
   response. Everything in §5 downstream depends on this being measured rather than assumed.
3. **agentgateway in the MCP path**, permitting everything. Re-verify the trace with the gateway
   interposed — the spike proved this works with a faithful client, so this step confirms it in
   the deployed system rather than discovering it.
4. **SPIRE**, issuing identity to gateway, PDS and agents.
5. **The PDS**, with the rule from §4 and the `policy.decision` span from §6.
6. **Wire ExtMCP to the PDS.** The deny becomes real.
7. **The terminal-denial path in `mcp_tools.py`**, with the anti-retry-loop test from §5.
8. **Cilium `toFQDNs` egress policy** as the second defence.

---

## 9. Exit criteria

1. `CLM-1842` submitted in the browser: the model proposes €6,200, requests execution, and the
   platform **denies** it. No payment is executed.
2. The model **narrates** the denial to the customer in its own words, and does **not** retry —
   asserted by a test that would fail on a retry loop.
3. **One trace**, one browser-owned root, zero orphans, containing the model's reasoning, the
   refused tool call, the gateway, and the PDS's decision span.
4. `settlement-agent` and `payments-mcp` contain no policy logic; the only change to either is
   `mcp_tools.py`'s denial-to-terminal-result branch.

   > **Factual correction, M2.2 Task 8 — the first clause held, the second did not.**
   > Five production files changed across the two components, not one:
   > `agents/settlement-agent/{executor,mcp_tools,settlement}.py` and
   > `tools/payments-mcp/{payments_client,server}.py` (measured with
   > `git diff --name-only 83cced5..50fac07` over both component trees,
   > excluding `tests/`). Four of the five come from unplanned task D1 and its
   > follow-up, and D1's justification is the reason they were unavoidable:
   > **a policy hook can only judge what is in the request.** The execute step
   > did not carry the amount, so `payments-mcp` had to advertise `amount` as a
   > *required* tool input and pass it on, and `settlement-agent` had to thread
   > the model's chosen amount into the `tools/call` arguments dict — the only
   > thing the hook sees. The fifth, `executor.py`, returns a refusal as a
   > normal A2A reply rather than letting it become a JSON-RPC error envelope.
   >
   > None of that is policy logic, and the criterion's real claim is intact:
   > the *decision* lives only in the Policy Decision Service, and
   > `tests/e2e/test_policy_denial.py`'s criterion-4 test enforces that
   > structurally — no threshold literal, no money comparison and no policy
   > vocabulary in either component, over an explicit allowlist of which
   > comparisons may exist and why. What this sentence got wrong is the *size*
   > of the change, not its shape. It is left standing with this note rather
   > than rewritten: it is what was believed at design time, and the distance
   > between that and what shipped is the useful part of the record.
5. The caller's SPIFFE identity is a **required** input to the decision — demonstrated by the
   decision changing when the identity changes.
6. A payment **below** the threshold still executes end to end, proving the platform permits as
   well as refuses.
7. Direct egress from `northstar-agents` to the model provider is **denied** by Cilium, with the
   gateway as the only path.

   > **Correction, M2.2 fix round 2026-09-10 (ruling R43) — documented, not implemented.
   > This criterion as written is NOT met, and this note is the record of that.**
   > What shipped: `deploy/platform/cilium-policies.yaml` explicitly **allows**
   > `settlement-agent → api.anthropic.com:443` by name (`toFQDNs.matchName`,
   > one host, port 443, TCP). What *is* enforced, and asserted in the datapath
   > by `tests/e2e/test_egress_policy.py` rather than argued: exactly **one**
   > of the namespace's three workloads may reach the model provider at all —
   > `claims-agent` and `customer-ui` are admitted to no external identity in
   > their BPF policy maps, and both were measured reaching it before Task 7 —
   > and no workload in the namespace may reach any other external host, which
   > is asserted against two non-provider hosts on 443. So the shipped property
   > is "one workload, not the namespace", not "denied".
   >
   > **What defers the criterion's own sentence.** "With the gateway as the only
   > path" is not architecturally available in M2: agentgateway is deployed here
   > as an MCP proxy on 3000 and carries no model traffic — `grep -ri anthropic
   > deploy/platform/agentgateway/` returns nothing, and the model id and the
   > API key both live on the agent (`deploy/k8s/settlement-agent.yaml`), not on
   > the gateway. Denying the direct hop today would
   > therefore leave no path to the model at all and no working demo. Meeting it
   > literally requires routing the agent's model calls through agentgateway's
   > own LLM path, which is a configuration this milestone does not build and
   > has not measured.
   >
   > **To which milestone.** Deferred past M2 and **not yet owned by any spec**:
   > §10 assigns M3 the reviewer UI and Dapr Workflow and no M3 spec exists at
   > this date. It belongs in the M3 spec as carried debt — the same treatment
   > §11 gives M1.2's — and the criterion should be restated there as the
   > "one workload, not the namespace" property it actually is, or the LLM route
   > built so the original wording becomes true. Left standing here with this
   > note rather than rewritten, for the same reason criterion 4's note gives:
   > the distance between what was specified and what shipped is the useful part
   > of the record.
8. Every telemetry attribute published in the contract was measured, not predicted.
9. The full gate passes with browser tests enabled.

---

## 10. Out of scope

Kyverno; the M1.3 fan-out to four agents, MCP servers and systems of record; Dapr Workflow and
the reviewer UI (M3); OpenSearch, Prometheus and evals (M4); OpenFeature/flagd autonomy
profiles (M5); Crossplane, Argo CD and Backstage (M6). Linkerd remains deferred with its own
attached experiment — whether a service mesh's telemetry joins or fragments this trace.

**Corrected 2026-09-15 (M6 Task 1), M6 clause only:** M6 is Backstage over the
`FeatureFlag` resource M5 introduced, not Crossplane/Argo CD/Backstage —
M5's own spec dropped the first two (`docs/superpowers/specs/
2026-09-15-northstar-m6-golden-path-design.md` §1). Left as written above per
this repo's standing convention for historical specs; not rewritten.

---

## 11. Carried debt from M1.2, to be cleared early

These were recorded at M1.2's close as non-blocking. Two of them let a defect class recur
silently and should be fixed near the start of M2 rather than at its final gate.

- **No service has an advisory gate.** M1.2 cleared seven advisories, but nothing prevents the
  same class returning unnoticed. `agents/settlement-agent/tests/test_pins.py` now pins
  `pytest`/`pytest-asyncio` versions via `importlib.metadata`; that pattern needs extending.
- **`claims-agent` and `payments-mcp` do not pin full transitive closures.** Two of four
  requirements files meet the house "nothing floats" rule.

  > **Correction, M3 Task 1 (2026-09-10) — cleared during M2, never struck off.**
  > M2.1 Task 6 pinned both files' full closures, citing this very list, and left the entry
  > standing. It was then copied into M3's spec and plan as outstanding work. Verified before
  > retiring it: every non-comment line carries `==`, and each file's installed closure diffs
  > clean against its own venv in both directions. Nothing here remains to do.

- **Ruling 5's single-asyncio-style decision was recorded and never applied**; both styles
  coexist in the test suites.

  > **Cleared, M3 Task 1 (2026-09-10).** 40 async tests: 18 carried an explicit
  > `@pytest.mark.asyncio`, 22 were bare under `asyncio_mode = auto`. Bare was dominant, so the
  > 18 decorators were removed. No test's meaning changed.
- **OQ-1's content-capture experiment remains unrun** — the observability-versus-confidentiality
  trade-off recorded, deliberately, as an open question rather than a resolved one.

---

## 12. Evidence this spec rests on

Recorded so a later reader can tell what was measured from what was reasoned.

**Measured during the 2026-09-09 agentgateway spike:**
- An `HTTPXClientInstrumentor`-instrumented request carries a `traceparent` HTTP header with no
  manual injection — verified against an echo server.
- The single trace survives agentgateway with a faithful client — verified in Jaeger, full parent
  chain recorded in §6.
- agentgateway's native CEL `mcpAuthorization` denies a `tools/call`, returning HTTP 400 with a
  JSON-RPC error body.
- agentgateway idles at ~11.2 MiB; install is a Docker image plus YAML matching the repo's
  `examples/`.

**Established from source or proto contract, not run live:**
- `ExtMCP` exists as a gRPC hook (`crates/protos/proto/ext_mcp.proto`), modelled on Envoy's
  ext_authz but MCP-method-aware, carrying tool name and raw arguments.
- agentgateway's ingress trace-context extraction is HTTP-header-only.
- agentgateway requires an external SPIFFE Workload API and identifies callers via JWT/JWKS or
  mTLS SPIFFE SVID.

**Corrected during the spike:** agentgateway is a Linux Foundation project; an earlier note in
this project described it as CNCF, which was wrong.

**Not yet established, and gating §5:** the on-the-wire shape of an `ExtMCP` deny.
