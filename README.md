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

```bash
export ANTHROPIC_API_KEY=...        # not used until M1.2
export DASH0_AUTH_TOKEN=...         # optional; adds a second OTLP exporter

./scripts/setup-kind.sh --stage 1
./scripts/smoke-test.sh
```

Then open:

| Surface | URL |
|---|---|
| Customer UI | http://localhost:8080 |
| Jaeger | http://localhost:16686 |

Tear down with `./scripts/teardown.sh`.

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

- [`docs/telemetry-contract.md`](docs/telemetry-contract.md) — verified instrumentation and convention gaps
- [`docs/superpowers/specs/`](docs/superpowers/specs/) — designs
- [`docs/superpowers/plans/`](docs/superpowers/plans/) — implementation plans
