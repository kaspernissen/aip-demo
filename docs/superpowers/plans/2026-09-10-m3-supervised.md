# M3 "Supervised" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A human reviewer approves the payout M2 refused, and it executes under a
different identity — not under an override.

**Architecture:** `senior-settlement-agent` is the settlement-agent image in a
second Deployment with its own ServiceAccount, hence its own SPIFFE ID, hence a
higher row in the PDS's existing `EXECUTE_LIMITS`. A new `reviewer-ui` lists
refused payments from the ledger and starts the approved run. The policy engine
gains one table row and no new logic.

**Tech Stack:** Python 3.12 (FastAPI, PydanticAI 2.41.0, a2a-sdk), Go
(payments-api), agentgateway v1.5.0, SPIRE, Cilium 1.20.1, OpenTelemetry, kind.

**Spec:** `docs/superpowers/specs/2026-09-10-northstar-m3-supervised-design.md`

## Global Constraints

- **Trust domain is `northstar.local`.** SPIFFE IDs are
  `spiffe://northstar.local/ns/<namespace>/sa/<serviceaccount>`; SPIRE's
  `spiffeIDTemplate` derives them, so a new ServiceAccount is a new identity.
- **No invented telemetry.** Telemetry from third-party frameworks must be the
  real emitted telemetry, recorded from a live run. Only telemetry from
  components in this repo may be authored.
- **No unmeasured claims.** Every number, span name, attribute key, status code
  and default written in code comments, docs or test assertions must trace to a
  live measurement or to read source.
- **No exact whole-trace span totals** may be published anywhere — they are
  non-deterministic on this stack (111, 113, 117 measured for one scenario).
- **Amounts, currencies, statuses and payment ids** on any surface come from the
  payments ledger, never from model output. Only `narrative` is the model's.
- **The policy engine gains no override input.** No approval flag, no token, no
  "permitted because a human said so". Approval changes the caller, not the rule.
- **Nothing is filed, commented, or written to any upstream repo or tracker.**
  Reading public trackers and source is fine.
- `.env` and credentials never appear in committed content; report secrets as
  `SET`/`EMPTY` only, never values, and never `set -x` near secret handling.
- **Each Python service's suite runs from its own directory with its own
  `.venv/bin/python -m pytest`.** Bare `python3` here is 3.10.4 and `uv run
  pytest` grabs homebrew 3.13 — both produce failures that are not real.
- **Never install into a service venv** (contents are pinned and asserted). Use
  `uvx` or a scratchpad venv.
- **Never run `scripts/teardown.sh`.** Never touch the `kind-registry`
  container. Recreating the `northstar` cluster requires explicit permission
  from the human partner, each time.
- **The gate (`scripts/smoke-test.sh`) runs once, in Task 5.** It performs live
  settlements. Earlier tasks run targeted suites only.
- Port-forwards: `smoke-test.sh` does not create them — it checks for four and
  exits. Establish them yourself, kill only those PIDs, leave none running.

## File Structure

| Path | Responsibility | Task |
|---|---|---|
| `deploy/kind/stage3.yaml` | kind config, fourth host port mapping for reviewer-ui | 1 |
| `agents/claims-agent/requirements.txt`, `tools/payments-mcp/requirements.txt` | full transitive pins | 1 |
| `*/tests/test_pins.py` | advisory gate per service | 1 |
| `systems/payments-api/store.go` | `StatusRefused`, the refusal record | 2 |
| `systems/payments-api/handlers.go` | `POST /payments/{id}/refuse`, `GET /payments?status=` | 2 |
| `platform/policy-decision-service/pds/rules.py` | one new `EXECUTE_LIMITS` row | 3 |
| `deploy/k8s/senior-settlement-agent.yaml` | ServiceAccount + Deployment + Service | 3 |
| `agents/settlement-agent/settlement.py` | `execute_approved()` entry point | 3 |
| `agents/settlement-agent/executor.py` | A2A routing to the new entry point | 3 |
| `deploy/platform/cilium-policies.yaml` | egress for the two new workloads | 3, 4 |
| `services/reviewer-ui/` | the HITL stage surface, `review.decision` span | 4 |
| `deploy/k8s/reviewer-ui.yaml` | ServiceAccount + Deployment + NodePort 30081 | 4 |
| `tests/e2e/test_supervised.py` | the seven exit criteria | 5 |

---

### Task 1: Groundwork — carried debt, and the stage-3 cluster shape

Two independent parts. Part A is mechanical and batched; Part B needs the human
partner's permission before it runs.

**Files:**
- Modify: `agents/claims-agent/requirements.txt`, `tools/payments-mcp/requirements.txt`
- Create: `agents/claims-agent/tests/test_pins.py`, `tools/payments-mcp/tests/test_pins.py`
- Create: `deploy/kind/stage3.yaml`
- Modify: `scripts/setup-kind.sh` (add `--stage 3`)

