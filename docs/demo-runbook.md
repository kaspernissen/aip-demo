# Northstar — Demo Runbook (M6, Golden path)

Status: M6 (Golden path). This is the **operator's** document: how the demo is
brought up, how it is run in front of an audience, what to say at each beat,
and what to say when something breaks.

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

    r1(["one trace exists"])
    r2(["identity is an INPUT to the decision"])
    r3(["a human can overturn"])
    r4(["from telemetry alone, no new app code"])
    r5(["no redeploy, no restart, no rebuild"])
    r6(["Backstage is just the view onto it"])

    s1 --> s2 --> s3 --> s4 --> s5 --> s6
    s1 -.-> r1
    s2 -.-> r2
    s3 -.-> r3
    s4 -.-> r4
    s5 -.-> r5
    s6 -.-> r6
</pre>

**The closing line the whole demo is built to earn:**

> *The platform API is the resource. Backstage is just the view a human uses
> to read and change it.*

---

## 2. The system at a glance (stage 6)

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
        ru -->|A2A| ss["`senior-settlement-agent · Python
      same image as settlement-agent,
      different ServiceAccount,
      its own allowance row in the PDS`"]
    end
    ss -->|"MCP — the same gateway"| gw

    subgraph evidence["THE EVIDENCE PLANE · stage 4"]
        svc["every service"] -->|OTLP| col["`OpenTelemetry Collector
      OTLP ingest ONLY — all 10 default
      scrape jobs are disabled`"]
        col --> jg["Jaeger :16686"]
        col --> os["OpenSearch :5601"]
        col --> pr["Prometheus :9090"]
        es["`eval-scorer :18300
      pull-only, POST /score`"] -->|"`writes its verdict back
      into the trace it judged`"| col
    end

    subgraph view["THE VIEW · stage 6"]
        bs["`Backstage :7007
      catalog, 9 Components
      + the Autonomy contract card`"]
    end
    bs -->|"`ServiceAccount backstage —
      get/list/watch only`"| ff
    bs -->|"`ServiceAccount backstage-autonomy-writer —
      get/list/PATCH, resourceNames: [northstar-autonomy],
      no more`"| ff
</pre>

Underneath all of it: **Cilium** (dataplane) · **SPIFFE/SPIRE** (workload
identity, trust domain `northstar.local`) — every MCP call carries a
SPIRE-minted JWT-SVID the gateway verifies before anything else happens.

### The two claims the whole demo runs on

| | Claim | Amount | Junior limit | What happens |
|---|---|---|---|---|
| **A** | `CLM-1842` | **€6,200** | €6,000 | **refused** — this is the platform working |
| **B** | `CLM-1842` | **€5,800** | €6,000 | **executed** — no human anywhere |

Same claim id, same shape, one number apart. **Showing both is mandatory**: a
platform that refused everything would look identical if you only ran A.

Then the same €6,200 claim is made to succeed **three different ways**, and
each one is a different milestone's point:

<pre class="mermaid">
flowchart LR
    r(["€6,200 refused"])
    r -->|"stage 3"| a["`approved by a human on the reviewer UI
      different **IDENTITY**:
      senior-settlement-agent, €25,000`"]
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
| 2 | Governed | Cilium, SPIFFE/SPIRE, agentgateway, Policy Decision Service | authorization and workload identity |
| 3 | Supervised | reviewer-ui, senior-settlement-agent | human-in-the-loop |
| 4 | Observable | OpenSearch + Dashboards, Prometheus, eval-scorer | evidence and evaluation |
| 5 | Progressive autonomy | cert-manager, Open Feature Operator, flagd sidecar, the `FeatureFlag` | policy as a live value |
| 6 | **Golden path** | **Backstage** | **the platform API and its UI — run this one** |

> **Default: `--stage 6`.** It is the complete demo. Lower stages exist for
> shorter talks and for isolating one idea.

**Two things about stages that will bite you:**

1. Stages **2–6 all need `networking.disableDefaultCNI`**, which is a
   *create-time* kind flag. You cannot layer Cilium onto a running default
   CNI. Going from stage 1 → stage 2 therefore needs the cluster recreated;
   the script refuses rather than doing something broken.
2. Stages **4, 5 and 6 all reuse stage 3's cluster config unchanged** —
   there is deliberately no `stage4.yaml`, `stage5.yaml` or `stage6.yaml`.
   So **3 → 4 → 5 → 6 needs no recreate.** You can climb the ladder live.

### Option 2: how long you have

| Length | Stage | Beats to run | Cut |
|---|:---:|---|---|
| **5 min** | 6 | §5 beats 1, 2, 7 | everything else — refuse, then flip in Backstage |
| **20 min** | 6 | beats 1–3, 6, 7 | the evidence view and the scorer |
| **45 min** | 6 | all of §5 | nothing |
| deep-dive | 6 | all of §5 + architecture.md's "See it yourself" | — |

### Option 3: environment switches

| Variable / flag | Default | What it changes |
|---|---|---|
| `ANTHROPIC_API_KEY` | **required** | The settlement decision is a **real model call**. Empty → every pod still comes up Running and fully traced, and the **first claim submission fails**. `setup-kind.sh` prints `SET`/`EMPTY` — **check for SET before going on stage.** |
| `DASH0_AUTH_TOKEN` + 3 siblings | unset | Adds a second OTLP exporter. **No application code changes.** Good beat if the audience cares about vendor-neutrality. |
| `NORTHSTAR_CAPTURE_CONTENT` | `true` | `false` → the evidence view loses **nothing** (every field it reads is emitted by the platform, not the model). What is lost is the agent's own statement of the amount. This is OQ-1, measured as a matched pair. |
| `RUN_BROWSER_TESTS=1` | unset | Unlocks 13 test modules / 75 tests that spend model tokens or drive a browser. **Only for the gate**, not for the demo. |
| `"emit": false` | — | On `POST /score`: read the scores **without writing spans**. How to look without changing what you are looking at. |

### Option 4: the three ways to flip the autonomy contract

| Route | Command / click | Use it for |
|---|---|---|
| Backstage card | http://localhost:7007/autonomy-contract → **Set settlement-agent: autonomous** | **the demo** — it is the milestone |
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

# one venv per Python service (six of them)
for svc in tools/payments-mcp agents/settlement-agent agents/claims-agent \
           platform/policy-decision-service apps/reviewer-ui platform/eval-scorer; do
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
./scripts/setup-kind.sh --stage 6
```

Watch for, at the end of the run:

- [ ] `ANTHROPIC_API_KEY: SET` — **if this says EMPTY, stop.** Every pod will
      come up Running and the first claim will fail at the model call.
- [ ] every component reporting installed, no rollout timeouts

### T-15 — start the surfaces, and leave this terminal alone

```bash
./scripts/start-demo.sh      # owns EVERY port-forward. Ctrl+C stops them all.
```

It prints every URL it established. Confirm you see a `Backstage` line and not
`Backstage: SKIPPED` — `SKIPPED` means the cluster is below stage 6.

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

Expect exactly this shape, with **both** identities resolving `assisted`:

```json
{ "flag": "northstar-autonomy", "namespace": "northstar-platform",
  "state": "ENABLED", "defaultVariant": "assisted",
  "perIdentity": {
    "settlement-agent":        { "level": "assisted", "branch": "targeting-then" },
    "senior-settlement-agent": { "level": "assisted", "branch": "targeting-else" } } }
```

- [ ] Customer UI loads — http://localhost:8080
- [ ] Reviewer UI loads — http://localhost:8081
- [ ] Jaeger loads and its service dropdown is populated — http://localhost:16686
- [ ] Backstage loads and the **Guest → Enter** button works — http://localhost:7007
- [ ] Autonomy contract card reads `assisted` / `assisted`

### T-5 — the rehearsal claim

**Submit €6,200 once and confirm it comes back `refused`.** This costs one
model call and it is the single most valuable thing you can do in pre-flight:
it proves the API key, the whole A2A chain, the gateway, the PDS, flagd and
the ledger, in one action.

Then **reset the ledger** before the audience sees anything — see §7.

---

## 5. The run — beat by beat

Each beat below is: **DO** (the action), **SHOW** (where to look), **SAY**
(the point). The SAY lines are the argument; everything else is scaffolding
for them.

---

### Beat 1 — the refusal · *stage 1–2* · ~3 min

**DO** — http://localhost:8080. The form is pre-filled: claim `CLM-1842`,
policy `POL-77120`, **€6,200**. Click **Submit claim**.

**SHOW** — the Outcome block, within a few seconds:

```json
{ "status": "refused",
  "paymentId": "PAY-0000NN",
  "amount": 6200,
  "narrative": "…the model's own words, different every run…" }
```

**SAY**

> "That is not a failure. That is the platform working. The agent *proposed*
> the payment — the platform refused to execute it. And notice the narrative
> is different every time you run this, because it is genuinely the model
> explaining its own refusal, not a canned string."

> ⚠️ **Do not let a `refused` land as an error.** Name it as the success
> before anyone reads it as a bug. This is the most common way this demo is
> misread.

**DO** — change the amount to **5800**, submit again. It comes back
`"status": "executed"`.

**SAY**

> "Same claim, same agents, same path, four hundred euros apart. One is
> refused and one goes through. A platform that refused everything would have
> looked identical on the first run alone — that is why you always see both."

---

### Beat 2 — one trace, and the absence that proves it · ~4 min

**DO** — http://localhost:16686. Service `customer-ui`, operation
`claim.submit`, **Find Traces**.

> Jaeger's `total` field is often `0` even when traces come back. It is a
> known API quirk. Read `data`, not `total`. Ignore any `harness-probe*`
> services in the dropdown — those are the test suite's own synthetic spans.

**SHOW** — open the €6,200 trace and walk these four spans:

| Span | Service | What it proves |
|---|---|---|
| `claim.submit` | customer-ui | the trace **root** — no parent, attributes set in the browser |
| `tools/call` (SERVER) | agentgateway | `jwt.sub` = the caller's **attested** SPIFFE ID, set by the gateway from the token it verified |
| `policy.decision` | policy-decision-service | `policy.decision=deny`, `policy.rule=settlement-agent.assisted.execute_limit`, `policy.request.amount=6200`, `policy.limit.amount=6000` |
| `Guardrail` (CLIENT) | agentgateway | `grpc.status = 0` — the *call* succeeded, the **deny is in the payload** |

**SAY**

> "Look at the status on the policy.decision span. It is **not** ERROR. A
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

### Beat 4 — the human overturns it · *stage 3* · ~5 min

**DO** — http://localhost:8081 (a NodePort — needs no port-forward and
survives a redeploy). The refused €6,200 payment is listed with **four
facts**: the rule that fired, the limit the caller held, the amount
requested, and the model's own narrative.

