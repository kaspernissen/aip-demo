# Northstar — an Agentic Internal Platform reference implementation

Northstar Insurance is a fictional insurer. A customer files a vehicle-damage
claim, and a set of AI agents investigates and settles it. The platform
underneath them decides whether each action is permitted, asks a human when
it isn't, records what happened as traces, scores whether the agents followed
policy, and controls how much autonomy they have through one live setting.

Everything runs on a laptop in a kind cluster, and it is built from open
standards and open-source projects rather than a proprietary "AI platform":
A2A and MCP for the agents, SPIFFE/SPIRE for identity, agentgateway for
enforcement, OpenFeature for the autonomy setting, OpenTelemetry for the
evidence, and Backstage as the view.

**Running the demo?** Start at [`docs/demo-runbook.md`](docs/demo-runbook.md).
It has the pre-flight checks, the demo beat by beat with what to say at each,
the reset between runs, and live triage. This file explains what the system
is; the runbook is the script.

## What it shows

- **Every tool call is governed.** The settlement agent can only reach the
  payments tool through agentgateway. The gateway verifies the agent's
  SPIRE-issued identity, then asks the Policy Decision Service whether this
  caller may make this call. The decision is fail-closed: if the policy
  service can't answer, the payment is refused.
- **A refusal is a success.** The autonomy dial is a maker-checker policy: at
  `assisted` the agent may release nothing (€0), at `autonomous` up to €6,000.
  A payment over the agent's limit is proposed, refused and recorded as
  awaiting approval. The model explains the refusal to the claimant instead of
  retrying.
- **A human releases the money.** The awaiting payment appears in the reviewer
  UI. When a reviewer approves it, the reviewer UI itself calls
  `execute_payment` through the same gateway and policy check, under its own
  identity. The policy permits that only when it reads `awaiting_approval` from
  the ledger. A reviewer can also reject, which is final. The approval trace is
  joined to the refused one by a span link.
- **One live value changes what the agent may do.** The autonomy level is an
  OpenFeature flag, `northstar-autonomy`, stored as a Kubernetes object and
  read by the policy service through a flagd sidecar. Flipping it from
  `assisted` to `autonomous` lets the agent execute a claim up to €6,000
  without a human, with nothing redeployed.
- **Why a payment happened can be proven.** Traces go to Jaeger and
  OpenSearch. The evidence view in OpenSearch Dashboards rebuilds a claim's
  audit trail from spans the system already emits: who decided, under which
  rule and limit, who approved, and which identity executed it. `eval-scorer`
  writes policy-adherence scores back into the trace they judge.
- **An agent can never edit what constrains it.** Kyverno denies an agent
  ServiceAccount any write to the autonomy flag, the gateway's config, the
  Cilium policy, RBAC or its own policy, even when an over-broad RBAC grant
  would allow it. It fails closed.
- **The platform API has a face.** Backstage shows the catalog and an autonomy
  card that reads the flag live and can flip it, through a ServiceAccount that
  may patch that one object and nothing else.

## Architecture

The claim path, and the approval path that re-enters the same gateway:

<pre class="mermaid">
flowchart LR
    br["browser · TS"] -->|A2A| ca["`claims-agent
      Python`"]
    ca -->|A2A| sa["`settlement-agent
      Python`"]
    sa -->|MCP| gw["`agentgateway
      Rust`"]
    gw -->|MCP| pm["`payments-mcp
      Python`"]
    pm -->|HTTP| pa["`payments-api
      Go`"]
    gw <-->|"`ExtMCP/gRPC — permit or deny,
      before forwarding`"| pds["`policy-decision-service
      Python`"]

    hb["browser · human"] --> ru["`reviewer-ui
      Python`"]
    ru -->|"`MCP — execute_payment
      under its own SPIFFE ID`"| gw
</pre>

A refused claim and its later approval are two traces, joined by a span link
from `review.decision` (the root of trace B) back to the refusal span in trace A,
rather than a parent-child edge, because the human decides minutes later in a
different process:

<pre class="mermaid">
flowchart LR
    subgraph A["trace A — refused"]
        a1["browser → ... → settlement-agent"] --> a2["deny"]
    end
    subgraph B["trace B — approved"]
        b1["review.decision"] --> b2["execute_payment via the gateway"]
    end
    b1 -.->|"span link"| a1
</pre>

| Component | What it does |
|---|---|
| `apps/customer-ui` | Next.js page where a customer files a claim; starts the trace in the browser |
| `agents/claims-agent` | Takes the claim over A2A and delegates settlement |
| `agents/settlement-agent` | Decides the payout with a Claude model call and pays through MCP tools |
| `tools/payments-mcp` | MCP server exposing `propose_payment`, `execute_payment` and friends |
| `systems/payments-api` | Go system of record for payments; records facts, decides nothing |
| `platform/policy-decision-service` | Permits or denies each `tools/call`, keyed by caller identity and autonomy level |
| `apps/reviewer-ui` | Where a human reads a refusal and approves or rejects it |
| `platform/eval-scorer` | Scores a settlement's trajectory against five policy-adherence checks |
| `platform/backstage` | Catalog, TechDocs and the autonomy-contract card |

Platform pieces installed from upstream: Cilium (network policy, and the only
route to the payments tool), SPIRE (workload identity), agentgateway (the
enforcement point), the Open Feature Operator and flagd (the autonomy flag),
Kyverno (the keystone),
and the OpenTelemetry Collector, Jaeger, OpenSearch with Dashboards, and
Prometheus (the evidence). [`docs/architecture.md`](docs/architecture.md)
walks every hop.

## The rings

The platform bounds an agent in layers, an organisation taken from William
Rizzo's *Bounded Agent Autonomy* demo (OSFF 2026), which is also where the
keystone idea comes from. Each ring answers one question:

| Ring | Question | Northstar |
|---|---|---|
| Core · Identity | who is it | SPIRE JWT-SVIDs, verified on every call |
| Ring 1 · Reach | what can it connect to | Cilium: the gateway is the only way into `payments-mcp` |
| Ring 2 · Capability | what may it do | agentgateway and the Policy Decision Service, fail-closed; the autonomy flag |
| Keystone | can it edit what constrains it | Kyverno: no |

Northstar differs from that demo in two places, on purpose: SPIRE rather than
Linkerd for identity, and a decision service rather than a static CEL
allowlist for capability. The keystone uses Kyverno's current
`ValidatingPolicy` type rather than the deprecated `ClusterPolicy`.

## Stages

The demo builds up in seven stages, so a shorter talk can stop at the idea it
needs. Each stage adds to the one before.

| Stage | Name | Adds |
|---|---|---|
| 1 | Connected | Agents, MCP server, payments system, customer UI, Collector, Jaeger |
| 2 | Governed | Cilium, SPIRE, agentgateway and the Policy Decision Service |
| 3 | Supervised | Reviewer UI, which releases the money itself |
| 4 | Observable | OpenSearch, OpenSearch Dashboards with the evidence view, Prometheus, eval-scorer |
| 5 | Progressive autonomy | Open Feature Operator, flagd and the `northstar-autonomy` flag |
| 6 | Golden path | Backstage |
| 7 | Bounded | Kyverno and the keystone policy |

Stage 7 is the complete demo. [`docs/demo-runbook.md`](docs/demo-runbook.md)
§3 says which stage suits which talk length.

## Quick start

You need Docker, kind, kubectl, helm, [uv](https://docs.astral.sh/uv/) and an
Anthropic API key. The cluster needs about 6 GB of memory at stage 6, measured before Kyverno
was added.

```bash
cp .env.template .env
# edit .env and set ANTHROPIC_API_KEY. The cluster comes up without it, but
# the first claim fails at the model call. setup-kind.sh prints SET or EMPTY
# for it. The DASH0_* variables are optional.

./scripts/setup-kind.sh --stage 7
./scripts/start-demo.sh
```

`./scripts/start-demo.sh` owns the port-forwards and checks that each one
actually answers. Then open:

| Surface | URL |
|---|---|
| Customer UI | http://localhost:8080 |
| Reviewer UI | http://localhost:8081 |
| Jaeger | http://localhost:16686 |
| OpenSearch Dashboards (the evidence view) | http://localhost:5601 |
| Prometheus | http://localhost:9090 |
| Backstage | http://localhost:7007 |

Submit the pre-filled €6,200 claim, watch it come back refused, then approve
it in the reviewer UI. Both the click and the approved payment show up in
Jaeger, linked to the refused trace.

**The five-minute demo** is five commands in one terminal, driven by
`scripts/demo` (it runs under the repo's `.venv`):

```bash
scripts/demo guard                # no identity: 401. Around the gateway: dropped. With SPIRE's identity: the tools
scripts/demo claim 6200           # a real claim: refused by the platform, then the trace
scripts/demo autonomy autonomous  # one live value, nothing redeployed ...
scripts/demo claim 6200           # ... the same claim, executed
scripts/demo autonomy assisted    # ... and back
scripts/demo keystone             # the agent cannot edit what constrains it
scripts/demo evidence             # who decided, under which rule, for which identity
scripts/demo reset                # flag assisted, over-grant absent, run state cleared
```

Each command exits non-zero when the expected outcome does not happen.
[`docs/demo-runbook.md`](docs/demo-runbook.md) §5 has what to say at each step.
The two claims call the model; `guard`, `keystone` and `evidence` do not.

Two short scripts show the outer rings and the keystone, with no model call
(`scripts/demo guard` and `scripts/demo keystone` run the same checks):
`./scripts/beat-reach.sh` (a workload cannot reach the payments tool around the
gateway) and `./scripts/beat-keystone.sh` (an agent cannot change the autonomy
flag, even when an over-broad grant lets RBAC through).

Tear everything down with `./scripts/teardown.sh`.

## Tests

`./scripts/smoke-test.sh` is the full gate. It needs a Python venv at the repo
root, one per Python service, and a Playwright Chromium:

```bash
uv venv --python 3.13 .venv && uv pip install -r tests/requirements.txt && .venv/bin/playwright install chromium
for svc in tools/payments-mcp agents/settlement-agent agents/claims-agent platform/policy-decision-service apps/reviewer-ui platform/eval-scorer; do
  (cd "$svc" && uv venv --python 3.13 .venv && uv pip install -r requirements.txt)
done
```

The Policy Decision Service's gRPC stubs are generated, not committed, so the
repo and its images can't disagree about the vendored `proto/ext_mcp.proto`.
The gate generates them itself. To run a suite on its own first, generate them:

```bash
(cd platform/policy-decision-service && ./gen-stubs.sh)
```

| Command | Cluster | Anthropic tokens |
|---|---|---|
| `.venv/bin/python -m pytest tests/unit -q` | not needed | none |
| `.venv/bin/python -m pytest tests/ -q` | required, with `./scripts/start-demo.sh` running | none |
| `RUN_BROWSER_TESTS=1 ./scripts/smoke-test.sh` | required | yes — the full gate |

`tests/unit` is hermetic: no cluster, no key, no network. A bare
`pytest tests/` needs the cluster and its port-forwards and fails with
`httpx.ConnectError` without them, on purpose. Every test that drives a model
call or a browser sits behind `RUN_BROWSER_TESTS=1`, so a bare run spends
nothing. The exact counts are measured in
[`docs/telemetry-contract.md`](docs/telemetry-contract.md), and a unit test
keeps them honest.

### Mutation-testing a guard

A guard here is trusted only once it has been mutated and seen to fail for the
reason it names. Don't hand-roll the edit, run, revert loop; use:

```bash
./scripts/mutate-guard.sh <file> <old-text> <new-text> <test command...>
```

It restores the file on every exit path, refuses a replacement that changes
nothing, and sets **`PYTHONDONTWRITEBYTECODE=1`** and clears `__pycache__`
around the run. That last part matters: CPython checks a cached `.pyc` only
against the source's size and whole-second mtime, so a same-length mutation
written within the same second can run stale bytecode and report a false
pass or a false failure. `tests/unit/test_mutation_protocol.py` pins this
behaviour.

## Telemetry

Everything is OTLP to an OpenTelemetry Collector, then Jaeger, OpenSearch and
Prometheus. Sampling is always 100%: an audit trail with gaps is not evidence.
Setting `DASH0_AUTH_TOKEN` adds a Dash0 exporter with no application change.

The system uses the telemetry the frameworks emit, including the GenAI
semantic conventions, rather than inventing its own, and it documents the gaps
it finds. See [`docs/telemetry-contract.md`](docs/telemetry-contract.md).

## What it deliberately does not do

- **Authorization covers one hop.** Identity and policy are enforced on the
  MCP call through agentgateway. The A2A hops and the call into payments-api
  carry no policy, so payments-api moves money for anything that can reach it.
- **Approval is one unauthenticated click.** There is no second approver and
  no expiry, and the reviewer id is a string the form supplies.
- **Backstage is unauthenticated.** Anyone who can reach port 7007 can flip
  the autonomy flag. What limits the damage is that the one write it can make
  is to that single object.
- **No scaffolder, and a static catalog.** The platform API exists as a real
  object with a real UI, but nothing here generates a new agent from it.

[`docs/architecture.md`](docs/architecture.md)'s "What the system
deliberately does not do" section has the full list.

## Documentation

- [`docs/demo-runbook.md`](docs/demo-runbook.md) — the operator's script: bring-up, the demo beats, options, reset and triage
- [`docs/architecture.md`](docs/architecture.md) — components, hops, protocols and the trace, end to end
- [`docs/telemetry-contract.md`](docs/telemetry-contract.md) — the instrumentation as measured, and the convention gaps
- [`docs/upstream-findings.md`](docs/upstream-findings.md) — filing-ready findings against the upstream projects this repo uses
- [`talk/`](talk/) — the KubeCon + CloudNativeCon North America 2026 talk built on this demo, with the reports it cites
- [`docs/superpowers/`](docs/superpowers/) — the design specs and implementation plans the repo was built from
