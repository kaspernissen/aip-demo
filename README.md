# Northstar — an Agentic Internal Platform reference implementation

Northstar Insurance is a fictional insurer. A customer files a vehicle-damage
claim; a set of agents collaborates to investigate and settle it. The platform
decides whether those interactions are permitted, whether a human must approve
them, records what happened, evaluates the quality of the trajectory, and
gradually determines how much autonomy the system should have.

Built from open standards and existing open-source projects, not a proprietary
"AI platform".

## Status

**M3 — Supervised.** A refused payment is no longer the end of the story. The
claim flows end to end and produces one correlated trace; agentgateway sits in
the MCP path, verifies a SPIRE-minted JWT-SVID on every call, and asks a Policy
Decision Service whether the call is allowed. The position is now **one hop
authorized, and one refusal a human can overturn**: submitting the pre-filled
€6,200 claim gets the payment **proposed, refused, and recorded as refused** —
the model narrates the refusal to the claimant and does not retry, and the
refusal becomes a row a human reviewer reads on the reviewer UI at
http://localhost:8081. Approving it there executes the payment under a
*second, more privileged identity* (`senior-settlement-agent`, the same image
under a different ServiceAccount), which the Policy Decision Service permits on
its own allowance row rather than on an override. The human's click is a
`review.decision` span, the approved execution is a
`settlement.execute_approved` span, and the two are joined to the original
refused trace by a **span link** — not a parent-child edge, because the human
decided minutes later in a different process. A second claim at €5,800 still
settles end to end with no human involved: a platform that asks a human about
everything is not supervision, it is a queue.

**What is deliberately still not decided**, so the "supervised" claim is not
overread. Approval is **one human, one click, once**: there is no second
approver, no timeout, no expiry, no rejection path back to the agent, and no
per-human identity — the reviewer UI reads *its own* SPIFFE ID for the
`review.decision` span, so the span records which deployment approved, not
which person. Authorization is still exactly one hop on the MCP call; the
browser → claims-agent, claims-agent → settlement-agent and
payments-mcp → payments-api hops carry no identity and no policy, and
payments-api still moves money for anything that can reach its Service —
including the approved execution, which is authorized at the gateway like any
other call and not by the ledger. See `docs/architecture.md`'s "What the system
deliberately does not do", which each milestone sharpens rather than deletes.

## Quick start

**Divergence from the spec's exported-env-var flow, noted explicitly (amendment
A2):** the spec's original acceptance criterion for this step was `git clone`
→ `export ANTHROPIC_API_KEY=...` → one command, no `.env` file. A2 replaced
that with a sourced, git-ignored `.env` (better for a repo with several
optional credentials), but no document had said so until now — a cold reader
following only the spec would hit `setup-kind.sh`'s very first failure. This
is that missing step:

```bash
cp .env.template .env
# edit .env: ANTHROPIC_API_KEY is REQUIRED now that M1.2 shipped — the
# settlement decision is a real Claude model call, not a stub. Leave it
# blank and setup-kind.sh will still bring up every pod Running and fully
# traced -- 7 application pods at stage 1, 9 at stage 2, where agentgateway
# and the Policy Decision Service join them, and 11 at stage 3, where
# reviewer-ui and senior-settlement-agent do (alongside SPIRE's four and
# Cilium's, which are platform rather than application). Counted off a live
# stage-3 cluster with `kubectl get pods -A`, not incremented on paper. The
# key is deliberately optional at the Kubernetes level, but the
# FIRST claim submission fails at the model call — the one thing this
# milestone exists to demonstrate. setup-kind.sh prints SET/EMPTY for this
# variable; check for SET before going on stage. DASH0_AUTH_TOKEN and its
# three DASH0_* siblings are optional — see the comments in .env.template for
# exactly which variables are required.

./scripts/setup-kind.sh --stage 3
./scripts/smoke-test.sh
```

