# Northstar — Demo Runbook (M9, Weaver registry, on M8's Human in the loop)

Status: M9 (Weaver registry), on top of M8 (Human in the loop). The five-minute flow reads Jaeger, and every telemetry name this runbook quotes is `northstar.*`, defined in `telemetry/registry/`. This is the **operator's** document: how the demo is
brought up, how it is run in front of an audience, what to say at each beat,
and what to say when something breaks.

> **M8 note.** `senior-settlement-agent` no longer exists, and the autonomy dial
> is maker-checker: `assisted` lets the agent release nothing (limit EUR 0),
> `autonomous` lets it release up to its limit. Above its limit, **a human
> releases the payment** from the reviewer-ui, under the reviewer-ui's own
> identity, through the gateway. The five-minute flow below is written for that.
> The longer beats further down were written for M7's amounts; Beat 4 is
> rewritten, the others are marked where they differ.

It is deliberately **not** a second copy of the architecture. Where this
document says *what to do*, [`docs/architecture.md`](architecture.md) says
*why it works*, at span-level detail, in its "See it yourself" section. When
the two disagree, architecture.md is right and this file is stale — it is the
newer document and the one with fewer guards on it.

### About the numbers in this document

This repo's standing rule is **derive figures; never retype them**. So this
runbook prints almost none. Where a count or a size matters, you get the
**command that produces it live** and, where a figure is already published
elsewhere, a pointer to the one place that owns it. A number you read on
stage should have come off the cluster in front of you, not out of a
markdown file written last week.

---

## 1. The story in one screen

> A customer files a vehicle-damage claim. Agents collaborate to settle it.
> The **platform** — not the agents — decides whether they may, whether a
> human must approve, records what happened, scores how well the trajectory
> followed policy, and holds the dial that says how much autonomy the system
> currently has.

Everything is built from open standards and existing open source. There is no
proprietary "AI platform" component anywhere in it.

