# Northstar M8 — Human in the loop

Status: **design**, written 2026-10-13. Builds on `m7-bounded` and on the
agentgateway v1.6.0 bump, so it branches from the branch's tip after that bump
lands.

**The line on stage:** *"The agent does the work. A human signs. The trace
shows how long the human took."*

---

## 1. Why this milestone exists

In M7 the autonomy dial is odd: `assisted` lets the agent pay up to €6,000 and
`autonomous` up to €1,000,000. No regulated insurer runs either mode. The
pattern they actually run is **maker-checker**: the agent prepares the payment,
and a human releases it. Autonomy means widening the band in which the agent
may release the payment itself.

M8 makes the dial mean that:

| Autonomy level | The agent may pay | Everything else |
|---|---|---|
| `assisted` (resting state) | nothing (limit €0) | waits for a human |
| `autonomous` | up to €6,000 | waits for a human |

It also brings the human approval forward. It is the *first* thing the demo
shows after the guard, because that is how the room works today. Until now the
approval existed only on the ledger and in Jaeger. The customer was told "Not
settled automatically" and never learned what happened next.

Kasper's constraints:

- **Use what the tools already give us; build as little custom as possible.**
  Every mechanism below is a standard feature of a component Northstar already
  runs: A2A task states, OpenTelemetry span links and histograms, and
  agentgateway CEL.
- **Keep the policy-decision-service (PDS)** as the decider. It owns the live
  dial, the named rule, the explainable refusal and the `policy.decision` span.
- **Show that agentgateway can do guardrails on its own** (§5).
- **Keep it simple:** one approval path and no second limit for a "senior"
  agent.

## 2. What exists today (measured 2026-10-13)

- When the PDS refuses `execute_payment`, the settlement-agent records the
  refusal on the ledger (`refuse_payment` → `POST /payments/{id}/refuse`). The
  record holds the rule, the limit, the narrative, and `refusedTraceId` and
  `refusedSpanId`. The agent then answers with `status: "refused"`.
- The customer-ui sends one synchronous `message/send` and renders the single
  reply. It has no waiting state. No A2A task states are used anywhere.
- The reviewer-ui lists `status=refused` straight from payments-api.
  - **Approve** sends an A2A message to `senior-settlement-agent`, which
    executes under its own SPIFFE ID and a €25,000 limit.
  - **Reject** records a span and changes nothing. The payment stays
    `refused` forever.
- The approval is a second trace, joined to the claim's trace by a span link.
  `review.decision` already carries `review.elapsed_ms`.

## 3. The design

### 3.1 Policy: two rows, one new caller

`pds/rules.py` `EXECUTE_LIMITS` becomes:

| Caller | Level | Limit | Rule name (unchanged shape) |
|---|---|---|---|
| settlement-agent | `assisted` | €0 | `settlement-agent.assisted.execute_limit` |
| settlement-agent | `autonomous` | €6,000 | `settlement-agent.autonomous.execute_limit` |
| reviewer-ui | any | none | `human-approved.execute` |

- **The senior-settlement-agent row is deleted.** So are its deployment, its
  SVID entry, its gateway route and its tests.
- **The reviewer-ui is the only path for a human approval.** It calls
  `execute_payment` itself, through the gateway and the PDS, under its own SPIRE
  identity. It already holds an SVID (`apps/reviewer-ui/identity.py`). The PDS
  allows that call only when the ledger shows the payment `awaiting_approval`.
  The reviewer-ui reads that with `get_payment`, as the senior agent did.
- **`propose_payment` stays allowed at every level.** The agent always does
  the work; only releasing the money is gated.

### 3.2 The ledger: name the waiting state, give reject an outcome

payments-api statuses become `proposed`, `awaiting_approval`, `executed` and
`rejected`.

- `refused` is renamed `awaiting_approval`. On stage the platform is not
  refusing the claim; it is routing it to a human. The PDS decision is still a
  `deny` *for the agent*, so `policy.decision` is unchanged. Only the ledger
  word and the UI word change.
- `POST /payments/{id}/reject` (new) moves `awaiting_approval` → `rejected`
  and records the reviewer and the time.
- `execute` refuses a `rejected` payment. Today it would execute a `refused`
  one.
- The record gains `a2aTaskId`: the claims-agent task the customer is waiting
  on (§3.3). The claims-agent passes it down when it delegates, and
  `refuse_payment` writes it.
- Storage stays in memory. A restart loses waiting claims; that is acceptable
  for the demo and stays documented.

### 3.3 The waiting claim: A2A's own task lifecycle

A2A defines a task with states. `a2a-sdk` 0.3.7, already pinned, ships the
task store and an updater for them. M8 uses those states instead of building a
polling API or a callback.

1. **The customer submits a claim.** The claims-agent creates a task and
   delegates to the settlement-agent, as today.
2. **The agent proposes; the PDS denies execute.** The settlement-agent
   records `awaiting_approval` and answers "awaiting approval" with the rule
   and the limit. The claims-agent moves its task to **`input-required`**: the
   task is waiting on someone. The message tells the customer why, using the
   PDS's rule and limit.
