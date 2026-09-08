# Northstar Telemetry Contract

Status: M1.1. Revised at every milestone.

## The governing rule

Use the telemetry the frameworks actually emit. Verify per component. Instrument
only our own code. Where a convention gap exists, report it upstream — never
paper over it with a bespoke convention.

## What the default suite proves, and what only the full run proves

`./scripts/smoke-test.sh` runs `pytest tests/e2e -v -k "not browser"` by default,
which excludes `tests/e2e/test_browser_root_span.py` entirely — that file is
gated behind `RUN_BROWSER_TESTS=1` (it needs a real Chromium via Playwright).
The default run proves the chain from claims-agent down to payments-api:
claims-agent → settlement-agent → payments-mcp → payments-api, one trace, no
orphans, `northstar.claim.id` correlated as far as the boundary below states.
**It proves nothing about the browser.** It does not start a browser, does not
exercise CORS, and does not confirm the root span the milestone's whole claim
depends on — that customer-ui, not a test harness, owns the trace root.

`./scripts/smoke-test.sh` **always** sets `RUN_BROWSER_TESTS=1` before invoking
pytest, precisely so the milestone's own gate exercises its own headline claim.
A future reader who sees `smoke-test.sh` finish green has confirmed the browser
hop; a reader who instead runs a bare `pytest tests/e2e` by hand — bypassing the
gate — has confirmed everything except it, and should not conclude the top of
the chain was checked.

Measured counts, from a clean cluster (`./scripts/teardown.sh &&
./scripts/setup-kind.sh --stage 1`), repo-root `pytest tests/`:

| Run | Result |
|---|---|
| Default (`RUN_BROWSER_TESTS` unset) | `47 passed, 6 skipped, 1 xfailed` |
| `RUN_BROWSER_TESTS=1` (what the gate always runs) | `53 passed, 1 xfailed` |

Per-service unit suites (also run by `smoke-test.sh`, before any e2e test):
claims-agent 14, settlement-agent 19, payments-mcp 7, payments-api (Go) 7 —
all passing.

The one remaining `xfail(strict=True)` in both runs is
`tests/e2e/test_claim_correlation.py::test_claim_id_is_on_every_mcp_hop_span`,
documenting the FastMCP baggage gap below (Gap 4). It is deliberately narrow —
pinned to that one assertion — and `strict=True` means the day that gap closes
upstream, this test starts failing until the marker is removed, rather than
staying silently green.

## Enabled instrumentation, as verified

