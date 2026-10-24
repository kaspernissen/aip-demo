# Northstar Telemetry Contract

What each component emits, the conventions the system follows, the names it
owns, the gaps found in upstream projects, and how the tests keep all of that
true. Every service exports OTLP to one OpenTelemetry Collector, which fans out
to Jaeger (traces), Prometheus (metrics) and, optionally, Dash0. Sampling is
always 100%: an audit trail with gaps is not evidence.

The owned names (`northstar.*`) are defined in the Weaver registry,
`telemetry/registry/`. The generated tables under "Registry-defined attributes,
spans and metrics" are the authoritative inventory; where prose and tables
disagree, the registry wins. Names that belong to other projects (`gen_ai.*`,
`mcp.*`, `http.*`, agentgateway's and Linkerd's own) are listed here as they
arrive on the wire, because this system does not rename or enrich them.

## The governing rule

Use the telemetry the frameworks actually emit. Verify per component. Instrument
only our own code. Where a convention gap exists, report it upstream and never
paper over it with a bespoke convention.

Two consequences recur below:

- Nothing in this repository rewrites, aliases or adds attributes to a
  third-party component's spans or metrics. Where a component's output is odd
  (agentgateway's attribute spelling, the MCP client's span names), this
  document records it as it is.
- A name this project defines is prefixed `northstar.`, so a reader can tell
  at a glance which attributes a framework emitted and which this project
  chose.

## Running the tests

`tests/unit` is hermetic: no cluster, no API key, no network.

A bare `pytest tests/` needs the cluster that `./scripts/setup-kind.sh` builds.
The tests reach the UIs, Jaeger, Prometheus, AgentEvals and the collector
through the same localhost ports the browser uses (kind host ports onto
NodePorts, `deploy/kind/cluster.yaml`). payments-api, payments-mcp and
settlement-agent have no host port, because their Linkerd Servers refuse a
caller without a mesh identity, so the tests open a `kubectl port-forward` to
each on first use (`tests/harness/live.py`) and close it when the run ends.
Without the cluster the run fails with `httpx.ConnectError` and `kubectl`
errors rather than skipping: a suite that skips when it cannot reach what it
asserts on looks green while proving nothing. Every e2e module that spends
Anthropic tokens or launches a browser sits behind `RUN_BROWSER_TESTS=1`, so a
bare run costs no tokens and proves nothing about the A2A chain or the browser
hop.

`./scripts/smoke-test.sh` is the gate. It runs every per-service suite, then
`RUN_BROWSER_TESTS=1 pytest tests -v` over the whole tree, and fails if
anything was skipped.

One xfail is expected:
`tests/e2e/test_claim_correlation.py::test_claim_id_is_on_every_mcp_hop_span`
documents the FastMCP baggage gap (Gap 4). It calls `pytest.xfail()` inside a
`try/except AssertionError` around that one assertion, so a failure in the
fixture's setup still shows as an error, and its `else` branch fails the run
the day the gap closes upstream.

## Enabled instrumentation

| Component | Instrumentation | Version | Span names | Notes |
|---|---|---|---|---|
| customer-ui | OpenTelemetry JS web, manual root spans | `@opentelemetry/sdk-trace-web` 2.8.0, `context-zone` 1.30.0, `exporter-trace-otlp-http` 0.219.0, `resources` 2.8.0 | `northstar.claim.submit`, `northstar.claim.wait`, `northstar.claim.answer` | Owns the trace roots. claims-agent's CORS `allow_headers` must list `traceparent`, `tracestate` and `baggage` so the browser's request survives preflight; the collector instead needs `http://localhost:8080` in its OTLP receiver's `allowed_origins`. The injection helper must be a *static* import: a dynamic `import()` or any other `await` before reading the active span loses it under `ZoneContextManager` (`apps/customer-ui/NOTES.md`; pinned by that service's vitest regression test). |
| claims-agent | `a2a-sdk[telemetry]` + `opentelemetry-instrumentation-starlette` | `a2a-sdk==0.3.7`, `starlette` instrumentation `0.65b0` | ASGI entry span `POST /`, and the `a2a.server.*` call-graph spans (`JSONRPCHandler.on_message_send`, `DefaultRequestHandler.*`, `EventQueue.*`, `EventConsumer.consume_all`, ...); owned `northstar.a2a.resume` | `DISABLE_OTEL_INSTRUMENTATION` must stay unset; `bootstrap.configure()` raises if it is `true`. Inbound W3C trace context comes from `StarletteInstrumentor` wrapping the built ASGI app: `a2a-sdk` extracts nothing itself (Gap 1). |
| claims-agent (intake model) | PydanticAI `Agent.instrument_all(InstrumentationSettings(...))` plus `HTTPX2ClientInstrumentor` | `pydantic-ai-slim==2.42.0`, `version=5` | `invoke_agent claims-agent` and one `chat <model>` per round, with the model's HTTP hop as its child. Only for a claim written as free text; a JSON claim emits none of these | The claim id is in baggage before the model runs, so these spans carry `northstar.claim.id`; `gen_ai.conversation.id` is the claim id. Held code-identical to settlement-agent's settings by `test_instrumentation_settings.py`. With content capture on, the `chat` span carries the customer's own description (see "Content capture"). |
| settlement-agent | `a2a-sdk[telemetry]` + Starlette instrumentation + the `mcp` client, which instruments itself | `a2a-sdk==0.3.7`, `mcp==2.2.0`, starlette instrumentation `0.65b0` | Same shape as claims-agent, plus outbound `httpx2` CLIENT spans to agentgateway and to `api.anthropic.com`, and the SDK's own `MCP send initialize` / `MCP send tools/call <tool>` CLIENT spans | Only caller of agentgateway in the claim path. The outbound spans to the gateway are `httpx2` spans because `mcp` 2.x's transport is built on `httpx2`, a distribution separate from `httpx`; the instrumentor for it is `HTTPX2ClientInstrumentor`, applied in `bootstrap.py` next to `HTTPXClientInstrumentor`. |
| settlement-agent (model) | PydanticAI `Agent.instrument_all(InstrumentationSettings(...))` in `bootstrap.py` | `pydantic-ai-slim==2.42.0`, `version=5` (the library default, pinned) | `invoke_agent settlement_agent` (internal, 1 per request), `chat <model>` (client, one per model turn), `execute_tool propose_payment`, `execute_tool execute_payment`, `execute_tool refuse_payment` (internal) | The decision itself, not only its transport. `include_content` reads `NORTHSTAR_CAPTURE_CONTENT` (default on; see "Content capture"). `include_model_request_parameters` stays at the library default (`True`). |
| agentgateway | native, `frontendPolicies.tracing` in its own config | `cr.agentgateway.dev/agentgateway:v1.6.0` | Per MCP message: a SERVER span named after the JSON-RPC method and a CLIENT span named `<method> <target>`; per tool call an extra `Guardrail` CLIENT span | Verifies the caller's SPIRE JWT-SVID (`jwtAuth`, strict), asks the Policy Decision Service about every `tools/call` (`mcpGuardrails`, remote, `failureMode: failClosed`), and enforces a CEL ceiling on `execute_payment`. Exports OTLP/gRPC on 4317, the only component here that does not use OTLP/HTTP on 4318. `randomSampling: true` is set, see "Why the trace survives the gateway". Resource `telemetry.sdk.language=rust`. |
| payments-mcp | FastMCP native | `fastmcp==4.0.3` | `tools/call <tool>` for `propose_payment`, `execute_payment`, `refuse_payment` and `get_payment` | The TracerProvider need not be installed before importing fastmcp: `fastmcp.telemetry.get_tracer()` asks for the provider on every span. `FASTMCP_TELEMETRY_MODE=native` is set explicitly although it is the default. |
| policy-decision-service | OTel Python SDK, applied by `pds/telemetry.py` | `opentelemetry-sdk==1.44.0` | `northstar.policy.decision` (SERVER), one per `tools/call` the gateway sends for a decision | Entirely this project's own span: the gRPC servicer is the top of its own call stack and no framework emits a policy decision. It joins the caller's trace because agentgateway forwards `traceparent` to the guardrail hook. Status stays `Unset` on a deny (see "The policy decision span"). Instrumentation scope `northstar.policy-decision-service`; the Resource has no `service.version`. |
| payments-api | `otelhttp` | `otelhttp v0.71.0`, otel `v1.46.0` | `POST /payments/propose`, `POST /payments/{paymentId}/execute`, `GET /payments/{paymentId}`, and the ledger's other routes | Stable HTTP spelling by default (`http.request.method`, `http.response.status_code`, `http.route`); `network.protocol.name` is not emitted, only `network.protocol.version`. No `OTEL_SEMCONV_STABILITY_OPT_IN` is set or needed. |
| reviewer-ui | OTel Python SDK in `bootstrap.py`; `HTTPXClientInstrumentor` for outbound calls | `opentelemetry-sdk==1.44.0` | Owned `northstar.review.decision` and `northstar.review.queue` (both SERVER, both trace roots) | No ASGI/FastAPI instrumentation. `GET /healthz` produces nothing. The outbound calls to payments-api, agentgateway and claims-agent get CLIENT spans and carry `traceparent`. |
| Backstage | hand-built OTLP/HTTP JSON | none | `northstar.autonomy_contract.change` | See "The autonomy card's span". |

### One HTTP convention across this project's own services

