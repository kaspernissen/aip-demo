# Northstar M3 "Supervised" — design

**Status:** approved 2026-09-10. Builds on M2 "Governed" (`main` @ `b472c89`).

**One line:** the refusal M2 ends on becomes a decision a human can make, and the
approved payout executes under a *different identity* — not under an override.

---

## 1. What M3 is

M2 ends on "no". The settlement agent proposes €6,200, the platform refuses it at
the choke point, and the agent narrates:

> "I'm unable to complete the execution of this payment myself, as it exceeds the
> authorization limit I currently hold. Your claim will need to be forwarded to a
> colleague with higher approval authority to finalize the payment."

That sentence is currently a polite fiction — there is no colleague. M3 builds
one, and in doing so gives the demo a second act: a human reviewer sees the
refused settlement, approves it, and the payment executes.

**The decisive property: approval changes who asks, not what is allowed.** There
is no approval flag, no override branch, no "permitted because a human said so"
input to the policy engine. The approved execution is performed by a workload
with a different SPIFFE ID whose row in `EXECUTE_LIMITS` carries a higher limit.
The PDS permits it under **the same rule it used to refuse** — `execute_limit` —
having evaluated a different principal.

This matters beyond elegance. A demo that adds an approval token to the policy
input has added a bypass, and the interesting question ("could someone forge
one?") is then the only question worth asking about the whole milestone. Keeping
approval *outside* the policy engine means M2's guarantees are untouched by M3.

### What M3 does not do

No reject workflow beyond recording the rejection. No reviewer queue,
assignment, or notification. No SLA timers. These are product features; none of
them shows anything on stage, and each one is a place for the demo to break.

---

## 2. Architecture

```
customer-ui ──A2A──> claims-agent ──A2A──> settlement-agent ─┐
                                                              │ MCP
                                              agentgateway <──┘
                                                   │ ExtMCP (gRPC)
                                           policy-decision-service
                                                   │
                                          [DENY: 6200 > 6000]
                                                   │
                                    payments-api: status = refused
                                                   │
                                          reviewer-ui (lists it)
                                                   │  human approves
                                                   ▼
                              senior-settlement-agent ──MCP──> agentgateway
                                                                    │
                                                    policy-decision-service
                                                                    │
                                              [PERMIT: 6200 < 25000, same rule]
```

### The same code, deployed twice

`senior-settlement-agent` is **the settlement-agent image**, unmodified, in a
second Deployment with its own ServiceAccount. Same prompt, same tools, same MCP
path, same gateway. SPIRE's `spiffeIDTemplate` is already
`spiffe://{{ .TrustDomain }}/ns/{{ .PodMeta.Namespace }}/sa/{{ .PodSpec.ServiceAccountName }}`,
so a new ServiceAccount is a new identity with no registration work beyond the
ServiceAccount itself.

On stage this is the point worth pausing on: *the code is byte-identical; the
platform treats the two differently because of who they are, and you can read
the difference off one span attribute.*

### Where the durable state lives, and why not Dapr

The M2 spec earmarked Dapr Workflow for this milestone. It is not needed and is
deliberately deferred.

A refused settlement is **already durable**: `payments-api` holds it as a
`proposed` payment with an id. What is missing is not durability but a *reason*
and a *list*. Adding Dapr would introduce a sidecar per workload, a state store,
and an extra hop in every trace — the same objection that deferred Linkerd, and
resolved the same way: deferred with an attached experiment, to be revisited
when the platform actually needs timeouts, retries and multi-step resumption
(M5's territory, where autonomy profiles make workflows genuinely branchy).

**The attached experiment**, to be run before M5 decides: instrument one
settlement as a Dapr Workflow, and measure whether the workflow's own spans join
this trace or fragment it. Same question, same shape, same standard of evidence
as the Linkerd deferral.

---

## 3. Components

| Component | Status in M3 | Language |
|---|---|---|
| `reviewer-ui` | **new** — the HITL stage surface | Python (FastAPI + server-rendered HTML) |
| `senior-settlement-agent` | **new Deployment**, existing image | — |
| `payments-api` | `refused` status, denial record, list endpoint | Go |
| `settlement-agent` | one new entry point; prompt unchanged | Python |
| `policy-decision-service` | one new `EXECUTE_LIMITS` row; no logic change | Python |
| `agentgateway`, SPIRE, Cilium | new ServiceAccounts and egress rules only | — |

`reviewer-ui` follows `customer-ui`'s existing pattern rather than introducing a
frontend toolchain. It is a stage surface: it must be legible from the back of a
room, and it must show the policy decision, not just a button.

---

## 4. The refusal record

`payments-api` gains a third status, `refused`, and a record beside it:

| Field | Source | Why |
|---|---|---|
| `rule` | PDS decision | what the platform decided on |
| `limit` | PDS decision | the authority the caller held |
| `requested` | PDS decision | what was asked for |
| `narrative` | the agent's own words | the reviewer sees what the claimant saw |
| `refusedTraceId` | the refused run's trace | the span link's target |
| `refusedAt` | server clock | measuring how long the human took |

`refused` is a **terminal-until-approved** state, not a fourth state machine.
`executed` remains reachable only through the existing execute path — the one the
PDS guards. Approval does not write `executed`; it starts a run that may.

**The honesty rule is unchanged from M1:** every amount, currency, status and
payment id on any surface comes from this ledger. Only `narrative` is the
model's, and it is stored as what the model said, never as what happened.

---

## 5. Identity and the policy

One new row, no new logic:

```python
EXECUTE_LIMITS: dict[tuple[str, str], float] = {
    ("spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent",
     "assisted"): 6000.0,
    ("spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent",
     "autonomous"): 1_000_000.0,
    # M3: the colleague with higher approval authority.
    ("spiffe://northstar.local/ns/northstar-agents/sa/senior-settlement-agent",
     "assisted"): 25_000.0,
}
```

€6,200 refused for one principal and permitted for another, under the same rule,
with the same code path, is the whole milestone in one table.

`reviewer-ui` gets its own SPIFFE ID and **no** `EXECUTE_LIMITS` row at all — it
must never be able to execute a payment. Its `unknown_caller` refusal is worth a
test: the UI that approves payouts cannot make one.

The human's identity travels in the approval payload, not in the SVID. The SVID
attests the *workload*; the payload carries *who clicked*. Conflating them would
mean minting per-human SVIDs, which is a real design in real systems and far
past what this demo needs to show.

---

## 6. Telemetry

Two additions, both ours to define, neither invented for third-party components.

**`review.decision`** (SpanKind.SERVER, on `reviewer-ui`):

| Attribute | Example |
|---|---|
| `review.payment.id` | `PAY-000053` |
| `review.decision` | `approve` \| `reject` |
| `review.reviewer.id` | `alex.mercer` |
| `review.reviewer.spiffe_id` | the UI's SVID |
| `review.refused.trace_id` | the refusal trace |
| `review.elapsed_ms` | time from `refusedAt` to the decision |

**The span link.** The approved execution is a **new trace**, carrying a span
link to the refusal trace. Not a continuation: a human took minutes, the original
process is gone, and a single span held open across that gap would misrepresent
both the duration and what was executing. Two linked traces are the honest shape,
and navigating the link on stage is the first moment in this demo where
OpenTelemetry does something a log line cannot.

`policy.decision` is unchanged — same nine attributes, same span name. The
side-by-side of two `policy.decision` spans, same `policy.rule`, opposite
outcomes, different `policy.caller.spiffe_id`, is the payoff screen.

**Not published:** any exact whole-trace span total. They are non-deterministic
on this stack (111, 113, 117 measured for one scenario).

---

## 7. Exit criteria

1. `CLM-1842` submitted in the browser is refused exactly as in M2.2 — no
   payment executed, ledger still unexecuted. A regression test on M2's promise.
2. The refusal appears in `reviewer-ui` showing rule, limit, requested amount and
   the agent's narrative — all read from the ledger.
3. A human approves in the browser; the payment executes; the ledger reads
   `executed` at the same amount, with the same payment id.
4. The PDS permitted it under **the same rule** (`execute_limit`) with a
   different `policy.caller.spiffe_id` — asserted from the spans, not narrated.
5. The approved-execution trace carries a span link to the refusal trace, and
   `review.decision` records the reviewer and the elapsed time.
6. **The junior identity still cannot execute €6,200 after an approval exists.**
   The approval must not have widened the policy for the original caller. This is
   the anti-bypass criterion and the one most worth having in the room.
7. `reviewer-ui`'s own SPIFFE ID cannot execute a payment (`unknown_caller`).

---

## 8. Carried debt, cleared in this milestone

M1.2's four items were marked "to be cleared early in M2" and were not. They are
Task 1 here, batched:

- No service has an advisory gate; extend `test_pins.py`'s pattern.
- ~~`claims-agent` and `payments-mcp` do not pin full transitive closures.~~
  **Already cleared, 2026-09-10.** Both files were fully pinned by M2.1 Task 6,
  which cited this very list and never struck itself off it. Verified two ways
  before the entry was retired: every non-comment line carries `==`, and each
  file's installed closure was diffed against its own venv in both directions
  with zero mismatches. Task 1 made no edit here.
- Ruling 5's single-asyncio-style decision was recorded and never applied.

> **This is the stale-prose defect class, for the third time.** M2.1's own
> closing lesson was that 13 of 13 final-review findings were sentences true
> when written and falsified by a later task. This entry was one of them and
> survived into two later planning documents — including this spec — because a
> task cleared the debt without clearing the record of it. **Clearing an item
> includes striking it off every list that names it**, and the M2 spec's §11 now
> carries the same correction.

**OQ-1 (the content-capture trade-off) stays open** and is explicitly *not* Task
1's business. It is a real experiment about what tracing an agent costs in
confidentiality, it deserves its own measurement, and folding it into a cleanup
batch would bury it. It moves to M4, where evals make the trade-off concrete.

## 9. Carried debt created by M2, not cleared here

- **M2 criterion 7** — direct agent → model-provider egress is still allowed.
  Closing it means routing model traffic through agentgateway's LLM path. Still
  unowned; a candidate for M4 or M5, not M3.
- **OQ-U1** — whether agentgateway v1.5.0 applies `mcpGuardrails` per-message
  inside a JSON-RPC batch. Unresolved, and a real bypass if unguarded. **This
  milestone adds a second privileged identity behind the same gateway**, which
  raises the stakes: it should be measured with a live batch probe during M3,
  even though closing it may not be M3's work.
