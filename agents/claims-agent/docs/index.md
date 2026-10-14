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
| In | A2A / JSON-RPC 2.0 | port `9001`: `message/stream` and `tasks/resubscribe` from the browser, `tasks/get` and `message/send` (the resume) from the reviewer-ui |
| Out | A2A | `settlement-agent:9002` |
| Out | HTTP | `payments-api:8080`, one read: `GET /payments/{id}`, when a task is resumed |

## The waiting claim

A claim moves `submitted → input-required → completed`, or `submitted →
completed` when the settlement agent settles it. The status message is one JSON
text part: `claimId`, `policyNumber`, `paymentId`, `amount`, `currency`,
`status` (`executed`, `awaiting_approval`, `rejected`), `narrative`, `rule`,
`limit`, `decision` (`null`, `approved`, `rejected`).

- The first `execute()` stays running while the task is `input-required`. It is
  parked on an in-process future. The SDK removes a task's event queue when
  `execute()` returns, and `tasks/resubscribe` needs that queue. So a claims-agent
  restart loses the waiting claims.
- The reviewer-ui resumes the same task id. The resume message is never
  trusted: the claims-agent reads the payment from the ledger and reports
  what the ledger says. A resume the ledger does not back is refused, and the
  task keeps waiting.
- Both status updates are `final=False`. The stream ends when the parked
  `execute()` returns. Clients end on the task state, not the `final` flag.
- A claim must be submitted with `message/stream`. A blocking `message/send`
  would hang for the whole wait.

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
