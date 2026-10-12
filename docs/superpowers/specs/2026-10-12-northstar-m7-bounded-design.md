# Northstar M7 — Bounded

Status: **M7 (Bounded)**, on branch `m7-bounded`. Design written 2026-10-12,
after M6 merged and the KubeCon NA deck landed at `17b4d33`.

**The line on stage:** *"The agent can never edit what constrains it."*

---

## 1. Why this milestone exists

William Rizzo co-presents the KubeCon NA 2026 talk. His own demo,
*Bounded Agent Autonomy* (`~/kaspernissen/bounded-ai`, built for OSFF 2026),
makes one argument Northstar shares — *you do not trust an agent to remember its
own authority limits; you make the platform enforce them* — and organises it as
rings:

| William's ring | Question | His project | Northstar's equivalent |
|---|---|---|---|
| Core · Identity | who it is | SPIRE (off his live path) | SPIRE JWT-SVIDs, verified by the gateway on every call (stage 2) |
| Ring 1 · Reach | what it connects to | Linkerd mTLS authz | Cilium: the gateway is the only way into `payments-mcp` (stage 2) |
| Ring 2 · Capability | what it may do | agentgateway CEL allowlist | agentgateway → ExtMCP → policy-decision-service, `failClosed` (stage 2), autonomy flag (stage 5) |
| Keystone | it can't edit what constrains it | Kyverno | **missing** |

Northstar has three of the four rings already, but only the Capability ring is
a live beat. M7 adds the fourth ring and makes the second one visible on stage.
It deliberately does **not** port William's other choices: Linkerd duplicates
SPIRE on identity (the M2 rationale still holds), and
the static CEL `mcpAuthorization` allowlist is the approach Northstar's
gateway replaced with a decision service. The deck presents that as two layers
("hide" vs "refuse and record"), not as a disagreement.

## 2. What M7 builds

### 2.1 Stage 7 — Bounded: Kyverno and the keystone policy

- `./scripts/setup-kind.sh --stage 7` = stage 6 + Kyverno. Like stages 4–6 it
  reuses `deploy/kind/stage3.yaml` (no host port needed); no `stage7.yaml`.
- **Kyverno chart 3.9.1 / app v1.19.1** — latest upstream release, checked
  against the GitHub releases API on 2026-10-12 (v1.19.1, 2026-09-10). Pin it
  next to the other pins and add it to `tests/unit/test_pins.py`.