3. **The customer-ui shows "Awaiting human approval"** with a running timer,
   and waits on **one A2A stream**. A2A closes a stream when a task reaches an
   interrupted state such as `input-required`, so the customer-ui then calls
   `tasks/resubscribe` on the task id. That call is a single streaming request
   (server-sent events) that stays open until the task changes. The
   claims-agent declares `capabilities.streaming: true` in its agent card, as
   the SDK expects.
4. **The reviewer decides in the reviewer-ui.**
   - **Approve:** the reviewer-ui executes through the gateway (§3.1). It then
     sends an A2A message to the claims-agent **on the same task id**. That is
     A2A's way of supplying an input-required task with its input. The
     claims-agent checks the ledger (`get_payment`) and completes the task with
     "Settled".
   - **Reject:** the reviewer-ui calls `/reject`, then resumes the task the
     same way. The claims-agent completes it with "Declined by a reviewer".
5. **The stream delivers `completed`**, closes, and the customer-ui renders
   the final state. If the stream drops (a network blip, a pod restart), the
   customer-ui reads the task once with `tasks/get` and resubscribes. That is
   a reconnect, not a polling loop.

A declined claim is `completed`, not A2A's `rejected`. In A2A, `rejected`
means *the agent* refused to do the task. Here the agent did the work, and a
human declined the payment.

**What is custom:** the resume handler in the claims-agent (verify the ledger,
complete the task) and the customer-ui's waiting view. Everything else is the
SDK's.

### 3.4 The trace: two traces, one link, one metric

- **Trace A** (the claim) ends when the task goes to `input-required`. It
  holds the `policy.decision` deny, with the rule and a limit of €0 or €6,000,
  and **no** `POST /payments/{id}/execute` span.
- **Trace B** (the approval) is rooted at `review.decision`. Its spans are the
  reviewer-ui's `execute_payment` through the gateway (with its own
  `policy.decision` allow, rule `human-approved.execute`), the payments-api
  execute, and the resume message to the claims-agent.
- **The link** stays an OpenTelemetry span link from `review.decision` to the
  refused span in trace A. That is OpenTelemetry's standard way to connect work
  that pauses for a person. One trace held open for minutes would show as
  unfinished in Jaeger.
- **The new metric** is one OpenTelemetry histogram,
  `northstar.approval.wait.duration` (seconds), recorded by the reviewer-ui at
  the decision. It measures the time from `awaitingSince` to the decision, with
  attributes `decision` (`approved`/`rejected`) and `autonomy.level`. Reviewer
  ids are deliberately left off: they would make the metric high-cardinality.
  The collector already has a metrics pipeline. This adds the reviewer-ui as a
  source, with no sampling and no change to the never-sample rule.
- **The customer's wait is one span.** The `tasks/resubscribe` request is
  one span per waiting claim, not a stream of polls. It carries a span link to
  the claim's trace A, and its duration is **how long the customer waited**.
  The histogram above is **how long the human took**. The two differ by the
  time the payment itself takes, and the talk can show both.
- **Measured before it is relied on:** a stream held open for 10 minutes
  survives every hop from the browser to the claims-agent (the customer-ui
  server, and agentgateway if that route goes through it), with no proxy idle
  timeout cutting it. If a hop cuts it, the fix is that hop's timeout or a
  keep-alive comment on the stream. If neither works, polling `tasks/get` is
  the fallback, and the spec is amended before it is built.

**On stage:** *"Trace A: the agent did everything up to the money. Trace B:
the human released it. The link joins them, and the histogram says how long
humans take: p50 and p95, per autonomy level."*

### 3.5 Who may act on the approval

- The reviewer's name stays an unauthenticated form field, as today. Sign-in
  for reviewers is out of scope and stays on the out-of-scope list in §8.
- The **reviewer-ui's** identity is authenticated: SPIRE, then the gateway,
  then the PDS. A human approval cannot be forged by an agent. The Kyverno
  keystone already stops agents from minting tokens or editing workloads, and
  the PDS allows `human-approved.execute` only for the reviewer-ui's SPIFFE ID.

## 4. The demo flow (replaces M7 §7.1)

Resting state: `assisted`. Two browser tabs (customer-ui, reviewer-ui) and the
terminal.

| # | What | ~Time | What the audience sees | Talk point |
|---|---|---|---|---|
| 1 | `demo guard` | 45s | unchanged from M7: 401 without identity; dropped around the gateway; the tools list with identity | Identity, Reach |
| 2 | Claim €5,800 in the customer-ui → approve in the reviewer-ui | 90s | "Awaiting human approval" with a timer; the reviewer sees rule `assisted`, limit €0, and the model's narrative; approve; the customer-ui flips to "Settled" | Maker-checker; human in the loop |
| 3 | `demo autonomy autonomous` → `demo claim 5800` → `demo claim 6200` | 75s | €5,800 settled by the agent, nothing waiting; €6,200 waits (limit €6,000) | One live dial; the band widens, the human stays |
| 4 | `demo keystone` | 45s | unchanged | Keystone |
| 5 | `demo evidence` | 45s | For each claim: who decided, the rule, who approved, how long the human took | Prove |