**Interfaces:**
- Produces: a cluster with host port **8081 → nodePort 30081** free for
  `reviewer-ui`, which Task 4 consumes.

#### Part A — the M1.2 carried debt (batched, one commit)

- [ ] **Step 1: Pin full transitive closures**

`agents/claims-agent` and `tools/payments-mcp` are the two of four requirements
files that do not. Use the same method the other two used — freeze from the
service's own venv, keep the direct dependencies' comments — and verify each
service's suite still passes from its own directory afterwards.

- [ ] **Step 2: Extend the advisory gate to every service**

Copy the pattern from `agents/settlement-agent/tests/test_pins.py`, which pins
`pytest`/`pytest-asyncio` via `importlib.metadata`. One `test_pins.py` per
Python service that lacks one. The gate must fail loudly on a version drift, not
skip.

- [ ] **Step 3: Apply Ruling 5's single asyncio style**

Both styles currently coexist in the suites. Pick the one already dominant, apply
it everywhere, and record the count you changed. Do not change any test's
meaning.

- [ ] **Step 4: Run every service suite from its own directory; commit**

```bash
git add -A && git commit -m "chore(m3): clear the M1.2 carried debt, two milestones late"
```

#### Part B — stage 3

- [ ] **Step 5: STOP. Ask the human partner for permission to recreate the cluster.**

This is a hard gate. Do not proceed without an explicit yes. Explain: the kind
config has three host port mappings and no spare, `reviewer-ui` needs a fourth,
and `extraPortMappings` is only applied at cluster creation.

- [ ] **Step 6: Write `deploy/kind/stage3.yaml`**

Copy `deploy/kind/stage2.yaml` verbatim and add one mapping:

```yaml
      - containerPort: 30081   # reviewer-ui
        hostPort: 8081
        protocol: TCP
```

Everything else — `disableDefaultCNI`, the containerd registry patches, the three
existing mappings — is unchanged. Add `--stage 3` to `scripts/setup-kind.sh`
selecting this file; stage 3 is stage 2 plus the M3 workloads.

- [ ] **Step 7: Recreate, then re-verify M2's baseline BEFORE adding anything**

Mirror M2.1's sequencing: the point is that each later step has one suspect. Run
the full M2.2 denial scenario on the fresh cluster and confirm the refusal still
happens, with `policy.decision` = deny and no execute span. Record the trace id.

- [ ] **Step 8: Commit**

---

### Task 2: The refusal record in the ledger

**Files:**
- Modify: `systems/payments-api/store.go`, `systems/payments-api/handlers.go`
- Test: `systems/payments-api/*_test.go`

**Interfaces:**
- Produces: `POST /payments/{id}/refuse` accepting
  `{rule, limit, requested, narrative, refusedTraceId}`; `GET /payments?status=refused`
  returning a list. Tasks 3 and 4 consume both.

- [ ] **Step 1: Write the failing tests first**