| Component | Instrumentation | Version | Verified span names | Notes |
|---|---|---|---|---|
| customer-ui | OpenTelemetry JS web, manual root span | `@opentelemetry/sdk-trace-web` 2.8.0, `@opentelemetry/context-zone` 1.30.0, `@opentelemetry/exporter-trace-otlp-http` 0.219.0, `@opentelemetry/resources` 2.8.0 | `claim.submit` | Owns the trace root. Requires `traceparent` in CORS allow-headers on both the collector and claims-agent. Requires a *static* import of the injection helper — a dynamic `import()` (or any other `await`) before reading the active span loses it under `ZoneContextManager` (see Gap 5). |
| claims-agent | `a2a-sdk[telemetry]` + `opentelemetry-instrumentation-starlette` | `a2a-sdk[telemetry]==0.3.7`, `opentelemetry-instrumentation-starlette==0.65b0` | ASGI entry span `POST /`; `a2a.server.request_handlers.jsonrpc_handler.JSONRPCHandler.on_message_send` and the rest of the `a2a.server.*` call-graph spans (`DefaultRequestHandler.on_message_send`, `EventQueue.enqueue_event`/`.dequeue_event`/`.close`, `EventConsumer.consume_all`, etc.) | `DISABLE_OTEL_INSTRUMENTATION` must remain unset; `bootstrap.configure()` raises if it is `true`. Inbound W3C trace-context extraction comes from `StarletteInstrumentor` wrapping the built ASGI app (A18) — `a2a-sdk` itself extracts nothing (see Gap 1). |
| settlement-agent | `a2a-sdk[telemetry]` + `opentelemetry-instrumentation-starlette` + `mcp` client | `a2a-sdk[telemetry]==0.3.7`, `mcp==1.9.4`, `opentelemetry-instrumentation-starlette==0.65b0` | Same shape as claims-agent (`POST /` ASGI entry span, `a2a.server.*` call-graph spans), plus outbound `httpx` CLIENT spans (`POST`) to payments-mcp | Only agent permitted to reach payments-mcp. Same `StarletteInstrumentor` fix as claims-agent for inbound extraction. |
| payments-mcp | FastMCP native | `fastmcp==4.0.3` | `tools/call propose_payment`, `tools/call execute_payment` | TracerProvider does **not** need to be installed before importing fastmcp on this version — `fastmcp.telemetry.get_tracer()` calls `get_tracer_provider()` fresh on every span, not once at import (measured; the brief's premise here was wrong for 4.0.3 — kept as a code convention anyway, not a correctness requirement). `FASTMCP_TELEMETRY_MODE=native` is set explicitly even though it is already the default. |
| payments-api | `otelhttp` | `otelhttp v0.66.0`, otel core `v1.41.0` | `POST /payments/propose`, `POST /payments/{paymentId}/execute`, `GET /payments/{paymentId}` | HTTP attribute spelling as emitted by the pinned version: **stable** — `http.request.method` / `http.response.status_code` / `http.route`, not the legacy `http.method`/`http.status_code`. `network.protocol.name` is **not** emitted by this otelhttp version (only `network.protocol.version` is); do not assert its presence. No `OTEL_SEMCONV_STABILITY_OPT_IN` is set or needed. |

**Architectural fact, applying to every component above:** none of them creates
a span of its own. Every span in this milestone comes from library
instrumentation — `StarletteInstrumentor`, `a2a-sdk`'s own `@trace_class`/
`@trace_function` decorators, `HTTPXClientInstrumentor`, FastMCP's native
tracer, `otelhttp`, and customer-ui's one hand-written `claim.submit` span
(the sole exception — it is the root, and nothing upstream of the browser
could have created it instead). There is no `get_tracer`/`start_span`/
`start_as_current_span` anywhere in this project's own `agents/`, `tools/`, or
`systems/` source; the only hit anywhere in the repo is a harness test probe.
This predicts where M1.2 changes shape: PydanticAI is expected to be the
first library that gives this project's own code spans to open directly,
rather than only ever wrapping calls from the outside.

## Conventions adopted

- **GenAI semconv** for model calls. Not yet exercised: M1.1 has no LLM.
- **MCP semconv** (`mcp.method.name`, `mcp.session.id`, `mcp.protocol.version`),
  with tool names in `gen_ai.tool.name`. `mcp.tool.name` does not exist.
- **HTTP semconv** for the Go system of record and A2A's transport spans.
- **MCP trace context via `params._meta`** (SEP-414), keys unprefixed. Not HTTP headers.

## Domain attributes we own

| Attribute | Set by | Propagation | Applies to |
|---|---|---|---|
| `northstar.claim.id` | claims-agent at intake | W3C baggage | Every span from claims-agent inward |
| `northstar.policy.number` | customer-ui root span | none | The root span only |

**Documented exemption:** the `customer-ui` root span does not carry
`northstar.claim.id` as an enriched attribute — the id is minted at intake, so the
browser span predates it. The browser sets it explicitly as its own attribute
instead. Correlation for that span is by trace ID.

**A second, deeper instance of the same exemption, one layer down:** of
claims-agent's own ~23 spans for a request, only 2 carry `northstar.claim.id` —
the outbound A2A `CLIENT` span and the final `EventQueue.enqueue_event` call,
both created inside the executor's own coroutine *after* the attach. The other
21 (the ASGI entry span and every `a2a.server.*` span) run in a separate
asyncio task that `a2a-sdk` 0.3.7 spawns via `asyncio.create_task()` before the
executor has attached anything to the ambient context — `create_task` copies
the context at call time, so nothing attached afterward can reach it
retroactively. Verified by an isolated reproduction (a minimal script driving
`DefaultRequestHandler` directly against a real `AgentExecutor`, with an
`InMemorySpanExporter`, no network). This is not a bug in this project's own
`correlation.py`/`executor.py`/`bootstrap.py`, and it is not something this
project routes around by patching `a2a-sdk`'s task-spawning — every one of
those 21 spans is third-party telemetry, and reaching into a2a-sdk internals
to force context across that boundary would be exactly the kind of hand-rolled
workaround this milestone avoids elsewhere. By contrast, settlement-agent
*receives* `northstar.claim.id` via an inbound baggage header, extracted by
`StarletteInstrumentor` at the ASGI boundary *before* any of its own internal
tasks spawn — every task spawned afterwards inherits it, so settlement-agent
shows 100% coverage (32/32 spans in one live capture) with no exceptions.
`tests/e2e/test_claim_correlation.py` asserts this real, capture-verified
boundary precisely, not a blanket "every span in every service" claim.

**Enrichment boundary:** the baggage span processor runs only in our own service
processes. Nothing is injected into telemetry emitted by infrastructure we do not
run in-process. In M2 that means agentgateway's own spans are correlated by trace
ID, not enriched.

## Content capture

Off. In M1.1 there is no model to capture. From M1.2, PydanticAI runs with
`InstrumentationSettings(include_content=False)` — recording everything a model
was told is itself a compliance problem in insurance. The setting is flipped
deliberately and visibly on stage, for one consequential tool.

## Confirmed convention gaps

For each: the evidence, whether upstream already has work in flight, and what we did.

### Gap 1: agent/task/context identity on A2A spans — and no inbound trace-context extraction at all

- **Evidence:** The reality is stricter than "only HTTP attributes." `a2a-sdk`
  0.3.7's own spans (both server-side, `agents/settlement-agent/NOTES.md` §3,
  and client-side, `agents/claims-agent/NOTES.md` §6) carry **no attributes of
  any kind** beyond OTel scope bookkeeping (`otel.scope.name`,
  `otel.scope.version`, `otel.status_code`) — no `gen_ai.operation.name`, no
  `gen_ai.conversation.id`, no `contextId`/`taskId`, no agent name, not even
  HTTP attributes on the entry point. Separately, `a2a-sdk` 0.3.7 does not
  extract inbound W3C trace context at all: an injected `traceparent` header
  is ignored outright and every request starts a fresh root trace (verified
  locally with `TestClient` and against the real deployed pod,
  `agents/settlement-agent/NOTES.md` §4). We fixed the extraction gap
  ourselves using real OTel framework instrumentation
  (`opentelemetry-instrumentation-starlette`, wrapping the built ASGI app —
  A18), not a workaround; the attribute gap is untouched by that fix and
  remains open.
