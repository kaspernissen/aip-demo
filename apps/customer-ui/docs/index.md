# Customer UI

The claimant's portal — and **the owner of the trace root**. It is the only
component in the system a customer touches directly.

## Responsibilities

- Present the claim form (pre-filled: `CLM-1842`, `POL-77120`, €6,200).
- Start the `northstar.claim.submit` span, which is the root of the whole distributed
  trace. It has no parent, and its two attributes
  (`northstar.claim.id`, `northstar.claim.policy_number`) are set here in the
  browser, propagated from nowhere.
- Submit over A2A to `claims-agent` and render the determination. When the claim waits for a person, the outcome arrives by push (below).

## What the claimant sees

A determination, the way an insurer writes back — not a toast:

| Status | Rendered as | Why |
|---|---|---|
| `refused` | **ochre**, not red | A refusal is the platform working. Colouring it as an error would contradict the system's own design, where `northstar.policy.decision` is deliberately not an ERROR span. |
| `executed` | green | Settled, no human involved |

## Build and serving

TypeScript, Next.js with `output: "export"` — a **static** export served by
a small Python Starlette server (`server/main.py`), which also receives the
claims-agent's A2A push and relays it to the browser (`/claims/push-config`,
`/a2a/push`, `/claims/events`). There is no Node process in the running pod.

Reached on host port `8080`, a kind `extraPortMapping` onto a NodePort, so it
needs no `kubectl port-forward` and survives a redeploy.

## The wait, the push and the token

The page asks the server for a push config (`POST /claims/push-config`, which
answers `{url, token}`: a fixed receiver URL and a fresh per-claim token) and
sends it with `message/stream` as `configuration.pushNotificationConfig`. The
stream ends when the claim reaches `input-required`. The page then holds
`GET /claims/events`, with the token in an `x-claim-token` header (never in the
URL), under the `northstar.claim.wait` span.

When a person decides, claims-agent's resume completes the task and the A2A SDK
pushes the finished Task to `POST /a2a/push` (terminal states only: the submitted
and input-required snapshots are not pushed, so trace A holds no push and no
`customer-ui-server` span) with the token in `X-A2A-Notification-Token`.
The server checks the token before it parses the body, relays the Task to the
open events stream, and the stream ends with the outcome. Nothing polls and the
page never resubscribes. If the events stream drops, the page reopens it; if it
is refused, it reads the task once with `tasks/get`. The relay is in memory and
single-replica, so a customer-ui restart loses the tokens.

The server is its own service, `customer-ui-server` (the browser keeps
`customer-ui`), and exports OTLP to the collector. No token is recorded on any
span. Linkerd lets only claims-agent's identity call `POST /a2a/push` on `8080` (any
other caller gets a 403); the page and the other paths stay open.

## A note for anyone editing the form

Three suites drive this page with Playwright, and they address it structurally:

- `input >> nth=0` — the claim reference must stay the **first** `<input>`
- `input[type=number]` — the amount must stay the **only** number input
- `button[type=submit]` — one
- `h2:text-is('Outcome') + pre` — adjacent siblings, nothing between them
- `text=Outcome` is *waited on* and substring-matched, so the word must appear
  **nowhere** on the page before a result exists

Each is marked with a comment beside the element that carries it.

## Known limits

- The A2A hop out of this page carries **no identity and no policy**.
- `customer-ui` and `claims-agent` **share one SPIFFE ID** — carried debt.
