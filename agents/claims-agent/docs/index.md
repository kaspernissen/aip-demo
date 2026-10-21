# Claims Agent

The intake agent. It is the first thing the browser talks to, and the thinnest
component in the chain — deliberately.

## Responsibilities

- Serve A2A on port `9001` for the customer UI.
- Parse the submitted claim.
- Delegate settlement to `settlement-agent` over A2A.
- Return settlement-agent's reply as the task's result. Every claim is an A2A
  task. A claim the platform routes to a human is `input-required` until the
  reviewer-ui resumes it (see "The waiting claim").

### What it explicitly does not do

- It does not decide anything about the claim.
- It does not touch the payment tools.
- It does not rewrite or summarise the settlement reply — what the claimant
  sees is what the settlement agent said. (The one exception is a refusal that
  could not be recorded on the ledger: there is nobody to review it, so the
  task completes with a plain "could not be routed for review" message.)

## Interfaces

| Direction | Protocol | Detail |
|---|---|---|
| In | A2A / JSON-RPC 2.0 | port `9001`: `message/stream` from the browser, `tasks/get` and a blocking `message/send` (the resume) from the reviewer-ui |
| Out | A2A | `settlement-agent:9002` |
| Out | HTTP | `payments-api:8080`, one read: `GET /payments/{id}`, when a task is resumed |
| Out | A2A push | `POST` of the finished Task (terminal states only; never in trace A) to `customer-ui:8080/a2a/push`, only to a URL on the exact-match allowlist (`CLAIMS_AGENT_PUSH_URL_ALLOWLIST`) |

## The waiting claim

A claim moves `submitted → input-required → completed`, or `submitted →
completed` when the settlement agent settles it. The status message is one JSON
text part: `claimId`, `policyNumber`, `paymentId`, `amount`, `currency`,
`status` (`executed`, `awaiting_approval`, `rejected`), `narrative`, `rule`,
`limit`, `decision` (`null`, `approved`, `rejected`).

- The first `execute()` records `input-required` and **returns**. Nothing is
  parked and nothing polls while the claim waits. Trace A, the agent's work,
  ends at that hand-off. A claims-agent restart still loses the waiting claims:
  the `TaskStore` is in memory (unchanged, and still documented).
- Two keys ride in the input-required status update's `metadata`, which the SDK
  stores on the task. They are A2A task metadata, not telemetry:
  `northstar.a2a.parked_span_context` (`trace_id`, `span_id`, `trace_flags` of
  the span that handed off) and `northstar.a2a.parked_reply` (the reply the
  customer is waiting on: claim id, payment id, amount, rule). Only an
  agent-side status update writes `task.metadata`, so the resume reads the
  reply from there and never from the client-writable history. A resume whose
  message is not user-role is refused.
- The person's side is trace B. The reviewer-ui records `northstar.review.decision`,
  releases the payment through agentgateway under `human-approved.execute`, and
  sends a blocking `message/send` with the same task id. That starts a fresh
  `execute()`, which opens the span `northstar.a2a.resume` in trace B with one
  span link back to the hand-off span in trace A
  (`northstar.a2a.link.reason = resume`).
- The resume is never trusted: the claims-agent reads the payment from the
  ledger and reports what the ledger says. A resume the ledger does not back is
  refused, and the task keeps waiting.
- The task then completes, and the SDK pushes the Task to the customer-ui
  server, which relays it to the browser. Only the outcome is pushed, in trace B:
  `TerminalPushNotificationSender` sends nothing for `submitted` or
  `input-required`, so trace A holds no push and no `customer-ui-server` span.
  The push goes only to a URL on the
  exact-match allowlist (SSRF guard), the receiver checks the token in
  `X-A2A-Notification-Token`, and customer-ui's Linkerd policy lets only claims-agent's identity
  make this one call (`POST /a2a/push`; any other caller gets a 403). Unlike
  the Cilium rule it replaced, it does not stop claims-agent reaching customer-ui's other paths. `tasks/pushNotificationConfig/get`,
  `/list` and `/delete` are refused (JSON-RPC -32004): `get` would hand the token
  to anyone holding the task id, and the customer-ui never needs them.
- A claim must be submitted with `message/stream` and a push config. The
  stream ends at `input-required`; the outcome arrives by push.

## How trace context crosses

Nothing in `delegation.py` injects a header by hand. `HTTPXClientInstrumentor`
propagates the context as a side effect of the ambient span being correct at
call time — which is the entire mechanism behind hop 2 of the trace. The
outbound client span (target `settlement-agent…:9002`) is worth clicking into
for exactly that reason.

## Known limits

- This hop carries **no identity and no policy**. The allow-list is the only
  control on it.
- `claims-agent` and `customer-ui` **share one SPIFFE ID**. That is measured,
  written down, and still open.