That is about 5:00. `demo reset` returns to `assisted`, removes the
over-grant, and leaves no task waiting: it rejects any open approval as
`demo-reset`.

`scripts/demo` gains `approve <claim>` and `reject <claim>`. They call the
reviewer-ui's own endpoint, so they are the stage fallback if a browser
misbehaves, and the path the e2e test drives.

## 5. Guardrails in agentgateway itself

The ceiling is in the config and tested, but it is **not** shown in the live demo (Kasper, 2026-10-13: "we will not need it for this demo"). It lives on the deck. The talk shows **two ways to say no**:

1. **A gateway guardrail (CEL, config only, no code).** One route-level
   `authorization` rule in `agentgateway-config` sets a hard ceiling:

   ```
   mcp.methodName != "tools/call"
     || mcp.tool.name != "execute_payment"
     || (mcp.tool.arguments.amount > 0 && mcp.tool.arguments.amount <= 50000)
   ```

   It applies to every caller, the reviewer-ui included. It is written as an
   *allow* rule, because a `deny:` rule fails open on a string amount
   (measured). It answers 403 `authorization failed` before the PDS is asked.
2. **The decision service (the PDS).** It provides the live dial, the named
   rule, the reason the customer is shown, and the `policy.decision` span.

The deck's "Why not a CEL rule" slide is replaced by "Two ways to say no", with
the measured trade-off:

| | agentgateway CEL | PDS |
|---|---|---|
| Code to write | none | a service |
| Sees tool arguments | yes, route level, v1.6.0+ | yes |
| Changes live | config reload (~0.36 s) | flag flip, nothing reloads |
| Says why | `403 authorization failed` | rule, limit, a reason the model can explain |
| In the trace | the gateway span only | `policy.decision` with rule and limit |

A second slide lists the guardrails agentgateway v1.6.0 ships for **model
traffic**: regex and personal-data masking, moderation, a webhook hook, and
the Bedrock, Azure Content Safety and Google Model Armor connectors (read from
`crates/agentgateway/src/llm/policy/` at the v1.6.0 tag). It also says plainly
that Northstar's model calls do not go through the gateway, so these are not
in the live demo. Routing them through the gateway is a separate milestone.

**Two things are measured, not assumed:** the ceiling's 403 leaves **no**
`policy.decision` span, and `initialize` and `tools/list` still pass for every
caller.

## 6. Tests

- **Unit:** the new PDS table (assisted €0 denies €0.01; autonomous allows
  €6,000 and denies €6,000.01; reviewer-ui is allowed only on
  `awaiting_approval`; the senior rows are gone); the ledger transitions
  (reject, execute-after-reject refused); the customer-ui waiting view.
- **e2e (`tests/e2e/test_human_in_the_loop.py`), live, gated:**
  - assisted €5,800 → the task is `input-required` → approve → `completed`,
    the ledger shows `executed`, trace B links to trace A, and the histogram
    has a sample;
  - reject → `completed` "Declined", the ledger shows `rejected`, and execute
    afterwards is refused;
  - autonomous €5,800 → `completed` with no wait; €6,200 → `input-required`.
  - the stream: a resubscribe held open across a 10-minute wait delivers
    `completed`; a dropped stream reconnects and still sees the outcome.
- **e2e gateway:** €60,000 → 403, no `policy.decision` span;
  `initialize` and `tools/list` pass.
- **The five-minute flow test** follows §4.
- **Telemetry contract:** new rows for the task states, trace B's spans, the
  histogram and the resubscribe span, all MEASURED in the gate.

## 7. Build: one lean milestone, five tasks

1. **Policy and ledger.** The PDS table (§3.1), the senior agent removed,
   payments-api statuses, `/reject`, `a2aTaskId`.
2. **The task lifecycle.** First, measure the 10-minute stream across the
   real path (§3.4). Then the claims-agent task store, `input-required`,
   streaming and resubscribe, the resume handler; the settlement-agent's "awaiting approval" reply; the
   reviewer-ui executing and resuming; the customer-ui waiting view.
3. **Trace and metric.** Trace B's spans, the link, the histogram, the
   collector pipeline, the contract rows.
4. **The gateway guardrail.** The CEL ceiling, the guard probe, the e2e test.
5. **Demo, runbook, deck.** `scripts/demo` (`approve`, `reject`, the new
   guard), the runbook, the deck slides and notes. Then the review, then one
   gate run.

## 8. Out of scope

- Reviewer sign-in (who the human is stays a form field).
- Routing model traffic through agentgateway (§5's second slide).
- Persistent storage for the ledger or the task store.
- Expiry, escalation or a second approver for waiting claims.
- A2A push notifications. They are webhooks to a server, and a browser
  can't receive one; streaming is the standard for a browser client.