Cover, at minimum: refusing a `proposed` payment sets `refused` and stores every
field; refusing an `executed` payment is rejected (`ErrExecuted`'s shape);
refusing an unknown id is 404; `GET /payments?status=refused` returns only
refused ones; a refused payment can still be executed afterwards (approval does
not close the execute path — the PDS is what guards it).

- [ ] **Step 2: Add the status and the record**

```go
const (
	StatusProposed = "proposed"
	StatusExecuted = "executed"
	StatusRefused  = "refused"
)

// refusal is what the platform decided and what the claimant was told.
// Stored beside the payment so the reviewer sees the decision, not a guess.
type refusal struct {
	Rule           string  `json:"rule"`
	Limit          float64 `json:"limit"`
	Requested      float64 `json:"requested"`
	Narrative      string  `json:"narrative"`
	RefusedTraceID string  `json:"refusedTraceId"`
	RefusedAt      string  `json:"refusedAt"`
}
```

`refused` is terminal-until-approved, **not** a new state machine: `executed`
stays reachable only through the existing execute path.

- [ ] **Step 3: Wire the routes**

Follow the existing `handle(pattern, spanName, fn)` helper in `newRouter` so the
span name is the route template, not the raw path. Do **not** wrap in
`withClaimID` if the route carries no claim id.

- [ ] **Step 4: Run the Go suite; commit**

```bash
cd systems/payments-api && go test ./... && cd -
git commit -am "feat(m3): record why a payout was refused, beside the payout"
```

---

### Task 3: The colleague with higher approval authority

**Files:**
- Modify: `platform/policy-decision-service/pds/rules.py`
- Create: `deploy/k8s/senior-settlement-agent.yaml`
- Modify: `agents/settlement-agent/settlement.py`, `agents/settlement-agent/executor.py`
- Modify: `deploy/platform/cilium-policies.yaml`
- Test: `platform/policy-decision-service/tests/`, `agents/settlement-agent/tests/`

**Interfaces:**
- Consumes: Task 2's `POST /payments/{id}/refuse`.
- Produces: `execute_approved(payment_id, approval) -> dict`, reachable over A2A;
  the SPIFFE ID `spiffe://northstar.local/ns/northstar-agents/sa/senior-settlement-agent`.

- [ ] **Step 1: Write the failing policy tests**

€6,200 denied for `settlement-agent`/`assisted`; €6,200 **permitted** for
`senior-settlement-agent`/`assisted` under the **same rule name**
(`execute_limit`); €30,000 denied for the senior identity too — the colleague has
more authority, not unlimited authority.

- [ ] **Step 2: Add the row — and nothing else**

```python
    # M3: the colleague with higher approval authority the agent already
    # narrates. Higher limit, same rule, same code path. Approval changes who
    # asks; it does not change what is allowed.
    ("spiffe://northstar.local/ns/northstar-agents/sa/senior-settlement-agent",
     "assisted"): 25_000.0,
```

No change to `decide()`. If this task edits any function in `rules.py`, it has
gone wrong.

- [ ] **Step 3: The second Deployment**

`deploy/k8s/senior-settlement-agent.yaml`: a ServiceAccount named
`senior-settlement-agent`, and a Deployment that is `settlement-agent.yaml` with
the ServiceAccount, `app` label, and Service name changed. **The image, the
prompt and every other env var stay identical** — that is the demo. SPIRE needs
no new registration: the existing `ClusterSPIFFEID` template derives the ID from
the namespace and ServiceAccount.

- [ ] **Step 4: The `execute_approved` entry point**

In `settlement.py`, beside `decide_settlement`. It does **not** re-run the
settlement decision: the claim was already assessed and the amount already
proposed. It executes the named payment through the same `PaymentsTools`, and
returns the ledger's record.

```python
async def execute_approved(
    payment_id: str, amount: float, currency: str, approval: dict[str, Any],
    payments: Any, refused_trace_id: str,
) -> dict[str, Any]:
    """Execute a payment a human approved. Same tools, same gateway, different identity.

    No model call and no new prompt: the reasoning happened in the refused run,
    a human reviewed *that*, and re-deciding here would mean the human approved
    something other than what executes. This function's whole job is to carry
    an approved instruction to the payment system under an identity the policy
    permits.
    """
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(
        "settlement.execute_approved",
        links=_refusal_link(refused_trace_id),
        attributes={
            "northstar.payment.id": payment_id,
            "northstar.approval.reviewer_id": approval["reviewerId"],
        },
    ):
        # Straight to execute. No propose: the proposal is what was reviewed.
        result = await payments.execute(payment_id, amount, currency)
    return result
```

Link the new trace to the refused one — the span link is the point, so assert it
in a test rather than trusting it:

```python
from opentelemetry import trace
from opentelemetry.trace import Link, NonRecordingSpan, SpanContext, TraceFlags


def _refusal_link(refused_trace_id: str) -> list[Link]:
    """A link, not a parent. A human took minutes and the original process is
    gone; a span held open across that gap would misstate both the duration and
    what was executing.

    A link needs a SpanContext, and we have only the refused *trace* id -- the
    refusal's span id is not carried in the ledger. Span id 0 is invalid, so
    link to the trace with an explicitly invalid span id and let the backend
    resolve the trace: Jaeger renders this as a trace-level link, which is what
    we want to click on stage.
    """
    if not refused_trace_id:
        return []
    return [
        Link(
            SpanContext(
                trace_id=int(refused_trace_id, 16),
                span_id=0,
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
        )
    ]
```

**Verify this against the installed OpenTelemetry SDK before building on it.** If
`SpanContext` with `span_id=0` is dropped as invalid by the exporter, fall back to
storing the refusal's *span* id alongside its trace id in Task 2's `refusal`
record and linking to the real span. Measure which one the backend actually
renders; do not assume. Report which you used.

- [ ] **Step 5: Record the refusal when it happens**

Nothing writes the refusal record until this step, so it belongs here, in
`decide_settlement`'s existing refused branch (`settlement.py`, the
`executed is None and refused is not None` case). It already holds everything the
record needs: `refused.rule`, `refused.limit`, `refused.requested`, the model's
`result.output` as `narrative`, and the proposal's `paymentId`.

The trace id comes from the current span:

```python
    span_ctx = trace.get_current_span().get_span_context()
    refused_trace_id = format(span_ctx.trace_id, "032x")
```

