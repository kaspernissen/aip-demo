# GenAI instrumentation options: PydanticAI today, Strands, OpenInference, OpenLLMetry

Date: 2026-10-24. Talk: 2026-11-11 (18 days).

This note answers three questions:

1. Does the agent telemetry we emit today cover the full agentic loop, measured against the
   current OpenTelemetry GenAI semantic conventions?
2. Is it worth trying Strands Agents as the agent framework?
3. Is it worth adding OpenInference (Arize) or OpenLLMetry (Traceloop), instead of or alongside
   what we use?

Everything below was measured offline, without the cluster, without an API key and without
spending tokens. The harness and every span dump are committed under
`experiments/genai-instrumentation/`. `experiments/genai-instrumentation/reproduce.sh`
rebuilds the virtualenvs and regenerates every dump.

## Short answer

- **Coverage today is good.** PydanticAI 2.42.0 (our pin) emits one `invoke_agent` span per
  run, one `chat` span per model turn and one `execute_tool` span per tool call, all in
  OpenTelemetry GenAI naming. **`gen_ai.response.finish_reasons` is on every `chat` span**,
  normalised to the semconv values (`tool_call` and `stop`). Token usage, response id, tool
  call id, tool name, conversation id, agent name, system instructions and the input/output
  messages are all there. Content goes on span attributes, which is where the current spec
  puts it.
- **The gaps are small and specific:** no `error.type` on failed spans, no `gen_ai.tool.type`,
  no `server.port`, the cache-write token count still uses the old name
  (`cache_creation` rather than `cache_write`), no finish reasons or standard usage names on
  the `invoke_agent` span, and a few non-standard extras (`gen_ai.agent.call.id`,
  `gen_ai.usage.details.*`, the deprecated `gen_ai.system`).