<pre class="mermaid">
flowchart TD
    s1["`**1 · Connected**
      “they can talk to each other”`"]
    s2["`**2 · Governed**
      “the platform decides, not the agent”`"]
    s3["`**3 · Supervised**
      “a refusal is not the end”`"]
    s4["`**4 · Observable**
      “prove why €5,800 was paid”`"]
    s5["`**5 · Progressive autonomy**
      “one live value changes what
      the platform is allowed to do”`"]
    s6["`**6 · Golden path**
      “the resource IS the platform API”`"]
    s7["`**7 · Bounded**
      “the agent can never edit
      what constrains it”`"]

    r1(["one trace exists"])
    r2(["identity is an INPUT to the decision"])
    r3(["a human can overturn"])
    r4(["from telemetry alone, no new app code"])
    r5(["no redeploy, no restart, no rebuild"])
    r6(["Backstage is just the view onto it"])
    r7(["a policy, not a permission, holds the line"])

    s1 --> s2 --> s3 --> s4 --> s5 --> s6 --> s7
    s1 -.-> r1
    s2 -.-> r2
    s3 -.-> r3
    s4 -.-> r4
    s5 -.-> r5
    s6 -.-> r6
    s7 -.-> r7
</pre>

**The closing line the whole demo is built to earn:**

> *The platform API is the resource. Backstage is just the view a human uses
> to read and change it.*

Stage 7 adds the line that makes that safe to say out loud:

> *The agent can never edit what constrains it.*

The idea, and the way it is organised, come from William Rizzo's *Bounded
Agent Autonomy* demo (OSFF 2026). He draws an agent's limits as rings, each
answering one question, and a keystone that holds the rings in place. Northstar
had three of the four; M7 adds the keystone and makes Reach a live beat.

| Ring | Question | Northstar's answer | Where it is shown |
|---|---|---|---|
| Core · Identity | who is it | SPIRE JWT-SVIDs, verified by the gateway on every call | beat 3 |
| Ring 1 · Reach | what can it connect to | Linkerd (ingress, by workload identity) and NetworkPolicy (egress): the gateway is the only way into `payments-mcp` | **Beat R** |
| Ring 2 · Capability | what may it do | agentgateway → policy-decision-service, fail-closed; the autonomy flag | beats 1, 4, 7 |
| **Keystone** | can it edit what constrains it | Kyverno: **no** | **Beat K** |

---

## 2. The system at a glance (stage 7)

<pre class="mermaid">
flowchart TD
    subgraph money["THE MONEY HOP"]
        br["`browser · TS
      :8080`"] -->|A2A| ca["`claims-agent
      Python`"]
        ca -->|A2A| sa["`settlement-agent
      Python`"]
        sa -->|MCP| gw["`agentgateway
      Rust`"]
        gw -->|"`ExtMCP/gRPC — permit or deny,
      BEFORE forwarding`"| pds["`policy-decision-service
      Python`"]
        pds -->|"resolves autonomy level per request"| fd["`flagd
      NATIVE SIDECAR in the PDS pod
      localhost:8013`"]
        gw -->|permit| pm["`payments-mcp
      Python`"]
        pm -->|"HTTP/JSON"| pa["`payments-api · Go
      money moves here`"]
    end

    ff[["`**THE CONTRACT**
      FeatureFlag/northstar-autonomy
      a Kubernetes object`"]]
    fd -->|reads| ff

    subgraph humanhop["THE HUMAN HOP · stage 3"]
        hb["`browser · human
      :8081`"] --> ru["`reviewer-ui
      Python`"]
    end
    ru -->|"MCP — execute_payment under its own identity, the same gateway"| gw

    subgraph evidence["THE EVIDENCE PLANE · stage 4"]
        svc["every service"] -->|OTLP| col["`OpenTelemetry Collector
      OTLP ingest ONLY — all 10 default
      scrape jobs are disabled`"]
        col --> jg["Jaeger :16686"]
        col --> os["OpenSearch :5601"]
        col --> pr["Prometheus :9090"]
        col --> ae["`AgentEvals :18001
      scores the agent's own spans`"]
    end

    subgraph view["THE VIEW · stage 6"]
        bs["`Backstage :7007
      catalog, 8 Components
      + the Autonomy contract card`"]
    end

    subgraph keystone["THE KEYSTONE · stage 7"]
        kv["`Kyverno ValidatingPolicy
      agents-cannot-edit-their-own-authority
      failurePolicy: Fail`"]
    end
    kv -.->|"`denies an agent identity's write to
      the flag, the gateway config, the reach
      policies, RBAC and Kyverno itself`"| ff
    bs -->|"`ServiceAccount backstage —
      get/list/watch only`"| ff
    bs -->|"`ServiceAccount backstage-autonomy-writer —
      get/list/PATCH, resourceNames: [northstar-autonomy],
      no more`"| ff
</pre>

Underneath all of it: **Linkerd** (ingress authorized by workload identity) and **NetworkPolicy** on kind's kindnet (egress) · **SPIFFE/SPIRE** (workload
identity, trust domain `northstar.local`) — every MCP call carries a
SPIRE-minted JWT-SVID the gateway verifies before anything else happens.

### The claims the whole demo runs on

| | Claim | Amount | Agent's limit | What happens |
|---|---|---|---|---|
| **A** | `CLM-1842` | **€5,800** at `assisted` | €0 | **waits** for a human; the human approves and it settles |
| **B** | `CLM-1842` | **€5,800** at `autonomous` | €6,000 | **settled** by the agent, no human |
| **C** | `CLM-1842` | **€6,200** at `autonomous` | €6,000 | **waits** for a human: the band widened, the human stayed |

Same claim id, same shape. **Showing both outcomes is mandatory**: a platform
that sent everything to a human would look identical if you only ran A.

Then a payment the agent could not release is made to succeed **three different
ways** (this is the longer-slot version, kept from M7), and each one is a
different milestone's point:

<pre class="mermaid">
flowchart LR
    r(["€6,200 refused"])
    r -->|"stage 3"| a["`approved by a human on the reviewer UI
      different **IDENTITY**:
      reviewer-ui, rule human-approved.execute`"]
    r -->|"stage 5"| b["`contract flipped to autonomous by kubectl
      same identity,
      different **LIMIT TABLE ROW**`"]
    r -->|"stage 6"| c["`contract flipped to autonomous in Backstage
      identical to the above —
      and that is the point`"]
</pre>

---

## 3. What you can choose — the options

### Option 1: which stage to bring up

`./scripts/setup-kind.sh --stage N`. Each stage installs **only** what it
needs, so the laptop runs one stage at a time. Stage N always includes
everything below it.

| Stage | Name | Adds | Run this stage if the talk is about… |
|:---:|---|---|---|
| 1 | Connected | agents, MCP servers, systems of record, customer UI, collector, Jaeger | distributed tracing across an agent chain |
| 2 | Governed | Linkerd, NetworkPolicy, SPIFFE/SPIRE, agentgateway, Policy Decision Service | authorization and workload identity |
| 3 | Supervised | reviewer-ui | human-in-the-loop |
| 4 | Observable | OpenSearch + Dashboards, Prometheus, Weaver live-check, AgentEvals | evidence and evaluation |
| 5 | Progressive autonomy | cert-manager, Open Feature Operator, flagd sidecar, the `FeatureFlag` | policy as a live value |
| 6 | Golden path | Backstage | the platform API and its UI |
| 7 | **Bounded** | **Kyverno and the keystone policy** | **that an agent cannot edit its own limits — run this one** |

> **Default: `--stage 7`.** It is the complete demo. Lower stages exist for
> shorter talks and for isolating one idea.

**Two things about stages that will bite you:**

1. **A cluster built before M12 runs Cilium and must be recreated.** Until
   M12, stages 2–7 used `networking.disableDefaultCNI`, a *create-time* kind
   flag, and Cilium owned the dataplane. Since M12 the CNI is kind's default
   (kindnet, which enforces NetworkPolicy) and Linkerd goes on top, so stage 2
   no longer needs a different cluster from stage 1. But a stage >= 2 run on an
   old Cilium cluster is refused by the script (the `kube-system/cilium`
   DaemonSet is the marker): `kind delete cluster --name northstar`, then
   `./scripts/setup-kind.sh --stage 7`. Nothing in M12 has run on the demo
   cluster yet; that recreate is pending.
2. Stages **4, 5, 6 and 7 all reuse stage 3's cluster config unchanged** —
   there is deliberately no `stage4.yaml`, `stage5.yaml`, `stage6.yaml` or
   `stage7.yaml`. So **3 → 4 → 5 → 6 → 7 needs no recreate** (and, on a post-M12 cluster, neither does 1 → 2). You can climb the
   ladder live. (Stage 7 on top of a running stage 4 cluster was how this one
   was brought up the first time, and is the supported path.)

### Option 2: how long you have

| Length | Stage | Beats to run | Cut |
|---|:---:|---|---|
| **5 min** | 7 | §5 "The five-minute flow": `scripts/demo` steps 1–5 | everything else: the longer beats below it |
| **20 min** | 7 | beats 1–3, R, 6, 7, K | the evidence view and the scorer; Beat R and K are a minute each |
| **45 min** | 7 | all of §5 | nothing |
| deep-dive | 7 | all of §5 + architecture.md's "See it yourself" | — |

### Option 3: environment switches

| Variable / flag | Default | What it changes |
|---|---|---|
| `ANTHROPIC_API_KEY` | **required** | The settlement decision is a **real model call**. Empty → every pod still comes up Running and fully traced, and the **first claim submission fails**. `setup-kind.sh` prints `SET`/`EMPTY` — **check for SET before going on stage.** |
| `DASH0_AUTH_TOKEN` + 3 siblings | unset | Adds a second OTLP exporter. **No application code changes.** Good beat if the audience cares about vendor-neutrality. |
| `NORTHSTAR_CAPTURE_CONTENT` | `true` | `false` → the evidence view loses **nothing** (every field it reads is emitted by the platform, not the model). What is lost is the agent's own statement of the amount. This is OQ-1, measured as a matched pair. |
| `RUN_BROWSER_TESTS=1` | unset | Unlocks the gated test modules (`docs/telemetry-contract.md` publishes how many) that spend model tokens or drive a browser, `tests/e2e/test_demo_flow.py` among them. **Only for the gate**, not for the demo. |
| `"emit": false` | — | On `POST /score`: read the scores **without writing spans**. How to look without changing what you are looking at. |

### Option 4: the four ways to flip the autonomy contract

| Route | Command / click | Use it for |
|---|---|---|
| `scripts/demo autonomy <assisted\|autonomous>` | one command, verifies flagd resolved it and that no pod restarted | **the five-minute flow** (step 3) |
| Backstage card | http://localhost:7007/autonomy-contract → **Set settlement-agent: autonomous** | the longer slot (Beat 7): it is the milestone |
| `kubectl` | `kubectl -n northstar-platform patch featureflag northstar-autonomy --type=json ...` | proving the card is not special |
| the test | `pytest tests/e2e/test_progressive_autonomy.py` | proving it mechanically, restores itself |

---

## 4. Pre-flight — do this before the audience arrives

Budget: **one full cold bring-up**, plus time to run both claims once. Do not
time this for the first time on stage.

### T-60 — one-time, per clone

```bash
cp .env.template .env
# edit .env: ANTHROPIC_API_KEY is REQUIRED.
```

```bash
# root venv + browser driver
uv venv --python 3.13 .venv \
  && uv pip install -r tests/requirements.txt \
  && .venv/bin/playwright install chromium

# one venv per Python service
for svc in tools/payments-mcp agents/settlement-agent agents/claims-agent \
           platform/policy-decision-service apps/reviewer-ui apps/customer-ui/server; do
  (cd "$svc" && uv venv --python 3.13 .venv && uv pip install -r requirements.txt)
done

# the gRPC stubs are git-ignored on purpose — generate them once
(cd platform/policy-decision-service && ./gen-stubs.sh)
```

> **Never install into an existing service venv.** Their contents are pinned
> and asserted by `test_pins.py` in each service. If one is wrong, delete it
> and recreate it from `requirements.txt`.

### T-30 — bring the cluster up

```bash
./scripts/setup-kind.sh --stage 7
```

Watch for, at the end of the run:

- [ ] `ANTHROPIC_API_KEY: SET` — **if this says EMPTY, stop.** Every pod will
      come up Running and the first claim will fail at the model call.
- [ ] every component reporting installed, no rollout timeouts
- [ ] `The keystone policy is Ready` — the script waits for it and fails
      rather than printing it if the policy never arms

### T-15 — start the surfaces, and leave this terminal alone

```bash
./scripts/start-demo.sh      # owns EVERY port-forward. Ctrl+C stops them all.
```

It prints every URL it established. Confirm you see a `Backstage` line and not
`Backstage: SKIPPED` — `SKIPPED` means the cluster is below stage 6. Its first
line is the keystone pre-flight, and it must read:

```
Keystone pre-flight: policy Ready, over-grant absent.
```

It refuses to start (exit 1) if the policy is not Ready, if Kyverno's webhook
registration is missing, if nothing answers behind `svc/kyverno-svc`, or if the
beat's over-grant is still applied. The registration check matters because of a
measured failure mode: a **graceful stop** of Kyverno deletes its own webhook
configurations, after which the keystone is **silently off** (an over-granted
agent's write succeeds) while the policy object still exists. An *unreachable*
Kyverno with the webhook still registered fails closed for agents. Neither
affects Backstage's flip in beat 7: the identity match is evaluated by the API
server first, and Backstage's identity never matches. The over-grant check
matters because the over-grant is a standing permission for
`settlement-agent` to patch the flag, which is exactly what this demo says
never exists.

### T-10 — verify with your own eyes, not with a green banner

```bash
# 1. everything Running? (prints the count; do not trust a remembered number)
kubectl get pods -A --no-headers | wc -l
kubectl get pods -A --no-headers | grep -v ' Running \| Completed ' || echo "all Running"

# 2. the contract is in its RESTING, REFUSING state
kubectl -n northstar-platform get featureflag northstar-autonomy \
  -o jsonpath='{.spec.flagSpec.flags.autonomy-level.state}{"  default="}{.spec.flagSpec.flags.autonomy-level.defaultVariant}{"\n"}'
# expect: ENABLED  default=assisted

# 3. the same fact through the surface you will actually show.
#    NOTE the token. A bare curl to this route returns
#    HTTP 401 {"error":{"name":"AuthenticationError","message":"Missing credentials"}}
#    -- the backend API *does* check a Backstage credential. What makes this
#    surface unauthenticated is that anyone can mint one, unchallenged, with
#    the POST below. That distinction is the limitation stated precisely, and
#    `tests/e2e/test_golden_path.py::test_unauthenticated_is_refused_authenticated_is_not`
#    asserts both halves of it.
TOKEN=$(curl -s -X POST localhost:7007/api/auth/guest/refresh \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["backstageIdentity"]["token"])')

curl -s -H "Authorization: Bearer $TOKEN" \
  localhost:7007/api/autonomy-contract/status | python3 -m json.tool
```

Expect exactly this shape, with the identity resolving `assisted`:

```json
{ "flag": "northstar-autonomy", "namespace": "northstar-platform",
  "state": "ENABLED", "defaultVariant": "assisted",
  "perIdentity": {
    "settlement-agent": { "level": "assisted", "branch": "targeting-then" } } }
```

- [ ] Customer UI loads — http://localhost:8080
- [ ] Reviewer UI loads — http://localhost:8081
- [ ] Jaeger loads and its service dropdown is populated — http://localhost:16686
- [ ] Backstage loads and the **Guest → Enter** button works — http://localhost:7007
- [ ] Autonomy contract card reads `assisted` / `assisted`

```bash
# 4. the keystone is armed, and the beat's over-grant is NOT standing
kubectl get validatingpolicy agents-cannot-edit-their-own-authority   # READY true
kubectl -n northstar-platform get role,rolebinding keystone-demo-overgrant \
  || echo "absent, as it should be"
```

### T-5 — the rehearsal claim

**Claim EUR 5,800 once and confirm it WAITS for a human.** This costs one
model call and it is the single most valuable thing you can do in pre-flight:
it proves the API key, the whole A2A chain, the gateway, the PDS, flagd, the
ledger and the reviewer-ui, in one action.

For the five-minute flow the rehearsal is the flow itself:
`scripts/demo claim 5800 --expect awaiting_approval` (it exits non-zero unless the
claim waits for a human), `scripts/demo approve last`, then `scripts/demo reset`.

**No open approvals before the audience arrives:**

```bash
curl -s localhost:8081/api/awaiting      # expect: []
```

Then **reset the ledger** before the audience sees anything — see §7.

---

## 5. The run — beat by beat

Each beat below is: **DO** (the action), **SHOW** (where to look), **SAY**
(the point). The SAY lines are the argument; everything else is scaffolding
for them.

**The first thing in this section, "The five-minute flow", is the live demo:**
five commands in one terminal. The beats after it are the longer-slot version.

---

### The five-minute flow · *stage 7* · ~5 min · **the live demo**

This is what the talk runs live. Two browser tabs (the customer-ui, the
reviewer-ui) and one terminal; the terminal does the rest and is also the
fallback for both tabs. Design:
`docs/superpowers/specs/2026-10-13-northstar-m8-human-in-the-loop-design.md`,
section 4. Everything below it in this section is the **longer-slot** version.

| # | What | ~Time | What the audience sees | Talk point |
|---|---|---|---|---|
| 1 | `scripts/demo guard` | 45s | 401 without identity; dropped around the gateway; the tools list with identity | Identity, Reach |
| 2 | Claim EUR 5,800 in the customer-ui, approve in the reviewer-ui | 90s | "Awaiting human approval" with a timer; the reviewer sees the rule, the limit and the model's narrative; approve; the customer tab flips to "Settled" | Maker-checker; human in the loop |
| 3 | `autonomy autonomous`, `claim 5800`, `claim 6200` | 75s | EUR 5,800 settled by the agent, nothing waiting; EUR 6,200 waits (limit 6,000) | One live dial; the band widens, the human stays |
| 4 | `scripts/demo keystone` | 45s | RBAC says no; Kyverno says no | Keystone |
| 5 | `scripts/demo evidence` | 45s | For each claim: who decided, the rule, who approved, how long the human took | Prove |
| 6 | `scripts/demo evals --quick` | 30s | For each claim: the path the agent took, whether it stopped when refused, whether its narrative stated an amount, scored by AgentEvals from the agent's own traces | What the agent did, graded |

The terminal version of the whole flow:

```bash
scripts/demo guard                  # 1  identity, reach, the tools
scripts/demo claim 5800             # 2  WAITS for a human ...
scripts/demo approve last           #    ... a human releases it
scripts/demo autonomy autonomous    # 3  one live value ...
scripts/demo claim 5800             #    ... settled by the agent
scripts/demo claim 6200             #    ... EUR 6,200 still waits
scripts/demo autonomy assisted      #    ... and back
scripts/demo keystone               # 4  the agent cannot edit what constrains it
scripts/demo evidence               # 5  prove it
scripts/demo evals --quick          # 6  what the agent did, graded (seconds)
scripts/demo reset                  #    rejects open approvals, back to rest
```

`scripts/demo` is a shim over `scripts/demo.py`, run by the repo's `.venv`. It
imports the same helpers the e2e tests import (`tests/harness/live.py`,
`contract.py`, `jaeger.py`), so what you run on stage is what
`tests/e2e/test_demo_flow.py` runs in the gate. Every command says what it is
about to do, then prints the platform's answer, **exits non-zero if the
expected outcome does not happen** (a payment that settles when it should wait,
a drop that connects, a keystone that does not fire), and never prints a token.
The decisive word is highlighted (white on red for something stopped, black on
green for something that went through). It reads at a large font in a
100-column terminal.

**Before you start:** §4 pre-flight, then `scripts/demo reset` as the last thing
you type before the audience arrives. It is safe at any time. The model is
called by `claim` only; the other commands are deterministic. Time any command
yourself with `time scripts/demo <command>`: a claim is the model (a few
seconds) plus a bounded wait for every service's spans to land in Jaeger; the
flips take under a second to reach flagd.

**On stage, about trace A while a claim waits.** Trace A, the agent's work,
ends at the hand-off: the claims-agent records `input-required` and returns, so
nothing polls and trace A does not grow while the claim waits. There is nothing to
wait for before opening it. The person's work is trace B: the decision, the
release through the gateway, the resume (`northstar.a2a.resume`, with one span
link back to trace A) and the push of the outcome to the customer-ui server
(`POST /a2a/push`, service `customer-ui-server`), which the browser's wait ends on.
Only the outcome is pushed: trace A holds no push and no `customer-ui-server` span. Step 5's `trace`
link for an approved claim opens trace B. The terminal driver's trace view prints
only the road the money would take.

---

#### Step 1 — `scripts/demo guard` · Core · Identity, Ring 1 · Reach

**DO** — `scripts/demo guard`

**SHOW**

```text
1  No identity
  $ POST gateway/mcp  tools/list   (no token)
  -> HTTP [401]  authentication failure: no bearer token found
  $ POST gateway/mcp  tools/call execute_payment   (no token)
  -> HTTP [401]  nothing forwarded

2  Around the gateway
  From inside claims-agent: no SVID, no business at the tool.
  $ POST  settlement-agent:9002/  (the one peer it may call: the control)
  -> [REACHED]  200  (the network works)
  $ POST  payments-mcp:8000/mcp  initialize, straight to the tool, 8s timeout
  -> [DROPPED (timeout)]  no answer, no 401. The node dropped the packets.
  From inside reviewer-ui: it may reach payments-api, but not as the tool.
  $ POST  payments-api:8080/payments/<id>/execute  straight to the ledger, skipping gateway and PDS
  -> [REFUSED 403 by payments-api/<route> (deny counter +1)]

3  With settlement-agent's identity
  $ spire-server jwt mint   sub=settlement-agent  aud=agentgateway
    sub              spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent
    aud              spiffe://northstar.local/ns/northstar-platform/sa/agentgateway
  -> HTTP [200]  tools settlement-agent may call:
       propose_payment ...   execute_payment ...   refuse_payment ...   get_payment ...
  Listed is not permitted: every call is decided, one by one.
```

**SAY**

> "Three ways to reach the payment tool. With no identity, the gateway says
> 401 and forwards nothing. Going around the gateway, straight at the tool,
> does not get a no, it gets silence: the node drops the packet. And where a
> workload may reach the service but not do that thing, the answer is a counted
> 403 from the service's own proxy. With the identity SPIRE issued to this one
> agent, I can see the tools. All four,
> including the one that moves money. Seeing a tool is not being allowed to use
> it. That comes next."

**Land:** the 8 seconds of waiting on the DROPPED line is the point. Say so out
loud: "That wait is the drop. There is no error to read, because nothing
answered." Neither is in the trace. The egress drop is silent
(nothing counts it); the ingress 403 is counted by the proxy
(`inbound_http_authz_deny_total`) but it is a counter, not a span: the probe
sends no `traceparent`, so the proxy records no span for it. This terminal line
is the evidence, and the slide says so.
*Expected output; first live run on Linkerd pending.*

**If it fails** — `FAIL ... the control did not answer`: the network is down, not
the policy; `kubectl -n northstar-agents get pods`. `FAIL ... was not dropped`:
the NetworkPolicy is not enforcing, see §8. No SPIRE token: `kubectl -n
northstar-platform get pod spire-server-0`. Fallback: none on the deck any more (the
transcript slides left it on 2026-10-19); say what the step proves and move on.

---

#### Step 2 — a human signs · Ring 2 · Capability · maker-checker

**DO** — in the **customer tab**, file a claim for EUR 5,800. In the
**reviewer tab**, read the rule (`assisted`), the limit (EUR 0) and the model's
narrative, then approve.

**SHOW** — the customer tab says "Awaiting human approval" with a timer. The
reviewer tab lists the claim with four facts. After the approve, the customer
tab flips to "Settled" **without a reload**.

**SAY**

> "The agent did the work. A human signed. The trace shows how long the human
> took."

> "This is maker-checker. The agent is the maker: it proposed the payment and
> tried to release it. The platform said no, and it said why: a rule, a limit.
> The reviewer is the checker, and the reviewer-ui is a different service with
> a different identity. The agent cannot approve its own payment, because the
> platform reads the ledger before it lets anyone release money."

**Fallback — the terminal does both halves.**

```bash
scripts/demo claim 5800 --expect awaiting_approval
scripts/demo approve last
```

```text
CLAIM  CLM-DEMO-656406   EUR 5,800
  autonomy level   assisted
  $ A2A message/stream  claims-agent  claim CLM-DEMO-656406  EUR 5,800
  -> 6s

  Outcome  [WAITING]  awaiting human approval. Nothing was paid.
  ...the model's own words, different every run...
  Approve with:  scripts/demo approve CLM-DEMO-656406   (or: scripts/demo approve last)

The same event, as the trace saw it
  settlement-agent  execute_tool execute_payment   the model asks
  +- agentgateway  tools/call execute_payment   HTTP 200  ERROR
      HTTP 200: the no is a tool result marked isError, with the rule in its text.
    +- Guardrail   the gateway asks the decision service
    +- policy-decision-service  northstar.policy.decision  [DENY]
          rule       settlement-agent.assisted.execute_limit
          limit      0  (requested 5800)
    gateway: mcp: mcpGuardrails rejected: requested 5800 EUR exceeds the 0 EUR limit ...
  payments-api spans in this trace:
    present  POST /payments/propose   records the proposal; moves no money
    [ABSENT]   POST /payments/{paymentId}/execute   money did not move

APPROVE  CLM-DEMO-656406   EUR 5,800
  $ reviewer-ui  POST /api/review/PAY-000026  decision=approve
  Outcome  [SETTLED]  released by a human after 19s
  The customer's claim was told: [completed]
```

**Land:** the no is `HTTP 200`: a tool result marked `isError`, with the rule in
its text. A no is a decision, not an HTTP failure. And the one span that would
have moved money is absent until the human acts. Say "no execute span", not "no
payments-api span": the proposal and the refusal record are payments-api spans.

**If it fails** — `FAIL ... expected the claim to end 'awaiting_approval'`: the
contract was left `autonomous`; `scripts/demo reset`. A model or API-key error:
§8's first row. `FAIL ... no claim is waiting for a human`: the reviewer-ui's queue is
empty, so the claim already ended or `reset` rejected it. `approve last` finds
a claim filed in the customer tab too: it reads the queue, not only this run's notes. The customer tab never flips:
§8.

---

#### Step 3 — `autonomy autonomous`, `claim 5800`, `claim 6200` · one live dial

**DO**

```bash
scripts/demo autonomy autonomous
scripts/demo claim 5800
scripts/demo claim 6200
scripts/demo autonomy assisted
```

**SHOW**

```text
  -> flagd resolves for settlement-agent: [autonomous]   (0.3s to take effect)
  Pods restarted: [NONE]  (12 pods, same UIDs, same start times, same restart counts)
```

then, for EUR 5,800:

```text
  Outcome  [SETTLED]  by the agent, inside its band. The payment was made.
    +- policy-decision-service  northstar.policy.decision  [PERMIT]
          rule       settlement-agent.autonomous.execute_limit
          limit      6000  (requested 5800)
    [PRESENT]  POST /payments/{paymentId}/execute   money moved
```

and for EUR 6,200:

```text
  Outcome  [WAITING]  awaiting human approval. Nothing was paid.
    +- policy-decision-service  northstar.policy.decision  [DENY]
          rule       settlement-agent.autonomous.execute_limit
          limit      6000  (requested 6200)
    [ABSENT]   POST /payments/{paymentId}/execute   money did not move
```

The EUR 6,200 claim is left waiting on purpose; `scripts/demo reset` (or
`scripts/demo reject last`) ends it.

**SAY**

> "One live dial. The band widens; the human stays."

> "Same agents, same code, same image. I changed one value on one Kubernetes
> object and flagd had it in under a second. Nothing restarted, and the command
> counts the pods to prove it. Now EUR 5,800 settles without a human, because
> it is inside the band. EUR 6,200 is outside it, so it waits. More autonomy
> did not remove the human. It moved the line."

**Land:** the line that matters is `Pods restarted: NONE`, compared by UID,
start time and restart count before and after, and the e2e test makes the same
comparison independently. The limit on screen is read off the decision span.

**If it fails** — `FAIL ... something restarted when only the value changed`:
believe it and look at `kubectl -n northstar-agents get pods`; a rollout
overlapped the flip. `FAIL ... flagd resolves 'assisted'`: the sync watch has
not caught up; run it again. **Never leave step 3 at `autonomous`:**
`scripts/demo reset` fixes it. Backstage's card does the same flip (Beat 7).

---

#### Step 4 — `scripts/demo keystone` · Keystone

**DO** — `scripts/demo keystone`

**SHOW**

```text
1  The agent tries to flip its own autonomy flag
    Error from server ([Forbidden]): featureflags.core.openfeature.dev "northstar-autonomy" is forbidden: User "system:serviceaccount:northstar-agents:settlement-agent" cannot get resource ...
  -> [RBAC says no]  That is the first line.

2  Someone makes a mistake: the agent is over-granted patch on that one flag
    role.rbac.authorization.k8s.io/keystone-demo-overgrant created
    rolebinding.rbac.authorization.k8s.io/keystone-demo-overgrant created
  -> yes   RBAC would now allow it

3  The same patch again. RBAC allows it. Kyverno does not.
    Error from server: admission webhook "vpol.validate.kyverno.svc-fail-finegrained-agents-cannot-edit-their-own-authority" denied the request: Policy agents-cannot-edit-their-own-authority failed: [DENIED]: agent identity 'system:serviceaccount:northstar-agents:settlement-agent' may not change the objects that define its own authority (FeatureFlag/northstar-autonomy). Authority changes go through a reviewed commit, never a runtime decision by the agent.
  -> [DENIED] by Kyverno, naming the policy. The second line.

4  Take the mistake back
    role.rbac.authorization.k8s.io "keystone-demo-overgrant" deleted
    rolebinding.rbac.authorization.k8s.io "keystone-demo-overgrant" deleted

5  What does the flag resolve to now?
  -> settlement-agent resolves: [assisted]
```

**SAY**

> "The agent can never edit what constrains it. Its own identity tries to flip its
> own flag: RBAC says no. That is the first line, and RBAC is usually right. So I
> make RBAC wrong, on purpose: I give it the permission, the kind of mistake that
> happens in real clusters. Same patch. This time Kyverno says no, in words
> written for a human, and it does not depend on RBAC being right. I take the
> mistake back, and the flag still says assisted."

**Land:** the first denial is RBAC's, the second is Kyverno's, and the script
asserts the policy name is in the message so an RBAC denial can never pass for
the keystone. The over-grant is removed on every exit path, including Ctrl-C.

**If it fails** — `FAIL ... is not Ready`: stage 7 is not up or Kyverno is down
(§8). `FAIL ... over-grant is already applied`: `scripts/demo reset`.
`FAIL ... not by the keystone`: Kyverno did not fire and RBAC did; read §8's
Kyverno rows. Fallback: none on the deck any more; say what the keystone proves and move on.

---

#### Step 5 — `scripts/demo evidence` · Prove

**DO** — `scripts/demo evidence`

**SHOW** (one block per claim of **this run**, nothing older; three, in order).
This is the output of a real run (claim 5800 and approve last; autonomy
autonomous; claim 5800; claim 6200; autonomy assisted; evidence; reset), pasted
as printed, not retyped. Ids, durations and trace links differ every run:

```text
EVIDENCE  can we prove why each claim ended the way it did?
-----------------------------------------------------------
  From Jaeger, for this run's 3 claim(s). Nothing older.

CLM-DEMO-1813EA   EUR 5,800
  $ Jaeger  claim id -> traces -> the decision and review spans  (CLM-DEMO-1813EA)
    who decided      policy-decision-service   [SENT TO A HUMAN]
    rule             settlement-agent.assisted.execute_limit
    limit            0   (requested 5800)
    autonomy level   assisted
    caller identity  spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent
    outcome          [SETTLED] by reviewer-ui
    approved by      kasper.on.stage, after 17 s
    trace            http://localhost:16686/trace/dab4155d36d9122b6e7b8ec23bcf888d

CLM-DEMO-396B0F   EUR 5,800
  $ Jaeger  claim id -> traces -> the decision and review spans  (CLM-DEMO-396B0F)
    who decided      policy-decision-service   [PERMIT]
    rule             settlement-agent.autonomous.execute_limit
    limit            6000   (requested 5800)
    autonomy level   autonomous
    caller identity  spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent
    outcome          [SETTLED] by settlement-agent
    approved by      none
    trace            http://localhost:16686/trace/e9bdb5753ff245fbb908e7c1562a8102

CLM-DEMO-203115   EUR 6,200
  $ Jaeger  claim id -> traces -> the decision and review spans  (CLM-DEMO-203115)
    who decided      policy-decision-service   [SENT TO A HUMAN]
    rule             settlement-agent.autonomous.execute_limit
    limit            6000   (requested 6200)
    autonomy level   autonomous
    caller identity  spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent
    outcome          [AWAITING HUMAN APPROVAL] no execute span: nothing moved
    approved by      none
    trace            http://localhost:16686/trace/7ad45ad5aa1542519bdcea94721fadb6

Every line above is a recorded span, read back from Jaeger. Nothing is reconstructed.
```

Then open one `trace` link (the first claim's, which is the approval's own
trace) and click the `northstar.review.decision` span: its attributes are the
`approved by` and `after N s` lines above. The same span in Jaeger is the
proof; the terminal only reads it back.

**SAY**

> "For each claim: who decided, the rule, who approved, how long the human
> took."

> "Every line here is a span attribute read back from Jaeger, not something I
> told you. The policy service decided, not the model. The first claim was
> approved by a person, and the record says who and after how many seconds. The
> second the agent settled alone. The third is still waiting, and the record
> says so. And the names of those attributes are not mine to change on a
> whim: they are defined in a published schema, a Weaver registry, and the
> cluster is checked against it."

**Land:** "who approved" and "how long" come from the reviewer-ui's own span
(`northstar.review.decision`, with `northstar.review.reviewer.id` and
`northstar.review.wait.duration`, in seconds), not from the model or the
driver. The rule, the limit and the autonomy level are attributes of the
`northstar.policy.decision` span (`northstar.policy.rule`,
`northstar.policy.limit.amount`, `northstar.policy.autonomy.level`).
OpenSearch is not part of this step: `evidence` makes no request to it. It stays
in the stack for the evidence dashboard and the longer slot.

**If it fails** — it waits for Jaeger, bounded (90s), printing `waiting for
Jaeger (...)`, then fails naming the fields that never arrived: read the
collector log (`kubectl -n observability logs deploy/otel-collector | grep
'Exporting failed'`) and check the `jaeger` pod. `FAIL ... no claims in this
run yet`: you ran `reset` between the claims and this; run the claims again.
Fallback: open the claim's trace in Jaeger by hand
(service `policy-decision-service`, operation `northstar.policy.decision`).

---

#### Step 6 — `scripts/demo evals --quick` · what the agent did, graded

**DO** — `scripts/demo evals --quick`. Optionally first, if the room asks what
exactly restrains the agent: `scripts/demo config` (the lines, from the files).

**SEE** — for each claim this run filed, four lines from AgentEvals:
`took_a_known_path` (settled: propose, execute; held: propose, execute refused),
`stopped_when_refused`, `amount_executed_matches_proposed` (n/a when nothing was
executed), `narrative_states_no_amount`. PASS on every line on a
normal run. Older sessions AgentEvals still holds (the gate's test claims) are
counted and not shown.

**SAY** —
> "Evidence was the platform's side: who decided and why. This is the agent's side,
> graded by an open-source evaluator, AgentEvals, from the same OpenTelemetry the
> agent already emits. It never touched the agent. Every grade goes back into the
> telemetry as a gen_ai.evaluation.result event. And it can only see what the
> agent did, not what the platform decided, which is why we keep both."

**Land:** `--quick` runs our four deterministic checks, seconds, the same answer
every run. The full `scripts/demo evals` (minutes, off stage) adds AgentEvals'
own metrics with an LLM judge, Claude by default (`AGENTEVALS_JUDGE` overrides
it); the judge is not stable across runs (measured: 0.60, 0.40, 0.40 on one
claim), which is the point of keeping the stage checks deterministic.

**If it fails** — `FAIL ... no finished claim yet`: a session completes a few
seconds after its last span; wait, re-run. Connection refused on 18001: the
forward is dead (the pod restarted); re-run `scripts/start-demo.sh`. Fallback:
the UI at http://localhost:18001, Local Development, expand a claim.

---

#### After the flow

```bash
scripts/demo reset     # rejects open approvals as demo-reset, flag assisted,
                       # over-grant absent, run state cleared
```

`reset` is idempotent and prints what it verified (`open approvals  NONE
(rejected N)`). **Run it at the end of every rehearsal and every performance**,
and before the audience arrives.

**Two things about the reviewer-ui to say plainly if asked.** First, it has no
ingress policy and no login on `/review` and `/api/review`: any pod in the
cluster that has egress to it can approve a payment. That is out of scope for
this demo (spec section 8) and it is a limitation, not a hidden property. What
the demo does hold is the other half: the reviewer-ui is the only service the
platform lets release a human-approved payment, and it can only reach
payments-api on three verbs (list, get, reject). Second, every resume logs a
`NoTaskQueue` ERROR from a2a-sdk's cleanup in the claims-agent. It is expected
noise, not a failure: the claim completed.

---

### The longer beats · for a longer slot

Everything from here to Beat 8 is the 20-, 45- and 90-minute version of the
demo, and the only home of the surfaces the five-minute flow leaves out: the
customer UI, the human approval, the reviewer UI, the scores, OpenSearch
Dashboards and the Backstage card. They are unchanged by the five-minute flow.

**M8 caveat for Beats 1, 2, 5, 6, 7 and K:** they use M7's amounts and the
M7 limit table. Under M8 the agent's limit is EUR 0 at `assisted` and EUR 6,000
at `autonomous` (read the live values from `scripts/demo claim`'s output, not
from this file), so an EUR 6,200 claim at `assisted` **waits for a human**
rather than being refused, and a claim at `autonomous` below the limit settles.
The mechanism each beat shows is unchanged; the numbers on screen are not.

---

### Beat 1 — the refusal · *stage 1–2* · ~3 min

**DO** — http://localhost:8080. The form is pre-filled: claim `CLM-1842`,
policy `POL-77120`, **€6,200**. Click **Submit claim**.

**SHOW** — the Outcome block, within a few seconds. At `assisted` the agent may
release nothing, so the claim **waits for a human** (the customer tab says
"Awaiting human approval"). The reply it carries:

```json
{ "status": "awaiting_approval",
  "paymentId": "PAY-0000NN",
  "amount": 6200,
  "narrative": "…the model's own words, different every run…" }
```

Reject it in the reviewer tab (or `scripts/demo reject last`) before the next
step, so nothing is left waiting.

**SAY**

> "That is not a failure. That is the platform working. The agent *proposed*
> the payment — the platform said no to its own execute, and a person has to
> decide. And notice the narrative
> is different every time you run this, because it is genuinely the model
> explaining its own refusal, not a canned string."

> ⚠️ **Do not let a `waits` land as an error.** Name it as the success
> before anyone reads it as a bug. This is the most common way this demo is
> misread.

**DO** — flip the dial to `autonomous` (Beat 7's card, or `scripts/demo autonomy
autonomous`), change the amount to **5800**, submit again. It comes back
`"status": "executed"`. Flip it back to `assisted` afterwards.

**SAY**

> "Same claim, same agents, same path. At `assisted` it waits for a person. At
> `autonomous`, inside the agent's limit, it goes through. A platform that
> made everyone wait would have looked identical on the first run alone — that
> is why you always see both."

---

### Beat 2 — one trace, and the absence that proves it · ~4 min

**DO** — http://localhost:16686. Service `customer-ui`, operation
`northstar.claim.submit`, **Find Traces**.

> Jaeger's `total` field is often `0` even when traces come back. It is a
> known API quirk. Read `data`, not `total`. Ignore any `harness-probe*`
> services in the dropdown — those are the test suite's own synthetic spans.

**SHOW** — open the €6,200 trace and walk these four spans:

| Span | Service | What it proves |
|---|---|---|
| `northstar.claim.submit` | customer-ui | the trace **root** — no parent, attributes set in the browser |
| `tools/call` (SERVER) | agentgateway | `jwt.sub` = the caller's **attested** SPIFFE ID, set by the gateway from the token it verified |
| `northstar.policy.decision` | policy-decision-service | `northstar.policy.decision=deny`, `northstar.policy.rule=settlement-agent.assisted.execute_limit`, `northstar.policy.request.amount=6200`, `northstar.policy.limit.amount=0` (the agent's limit at `assisted`) |
| `Guardrail` (CLIENT) | agentgateway | `grpc.status = 0` — the *call* succeeded, the **deny is in the payload** |

**SAY**

> "Look at the status on the northstar.policy.decision span. It is **not** ERROR. A
> refusal is the platform working, and it must not be an error, or every
> dashboard you own will page you for correct behaviour."

> "And look at the gateway's Guardrail span. gRPC status zero. That is how
> 'the policy said no' stays distinguishable from 'the policy service was
> down' — which is the difference between a refusal and an outage."

**SHOW — the absence.** In the €6,200 trace there is **no** `payments-mcp`
span for `execute_payment`, and **no** `payments-api` execute span.

**SAY**

> "The execute call never reached the tool. The gateway stopped it. The
> *absence* of those spans is the evidence that nothing happened — rather
> than something happening and failing."

---

### Beat 3 — identity is an input · ~3 min

**DO** — no model call, no browser, costs nothing:

```bash
.venv/bin/python -m pytest tests/e2e/test_policy_denial.py -v -k "criterion_4 or criterion_5"
```

**SAY**

> "Criterion 4 is a hermetic walk over the source proving there is no policy
> logic in the settlement agent or the MCP server at all. Criterion 5 speaks
> MCP to the gateway directly with **two different SPIRE-minted identities**
> and shows the verdict change with the caller. That is what makes 'identity
> is an input to the decision' a demonstration instead of a claim."

---

### Beat R — the only way in · *stage 7* · ~2 min

The Reach ring has two enforcement points. **Egress** is core NetworkPolicy,
enforced in the node by kindnet: a workload with no business at the tool cannot
get a packet to it. **Ingress** is Linkerd, at the destination's proxy, by
workload identity: a workload that may reach a service but not do that thing
there gets a 403, and the proxy counts it. No model call, no browser, no cost.

**DO**

```bash
./scripts/beat-reach.sh
```

**SHOW** — *expected output; first live run on Linkerd pending (the demo cluster
still runs Cilium until it is recreated; the shape below is what the script
prints, not a recorded run).* Step 1 is the control (the same pod reaches the
one peer it exists to call); step 2 is the egress drop; step 3 is the ingress
refusal and its counter.

```
=== Beat R: the only way in ===
Egress is NetworkPolicy in the node; ingress is Linkerd, by workload identity.

1. Control: claims-agent calls settlement-agent, the one peer it exists to call
$ POST http://settlement-agent.northstar-agents.svc.cluster.local:9002/
  -> REACHED 200
  REACHED. reachable, as it should be.

2. Egress: claims-agent sends an MCP initialize straight to payments-mcp, around the gateway
$ POST http://payments-mcp.northstar-tools.svc.cluster.local:8000/mcp   (initialize, 8s timeout)
  -> DROPPED (proxy error: the node dropped the packets, this pod's proxy answered 504)
  DROPPED. Nothing answered: not a 401, not a 403. The packets never arrived.

3. Ingress: reviewer-ui calls payments-api's execute route directly, skipping the gateway and the PDS
$ POST http://payments-api.northstar-systems.svc.cluster.local:8080/payments/PAY-000000/execute
  -> REFUSED 403 by payments-api/<route> (deny counter +1)
  REFUSED. By the proxy, by identity, and counted.

Beat R holds: the gateway is the only route to payments-mcp, and only payments-mcp's identity may execute at payments-api.
(Refusals are counted at the destination proxy, not traced.)
```

The egress verdict is read above TCP. A meshed pod's own proxy accepts every
connect locally, so a connect never fails; the HTTP request does (a TLS reset or
the proxy's own 504). That is why the line says DROPPED with a reason, never
"connection refused".

**SAY**

> "`claims-agent` is a real neighbour in the agents' own namespace. It has no
> business at the payments tool. Step one: it can reach the one thing it is
> for. Step two: it tries to talk to the tool directly, around the gateway. Not
> a 401. Not a refusal. **Nothing answers.** The node dropped the packet, so
> the only way into `payments-mcp` is the gateway, and the gateway is where the
> policy decision happens. Step three is the other layer: `reviewer-ui` is
> allowed to talk to the ledger, it lists and rejects there, but its identity is
> not the one allowed to execute. Linkerd answers 403, and counts it."

**Say the limits out loud.** Neither refusal is in the trace. The egress drop
is silent: nothing counts it. The ingress 403 is counted
(`inbound_http_authz_deny_total` on the destination proxy) but it is a counter,
not a span: the probe carries no `traceparent`, and the proxies only trace
requests that do. Say also what Linkerd cost us against Cilium: egress is now an address
range (Anthropic's published `160.79.104.0/23` and `2607:6bc0::/48`) rather than
a name, so any other host in that range is also reachable from settlement-agent;
the enforcement of egress is kind's kindnet, and a production cluster needs a CNI
that enforces NetworkPolicy; a TCP connect no longer proves reach; and between meshed pods egress is per pod, not per port (a pod allowed to reach a peer's proxy on 4143 can reach any port that listens on that pod's IP; the peer's Linkerd Servers decide which ports answer, per port and per identity). The script
exits non-zero if the control fails, if the direct call is not dropped, or if
the 403 is not counted.

**Read the counter yourself** (through the API server, never a port-forward:
a port-forward bypasses the proxy):

```bash
kubectl get --raw /api/v1/namespaces/northstar-systems/pods/<payments-api-pod>:4191/proxy/metrics | grep inbound_http_authz_deny_total
```

---

### Beat 4 — the human releases it · *stage 3* · ~5 min

This is step 2 of the five-minute flow, taken slowly.

**DO** — http://localhost:8081 (a NodePort — needs no port-forward and
survives a redeploy). The payment that is waiting is listed with **four
facts**: the rule that fired, the limit the caller held, the amount
requested, and the model's own narrative. Meanwhile the customer-ui shows
"Awaiting human approval" for the same claim.

Type a reviewer id (the form supplies it; the stage uses `kasper.on.stage`)
→ **Approve**. The customer tab flips to "Settled" without a reload.

**SAY**

> "The human's information path here is the **ledger**, not telemetry. Turn
> content capture off and this page is unchanged. Telemetry is for the
> platform team; the ledger is for the reviewer."

> "There is a Reject button and it means it: the payment is recorded as
> rejected, it is final, and the customer is told their claim was declined.
> Executing a rejected payment is a 409. What there is not: a second approver,
> a timeout, an expiry, or a login. The reviewer id is a string the form
> supplies. That is written down as a limitation rather than hidden."

**SHOW** — Jaeger, service `reviewer-ui`, operation `northstar.review.decision`:

<pre class="mermaid">
flowchart LR
    subgraph A["trace A — the agent did everything up to the money"]
        a1["customer-ui → claims-agent → settlement-agent"] --> a2["deny: awaiting_approval"]
    end
    subgraph B["trace B — a human released it"]
        b1["northstar.review.decision"] --> b2["execute_payment through the gateway"] --> b3["northstar.a2a.resume"] --> b4["POST /a2a/push to customer-ui"]
    end
    b1 -.->|"span LINK"| a1
    b3 -.->|"span LINK"| a1
</pre>

**SAY**

> "That is a **link**, not a parent-child edge. The human took minutes, in a
> process that no longer exists. A parent edge would have misstated both the
> duration and what was executing."

> "Now compare the northstar.policy.decision span in *this* trace with the one in trace
> A. Same service, same code path, but the caller is now `reviewer-ui`, the
> rule is `human-approved.execute`, the decision service has read
> `awaiting_approval` from the ledger, and the verdict is permit. **Nothing
> was overridden. A different caller was asked about.**"

> "And how long humans take is a metric:
> `northstar_approval_wait_duration_seconds`, by `northstar_policy_autonomy_level`
> and `northstar_review_decision` (`approve` or `reject`).
> Ask Prometheus for the 95th percentile."

> The approver is a different service, so `service.name` tells you who
> called. `northstar.policy.caller.spiffe_id` is still the attested answer.

---

### Beat 5 — prove why the money moved · *stage 4* · ~6 min

**DO** — http://localhost:5601/app/dashboards#/view/northstar-evidence

> The dashboard is imported from
> `deploy/observability/dashboards/evidence.ndjson` at stage 4, so it
> survives a cluster recreate. Read its **top-left readme panel first** — it
> records two decisions this walkthrough depends on. Set the time range to
> cover your two submissions.

| Panel | What it shows | The point |
|---|---|---|
| 1 · Claims in this window | claim id → trace ids. A claim a human released has **two** (the wait, then the approval); a claim the agent settled at `autonomous` has one | this is where a human hops from the number they have to the id telemetry joins on |
| 2 · What the platform decided | both claims' `execute_payment` decisions, **same rule**, different outcomes | same rule, different caller, different answer |
| 3 · Was a human in the loop? | **empty** for the claim the agent settled at `autonomous` | the emptiness *is* the evidence |
| 4 · What reached the ledger | every `payments-api` server span | not one of them carries a claim id — they are here only via the trace-id join |

**SAY**

> "There is **no new application code** behind this dashboard. Every panel
> is built out of spans the system already emitted."

> "The join is on **trace id, not claim id**, and that is deliberate.
> `northstar.claim.id` stops at the MCP boundary — a claim-id filter would
> blank exactly the two panels that matter. We built the dashboard around the
> gap instead of pretending it isn't there."

---

### Beat 6 — ask what the agent did · *stage 4* · ~3 min

```bash
scripts/demo evals --quick
```

> AgentEvals holds one session per claim. `--quick` runs this repo's four code
> evaluators only: deterministic, seconds. Without it the built-in LLM-judged
> metrics run too, which takes minutes (see "`scripts/demo evals`" below).

**SAY**

> "Four checks of what the agent did, read off its own spans: it took a path we
> designed, it stopped when the platform refused, it executed the amount it
> proposed, and its narrative states no amount."

> "Each score goes back through the collector as a `gen_ai.evaluation.result`
> event on the agent's span, so the judgement travels with the evidence."

> "What it cannot see is the platform's side: the policy decision, the human,
> the caller's identity. Those are in the trace, for a person to read."

---

### Beat 7 — the golden path · *stage 6* · ~6 min · **THE PUNCHLINE**

**DO** — http://localhost:7007 → **Guest → Enter**.

**SAY** (immediately, before anyone asks)

> "That login asked me for nothing. It is a real auth round-trip, but it is
> unauthenticated by design on a laptop cluster reachable only through an
> explicit port-forward. I'll come back to what actually keeps this safe."

**SHOW — the catalog.** Left nav → **Catalog**. Eight `Component` entities.

**SAY**

> "This list is not typed by hand on either side. The catalog is built from
> the `catalog-info.yaml` files committed in the tree, and the test that
> checks it is a **directory walk** — so neither side of the comparison is a
> list someone wrote down and forgot to update."

**SHOW** — open `settlement-agent` → follow **provides API** →
`settlement-agent-card`.

**DO — the flip.** Left sidebar → **Autonomy contract**
(http://localhost:7007/autonomy-contract).

> ⚠️ It is a **standalone page**, not an entity tab, and Backstage's search
> does **not** index frontend routes. Do not try to find it by searching
> "autonomy" on stage. Use the sidebar link or the URL.

```
  ┌─ Autonomy contract ──────────────────────────────────────────────┐
  │  northstar-autonomy   (namespace: northstar-platform)            │
  │                                                                   │
  │  flag state: [ENABLED]    fail-closed default: [assisted]        │
  │                                                                   │
  │  Identity                    Resolved level   Branch              │
  │  ─────────────────────────────────────────────────────────────    │
  │  settlement-agent            [assisted]       targeting-then      │
  │                                                                   │
  │  The one write this card can make: settlement-agent's             │
  │  targeting branch, assisted ⇄ autonomous.                         │
  │                                                                   │
  │  [ Set settlement-agent: autonomous ]  [ Set settlement-agent: assisted ]
  └───────────────────────────────────────────────────────────────────┘
```

**SAY**

> "That is a **live read** of the Kubernetes object, per identity — not a
> static description of what the demo does."

**DO** — submit **€5,800** first (or point back at beat 1) and confirm it
**waits** for a human; approve or reject it so nothing is left waiting. Then
click **Set settlement-agent: autonomous**. Then submit the **identical** €5,800
claim again. (Do not use €6,200 here: it is above the `autonomous` limit of
€6,000 and would wait again.)

**SHOW** — `"status": "executed"`.

**SAY — this is the line the whole demo exists for**

> "Same claim. Same amount. Same identity. Same choke point. **Nothing else
> in the cluster was touched** — no pod restarted, no image rebuilt, no
> deployment rolled. One word changed on one live object, and what the
> platform is allowed to do changed with it."

> "And the reason that works is this: **the platform API is the resource.
> Backstage is just the view a human uses to read and change it.** I could
> have done that with `kubectl`. The test suite does exactly that. The UI is
> a convenience over an API that was already there — not the thing itself."

**SHOW — the audit trail.** Jaeger → service `backstage`, operation
`northstar.autonomy_contract.change`.

**SAY**

> "One span per flip made through the card. The object's own
> `managedFields` tells you who wrote it *last* — it is overwritten by the
> very next write, by whatever tool. That is a point-in-time fact, not a
> history. **This span is what survives** the object being reverted and the
> pod being recycled."

**DO — flip it back to `assisted`.** ← *Do not skip this. See §7.*

---

### Beat K — the keystone · *stage 7* · ~4 min

Run this **after** beat 7, with the contract resting at `assisted` (§7 item 1).
No model call.

**DO**

```bash
./scripts/beat-keystone.sh
```

**SHOW** — a real run. Five steps: the agent tries and RBAC refuses; someone
makes a mistake (the over-grant); the **same** patch, and now Kyverno refuses;
the mistake is taken back; the flag still resolves `assisted`.

```
=== Beat K: the keystone ===
Acting as system:serviceaccount:northstar-agents:settlement-agent (kubectl --as).

1. The agent tries to flip its own autonomy flag to 'autonomous'
$ kubectl --as=system:serviceaccount:northstar-agents:settlement-agent -n northstar-platform patch featureflag/northstar-autonomy --type=json -p '<replace targeting.if[1] with autonomous>'
  Error from server (Forbidden): featureflags.core.openfeature.dev "northstar-autonomy" is forbidden: User "system:serviceaccount:northstar-agents:settlement-agent" cannot get resource "featureflags" in API group "core.openfeature.dev" in the namespace "northstar-platform"
  RBAC says no. That is the first line.

2. Someone makes a mistake: the agent is over-granted 'patch' on that one flag
$ kubectl apply -f deploy/demo/keystone-overgrant.yaml
  role.rbac.authorization.k8s.io/keystone-demo-overgrant created
  rolebinding.rbac.authorization.k8s.io/keystone-demo-overgrant created
$ kubectl --as=system:serviceaccount:northstar-agents:settlement-agent auth can-i patch featureflag/northstar-autonomy
  yes

3. The same patch again. RBAC now allows it. Kyverno does not.
$ kubectl --as=system:serviceaccount:northstar-agents:settlement-agent -n northstar-platform patch featureflag/northstar-autonomy --type=json -p '<replace targeting.if[1] with autonomous>'
  Error from server: admission webhook "vpol.validate.kyverno.svc-fail-finegrained-agents-cannot-edit-their-own-authority" denied the request: Policy agents-cannot-edit-their-own-authority failed: DENIED: agent identity 'system:serviceaccount:northstar-agents:settlement-agent' may not change the objects that define its own authority (FeatureFlag/northstar-autonomy). Authority changes go through a reviewed commit, never a runtime decision by the agent.
  Denied by Kyverno, naming the policy. That is the second line, and it did not need RBAC to be right.

4. Take the mistake back
$ kubectl delete -f deploy/demo/keystone-overgrant.yaml
  role.rbac.authorization.k8s.io "keystone-demo-overgrant" deleted
  rolebinding.rbac.authorization.k8s.io "keystone-demo-overgrant" deleted

5. What does the flag resolve to now?
$ kubectl -n northstar-platform get featureflag/northstar-autonomy -o jsonpath='{... targeting.if[1]}'
  settlement-agent resolves: assisted

Beat K holds: the agent could not edit what constrains it, even with the grant.
```

**SAY**

> "Two lines, not one. The first is RBAC: the agent has no right to touch its
> own flag, and the cluster says so. But every real cluster collects a mistake
> like the one in step two, a well-meaning change that gives the agent `patch`
> on one object. RBAC now says yes. **Kyverno still says no**, and the message
> names the policy and the reason: authority changes go through a reviewed
> commit, never a runtime decision by the agent. The agent can never edit what
> constrains it, and that does not depend on RBAC being right."

> "The thing that **can** change it is the human path you saw in beat 7:
> Backstage, under its own narrow identity, which the policy does not match."

**SHOW (optional, 30 s)** — the refusal is in Jaeger, with a limit. Service
`kyverno-admission-controller`, operation
`VALIDATE UPDATE core.openfeature.dev/v1beta1, Kind=FeatureFlag`: the identity,
the object, `admission.response.allowed = False` and the message naming the
policy.

**SAY**

> "That is its **own** trace, not part of the claim's. `kubectl` does not
> carry a trace context, so nothing joins them. Which refusals the trace can
> see: the gateway's decision, yes, inside the claim. Kyverno's, yes, but
> standalone. Linkerd's is a counted 403, not a span, and an egress drop is silent."

The script removes the over-grant on every exit path, including Ctrl-C.
**If you abort the room's patience some other way, check §7 item 2.**

---

### Beat 8 — close with the limitations · ~2 min

Say these out loud. An audience of platform engineers will spot all three,
and naming them first is worth more than the features.

| Limitation | The honest version |
|---|---|
| **The keystone fails closed only while Kyverno is registered** | Measured. `failurePolicy: Fail` means an *unreachable* Kyverno (webhook registered, no endpoints) **refuses** an over-granted agent's write (`connection refused`). But a *graceful stop* of the admission controller makes Kyverno delete its own webhook configurations, and then the same write **succeeds**: fail-open. Pre-flight and the gate check the registration for this reason. **Backstage's flip is unaffected either way** (and so are humans and CI), because the API server evaluates the identity condition before calling Kyverno. |
| **Kyverno cannot guard webhook configurations, or RBAC itself** | `ValidatingWebhookConfiguration` and `MutatingWebhookConfiguration` objects are **RBAC-only**: the API server never sends admission for them to webhooks, so an agent allowed to edit one could remove Kyverno's registration and only RBAC would stop it. Agents hold no such grant. Likewise anything RBAC grants an agent that the policy's list does not name, and Kyverno's default `namespaceSelector` skips `kube-system` and `kyverno`. |
| **The PDS execute limits are not covered by it** | They live in code (`pds/rules.py`) and change only by a reviewed commit and a rebuild. There is no cluster object for the policy to guard. |
| **What the keystone does cover** | Every ring's configuration (the flag, the gateway ConfigMap, NetworkPolicies and Linkerd policy objects, SPIRE's `ClusterSPIFFEID`/`ClusterStaticEntry`/`ClusterFederatedTrustDomain`, RBAC, **all** Kyverno policy kinds and exceptions) and the routes to another identity: workloads (pods, deployments, replicasets, statefulsets, daemonsets, jobs, cronjobs) and `serviceaccounts/token`. |
| **Backstage here is unauthenticated** | Be precise, because the sloppy version is wrong: the backend API **does** check a credential — a bare `curl` to it gets HTTP 401. What makes it unauthenticated is that **anyone can mint that credential, unchallenged**, through the guest flow. So: anyone who can reach 7007 can flip the contract. What keeps it honest is **not a login** — it is the narrowness of the one write it can make: `get`/`list`/`patch` on `resourceNames: [northstar-autonomy]`, no delete, no create, separate from the read-only ServiceAccount the Kubernetes plugin uses. **Anyone reaching this port can flip the contract and nothing else.** |
| **No scaffolder** | M1's claim — "a platform team could add a service in an afternoon through the same API" — is **half** demonstrated. The platform API exists as a real object with a real UI. Nothing here generates a new agent from it. |
| **The catalog is static** | It reflects committed `catalog-info.yaml` files staged into the image, not a discovered repository — **there is no git remote** for Backstage's discovery processors to point at. A component added without its own file does not appear until the image is rebuilt, and a unit test catches that drift before a demo does. |

**SAY**

> "The sharper trade we did *not* make: a separate Deployment fronting the
> write, so a Backstage compromise yields the ability to **ask** rather than a
> credential to **keep**. That is the right answer outside a laptop, and it
> is written down as such."

---

## 6. Every surface, and who owns it

| Surface | URL | Stage | Reached by |
|---|---|:---:|---|
| Customer UI | http://localhost:8080 | 1+ | kind host port |
| Reviewer UI | http://localhost:8081 | 3+ | kind host port (NodePort) |
| Jaeger | http://localhost:16686 | 1+ | kind host port |
| OpenSearch Dashboards | http://localhost:5601 | 4+ | `start-demo.sh` |
| Prometheus | http://localhost:9090 | 4+ | `start-demo.sh` |
| Backstage | http://localhost:7007 | 6+ | `start-demo.sh` |

Plus the tool ports `start-demo.sh` also forwards, which the walkthrough never
opens but which are useful when debugging live: `claims-agent` 19001,
`settlement-agent` 19002, `payments-mcp` 18000, `payments-api` 18080,
**flagd metrics** 18014.

> The **kind host ports survive a redeploy**; the port-forwards do not. If a
> surface in the bottom three rows stops answering, the fix is almost always
> to restart `start-demo.sh`, not to touch the cluster.

**flagd's metrics port is the one place** that shows how many evaluations
resolved `assisted` versus `autonomous` without querying Prometheus or
OpenSearch by hand:

```bash
curl -s localhost:18014/metrics | grep feature_flag_flagd_impression_total
```

---

## 7. Reset between runs — the checklist that matters most

```bash
# 1. THE CONTRACT MUST BE LEFT REFUSING. This is the demo's resting state.
kubectl -n northstar-platform get featureflag northstar-autonomy \
  -o jsonpath='{.spec.flagSpec.flags.autonomy-level.targeting}{"\n"}'
# both branches must read "assisted"
```

**`scripts/demo reset` does all of this in one command** (every open approval
rejected as `demo-reset`; flag back to `assisted`, with flagd verified;
over-grant deleted; the run's state cleared) and
is the first thing to type when something looks off. The manual checks below are
what it automates.

If it is flipped, flip it back **through the Backstage card** (so the revert
gets its own audit span) or:

```bash
kubectl -n northstar-platform apply -f deploy/platform/openfeature/featureflag.yaml
```

```bash
# 2. THE OVER-GRANT MUST BE ABSENT. beat-keystone.sh removes it itself, even on
#    Ctrl-C, but check: it is a standing permission for settlement-agent to
#    patch the flag, which is exactly what this demo says never exists.
kubectl -n northstar-platform get role,rolebinding keystone-demo-overgrant \
  || echo "absent, as it should be"
# if it is there:
kubectl delete -f deploy/demo/keystone-overgrant.yaml
```

- [ ] **contract reads `assisted` on both branches** ← the one that ruins a
      second run if you forget it. A cluster left `autonomous` settles EUR 5,800
      without a human, and step 2 collapses.
- [ ] **no approval is open**: `curl -s localhost:8081/api/awaiting` reads `[]`.
      `scripts/demo reset` rejects any open approval as `demo-reset`.
- [ ] **the over-grant is absent** (`start-demo.sh` refuses to start if it is
      still applied)
- [ ] the ledger is per-process and in-memory — restart `payments-api` to
      clear old rows from the reviewer UI:
      `kubectl -n northstar-systems rollout restart deploy/payments-api`
- [ ] payment ids are cumulative since that pod's last restart, so
      `PAY-000001` only appears on a fresh one. **Never promise a specific
      payment id on stage.**

---

## 8. When it breaks — live triage

| Symptom | Most likely cause | Fix, in order |
|---|---|---|
| `otlp_grpc/weaver` `Exporting failed` lines in the collector log | the weaver live-check pod was restarted or is starting (a gate run, `start-demo.sh`, a manual `report()`), so the collector's exporter to it retries in a burst for up to about a minute. **Live-check's, not demo telemetry loss**: Jaeger, Prometheus and OpenSearch get their own copies through separate exporters and are unaffected | wait a minute; `kubectl -n observability get pods -l app=weaver-live-check`. If it stays failing, the pod is not Ready: `kubectl -n observability logs deploy/weaver-live-check`. Every weaver start clones the semconv dependency from github.com (measured: not cached, a new checkout each start), so a pod that never gets Ready usually has no egress to github.com |
| First claim submission errors | `ANTHROPIC_API_KEY` empty | it is a **model call**. Check `.env`, restart the agent deployments |
| A surface at 5601 / 9090 / 7007 does not answer | dead port-forward (a dead forward **keeps its socket bound** and still looks healthy to `lsof`) | Ctrl+C and re-run `./scripts/start-demo.sh` |
| `Backstage: SKIPPED` in start-demo output | cluster is below stage 6 | `./scripts/setup-kind.sh --stage 7` — no recreate needed from stage 3+ |
| `start-demo.sh` refuses: *keystone policy is not Ready* | the policy never armed (usually Kyverno cannot read a kind it matches) | `kubectl get validatingpolicy agents-cannot-edit-their-own-authority -o yaml` and read `status.conditionStatus.conditions`. Beat 7 is **not** affected: Backstage's flip works with Kyverno down |
| `start-demo.sh` refuses: *webhook configuration is not registered* | Kyverno was stopped gracefully (scaled to 0, a rollout) and deleted its own registration: **the keystone is silently off** | `kubectl -n kyverno scale deploy/kyverno-admission-controller --replicas=1`, wait for `kubectl get validatingwebhookconfiguration kyverno-resource-validating-webhook-cfg`, re-run |
| `start-demo.sh` refuses: *nothing answers behind svc/kyverno-svc* | Kyverno pods not Ready, webhook still registered: an agent's write is *refused* (fails closed) | `kubectl -n kyverno get pods`, logs |
| Backstage's flip answers with an admission error mentioning `kyverno` | the policy's `matchConditions` now match Backstage's identity (a Kyverno outage alone cannot do this: measured, Backstage is unaffected by one) | Backstage's identity is `backstage-autonomy-writer` in `northstar-platform`; the policy matches only `northstar-agents` and `northstar-tools` prefixes. `kubectl get validatingwebhookconfiguration kyverno-resource-validating-webhook-cfg -o yaml` and read the `matchConditions` |
| `start-demo.sh` refuses: *over-grant is still applied* | an earlier beat-keystone run was killed hard (SIGKILL) | `kubectl delete -f deploy/demo/keystone-overgrant.yaml` |
| `beat-keystone.sh` step 3 prints `Forbidden` instead of the Kyverno message | the over-grant did not take effect yet | re-run; the script waits for `auth can-i` to say `yes` first, so this means RBAC is not what it was |
| `beat-reach.sh` step 2 prints `REFUSED` or `REACHED` | the egress NetworkPolicy is not enforcing (kindnet), or the pod was not dropped | `kubectl get networkpolicy -A`; `tests/e2e/test_egress_policy.py` |
| `beat-reach.sh` step 3 does not print `REFUSED 403` | payments-api's Linkerd policy is missing or not holding, or the deny counter did not rise | `kubectl get servers,httproutes.policy.linkerd.io,authorizationpolicies -A`; `kubectl get --raw /api/v1/namespaces/northstar-systems/pods/<pod>:4191/proxy/metrics \| grep inbound_http_authz_deny_total`; `tests/e2e/test_gateway_is_the_only_route.py` |
| `setup-kind.sh` refuses: *already exists and still runs Cilium* | the cluster predates M12 (it was created with `disableDefaultCNI`) | `kind delete cluster --name northstar`, then `./scripts/setup-kind.sh --stage 7` |
| settlement-agent's model call dies with a TLS reset | Anthropic's address range is static in `network-policies.yaml`; a provider address change lands outside it | compare `api.anthropic.com` with 160.79.104.0/23 and 2607:6bc0::/48 and update the `ipBlock`s |
| EUR 5,800 comes back `executed` at the start of step 2 | **contract left flipped from a previous run** | `scripts/demo reset`, or §7 |
| The customer tab never flips to "Settled" after the approve | the customer-ui's stream to claims-agent is down (a dead forward), or the page lost the task | check `scripts/start-demo.sh`'s claims-agent forward (`localhost:19001`), then finish from the terminal: `scripts/demo approve last` (the stage fallback). The payment is settled either way; only the tab is stale |
| A claims-agent restart while a claim waits | waiting tasks live in claims-agent's memory, by design: a restart loses them | the re-send above cannot help (the task is gone). Reject the stranded approvals with `scripts/demo reset` and file the claim again |
| `scripts/demo approve last` says no claim is waiting | the reviewer-ui's queue is empty: the claim already ended, or `reset` rejected it | `curl -s localhost:8081/api/awaiting` lists what is waiting; approve it in the reviewer tab. If the queue is empty and the customer tab still says "Awaiting human approval", the claim was decided and never told: `approve last` finds that too (next row) |
| The money moved but the customer tab is stuck on "Awaiting human approval" (`demo approve` printed `the customer's claim could not be told`) | the decision is on the ledger, but the message to claims-agent was lost (a dropped answer, claims-agent unreachable for a moment) | **run the same command again**: `scripts/demo approve last` (or `reject last`; there is no button for a claim that has left the queue). `demo approve|reject <claim|last>` finds it even though it is no longer in `/api/awaiting`: it reads the ledger's executed and rejected payments and keeps the one whose task is still `input-required`. The reviewer-ui sees the decision is already on the ledger, takes no new money decision and tells the claim again. It only answers 409 if the claim is no longer waiting |
| `scripts/demo` prints `FAIL` | the expected outcome did not happen: that is the command doing its job | read the line: it names what was expected. `scripts/demo reset`, then re-run the one step |
| `scripts/demo claim` hangs after the model's explanation | it is waiting (up to 90s) for every service's spans to reach Jaeger, so that an *absent* span is a real absence | give it a few seconds; the explanation is already on screen. If it times out, the Jaeger UI at http://localhost:16686 is the fallback |
| EUR 5,800 waits at `autonomous` | the flag did not take, or the caller identity is wrong | check the card; check `northstar.policy.caller.spiffe_id` on the span |
| Backstage card shows the old value | it reads live — so this means the write failed | check the response, then `kubectl get featureflag` as an independent witness |
| Jaeger shows two disconnected traces | trace context broken at a hop | expected only for the **human approval**, which is a link by design (beat 4) |
| Trace A looks short and ends at `input-required` | that is the design: trace A ends at the hand-off, and a person's decision is trace B | expected. Follow the link from trace B's `northstar.a2a.resume` span back to A |
| `NoTaskQueue` ERROR in the claims-agent log after an approve | a2a-sdk's cleanup on every resume | expected noise; the claim completed |
| Scores come back with `spanCount` still climbing | OpenSearch near-real-time lag | wait, re-ask with `"emit": false` until it settles |
| A pod is CrashLooping | — | `kubectl -n <ns> logs <pod> --previous` |

**The two things not to do live:**

- **Do not run `./scripts/teardown.sh`** to fix something mid-demo. It
  destroys the cluster and the local registry; a rebuild is not a
  five-minute operation.
- **Do not run `./scripts/smoke-test.sh`** as a health check. It is the full
  gate — it spends model tokens, drives browsers, and takes ~10 minutes. Use
  the §4 T-10 checks instead.

---

### Weaver live-check: what it covers, and how to run it

The gate checks the cluster's telemetry against the registry in
`telemetry/registry/` (Weaver live-check, in-cluster). Read a green result for
what it says; the measured limits are in `docs/telemetry-contract.md`, "What
live-check checks, and what it does not":

- **A missing required attribute is not caught.** Live-check v0.28.0 with a v1
  registry reported no finding when `northstar.policy.rule` was dropped from the
  decision span (measured; four decision spans, no `required_attribute_not_present`).
  The behaviour tests pin the required attributes instead:
  `tests/e2e/test_policy_denial.py`, `tests/e2e/test_human_in_the_loop.py` and
  `tests/e2e/test_demo_flow.py`.
- **Span names are not judged by live-check**, so the live-check test checks them:
  it fails on any span name in the report that starts with a retired un-prefixed
  prefix. A misspelled `northstar.claim.submit`, `northstar.claim.wait` or
  `northstar.autonomy_contract.change` is still unchecked; the test pins only
  `northstar.policy.decision` and `northstar.review.decision` as seen.
- **Standard names show as "does not exist in the registry"** at `information`
  level: our use of semconv names is not validated, only our own names.
- **Re-running the live-check test alone** (`tests/e2e/test_registry_live_check.py`)
  needs a restart of the live-check pod and fresh traffic first, or it fails with
  "live-check received nothing":
  `PYTHONPATH=. .venv/bin/python -m tests.harness.livecheck restart`.
- **The collector-log loss guards go red** if they run within about 60 seconds of
  a manual `report()`: reading the report restarts the weaver pod and the
  collector's exporter retries in a burst. Wait a minute. The gate's order
  (restart, the rest of `tests/`, then the live-check file) avoids both.

---

## 9. The six sentences to land

If the audience remembers nothing else:

1. **"A refusal is the platform working."** — not an error, not a failure,
   and not an ERROR span.
2. **"Identity is an input to the decision, not a label on it."** — the same
   code path, the same rule, a different caller, a different answer.
3. **"The evidence is built out of spans the system already emitted."** — no
   new application code behind the dashboard, and the gaps are published
   rather than papered over.
4. **"One live value changed what the platform is allowed to do."** — no
   redeploy, no restart, no rebuild.
5. **"The platform API is the resource. Backstage is just the view."**
6. **"The agent can never edit what constrains it."** — RBAC is the first
   line; Kyverno is the one that holds when RBAC is wrong.

---

## 10. Where everything else lives

| Document | What it owns |
|---|---|
| [`README.md`](../README.md) | status per milestone, quick start, the stages table |
| [`docs/architecture.md`](architecture.md) | the rings and the keystone, components, hops, protocols, the trace span by span, the full "See it yourself" walkthrough, and the authoritative limitations list |
| [`docs/telemetry-contract.md`](telemetry-contract.md) | verified instrumentation, the published gaps, the pin table |
| [`docs/upstream-findings.md`](upstream-findings.md) | filing-ready findings against upstream projects — **none of them filed** |
| [`docs/superpowers/specs/`](superpowers/specs/) | the design behind each milestone, including what was deliberately dropped |
| [`docs/superpowers/plans/`](superpowers/plans/) | the implementation plans |

---

## Appendix: off-stage commands

### `scripts/demo ceiling`: the gateway's own limit (not in the five-minute flow)

Mints settlement-agent's SVID and calls `execute_payment` through the gateway
for an amount above the gateway's CEL ceiling. It prints
`HTTP 403  authorization failed  -- the gateway said no, the PDS was never asked`
and exits non-zero unless the answer was a 403. It changes no state and leaves
no claim waiting. Use it only when a question asks "why not a CEL rule?"; the
answer is the "Two ways to say no" table in `docs/architecture.md`.

The rule opens with `request.method != "POST"`, so the agents' MCP client's own
`GET` and `DELETE` on `/mcp` pass. Only a `POST` that is a `tools/call` for
`execute_payment` is judged, and a JSON-RPC batch is still `403`.

### A claim above the ceiling: nobody sees it, by design

The gateway's ceiling is **not a decision the PDS makes**, so it does not send
anything to a human. Claim above the ceiling (EUR 50,000 today; the figure is
read from `deploy/platform/agentgateway/config.yaml`) and this happens:

1. The settlement agent proposes the payment. The ledger records it `proposed`.
2. The agent's `execute_payment` is answered by the gateway with `HTTP 403
   authorization failed`. The PDS is never asked, so there is no
   `northstar.policy.decision` span for the execute and no refusal is recorded.
3. The agent's tool client treats a 403 as a failed call, not as a "no" tool
   result, so the claim's task ends `failed` ("could not be processed").
4. The payment stays `proposed`. It is **not** `awaiting_approval`, so it is not
   in the reviewer-ui's queue and no human sees it.

By design nobody, a human included, may release it through this gateway: the
reviewer-ui's release is the same `execute_payment` call through the same
route, and the ceiling stops it too. A payment that large is out of scope for
this demo (spec section 8) and would need a different road, not a bigger
number. `tests/e2e/test_gateway_ceiling.py::test_a_claim_over_the_ceiling_fails_and_nobody_is_asked`
pins every part of this.


### `scripts/demo evals`: AgentEvals scores what the agent did (not in the five-minute flow)

AgentEvals (`ghcr.io/agentevals-dev/agentevals:0.11.0`, `deploy/observability/agentevals.yaml`,
stage 4 and up) receives a copy of every trace from the collector and holds one session per
claim: settlement-agent sets the run's `gen_ai.conversation.id` to the claim id. Nothing scores
on its own. `scripts/demo evals` asks it to score every finished claim with this repo's four
code evaluators (`deploy/observability/agentevals/evaluators`), and prints one line per check:

- `took_a_known_path`: the tool calls in order are the settled path (propose, execute) or
  the held one (propose, execute refused by the platform; settlement-agent's own code then
  records the refusal, not the model). Anything else is a path nobody designed.
- `stopped_when_refused`: no payment call after the platform's refusal.
- `amount_executed_matches_proposed`: every `execute_payment` states the amount of the
  `propose_payment` before it (passes when equal, not evaluated when nothing was executed,
  fails otherwise; a refused execute is still compared).
- `narrative_states_no_amount`: the agent's prompt says "Do not state amounts in your
  narrative". This checks the answer.

All four skip claims-agent's intake extraction: a free-text claim's extraction runs under the
same `gen_ai.conversation.id` (the claim id), so it lands in the claim's session, and its prompt
opens with "First notice of loss" (`agents/claims-agent/extraction.py`). Judged as a settlement
it would fail the path check (its output tool is a tool call) and the narrative check (it
answers with the customer's estimate).

Beside those four it asks for every built-in metric that can run here, judged by Claude through
LiteLLM (setup-kind.sh copies the model secret into `observability`): `hallucinations_v1`, the two
rubric metrics with Northstar rubrics (no amount, an honest outcome, a person named as the next
step; propose before execute, stop on refusal, the same amount), and three that compare each claim
with a golden one, the latest claim the agent settled (`tool_trajectory_avg_score`,
`response_match_score`, `final_response_match_v2`; across claims with different ids and amounts
they mostly measure that difference, and the golden run is labelled because it scores itself 1.0).
Not available: `safety_v1`, `response_evaluation_score` and the multi-turn metrics need Google
Cloud. Measured 2026-10-21: the judge is not stable (one claim scored 0.60, 0.40, 0.40 for
hallucinations on three runs, nothing changed), and the built-in metrics export no explanation.
A run of nine metrics over three claims takes about three minutes.

Each score also goes back through the collector as a `gen_ai.evaluation.result` log event on
the agent's `invoke_agent` span. Open the UI at http://localhost:18001 (start-demo.sh forwards
it) to see the sessions and run evaluations by hand.

What it cannot see: only the agent's GenAI spans reach an evaluator. Not the policy decision, not the reviewer's approval, not the caller's identity,
and it does not follow span links. Its built-in `tool_trajectory_avg_score` compares tool
arguments too (claim ids, amounts) with no switch to ignore them at 0.11.0, so against a
golden run of another claim it fails every claim: `took_a_known_path` is the stand-in.
Sessions live in the pod's memory; a restart empties the UI.