Type any reviewer id (placeholder `alex.mercer`) → **Approve**.

**SAY**

> "The human's information path here is the **ledger**, not telemetry. Turn
> content capture off and this page is unchanged. Telemetry is for the
> platform team; the ledger is for the reviewer."

> "There is a Reject button and it is honest about doing nothing — it records
> a span and leaves the payment exactly as refused. No second approver, no
> timeout, no expiry, and the reviewer id is a string the form supplies. That
> is written down as a limitation rather than hidden."

**SHOW** — Jaeger, service `reviewer-ui`, operation `review.decision`:

<pre class="mermaid">
flowchart LR
    subgraph A["trace A — refused"]
        a1["browser → … → settlement-agent"] --> a2["deny"]
    end
    subgraph B["trace B — approved"]
        b1["review.decision"] --> b2["settlement.execute_approved"]
    end
    b2 -.->|"span LINK"| a1
</pre>

**SAY**

> "That is a **link**, not a parent-child edge. The human took minutes, in a
> process that no longer exists. A parent edge would have misstated both the
> duration and what was executing."

> "Now compare the policy.decision span in *this* trace with the one from
> beat 2. Same rule shape, same service, same code path — but the caller is
> now `senior-settlement-agent`, the limit reads €25,000, and the verdict is
> permit. **Nothing was overridden. A different caller was asked about.**"