`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set on the three Python services that
use HTTP instrumentation with a default legacy spelling (claims-agent,
settlement-agent, payments-mcp). OTel Python's HTTP instrumentation defaults to
the legacy attribute names (`http.method`, `http.url`, `http.status_code`),
while payments-api's Go `otelhttp` emits the stable names with no opt-in
available. The variable is the only reason those four services agree on one
HTTP convention. A service added without it would mix conventions invisibly,
because both spellings are well-formed telemetry. Each service's `NOTES.md`
shows the legacy output before and the stable output after the opt-in. The
opt-in applies to both `HTTPXClientInstrumentor` and `HTTPX2ClientInstrumentor`,
since they share one tracer in the same module.

Two components do not follow it, and both are recorded rather than hidden:

- **agentgateway** emits its own attribute vocabulary on some spans (see its
  section) and has no opt-in to change it.
- **reviewer-ui** sets no `OTEL_SEMCONV_STABILITY_OPT_IN`, so its outbound HTTP
  spans use the legacy spelling and its HTTP client metric is
  `http_client_duration_milliseconds_*` (legacy, milliseconds) where the other
  Python services report `http_client_request_duration_seconds_*`. Setting the
  variable would also change reviewer-ui's span attributes, which e2e
  assertions read, so it is a contract change of its own. A panel that sums
  HTTP client latency across services mixes units because of this.

### OpenTelemetry library versions

Every Python process runs one OTel generation: `opentelemetry-sdk==1.44.0` with
instrumentation `0.65b0`, including the repo-root test harness. The browser side
is not aligned: `@opentelemetry/context-zone` is `1.30.0` beside `sdk-trace-web`
and `resources` at `2.8.0`, and the top-level `@opentelemetry/semantic-conventions`
pin (`1.28.0`) differs from what transitives resolve. Neither is a live defect:
the lockfile carries exactly one `@opentelemetry/core` (`2.8.0`), and each
service's emitted spelling is measured independently rather than assumed from
another service's pin. A bump of any pin needs the same re-verification: rebuild
the service, re-run the suites, re-capture a live trace and confirm the span
shape is unchanged.

### Hand-written spans

Besides library instrumentation, this project's own code opens spans in a small,
named set of places, each because nothing else could: customer-ui's three root
spans (nothing upstream of the browser exists), the Policy Decision Service's
decision span, reviewer-ui's two spans, claims-agent's resume span and
Backstage's contract-change span. Everything else in a trace comes from a
library. A grep for `get_tracer`, `start_span`, `start_as_current_span` and
`startActiveSpan` over `agents/`, `apps/`, `platform/`, `tools/` and `systems/`
(tests aside) finds only those sites.

## Export destinations: Jaeger, Prometheus and Dash0

The collector runs `deploy/observability/collector-config.yaml`. When
`DASH0_AUTH_TOKEN` is set, `scripts/setup-kind.sh` also passes
`deploy/observability/collector-dash0-overlay.yaml` as a second `--config`. The
collector merges the two: the overlay declares `otlp_grpc/dash0` and restates
each pipeline's exporter list with it added
(`tests/unit/test_collector_dash0_overlay.py` checks the merged result).

| Pipeline | Exporters |
|---|---|
| `traces` | `otlp_grpc/jaeger`, `otlp_grpc/weaver`, `otlp_http/agentevals`, `otlp_grpc/dash0` (with the overlay), `debug` |
| `metrics` | `otlp_http/prometheus`, `otlp_grpc/weaver`, `otlp_grpc/dash0` (with the overlay), `debug` |
| `logs` | `otlp_grpc/weaver`, `otlp_grpc/dash0` (with the overlay), `debug` |

Traces go to Jaeger and Dash0; metrics go to Prometheus and Dash0; logs go to
Dash0. The logs are AgentEvals' `gen_ai.evaluation.result` events. Every signal
also goes, as a copy and never a sample, to Weaver live-check
(`otlp_grpc/weaver`), which checks it against the registry. AgentEvals receives a
copy of every trace and scores what the agent did from its spans. No processor
renames an attribute, so every destination gets the canonical names.

Jaeger is what the talk shows, because anyone can stand it up locally with no
account. Dash0 is additive, enabled by one environment variable
(`DASH0_AUTH_TOKEN`), and never a dependency of the demo working. Jaeger runs
with an in-memory store capped at 20,000 traces; the pod's memory limit is sized
for that cap so an out-of-memory kill does not erase the evidence.

## The trace

One claim that settles without a human is one browser-rooted trace across
seven services:

```
customer-ui        northstar.claim.submit                     root, from the browser
  claims-agent       POST /  ->  a2a.server.* spans            A2A message/send
    settlement-agent   POST /  ->  invoke_agent settlement_agent
      settlement-agent   execute_tool propose_payment          internal
        settlement-agent   MCP send tools/call propose_payment client   (mcp 2.2.0's own span)
          settlement-agent   POST                              client   (httpx2, to agentgateway)
            agentgateway     tools/call                        server
              agentgateway     Guardrail                       client   (sibling of the next)
              policy-decision-service  northstar.policy.decision  server
              agentgateway     tools/call payments-mcp_propose_payment  client
                payments-mcp     tools/call propose_payment    server
                  payments-mcp     POST                        client
                    payments-api     POST /payments/propose    server
```

Every span is parented on the one above it and there is one trace id and one
root. The same shape repeats for `execute_payment`. A refused `execute_payment`
stops at the gateway: the trace holds the proposal in payments-api and no
execute span, which is the trace-level evidence that the proposal was recorded
and only the execution was refused.

**A claim that waits for a person is two traces joined by span links.** Trace A
is the agent's work. It ends when claims-agent records `input-required` and
`execute()` returns: no SDK span stays open for the wait and nothing polls.
Trace B is the person's work, rooted at reviewer-ui's `northstar.review.decision`:

```
northstar.review.decision [reviewer-ui, SERVER]   link -> trace A, the refusal span
  GET [reviewer-ui, CLIENT]                       the ledger read
    GET /payments/{paymentId} [payments-api]
  POST [reviewer-ui, CLIENT]  x3                  initialize, notifications/initialized, tools/call
    tools/call [agentgateway]
      northstar.policy.decision [policy-decision-service]   rule human-approved.execute
        GET /payments/{paymentId} [payments-api]            the PDS's own ledger read
      Guardrail [agentgateway]
      tools/call payments-mcp_execute_payment [agentgateway]
        tools/call execute_payment [payments-mcp]
          POST [payments-mcp] -> POST /payments/{paymentId}/execute [payments-api]
  POST [reviewer-ui, CLIENT] -> POST / [claims-agent]       tasks/get, then message/send (the resume)
    northstar.a2a.resume [claims-agent]           link -> trace A, the hand-off span
```

(The a2a-sdk's own spans under claims-agent's `POST /` are omitted.) A span link,
not a parent, joins the traces, because the human acts minutes later in a
different process: a span held open across that gap would misstate both the
duration and what was executing. Two links exist:

- `northstar.review.decision` links to the settlement-agent span that was
  active when the refusal was recorded. The ids come from the ledger's
  `refusal.refusedTraceId` and `refusedSpanId`. The link is added after the span
  starts, because the ids come from the ledger read, which runs inside the span;
  nothing is linked when the record holds no ids. A span id has to travel, not
  only a trace id: the SDK silently drops a link whose `SpanContext` has
  `span_id=0`.
- `northstar.a2a.resume` (claims-agent) links to the span that handed the claim
  over, with the link attribute `northstar.a2a.link.reason = resume`. The parked
  span context and the parked reply travel in the input-required update's task
  metadata (`northstar.a2a.parked_span_context`, `northstar.a2a.parked_reply`).
  That is A2A task metadata, not telemetry, so neither is a registry attribute.

`tests/e2e/test_human_in_the_loop.py` asserts the shape (`test_trace_a_is_finished_at_the_hand_off`,
`test_trace_b_is_rooted_at_review_decision_and_links_to_trace_a`,
`test_the_resume_runs_in_trace_b_and_links_to_trace_a`).

Jaeger renders a link as a `FOLLOWS_FROM` reference carrying the target's
`traceID` and `spanID`; a parent is `CHILD_OF`. The harness's `Span.links` reads
the former and `Span.parent_id` the latter.

When the task completes, a2a-sdk pushes the final Task to the customer-ui server
(`POST /a2a/push`, service `customer-ui-server`, token in
`X-A2A-Notification-Token`), and the browser's `GET /claims/events` ends with
the outcome. claims-agent's `TerminalPushNotificationSender` sends terminal
states only, so trace A holds no push. The push token is on no span; a test
asserts it. The `TaskStore` is in memory, so a claims-agent restart loses the
waiting claims.

**No whole-trace span total is published.** The total varies between identically
shaped runs: the a2a-sdk's polling spans, whether a model turn produced a
`thinking` part, MCP session bookkeeping, and a refused run being genuinely
smaller than a permitted one. What is stable, and what the tests assert, is the
shape: which services, which operations, kinds and parents, one root and no
orphans beyond the test's own synthetic root. Jaeger's own `span.kind` and
`internal.span.format` tags appear on every span from every service and are
excluded from every attribute list below.

**The refusal is not a red span.** `northstar.policy.decision` stays `Unset` on a
deny: a refusal is the platform working. The refusal is legible from its
attributes (decision, rule, amount, limit) and from agentgateway's own
`otel.status_description` one span up, where the gateway does set ERROR because
from its point of view the proxied call failed.

## Model calls (PydanticAI)

`agents/settlement-agent/bootstrap.py` applies
`Agent.instrument_all(InstrumentationSettings(include_content=capture_content_enabled(), version=5))`.
`version` is pinned at the library default so a later default cannot drift
underneath: at `version=5`, `CallDeferred` and `ApprovalRequired` are treated as
control flow rather than marking their span `ERROR`, and tool results are emitted
as `role='user'` messages (`version=6` would use `role='tool'`; not adopted).
`include_model_request_parameters` is never passed and stays at its default
(`True`). `pydantic-ai-slim` is pinned to 2.42.0, and the inventory below is
re-measured on every bump by diffing every `pydantic-ai`-scoped span's whole
attribute set out of Jaeger before and after.

**The complete `gen_ai.*` attribute vocabulary with content capture on: 28
names.**

```
gen_ai.agent.call.id
gen_ai.agent.name
gen_ai.aggregated_usage.details.cache_creation_input_tokens
gen_ai.aggregated_usage.details.cache_read_input_tokens
gen_ai.aggregated_usage.details.thinking_tokens
gen_ai.aggregated_usage.input_tokens
gen_ai.aggregated_usage.output_tokens
gen_ai.conversation.id
gen_ai.input.messages
gen_ai.operation.name
gen_ai.output.messages
gen_ai.provider.name
gen_ai.request.model
gen_ai.response.finish_reasons
gen_ai.response.id
gen_ai.response.model
gen_ai.system
gen_ai.system_instructions
gen_ai.tool.call.arguments
gen_ai.tool.call.id
gen_ai.tool.call.result
gen_ai.tool.definitions
gen_ai.tool.name
gen_ai.usage.details.cache_creation_input_tokens
gen_ai.usage.details.cache_read_input_tokens
gen_ai.usage.details.thinking_tokens
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
```

Three are content-gated and disappear with capture off:
`gen_ai.tool.call.arguments` and `gen_ai.tool.call.result` on the `execute_tool`
spans, and `gen_ai.system_instructions` on the `chat` and `invoke_agent` spans.
The remaining 25 are present in both modes.

| Span | Kind | Count per request | Carries |
|---|---|---|---|
| `invoke_agent settlement_agent` | internal | 1 | `gen_ai.agent.call.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `gen_ai.operation.name=invoke_agent`, the five `gen_ai.aggregated_usage.*` roll-ups. Also the non-`gen_ai` `agent_name`, `model_name`, `logfire.msg`, `logfire.json_schema` and `pydantic_ai.all_messages` (the whole conversation; message shape only unless capture is on). |
| `chat <model>` | client | one per model turn (propose, execute or refuse, final narrative) | `gen_ai.agent.call.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `gen_ai.operation.name=chat`, `gen_ai.provider.name` and `gen_ai.system` (both `anthropic`; `gen_ai.system` is the deprecated spelling and `version=5` emits both), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.tool.definitions`, the `gen_ai.usage.*` fields (`details.thinking_tokens` only on turns where the model thought, deliberately not asserted). Also `server.address=api.anthropic.com`, `operation.cost` and `model_request_parameters`. Each has exactly one httpx2 CLIENT child, the model's wire-level hop (Gap 7). |
| `execute_tool <tool>` | internal | 1 per tool call | `gen_ai.agent.call.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `gen_ai.operation.name=execute_tool`, `gen_ai.tool.call.id`, `gen_ai.tool.name`, plus `gen_ai.tool.call.arguments` and `gen_ai.tool.call.result` when capture is on. |
| `tools/call <tool>` (payments-mcp) | server | 1 per tool call | `gen_ai.tool.name` only. FastMCP's behaviour (Gap 5 covers what is missing). |

`gen_ai.aggregated_usage.*` is a pydantic-ai extension with no counterpart in the
GenAI semantic conventions (pydantic-ai's source calls it "a custom namespace,
not part of the OpenTelemetry Semantic Conventions"). `gen_ai.tool.definitions`
is not redacted by `include_content=False`, so neither `include_content` nor
`include_model_request_parameters` is, on its own, a complete redaction switch
(see "Content capture").

## The `mcp` client's own spans

`mcp` 2.x ships OpenTelemetry on by default, so settlement-agent has spans it
does not create. They sit between PydanticAI's `execute_tool <tool>` and the
httpx2 `POST` to the gateway, and are a level of the chain, not a replacement
for one:

| Span name | Kind | Count per settlement | Attributes |
|---|---|---|---|
| `MCP send initialize` | CLIENT | one per tool call (this client opens a fresh session per call) | `mcp.method.name=initialize`, `jsonrpc.request.id=1` |
| `MCP send tools/call <tool>` | CLIENT | one per tool call | `mcp.method.name=tools/call`, `jsonrpc.request.id=2` |

Both carry `otel.scope.name=mcp-python-sdk` with no `otel.scope.version`, and
`northstar.claim.id` (stamped by this project's baggage span processor, not the
SDK).

Two convention gaps, recorded as they are:

- The span name is `MCP send {method} {target}`, not the OTel MCP semantic
  conventions' `{mcp.method.name} {target}` that payments-mcp's own server spans
  follow. The `MCP send ` prefix is a literal in `jsonrpc_dispatcher.py`.
- There is no `gen_ai.tool.name`. The tool name appears only in the span name, so
  it is not queryable as an attribute on the client side, where payments-mcp's
  server span does carry it.

The SDK's dispatcher also injects W3C trace context into `params._meta` itself
(SEP-414) on every outbound request. `agents/settlement-agent/mcp_tools.py`
hand-injects the same keys through `send_request()` because an earlier SDK
offered no other route. The SDK's injection overwrites them with a strictly
deeper parent in the same trace, so the wire is correct either way and the
hand-built path is redundant (Gap 6).

## agentgateway

`cr.agentgateway.dev/agentgateway:v1.6.0`, standalone (ConfigMap, Deployment,
Service; not the Helm chart, which installs a Gateway API control plane with
nowhere to put the policy block), in `northstar-platform`.
`deploy/platform/agentgateway/config.yaml`'s `frontendPolicies.tracing` exports
OTLP/gRPC to `otel-collector.observability.svc.cluster.local:4317`.
settlement-agent's `PAYMENTS_MCP_URL` names it, so every MCP call the model makes
crosses it. The gateway also carries one CEL `authorization` ceiling on the
route, which only ever says no (see below).

Every name in this section was read off a live span in Jaeger and dumped in full.
None was predicted from agentgateway's source or documentation, because it is not
this project's component. `tests/e2e/test_gateway_trace.py` asserts exact set
equality on the attribute keys below in both directions: a key appearing is a
failure as much as a key disappearing. The tables are for v1.6.0, which moved the
SERVER spans to the stable HTTP semantic conventions (upstream PR
[`#3182`](https://github.com/agentgateway/agentgateway/pull/3182)); the Guardrail
span still carries the legacy `http.host`, `http.method` and `http.path`.

### Span shape

Two spans per MCP message, one for the request received and one for the request
made upstream:

| Span | Kind | Parent | Count per request |
|---|---|---|---|
| `initialize` | server | settlement-agent's httpx `POST` CLIENT span | 1 per tool call |
| `initialize payments-mcp` | client | agentgateway `initialize` | 1 per tool call |
| `notifications/initialized` | server | settlement-agent's httpx `POST` | 1 per tool call |
| `notifications/initialized payments-mcp` | client | agentgateway `notifications/initialized` | 1 per tool call |
| `GET /*` | server | settlement-agent's httpx `GET` | 1 per tool call |
| `GET payments-mcp` | client | agentgateway `GET /*` | 1 per tool call |
| `tools/call` | server | settlement-agent's httpx `POST` | 1 per tool call |
| `tools/call payments-mcp_<tool>` | client | agentgateway `tools/call` | 1 per tool call |
| `DELETE /*` | server | settlement-agent's httpx `DELETE` | 1 per tool call |
| `DELETE payments-mcp` | client | agentgateway `DELETE /*` | 1 per tool call |

Three things are easy to get wrong from the outside:

- **The SERVER span for a tool call is named just `tools/call`**, the JSON-RPC
  method with no tool name. An operator filtering Jaeger by operation name sees
  identically named `tools/call` spans per settlement and must read
  `gen_ai.tool.name` to tell the tools apart. Only the CLIENT span carries the
  tool in its name.
- **`payments-mcp_<tool>` is agentgateway's multiplexing prefix**
  (`<target>_<tool>`), not a name payments-mcp recognises. payments-mcp's own
  span for the same call is `tools/call propose_payment`.
- **`GET /*` and `DELETE /*` fall back to method-and-route naming** because those
  two verbs of the streamable-HTTP transport carry no JSON-RPC method; `/*` is
  the route's match pattern.

### Attributes on the `tools/call` SERVER span

22 keys, the complete set:

```
client.address               e.g. "10.244.0.15"
duration                     e.g. "3ms"  (a string)
gateway                      "default/default"
gen_ai.tool.name             "propose_payment" | "execute_payment" | ...
http.request.method          "POST"
http.response.status_code    200
jwt.sub                      "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
listener                     "listener0"
mcp.method.name              "tools/call"
mcp.resource.type            "tool"
mcp.session.id               (base64 JSON, see the findings)
mcp.target                   "payments-mcp"
network.protocol.version     "1.1"
otel.scope.name              "agentgateway"
protocol                     "mcp"
route                        "default/route0"
server.address               "agentgateway.northstar-platform.svc.cluster.local"
server.port                  3000
span.id                      the span's own id, repeated as an attribute
trace.id                     the trace's own id, repeated as an attribute
url.path                     "/mcp"
url.scheme                   "http"
```

`jwt.sub` carries the `sub` claim of the token the gateway verified, which for a
SPIRE-minted JWT-SVID is the caller's SPIFFE ID. It is the caller's identity, not
the audience the token was minted for (both asserted in `test_gateway_trace.py`),
and it is absent from the CLIENT span: the gateway strips the credential before
the upstream call (`if !self.preserve_token { self.location.remove(req)?; }`,
`crates/agentgateway/src/http/jwt.rs`). So payments-mcp learns nothing about who
called it.

### Attributes on the `tools/call payments-mcp_<tool>` CLIENT span

Six keys, and that is all (no HTTP attributes, no duration, no session id):

```
agentgateway.outbound.kind      "Primary"
agentgateway.outbound.subtype   "Mcp"
gen_ai.tool.name                "propose_payment" | "execute_payment" | ...
mcp.method.name                 "tools/call"
mcp.target                      "payments-mcp"
otel.scope.name                 "agentgateway"
```

### An unauthenticated call's SERVER span

A refused call produces a span of a different shape. Sending an MCP `initialize`
with no `Authorization` header and a hand-minted `traceparent` makes the gateway
join that trace and emit exactly one span, 21 keys:

```
client.address                  "127.0.0.1"
duration                        "0ms"
error                           true
error.type                      "JwtAuth"
gateway                         "default/default"
http.request.method             "POST"
http.response.status_code       401
listener                        "listener0"
network.protocol.version        "1.1"
otel.scope.name                 "agentgateway"
otel.status_code                "ERROR"
otel.status_description         "authentication failure: no bearer token found"
protocol                        "http"
reason                          "JwtAuth"
route                           "default/route0"
server.address                  "localhost"  (the Host header the probe sent)
server.port                     18777        (the probe's port-forward)
span.id                         the span's own id
trace.id                        the trace's own id
url.path                        "/mcp"
url.scheme                      "http"
```

What differs from the accepted path, and why it matters:

- **The span is named `POST /*`, not `initialize`.** `jwtAuth` runs as an HTTP
  filter before the MCP backend is consulted, so the request is never parsed as
  JSON-RPC. There are no `mcp.*` attributes and no `gen_ai.tool.name`: the span
  does not say which tool was refused.
- **No `jwt.sub`.** Nothing was verified, so a refused call is anonymous in the
  trace: it shows that an unauthenticated caller was refused, never who tried.
- **`otel.status_code = ERROR`, with `otel.status_description` carrying the
  reason** (the same `TokenError` text the HTTP response returns), the one place
  this refusal's reason is machine-readable from telemetry alone.
- **`reason = "JwtAuth"`, `error = true` and `error.type = "JwtAuth"`** appear on
  no accepted span.
- **There is no CLIENT span**, the trace-level evidence that it was a refusal and
  not a failed forward.

`tests/e2e/test_caller_identity.py` asserts the behavioural half (no tool result
comes back). The exact `otel.status_description` string is deliberately not
asserted: it would pin an upstream error string with no added safety.

### A ceiling-denied call's SERVER span

A `tools/call` for `execute_payment` above the gateway's CEL ceiling (the
route's `authorization`; see `docs/architecture.md`, "Two ways to say no") is
answered by the gateway itself: `403`, `Content-Type: text/plain`, body
`authorization failed`. The trace holds one span, service `agentgateway`,
operation `POST /*` (the MCP context is not attached when an HTTP policy
rejects), no parent. Its distinguishing attributes: `error.type=Authorization`,
`reason=Authorization`, `otel.status_code=ERROR`,
`otel.status_description=authorization failed`,
`http.response.status_code=403`, `url.path=/mcp`, and `jwt.sub`. No attribute
names the rule, the limit or the amount. There is no `northstar.policy.decision`
span, because the Policy Decision Service is never asked
(`tests/e2e/test_gateway_ceiling.py::test_the_403_leaves_no_policy_decision_span`).

The rule opens with `request.method != "POST"`: without it every MCP session's
`GET` and `DELETE` on `/mcp` would be a 403. With it they pass (`GET` 200,
`DELETE` 202;
`test_a_sessions_get_and_delete_are_not_stopped_by_the_ceiling`).

### Guardrail span and policy-denied calls

`mcpGuardrails.processors[0]` is `kind: remote`, `methods: {"tools/call":
request}`, `host: policy-decision-service.northstar-platform.svc.cluster.local:9100`,
`failureMode: failClosed`. The hop shows in the trace as one `Guardrail` CLIENT
span per `tools/call`, a child of the gateway's `tools/call` SERVER span, with
nine keys:

```
agentgateway.outbound.kind      "Policy"
agentgateway.outbound.subtype   "Guardrail"
grpc.status                     0
http.host                       "policy-decision-service.northstar-platform.svc.cluster.local"
http.method                     "POST"
http.path                       "/agentgateway.dev.ext_mcp.ExtMcp/CheckRequest"
mcp.method.name                 "tools/call"
mcp.target                      "payments-mcp"
otel.scope.name                 "agentgateway"
```

- `http.path` is literally the ExtMCP gRPC method, so one span tells an operator
  the gateway asked an external service for a decision.
- `grpc.status = 0` reports the transport, not the decision: the call succeeded
  and the deny is in the payload. Under `failClosed`, a refusal caused by an
  unreachable PDS shows a different `grpc.status`, so "the policy said no" and
  "the policy service was down" are separable.
- The span appears on a permitted call too, with the identical key set; the
  decision is not in this span.
- Nothing names the guardrail processor, and nothing carries the JSON-RPC error
  code or the `mcp_error` payload the PDS returned.

A policy-denied `tools/call` SERVER span carries 27 keys: the 22 above plus five
that only a refusal carries (values are examples).

```
error                        true
error.type                   "MCP"
otel.status_code             "ERROR"
otel.status_description      "mcp: mcpGuardrails rejected: requested 6200 EUR exceeds the 6000 EUR limit for this agent at autonomy level autonomous [policy-denial rule=settlement-agent.autonomous.execute_limit limit=6000.00 requested=6200.00]"
reason                       "MCP"
```

- **`http.response.status_code` is 200, not 4xx.** ExtMCP's refusal is an MCP
  tool result marked `isError` inside a successful HTTP response (upstream PR
  #3526). Anyone alerting on HTTP status alone will not see this refusal. The
  rule, limit and requested amount reach the client only as the
  `[policy-denial ...]` tag at the end of the result text
  (`pds/deny_text.py::format_deny`).
- **`reason = "MCP"` names the layer, not the rule**; `jwtAuth` refusals read
  `"JwtAuth"`.
- **`otel.status_description` carries the PDS's reason string verbatim**, behind
  a fixed `mcp: mcpGuardrails rejected: ` prefix. It is free text, not a code.
- **Every `mcp.*` attribute is present**, including `gen_ai.tool.name`, because
  the guardrail runs after the message is parsed. An operator can tell which tool
  was refused, which an authentication refusal could not say. `jwt.sub` is
  present because the caller was authenticated, then refused.
- **There is no CLIENT span to payments-mcp** and no payments-mcp span for the
  tool call, the evidence that the execution never happened.

`tests/e2e/test_policy_denial.py` asserts the behaviour and pins the SERVER
span's child shape by exact equality on the sorted `(service, operation, kind)`
triples: `Guardrail` (CLIENT, agentgateway) and `northstar.policy.decision`
(SERVER, policy-decision-service) are siblings under it, because the gateway
propagates the context it received to the hook, not its own client span's. The
exact `otel.status_description` text is not asserted: it concatenates an
upstream prefix with this project's reason string, and each half is covered
elsewhere.

### Findings about agentgateway's telemetry

1. **`span.id`, `trace.id` and `duration` are emitted as span attributes** on the
   SERVER spans, restating the envelope, and `duration` is a formatted string
   (`"3ms"`), not a number.
2. **`mcp.session.id` changes format mid-session.** On `initialize` it is a plain
   UUID; on every later span it is a base64-encoded JSON object naming the
   target and an upstream session id (`{"t":"mcp","s":[{"t":"payments-mcp","s":"..."}]}`).
   The same key does not join the `initialize` span to the rest of its session.
3. **`host.name` on the Resource is `default.default.svc.cluster.local`**, the
   gateway's default identity string, not the pod's hostname. Cosmetic here
   (Jaeger keys on `service.name`), but it would misattribute spans in a view
   that groups by host.
4. **`jwt.sub` is not a semantic-convention name.** The authenticated principal
   has a stable spelling, `user.id`; the gateway names the attribute after the JWT
   claim it read. A query written against `user.id` finds every service here
   except the one that authenticates anything. Not in PR #3182 or any upstream
   issue.
5. **The gateway sets no `otel.scope.version`**; the version is on the Resource.
6. **A benign gain: payments-mcp's `initialize` spans join the trace.**
   `settlement-agent` injects `params._meta` only on the tool call, but the
   gateway's `stamp_trace_context` (`crates/agentgateway/src/mcp/upstream/mod.rs`,
   upstream PR #2520 for SEP-414) copies `traceparent` into the outbound `_meta`
   of every MCP message it forwards. The gateway writes SEP-414 `_meta` on egress
   but reads only HTTP headers on ingress.

### Why the trace survives the gateway

Two independent carriers, not one:

- This project's MCP trace-context convention is SEP-414: `traceparent`,
  `tracestate` and `baggage` as unprefixed keys in `params._meta`, never HTTP
  headers.
- **agentgateway does not read that.** `TraceParent::from_request`
  (`crates/agentgateway/src/telemetry/trc.rs`) reads the HTTP `traceparent`
  header and nothing else. Read alone, that predicts a fractured trace.
- The trace survives because `bootstrap.py` instruments the httpx2 client the
  MCP streamable-HTTP transport is built on, so every outbound MCP request also
  carries an auto-injected `traceparent` HTTP header
  (e.g. `00-df60a027741941a16675b35edbcf0a32-5675a787e51b87e6-03`).

So nothing here means agentgateway supports SEP-414 on ingress. A client that
carried context in `_meta` alone (a stdio transport, or a library that does not
instrument httpx) would fragment at this hop. settlement-agent is not that
client.

A related sampling property of the same header-only design
(`TraceSampler::trace_sampled`, `crates/agentgateway/src/telemetry/log.rs`): a
request arriving with a `traceparent` is sampled by default and one arriving
without is dropped. `randomSampling: true` is set so a headerless caller is
visible too, which changes nothing for settlement-agent's path.

## The policy decision span

No OpenTelemetry semantic convention exists for a policy or autonomy decision
(Gap 3), so this project defines one, in the open. The span is
`northstar.policy.decision`, SERVER, emitted by
`platform/policy-decision-service`. Its attribute keys are declared once, in
`pds/telemetry.py` (`ATTRIBUTES`), and the registry in `telemetry/registry/`
defines them for live-check.
`platform/policy-decision-service/tests/test_servicer.py::test_every_attribute_we_emit_is_declared_and_visibly_ours`
asserts the emitted set against the declared one, so the source, the guard and
this contract cannot drift into different lists. The keys are the ten
`northstar.policy.*` names in the generated tables plus one borrowed
OpenTelemetry attribute, `feature_flag.version` (present only when flagd's
response carries a version, which it does not for the `autonomy-level` flag).

An illustrative `execute_payment` decision on a limit rule (the values are examples) carries these keys:

```
otel.scope.name                       "northstar.policy-decision-service"
northstar.policy.autonomy.level       "assisted"
northstar.policy.autonomy.reason      "TARGETING_MATCH"
northstar.policy.caller.spiffe_id     "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
northstar.policy.decision             "deny"
northstar.policy.retryable            false
northstar.policy.ledger.status        "proposed"
northstar.policy.limit.amount         0
northstar.policy.request.amount       5800
northstar.policy.request.currency     "EUR"
northstar.policy.rule                 "settlement-agent.assisted.execute_limit"
northstar.policy.tool.name            "execute_payment"
```

How many keys a span carries depends on the decision, because the service writes
the optional ones only when it has a value. Absent reads as absent: a zero amount
would read as a request for nothing.

| Tool | Caller | Rule | Keys beyond the scope |
|---|---|---|---|
| `propose_payment` | settlement-agent | `unlimited_tool` | autonomy level and reason, caller, decision, retryable, request amount and currency, tool, rule (no limit, no ledger status) |
| `refuse_payment` | settlement-agent | `unlimited_tool` | the same without request amount, currency or limit: it moves nothing |
| `execute_payment` | settlement-agent | `settlement-agent.<level>.execute_limit`, then `settlement-agent.payment_not_proposed` or `settlement-agent.ledger_unreadable` | all of them, limit and ledger status included |
| `execute_payment` | reviewer-ui | `human-approved.execute` | all except `limit.amount`: the rule states no limit |

So **two or three decision spans appear per settlement**, one per tool call, and
the set is the artefact rather than any one span. A refused settlement has three
(propose permitted, execute denied, refuse permitted); a permitted one has two.
`propose_payment` and `refuse_payment` are in the PDS's `UNLIMITED_TOOLS` set
because neither moves money. `execute_payment` is the one tool a limit rule fires
against.

The policy, as `pds/rules.py` enforces it:

| Caller | Level | `execute_payment` is permitted |
|---|---|---|
| `settlement-agent` | `assisted` | never (limit EUR 0) |
| `settlement-agent` | `autonomous` | up to EUR 6,000, and only a payment the ledger shows `proposed` |
| `reviewer-ui` (in `HUMAN_APPROVERS`) | no level | only when the ledger shows the payment `awaiting_approval` (`human-approved.execute`) |
| any other caller | any | never (`unknown_caller`) |

`northstar.policy.ledger.status` is what the PDS read from payments-api for the
payment, on both kinds of execute decision: `proposed` or `awaiting_approval` on
a permit, and `executed`, `rejected`, `proposed`, `awaiting_approval` or
`unreadable` on a deny.

Properties that are easy to get wrong:

- **`northstar.policy.request.amount` is a float**, `6200` and not `"6200"`. The
  amount arrives as a JSON number inside `mcp_request` and nothing stringifies it.
- **`northstar.policy.retryable` is always `false`**, and is on the span because
  it is the field the agent keys its terminal branch off. A denial the agent could
  retry is a denial it could sit retrying.
- **`northstar.policy.caller.spiffe_id` is the caller's attested identity**, the
  same string the gateway records as `jwt.sub` one span up, handed to the PDS
  through the guardrail's `metadata: {caller: 'jwt.sub'}` CEL expression. The PDS
  validates no token itself.
- **`northstar.policy.autonomy.level` is resolved per decision** from the
  `autonomy-level` flag through `pds/autonomy.py::resolve_autonomy`, so a trace
  shows what the behaviour was keyed on. The OpenFeature `TracingHook` also puts a
  `feature_flag.evaluation` event on the decision span.
- **The span status is `Unset` on a deny.**
- **No `northstar.policy.*` attribute is set anywhere except in the PDS.**
  Nothing in settlement-agent, payments-mcp or payments-api writes one, and
  nothing rewrites agentgateway's spans to add one.
- **The decision span carries no `northstar.claim.id`.** The PDS does not run the
  baggage span processor, and agentgateway forwards only `traceparent` (plus
  `:authority` and `:path`) to the guardrail hook, never `baggage`. The decision
  is correlated to the claim by trace id.

## SPIRE

SPIRE 1.15.3 emits no spans. Its telemetry configuration accepts Prometheus,
DogStatsD, Statsd, M3 and an in-memory sink, and nothing else; there is no OTLP
exporter and no `traceparent` handling in the Workload API path. The JWT-SVID
fetch that precedes every MCP call therefore appears in traces only as latency
inside settlement-agent's `execute_tool` span.

SPIRE does emit Prometheus metrics, which the chart ships off. They are enabled
in `deploy/platform/spire/values.yaml` (`spire-server.telemetry.prometheus.enabled`
and the matching `spire-agent` key), two ClusterIP Services in
`deploy/platform/spire/metrics.yaml` expose the two ports, and the collector
scrapes them through a `prometheus/spire` receiver. The scrape's `job_name`
becomes `service.name` (`spire-server`, `spire-agent`), which puts SPIRE in the
same service vocabulary as everything else. `ScopeMetrics` names the Prometheus
receiver, because SPIRE carries no OTel SDK.

Series that show the identity mechanism, as they appear on `/metrics`:

```
spire_agent_workload_api_workload_attestor{attestor="k8s",status="OK"}
spire_agent_rpc_workload_api_fetch_jwtsvid{status="OK"}
spire_agent_jwt_svid_cache_size
spire_server_rpc_svid_v1_svid_new_jwtsvid{status="OK"}
spire_server_server_ca_sign_jwt_svid
```

`attestor="k8s"` says the identity came from the kubelet-based workload attestor
(the pod's namespace and service account), not from anything the workload
asserted. The agent caches, so a repeat fetch for the same audience is answered
locally and never reaches the server (`fetch_jwtsvid` at the agent exceeds
`new_jwtsvid` at the server), which is why `svid.py` keeps its own in-process
cache with a refresh margin. A high `workload_attestation` count against a single
digit JWT-SVID count is the chart's own OIDC discovery provider holding an
X509-SVID stream open: attestation happens per Workload API connection, not per
token. The counter values are not fixtures; the metric names and label keys are
what a dashboard or alert would be built on.

## Linkerd proxy spans

Proxy tracing is on (`deploy/platform/linkerd/values.yaml`), so the Linkerd
proxies add their own spans to the trace. The table is read from the pinned
proxy's source (linkerd2-proxy `v2.370.0`, shipped by `edge-26.10.1`:
`linkerd/trace-context/src/service.rs`, `linkerd/app/core/src/http_tracing.rs`,
`linkerd/app/src/env/trace.rs`) and the chart's templates. On a running cluster
the proxy spans reach Jaeger under `linkerd-proxy` (148 of the 313 spans in one
held claim's trace), the full gate passes with no app span orphaned once the trace
settles, and live-check reports no owned violation.

The proxies export on their own schedule, later than the SDKs' 5 s batch. A trace
read too early can show an app span whose proxy parent has not landed yet, so a
test that reads a trace waits for its span count to hold steady (or for its
orphans to resolve) before asserting on it.

| What | Value |
|---|---|
| Export | OTLP gRPC to `otel-collector.observability.svc.cluster.local:4317`, over mTLS. The proxy checks the collector's identity `otel-collector.observability.serviceaccount.identity.linkerd.cluster.local`, which is why the collector pod is meshed |
| Sampling | None of its own: a span only for a request whose incoming `traceparent` is sampled, never a new trace. The apps sample everything, so nothing is sampled away. A full export queue drops a span with an `info` log ("Span dropped"), the one loss path |
| Spans per meshed hop | Four: the caller's proxy emits an outbound SERVER and an outbound CLIENT span, the callee's proxy an inbound SERVER and an inbound CLIENT span. Each layer re-parents the request, so the callee's app SERVER span becomes a child of the inbound CLIENT proxy span. A hop with one meshed end has two |
| Span name | The request path (`/`, `/mcp`, `/payments/propose`, ...). Not a registry name, so live-check judges none of them |
| Span attributes | `direction` (`inbound`/`outbound`), `http.request.method`, `url.scheme`, `url.path`, `url.query`, `url.full`, `network.transport`, `server.address`, `server.port`, `user_agent.original`, `http.request.header.content-type`, `http.request.header.content-length`, `http.request.header.l5d-orig-proto`, `http.response.status_code`. No other header is copied (no `authorization`, no A2A push token) |
| Resource attributes | `service.name=linkerd-proxy`, `k8s.pod.ip`, `k8s.pod.uid`, `k8s.container.name`, and every label of the pod (downward API): `app`, `pod-template-hash`, the injector's `linkerd.io/*` labels and this repo's `northstar.io/plane` and `northstar.io/owner` |

An ingress refusal is an HTTP 403 (a gRPC `PERMISSION_DENIED` at the PDS) that
the destination proxy counts as
`inbound_http_authz_deny_total{srv_name, route_name, client_id}`. A refused request that
carries a sampled `traceparent` is expected to leave an inbound proxy SERVER span
with `http.response.status_code` 403; a request without one, such as the reach
probes, leaves only the counter. An egress drop (NetworkPolicy, enforced by
kindnet) is silent: nothing counts it. Every network claim in this repository
rests on an HTTP status plus that counter, or on a probe from inside a pod. No
network telemetry is asserted as a span anywhere. Linkerd's Go controllers export
tracing with the OpenCensus agent exporter, not OTLP, so their tracing stays off.

What the repo does about the proxy spans: the e2e harness (`tests/harness/jaeger.py`,
`Trace`) leaves `linkerd-proxy` spans out of `spans`, points each app span at its
nearest app ancestor, and keeps the proxy spans in `proxy_spans`, so every
propagation assertion still means "the app's context reached the next app", and
the settle loops count both (`span_count`). `telemetry/live-check.toml` lowers
`northstar.io/*` (pod labels on the proxy's resource) to information before the
owned-names override, and `tests/harness/livecheck.py` leaves them out of the
owned violations. Nothing is added to the registry: the proxy emits no owned name.

## Metrics

Five Python services install a `MeterProvider` (claims-agent, settlement-agent,
payments-mcp, reviewer-ui, policy-decision-service) and the collector's `metrics`
pipeline exports to Prometheus over OTLP. `systems/payments-api` (Go) emits spans
and no metrics, so the money path has no metric of its own. SPIRE's own metrics
reach Prometheus through the collector's scrape (see "SPIRE"). None of the twelve
GenAI metrics concerns payments-api: every one is about an agent, and no agent
runs in it.

### Where the conventions live

The GenAI semantic conventions, and the MCP conventions, live in
[`open-telemetry/semantic-conventions-genai`](https://github.com/open-telemetry/semantic-conventions-genai),
not in the main `open-telemetry/semantic-conventions` repository. The old
`docs/gen-ai/` pages in the main repository are stubs titled "Moved: Generative AI
semantic conventions" that still return 200 while saying they are no longer
maintained. HTTP, Kubernetes and the rest remain in the main repository.

That repository defines twelve GenAI metrics, **all at Development stability**: a
Development metric can be renamed upstream, so every claim here carries that
qualifier. Six are in scope for this system's agents; the other six
(`gen_ai.client.operation.time_to_first_chunk`,
`gen_ai.client.operation.time_per_output_chunk`, `gen_ai.server.request.duration`,
`gen_ai.server.time_per_output_token`, `gen_ai.server.time_to_first_token`,
`gen_ai.invoke_workflow.duration`) are not asked about.

### The six agent metrics

| Metric (OTel name) | Type | Unit | Emitted? | Verified at |
|---|---|---|---|---|
| `gen_ai.invoke_agent.duration` | Histogram | `s` | **No** | SDK: created by nothing |
| `gen_ai.invoke_agent.inference_calls` | Histogram | `{inference_call}` | **No** | SDK: created by nothing |
| `gen_ai.invoke_agent.tool_calls` | Histogram | `{tool_call}` | **No** | SDK: created by nothing |
| `gen_ai.execute_tool.duration` | Histogram | `s` | **No** | SDK: created by nothing |
| `gen_ai.client.token.usage` | Histogram | `{token}` | **Yes** | Prometheus: series present |
| `gen_ai.client.operation.duration` | Histogram | `s` | **No** | SDK: created by nothing |

One of six. "Created by nothing" is the strongest available claim: the metric is
never brought into existence in any process, so no reader, exporter, collector
pipeline or Prometheus receiver is a candidate explanation. None of those five
names appears anywhere in `pydantic-ai-slim` 2.42.0, `a2a-sdk` 0.3.7, `fastmcp`
4.0.3 or `mcp` 2.2.0, and the last three contain no `get_meter(` call at all.
`docs/upstream-findings.md` Finding 12 has the full sweep and the argument that
this is a framework gap and not a scope boundary.

### What PydanticAI creates

Constructing this project's own `InstrumentationSettings` inside the deployed pod
and enumerating the instruments it builds gives three:

| Instrument | Unit | In the conventions? | Reaches Prometheus? |
|---|---|---|---|
| `gen_ai.client.token.usage` | `{token}` | Yes (Development) | **Yes** |
| `gen_ai.client.operation.time_to_first_chunk` | `s` | Yes (Development) | **No**, and nothing is broken |
| `operation.cost` | `{USD}` | **No**: PydanticAI's own name, in no OTel namespace | **Yes** |

`gen_ai.client.operation.time_to_first_chunk` is created and never recorded into.
`record_metrics` guards it with `if time_to_first_chunk is not None`
(`pydantic_ai/models/instrumented.py`), and that value exists only for a
streaming request. This demo streams nothing, so the SDK exports no data point
for it. Created is not emitted, and a dashboard built on it would be permanently
empty.

`operation.cost` carries the model call's monetary cost, the same figure as the
`chat` span's `operation.cost` attribute. It is a real series and a non-convention
name: a reader building on it should know it is PydanticAI's invention, with none
of a convention's stability promise.

### `gen_ai.client.token.usage` as it arrives

Prometheus's default OTLP translation strategy is `UnderscoreEscapingWithSuffixes`,
left at its default here, so a dotted OTel name becomes underscores plus a
histogram suffix and dotted attributes become underscored labels. The OTel name is
what a convention claim is about; the Prometheus name is what a query uses.

| OTel name | Prometheus series |
|---|---|
| `gen_ai.client.token.usage` | `gen_ai_client_token_usage_bucket`, `_sum`, `_count` |
| `operation.cost` | `operation_cost_bucket`, `_sum`, `_count` |
| `northstar.approval.wait.duration` | `northstar_approval_wait_duration_seconds_bucket`, `_sum`, `_count` |

One series, as stored: `gen_ai_client_token_usage_sum` with labels
`gen_ai_operation_name="chat"`, `gen_ai_provider_name="anthropic"`,
`gen_ai_request_model`, `gen_ai_response_model`, `gen_ai_system="anthropic"`,
`gen_ai_token_type="input"` (or `output`) and `job="settlement-agent"`.
`gen_ai_system` is the deprecated spelling of `gen_ai_provider_name` and both
appear, as on the `chat` span.

### Which services emit anything

A "no such metric" result is worthless unless the path it would have taken is known
to work, so every absence is paired with a presence from the same window. Per
`job` (Prometheus's rendering of `service.name`):

| `job` | What proves its path works |
|---|---|
| `spire-server`, `spire-agent` | Collector-scraped and pushed over OTLP, with no Python service involved: proves the collector-to-Prometheus hop on its own |
| `settlement-agent` | `gen_ai_client_token_usage_*` and `operation_cost_*` from real model calls |
| `claims-agent` | `http_server_*` and `http_client_*` from the ASGI and httpx instrumentations |
| `payments-mcp` | `http_client_request_duration_seconds_*` on every call to payments-api |
| `reviewer-ui` | `http_client_duration_milliseconds_*` on every ledger read, and `northstar_approval_wait_duration_*` |
| `policy-decision-service` | Records no metric of its own; its export path was proven once by sending a probe counter |

payments-mcp's row decides `gen_ai.execute_tool.duration`: tool execution happens
in that process, under FastMCP, so that is where the metric would be recorded. Its
MeterProvider demonstrably gets other metrics to Prometheus in the same window,
which makes the absence FastMCP's.

The probe for the services that record nothing was sent by hand once and is
deliberately not a standing test: a suite that otherwise only reads the cluster
would have to write to it on every run to re-prove a property the source already
states and `tests/unit/test_metrics_wiring.py` guards. If it needs re-establishing,
re-send the probe; do not read an expired series as an absence.

### `northstar.approval.wait.duration`

One histogram, created in `apps/reviewer-ui/approval_metrics.py` and recorded by
`review.decide()` after a release or reject took effect. A failed execute records
nothing. The value is the span's own `northstar.review.wait.duration`, in seconds,
so metric and span cannot disagree: both read the ledger's `awaitingSince`.

| Field | Value |
|---|---|
| Instrument | `northstar.approval.wait.duration`, histogram, unit `s` |
| Attributes | `northstar.review.decision` (`approve` or `reject`), `northstar.policy.autonomy.level` (`assisted`, `autonomous` or `unknown`, read off the refusal's rule) |
| Deliberately absent | the reviewer's id: one series per person would be high cardinality |
| Aggregation | the SDK default (explicit bucket histogram); `bootstrap.py` installs the provider with no views and nothing is sampled |

In Prometheus it is the three `northstar_approval_wait_duration_seconds_*` names,
with labels `job="reviewer-ui"`, `northstar_review_decision` and
`northstar_policy_autonomy_level`, and the default buckets
(`le` = 0, 5, 10, 25, 50, 75, 100, 250, 500, 750, 1000, 2500, 5000, 7500, 10000,
+Inf). `tests/e2e/test_human_in_the_loop.py::test_the_histogram_has_a_sample_for_the_approval`
asserts the `_count` series.

### Gaps in this project's own configuration

- **reviewer-ui emits the legacy HTTP metric name**, because its Deployment sets no
  `OTEL_SEMCONV_STABILITY_OPT_IN` (see "One HTTP convention").
- **payments-api has no `MeterProvider`.**

### How this is kept true

`tests/e2e/test_prometheus_metrics_ingest.py` re-verifies the inventory against
the live cluster and is written so a false negative cannot pass quietly: every
absence assertion is paired with a positive control in the same test, the controls
are bounded to a recent window (an unbounded check would stay green for
Prometheus's 15-day retention after everything stopped emitting), a test fails if
any `gen_ai` name turns up that the file does not account for, and an unreachable
cluster fails rather than skips. If a framework implements one of the five, that
file goes red and the table above is what has to change.

`tests/unit/test_metrics_wiring.py` covers what the cluster cannot show: that all
five services install the provider, that the copies of the wiring have not
drifted, that nothing configures a view or temporality that would discard data,
that the chart pin and the collector's endpoint agree, that no `deploy/k8s/`
manifest changes the temporality, export interval or exporter from outside the
measured Python, and that nothing under `agents/`, `apps/`, `platform/`, `tools/`
or `systems/` authors an instrument under a namespace an instrumentation library
owns. The last check is the only layer that can see who wrote a metric at all.

## Spans owned by the human path

### reviewer-ui: `northstar.review.decision` and `northstar.review.queue`

`apps/reviewer-ui/review.py`'s `decide()` creates `northstar.review.decision`
(SERVER) around a reviewer's approve or reject. reviewer-ui applies no ASGI
instrumentation, so this span is the root of trace B and there is no framework
entry span for it to nest in. It opens first and reads the ledger inside it, so
a stale link or a race with another reviewer produces a span ending ERROR with
the exception on it, carrying the four attributes known before the read and not
those read off the record. Its attributes (see the generated tables):
`northstar.review.payment.id`, `northstar.review.decision`,
`northstar.review.reviewer.id`, `northstar.review.reviewer.spiffe_id`,
`northstar.review.wait.duration` and `northstar.claim.id`, plus
`northstar.review.resume_only` when the decision was already on the ledger.

- Values come from the ledger record `decide()` fetches at decision time, except
  `northstar.review.decision` (the reviewer's choice) and
  `northstar.review.reviewer.id` (the name typed into the form: a claim carried on
  the span, not a verified credential).
- `northstar.review.reviewer.spiffe_id` is reviewer-ui's own workload identity,
  read from the SPIFFE Workload API (`apps/reviewer-ui/identity.py`), not assembled
  from the namespace and ServiceAccount strings the process merely believes it was
  deployed with.
- `northstar.review.wait.duration` is computed from the ledger's `refusedAt`/
  `awaitingSince`, never from a timer started when the listing page loaded:
  `_elapsed_ms` takes no argument from the HTTP request, so no browser tab's dwell
  time can reach it. It is in seconds.
- The span carries one span link to the refusal span in trace A (see "The trace"),
  and the claim id is also baggage for the gateway call and the resume.

`northstar.review.queue` (SERVER, one per `GET /`) wraps the reviewer's queue
page. It carries no attributes: every figure on the page is a ledger record, and
payments-api's own `GET /payments` span, now in the same trace, describes the
read. Without a span current, each ledger read's httpx CLIENT span would root a
trace of its own.

**reviewer-ui trace context reaches payments-api.** reviewer-ui's outbound
`traceparent` carries a Level 2 flag byte (`-03`: sampled plus the "random trace
id" bit), because `opentelemetry-sdk` 1.44 sets it. payments-api runs
`go.opentelemetry.io/otel` v1.46.0, whose extractor accepts it (v1.41.0 refused a
version-00 header with a flag byte above `0x02` and started a fresh root).
reviewer-ui registers the stock `TraceContextTextMapPropagator`; nothing masks the
flag byte. payments-mcp, on the same SDK, forwards whatever flag byte it receives.
### claims-agent: A2A task state and the resume

claims-agent sets owned attributes on spans in its executor:
`northstar.a2a.task.id` (the A2A task the customer streams) and
`northstar.a2a.task.state` (`input-required` on a waiting claim) on the span
current while the executor runs, and `northstar.a2a.parked_reason`
(`awaiting_approval` or `needs_information`) on the span that parks the task and
on the `northstar.a2a.resume` span that resumes it. The resume span links back to
the parking span with `northstar.a2a.link.reason = resume` in both cases. The
a2a-sdk's own spans add no `rpc.*` or `gen_ai.*` attributes: they carry
`otel.scope.name` `a2a-python-sdk` and, on success, `otel.status_code` `OK`.

A free-text claim can park for a question to the customer
(`needs_information`). The customer's answer arrives in the browser's own root
span, `northstar.claim.answer`, linked to the round that asked.

### customer-ui: the wait

Only the browser emits `northstar.claim.wait`, so it is asserted in
`tests/e2e/test_policy_denial.py`'s browser run. customer-ui opens it when the
stream returns `input-required` and ends it when the outcome arrives over
`GET /claims/events` after the A2A push. It is its own trace, linked once to the
`northstar.claim.submit` trace (or to the answer span of a free-text claim), and
carries `northstar.claim.id`. The test asserts the wait is at least as long as the
time it measured between the page showing "Awaiting human approval" and the
approval, less one second.

### The autonomy card's span

`platform/backstage/packages/backend/src/plugins/autonomyContract/telemetry.ts`'s
`emitContractChangeSpan` is called once from `router.ts`'s `POST /variant` handler
after the FeatureFlag patch that flips the contract has succeeded. The span,
`northstar.autonomy_contract.change`, is one per successful write through the
autonomy card, not per read and not for a request with an invalid `variant`
(rejected 400 before the Kubernetes client is called). It is one of three
independent signals for "the write must be auditable and distinguishable from a
`kubectl` edit", and the one that outlives the others: the object's
`metadata.managedFields` entry is overwritten by the next write from any client,
and Backstage's `coreServices.auditor` event lives only in one pod's logs. The span
survives both.

The plugin builds and `POST`s an OTLP/HTTP JSON `resourceSpans` payload to
`${OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces` itself instead of using the Node OTel
SDK: it emits a few spans per process lifetime, so there is no batching or
sampling case for a tracer-provider lifecycle. `service.name` is
`OTEL_SERVICE_NAME`, unset in this deployment, so it falls back to `backstage`.
`emitContractChangeSpan` never throws (a telemetry outage must not block a
contract write that already landed), but it returns whether the `POST` was
attempted and whether the collector answered 2xx, and `router.ts` logs a failure
loudly. There is no local queue and no retry: a collector outage at the moment of
the write drops that span, reported and not hidden.

Attributes: `northstar.contract.previous_variant` (read off the live object
immediately before) and `northstar.contract.new_variant` (the validated request),
`northstar.contract.field_manager` (the same string the patch sets as its
Server-Side Apply field manager, so the span can be matched against the object's
`managedFields` while both still agree), `northstar.contract.flag.name` (the flag
resource's name, `AUTONOMY_NAME`; the registry avoids `k8s.resource.name` because
that name carries a Kubernetes meaning this value lacks), `k8s.namespace.name`
(a semantic-convention attribute, reused) and `northstar.contract.actor`. The
actor is a fixed literal, `backstage-autonomy-card`, not a caller identity:
Backstage's guest auth means every write through this UI is indistinguishable from
every other, and there is no signed user upstream of the route for the span to
carry honestly.

## Conventions adopted

- **GenAI semconv** (`open-telemetry/semantic-conventions-genai`) for model calls:
  see "Model calls" for the attribute inventory and "Metrics" for which of its
  twelve metrics anything emits (one).
- **MCP semconv** (`mcp.method.name`, `mcp.session.id`, `mcp.protocol.version`),
  with tool names in `gen_ai.tool.name`. `mcp.tool.name` does not exist. Also in
  `semantic-conventions-genai` (`docs/gen-ai/mcp.md`); the main repository has no
  `docs/mcp/`.
- **HTTP semconv** for the Go system of record and A2A's transport spans (the main
  repository). See "One HTTP convention" for the two components that do not follow
  it.
- **MCP trace context via `params._meta`** (SEP-414), keys unprefixed and never HTTP
  headers, plus the auto-injected HTTP `traceparent` header (see "Why the trace
  survives the gateway").

## Domain attributes we own

The registry in `telemetry/registry/`, rendered in the generated tables below, is
the inventory. This table says where each family is set and how far it travels.

| Attribute | Set by | Propagation | Applies to |
|---|---|---|---|
| `northstar.claim.id` | claims-agent at intake; the browser on its own root spans; reviewer-ui where it acts on a claim | W3C baggage | Not every span everywhere; see "Claim id coverage" |
| `northstar.claim.policy_number` | customer-ui `northstar.claim.submit` span | none | The root span only |
| `northstar.policy.*` (ten keys), plus the borrowed `feature_flag.version` | policy-decision-service, on `northstar.policy.decision` | none: set locally | That span only |
| `northstar.payment.refusal_cause` | payments-api, on its execute span, when it refuses with 409 (`amount_mismatch`, `already_executed`, `rejected`) | none | Absent on success, so its presence means "refused here"; asserted by payments-api's own Go tests via an in-memory span recorder |
| `northstar.review.*` | reviewer-ui, on `northstar.review.decision` | none | That span only; `northstar.review.queue` carries none |
| `northstar.a2a.*` | claims-agent, on its executor spans and the resume span | none | Those spans; `parked_span_context` and `parked_reply` are task metadata, not attributes |
| `northstar.approval.wait.duration` (histogram) | reviewer-ui | none (a metric) | One sample per decision that took effect; no reviewer id |
| `northstar.contract.*` | Backstage autonomy card, on `northstar.autonomy_contract.change` | none | That span only; `actor` is a fixed literal |

### How `northstar.claim.id` reaches spans

claims-agent, settlement-agent and payments-mcp each build upstream's
`BaggageSpanProcessor` (`opentelemetry-processor-baggage==0.65b0`) in
`correlation.py`, with a predicate that admits `northstar.claim.id` and no other
key (`correlation.is_claim_id`), and `bootstrap.configure()` adds it to the tracer
provider. On span start it reads the parent context's baggage, so a span gets the
id only if baggage already carries it in that process. The predicate is
deliberate: `ALLOW_ALL_BAGGAGE_KEYS` would copy any entry an upstream caller put
in baggage onto every span and export it. Each service's
`tests/test_claim_id_processor.py` pins that the processor is upstream's, that its
predicate admits only the claim id, that a second baggage key does not reach the
span, and that `configure()` adds it.

| Service | What copies `northstar.claim.id` onto its spans |
|---|---|
| claims-agent | `BaggageSpanProcessor(is_claim_id)`; also sets the baggage at intake (`set_claim_id`) |
| settlement-agent | `BaggageSpanProcessor(is_claim_id)` |
| payments-mcp | `BaggageSpanProcessor(is_claim_id)`; finds nothing in practice (Gap 4) |
| payments-api | `withClaimID` middleware (`handlers.go`), the Go counterpart, since Go's SDK has no baggage span processor; dormant on the money hops for the same reason (Gap 4) |
| reviewer-ui | its own spans set the attribute where it acts on a claim (`review.py`); no baggage processor |
| PDS, agentgateway, Linkerd proxies | none: correlated to the claim by trace id |

**Claim id coverage.** The correct reading of coverage is by mechanism, not by
span count, because counts move with the a2a-sdk's polling spans. The processor
copies the id only if baggage is already in the ambient context when the span
starts; it never reaches outside the process to fetch it.

- **customer-ui**: the browser sets the id explicitly as its own attribute at span
  creation. The id is minted at claims-agent's intake, after the browser span
  exists, so nothing is propagated into it.
- **claims-agent**: only the spans created inside the executor's coroutine after the
  baggage attaches carry the id (the outbound A2A CLIENT span and the final
  `EventQueue.enqueue_event`). The ASGI entry span and every `a2a.server.*` span
  run in a separate asyncio task that a2a-sdk 0.3.7 spawns with
  `asyncio.create_task()` before the executor has attached anything, and
  `create_task` copies the context at call time. This is not a bug in this
  project's code and it is not patched around: those spans are third-party
  telemetry.
- **settlement-agent**: receives the id in an inbound baggage header, extracted by
  `StarletteInstrumentor` at the ASGI boundary before any internal task spawns, so
  every span carries it.
- **payments-mcp and payments-api**: the id never arrives on the money hops (Gap 4).
  The ledger reads do carry it: claims-agent resumes a waiting claim with the id in
  baggage and its `GET /payments/{paymentId}` reaches payments-api with it.
  `POST /payments/propose` and `POST /payments/{paymentId}/execute` carry none.

`tests/e2e/test_claim_correlation.py` asserts this boundary precisely, positively
for customer-ui, claims-agent's executor spans and settlement-agent, and by the
narrow manual xfail for payments-mcp and payments-api.

**Enrichment boundary.** The baggage span processor runs only in this project's
own service processes. Nothing is injected into telemetry from infrastructure it
does not run in-process, so agentgateway's spans, the PDS's and the Linkerd
proxies' carry no `northstar.claim.id` and are correlated by trace id. That is
asserted:
`tests/e2e/test_gateway_trace.py::test_the_gateway_carries_no_northstar_claim_id`
fails if enrichment of third-party infrastructure telemetry ever starts (and it
asserts gateway spans exist first, so it cannot pass vacuously).

## Registry-defined attributes, spans and metrics (generated)

The attributes, spans and the one metric below are generated from the
OpenTelemetry Weaver registry in `telemetry/registry/`, the checked definition of
every name this project owns. Every owned name starts with `northstar.`. Do not
edit between the markers: `scripts/check-registry.sh` regenerates the region and
fails when the committed text differs
(`python3 scripts/weaver_docs_region.py --write docs/telemetry-contract.md .demo/weaver-docs`
rewrites it). Where a hand-written section and the generated tables disagree, the
registry wins.

<!-- BEGIN weaver-generated -->

### `northstar.claim`

The insurance claim a trace is about, and the A2A task that carries it.

| Attribute | Type | Stability | Description | Examples / values |
|---|---|---|---|---|
| `northstar.a2a.link.reason` | enum | development | Why this span links to another trace. A link attribute, not a span attribute. | `resume` |
| `northstar.a2a.parked_reason` | enum | development | Why claims-agent parked the task at input-required, on the span that parked it and on the span that resumed it. | `awaiting_approval`, `needs_information` |
| `northstar.a2a.task.id` | string | development | The A2A task id of a claim being processed by claims-agent. | `f2d33c09-7ffb-406b-8491-7fc94b9c6693` |
| `northstar.a2a.task.state` | enum | development | The A2A task state claims-agent reported on its span. | `failed`, `completed`, `input-required` |
| `northstar.claim.id` | string | development | The claim a span belongs to. Travels as W3C baggage; the span processors copy it onto spans. | `CLM-1842` |
| `northstar.claim.policy_number` | string | development | The insurance policy number the claim is made under (not the authorization policy). | `POL-77120` |
| `northstar.payment.refusal_cause` | enum | development | Why payments-api refused a request with 409. Absent on a request it did not refuse. | `amount_mismatch`, `already_executed`, `rejected` |

### `northstar.contract`

A change to the autonomy contract, made through Backstage.

| Attribute | Type | Stability | Description | Examples / values |
|---|---|---|---|---|
| `northstar.contract.actor` | string | development | Who made the change. | `backstage-autonomy-card` |
| `northstar.contract.field_manager` | string | development | The Kubernetes field manager the change was applied as. | `backstage-autonomy-card` |
| `northstar.contract.flag.name` | string | development | The name of the flag resource the change was made to. The k8s semantic conventions have no generic resource-name attribute. | `northstar-autonomy` |
| `northstar.contract.new_variant` | enum | development | The autonomy variant after the change. | `assisted`, `autonomous` |
| `northstar.contract.previous_variant` | enum | development | The autonomy variant before the change. | `assisted`, `autonomous` |

### `northstar.policy`

A policy decision the platform made about one MCP tools/call.

| Attribute | Type | Stability | Description | Examples / values |
|---|---|---|---|---|
| `northstar.policy.autonomy.level` | enum | development | The autonomy level in effect, resolved from the autonomy-contract flag at decision time. | `assisted`, `autonomous`, `unknown` |
| `northstar.policy.autonomy.reason` | string | development | The OpenFeature reason the autonomy level came back with. | `TARGETING_MATCH`, `STATIC`, `DEFAULT`, `ERROR` |
| `northstar.policy.caller.spiffe_id` | string | development | The SPIFFE ID of the workload the decision was made about. | `spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent` |
| `northstar.policy.decision` | enum | development | What the policy-decision-service decided. | `permit`, `deny` |
| `northstar.policy.ledger.status` | enum | development | What the ledger showed for the payment an execute_payment call asked about. | `proposed`, `awaiting_approval`, `executed`, `rejected`, `unreadable` |
| `northstar.policy.limit.amount` | double | development | The limit in EUR the rule applied. Absent when the rule has no limit. | `6000.0` |
| `northstar.policy.request.amount` | double | development | The payment amount the tool call asked for. | `6800.0` |
| `northstar.policy.request.currency` | string | development | The currency of the requested amount. | `EUR` |
| `northstar.policy.retryable` | boolean | development | Whether the caller may retry the same call and expect a different answer. |  |
| `northstar.policy.rule` | string | development | The rule that decided, e.g. settlement-agent.assisted.execute_limit. | `settlement-agent.assisted.execute_limit`, `human-approved.execute` |
| `northstar.policy.tool.name` | string | development | The MCP tool the call asked for. gen_ai.tool.name moved out of the core registry in semantic-conventions v1.44.0, so it cannot be referenced. | `propose_payment`, `execute_payment` |

### `northstar.review`

A human's decision about one payment that policy refused to let an agent execute.

| Attribute | Type | Stability | Description | Examples / values |
|---|---|---|---|---|
| `northstar.review.decision` | enum | development | What the human reviewer decided. | `approve`, `reject` |
| `northstar.review.payment.id` | string | development | The ledger id of the payment under review. | `PAY-000043` |
| `northstar.review.resume_only` | boolean | development | True when the decision was already on the ledger and the call only re-sent the resume to the claim. |  |
| `northstar.review.reviewer.id` | string | development | The id the reviewer signed the decision with. | `kasper.nissen` |
| `northstar.review.reviewer.spiffe_id` | string | development | The SPIFFE ID of the reviewer-ui workload that took the decision. Absent when the workload cannot prove its identity. | `spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui` |
| `northstar.review.wait.duration` | double | development | Seconds from the payment awaiting approval to the human's decision. Absent on a resume-only call. | `10.931` |



### Spans

| Span | Kind | Brief | Attributes (requirement level) |
|---|---|---|---|
| `northstar.a2a.resume` | internal | The resume of a claim that waited, either for a reviewer's decision or for the customer's answer to a question. Runs in that person's trace and carries one link back to the span that parked the claim, with northstar.a2a.link.reason on the link. | `northstar.a2a.parked_reason` (recommended); `northstar.a2a.task.id` (recommended); `northstar.a2a.task.state` (recommended); `northstar.claim.id` (recommended) |
| `northstar.autonomy_contract.change` | internal | One flip of the autonomy contract. Starts and ends in the same instant. | `k8s.namespace.name` (recommended); `northstar.contract.actor` (required); `northstar.contract.field_manager` (required); `northstar.contract.flag.name` (recommended); `northstar.contract.new_variant` (required); `northstar.contract.previous_variant` (required) |
| `northstar.claim.answer` | internal | The customer answers claims-agent's question about a free-text claim, on the same A2A task. Its own trace, linked to the span of the round that asked. | `northstar.claim.id` (required) |
| `northstar.claim.submit` | internal | The customer submits a claim. Root of the claim's first trace. A claim written as free text gets its id from claims-agent, so the id is set once the first reply names it, and the policy number only if the customer gave one. | `northstar.claim.id` (required); `northstar.claim.policy_number` (recommended) |
| `northstar.claim.wait` | internal | The customer waits for a human decision. Its own trace, linked to the span that sent the claim to review (the submit span, or the answer span of a free-text claim). | `northstar.claim.id` (required) |
| `northstar.policy.decision` | server | One decision, a child of the gateway's tools/call span. | `feature_flag.version` (recommended); `northstar.policy.autonomy.level` (recommended); `northstar.policy.autonomy.reason` (recommended); `northstar.policy.caller.spiffe_id` (recommended); `northstar.policy.decision` (required); `northstar.policy.ledger.status` (conditionally required: when the call is an execute_payment by a caller a rule knows); `northstar.policy.limit.amount` (conditionally required: when the rule has a limit); `northstar.policy.request.amount` (recommended); `northstar.policy.request.currency` (recommended); `northstar.policy.retryable` (recommended); `northstar.policy.rule` (required); `northstar.policy.tool.name` (recommended) |
| `northstar.review.decision` | server | One human decision. Links back to the span where policy refused the payment. | `northstar.claim.id` (recommended); `northstar.review.decision` (required); `northstar.review.payment.id` (required); `northstar.review.resume_only` (conditionally required: when the decision was already on the ledger); `northstar.review.reviewer.id` (required); `northstar.review.reviewer.spiffe_id` (recommended); `northstar.review.wait.duration` (conditionally required: when money moves) |
| `northstar.review.queue` | server | The reviewer's queue of payments awaiting a decision. Carries no attributes of its own. |  |


### Metrics

| Metric | Instrument | Unit | Brief | Attributes (requirement level) |
|---|---|---|---|---|
| `northstar.approval.wait.duration` | histogram | `s` | How long a human took, from the payment awaiting approval to the decision. | `northstar.policy.autonomy.level` (required); `northstar.review.decision` (required) |

<!-- END weaver-generated -->

### What live-check checks, and what it does not

Weaver live-check (`otel/weaver:v0.28.0`, in-cluster, fed a copy of every signal by
the collector's `otlp_grpc/weaver` exporter; policy in `telemetry/live-check.toml`)
judges the names this project owns against the registry. Its limits are listed so
a green gate is read for what it says.

* **A missing required attribute is not caught.** With a v1 registry, live-check
  v0.28.0 does not report `required_attribute_not_present`: a mutation that dropped
  `northstar.policy.rule` (`required` on `northstar.policy.decision`) from the
  decision span survived. What pins the required attributes are the behaviour
  tests, which read them off real spans: `tests/e2e/test_policy_denial.py` (the
  decision span's `northstar.policy.decision`, `northstar.policy.rule`,
  `northstar.policy.limit.amount` and `northstar.policy.request.amount`),
  `tests/e2e/test_human_in_the_loop.py` (the review span is found by
  `northstar.review.reviewer.id`, trace B's decision carries
  `northstar.policy.rule` and `northstar.policy.ledger.status`, and the histogram is
  asserted by `northstar_review_decision`), and `tests/e2e/test_demo_flow.py` (the
  evidence step reads rule, limit, autonomy level, caller, reviewer id and
  `northstar.review.wait.duration` back out of Jaeger). The
  `finding_level_overrides` entry for `required_attribute_not_present` in
  `telemetry/live-check.toml` exists so a Weaver release that starts checking it is
  not silently lowered.
* **Span names are not judged by live-check, so a test checks them.** Live-check
  does not compare a span's name with the registry, so a service that went back to
  an un-prefixed span name raises no finding. `tests/e2e/test_registry_live_check.py`
  fails on any span name in the report that starts with a retired prefix
  (`livecheck.legacy_span_names`) and pins `northstar.policy.decision` and
  `northstar.review.decision` as seen. A misspelled `northstar.claim.submit`,
  `northstar.claim.wait` or `northstar.autonomy_contract.change` is still not
  caught.
* **Memory grows without bound while it runs, and the semconv fetch is not
  cached.** Live-check keeps every sample it receives: on an idle demo cluster
  (about 490 metric points and 60 spans a minute) the container grew about 3.8 MB a
  minute, 220 MiB an hour, from 13 MiB at start. The limit is 4Gi (about 17 hours)
  and the pod is recycled at every `scripts/setup-kind.sh` run and every gate run.
  Every weaver start also clones the semconv v1.44.0 dependency from github.com into
  a temporary directory, so it is fetched again on every start and needs egress.
* **Standard names are not validated, only ours.** Semantic-convention names
  (`gen_ai.*`, `http.*`, `k8s.*`) appear in the report as "does not exist in the
  registry" at `information` level. That is the policy working as intended.
* **Operating it.** Re-running the live-check test alone needs a `restart()` of the
  pod and fresh traffic first, or it fails with "live-check received nothing". The
  collector-log loss guards go red if they run within about 60 seconds after a
  manual `report()`, because reading the report restarts the weaver pod and the
  collector's exporter retries in a burst. The gate's order
  (`scripts/smoke-test.sh`: restart, the rest of `tests/`, then the live-check file
  on its own) avoids both.
* **`gen_ai.tool.name` cannot be referenced by the registry.** Semantic conventions
  v1.44.0 excludes `gen_ai.*` from dependency resolution, so the policy span's tool
  name is the owned `northstar.policy.tool.name` and not a reference to the
  standard attribute.

## Content capture

Capture is **on by default**. With it off, a span still proves that EUR 6,200 was
executed and by which tool, but not why that amount, on what stated grounds, or
what the model was told about the claim. Redaction is a trade, not a free safety
win, and the demo shows the trade: `NORTHSTAR_CAPTURE_CONTENT`
(`capture_content_enabled()` in `agents/settlement-agent/bootstrap.py`, read at
`configure()` and handed to PydanticAI's `InstrumentationSettings(include_content=...)`)
is the single switch, an environment variable and not a constant, deployed at
`"true"` in `deploy/k8s/settlement-agent.yaml` and meant to be flipped deliberately
and visibly. Unset defaults to on; `"false"`, `"0"`, `"no"` and `"off"`
(case-insensitive) turn it off; anything else is on.

**claims-agent reads the same switch** (its own `bootstrap.py`, deployed at
`"true"` in `deploy/k8s/claims-agent.yaml`). What it captures differs in kind:
settlement-agent's prompt holds a claim id, an amount and a currency, while
claims-agent's intake prompt holds the customer's own words, every message sent on
the task, and its output holds the extracted policy number, amount, date and
description. A free-text claim is the one place where a customer's free text lands
in span attributes (`gen_ai.input.messages`, `gen_ai.output.messages` on the intake
`chat` span). A JSON claim never reaches the model and is captured nowhere.

**What each setting gates:**

- `gen_ai.input.messages`, `gen_ai.output.messages` and the `invoke_agent` span's
  `pydantic_ai.all_messages` always carry message shape (`role`, `parts[].type`, and
  on a `tool_call` part `id` and `name`). With capture off, no `text`/`thinking`
  part carries a `content` key and no `tool_call` part carries `arguments`.
  Source (`pydantic_ai/messages.py`, `_tool_call_otel_part`):
  `if settings.include_content and part.args is not None: call_part['arguments'] = ...`.
  `tool_call` parts never carry `content` at either setting, so a chat turn that is
  purely a tool call has no content-bearing part regardless of the setting.
- `gen_ai.tool.call.arguments` and `gen_ai.tool.call.result` are set on the
  `execute_tool <name>` span (`pydantic_ai/capabilities/instrumentation.py`,
  `_tool_span_attributes`) and gated by `include_content`. On, the
  `execute_tool propose_payment` span carries the arguments the model chose (for
  example `{"amount":6200,"currency":"EUR"}`), the literal answer to "why that
  amount", and the result carries the ledger's record back (for example
  `{"paymentId":"PAY-000104","claimId":"...","amount":6200,"currency":"EUR","status":"proposed"}`).
  Pinned by `test_tool_call_arguments_carries_the_settled_amount` (on-mode only; the
  off-mode absence follows from the source-level gating).
- `gen_ai.system_instructions` is set on every `chat` span and the `invoke_agent`
  span (`system_instructions_attributes()` in `pydantic_ai/models/instrumented.py`)
  and gated by `include_content`.
- `model_request_parameters` (no `gen_ai.` prefix) is gated by the separate
  `include_model_request_parameters`, deliberately left at `True` regardless: it
  carries the agent's static instructions and the full tool JSON schemas,
  byte-identical across turns, with no claim-specific data. It is an audit asset,
  not a redaction target, and it is the floor of what survives full redaction.
- `gen_ai.tool.definitions` is emitted on every `chat` span unconditionally.

**What the platform's audit trail does not depend on.** With capture off, the
`gen_ai.*` layer shrinks from 28 distinct keys to 25 and the content inside
`gen_ai.input.messages`, `gen_ai.output.messages` and `pydantic_ai.all_messages` is
gone, but none of the platform-emitted evidence changes. Every field
`scripts/demo evidence` aggregates (`northstar.policy.*`, `northstar.review.*`,
`northstar.claim.id`, trace ids, span names, service names) is emitted by the
platform (the PDS, reviewer-ui, payments-api) and none is gated on
`include_content`. The governance story (who decided, under which rule and limit,
who approved, how long the human took, where the money went) is reconstructed from
what the platform decided, not from what the model said, and survives full
redaction intact.

What capture off does lose is the model's own account of itself:

1. **Independent corroboration of the requested amount.**
   `gen_ai.tool.call.arguments` is absent, so the PDS's own record of the request
   is the only account of what was asked for, and nothing in telemetry can
   contradict it. This is an audit property, not a debugging convenience.
2. **What the agent was told about this claim.** `gen_ai.system_instructions` is
   absent; `model_request_parameters` survives and says what the agent was told it
   could do in general.
3. **What the ledger said back.** `gen_ai.tool.call.result` is absent.

Message content in the surviving `gen_ai.input.messages` and
`gen_ai.output.messages` goes with it, and `pydantic_ai.all_messages` shrinks to
shape only (about 1.6 kB to about 0.7 kB in one measured run). The reviewer page's
view of "what the agent said" is independent of capture: it reads the model's
narrative from payments-api's ledger (stored by `settlement.py`'s refused branch
and fetched by `apps/reviewer-ui/ledger.py`), not from telemetry.

Whether a regulated insurer should run capture on is its owner's decision, and the
default stays on because on this demo "why that amount" is the question being
shown. Redaction can also be applied in the collector, per attribute and after the
span is emitted (an `attributes/redact` processor with `action: hash`), so the SDK
keeps emitting everything and the backend never sees the sensitive field; this
repository does not build that.

## Confirmed convention gaps

For each: the evidence, the upstream state, and what this project does.

### Gap 1: agent, task and context identity on A2A spans, and no inbound trace-context extraction

- **Evidence.** `a2a-sdk` 0.3.7's own spans, server-side and client-side, carry no
  attributes beyond OTel scope bookkeeping (`otel.scope.name`, `otel.scope.version`,
  `otel.status_code`): no `gen_ai.operation.name`, no `gen_ai.conversation.id`, no
  `contextId` or `taskId`, no agent name, not even HTTP attributes on the entry
  point. Separately it extracts no inbound W3C trace context: an injected
  `traceparent` is ignored and every request starts a fresh root. This project fixes
  extraction with real OTel framework instrumentation
  (`opentelemetry-instrumentation-starlette` wrapping the built ASGI app); the
  attribute gap remains open.
- **Upstream.** `open-telemetry/semantic-conventions-genai` has open proposals for
  A2A: [#70](https://github.com/open-telemetry/semantic-conventions-genai/issues/70)
  (reusing `gen_ai.conversation.id` for A2A's `contextId` and `gen_ai.operation.name`
  for the A2A operation, matching this project's position),
  [#254](https://github.com/open-telemetry/semantic-conventions-genai/issues/254),
  [#195](https://github.com/open-telemetry/semantic-conventions-genai/issues/195) and
  [#447](https://github.com/open-telemetry/semantic-conventions-genai/issues/447).
  On the extraction side `a2aproject/a2a-python` has two closed issues,
  [#168](https://github.com/a2aproject/a2a-python/issues/168) and
  [#281](https://github.com/a2aproject/a2a-python/issues/281), neither addressing
  that `A2AStarletteApplication` extracts nothing at the ASGI/HTTP boundary.
- **Position.** GenAI semconv says `gen_ai.conversation.id` should be populated when
  an identifier is readily available and forbids a UUID, trace id or content-hash
  fallback; A2A supplies `contextId` and `taskId`. No `a2a.*` or `gen_ai.*` attribute
  is invented here. The `a2a-python` zero-attribute finding and the extraction gap
  are filing-ready (`docs/upstream-findings.md` Finding 1).
- **Newer major version.** `a2a-sdk` 1.2.3 still has no propagation code (no
  `traceparent` or `TextMapPropagator` anywhere, and all 16 decorated
  modules still carry a bare `@trace_class(kind=...)` with no attributes). Both agents
  stay on 0.3.7 because 1.x is A2A protocol v1.0 (the JSON-RPC method is
  `SendMessage`, not `message/send`), its `AgentCard` is a protobuf message that
  cannot parse this repository's committed card, and
  `a2a.server.apps.A2AStarletteApplication`, the object the Starlette instrumentation
  attaches to, no longer exists.

### Gap 2 (closed): human-in-the-loop approval spans

- **Evidence.** OpenTelemetry's semantic conventions have no span, attribute
  namespace or registry entry for an approval, an escalation or a human-interrupt
  step in an agent trajectory. The GenAI conventions cover the model call, the tool
  call and (in open proposals) agent-to-agent identity; none has a place to say a
  person decided this, at this time, and the work resumed under a different
  principal. The nearest neighbours (`user.id`, the deprecated `enduser.*`) are
  about the principal, not a decision.
- **Status.** Closed by defining the names in the open: `northstar.review.decision`,
  the `northstar.review.*` attributes, `northstar.a2a.resume` and the span links
  described in "The trace". Inventing telemetry for a third-party component is
  forbidden; instrumenting this project's own code where no framework does it is not
  the same act, and the `northstar.` prefix lets a reader tell the two apart. Nothing
  was renamed, aliased or back-filled onto anyone else's spans.
- **Not covered.** One reviewer, one decision, one resumed execution. Multi-party
  approval, an approval that times out, a rejection that re-enters the trajectory and
  per-human identity in the SVID rather than the payload are out of scope.
- **Would reopen** if OpenTelemetry specifies a human-approval or agent-handoff
  convention: the names then become divergence, and the move is to adopt theirs.

### Gap 3 (closed): policy and autonomy decision spans

- **Evidence.** The semantic conventions have no span, namespace or registry entry for
  a policy, authorization or autonomy decision. The nearest neighbours are about the
  principal, not a verdict.
- **Status.** Closed by defining `northstar.policy.decision` and its attributes
  ("The policy decision span"). A policy decision is this project's own code and no
  library emits one, so either this project names the attributes or the decision is
  invisible.
- **Would reopen** if OpenTelemetry specifies a policy-decision convention, which is
  plausible: the GenAI conventions are growing towards agent governance. Nothing here
  would notice the day it lands.

### Gap 4: baggage propagation across the MCP boundary

- **Evidence.** `northstar.claim.id` reaches payments-mcp on 0 of its tool-call spans
  and payments-api on 0 of its money-hop spans in a live capture of the full chain
  (`tests/e2e/test_claim_correlation.py::test_claim_id_is_on_every_mcp_hop_span`). The
  `traceparent` and `tracestate` crossing is intact: this is a correlation gap, not a
  broken trace. FastMCP 4.0.3's `extract_trace_context()` and `inject_trace_context()`
  (`fastmcp/telemetry.py`) copy only `traceparent` and `tracestate` out of
  `params._meta`; `baggage` is never read or written as a `_meta` key anywhere in the
  installed package (the one `grep -rn baggage` match is a code comment about
  preserving already-active baggage on extract). SEP-414, which this project's MCP
  trace-context convention follows, documents `baggage` as one of the three unprefixed
  `_meta` keys.
- **Upstream.** `PrefectHQ/fastmcp#3998` (open) reports the client side of the gap:
  the server "does not receive `traceparent` / `baggage` through MCP `_meta`". It
  assumes `tools/call` already propagates baggage, which a live capture shows is not
  true for the `baggage` key. No open issue reports that the server never reads
  `baggage` out of `_meta`, the precise finding here; `#4358` concerns the same file
  but does not mention baggage. The MCP spec's
  [SEP-2028](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/2028)
  is adjacent (forwarding `_meta` context to downstream HTTP calls), not the same gap.
  A natural fix adds a `baggage` key to both `extract_trace_context()` and
  `inject_trace_context()`.
- **Position.** Filing-ready and not filed (`tools/payments-mcp/NOTES.md`,
  `agents/settlement-agent/NOTES.md`). Not worked around: nothing here reaches into
  FastMCP's `_meta` handling. Pinned by the manual `pytest.xfail()` test named above.
  `scripts/demo evidence` is built around this gap: it finds a claim's traces by claim
  id, then the decision and the money hops by trace id.

### Gap 5: FastMCP never emits `gen_ai.operation.name` on tool-call spans

- **Evidence.** FastMCP 4.0.3's `tools/call <tool>` spans carry `gen_ai.tool.name`
  but never `gen_ai.operation.name`, where the GenAI semconv specifies `execute_tool`
  for a tool invocation (`tools/payments-mcp/NOTES.md`). The metric-side counterpart
  is Gap 8: FastMCP records no `gen_ai.execute_tool.duration` either, because the
  package has no `get_meter(` call.
- **Upstream.** Already filed and open:
  [`PrefectHQ/fastmcp#3534`](https://github.com/PrefectHQ/fastmcp/issues/3534).
- **Position.** No action here and no bespoke `gen_ai.operation.name` added to
  `server.py`.

### Gap 6: trace context in `params._meta` is the SDK's job, and this repo still does it by hand

- **Evidence.** `mcp==1.9.4`'s `ClientSession.call_tool()` had no `_meta` parameter, so
  `agents/settlement-agent/mcp_tools.py` and `tests/e2e/test_payments_mcp_trace.py`
  hand-build a `CallToolRequestParams(..., _meta=...)` and call the lower-level
  `send_request()`.
- **Upstream.** Fixed:
  [`modelcontextprotocol/python-sdk#1231`](https://github.com/modelcontextprotocol/python-sdk/pull/1231)
  added `call_tool(meta=...)`. At the pinned `mcp==2.2.0` the gap is obsolete: the
  JSON-RPC dispatcher injects W3C trace context into `_meta` on every outbound
  request (`mcp/shared/jsonrpc_dispatcher.py`, with a comment naming SEP-414).
- **Position.** `mcp_tools.py`'s hand-built `send_request()` path is redundant but
  harmless (the SDK's injection overwrites the same keys with a deeper parent in the
  same trace, and the unit tests pin the hand-built path). Removing it is a
  behavioural change that needs its own live proof; `docs/upstream-findings.md`
  Finding 4.

### Gap 7 (closed): the model's outbound HTTP hop had no CLIENT span

- **Cause.** `anthropic` requires `httpx2`, a distribution separate from `httpx`
  (`pydantic/httpx2`, an API-compatible fork). `HTTPXClientInstrumentor` does not
  cover it. `HTTPX2ClientInstrumentor`, in the same `opentelemetry-instrumentation-httpx`
  package, does (upstream
  [`#4730`](https://github.com/open-telemetry/opentelemetry-python-contrib/pull/4730),
  shipped in 1.44.0/0.65b0). There was no upstream gap to report; the omission was this
  project's own `bootstrap.py`.
- **What it would lose.** HTTP status (a retried 429, a 529 overload and a malformed
  400 are indistinguishable from a slow success), the SDK's internal retry count,
  wire-versus-logical latency, and connection-level failure detail.
- **Status.** `bootstrap.py` calls `HTTPX2ClientInstrumentor().instrument()` beside
  `HTTPXClientInstrumentor()`. Each `chat` span has exactly one httpx2 CLIENT child
  (`otel.scope.name=opentelemetry.instrumentation.httpx`, `POST`, stable HTTP semconv
  under the opt-in: `http.request.method`, `http.response.status_code`,
  `server.address=api.anthropic.com`, `url.full`, `network.protocol.version`). The
  two instrumentor classes share one tracer, so the scope name cannot tell an httpx
  span from an httpx2 span; the model-bound span is identified by `server.address ==
  "api.anthropic.com"`. `chat` is the outer logical operation (with `gen_ai.*`) and
  the httpx span the wire hop (with `http.*`): two CLIENT spans at two depths for two
  different things, not a duplicate. Pinned by
  `tests/e2e/test_settlement_llm_trace.py::test_anthropic_client_span_exists_and_is_parented_to_chat`.
  See `docs/upstream-findings.md` Finding 5.
- **Ecosystem note.** Related httpx2 issues remain open elsewhere (for example
  `open-telemetry/opentelemetry-python-contrib#4953` and `#4954` for the OpenAI
  client's `server.address`, `DataDog/dd-trace-py#18780`,
  `elastic/apm-agent-python#2706`): the httpx-to-httpx2 move is an ecosystem-wide
  instrumentation transition.

### Gap 8: five of the six agent GenAI metrics are emitted by nothing

- **Evidence.** With a `MeterProvider` in all five Python services and Prometheus
  receiving OTLP directly, exactly one of the six metrics reaches it:
  `gen_ai.client.token.usage`, from PydanticAI. The other five are created by nothing
  (see "Metrics").
- **Upstream.** All twelve GenAI metrics are published in
  `semantic-conventions-genai` at Development stability, so this is a
  framework-coverage gap against a published convention. `docs/upstream-findings.md`
  Finding 12 has what was and was not found in the trackers.
- **Position.** Nothing hand-rolled: a `gen_ai.*`-named instrument authored here
  would make this project's telemetry disagree with what a fixed framework later
  emits. Two tests enforce it, because it takes two layers:
  `tests/e2e/test_prometheus_metrics_ingest.py::test_no_metric_was_invented_to_fill_a_framework_gap`
  reads a store, so it sees what a series is called and never who authored it, and
  `tests/unit/test_metrics_wiring.py::test_no_service_authors_a_metric_under_a_convention_namespace`
  walks the AST of every Python file under `agents/`, `apps/`, `platform/`, `tools/`
  and `systems/` for an instrument created under a namespace an instrumentation
  library owns, with no cluster.

## Known limitations

Deliberate scope boundaries, not defects found late. None is a telemetry-invention
issue, and none makes a published claim above false. They are the edges a reader
who pushes on this system will find.

**1. The policy limit is currency-blind.** `pds/rules.py` keys `EXECUTE_LIMITS` on
`(SPIFFE ID, autonomy level)` and compares `req.amount > limit` with no reference to
`req.currency`; the currency reaches `decide()` and is used only to render the refusal
text. A request for 6,200 of any unit is judged against a limit denominated in euros.
It is bounded outside the PDS: payments-api's execute handler confirms the stated
currency against the recorded proposal, and `contracts/openapi/payments-api.yaml`
puts `enum: [EUR]` on the propose body. The platform as a whole is EUR-only; the
policy engine alone does not know what a euro is.

**2. Unbounded attacker-supplied strings reach a span attribute.** `decide()`'s
refusal texts interpolate values from the wire (`f"unknown tool {req.tool!r}"` echoes
the tool name, `_money()` echoes the currency). `parse_call` type-checks both but does
not length-check them. That string becomes `AuthorizationError.reason`, which
agentgateway copies verbatim into `otel.status_description` on its `tools/call` SERVER
span. A caller that can reach the gateway and authenticate (today only
`settlement-agent`) can put an arbitrary-length string into this system's telemetry.
The failure mode is a fat span, not an injection, since it lands as an OTLP attribute
value and not in a query or a shell.

**3. `_latch` latches the whole run, not the refused call.**
`agents/settlement-agent/settlement.py`'s `_latch` stops a model retrying a payment it
will never be allowed to make: after any refusal, no further payment call leaves the
process for that claim. The granularity is coarse on purpose, since the correct next
move after a refusal is to narrate it. It would be wrong for a re-propose at a
compliant amount by the original agent on the same claim, which it still refuses. A
human approval is unaffected: the release is made by reviewer-ui, not by a model run.

**4. `_leaf_exception` can drop a denial-bearing `McpError` in a mixed exception
group.** `agents/settlement-agent/mcp_tools.py`'s unwrapper prefers a `ToolCallError`
leaf anywhere in the group and otherwise takes `leaves[0]`. `McpError`, which is what
a policy denial arrives as, is not a `ToolCallError`, so a group holding both would
surface the `ToolCallError` and lose the denial. Not reachable today: every exception
group measured on this path holds one leaf.

**5. settlement-agent configures no logging.** It has no `logging.basicConfig` or
`dictConfig`, so its root logger sits at WARNING with no handlers and its own INFO
lines (`decide_settlement`'s refusal line, `executor.py`'s reply line) print nothing;
`_latch`'s WARNING does appear. The Policy Decision Service calls
`logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))` in `serve()` and logs
one `decision=... rule=... tool=... caller=...` line per decision.

**6. `tests/unit` is not self-sufficient on a fresh clone.** It needs one generated
artifact that only a script outside it produces: the Policy Decision Service's gRPC
stubs. `agents/settlement-agent/tests/test_policy_deny_contract.py` imports
`pds.server`, so the settlement-agent suite aborts at collection without them. The
stubs are not committed, so the repository and its images cannot disagree about the
vendored `proto/ext_mcp.proto`. The gate runs `./gen-stubs.sh` ahead of every suite,
and the README says how to generate them to run a suite on its own.

**7. Every service image carries its own test tooling.** Each Python service has one
`requirements.txt` for runtime and test, and its Dockerfile installs the whole file
with `pip install --no-cache-dir -r requirements.txt`, so the running images contain
`pytest` (and `pytest-asyncio` in three of them). The Policy Decision Service image
additionally contains `grpcio-tools` and its `setuptools` build dependency, needed
only by the `RUN ./gen-stubs.sh python` build layer. Its ingress is otherwise minimal
(a Linkerd Server that admits only agentgateway's identity), and the image runs as
non-root uid 10001 with no subprocess in the serving path. Nobody has measured whether
any of it is exploitable. A multi-stage build would risk the image/repo divergence the
uncommitted stubs avoid, and splitting each `requirements.txt` re-opens a
two-dependency-closures problem per service.
