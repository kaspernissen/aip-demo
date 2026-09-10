# Northstar — an Agentic Internal Platform reference implementation

Northstar Insurance is a fictional insurer. A customer files a vehicle-damage
claim; a set of agents collaborates to investigate and settle it. The platform
decides whether those interactions are permitted, whether a human must approve
them, records what happened, evaluates the quality of the trajectory, and
gradually determines how much autonomy the system should have.

Built from open standards and existing open-source projects, not a proprietary
"AI platform".

## Status

**M2.2 — Decided.** The claim flows end to end and produces one correlated
trace. agentgateway sits in the MCP path, verifies a SPIRE-minted JWT-SVID on
every call, and now asks a Policy Decision Service whether the call is
allowed. The position is **one hop authorized, on one rule**: submitting the
pre-filled €6,200 claim gets the payment **proposed and then refused**, the
model narrates the refusal to the claimant in its own words and does not
retry, and the decision itself is a `policy.decision` span in the same trace
as the call it decided. A second claim at €5,800 settles end to end — a
platform that refuses everything is not governance, it is an outage. Cilium
enforces the same shape at the network layer in both directions, so the
gateway is not a proxy an agent may choose to skip.

**What is deliberately still not decided**, so the "authorized" claim is not
overread: exactly one hop authorizes, on exactly one rule. The
browser → claims-agent, claims-agent → settlement-agent and
payments-mcp → payments-api hops carry no identity and no policy, no human is
in the loop anywhere, and payments-api still moves money for anything that can
reach its Service. Human approval is M3. See `docs/architecture.md`'s "What
the system deliberately does not do", which each milestone sharpens rather
than deletes.

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
# traced -- 7 application pods at stage 1, and 9 at stage 2, where
# agentgateway and the Policy Decision Service join them (alongside SPIRE's
# four and Cilium's, which are platform rather than application). The key is
# deliberately optional at the Kubernetes level, but the
# FIRST claim submission fails at the model call — the one thing this
# milestone exists to demonstrate. setup-kind.sh prints SET/EMPTY for this
# variable; check for SET before going on stage. DASH0_AUTH_TOKEN and its
# three DASH0_* siblings are optional — see the comments in .env.template for
# exactly which variables are required.

./scripts/setup-kind.sh --stage 2
./scripts/smoke-test.sh
```

`smoke-test.sh` additionally requires a repo-root Python venv, a venv per
Python service, and a Playwright Chromium install — see the actionable error
message it prints for each, or run the four commands it lists yourself
ahead of time:

```bash
uv venv --python 3.13 .venv && uv pip install -r tests/requirements.txt && .venv/bin/playwright install chromium
for svc in tools/payments-mcp agents/settlement-agent agents/claims-agent; do
  (cd "$svc" && uv venv --python 3.13 .venv && uv pip install -r requirements.txt)
done
```

The Policy Decision Service is deliberately not in that loop: `smoke-test.sh`
does not run its suite, because that suite needs generated gRPC stubs and the
gate does not generate anything. To run it, do the generation first — it fails
loudly rather than skipping if the stubs are missing:

```bash
(cd platform/policy-decision-service \
  && uv venv --python 3.13 .venv && uv pip install -r requirements.txt \
  && ./gen-stubs.sh \
  && .venv/bin/python -m pytest tests -q)