- **Strands Agents 1.62.0 emits less standard telemetry than what we have**, measured on the
  same scenarios. It has no `gen_ai.response.finish_reasons` attribute at all, sets
  `gen_ai.provider.name` to `strands-agents` instead of `anthropic`, makes the model span
  `INTERNAL` and names it `chat` without the model, and has no `gen_ai.response.id` or
  `gen_ai.conversation.id`. Moving would also downgrade the MCP SDK (`mcp<2.2`) and the
  Anthropic SDK (`anthropic<1.0`), and touch files totalling about 6,000 lines (947 of
  agent code, the rest tests that assert today's span shapes).
- **OpenInference uses its own conventions** (`llm.*`, `openinference.span.kind`). It has an
  opt-in GenAI mode, but even then the span is `INTERNAL`, named `beta.messages.create`, and
  the finish reasons are raw Anthropic values. **OpenLLMetry's Anthropic instrumentor emits
  GenAI semconv** and is close to correct. Both add a **second model span per turn** when
  run alongside PydanticAI, because they patch the Anthropic client underneath PydanticAI's
  own `chat` span.
- **Recommendation:** stay on PydanticAI 2.42.0 with its native instrumentation, add no
  third-party GenAI instrumentor, and do not switch frameworks before the talk. If any of
  the gaps matter on stage, they can be closed with a few lines in our own code (see
  "Closing the gaps").
- **One trap to know about:** the Anthropic Python SDK has traced natively since **1.14.0**
  (2026-10-14), on by default whenever a global tracer provider is set. Our pin is 1.4.0, so
  we are not affected today. Any bump of `pydantic-ai` past 2.42 pulls `anthropic>=1.12.1`
  and in practice 2.x, and then every model turn gets a second `CLIENT` span carrying its
  own `gen_ai.usage.*`, unless `ANTHROPIC_OPEN_TELEMETRY=false` is set.

## How this was measured

**The model.** `common/fake_anthropic.py` is a scripted Anthropic Messages API behind an
`httpx`/`httpx2` `MockTransport`. The real `anthropic` client and the real PydanticAI
`AnthropicModel` run against it, so attributes that depend on the provider
(`gen_ai.provider.name`, `gen_ai.response.id`, finish reasons, usage) come out exactly as
they would against `api.anthropic.com`. It answers non-streaming requests and SSE streams.
Its usage figures include non-zero cache reads (100) and cache writes (20) on purpose:
Anthropic's `input_tokens` excludes cached tokens, and the semconv says
`gen_ai.usage.input_tokens` must add them back. With the cache at zero, an instrumentation
that forgets to add them would still look correct.

**The agent.** `pydantic_ai_audit/run.py` imports the repo's own, unmodified
`settlement.settlement_agent` and `decide_settlement` from `agents/settlement-agent/`, and
configures instrumentation the way `bootstrap.py` does
(`Agent.instrument_all(InstrumentationSettings(include_content=..., version=5))`, global
tracer provider), but with an in-memory exporter.

**The scenarios**, each run once per configuration:

| Scenario | What happens | Expected outcome |
|---|---|---|
| `happy` | propose -> execute -> narrative | reply `EXECUTED` |
| `refused` | execute is denied by the platform (`PolicyDenial`, a PydanticAI `ToolFailed`) | model narrates; reply `awaiting_approval` |
| `tool_error` | execute fails with `ToolCallError` (a 409, a malfunction) | the run raises |

**The spec.** The GenAI semconv now lives in
[open-telemetry/semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai).
It has no release yet. Everything here was read at commit
[`9ffc7e0`](https://github.com/open-telemetry/semantic-conventions-genai/tree/9ffc7e00c639be7712e544e3e54331e885dccf84)
(2026-10-21). The last *released* GenAI conventions are in
`open-telemetry/semantic-conventions` **v1.41.1** (2026-05-11); from v1.42.0 on, that
repository's `docs/gen-ai/` pages are "Moved" stubs. `common/semconv_spec.py` flattens the
spec's `model/gen-ai/spans.yaml` into `semconv/spans-flat-9ffc7e0.json`, and
`common/compare.py` diffs each dump against it. Each observed span is classed by its
`gen_ai.operation.name`: `chat` is compared with `anthropic.gen_ai.client.inference`,
`invoke_agent` with `gen_ai.invoke_agent.internal`, and `execute_tool` with
`gen_ai.execute_tool.internal`.

**Spec changes since v1.41.1 that matter here** (changelog fragments in `changelog.d/` at
that commit):

| Fragment | Change |
|---|---|
| 440.breaking | `gen_ai.usage.cache_creation.input_tokens` renamed to `gen_ai.usage.cache_write.input_tokens` |
| 469.breaking | cache read/write token counts removed from the internal `invoke_agent` span |
| 374.breaking | `gen_ai.client.token.usage` histogram (and `gen_ai.token.type`) replaced by per-direction histograms, e.g. `gen_ai.client.inference.usage.input_tokens` |
| 363.deprecation | `gen_ai.output.messages[].finish_reason` deprecated in favour of `gen_ai.response.finish_reasons` |
| 242.breaking | `gen_ai.agent.id` removed from internal agent spans |
| 289.breaking | `gen_ai.provider.name` no longer required on the internal `invoke_agent` span |
| 521.breaking | adds `gen_ai.client.inference.duration`; `gen_ai.client.operation.duration` is not for inference |

**Events versus attributes.** The current spec records content as opt-in **span
attributes**: `gen_ai.system_instructions`, `gen_ai.input.messages`,
`gen_ai.output.messages`, `gen_ai.tool.definitions`, `gen_ai.tool.call.arguments` and
`gen_ai.tool.call.result`. The older per-message events (`gen_ai.user.message`,
`gen_ai.choice` and the rest) are gone. The one content event left,
`gen_ai.client.inference.operation.details`, is a **log-based event** for storing details
apart from traces, not a span event. The only other GenAI events are
`gen_ai.evaluation.result` and `gen_ai.client.operation.exception`.

## 1. What we emit today (PydanticAI 2.42.0, `version=5`, content on)

The span tree is the same for all three scenarios, apart from the error status:

```
invoke_agent settlement_agent   INTERNAL  pydantic-ai
  chat claude-sonnet-5          CLIENT    (turn 1: tool_call propose_payment)
  execute_tool propose_payment  INTERNAL
  chat claude-sonnet-5          CLIENT    (turn 2: tool_call execute_payment)
  execute_tool execute_payment  INTERNAL  [ERROR in refused and tool_error]
  chat claude-sonnet-5          CLIENT    (turn 3: narrative; absent in tool_error)
```

(In the running service each `chat` span also has an `httpx2` CLIENT child from
`HTTPX2ClientInstrumentor`. The harness does not install that instrumentor.)

### The agentic loop

| Loop step | Covered? | Where |
|---|---|---|
| Every model turn | yes | one `chat {model}` CLIENT span per turn |
| Every tool call | yes | one `execute_tool {name}` span; `gen_ai.tool.name`, `gen_ai.tool.call.id` (the model's `toolu_…` id) |
| Every tool argument and result | yes (content on) | `gen_ai.tool.call.arguments` / `gen_ai.tool.call.result` on the tool span; also as `tool_call` / `tool_call_response` parts in the next turn's `gen_ai.input.messages` |
| Why each turn ended | yes | `gen_ai.response.finish_reasons` on every `chat` span: `["tool_call"]`, `["tool_call"]`, `["stop"]` |
| Token usage | yes | `gen_ai.usage.input_tokens` (cache included: 620 = 500 + 100 + 20), `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens` per turn; run totals on `invoke_agent` under `gen_ai.aggregated_usage.*` |
| Conversation | yes | `gen_ai.conversation.id` on all three span types. On `main` it is a random UUID per run; on `m11-agentevals` and later, `decide_settlement` passes `conversation_id=claim_id` |
| Agent identity | name only | `gen_ai.agent.name=settlement_agent` on all spans; no `gen_ai.agent.description` (the `Agent` is not given one) |
| System instructions | yes | `gen_ai.system_instructions` on `chat` and `invoke_agent` |
| Input/output messages | yes, on `chat` | `gen_ai.input.messages`, `gen_ai.output.messages`; on `invoke_agent` only as the non-standard `pydantic_ai.all_messages` |
| Policy refusal | yes | `execute_tool execute_payment` status ERROR, `exception` event (`ToolFailedError`), refusal text in `gen_ai.tool.call.result`; the model's narration is in turn 3's output |
| Malfunction | partly | tool span and `invoke_agent` status ERROR, `exception` events (`mcp_tools.ToolCallError`); **no `error.type`** anywhere |

### Attribute coverage against the semconv

"Covered" means present with the spec's name and meaning. "Differs" means present but
named, placed or shaped differently. "Missing" means absent where the spec asks for it.

| Attribute | Spec level (span) | Today | Notes |
|---|---|---|---|
| `gen_ai.operation.name` | required (all) | covered | `chat`, `invoke_agent`, `execute_tool` |
| `gen_ai.provider.name` | required (chat) | covered | `anthropic` |
| `gen_ai.system` | deprecated | differs | still emitted alongside `gen_ai.provider.name` |
| `gen_ai.request.model` | cond. required (chat); recommended (invoke_agent) | covered on `chat`; missing on `invoke_agent` | `invoke_agent` carries the non-standard `model_name` instead |
| `gen_ai.response.model` | recommended (chat) | covered | |
| `gen_ai.response.id` | recommended (chat) | covered | `msg_…` |
| **`gen_ai.response.finish_reasons`** | recommended (chat, invoke_agent) | **covered on `chat`**; missing on `invoke_agent` | Anthropic `tool_use` -> `tool_call`, `end_turn` -> `stop`, `max_tokens` -> `length`, `refusal` -> `content_filter`. Two edge cases: `compaction` -> `stop` (the spec now has a `compaction` value), and `pause_turn` -> no value |
| `gen_ai.usage.input_tokens` / `output_tokens` | recommended | covered on `chat`; differs on `invoke_agent` | `invoke_agent` uses `gen_ai.aggregated_usage.*` (a PydanticAI default, `use_aggregated_usage_attribute_names=True`, which stops backends that sum across parent and child spans from double counting) |
| `gen_ai.usage.cache_read.input_tokens` | recommended when non-zero | covered | |
| `gen_ai.usage.cache_write.input_tokens` | recommended when non-zero | differs | emitted under the pre-rename name `gen_ai.usage.cache_creation.input_tokens`, plus four non-standard `gen_ai.usage.details.*` duplicates |
| `gen_ai.conversation.id` | cond. required | covered | |
| `gen_ai.agent.name` | cond. required (invoke_agent, execute_tool) | covered | |
| `gen_ai.agent.description` | cond. required when available | missing | `Agent(description=...)` exists at 2.42.0 and would emit it; we pass none |
| `gen_ai.agent.id` | not on internal `invoke_agent` (#242) | n/a | correct to omit |
| `gen_ai.agent.call.id` | not in spec | differs (extra) | PydanticAI's own run id |
| `gen_ai.system_instructions` | opt-in | covered | |
| `gen_ai.input.messages` / `gen_ai.output.messages` | opt-in (chat, invoke_agent) | covered on `chat`; missing on `invoke_agent` | JSON string, which the spec allows on spans |
| `gen_ai.tool.definitions` | opt-in | covered on `chat` | not redacted when content is off |
| `gen_ai.tool.name` | required (execute_tool) | covered | |
| `gen_ai.tool.call.id` | cond. required | covered | |
| `gen_ai.tool.call.arguments` / `.result` | opt-in | covered | |
| `gen_ai.tool.type` | recommended | missing | would be `function` |
| `gen_ai.tool.description` | recommended | missing | the description is in `gen_ai.tool.definitions` on the `chat` span |
| `error.type` | cond. required on error (all) | **missing** | PydanticAI's own source has a `TODO … error.type: unclear` (`_instrumentation.py`), still there at 2.60.0. Spans do get status ERROR and an `exception` event |
| `server.address` | recommended (chat) | covered | `api.anthropic.com` |
| `server.port` | cond. required with `server.address` | missing | |
| `gen_ai.request.max_tokens`, `temperature`, `top_p`, … | recommended | missing | recorded only when set in `model_settings`; settlement sets none |

**Metrics** (same harness, `InMemoryMetricReader`): `gen_ai.client.token.usage` (histogram,
with `gen_ai.token.type`) and the non-standard `operation.cost`. Upstream is replacing
`gen_ai.client.token.usage` with per-direction histograms (#374, unreleased). There is no
`gen_ai.client.inference.duration` and no `gen_ai.client.operation.duration`.

**With content off** (`NORTHSTAR_CAPTURE_CONTENT=false`), the run drops
`gen_ai.system_instructions`, `gen_ai.tool.call.arguments` and `gen_ai.tool.call.result`,
and keeps message *shape* only. `version=6` changes one thing: tool results go into
messages with `role: "tool"` instead of `role: "user"` (3 such messages in `happy`). The
attribute names do not change.

Dumps: `pydantic_ai_audit/out/v5-content-on/`, `v5-content-off/`, `v6-content-on/`. The
semconv diff is `pydantic_ai_audit/out/v5-content-on/semconv-diff.json`.

### What changes with newer PydanticAI (pin not bumped)

`pydantic-ai-slim` 2.60.0 was released 2026-10-23, and 2.61.0 followed on 2026-10-24. The
harness was rerun against 2.60.0, in a separate venv with the repo's other pins
(`pydantic_ai_audit/out-2.60.0/`).

| Change | Measured or read | Effect on us |
|---|---|---|
| Same span names, kinds and `gen_ai.*` set | measured | no gain: `error.type`, `gen_ai.tool.type` and `server.port` are still missing; `cache_creation` still uses the old name |
| `pydantic_ai.cache.established_tokens`, `pydantic_ai.cache.hit_ratio` added to `chat` | measured | extra non-standard attributes |
| Requests to Anthropic are now streamed (`max_tokens` defaults to the model maximum) | measured (`gen_ai.request.stream=true`, `max_tokens=128000` on the SDK span) | in `gen_ai.output.messages`, tool-call `arguments` become a **JSON string** instead of an object (the spec's schema wants an object) |
| Pulls `anthropic>=1.12.1`, so 2.3.0 in practice | measured | the SDK's native tracer adds `anthropic.messages.create` (CLIENT, scope `com.anthropic.sdk.python`) under every `chat` span, carrying its own `gen_ai.usage.*`: **usage is counted twice** unless `ANTHROPIC_OPEN_TELEMETRY=false` |
| `RetryPromptPart` deprecated (2.60.0) | changelog | `tests/test_settlement.py` uses it |
| GHSA-4x9p-g9wm-8q7f (fixed 2.44.0): with `include_content=False`, spans still carried exceptions, error statuses and instructions | changelog | affects our content-off mode only; the default is on |
| GHSA-xv4x-g8mx-ch56 (fixed 2.58.0): provider-native MCP config could leak tokens into traces | changelog | not applicable; we call MCP from our own code |

No changelog entry from 2.43.0 to 2.61.0 adds `error.type`, `gen_ai.tool.type`, or changes
the GenAI span shapes.

## 2. Strands Agents

### Facts

| Item | Value |
|---|---|
| Latest release | `strands-agents` **1.62.0**, 2026-10-21 ([PyPI](https://pypi.org/project/strands-agents/)) |
| Licence | Apache-2.0 |
| Repository | [strands-agents/harness-sdk](https://github.com/strands-agents/harness-sdk) (the old `sdk-python` name redirects here; Python and TypeScript in one repo) |
| Governance | AWS-led, in the `strands-agents` GitHub org. Not part of the Linux Foundation's Agentic AI Foundation: AAIF's projects are MCP, goose, AGENTS.md, A2A, OpenSandbox, Agent Skills, OpenEnv, Open Responses and others ([LF](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation), [The Register, 2026-10-23](https://www.theregister.com/ai-and-ml/2026/10/23/vendor-affiliated-ai-agent-tech-gets-adopted-by-friendly-funded-foundation/5304912)) |
| Anthropic models | `strands.models.anthropic.AnthropicModel`; extra pins **`anthropic<1.0.0`** (resolved to 0.125.0). The default model provider is Bedrock |
| MCP | `strands.tools.mcp.MCPClient` (stdio, streamable HTTP); injects trace context into `params._meta` itself; requires **`mcp<2.2`** (we pin 2.2.0) |
| A2A | `strands.multiagent.a2a` (`A2AServer`), `a2a-sdk>=0.3.0,<0.4.0`; compatible with our 0.3.7 |
| OTel | native tracer (`strands.telemetry.tracer`). Default output is the older GenAI form (per-message span events, `gen_ai.system`). `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` switches to the newer names; `gen_ai_tool_definitions` adds tool definitions; `gen_ai_span_attributes_only` moves content from span events onto span attributes. The docs say "latest OTEL semantic conventions" and name no version ([Strands traces docs](https://strandsagents.com/latest/documentation/docs/user-guide/observability-evaluation/traces/)) |
| Metrics | its own `strands.*` names (`strands.event_loop.input.tokens`, `strands.tool.duration`, …), not `gen_ai.client.*` |
| Test model | none shipped; the prototype uses the same fake Anthropic endpoint |

### Prototype

`strands_prototype/run.py` is settlement-agent's agent rewritten in Strands: the same
instructions, the same two tools served over MCP by a stdio stub
(`strands_prototype/payments_mcp_stub.py`, with payments-mcp's argument names), the same
three scenarios and the same fake Anthropic endpoint. It also carries over three
behaviours of `settlement.py`, using hooks:

- `BeforeToolCallEvent` writes `claim_id` into `propose_payment`'s input. The model must
  not choose it; `settlement.py` injects it from deps.
- After a refusal, `BeforeToolCallEvent` sets `cancel_tool` for any further payment call.
  This is `settlement.py`'s `_latch`.
- `AfterToolCallEvent` tells a policy denial apart from a malfunction. **This is a
  behaviour difference:** Strands turns every tool failure into an error result that the
  model reads, so the model narrates a 409 before our code can raise. `settlement.py`
  lets `ToolCallError` escape the run before the model sees it.

All three scenarios end correctly (`EXECUTED`, `awaiting_approval`, and a raise after the
run). The span tree with `gen_ai_latest_experimental`:

```
MCP send server/discover        CLIENT    mcp-python-sdk    (own trace, outside the agent)
MCP send tools/list             CLIENT    mcp-python-sdk    (own trace)
invoke_agent settlement_agent   INTERNAL  strands.telemetry.tracer
  execute_event_loop_cycle      INTERNAL
    chat                        INTERNAL
    execute_tool propose_payment INTERNAL
      MCP send tools/call propose_payment  CLIENT  mcp-python-sdk
  execute_event_loop_cycle      INTERNAL
    chat                        INTERNAL
    execute_tool execute_payment INTERNAL  [ERROR in refused, tool_error]
      MCP send tools/call execute_payment  CLIENT  mcp-python-sdk
  execute_event_loop_cycle      INTERNAL
    chat                        INTERNAL
```

### PydanticAI today versus Strands, against the spec

| Item | PydanticAI 2.42.0 (today) | Strands 1.62.0 (`latest`) |
|---|---|---|
| Model span name and kind | `chat claude-sonnet-5`, CLIENT | `chat`, **INTERNAL** |
| `gen_ai.provider.name` | `anthropic` | **`strands-agents`** (wrong per spec: should be the model provider) |
| **`gen_ai.response.finish_reasons`** | **on every `chat` span, normalised** | **absent.** Only inside the output-message content, raw (`tool_use`, `end_turn`), which is the location #363 deprecates |
| `gen_ai.response.id` / `response.model` | present | absent |
| `gen_ai.usage.input_tokens` | 620 (cache added) | 620 (cache added) |
| cache write tokens | `cache_creation` (old name) | `cache_creation` (old name) |
| deprecated duplicates | `gen_ai.system` | `gen_ai.usage.prompt_tokens`, `completion_tokens`, `total_tokens` |
| `gen_ai.conversation.id` | present | absent (`trace_attributes` can add `session.id`) |
| `gen_ai.agent.name` on tool spans | present | absent |
| `gen_ai.agent.id` / `description` | not set / not set | not emitted, although `agent_id` and `description` were passed (for `agent.id` this is correct on an internal span) |
| Content placement | span attributes | **span events** named `gen_ai.client.inference.operation.details` (a log event in the spec); attributes only with `gen_ai_span_attributes_only` |
| `gen_ai.tool.description` | absent | present |
| `gen_ai.tool.type` | absent | absent |
| `error.type` | absent | absent (span status description is set) |
| Extra spans | none | `execute_event_loop_cycle` per turn (non-standard `gen_ai.operation.name`) |
| Extra attributes | `gen_ai.agent.call.id`, `gen_ai.usage.details.*`, `operation.cost`, `logfire.*` | `gen_ai.event.start_time`/`end_time`, `gen_ai.tool.status`, `gen_ai.tool.json_schema`, `gen_ai.agent.tools` |
| Metrics | `gen_ai.client.token.usage` | `strands.*` only |
| MCP client spans | none from the framework (our `mcp_tools.py` makes the call) | `MCP send tools/call …` from the MCP SDK, with only `mcp.method.name` and `jsonrpc.request.id` |
| Content capture switch | `include_content` (on/off) | always on; opt-in redaction list |

In legacy mode (no opt-in), Strands uses the removed per-message events
(`gen_ai.user.message`, `gen_ai.choice`, `gen_ai.tool.message`, …) and `gen_ai.system`.
Dumps and diffs: `strands_prototype/out/{legacy,latest,latest-attrs}/`.

### What a switch would touch

| Area | Files (lines today) | Why it changes |
|---|---|---|
| Agent | `agents/settlement-agent/settlement.py` (350) | agent, tools, deps, latch and refusal handling rebuilt on hooks |
| MCP client | `mcp_tools.py` (389) | `PolicyDenial` subclasses `pydantic_ai.ToolFailed`; the hand-built `_meta` call and per-call JWT-SVID bearer would need to move into `MCPClient` (custom `httpx.Auth`) or stay as `@tool` wrappers |
| Bootstrap | `bootstrap.py` (208) | `_instrument_pydantic_ai` becomes environment opt-ins; no content off switch; the httpx/httpx2 instrumentor split changes because `anthropic<1.0` uses `httpx` |
| Pins | `requirements.txt` | `mcp` 2.2.0 -> <2.2, `anthropic` 1.4.0 -> 0.x, plus `strands-agents`, boto3 and botocore (required dependencies) |
| Unit tests | `tests/test_settlement.py` (558), parts of `test_mcp_tools.py` (848), `test_pins.py`, `test_instrumentation_settings.py` | built on `FunctionModel`, `RetryPromptPart`, `ToolFailed`; Strands ships no test model |
| E2E and unit tests on span shape | `tests/e2e/test_settlement_llm_trace.py` (781), `test_policy_denial.py` (1,743), `test_settlement_agent_trace.py` (478), `tests/unit/test_metrics_wiring.py` (630), `test_telemetry_contract_attribute_counts.py` | assert `chat claude-sonnet-5`, `invoke_agent settlement_agent`, the 28-name `gen_ai.*` inventory, and `gen_ai_client_token_usage_*` in Prometheus |
| Demo and dashboards | `scripts/demo.py`, `deploy/observability/dashboards/evidence.ndjson` | span names and attributes |
| Contract and registry | `docs/telemetry-contract.md`, `telemetry/registry/` | the inventory and live-check expectations |
| Later branches | `m11-agentevals` (AgentEvals keys on `gen_ai.conversation.id`, which Strands does not emit), `m13-claims-intake` (`agents/claims-agent/extraction.py`, 99 lines, also PydanticAI) | same work again, plus a lost join key |

The files in this table that exist on `main` total 5,985 lines: 947 of agent code
(`settlement.py`, `mcp_tools.py`, `bootstrap.py`), 1,406 of unit tests and 3,632 of
span-shape tests. Not every line changes, but every file does, and a gate run on the
cluster has to follow. The telemetry gets less standard on the axis this demo talks
about.

## 3. OpenInference, OpenLLMetry, and two options not asked about

Two more emitters turned up during the research and belong in the same comparison.
**OpenTelemetry's own Anthropic instrumentation** (`opentelemetry-instrumentation-genai-anthropic`,
in [open-telemetry/opentelemetry-python-genai](https://github.com/open-telemetry/opentelemetry-python-genai),
a repo created 2026-05-12) is the upstream, vendor-neutral one. The **Anthropic SDK's
native tracing** arrived in `anthropic` 1.14.0.

### Facts

| | OpenInference | OpenLLMetry | OTel upstream (`genai-anthropic`) | Anthropic SDK native |
|---|---|---|---|---|
| Maintainer | Arize AI ([Arize-ai/openinference](https://github.com/Arize-ai/openinference)) | Traceloop ([traceloop/openllmetry](https://github.com/traceloop/openllmetry)); Traceloop acquired by ServiceNow, closed by May 2026 ([Calcalist](https://www.calcalistech.com/ctechnews/article/sjghwiqf11e)) | OpenTelemetry (CNCF) | Anthropic |
| Licence | Apache-2.0 | Apache-2.0 | Apache-2.0 | Apache-2.0 (SDK) |
| Anthropic instrumentor, latest | `openinference-instrumentation-anthropic` 3.1.0 (2026-10-23) | `opentelemetry-instrumentation-anthropic` 0.62.4 (2026-09-29) | `opentelemetry-instrumentation-genai-anthropic` 1.3b0 (2026-10-23) | built in since `anthropic` 1.14.0 (2026-10-14); latest 2.3.0 |
| Conventions | own: `llm.*`, `input.value`/`output.value`, `openinference.span.kind`. Opt-in `OPENINFERENCE_ENABLE_GENAI_SEMCONV=true` adds `gen_ai.*` alongside (default off) | GenAI semconv (`gen_ai.*`), content on attributes by default (`use_attributes=True`) | GenAI semconv via `opentelemetry-util-genai`; needs `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` | GenAI semconv names plus HTTP attributes; content off by default (`ANTHROPIC_OPEN_TELEMETRY_TRACES_CONTENT_MODE=content` to record it) |
| PydanticAI | `openinference-instrumentation-pydantic-ai` 0.1.30: a span processor that rewrites PydanticAI's spans into OpenInference attributes | none | none | n/a |
| Strands | `openinference-instrumentation-strands-agents` 0.1.12 | none | none | n/a |
| MCP | `openinference-instrumentation-mcp` 2.0.13 | `opentelemetry-instrumentation-mcp` 0.62.4 | none (the MCP SDK 2.x emits its own spans) | n/a |
| Resolves against our pins | yes, but needs `anthropic>=1.8.0` at runtime: with our 1.4.0, `instrument()` logs `DependencyConflict` and emits **nothing** | yes | yes | needs an `anthropic` bump |

Naming trap: Traceloop publishes under `opentelemetry-instrumentation-*` package names, so
`opentelemetry-instrumentation-anthropic` is OpenLLMetry. The OpenTelemetry project's own
package is `opentelemetry-instrumentation-genai-anthropic`.

### Measured: each Anthropic instrumentor alone (PydanticAI instrumentation off)

Same harness, `happy` + `refused` + `tool_error`. OpenInference ran with `anthropic` 1.13.0
(see above). Dumps: `pydantic_ai_audit/out-instrumentors/`.

| | OpenInference 3.1.0 | OpenInference + GenAI flag | OpenLLMetry 0.62.4 | OTel `genai-anthropic` 1.3b0 | PydanticAI 2.42.0 `chat` (for reference) |
|---|---|---|---|---|---|
| Span name / kind | `beta.messages.create` / INTERNAL | same | `anthropic.chat` / CLIENT | **`chat claude-sonnet-5` / CLIENT** | `chat claude-sonnet-5` / CLIENT |
| Finish reasons | `llm.finish_reason=tool_use` (raw) | `gen_ai.response.finish_reasons=["tool_use"]` (raw, not normalised) | `["tool_call"]`, `["stop"]` | `["tool_call"]`, `["stop"]` | `["tool_call"]`, `["stop"]` |
| `gen_ai.provider.name` | `llm.provider=anthropic` | `anthropic` | `anthropic` | `anthropic` | `anthropic` |
| `gen_ai.response.id` | in `output.value` only | present | present | present | present |
| Input tokens (cache added) | `llm.token_count.prompt=620` | 620 | 620 | 620 | 620 |
| Cache write name | `prompt_details.cache_write` | `cache_creation` (old) | `cache_creation` (old) | **`cache_write` (current)** | `cache_creation` (old) |
| System instructions | as a `system` message in `llm.input_messages` | as a `system` message in `gen_ai.input.messages` | `gen_ai.system_instructions` | `gen_ai.system_instructions` | `gen_ai.system_instructions` |
| `gen_ai.request.max_tokens` | in `llm.invocation_parameters` | present | present | present | absent |
| `server.address` / `port` | no / no | no / no | no / no | yes / no | yes / no |
| `error.type` on failure | n/a (no model failure in these scenarios) | n/a | n/a | n/a | absent on tool/agent failure |
| Extras | `input.value`, `output.value` (full request/response JSON) | both sets | `gen_ai.is_streaming`, `gen_ai.usage.total_tokens` | none | see section 1 |
| Metrics | none | none | `gen_ai.client.operation.duration`, `gen_ai.client.token.usage`, `gen_ai.client.generation.choices` | `gen_ai.client.operation.duration`, `gen_ai.client.token.usage` | `gen_ai.client.token.usage`, `operation.cost` |
| Noise | none | none | logs `Invalid type Omit for attribute 'gen_ai.request.stop_sequences'` on every call with `anthropic` 1.x | none | none |

These are model-call instrumentors only. None of them produces `invoke_agent` or
`execute_tool` spans for a PydanticAI agent, so they cannot replace PydanticAI's
instrumentation. They can only be added to it.

### Measured: alongside PydanticAI's own instrumentation

| Combination | What happens |
|---|---|
| PydanticAI + OpenInference Anthropic | each `chat` span gets a child `beta.messages.create` with a second set of token counts (`llm.token_count.*`) |
| PydanticAI + OpenLLMetry Anthropic | each `chat` span gets a child `anthropic.chat` CLIENT span with a second `gen_ai.usage.*` and `gen_ai.operation.name=chat`: **two model spans per turn, double-counted usage** in any backend that sums `gen_ai.usage.*` across spans |
| PydanticAI + OTel `genai-anthropic` | the same, with a child named exactly like its parent (`chat claude-sonnet-5`) |
| PydanticAI 2.60 + `anthropic` 2.3 (native) | the same, with child `anthropic.messages.create` (see section 1) |
| PydanticAI + OpenInference span processor | no new spans; every PydanticAI span gets OpenInference attributes **added** (`llm.*`, `input.value`, `output.value`, `openinference.span.kind`, `session.id` taken from the conversation id) on top of the `gen_ai.*` set. This is useful only for an Arize/Phoenix backend; it roughly doubles span payload |

## 4. Recommendation

**For the most standard GenAI telemetry with the least custom code: keep PydanticAI 2.42.0's
native instrumentation as the only GenAI emitter.** Measured on the same scenarios, it is
the most complete of the agent-level options. It is the only one with all three span types
in semconv naming, the correct span kinds, `gen_ai.provider.name=anthropic`, normalised
`gen_ai.response.finish_reasons` on every model turn, `gen_ai.response.id` and
`gen_ai.conversation.id`, and content on span attributes as the spec describes. It needs
zero new dependencies.

**Do not add OpenInference or OpenLLMetry.** Neither adds agent or tool spans for
PydanticAI. Both add a second model span per turn under PydanticAI's `chat` span.
OpenInference's default output is not GenAI semconv, and its opt-in mode is still not
semconv-shaped (INTERNAL kind, SDK-method span name, raw finish reasons, system prompt
inside the messages). It also refuses our `anthropic` 1.4.0. OpenLLMetry is closer to the
spec but adds nothing PydanticAI lacks except `gen_ai.request.max_tokens` and three
metrics, and it logs an error on every call with `anthropic` 1.x. The OpenTelemetry
upstream `genai-anthropic` instrumentor is the most spec-exact model span measured (of the
instrumentors, only it and the Anthropic SDK's native tracer use the current
`cache_write` name), but alongside PydanticAI it duplicates the `chat` span under the same
name.

**Do not switch to Strands before 2026-11-11.**

- **Telemetry regresses.** It loses finish reasons, response id, conversation id and the
  provider name, and the model span becomes INTERNAL.
- **Dependency risk.** `mcp` 2.2.0 goes back to <2.2, and `anthropic` 1.x goes back to 0.x.
- **Behaviour risk.** A malfunction is shown to the model as a tool result before our code
  can stop the run. The refusal path, the demo's central moment, would be re-implemented
  on hooks.
- **Test and evidence risk.** About 5,000 lines of unit and e2e tests assert today's span
  shapes and names, and so do the telemetry contract's attribute-count guard, the Weaver
  registry and live-check, the evidence dashboard, `scripts/demo.py`, and AgentEvals'
  `gen_ai.conversation.id` key on `m11`.
- **No safe buffer.** Each of these needs a gate run on the cluster. The gate history
  (M8: four failed runs before a measured row; M10: runs 2-5 failed on unrelated causes)
  says 18 days does not leave room for that.

**Pin hygiene that follows from the measurements:**

- If `pydantic-ai` or `anthropic` is ever bumped past `anthropic` 1.13, set
  `ANTHROPIC_OPEN_TELEMETRY=false` (or pass `open_telemetry=False` to the client) in the
  same change. Otherwise every model turn gets a second CLIENT span with its own usage.
- A `pydantic-ai` bump also changes our `gen_ai.output.messages` (streamed tool-call
  arguments become strings) and deprecates `RetryPromptPart`, which our tests use. Nothing
  in 2.43 to 2.61 closes a gap listed above.

### Closing the gaps, if any of them matter on stage (no new dependencies)

| Gap | Smallest fix in our code | Size |
|---|---|---|
| `error.type` on failed `execute_tool` spans | in `settlement.py`'s two tool functions, `trace.get_current_span().set_attribute("error.type", type(exc).__qualname__)` before re-raising (the `execute_tool` span is current inside the tool; checked at 2.42.0) | ~4 lines |
| `gen_ai.tool.type` | a `SpanProcessor.on_start` that sets `gen_ai.tool.type="function"` when `gen_ai.operation.name == "execute_tool"` | ~10 lines |
| `gen_ai.agent.description` | `Agent(description="...")` on `settlement_agent` (checked at 2.42.0: lands on `invoke_agent`) | 1 line |
| `gen_ai.request.max_tokens` | set `model_settings={"max_tokens": ...}` on the agent; PydanticAI records request settings that are set | 1 line (it also changes the request) |
| `cache_write` name, `server.port`, finish reasons on `invoke_agent`, `gen_ai.system` duplicate | upstream PydanticAI; an OTel Collector `transform` processor could rename `gen_ai.usage.cache_creation.input_tokens` | collector config |

Every one of these changes span content that the contract's inventory and its count guard
(`tests/unit/test_telemetry_contract_attribute_counts.py`) measure. Each fix moves that
count, and the gate row has to be re-measured after it.

## Sources

- OTel GenAI semconv repo: https://github.com/open-telemetry/semantic-conventions-genai (read at `9ffc7e00c639be7712e544e3e54331e885dccf84`): `model/gen-ai/spans.yaml`, `registry.yaml`, `events.yaml`, `metrics.yaml`, `token-metrics.yaml`, `gen-ai-output-messages.json`, `docs/gen-ai/anthropic.md`, `changelog.d/`
- Move notice in the main semconv repo: https://github.com/open-telemetry/semantic-conventions/blob/v1.42.0/docs/gen-ai/README.md ; last release with content: https://github.com/open-telemetry/semantic-conventions/tree/v1.41.1/docs/gen-ai
- PydanticAI releases v2.43.0 to v2.61.0: https://github.com/pydantic/pydantic-ai/releases ; advisories https://github.com/pydantic/pydantic-ai/security/advisories/GHSA-4x9p-g9wm-8q7f , https://github.com/pydantic/pydantic-ai/security/advisories/GHSA-xv4x-g8mx-ch56
- PydanticAI on PyPI: https://pypi.org/project/pydantic-ai-slim/
- Anthropic Python SDK v1.14.0 release (native OpenTelemetry tracing): https://github.com/anthropics/anthropic-sdk-python/releases/tag/v1.14.0 ; docs https://platform.claude.com/docs/en/manage-claude/opentelemetry/sdk
- Strands Agents: https://github.com/strands-agents/harness-sdk , https://pypi.org/project/strands-agents/ , https://strandsagents.com/latest/documentation/docs/user-guide/observability-evaluation/traces/
- Agentic AI Foundation: https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation , https://www.theregister.com/ai-and-ml/2026/10/23/vendor-affiliated-ai-agent-tech-gets-adopted-by-friendly-funded-foundation/5304912
- OpenInference: https://github.com/Arize-ai/openinference , https://pypi.org/project/openinference-instrumentation-anthropic/ , https://pypi.org/project/openinference-instrumentation-pydantic-ai/ , https://pypi.org/project/openinference-instrumentation-strands-agents/ , https://pypi.org/project/openinference-instrumentation-mcp/
- OpenLLMetry: https://github.com/traceloop/openllmetry , https://pypi.org/project/opentelemetry-instrumentation-anthropic/ , https://pypi.org/project/traceloop-sdk/
- ServiceNow acquires Traceloop: https://www.calcalistech.com/ctechnews/article/sjghwiqf11e
- OpenTelemetry upstream GenAI instrumentations: https://github.com/open-telemetry/opentelemetry-python-genai , https://pypi.org/project/opentelemetry-instrumentation-genai-anthropic/ , https://pypi.org/project/opentelemetry-util-genai/
- Versions and dates above were read from the PyPI JSON API on 2026-10-24 (`experiments/genai-instrumentation/common/pypi_versions.py`).