**`--stage 3`, not `--stage 2`, as of M3 Task 5B.** This said `--stage 2` for
the whole of M3, which was correct until Task 4 and false afterwards: stage 3
now installs reviewer-ui and senior-settlement-agent on top of stage 2, and
`smoke-test.sh`'s own prerequisite check requires reviewer-ui to answer on
port 8081 — so following the old instruction rebuilt a cluster the very next
command then rejected. `tests/unit/test_stage2_cilium_config.py::test_no_operator_facing_instruction_still_points_at_an_older_stage`
now pins this across every script and this file.

`smoke-test.sh` additionally requires a repo-root Python venv, a venv per
Python service, and a Playwright Chromium install — see the actionable error
message it prints for each, or run the four commands it lists yourself
ahead of time:

```bash
uv venv --python 3.13 .venv && uv pip install -r tests/requirements.txt && .venv/bin/playwright install chromium
for svc in tools/payments-mcp agents/settlement-agent agents/claims-agent platform/policy-decision-service apps/reviewer-ui; do
  (cd "$svc" && uv venv --python 3.13 .venv && uv pip install -r requirements.txt)
done
```

`apps/reviewer-ui` joined that loop in M3 Task 5B, and — exactly like the
Policy Decision Service one milestone earlier — it was an existing dependency
finally being declared rather than a new one. `tests/unit`'s per-service count
guard shells out to reviewer-ui's own suite through `apps/reviewer-ui/.venv`,
so the gate already could not pass without this venv; it just failed four
minutes in, inside a unit test, complaining about a published count. The gate
now requires it up front and runs reviewer-ui's 50 tests alongside the other
six suites.

**The Policy Decision Service is in that loop, and it is not optional.** An
earlier version of this section said it was "deliberately not in that loop"
because the gate does not run its suite. That was wrong, and wrong in the
direction that breaks a fresh clone: `agents/settlement-agent`'s own suite
imports `pds.server`, which imports the generated gRPC stubs, so
`smoke-test.sh` has always needed the PDS set up — it simply needed it silently
and got none of the PDS's tests in return (96 of them when that was written;
**105** as of M3, re-measured with `--collect-only`). As of the M2.2
final-review fix round the gate requires this venv like the others, generates the stubs
itself with `./gen-stubs.sh` before running any suite, and runs the PDS suite
alongside the rest.

The stubs stay git-ignored on purpose (ruling R22): committing them would let
the image and the repo disagree about a contract whose only source of truth is
the vendored `proto/ext_mcp.proto`, which the Dockerfile regenerates from at
build time. So they have to be generated once per clone, and the venv has to
come first, because `gen-stubs.sh` defaults to that venv's `python` for the
pinned `grpc_tools.protoc`. `smoke-test.sh` does this for you. If you want to
run the PDS suite on its own, or run `pytest tests/unit` from the repo root
before ever running the gate, do the generation yourself first — everything
that needs the stubs fails loudly rather than skipping when they are missing,
and prints this command:

```bash
(cd platform/policy-decision-service && ./gen-stubs.sh)
(cd platform/policy-decision-service && .venv/bin/python -m pytest tests -q)
```

Then open:

| Surface | URL |
|---|---|
| Customer UI | http://localhost:8080 |
| Reviewer UI (stage 3 only) | http://localhost:8081 |
| Jaeger | http://localhost:16686 |

The reviewer UI is where M3 is actually visible. Submit the pre-filled €6,200
claim in the customer UI, watch it come back refused, then open the reviewer UI:
the refusal is listed there with the rule that produced it, the limit it
exceeded, and the model's own narrative. Approve it and the payment executes
under `senior-settlement-agent`. Both the click and the execution land in
Jaeger — `review.decision` and `settlement.execute_approved` — in a trace that
carries a **link** back to the refused one.

Unlike the four ports the setup script forwards, 8081 is a NodePort mapped by
`deploy/kind/stage3.yaml`, so it works without a `kubectl port-forward` and
survives a redeploy.

Tear down with `./scripts/teardown.sh`.

### Running the tests directly costs money

`./scripts/smoke-test.sh` is the supported way to run the suite. If you invoke
pytest yourself, know what you are paying for:

| Command | Cluster | Anthropic tokens |
|---|---|---|
| `.venv/bin/python -m pytest tests/unit -q` | not needed | none |
| `.venv/bin/python -m pytest tests/ -q` | **required** | **yes — two live settlements** |
| `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` | required | yes — the full gate |

A bare `pytest tests/` is **not** free and **not** cluster-independent. Since
M1.2 made the settlement a real model call, `tests/e2e/test_settlement_agent_trace.py`
and `tests/e2e/test_claims_chain_trace.py` each drive a live model-driven
settlement — three Anthropic turns apiece — from a module-scoped fixture, and
they are deliberately left ungated so the default suite still proves the chain
end to end. **Five** modules sit behind `RUN_BROWSER_TESTS=1`, **33** tests in
all — re-measured for M3 Task 5B by walking pytest's collected items and
reading each item's own `skipif` marker, not counted by hand and not carried
forward from M2.2's four-module, 27-test figure:
`tests/e2e/test_browser_root_span.py` (8), `tests/e2e/test_gateway_trace.py`
(6), `tests/e2e/test_settlement_llm_trace.py` (9), 4 of the 6 tests in
`tests/e2e/test_policy_denial.py`, and 6 of the 7 in
`tests/e2e/test_supervised.py` (M3). Those last two are the partly-gated ones.
`test_policy_denial.py`'s two ungated tests spend nothing: one reads source
structurally, and the other drives two direct MCP calls with different SPIFFE
identities and no model call at all. `test_supervised.py`'s one ungated test is
its criterion 7, which spends nothing for the same reason: it speaks MCP to the
gateway directly with a minted SVID for reviewer-ui's own identity, no browser
and no model call. Without a live cluster and port-forwards those e2e files
fail rather than skip.

`tests/unit` is the hermetic subset: no cluster, no API key, no network. It is
not, however, self-sufficient on a fresh clone — its per-service count guard
shells out to the settlement-agent and policy-decision-service suites, both of
which need the generated ExtMCP stubs, so run `(cd
platform/policy-decision-service && ./gen-stubs.sh)` once first. It fails
loudly and names that command if you do not (limitation 10 in
`docs/telemetry-contract.md`).

## Stages

The demo has six stages; the setup script installs only what a stage needs, so
the laptop runs one stage at a time.