- **Upstream check (searched the trackers directly, not assumed):**
  `open-telemetry/semantic-conventions-genai` has multiple **open** proposals
  addressing exactly this space — [#70 "Add semantic conventions for A2A
  (Agent-to-Agent) protocol"](https://github.com/open-telemetry/semantic-conventions-genai/issues/70),
  which explicitly proposes reusing `gen_ai.conversation.id` for A2A's
  `contextId` and `gen_ai.operation.name` for the A2A operation, matching our
  own position below; [#254 "Semantic Conventions for Agent-to-Agent (A2A)
  Protocol Telemetry"](https://github.com/open-telemetry/semantic-conventions-genai/issues/254);
  [#195 "semconv for a2a protocol"](https://github.com/open-telemetry/semantic-conventions-genai/issues/195);
  and [#447 "Unified OpenTelemetry model for tracking agent-to-agent
  interactions"](https://github.com/open-telemetry/semantic-conventions-genai/issues/447).
  On the extraction side, `a2aproject/a2a-python` has two related, already
  **closed** issues — [#168 "A2AClient should propagate trace
  context?"](https://github.com/a2aproject/a2a-python/issues/168) and [#281
  "EventQueue should propagate trace
  context?"](https://github.com/a2aproject/a2a-python/issues/281) — neither of
  which addresses the specific gap we found (that `A2AStarletteApplication`
  extracts nothing at the ASGI/HTTP boundary by default); we found no open
  issue on that exact point.
- **Our position:** GenAI semconv specifies `gen_ai.conversation.id` should be
  populated when an identifier is readily available, and explicitly forbids
  falling back to a UUID, trace ID or content hash. A2A supplies exactly such
  identifiers in `contextId` and `taskId`.
- **Action:** Not filed by this project. The semconv-side proposals above are
  already upstream and already argue our position; we have not opened a new
  one, and have not filed anything against `a2a-python` for the
  zero-attribute finding specifically. No `a2a.*`/`gen_ai.*` attribute was
  invented anywhere in this repo to fill the gap in the meantime.

### Gap 2: human-in-the-loop approval spans

- **Evidence:** No convention found covering an approval, escalation or
  human-interrupt step inside an agent trajectory.
- **Status:** Deferred to M3, when we have a working implementation to argue from.

### Gap 3: policy and autonomy decision spans

- **Evidence:** No convention found. `policy.decision` in M2 is ours to define.
- **Status:** Deferred to M2.

### Gap 4: baggage propagation across the MCP boundary

- **Evidence:** Neither. `northstar.claim.id` reaches payments-mcp on **0** of
  its tool-call spans, and payments-api on **0** of its spans, in a live
  capture of the full chain (`task-7-report.md`, "Where `northstar.claim.id`
  lands"). The `traceparent`/`tracestate` crossing is intact — this is a
  correlation gap, not a broken trace: FastMCP 4.0.3's own
  `extract_trace_context()`/`inject_trace_context()` (`fastmcp/telemetry.py`)
  copy only `traceparent`/`tracestate` out of `params._meta`; `baggage` is
  read and written nowhere in the installed package (`grep -rn baggage` over
  the package tree: zero matches). SEP-414 (which this project's own MCP
  trace-context convention already follows) documents `baggage` as one of the
  three unprefixed `_meta` keys, alongside `traceparent`/`tracestate`.
- **Upstream check:** No open FastMCP (`PrefectHQ/fastmcp`) issue found asking
  for `baggage` support in `_meta` specifically, despite several open issues
  about this exact file's trace-context handling (e.g. [#4358 "Incoming W3C
  traceparent header not propagated to FastMCP's internal
  spans"](https://github.com/PrefectHQ/fastmcp/issues/4358), [#3998 "FastMCP
  client does not propagate trace context on initialize and list
  methods"](https://github.com/PrefectHQ/fastmcp/issues/3998)) — none of them
  mention `baggage`. The MCP spec repo has a related but distinct open
  proposal, [SEP-2028 "Automatic `_meta` to HTTP header forwarding for
  distributed
  tracing"](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/2028),
  which is about forwarding `_meta` context (including `baggage`) to
  *downstream HTTP calls a server makes*, not about a server reading `baggage`
  out of `_meta` into its own OTel context in the first place — adjacent
  motivation, different gap.
- **Action:** Not filed. Recorded here as upstream-contribution evidence
  (`tools/payments-mcp/NOTES.md` §6, `agents/settlement-agent/NOTES.md` §11).
  A natural upstream fix would add a `baggage` key to both
  `extract_trace_context()` and `inject_trace_context()`, symmetric with how
  they already handle `traceparent`/`tracestate`. Not worked around: no code
  in this repo reaches into FastMCP's own `_meta` handling to bridge this
  gap. Pinned by `tests/e2e/test_claim_correlation.py::
  test_claim_id_is_on_every_mcp_hop_span` (`xfail(strict=True)`, narrowed to
  this one assertion per a review finding — a fixture-setup failure upstream
  of it now surfaces as an ordinary `ERROR`, not a false-quiet `xfailed`).

### Gap 5: FastMCP never emits `gen_ai.operation.name` on tool-call spans

- **Evidence:** FastMCP 4.0.3's `tools/call propose_payment`/`execute_payment`
  spans carry `gen_ai.tool.name` (e.g. `execute_payment`) correctly, but never
  `gen_ai.operation.name`, where the GenAI semconv specifies `execute_tool`
  for a tool invocation (`tools/payments-mcp/NOTES.md` §2). The sibling
  attribute is done properly; the operation classifier is simply absent.
- **Upstream check:** Already filed and open —
  [`PrefectHQ/fastmcp#3534` "Support GenAI semantic convention in open
  telemetry"](https://github.com/PrefectHQ/fastmcp/issues/3534), which asks
  for exactly `gen_ai.operation.name = execute_tool` on these spans (it also
  asks for `gen_ai.tool.name`, which 4.0.3 already emits — the issue predates
  or is unaware of that half being fixed).
- **Action:** No action needed from this project — the gap is already
  upstream, filed by someone else, and open. No bespoke `gen_ai.operation.name`
  was added to `server.py` to paper over it while waiting.
