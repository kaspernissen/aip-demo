# Northstar M10 — the wait for a human is two traces and a link

Status: approved in conversation 2026-10-18; this document is the written spec.
Builds on M9 (`2026-10-16-northstar-m9-weaver-registry-design.md`), branch
`m10-span-links` from `main` at `2e4d417`.

## 1. Why

A claim the platform routes to a human waits for as long as the human takes.
Since M8 the claims-agent has modelled that wait as work in progress: the first
`execute()` stays running, parked on an in-process future, so that
`tasks/resubscribe` keeps the task's event queue (a2a-sdk 0.3.7 removes the queue
when `execute()` returns). The cost shows in trace A. When the customer closes the
claim stream, the SDK starts `background_consume`, which runs `consume_all` with a
0.5 s dequeue timeout, and every dequeue is an `EventQueue.dequeue_event` span:
313 spans for an 83 s wait (`agents/claims-agent/NOTES.md`), still growing while
the claim waits. The tests ignore them (`steady_ignoring`); the runbook tells the
operator to open trace A only after the decision.

The telemetry is describing the implementation, not what happened. What
happened is two pieces of work separated by human time: the agent's attempt,
which ends where the platform says *a person decides*, and the person's
decision, which releases the payment. OpenTelemetry's model for causally related
work separated by an unbounded wait is two traces joined by a span link, not one
parent span held open for minutes. The talk's spine is "know what the agents did
and why"; a trace that ends cleanly at the hand-off, and a second trace that
starts with the human and points back, says that directly.

Decisions made by Kasper (2026-10-18):

- Span links, not a long-lived trace (option 1 of three: links / suppress the
  queue spans / change nothing).
- The customer learns the outcome by **A2A push notification**, not by polling
  `tasks/get` and not on demand.
- The TaskStore stays **in-memory**. A claims-agent restart still loses waiting
  claims, as today; persistence is carried, not built.
- The push receiver is **approach (a)**: the customer-ui image swaps nginx for a
  small instrumented Python/Starlette server (rejected: Next.js server mode; a
  separate notify Deployment).

## 2. What the demo shows after M10

- **Trace A, the agent's work.** The customer submits on `message/stream`; the
  claims-agent delegates; the settlement-agent proposes; agentgateway asks the
  PDS; the PDS answers *sent to a human*; the claims-agent records the task as
  `input-required` and `execute()` returns. The stream ends. Trace A is finite
  and finished: no `background_consume`, no `EventQueue.dequeue_event`, no
  `_run_event_stream` span left open.
- **Trace B, the human's work.** `northstar.review.decision` (reviewer-ui, already
  linked to trace A since M8) → the release through agentgateway under
  `human-approved.execute` → the reviewer-ui's `message/send` on the same task id
  → a fresh claims-agent `execute()` that reads the ledger, never trusting the
  resume → the task completes → the A2A push to the customer-ui → the browser's
  events stream ends with the outcome.
- **The link, twice.** `northstar.review.decision` links to trace A's refusal (as
  today). The claims-agent's resume span adds its own link to the span context it
  saved when the task parked: the claims-agent's record that this is the same A2A
  task, independent of what the reviewer-ui claims.
- `scripts/demo evidence` shows the same lines as today; "outcome" and
  "approved by" are read from trace B, followed from trace A by the link.

## 3. Design

### 3.1 claims-agent (`agents/claims-agent/`)

- `executor.py`: on a payment routed to a human, publish the `input-required`
  status, store trace A's span context (trace id, span id, flags) in the task's
  `metadata` under `northstar.a2a.parked_span_context`, and **return**. The
  parked future and the M8 "execute() must stay running" machinery go.
- On a resume (`context.current_task` is `input-required`), start the resume span
  as today inside the inbound trace (trace B), add a span link to the stored
  context with link attribute `northstar.a2a.link.reason = "resume"`, read the
  ledger, and complete or refuse exactly as M8 does (executed → Settled,
  rejected → Declined, anything else → refused, task keeps waiting). A stored
  context that is missing or malformed means no link and a WARNING log, never a
  failed resume.
- The `final=False` workaround stays only where a test shows it is still needed;
  the spec does not presume either way.
- `main.py`: `DefaultRequestHandler` gets `InMemoryPushNotificationConfigStore`
  and `BasePushNotificationSender` with an `httpx.AsyncClient` that
  `HTTPXClientInstrumentor` instruments, so the push carries trace B's context.
