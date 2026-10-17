# Customer UI

The claimant's portal — and **the owner of the trace root**. It is the only
component in the system a customer touches directly.

## Responsibilities

- Present the claim form (pre-filled: `CLM-1842`, `POL-77120`, €6,200).
- Start the `northstar.claim.submit` span, which is the root of the whole distributed
  trace. It has no parent, and its two attributes
  (`northstar.claim.id`, `northstar.claim.policy_number`) are set here in the
  browser, propagated from nowhere.
- Submit over A2A to `claims-agent` and render the determination.

## What the claimant sees

A determination, the way an insurer writes back — not a toast:

| Status | Rendered as | Why |
|---|---|---|
| `refused` | **ochre**, not red | A refusal is the platform working. Colouring it as an error would contradict the system's own design, where `northstar.policy.decision` is deliberately not an ERROR span. |
| `executed` | green | Settled, no human involved |

## Build and serving

TypeScript, Next.js with `output: "export"` — a **static** export served by
nginx. There is no Node process in the running pod.

Reached on host port `8080`, a kind `extraPortMapping` onto a NodePort, so it
needs no `kubectl port-forward` and survives a redeploy.

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
