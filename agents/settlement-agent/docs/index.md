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

There is one settlement agent and one identity, and its limit is a function of
the autonomy dial, not of who it is:

```
settlement-agent @ assisted     → spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent → €0
settlement-agent @ autonomous   → spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent → €6,000
```

The dial is a maker-checker policy. At `assisted` the agent may propose and
nothing more; at `autonomous` it may release up to €6,000. Above its band the
agent refuses, and the payment moves to `awaiting_approval` in the ledger. A
human then releases it through the **reviewer-ui**, which calls `execute_payment`
itself, through the same gateway and the same policy decision, under its own
identity. Nothing is overridden: the policy permits the reviewer-ui under
`human-approved.execute`, and only for a payment the ledger shows awaiting
approval. (A second Deployment, `senior-settlement-agent`, did this job until M8
and has been removed.)

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