> ⚠️ **The thing that looks wrong and is not:** the senior agent's spans
> arrive under `service.name=settlement-agent`, because its
> `OTEL_SERVICE_NAME` is deliberately the same. Tell them apart by `jwt.sub`
> / `policy.caller.spiffe_id`, **never** by service name.

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
| 1 · Claims in this window | claim id → trace ids. €6,200 has **two** (refusal + approval); €5,800 has one | this is where a human hops from the number they have to the id telemetry joins on |
| 2 · What the platform decided | both claims' `execute_payment` decisions, **same rule**, different outcomes | same rule, different caller, different answer |
| 3 · Was a human in the loop? | **empty** for €5,800 | the emptiness *is* the evidence |
| 4 · What reached the ledger | every `payments-api` server span | not one of them carries a claim id — they are here only via the trace-id join |

**SAY**

> "There is **no new application code** behind this dashboard. Every panel
> is built out of spans the system already emitted."

> "The join is on **trace id, not claim id**, and that is deliberate.
> `northstar.claim.id` stops at the MCP boundary — a claim-id filter would
> blank exactly the two panels that matter. We built the dashboard around the
> gap instead of pretending it isn't there."

---

### Beat 6 — ask the auditor · *stage 4* · ~4 min

```bash
curl -s localhost:18300/score -X POST \
  -H 'content-type: application/json' \
  -d '{"claimId": "CLM-1842", "emit": false}' | python3 -m json.tool
```

> Check `spanCount` and `decisionCount` first. OpenSearch is near-real-time,
> and a claim scored the instant a settlement returns can be scored
> half-written. When those stop moving, ask once more **without** `emit` and
> the five scores land in the trace.

