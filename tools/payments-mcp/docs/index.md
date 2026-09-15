# Payments MCP

The MCP server the settlement agents call. **An adapter over `payments-api`,
not a system of record** — its own module docstring says so, and it owns no
state whatsoever.

## Tools

| Tool | What it does |
|---|---|
| `propose_payment` | Registers an intended payment and returns its id |
| `execute_payment` | Asks `payments-api` to actually move the money |
| `refuse_payment` | Records *why* a payout did not happen |
| `get_payment` | Reads one payment back |

## The call never arrives directly

Settlement agents do not reach this service. They call **agentgateway**, which
verifies their JWT-SVID, asks the Policy Decision Service for a verdict, and
forwards here only on a permit.

In a refused €6,200 settlement there is **no `tools/call execute_payment` span
on this service at all** — the call never got here. That absence is the
trace-level evidence that nothing happened, rather than something happening and
failing.

## Interfaces

| Direction | Protocol | Detail |
|---|---|---|
| In | MCP over HTTP | port `8000`, endpoint `/mcp` |
| Out | HTTP / JSON | `payments-api:8080` |

## Telemetry

`gen_ai.tool.name` is present on the `tools/call` spans. `gen_ai.operation.name`
is **not** — a published convention gap, not an oversight.

`northstar.claim.id` is deliberately **absent** here: the claim id stops at the
MCP boundary. That is Gap 4, and it is why the evidence dashboard joins on trace
id rather than claim id.

## Known limits

- The outbound hop to `payments-api` carries **no identity and no policy**.
- A JSON-RPC **batch** cannot smuggle a call past the gateway's policy hook —
  measured and closed: the gateway rejects the batch at deserialization with
  HTTP 400 and no decision span is ever created.