```

The venv comes first because `gen-stubs.sh` defaults to that venv's `python`
for the pinned `grpc_tools.protoc`. The stubs are git-ignored on purpose
(ruling R22): committing them would let the image and the repo disagree about
a contract whose only source of truth is the vendored
`proto/ext_mcp.proto`.

Then open:

| Surface | URL |
|---|---|
| Customer UI | http://localhost:8080 |
| Jaeger | http://localhost:16686 |

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
end to end. Four modules sit behind `RUN_BROWSER_TESTS=1`, 27 tests in all
(re-measured for M2.2 Task 8 with `--collect-only`, not counted by hand):
`tests/e2e/test_browser_root_span.py` (8), `tests/e2e/test_gateway_trace.py`
(6), `tests/e2e/test_settlement_llm_trace.py` (9), and 4 of the 6 tests in
`tests/e2e/test_policy_denial.py` — that last file is the only partly-gated
one. Its two ungated tests spend nothing: one reads source structurally, and
the other drives two direct MCP calls with different SPIFFE identities and no
model call at all. Without a live cluster and port-forwards those e2e files
fail rather than skip.

`tests/unit` is the hermetic subset: no cluster, no API key, no network.

## Stages

The demo has six stages; the setup script installs only what a stage needs, so
the laptop runs one stage at a time.

| Stage | Name | Adds |
|---|---|---|
| 1 | Connected | agents, MCP servers, systems of record, customer UI, collector, Jaeger |
| 2 | Governed | agentgateway, SPIFFE/SPIRE, Policy Decision Service, Kyverno, Cilium — **as of M2.2 `--stage 2` installs Cilium (M2.1 Task 3, with M2.2 Task 7 adding ingress and `toFQDNs` egress policies), agentgateway (M2.1 Task 3, in the MCP path between settlement-agent and payments-mcp), SPIRE (M2.1 Task 4, verifying a JWT-SVID at the gateway) and the Policy Decision Service (M2.2 Task 4, deciding every `tools/call` through the gateway's `mcpGuardrails` hook) — Kyverno is NOT installed yet**, on a cluster created with kind's default CNI disabled |
| 3 | Supervised | Dapr Workflow, reviewer UI, Keycloak |
| 4 | Observable | OpenSearch + Agent Evals, Prometheus |
| 5 | Progressive autonomy | OpenFeature / flagd |
| 6 | Golden path | Crossplane `Agent` CRD, Argo CD, Backstage |

## Architecture (stage 1 workloads, now on the stage-2 dataplane)

M2.1 Task 1 left this chain unchanged — Cilium sits underneath every hop of it,
and the trace was verified span-by-span against a pre-Cilium baseline to confirm
that. **M2.1 Task 3 added one hop**: agentgateway is interposed on the MCP
call. **M2.2 Task 4 added a branch off it**: before the gateway forwards a
`tools/call`, it asks the Policy Decision Service whether to. The whole chain
is still one distributed trace, and the decision is a span inside it (measured
— see `tests/e2e/test_gateway_trace.py`, `tests/e2e/test_policy_denial.py`, and
`docs/telemetry-contract.md`'s "agentgateway instrumentation, as measured" and
"The policy decision, as measured").

```text
browser ──A2A──► claims-agent ──A2A──► settlement-agent ──MCP──► agentgateway ──MCP──► payments-mcp ──HTTP──► payments-api
  (TS)              (Python)              (Python)                 │  (Rust)             (Python)              (Go)
                                                                   │ ExtMCP/gRPC — permit or deny, before forwarding
                                                                   ▼
                                                         policy-decision-service
                                                                (Python)
```

## Telemetry

Vendor-neutral: OTLP to an OpenTelemetry Collector, then Jaeger. Setting
`DASH0_AUTH_TOKEN` adds a second exporter with no application code change.

We use the telemetry the frameworks emit rather than inventing our own, and we
report the gaps we find. See [`docs/telemetry-contract.md`](docs/telemetry-contract.md).

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — components, hops, protocols, and the trace, walked end to end
- [`docs/telemetry-contract.md`](docs/telemetry-contract.md) — verified instrumentation and convention gaps
- [`docs/upstream-findings.md`](docs/upstream-findings.md) — ten consolidated, filing-ready findings against `a2a-sdk`, FastMCP, `mcp` and agentgateway v1.5.0, **none of them filed**: nine still open (one of those already fixed upstream but unreleased) and one closed on discovery. M2.2 added three, all about agentgateway's ExtMCP policy hook
- [`docs/superpowers/specs/`](docs/superpowers/specs/) — designs
- [`docs/superpowers/plans/`](docs/superpowers/plans/) — implementation plans