**SAY**

> "Five policy-adherence scores, written back as spans **into the trace they
> judge** — so the judgement travels with the evidence instead of living in a
> separate system."

> "The one to read is `stopped_when_refused`. It is **1.0 compliant** for the
> real run and **0.0 retry-after-refusal** for a deliberately mis-routed one.
> A scorer that returns 1.0 for everything proves nothing — that difference
> is what makes the other four worth reading."

> "These are **pull-only** and deterministic. Nothing scores a settlement as
> it happens, and there is no LLM-as-judge anywhere in this."

---

### Beat 7 — the golden path · *stage 6* · ~6 min · **THE PUNCHLINE**

**DO** — http://localhost:7007 → **Guest → Enter**.

**SAY** (immediately, before anyone asks)

> "That login asked me for nothing. It is a real auth round-trip, but it is
> unauthenticated by design on a laptop cluster reachable only through an
> explicit port-forward. I'll come back to what actually keeps this safe."

**SHOW — the catalog.** Left nav → **Catalog**. Nine `Component` entities.

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
  │  senior-settlement-agent     [assisted]       targeting-else      │
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

**DO** — submit **€6,200** again first (or point back at beat 1) and confirm
`refused`. Then click **Set settlement-agent: autonomous**. Then submit the
**identical** €6,200 claim again.

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

### Beat 8 — close with the limitations · ~2 min

Say these out loud. An audience of platform engineers will spot all three,
and naming them first is worth more than the features.

| Limitation | The honest version |
|---|---|
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
| Backstage | http://localhost:7007 | 6 | `start-demo.sh` |

Plus the tool ports `start-demo.sh` also forwards, which the walkthrough never
opens but which are useful when debugging live: `claims-agent` 19001,
`settlement-agent` 19002, `payments-mcp` 18000, `payments-api` 18080,
`eval-scorer` 18300, **flagd metrics** 18014.

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

If it is flipped, flip it back **through the Backstage card** (so the revert
gets its own audit span) or:

```bash
kubectl -n northstar-platform apply -f deploy/platform/openfeature/featureflag.yaml
```

- [ ] **contract reads `assisted` on both branches** ← the one that ruins a
      second run if you forget it. A cluster left `autonomous` makes beat 1
      execute instead of refuse, and the entire narrative collapses.
- [ ] the ledger is per-process and in-memory — restart `payments-api` to
      clear refused rows from the reviewer UI:
      `kubectl -n northstar-systems rollout restart deploy/payments-api`
- [ ] payment ids are cumulative since that pod's last restart, so
      `PAY-000001` only appears on a fresh one. **Never promise a specific
      payment id on stage.**

---

## 8. When it breaks — live triage

| Symptom | Most likely cause | Fix, in order |
|---|---|---|
| First claim submission errors | `ANTHROPIC_API_KEY` empty | it is a **model call**. Check `.env`, restart the agent deployments |
| A surface at 5601 / 9090 / 7007 does not answer | dead port-forward (a dead forward **keeps its socket bound** and still looks healthy to `lsof`) | Ctrl+C and re-run `./scripts/start-demo.sh` |
| `Backstage: SKIPPED` in start-demo output | cluster is below stage 6 | `./scripts/setup-kind.sh --stage 6` — no recreate needed from stage 3+ |
| €6,200 comes back `executed` | **contract left flipped from a previous run** | §7 |
| €5,800 comes back `refused` | contract flipped, or wrong caller identity | check the card; check `policy.caller.spiffe_id` on the span |
| Backstage card shows the old value | it reads live — so this means the write failed | check the response, then `kubectl get featureflag` as an independent witness |
| Jaeger shows two disconnected traces | trace context broken at a hop | expected only for the **human approval**, which is a link by design (beat 4) |
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

## 9. The five sentences to land

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

---

## 10. Where everything else lives

| Document | What it owns |
|---|---|
| [`README.md`](../README.md) | status per milestone, quick start, the stages table |
| [`docs/architecture.md`](architecture.md) | components, hops, protocols, the trace span by span, the full "See it yourself" walkthrough, and the authoritative limitations list |
| [`docs/telemetry-contract.md`](telemetry-contract.md) | verified instrumentation, the published gaps, the pin table, test counts |
| [`docs/upstream-findings.md`](upstream-findings.md) | filing-ready findings against upstream projects — **none of them filed** |
| [`docs/superpowers/specs/`](superpowers/specs/) | the design behind each milestone, including what was deliberately dropped |
| [`docs/superpowers/plans/`](superpowers/plans/) | the implementation plans |