| Stage | Name | Adds |
|---|---|---|
| 1 | Connected | agents, MCP servers, systems of record, customer UI, collector, Jaeger |
| 2 | Governed | agentgateway, SPIFFE/SPIRE, Policy Decision Service, Kyverno, Cilium — **as of M2.2 `--stage 2` installs Cilium (M2.1 Task 3, with M2.2 Task 7 adding ingress and `toFQDNs` egress policies), agentgateway (M2.1 Task 3, in the MCP path between settlement-agent and payments-mcp), SPIRE (M2.1 Task 4, verifying a JWT-SVID at the gateway) and the Policy Decision Service (M2.2 Task 4, deciding every `tools/call` through the gateway's `mcpGuardrails` hook) — Kyverno is NOT installed yet**, on a cluster created with kind's default CNI disabled |
| 3 | Supervised | Dapr Workflow, reviewer UI, Keycloak — **as of M3 `--stage 3` installs the reviewer UI (Task 4, the page a human reads and clicks) and `senior-settlement-agent` (Task 3, the settlement-agent image in a second Deployment under a second ServiceAccount, so "senior" is an identity rather than a different program) on top of everything stage 2 installs — Dapr Workflow and Keycloak are NOT installed**, on a cluster created with a fourth host port reserved for the reviewer UI |
| 4 | Observable | OpenSearch + Agent Evals, Prometheus |
| 5 | Progressive autonomy | OpenFeature / flagd |
| 6 | Golden path | Crossplane `Agent` CRD, Argo CD, Backstage |

## Architecture (stage 1 workloads, on the stage-2 dataplane, with M3's approval path)

M2.1 Task 1 left this chain unchanged — Cilium sits underneath every hop of it,
and the trace was verified span-by-span against a pre-Cilium baseline to confirm
that. **M2.1 Task 3 added one hop**: agentgateway is interposed on the MCP
call. **M2.2 Task 4 added a branch off it**: before the gateway forwards a
`tools/call`, it asks the Policy Decision Service whether to. The whole chain
is still one distributed trace, and the decision is a span inside it (measured
— see `tests/e2e/test_gateway_trace.py`, `tests/e2e/test_policy_denial.py`, and
`docs/telemetry-contract.md`'s "agentgateway instrumentation, as measured" and
"The policy decision, as measured").

**M3 added a second chain, not a hop in the first one.** When a payment is
refused, that is the end of the automated trace; the approval that follows is a
separate request, started minutes later by a human in a different browser tab,
and it re-enters the *same* choke point — agentgateway, the same JWT-SVID
verification, the same Policy Decision Service — under a different identity.
The two traces are joined by a span link rather than a parent-child edge,
because neither caused the other in the way a parent causes a child (measured —
see `tests/e2e/test_supervised.py` and `docs/telemetry-contract.md`'s "Gap 2
(closed, M3)").

```text
browser ──A2A──► claims-agent ──A2A──► settlement-agent ──MCP──► agentgateway ──MCP──► payments-mcp ──HTTP──► payments-api
  (TS)              (Python)              (Python)                 ▲  (Rust)             (Python)              (Go)
                                                                   │ │ ExtMCP/gRPC — permit or deny, before forwarding
                                                                   │ ▼
                                                                   │ policy-decision-service
                                                                   │        (Python)
browser ────────► reviewer-ui ──A2A──► senior-settlement-agent ──MCP┘
  (human)          (Python)                  (Python)
                                    same image as settlement-agent,
                                    different ServiceAccount, own
                                    allowance row in the PDS
```

The refused trace and the approved trace are two traces, linked:

```text
  trace A (refused)                              trace B (approved)
  browser → ... → settlement-agent → deny        review.decision → settlement.execute_approved
                         ▲                                                     │
                         └──────────────── span link ────────────────────────────┘
```

The link's target is not the `policy.decision` span but the settlement-agent
span that was active when the refusal was written — its trace id *and* its span
id are persisted in the ledger's refusal record (`refusedTraceId`,
`refusedSpanId`) and read back at approval time. Both had to be stored: a
`Link` built with `span_id=0` is dropped by the OpenTelemetry SDK before any
exporter sees it, which M3 Task 3 found by reading `opentelemetry-sdk` 1.44.0's
`_is_valid_link` and then reproducing it in-process.

## Telemetry

Vendor-neutral: OTLP to an OpenTelemetry Collector, then Jaeger. Setting
`DASH0_AUTH_TOKEN` adds a second exporter with no application code change.

We use the telemetry the frameworks emit rather than inventing our own, and we
report the gaps we find. See [`docs/telemetry-contract.md`](docs/telemetry-contract.md).

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — components, hops, protocols, and the trace, walked end to end
- [`docs/telemetry-contract.md`](docs/telemetry-contract.md) — verified instrumentation and convention gaps
- [`docs/upstream-findings.md`](docs/upstream-findings.md) — ten consolidated, filing-ready findings against `a2a-sdk`, FastMCP, `mcp` and agentgateway v1.5.0, **none of them filed**: nine still open (one of those already fixed upstream but unreleased) and one closed on discovery. M2.2 added three, all about agentgateway's ExtMCP policy hook. **M3 added none** — it added a measurement instead: OQ-U1, "can a JSON-RPC *batch* smuggle a `tools/call` past the gateway's policy hook", is now closed. It cannot, on this transport: agentgateway v1.5.0 rejects the batch at deserialization with HTTP 400 and no decision span is ever created. Nothing to file, because failing closed is the right direction
- [`docs/superpowers/specs/`](docs/superpowers/specs/) — designs
- [`docs/superpowers/plans/`](docs/superpowers/plans/) — implementation plans