- **Policy type: `ValidatingPolicy` (`policies.kyverno.io/v1`), CEL.** v1.19.0
  surfaces deprecation warnings for the legacy `kyverno.io` `ClusterPolicy`
  (kyverno/kyverno#16868). William's demo uses the legacy type on v1.18.1;
  Northstar uses the current one. Same rule, same message shape.
- File: `deploy/platform/kyverno/keystone-policy.yaml`, policy name
  `agents-cannot-edit-their-own-authority`.
- **Who is denied:** any request whose `request.userInfo.username` starts with
  `system:serviceaccount:northstar-agents:` or `system:serviceaccount:northstar-tools:`.
  Prefix match, not a list, so a new agent is covered without editing the
  policy. Humans, CI and `backstage-autonomy-writer` (in `northstar-platform`)
  are not matched.
- **What is protected** (CREATE, UPDATE, DELETE):
  1. `FeatureFlag` and `FeatureFlagSource` in `northstar-platform` — the
     autonomy contract;
  2. the `agentgateway-config` ConfigMap — the gateway's rules;
  3. `CiliumNetworkPolicy` — the Reach ring;
  4. `Role`, `RoleBinding`, `ClusterRole`, `ClusterRoleBinding`;
  5. Kyverno's own `ValidatingPolicy` and `PolicyException` — an exception is
     a bypass, so creating one is an edit to the constraint.
  The PDS execute limits live in code (`pds/rules.py`), so they change only
  by a reviewed commit and a rebuild; the policy does not need to cover them,
  and the note on the deck's keystone slide says so.
- **Message** (shown live, so it is written for the audience):
  `DENIED: agent identity '<username>' may not change the objects that define
  its own authority (<kind>/<name>). Authority changes go through a reviewed
  commit, never a runtime decision by the agent.`
- **Fail closed.** The webhook's `failurePolicy` is `Fail`, consistent with the
  gateway's `failClosed`. Consequence, stated in the runbook: if Kyverno is down,
  Backstage's flip of the flag fails too. Pre-flight checks Kyverno is Ready.

### 2.2 The over-grant fixture

RBAC already denies every agent ServiceAccount a write to the flag, so on a
correct cluster the keystone beat would show an RBAC `Forbidden`, not Kyverno.
That is a true and useful first line, but it isn't the keystone. William's demo
solves this with a standing RBAC grant; Northstar does not plant a standing
over-grant in a reference implementation. Instead:

- `deploy/demo/keystone-overgrant.yaml` — a Role + RoleBinding giving
  `northstar-agents:settlement-agent` `patch` on
  `featureflags/northstar-autonomy`, headed by a comment that it is a **demo
  fixture modelling a mistake**, never applied by `setup-kind.sh`.
- The beat applies it, shows the denial, and deletes it. The reset checklist
  and `scripts/start-demo.sh`'s pre-flight both assert it is absent.

### 2.3 The two beat scripts

`scripts/beat-reach.sh` and `scripts/beat-keystone.sh`: short, stage-legible,
each step printed before it runs, every command with a timeout, exit non-zero
if the denial does **not** fire.

- **Reach:** from `claims-agent` (same prober and reasoning as
  `tests/e2e/test_gateway_is_the_only_route.py`), (a) the control —
  `settlement-agent:9002` is reachable; (b) a direct MCP `initialize` at
  `payments-mcp.northstar-tools:8000/mcp` → dropped (timeout). Uses whatever
  client the existing test uses inside that pod (no new image tooling).
- **Keystone:** (1) as `settlement-agent`, patch `northstar-autonomy` to
  `autonomous` → RBAC `Forbidden`; (2) apply the over-grant ("someone made a
  mistake"); (3) the same patch → **Kyverno denies**, message printed; (4)
  delete the over-grant; (5) print the flag's resolved level, still `assisted`.
  The human path is beat 7 itself (Backstage's identity), so the script does
  not repeat it.

### 2.4 Is the keystone refusal in the trace? Measure it.

Kyverno exports OTLP traces (`*.tracing.enabled/address/port` chart values).
Point the admission controller at the in-cluster collector and **measure**:

1. Does a denied admission produce a span in Jaeger? Which service name,
   operation and attributes (policy name, user, allowed/denied)?
2. Is anything sampled? The demo never samples: if Kyverno samples,
   set it to always-on or record why it cannot be.
3. It is expected to be a **standalone** trace: `kubectl` does not propagate
   `traceparent`. Record that as a finding, don't paper over it.

The result is written into `docs/architecture.md` and drives one deck slide
("Which refusals the trace can see"). If tracing does not produce a usable
span, the slide says so; the milestone does not fail on it. Cilium drops are
**not** in the trace (Hubble is not installed) — also stated, not fixed.

### 2.5 Tests

- `tests/unit/`: pins; the policy file parses, matches by prefix, covers the
  five object groups, `failurePolicy: Fail`; the over-grant fixture is never
  referenced by `setup-kind.sh`.
- `tests/e2e/test_keystone.py` (live stage 7, no model tokens):
  RBAC-forbidden without the grant; Kyverno-denied with it (asserting the
  policy name in the message, so an RBAC denial can't pass for it); a human
  context's patch is allowed and reverted; an agent `PolicyException` create is
  denied; the fixture is cleaned up in a `finally`.
- If 2.4 yields a span: assert it reaches Jaeger with the denial attributes.
- **Mutation pass** on `test_keystone.py` (remove the policy → the test must
  go red, not skip) using `scripts/mutate-guard.sh`.

## 3. Carried debt this milestone pays

`CURRENT_STAGE = 5` in
`tests/unit/test_stage2_cilium_config.py` — M6's carried item 1 — is raised to
**7**, and every stale `--stage N` it reddens is fixed in the same task.

Not paid here (still carried): the SPIRE-CDN gate dependency, M4's debt list.

## 4. Docs

- `docs/demo-runbook.md`: stage 7 in the ladder; **Beat R — the only way in**
  (after beat 3) and **Beat K — the keystone** (after beat 7), DO/SHOW/SAY;
  pre-flight (Kyverno Ready, over-grant absent); reset (over-grant absent);
  triage (Kyverno down ⇒ flag flips fail closed).
- `docs/architecture.md`: the rings table from §1; Kyverno; the 2.4 findings.
- `README.md`: stage 7, and William's demo credited as the source of the
  rings and the keystone.

## 5. Gate

Bring up `--stage 7` cold, run both beat scripts by hand and read their output
(the only check that instructions work is
running them), then the whole-branch review and its fix wave, **then** one
`scripts/smoke-test.sh` run (the gate goes after the review, as in M4 and M6).

## 6. Out of scope

Linkerd; a CEL `mcpAuthorization` layer; Hubble; an AgentCard CRD (Northstar's
agent cards are A2A documents served by the agents, not cluster objects);
William's model-driven `--agent` mode.

---

## 7. The five-minute demo flow (added 2026-10-12)

**Why.** By the end of §1–6 the runbook had ten beats over six surfaces
(customer UI, Jaeger, reviewer UI, OpenSearch Dashboards, Backstage, a
terminal), 2–6 minutes each. The talk is 30 minutes, co-presented. William's
OSFF demo runs in about five minutes because each point is one command in one
terminal. The talk's live demo now follows that shape: five commands, about
five minutes, every main point of the talk landed by exactly one of them.

### 7.1 The flow

| # | Command | ~Time | What the audience sees | Talk point |
|---|---|---|---|---|
| 1 | `demo guard` | 45s | No identity → **401** at the gateway; around the gateway → **dropped** by Cilium; with settlement-agent's SPIRE identity → the **MCP tools it may call** (`tools/list` through the gateway) | Core · Identity, Ring 1 · Reach, access to MCP |
| 2 | `demo claim 6200` | 90s | The real claims-agent → settlement-agent → model proposes €6,200; the platform **refuses**; the model's explanation to the claimant; then the span tree from Jaeger in the terminal: `policy.decision` deny, rule and limit, **no** `payments-api` span; the Jaeger link | Ring 2 · Capability, "a refusal is a success", "HTTP 200 means no", Trace |
| 3 | `demo autonomy autonomous` → `demo claim 6200` → `demo autonomy assisted` | 75s | The same claim **executed**; nothing redeployed; the flag back to `assisted` | one live value; "the platform API is the resource" (done with the CLI, not Backstage) |
| 4 | `demo keystone` | 45s | As settlement-agent: RBAC **Forbidden**; over-grant; Kyverno **Denied**; over-grant removed; flag still `assisted` | Keystone |
| 5 | `demo evidence` | 45s | For both claims of this run, from OpenSearch: who decided, rule, limit, caller identity, outcome | Prove |

Total ≈ 5:00. The model is called in steps 2 and 3 only; 1, 4 and 5 are
deterministic. `demo reset` returns the cluster to the resting state
(flag `assisted`, no over-grant) and is safe to run at any time.

### 7.2 What moves out of the live demo

Each keeps its slide (screenshot or transcript), so the story keeps it; only
the live moment goes: the human approval (Beat 4), Backstage (Beat 7's UI —
the flip itself is now step 3's CLI), the evaluation scores and OpenSearch
Dashboards (Beats 5–6; step 5 prints the evidence), and the separate Beats 3
and R (folded into `demo guard`).

### 7.3 Build

- `scripts/demo` — one Python entry point (repo venv), subcommands `guard`,
  `claim <amount>`, `autonomy <assisted|autonomous>`, `keystone`, `evidence`,
  `reset`. Every subcommand prints what it is about to do, then the platform's
  answer, with a timeout on every network call, and exits non-zero if the
  expected outcome does not happen (a refusal that executes, a drop that
  connects). Output is written for a room: short lines, the decisive word
  highlighted, no secrets (tokens are never printed).
- **No new mechanism.** It reuses what the tests already prove: claim
  submission over A2A (`_submit_claim`), server-side SVID minting and direct
  MCP calls (`mint_svid`, `call_tool_directly`), the Jaeger harness, and
  `evidence_for`. Those helpers move from test files into a shared module
  (`demo/` package or `tests/harness/`) imported by both the tests and the
  driver, so the stage path and the tested path are the same code.
- `beat-reach.sh` and `beat-keystone.sh` stay as the implementation of
  `guard`'s reach half and of `keystone` (or are absorbed into the driver);
  either way the gate keeps running them.
- `demo claim` uses a fresh claim id per run and remembers the run's claim ids
  in a state file under the git-ignored scratch path, so `demo evidence`
  reports this run's claims and nothing older.

### 7.4 Tests and verification

- `tests/e2e/test_demo_flow.py` runs 1 → 5 in order against the live stage-7
  cluster (with `RUN_BROWSER_TESTS=1`, because steps 2–3 spend model calls),
  asserting each step's outcome and that `demo reset` leaves the resting
  state. It joins the gate.
- Every assumption the slides make about the flow is checked by running it:
  the refusal's HTTP status and rule, the absent `payments-api` span, the
  executed claim after the flip with no pod restarted (pod start times
  compared before and after), the keystone messages, and the evidence fields.
- The runbook gets a "5-minute flow" section first; the longer beats stay
  below it for a longer slot.

### 7.5 The deck

"The demo" slide becomes the §7.1 table. The five steps are the only live
moments, each with a real-output fallback slide (terminal transcript, as R and
K already have). The old live-beat slides (1, 2, 4, 7, R) become ordinary
slides from their screenshots or transcripts, so they can be kept or cut.
