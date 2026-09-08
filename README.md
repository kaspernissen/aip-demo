# Northstar — an Agentic Internal Platform reference implementation

Northstar Insurance is a fictional insurer. A customer files a vehicle-damage
claim; a set of agents collaborates to investigate and settle it. The platform
decides whether those interactions are permitted, whether a human must approve
them, records what happened, evaluates the quality of the trajectory, and
gradually determines how much autonomy the system should have.

Built from open standards and existing open-source projects, not a proprietary
"AI platform".

## Status

**M1.1 — Connected (walking skeleton).** The claim flows end to end and produces
one correlated trace. There is no governance of any kind: a €6,200 payment
executes because a message asked for it. That is deliberate — it is the system
the rest of the demo argues against.

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
# blank and setup-kind.sh will still bring up all 7 pods Running and fully
# traced (the key is deliberately optional at the Kubernetes level), but the
# FIRST claim submission fails at the model call — the one thing this
# milestone exists to demonstrate. setup-kind.sh prints SET/EMPTY for this
# variable; check for SET before going on stage. DASH0_AUTH_TOKEN and its
# three DASH0_* siblings are optional — see the comments in .env.template for
# exactly which variables are required.

./scripts/setup-kind.sh --stage 1
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
end to end. Only `tests/e2e/test_browser_root_span.py` and
`tests/e2e/test_settlement_llm_trace.py` sit behind `RUN_BROWSER_TESTS=1`.
Without a live cluster and port-forwards those e2e files fail rather than skip.

`tests/unit` is the hermetic subset: no cluster, no API key, no network.

## Stages

The demo has six stages; the setup script installs only what a stage needs, so
the laptop runs one stage at a time.

| Stage | Name | Adds |
|---|---|---|
| 1 | Connected | agents, MCP servers, systems of record, customer UI, collector, Jaeger |
| 2 | Governed | agentgateway, SPIFFE/SPIRE, Policy Decision Service, Kyverno, Cilium |
| 3 | Supervised | Dapr Workflow, reviewer UI, Keycloak |
| 4 | Observable | OpenSearch + Agent Evals, Prometheus |
| 5 | Progressive autonomy | OpenFeature / flagd |
| 6 | Golden path | Crossplane `Agent` CRD, Argo CD, Backstage |

## Architecture (stage 1)

```text
browser ──A2A──► claims-agent ──A2A──► settlement-agent ──MCP──► payments-mcp ──HTTP──► payments-api
  (TS)              (Python)              (Python)                 (Python)              (Go)
```

## Telemetry

Vendor-neutral: OTLP to an OpenTelemetry Collector, then Jaeger. Setting
`DASH0_AUTH_TOKEN` adds a second exporter with no application code change.

We use the telemetry the frameworks emit rather than inventing our own, and we
report the gaps we find. See [`docs/telemetry-contract.md`](docs/telemetry-contract.md).

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — components, hops, protocols, and the trace, walked end to end
- [`docs/telemetry-contract.md`](docs/telemetry-contract.md) — verified instrumentation and convention gaps
- [`docs/upstream-findings.md`](docs/upstream-findings.md) — the four convention gaps, consolidated as filing-ready findings (not yet filed)
- [`docs/superpowers/specs/`](docs/superpowers/specs/) — designs
- [`docs/superpowers/plans/`](docs/superpowers/plans/) — implementation plans