- **Push URL allowlist.** The claims-agent accepts a push config only if its URL
  is on `CLAIMS_AGENT_PUSH_URL_ALLOWLIST` (exact-match list from the Deployment's
  env; default the customer-ui's in-cluster push route). Anything else is refused
  with a JSON-RPC `InvalidParams` before the task starts. The SDK has no hook for
  this, so the check wraps the request handler; the wrapper is the one place it
  lives.
- Agent card (`contracts/agent-cards/claims-agent.json`): `pushNotifications:
  true`.

### 3.2 customer-ui (`apps/customer-ui/`)

- The image's second stage becomes a Python/Starlette server (same Python
  version, OTel bootstrap and instrumentation as `apps/reviewer-ui/`), replacing
  nginx. It:
  - serves the Next.js static export unchanged (the build stage is untouched);
  - `POST /a2a/push`: accepts an A2A push only with the
    `X-A2A-Notification-Token` it issued for that task (constant-time compare),
    stores the latest task snapshot per task id, and wakes any waiter;
  - `POST /claims/{taskId}/push-token`: issues the per-claim random token the
    browser puts in `pushNotificationConfig` (the token lives only server-side
    and in that one request);
  - `GET /claims/{taskId}/events`: SSE to the browser; sends the stored snapshot
    at once if it is terminal, else waits on an `asyncio.Event` (no loop, no
    timeout-poll) and sends the snapshot when the push arrives, then ends.
  - Snapshots and tokens live in memory with a bounded size (oldest evicted);
    a customer-ui restart loses them and the browser falls back to one
    `tasks/get`.
- `src/a2a.ts`: `streamClaim` sends `configuration.pushNotificationConfig`
  `{url: <in-cluster push route>, token}`; on `input-required`, `awaitOutcome`
  opens the events stream instead of `tasks/resubscribe`. A dropped events stream
  reads the task once with `tasks/get` and reopens; `resubscribe()` is deleted.

### 3.3 Reach (`deploy/platform/cilium-policies.yaml`)

- claims-agent egress gains exactly one allow: `customer-ui:8080` in
  `northstar-agents` (TCP). Nothing else changes.
- If customer-ui has an ingress policy, it gains the matching allow from
  claims-agent; if it has none, the spec does not add one.

### 3.4 Telemetry contract (`telemetry/registry/`)

- One new registered attribute, `stability: development`:
  `northstar.a2a.link.reason` (enum: `resume`), set on the resume span's link.
  `northstar.a2a.parked_span_context` is A2A task metadata, not telemetry, and is
  not registered. Live-check runs over the new spans in the gate.
- The registry and `docs/telemetry-contract.md` say that trace A ends at the
  hand-off and name the link that joins B to A.

### 3.5 Tests and the harness

- `agents/claims-agent/tests/test_task_lifecycle.py`: the resubscribe tests go;
  new tests pin: `execute()` returns at `input-required`; the task is stored as
  `input-required` with the parked context; a resume on that task id completes it
  from the ledger; a ledger-unbacked resume is refused and the task keeps
  waiting; a resume after completion is refused; a resume span carries one link
  to the parked context; a malformed stored context resumes without a link.
- Push guardrails, each pinned: a push URL off the allowlist is refused; a push
  without or with the wrong token is rejected by the customer-ui; the claims-agent
  cannot reach any customer-ui port but 8080 (dataplane test, Reach style).
- `tests/harness/jaeger.py`: `steady_ignoring` goes. In its place,
  `test_human_in_the_loop` asserts **zero** `EventQueue.dequeue_event` spans in
  trace A after a wait of at least 10 s, and that trace A's span count does not
  change across that wait (the parity guard that the old behaviour stays gone).
- The e2e HITL test follows the claim from trace A through the link into trace B
  and asserts the completion and the push are in trace B.
- customer-ui vitest: the events-stream path, the drop-then-`tasks/get` path,
  and a push landing before the browser subscribes.

### 3.6 Docs and deck

- `agents/claims-agent/{NOTES.md,docs/index.md}`, `docs/demo-runbook.md`
  ("open trace A after the decision" goes), the customer-ui docs, the README's
  flow.
- Deck: the HITL trace slide shows two traces and a link; the speaker note says
  why (work separated by human time). Slide count and timing are derived by the
  build, not retyped.

## 4. Unchanged

The money path (gateway, ExtMCP, PDS failClosed, the €50,000 CEL ceiling,
`human-approved.execute`, the ledger check on resume); never-sample; the
in-memory TaskStore and its restart loss; OpenFeature/flagd autonomy; the Kyverno
keystone; the Weaver registry as the checked contract.

## 5. Risks

- **a2a-sdk 0.3.7 behaviour after `execute()` returns at `input-required`** is
  read from source, not yet measured: the stream ends, the queue is removed, a
  `message/send` on the task id starts a new `execute()`. Task 1 measures it
  first; if any of it is false, stop and rule before building on it.
- **Push delivery is fire-and-forget in the SDK.** A failed push is logged by the
  sender and not retried; the browser's drop path (`tasks/get`) is the recovery.
- **Static export served by Python** must keep the same paths and headers the
  nginx config set (read `nginx.conf` and match it).

## 6. Scope

Lean, five tasks: (1) claims-agent return-and-resume with the link, measured
first; (2) push sender, allowlist, agent card; (3) customer-ui server and
browser; (4) Reach, registry, harness and e2e; (5) docs and deck. One review of
the risky tasks (1, 3, 4), then one gate run, after the review.
