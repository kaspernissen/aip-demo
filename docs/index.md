# Northstar

**A motor-claims settlement system where agents do the work and the platform
decides what they are allowed to do.**

A claimant files a vehicle-damage claim. Two agents read it, assess it and
propose a settlement. Whether that settlement is *paid* is not the agents'
decision to make — and the interesting part of this system is the machinery
that makes that sentence true rather than aspirational.

---

## The claim's journey

<pre class="mermaid">
flowchart TD
    browser(["browser"])
    claims["claims-agent"]
    settle["settlement-agent"]
    gw{{"agentgateway"}}
    pds["policy-decision-service"]
    flag[("`northstar-autonomy
      OpenFeature flag`")]
    mcp["payments-mcp"]
    api[("`payments-api
      system of record`")]

    browser -->|A2A| claims
    claims -->|A2A| settle
    settle -->|"MCP + JWT-SVID"| gw
    gw <-->|"gRPC — permit or deny"| pds
    pds -->|reads| flag
    gw -->|"permit only"| mcp
    mcp -->|"HTTP/JSON"| api
</pre>

Four hops, and a fifth that is not on the line: **every `tools/call` is
referred to the policy decision service before it is forwarded**. The gateway
is configured `failureMode: failClosed`, so a policy service that is down
refuses payments rather than waving them through.

| Hop | From → To | Protocol | Carries |
|---|---|---|---|
| 1 | browser → claims-agent | A2A / JSON-RPC over HTTP | the claim; the trace **root** span |
| 2 | claims-agent → settlement-agent | A2A / JSON-RPC over HTTP | delegation, behind an allow-list |
| 3a | settlement-agent → agentgateway | MCP over streamable HTTP | `Authorization: Bearer` **JWT-SVID**, trace context |
| 3p | agentgateway → policy-decision-service | gRPC (ExtMCP) | caller identity + amount → **permit or deny** |
| 3b | agentgateway → payments-mcp | MCP | only on a permit; the credential is **stripped** |
| 4 | payments-mcp → payments-api | HTTP/JSON | the payment itself |

## What actually stops a payment

Four independent things, and they are independent on purpose — none of them
relies on an agent choosing to behave.

1. **The policy decision service** is the only decision point. A hermetic test
   walks the source of `settlement-agent` and `payments-mcp` to prove no policy
   logic has leaked into either of them.
2. **The autonomy contract** — an OpenFeature flag, `northstar-autonomy`,
   served by a flagd sidecar — decides whether a settlement may execute at all
   or must go to a human. It ships **refusing**: `defaultVariant: assisted`,
   with both targeting branches also `assisted`.
3. **Workload identity.** The caller is an attested SPIFFE ID, not a
   self-declared name. The gateway verifies the SVID and strips it before
   forwarding.
4. **The network**, as the second defence: nothing in the agent plane can reach
   the policy service, and `reviewer-ui` has no entry in the execute-limit
   table at all — not "it does not try to execute payments", but "it could not
   if it did".

## Where it refuses, and why that is the point

A refusal is **the platform working**, and the system is built to say so
clearly rather than to look like a failure:

- the gateway's own span reports `grpc.status = 0` on a refusal — the *call*
  succeeded, the deny is in the payload, which is how "policy said no" stays
  distinguishable from "policy service was down";
- the `northstar.policy.decision` span is deliberately **not** `ERROR` on a denial;
  marking it an error would page an on-call engineer for correct behaviour;
- both demo UIs render a refusal in **ochre, never red**.

## What you can look at

| Surface | Where | What it shows |
|---|---|---|
| Claim form | [localhost:8080](http://localhost:8080) | filing a claim, and the determination |
| Review console | [localhost:8081](http://localhost:8081) | settlements awaiting a human, and the ledger |
| Autonomy contract | [the OpenFeature page](/autonomy-contract) | the live flag, and the one write that flips it |
| Traces | [Jaeger](http://localhost:16686) | one trace, end to end, across every service |
| Metrics | [Prometheus](http://localhost:9090) | request rates, LLM tokens, model cost |
| The catalog | [Components](/catalog) | every component, its docs, and its own links into the above |

Each component's entity page carries its own pre-filtered links into Jaeger
and Prometheus — no hunting for the right query.

## Read next

- **[Architecture](architecture.md)** — the long form: every hop, the decision
  logic, the identity story, what is real and what is deferred, and a
  span-by-span walk of the trace.
- **[Running the demo](demo-runbook.md)** — the operator's document: the
  seven-step flow, what to say, what to show, how to reset, and what to do when
  something breaks.
- **[Telemetry contract](telemetry-contract.md)** — what every span and metric
  must carry, the conventions used and the known gaps.
- **[Upstream findings](upstream-findings.md)** — filing-ready findings against the
  upstream projects this system is built on.

## Honest limits

This is a laptop demo cluster, and two things are true that would not be
acceptable anywhere else:

- **This portal is unauthenticated.** Anyone who can reach the port can flip
  the autonomy guardrail. What keeps it honest is how narrow the write is —
  the card's ServiceAccount can `get/list/patch` exactly one named resource
  and nothing else.
- **Authorization is exactly one hop.** The browser → claims-agent,
  claims-agent → settlement-agent and payments-mcp → payments-api hops carry
  no identity and consult no policy. `claims-agent` and `customer-ui` still
  share one SPIFFE ID.
