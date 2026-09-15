# Settlement Agent

The agent that actually decides what a claim is worth, and then tries to pay
it. **The settlement decision is a real Claude model call**, not a stub — which
is why `ANTHROPIC_API_KEY` is required and why the first claim submission fails
without it.

## Responsibilities

- Receive a delegated claim from `claims-agent` over A2A.
- Decide the settlement with a model call (PydanticAI).
- Drive the payment tools over MCP: `propose_payment`, then `execute_payment`.
- When execution is refused, **narrate the refusal to the claimant and stop** —
  it does not retry, and a scorer checks that it did not.

### What it explicitly does not do

- **It holds no policy.** Whether it may execute a payment is not its decision
  and not in its code; a hermetic AST walk proves it.
- **It does not know its own limit.** It proposes; the platform refuses.

## Identity — the thing worth pausing on

`senior-settlement-agent` runs **this same image, byte-identical**, in a second
Deployment under a second ServiceAccount. It is not a different program. The
only difference is the SPIFFE ID it presents, and therefore the row the Policy
Decision Service looks up:

```
settlement-agent          → spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent          → €6,000
senior-settlement-agent   → spiffe://northstar.local/ns/northstar-agents/sa/senior-settlement-agent   → €25,000
```

When a human approves a refusal, **nothing is overridden** — a different caller
is asked about.

> Both Deployments report `service.name=settlement-agent`, because
> `OTEL_SERVICE_NAME` is deliberately the same. Tell them apart by `jwt.sub` or
> `policy.caller.spiffe_id`, **never** by service name. This looks like a bug
> and is not.

## Interfaces

| Direction | Protocol | Detail |
|---|---|---|
| In | A2A / JSON-RPC 2.0 | port `9002` |
| Out | MCP | through **agentgateway**, never directly to `payments-mcp` |
| Identity | SPIFFE | JWT-SVID from the SPIRE Workload API, mounted at `/spiffe-workload-api/spire-agent.sock` |

## Telemetry

`gen_ai.client.token.usage` is the **one** GenAI metric anything in this system
emits — five of the six the spec put in scope are published as gaps rather than
invented.

Its outbound MCP call is where the caller identity first becomes real: the
gateway sets `jwt.sub` from the token it verified, not from anything this agent
sends.

## Run and test

```bash
cd agents/settlement-agent
.venv/bin/python -m pytest tests -q
```

Its suite imports `pds.server`, so the Policy Decision Service's generated gRPC
stubs must exist first:

```bash
(cd platform/policy-decision-service && ./gen-stubs.sh)
```

## Known limits

- The A2A hop from `claims-agent` is protected by an **allow-list only** — no
  identity, no policy.
- `a2a-sdk` is pinned at 0.3.7; the 1.x migration is carried debt.
