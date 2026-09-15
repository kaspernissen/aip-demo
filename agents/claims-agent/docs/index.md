# Claims Agent

The intake agent. It is the first thing the browser talks to, and the thinnest
component in the chain — deliberately.

## Responsibilities

- Serve A2A on port `9001` for the customer UI.
- Parse the submitted claim.
- Delegate settlement to `settlement-agent` over A2A.
- Return settlement-agent's JSON reply **verbatim**.

### What it explicitly does not do

- It does not decide anything about the claim.
- It does not touch the payment tools.
- It does not rewrite or summarise the settlement reply — what the claimant
  sees is what the settlement agent said.

## Interfaces

| Direction | Protocol | Detail |
|---|---|---|
| In | A2A / JSON-RPC 2.0 | port `9001`, from the browser |
| Out | A2A | `settlement-agent:9002` |

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