Call Task 2's `POST /payments/{id}/refuse` **through the existing payments
client**, not a new HTTP path. Two rules:

- A failure to record must not turn a refusal into an error the claimant sees.
  Log it loudly and still return the refused reply — the ledger already shows the
  payment unexecuted, which is the safe state.
- The reply this function returns is unchanged. M2.2's contract (`status:
  "refused"`, amounts from the ledger, `narrative` from the model) is asserted by
  existing tests; this step adds a side effect, not a new shape.

- [ ] **Step 6: Route it over A2A in `executor.py`**

Follow the existing dispatch. Malformed approval payloads must fail loudly, not
default to executing.

- [ ] **Step 7: Cilium egress for the new workload**

`senior-settlement-agent` needs exactly what `settlement-agent` has. Remember the
measured hazard: **any egress rule selecting an endpoint makes it default-deny
for egress**, and behavioural probes are insufficient — assert the datapath with
`cilium-dbg bpf policy get`, which is what caught two silent regressions in M2.2.

- [ ] **Step 8: Run the PDS and settlement-agent suites from their own directories; commit**

---

### Task 4: The Reviewer UI

**Files:**
- Create: `services/reviewer-ui/` (follow `services/customer-ui/`'s layout exactly)
- Create: `deploy/k8s/reviewer-ui.yaml`
- Modify: `deploy/platform/cilium-policies.yaml`

**Interfaces:**
- Consumes: Task 2's `GET /payments?status=refused`; Task 3's `execute_approved`
  over A2A.
- Produces: NodePort **30081**, host **8081**.

- [ ] **Step 1: The surface**

Server-rendered HTML like `customer-ui` — no frontend toolchain. It is a stage
surface: legible from the back of a room. For each refused payment show the claim
id, the amount requested, **the rule, the limit and the requested amount from the
ledger**, and the agent's narrative verbatim. Approve and Reject buttons. The
reviewer's name is an input on the form; it travels in the payload, not the SVID.

Every figure on this page is read from the ledger. Nothing is recomputed in the
UI and nothing comes from model output except `narrative`.

- [ ] **Step 2: The `review.decision` span**

SpanKind.SERVER, with exactly the attributes in spec §6:
`review.payment.id`, `review.decision`, `review.reviewer.id`,
`review.reviewer.spiffe_id`, `review.refused.trace_id`, `review.elapsed_ms`.
`review.elapsed_ms` is computed from the ledger's `refusedAt`, not from a timer
started when the page loaded.

- [ ] **Step 3: Deployment, ServiceAccount, NodePort, Cilium**

`reviewer-ui` gets its own ServiceAccount and therefore its own SPIFFE ID, and
**no `EXECUTE_LIMITS` row** — it must never execute a payment. Egress: the
payments-api and the senior agent's A2A endpoint, nothing else.

- [ ] **Step 4: Unit tests, then commit**

---

### Task 5: Prove it, document it, gate it

**Files:**
- Create: `tests/e2e/test_supervised.py`
- Modify: `docs/telemetry-contract.md`, `docs/architecture.md`, `README.md`
- Modify: `scripts/smoke-test.sh` (reviewer-ui suite, fifth port-forward)

- [ ] **Step 1: One test per exit criterion — seven of them**

Criteria 1–7 from spec §7, one test each, named for the criterion. Criterion 6
(the junior identity still cannot execute €6,200 **after** an approval exists) and
criterion 7 (`reviewer-ui`'s own SPIFFE ID gets `unknown_caller`) are the
anti-bypass pair; write them first and make sure each fails before its fix exists.

- [ ] **Step 2: The skip-path mutation pass**

M2.2's recurring defect, four times over: a test that **skips** instead of failing
on the object it guards. For every cluster-dependent test added here, discriminate
the skip on a *different object* than the one under test, then mutate to prove it
— point it at a missing target and confirm it goes **red**, not skipped. See
`tests/e2e/test_gateway_config_drift.py` for the pattern and the incident.

- [ ] **Step 3: The stale-prose pass**

M2.1's lesson: 13 of 13 final-review findings were sentences true when written and
falsified by a later task. Re-read every doc this milestone touched against what
now exists. Re-measure every published count.

- [ ] **Step 4: Measure OQ-U1 while a second privileged identity exists**

Probe whether agentgateway v1.5.0 applies `mcpGuardrails` per-message inside a
JSON-RPC **batch**. Record what you measure in `docs/upstream-findings.md`,
whichever way it goes. File nothing upstream. If it is unguarded, say so plainly —
it is a real bypass and this milestone raised the stakes on it.

- [ ] **Step 5: Run the gate once**

```bash
RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh
```

Report the exact final line and exit code. Kill every port-forward you started.

- [ ] **Step 6: Commit**
