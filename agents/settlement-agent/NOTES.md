# settlement-agent — verified SDK surfaces and emitted telemetry

This file is evidence for an upstream-contribution workstream (A8): every gap
between what a2a-sdk emits and what the OTel GenAI/A2A semantic conventions
define is recorded here, verbatim, rather than silently patched with an
invented attribute name or a hand-rolled span.

## 1. Version pins: brief's OTel pins conflict with `a2a-sdk[telemetry]==0.3.7`

The brief pinned `opentelemetry-sdk==1.29.0` and
`opentelemetry-exporter-otlp-proto-http==1.29.0` alongside
`a2a-sdk[telemetry]==0.3.7`. That combination is **unsatisfiable**:

```
× No solution found when resolving dependencies:
  Because a2a-sdk[telemetry]==0.3.7 depends on opentelemetry-sdk>=1.33.0
  and you require a2a-sdk[telemetry]==0.3.7, we can conclude that you
  require opentelemetry-sdk>=1.33.0.
  And because you require opentelemetry-sdk==1.29.0, we can conclude that
  your requirements are unsatisfiable.
```

Resolving `a2a-sdk[telemetry]==0.3.7` with the OTel packages otherwise
unpinned (`uv pip install 'a2a-sdk[telemetry]==0.3.7' 'mcp==1.9.4'
'uvicorn==0.34.0' 'opentelemetry-exporter-otlp-proto-http'`) lands on
`opentelemetry-api==1.44.0`, `opentelemetry-sdk==1.44.0`,
`opentelemetry-exporter-otlp-proto-http==1.44.0`. Adding
`opentelemetry-instrumentation-httpx` (needed per A13, see §4) at its own
newest pin (`0.50b0`, payments-mcp's pin) reintroduces the identical class of
conflict one level down (it depends on `opentelemetry-semantic-conventions
==0.50b0`, incompatible with `opentelemetry-sdk==1.44.0`'s
`==0.65b0`). Resolving all four together settles on
`opentelemetry-instrumentation-httpx==0.65b0`. **Ruled: pin the versions the
resolver actually settles on, not the brief's**, since this is a version-skew
problem, not an unknown-API problem — no architectural decision is implied,
just accurate pins:

```text
a2a-sdk[telemetry]==0.3.7
mcp==1.9.4
uvicorn==0.34.0
opentelemetry-sdk==1.44.0
opentelemetry-exporter-otlp-proto-http==1.44.0
opentelemetry-instrumentation-httpx==0.65b0
opentelemetry-instrumentation-starlette==0.65b0
```

(`opentelemetry-instrumentation-starlette` was added in fix round 1, A18 —
see §4a — and resolves at the same `0.65b0` line with zero additional
conflicts.)

A clean `uv venv --python 3.13 .venv && uv pip install -r requirements.txt`
resolves with zero conflicts against this file as committed.

**A16 client-side skew check (review nit 6 — demonstrated in this task but
not previously written down):** `mcp==1.9.4`'s `ClientSession`/
`streamablehttp_client` (this pin, not the brief's) was exercised live
against the deployed FastMCP 4.x `payments-mcp` server (A16) for all four
paths this agent depends on — `propose_payment` success, `execute_payment`
success, the 404 (payment not found) error shape, and the 409 (already
executed) error shape (§7 below has the verbatim `CallToolResult`s). All
four worked exactly as the MCP spec and `mcp_tools.py`'s handling expect.
**No protocol skew was found between the `mcp==1.9.4` client and the pinned
4.x FastMCP server** on the surface this agent actually uses (`initialize`,
`call_tool`, `CallToolResult.isError`/`structuredContent`). This narrows
Task 7's `params._meta` client-injection work to exactly that one gap (§5) —
the transport and tool-call plumbing underneath it is not itself a source of
version-skew risk.

## 2. a2a-sdk 0.3.7 API surface — verified (Step 1)

```
$ python -c "import a2a.server.apps as m; print([n for n in dir(m) if not n.startswith('_')])"
['A2AFastAPIApplication', 'A2ARESTFastAPIApplication', 'A2AStarletteApplication',
 'CallContextBuilder', 'JSONRPCApplication', 'jsonrpc', 'rest']

$ python -c "import a2a.server.request_handlers as m; print([n for n in dir(m) if not n.startswith('_')])"
['DefaultRequestHandler', 'GrpcHandler', 'JSONRPCHandler', 'RESTHandler',
 'RequestHandler', ...]

$ python -c "import a2a.server.tasks as m; print([n for n in dir(m) if not n.startswith('_')])"
['BasePushNotificationSender', 'DatabasePushNotificationConfigStore',
 'DatabaseTaskStore', 'InMemoryPushNotificationConfigStore', 'InMemoryTaskStore',
 'PushNotificationConfigStore', 'PushNotificationSender', 'ResultAggregator',
 'TaskManager', 'TaskStore', 'TaskUpdater', ...]

$ python -c "import a2a.utils.constants as c; print([(n, getattr(c, n)) for n in dir(c) if not n.startswith('_')])"
[('AGENT_CARD_WELL_KNOWN_PATH', '/.well-known/agent-card.json'),
 ('DEFAULT_RPC_URL', '/'),
 ('EXTENDED_AGENT_CARD_PATH', '/agent/authenticatedExtendedCard'),
 ('PREV_AGENT_CARD_WELL_KNOWN_PATH', '/.well-known/agent.json')]

$ python -c "import a2a.utils.telemetry as t; print([n for n in dir(t) if not n.startswith('_')])"
['SpanKind', 'StatusCode', 'trace_class', 'trace_function', ...]

$ env | grep -i DISABLE_OTEL_INSTRUMENTATION || echo "not set (correct)"
DISABLE_OTEL_INSTRUMENTATION not set (correct)
```

Every class name and the executor signature in the brief (Step 8) matched
exactly on the pinned version:

- `A2AStarletteApplication`, `DefaultRequestHandler`, `InMemoryTaskStore` — all
  present, same names.
- Well-known card path: `/.well-known/agent-card.json` — **matches the brief's
  assumption exactly**; the brief's warning that "the SDK renamed the
  well-known agent-card path between spec versions" did not apply at 0.3.7.
  No test/path change was needed (unlike the contingency the brief flagged).
- `AgentExecutor.execute(self, context, event_queue)` /
  `cancel(self, context, event_queue)` — confirmed via
  `inspect.getsource(a2a.server.agent_execution.AgentExecutor)`. Our
  implementation names the parameter `request_context` instead of `context`
  (per the brief's own reasoning, preserved verbatim in `executor.py`); Python
  does not enforce parameter names on an abstract method override, so this is
  cosmetic only.
- `pip show`/`uv pip list` confirms `a2a-sdk==0.3.7` installed with the
  `telemetry` extra's dependencies present
  (`opentelemetry-api>=1.33.0`, `opentelemetry-sdk>=1.33.0` both satisfied).
- `contracts/agent-cards/settlement-agent.json` (Step 2, `protocolVersion:
  "0.3.0"`) validates cleanly against `a2a.types.AgentCard.model_validate`.

**Confirmed real, not a no-op:** `a2a.server.request_handlers.default_request_handler`
and `.jsonrpc_handler` are both decorated `@trace_class(kind=SpanKind.SERVER)`
using `a2a.utils.telemetry`'s real `opentelemetry.trace` import (not the
package's `_NoOp` fallback, which only activates when OTel isn't importable at
all). So `a2a-sdk[telemetry]==0.3.7` genuinely instruments — see §3 for what,
precisely.

## 3. What a2a-sdk 0.3.7's telemetry actually emits — captured from a real trace

Captured against the deployed pod (`kubectl -n northstar-agents port-forward
svc/settlement-agent 19002:9002`), sending one real `message/send` settling a
claim, then reading the resulting trace back from Jaeger (deduplicated by
operation name; the full per-call dump is longer — 18-20 `a2a.server.*`
spans plus outbound httpx spans per request, the count varying slightly by
how many times `EventQueue.close` fires in a given capture — see the
per-capture note after the attribute map below).

### Span names, kinds

All spans are named with the fully-qualified Python module/class/method path
of whatever `a2a-sdk` internal call produced them, e.g.:

```
a2a.server.request_handlers.jsonrpc_handler.JSONRPCHandler.on_message_send   [kind=server]  <- root
a2a.server.request_handlers.default_request_handler.DefaultRequestHandler.on_message_send   [kind=server]
a2a.server.request_handlers.default_request_handler.DefaultRequestHandler._setup_message_execution   [kind=server]
a2a.server.request_handlers.default_request_handler.DefaultRequestHandler._register_producer   [kind=server]
a2a.server.request_handlers.default_request_handler.DefaultRequestHandler._run_event_stream   [kind=server]
a2a.server.request_handlers.default_request_handler.DefaultRequestHandler._cleanup_producer   [kind=server]
a2a.server.request_handlers.default_request_handler.DefaultRequestHandler._send_push_notification_if_needed   [kind=server]
a2a.server.events.in_memory_queue_manager.InMemoryQueueManager.create_or_tap   [kind=server]
a2a.server.events.in_memory_queue_manager.InMemoryQueueManager.close   [kind=server]
a2a.server.events.event_queue.EventQueue.enqueue_event   [kind=server]
a2a.server.events.event_queue.EventQueue.dequeue_event   [kind=server]
a2a.server.events.event_queue.EventQueue.task_done   [kind=server]
a2a.server.events.event_queue.EventQueue.clear_events   [kind=server]
a2a.server.events.event_queue.EventQueue.close   [kind=server]  (x2, one per queue reference)
a2a.server.events.event_consumer.EventConsumer.consume_all   [kind=server]
a2a.server.events.event_consumer.EventConsumer.agent_task_callback   [kind=server]
```

**All of these are `SpanKind.SERVER`** — including internal, in-process
helper methods that create/close a queue or track a producer task. There is
no `SpanKind.INTERNAL` used anywhere despite `trace_function`'s own default
being `SpanKind.INTERNAL` (see `a2a/utils/telemetry.py`'s docstring) — every
one of these is explicitly overridden to `SERVER` via `@trace_class(kind=
SpanKind.SERVER)` on both `DefaultRequestHandler` and `JSONRPCHandler`, and
apparently on the `events` submodule's classes too.

### Full attribute map — every `a2a.server.*` span in this capture (verbatim)

```json
{
  "internal.span.format": "otlp",
  "otel.scope.name": "a2a-python-sdk",
  "otel.scope.version": "1.0.0",
  "otel.status_code": "OK",
  "span.kind": "server"
}
```

**This is the complete attribute set on every a2a-sdk span in this capture,
with no exceptions** — the root span (`JSONRPCHandler.on_message_send`) is
identical to the innermost helper (`EventQueue.task_done`). (Per-capture note,
corrected during review: the exact count and the attribute keys present vary
slightly capture to capture — a fresh capture taken during fix round 2 found
18 `a2a.server.*` spans, 15 carrying all five keys above and 3 omitting
`otel.scope.version`; a review capture independently found the same 18/15/3
split. Read "20" elsewhere in this file, and "every single one", as referring
to a specific capture rather than a fixed constant — the substantive claim,
that no `gen_ai.*`/A2A-identity/`http.*` attribute is ever present on any of
these spans in any capture taken, holds across every capture regardless of
the exact count.) None of the following ever appear, on any span, anywhere in
this SDK's telemetry:

- `gen_ai.operation.name` (the spec expects `invoke_agent` for an agent
  invocation)
- `gen_ai.conversation.id` (the spec says A2A's `contextId`/`taskId` are
  natural fits — see §5)
- Any A2A-specific identity attribute at all: no `contextId`, no `taskId`,
  no `agent.name`, no method name, no message ID.
- No HTTP attributes either — despite the entry point being an HTTP POST to
  a JSON-RPC endpoint, `a2a-sdk`'s own spans carry zero `http.*`/`url.*`
  attributes (see §5 for why, and where the HTTP-facing spans do come from
  instead).

### Outbound httpx CLIENT spans (from `HTTPXClientInstrumentor`, this task's own bootstrap.py)

```json
{
  "http.request.method": "POST",
  "http.response.status_code": 202,
  "internal.span.format": "otlp",
  "network.peer.address": "payments-mcp.northstar-tools.svc.cluster.local",
  "network.peer.port": 8000,
  "network.protocol.version": "1.1",
  "otel.scope.name": "opentelemetry.instrumentation.httpx",
  "otel.scope.version": "0.65b0",
  "server.address": "payments-mcp.northstar-tools.svc.cluster.local",
  "server.port": 8000,
  "span.kind": "client",
  "url.full": "http://payments-mcp.northstar-tools.svc.cluster.local:8000/mcp"
}
```

(named `POST`/`GET`/`DELETE` per the HTTP verb — `POST` for the MCP JSON-RPC
calls, `GET` for the SSE response stream, `DELETE` for MCP session teardown —
all `kind=client`, all correctly nested under the same trace as the
`a2a.server.*` spans above, since they're emitted from the same process on
the same ambient OTel context.) These are **not** part of a2a-sdk's own
telemetry — they exist because this task's `bootstrap.py` adds
`HTTPXClientInstrumentor().instrument()` on top of what the brief's Step 7
sketch included, specifically to have something to measure for A13 (see §4).

### Point-by-point comparison against the OTel GenAI/A2A conventions

| Convention | Emitted by a2a-sdk 0.3.7? | Verdict |
|---|---|---|
| `gen_ai.operation.name = invoke_agent` on the agent-invocation span | Absent — zero `gen_ai.*` attributes anywhere | **GAP** |
| `gen_ai.conversation.id` (spec allows A2A `contextId`/`taskId` here) | Absent | **GAP** |
| Agent identity attributes (name, id) on any span | Absent | **GAP** |
| Task/context identity (`taskId`, `contextId`) on any span | Absent | **GAP** |
| Any attribute at all beyond OTel's own internal scope bookkeeping | Absent — see the verbatim dump above | **GAP** (total) |
| Meaningful, distinguishable span names | Yes — Python-call-graph-shaped names (`a2a.server....ClassName.method_name`) | Not a semconv attribute, but genuinely useful for reading a trace; not a gap, just not GenAI-shaped |
| Span kind reflects the HTTP-facing entry point | All internal spans are `SERVER`, including ones that are really just in-process helper calls; no distinction between the true request boundary and helper methods | Debatable design choice, not scored as a gap here — flagged for awareness |

**Summary: this confirms candidate gap 1 from the spec exactly, and goes
further than what the brief anticipated.** The brief expected "A2A spans
carry only HTTP/gRPC attributes, not task/context/agent identity" — the
verified reality is stricter: **a2a-sdk 0.3.7's own spans carry no
attributes of any kind**, HTTP/gRPC included. No bespoke attribute was added
anywhere to compensate; this is upstream-issue material as-is.

## 4. The load-bearing finding: a2a-sdk 0.3.7 does not extract inbound HTTP trace context

This is the most significant finding of this task, verified twice — once
locally with `starlette.testclient.TestClient` against an in-process app, and
once against the real deployed pod through the port-forwarded Service.

**Local proof** (`A2AStarletteApplication` built directly, `TestClient` with a
`ConsoleSpanExporter`, a hand-minted `traceparent` header sent on the POST):

```
INJECTED: trace_id=8123f499b0f141c497234dd5f89fb120  span_id=8ec970f29cae436b
...
{
  "name": "a2a.server.request_handlers.jsonrpc_handler.JSONRPCHandler.on_message_send",
  "context": {"trace_id": "0x6047e5e9e4186f8df4c33368e992ddac", "span_id": "0x4efa06f9f5ff92e1", ...},
  "parent_id": null,
  ...
}
```

The resulting root span's trace ID (`6047e5e9...`) has **no relationship** to
the injected trace ID (`8123f499...`), and `parent_id` is `null` — the
injected header was read by nothing.

**Real-cluster proof** (deployed pod, one A2A call with a `traceparent`
header sent via `httpx.post(..., headers={"traceparent": ...})`, trace looked
up in Jaeger both by the injected ID and by search):

```
INJECTED trace_id: 9309938991454833960210249664e14a  span_id: 4af77f0781894886
$ curl .../api/traces/9309938991454833960210249664e14a
{"data": null, "total": 0, "errors": [{"code": 404, "msg": "trace not found"}]}

$ curl '.../api/traces?service=settlement-agent&lookback=5m&limit=2'
trace d27e6099bd47df64e06f5b8a2f683c9a
  root op: a2a.server.request_handlers.jsonrpc_handler.JSONRPCHandler.on_message_send
  attrs: {internal.span.format: otlp, otel.scope.name: a2a-python-sdk,
          otel.scope.version: 1.0.0, otel.status_code: OK, span.kind: server}
```

**The injected trace never exists in Jaeger at all** — a completely different,
self-generated trace ID is used every time, regardless of what `traceparent`
header (if any) is sent. Searching `a2a-sdk`'s own source
(`a2a/server/apps/jsonrpc/*.py`) for `propagate`/`traceparent`/`extract`/
`TextMapPropagator` returns **zero matches**: the package genuinely contains
no code path that reads an inbound HTTP header into OTel context. The
`trace_class`/`trace_function` decorators call `tracer.start_as_current_span`
using whatever the *ambient* Python OTel context already is at call time —
for a fresh incoming HTTP request under plain `uvicorn`/Starlette with no
ASGI-level OTel instrumentation, that ambient context is empty, so every
request becomes a fresh root trace.

**Consequence for this task's e2e test:** A14's technique (mint a trace/span
ID, send `traceparent`, assert the entry span's `parent_id` equals the
injected span ID) **cannot be implemented for the A2A hop today** — not
because of a testing shortcut, but because there is nothing on the server
side to receive the injection. `tests/e2e/test_settlement_agent_trace.py`
therefore falls back to search-based lookup (`find_trace`, short 5-minute
lookback) for this one case, with the reasoning recorded in that file's
module docstring — this is squarely inside A14's own carve-out ("only where
the trace ID genuinely cannot be known up front"), just triggered by an SDK
gap rather than by Task 7's browser-origin case.

**This is worth an upstream issue distinct from the attribute-gap one in
§3**: `a2a-sdk[telemetry]` instruments the framework's own call graph but
does not wire up W3C trace-context extraction for the transport it serves
over (HTTP via Starlette). The rest of this section (as originally written,
before the fix round below) considered adding ASGI instrumentation ourselves
and deliberately did not, judging it a nontrivial architectural addition
beyond this task's scope. **The coordinator overruled that call in fix round
1 — see §4a.** The upstream gap in `a2a-sdk` itself is unchanged by that fix
and remains open: this section documents what `a2a-sdk[telemetry]` does and
does not do on its own, unmodified by anything this repo adds on top.

## 4a. Fix round 1 (A18): ASGI instrumentation fixes inbound extraction — verified by capture

**Ruling (A18):** hand-rolling spans to compensate for §4 would be wrong, but
instrumenting the ASGI application underneath `a2a-sdk` is real OTel
framework instrumentation, not a workaround, and settlement-agent is the
first of several A2A servers in this milestone — fixing it here means
Tasks 5/6 follow the same pattern instead of each hitting the same wall.

**Package verified against the installed `a2a-sdk`, not assumed:**
`A2AStarletteApplication.build()` (`a2a/server/apps/jsonrpc/starlette_app.py`)
returns a plain `starlette.applications.Starlette` instance (confirmed via
`inspect.getsource`), so `opentelemetry-instrumentation-starlette` — not the
lower-level generic `-asgi` package directly — is the applicable
instrumentation; it pulls in `opentelemetry-instrumentation-asgi==0.65b0` as
its own dependency, resolved cleanly (`uv pip install
opentelemetry-instrumentation-starlette` → `opentelemetry-instrumentation-
starlette==0.65b0`, `opentelemetry-instrumentation-asgi==0.65b0`, no
conflicts against the pins in §1). Pinned in `requirements.txt`:
`opentelemetry-instrumentation-starlette==0.65b0`.

**Where it's applied:** `StarletteInstrumentor().instrument_app(app)` in
`main.py`, right after `A2AStarletteApplication(...).build()` returns — the
per-app `instrument_app` API is used (not the global `.instrument()`) since
the `Starlette` instance doesn't exist until `build()` returns.

**Propagators registered explicitly (A18):** `bootstrap.py` now calls
`opentelemetry.propagate.set_global_textmap` with a `CompositePropagator`
of `TraceContextTextMapPropagator` + `W3CBaggagePropagator`, matching
`systems/payments-api/telemetry.go`'s
`propagation.NewCompositeTextMapPropagator(propagation.TraceContext{},
propagation.Baggage{})`. This is already OTel Python's own default
(`OTEL_PROPAGATORS` defaults to `"tracecontext,baggage"`), so this call
changes no behaviour — it makes the choice explicit in code, the same way
the Go side does, rather than leaving it to an implicit default.

**Verified by capture — before and after, same technique as §4:**

Before (§4, unchanged, kept as evidence): injected `traceparent`
`00-9309938991454833960210249664e14a-4af77f0781894886-01` on the real
deployed pod → entry span `d27e6099bd47df64e06f5b8a2f683c9a` (unrelated trace
ID, `parent_id: null`).

After (this fix, real deployed pod, fresh capture):

```
injected trace_id 63748f22e3f04ed7be13c115a279ab86  span_id 48f523bbcc714961
...
settlement-agent :: POST / [kind=server] parent=48f523bbcc714961
{
  "client.address": "127.0.0.1", "client.port": 48050,
  "http.request.method": "POST", "http.response.status_code": 200,
  "http.route": "/", "internal.span.format": "otlp",
  "network.protocol.version": "1.1",
  "otel.scope.name": "opentelemetry.instrumentation.starlette",
  "otel.scope.version": "0.65b0", "server.address": "127.0.0.1:9002",
  "server.port": 9002, "span.kind": "server", "url.path": "/",
  "url.scheme": "http", "user_agent.original": "python-httpx/0.28.1"
}
trace_id (jaeger): 63748f22e3f04ed7be13c115a279ab86
```

The entry span's trace ID now **equals** the injected trace ID, and its
`parent_id` **equals** the injected span ID — exactly what A14 requires, and
the opposite of §4's finding. `tests/e2e/test_settlement_agent_trace.py::
test_settlement_agent_entry_span_is_parented_to_the_injected_context` asserts
this directly against a live deployment. A negative control was also run as
part of this fix round: temporarily sending no `traceparent` header
reproduces §4's original symptom exactly (`wait_for_trace_id` times out
after 90s, `404 Client Error: Not Found` on every poll of the injected trace
ID) — confirming the test would fail if this fix ever regressed. The file
was restored to its committed state immediately after and `git diff` showed
no residual change.

**New span, new semconv spelling to check (A13):** `StarletteInstrumentor`'s
`POST /` span is itself an HTTP-facing span, subject to the same legacy/
stable spelling question as the outbound httpx spans in §6. Measured the
same way: default is legacy (`http.method`, `http.url`, `http.status_code`,
`net.peer.ip`); with `OTEL_SEMCONV_STABILITY_OPT_IN=http` (already set on
`deploy/k8s/settlement-agent.yaml` for the outbound hop) the same span comes
out with `http.request.method`, `http.response.status_code`, `server.address`,
`url.path`, `client.address` — confirmed in the capture above, no manifest
change needed since the env var applies process-wide.

**Double-counting check (explicitly asked for):** compared the full span set
before and after. `StarletteInstrumentor` adds four *new*, distinctly-named
spans per request (`POST /`, `POST / http receive`, `POST / http send` ×2)
that did not exist before; every one of the pre-existing `a2a.server.*`
spans is still present, unchanged in name, kind, and (empty) attribute
content, just now correctly nested one level deeper.

**Correction (review Finding 4):** an earlier draft of this section
misstated the nesting as "under `POST /` via `JSONRPCHandler.on_message_send`,
whose own parent is now the ASGI `http receive` span rather than `null`" —
that is wrong on two counts, confirmed by re-capturing the live span tree
(`JSONRPCHandler.on_message_send` span id `a259bdf2fa14c263`, `POST /` span
id `a63ae041e614173c`, `POST / http receive` span id `8d45f0a811b58af4`,
trace `822c17bb87414069a08021fe8f71b2e4`):
`JSONRPCHandler.on_message_send`'s `parent_id` is `a63ae041e614173c` — a
**direct** child of `POST /` itself, not of `http receive`; and
`POST / http receive` has **zero** children of its own (queried by parent_id
over the same trace) — it is a sibling leaf span next to `POST /`'s other
descendants, not an intermediate parent of anything. The conclusion the
sentence was supporting is correct and unaffected: `a2a-sdk`'s spans nest one
level deeper under the new ASGI root, with no duplicate span for the same
logical operation. **No duplicate spans for the same logical operation were
found** — this is two complementary instrumentation layers (ASGI transport
vs. a2a-sdk's internal call graph), not double counting of one operation.
Reported here per instruction, since the coordinator asked to know either
way.

**What this fix does and does not address, precisely:** it fixes *context
extraction* — an inbound `traceparent` now correctly becomes the ambient
OTel context a2a-sdk's own spans nest under. It does **not** give a2a-sdk's
own spans any attributes: every `a2a.server.*` span still carries only
`{internal.span.format, otel.scope.name, otel.scope.version,
otel.status_code, span.kind}` (§3, re-verified in this round's captures,
identical). The `gen_ai.operation.name`/`gen_ai.conversation.id`/task-identity
gap is **untouched** and remains open upstream-issue material — see §3 and
the note at the top of this section. Nothing new (`a2a.*`, `gen_ai.*`, or
otherwise) was invented to fill it.

## 5. Trace-context propagation, hop by hop — the true starting point after this fix round

- **A2A client → settlement-agent: now propagates.** Fixed in §4a: an
  inbound `traceparent` is extracted by `StarletteInstrumentor` and produces
  a real child span, not a fresh root. Historical note: before this fix
  round, this hop did not propagate at all (§4) — kept for the record since
  §4's finding remains true of `a2a-sdk[telemetry]` on its own, unmodified.
- **settlement-agent (internal) is coherent.** All of settlement-agent's own
  spans — the ASGI-level spans, the `a2a.server.*` spans (§3), and this
  agent's own outbound httpx CLIENT spans to payments-mcp — correctly share
  one trace with no orphans beyond the expected synthetic-root exception
  (A17; the entry span's declared parent is the externally-injected span ID,
  which never itself lands in Jaeger). Verified against a real deployed
  trace via `tests/e2e/test_settlement_agent_trace.py`.
- **settlement-agent → payments-mcp: does NOT propagate, unchanged, by
  deliberate scope decision (coordinator's ruling on fix round 1):** for two
  independent reasons that compound:
  1. `mcp_tools.py` uses `mcp==1.9.4`'s high-level `ClientSession.call_tool()`
     (per this task's explicit scope — production `_meta` injection is
     Task 7's job via the shared `correlation.py`, A6, because more than one
     service needs it), which has no way to set `params._meta`, and does not
     do so automatically.
  2. FastMCP (payments-mcp, Task 3's findings, reconfirmed here) only ever
     extracts trace context from `params._meta` — never from HTTP headers —
     so even though this agent's outbound httpx call *does* carry a
     `traceparent` HTTP header (via `HTTPXClientInstrumentor`'s ordinary
     ambient-context injection, and now carrying the real externally-injected
     trace rather than a self-generated one), payments-mcp has nothing that
     reads it.
  Verified directly, post-fix: after a real settlement call with an injected
  `traceparent`, payments-mcp's `tools/call propose_payment`/`tools/call
  execute_payment` spans still do **not** appear anywhere in settlement-agent's
  trace — `trace.services() == {"settlement-agent"}` only. This is expected
  and intentionally left as `xfail(strict=True)` in the e2e test (§9).
- **payments-mcp → payments-api: propagates correctly** (Task 3's own
  finding, unaffected by anything in this task — that hop is ordinary
  W3C-header-based httpx→otelhttp propagation and was already proven intact).

**Net, after this fix round: trace context now propagates *into*
settlement-agent from its A2A caller, but not *out* to payments-mcp.** The
chain is connected from the browser/A2A-client side through settlement-agent,
and breaks exactly once, at the MCP boundary — a single, precisely located,
deliberately deferred gap rather than two compounding ones. Task 7's MCP
client `_meta` injection is the one remaining piece.

## 6. Outbound HTTP semconv spelling (A13) — measured, not assumed

`opentelemetry-instrumentation-httpx==0.65b0`'s default spelling, captured
with a local `ConsoleSpanExporter` against a throwaway HTTP server (no
opt-in set):

```
http.method       = GET
http.url          = http://127.0.0.1:.../foo
http.status_code  = 200
```

Legacy spelling — same conclusion as payments-mcp's own measurement (Task 3),
despite the newer instrumentation version (`0.65b0` vs. payments-mcp's
`0.50b0`) — the legacy default hasn't changed. With
`OTEL_SEMCONV_STABILITY_OPT_IN=http` set, the same call:

```
http.request.method       = GET
url.full                  = http://127.0.0.1:.../foo
server.address            = 127.0.0.1
server.port               = <port>
network.peer.address      = 127.0.0.1
network.peer.port         = <port>
http.response.status_code = 200
network.protocol.version  = 1.0
```

**`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set on settlement-agent's deployment
manifest** (`deploy/k8s/settlement-agent.yaml`), and confirmed against the
real deployed pod's outbound calls to payments-mcp (§3's outbound span dump
above): `http.request.method`, `http.response.status_code`, `url.full`,
`server.address` all present; the legacy `http.method`/`http.url`/
`http.status_code` are absent. This matches payments-mcp's own already-stable
spelling on its outbound hop to payments-api (A12/A13), so the chain — where
it does connect — uses one consistent HTTP convention, not a mix.

## 7. MCP tool-call error surfacing — `mcp_tools.py`

Verified against the real deployed payments-mcp, both error cases the task
calls out:

- **404 (payment not found)**: `execute_payment` with a nonexistent ID comes
  back as `CallToolResult(isError=True, content=[TextContent(text="Error
  calling tool 'execute_payment': Client error '404 Not Found' for url
  '.../payments/PAY-999999/execute'\n...")])`. `structuredContent` is not
  merely `None` here — the field is entirely **absent** from the response
  (accessing it directly raises `AttributeError`; `mcp.types.CallToolResult`
  declares no `structuredContent` field, so pydantic exposes it only as an
  "extra" attribute when the server actually sends one).
- **409 (already executed)**: identical shape, `Client error '409 Conflict'`.
- **Successful calls**: `structuredContent` **is** present as an extra
  field (`model_fields_set` includes it), matching the brief's `_unwrap`
  assumption.

`mcp_tools._unwrap` was extended (beyond the brief's sketch) to check
`result.isError` first and raise a new `ToolCallError(detail)` carrying the
tool's own error text, rather than falling through to the generic "tool call
returned no structured content" `ValueError`. Reason: an unhandled exception
from `AgentExecutor.execute()` is turned into a JSON-RPC error response by
`a2a-sdk`'s own request handler (verified: `{"error": {"code": -32603,
"message": "<str(exc)>"}}`), so the *message* on the raised exception is what
the caller actually sees — it needs to be the real 404/409 text, not a raw
`CallToolResult` repr.

**A wrinkle found while verifying this**: `mcp.client.streamable_http`'s
`streamablehttp_client` and `ClientSession` are each backed by their own
`anyio` `TaskGroup`. An exception raised inside the `async with` body (like
`ToolCallError`) is wrapped in a `BaseExceptionGroup` once per nesting level
on the way out — confirmed empirically as a **doubly**-nested group for a
single `ToolCallError`. Using `except* ToolCallError: raise
eg.exceptions[0]` does **not** fix this: PEP 654 always re-wraps whatever a
new exception an `except*` handler raises, by design, so the caller would
still see `"unhandled errors in a TaskGroup (1 sub-exception)"` instead of
the real message. `_call()` therefore catches `BaseExceptionGroup` with a
plain `except` and manually unwraps to the first leaf exception
(`_leaf_exception`) before re-raising. Verified end to end: a direct
`PaymentsTools.execute("PAY-999999")` call now raises a plain
`ToolCallError` with the clean 404 text, not a wrapped group.

## 8. `message/send` returns only the first `Message` event — a2a-sdk 0.3.7, verified

The brief's Step 8 `executor.py` sketch enqueues three separate
`new_agent_text_message(...)` events per settlement (an "Assessed
settlement" narration, a "Proposed payment X" narration, then the final JSON
result). Deployed and driven with a real `message/send` call, the actual A2A
response only ever contained the **first** message:

```json
{"result": {"parts": [{"kind": "text", "text": "Assessed settlement: Claim CLM-TEST-... is covered..."}]}}
```

and the pod logs showed `Queue is closed. Event will not be enqueued.` twice
— once for each of the two later messages. Root cause, confirmed by reading
`a2a.server.tasks.result_aggregator.ResultAggregator.consume_and_break_on_interrupt`:

```python
async for event in event_stream:
    if isinstance(event, Message):
        self._message = event
        return event, False   # <- returns immediately on the FIRST Message
    ...
```

**Any bare `Message` event is treated as the final, singular answer for a
blocking `message/send` call — the first one wins, unconditionally.** This
is not a bug in this task's code; it reproduces identically with the brief's
own executor.py sketch, and is a real, documented (if surprising) property of
`a2a-sdk 0.3.7`'s blocking request handling — plain `Message` events are
apparently intended for a single terminal answer, with `TaskStatusUpdateEvent`
being the mechanism for genuine interim progress. **Fixed here**: `executor.py`
now enqueues exactly **one** `Message` — the final JSON result — and logs the
intermediate narration via the standard `logging` module instead of the event
queue, so the A2A response actually satisfies this agent's documented
interface (`{claimId, paymentId, amount, status, narrative}`). Verified after
the fix: the same real call now returns that full JSON object as the
`message/send` response.

## 9. `tests/e2e/test_settlement_agent_trace.py` — evolution across both rounds

**Round 1 (pre-A18):** given §4 (A2A hop never propagated) and the MCP hop
never propagating (by scope decision), the brief's original Step 10 test —
which asserted `{"settlement-agent", "payments-mcp", "payments-api"} <=
services()` for a single trace, found via a plain `wait_for_trace` search —
could not pass against the real system, and forcing it to pass would have
required either fabricating instrumentation beyond the brief or
pre-implementing Task 7's MCP client injection, both out of scope. That
round's file passed 3 real assertions (card served; internal trace
coherence; outbound MCP-transport tracing) and marked all three
cross-service assertions `xfail(strict=True)`.

**Round 2 (A18, this fix round):** with the A2A hop now fixed (§4a), the file
was rewritten to use the real thing A14 asks for rather than the search-based
fallback:
- `settlement_trace` now mints its own trace/span ID, sends it as a
  `traceparent` header (an A2A/HTTP boundary — a header is correct here,
  unlike the MCP boundary below), and looks it up with `wait_for_trace_id`,
  never search.
- `test_settlement_agent_entry_span_is_parented_to_the_injected_context`
  replaces the old internal-coherence check with the real A14 assertion:
  the entry span's `parent_id` equals the injected span ID, and the trace ID
  matches.
- `test_settlement_agent_trace_has_no_unexpected_orphans` applies A17's
  precise exclusion: the synthetic root's span ID never lands in Jaeger, so
  the entry span is an unavoidable orphan — excluded by *both* its operation
  name and its `parent_id`, not a count tolerance, so a genuine propagation
  defect anywhere else in the trace still fails this test.
- The MCP-hop assertions (`test_all_four_services_are_in_one_trace`,
  `test_mcp_tool_calls_are_present`, `test_settlement_agent_span_precedes_
  the_payment`) remain `xfail(strict=True)`, per the coordinator's explicit
  ruling to keep this hop as-is and defer to Task 7's shared
  `correlation.py` (A6). `strict=True` still means the day that gap closes,
  the corresponding test becomes an unexpected pass and fails the run,
  forcing the marker's removal rather than a silent stale pass.

**Negative-control verification (this fix round):** before committing, the
fixture's `traceparent=traceparent` argument was temporarily changed to
`traceparent=None` and the suite re-run. Result: `wait_for_trace_id` timed
out after 90s with `404 Client Error: Not Found` on every poll — the
injected trace ID this test asserts about is never created without the
header, so the newly-added assertions cannot pass vacuously; they fail
exactly the way they should if the A18 fix ever regressed. The file was
restored to its exact prior content immediately afterward (`diff` confirmed
byte-identical).

The round-1 residual risk note (ad hoc manual calls polluting a
search-based lookup) no longer applies to the A2A-hop assertions, since
they're now looked up by exact injected ID rather than search — that risk
was specific to the pre-A18 fallback and is retired along with it.

**Fix round 2 (A19 / review Findings 1-4, nits 6/8/9/10):** the two hardest
correctness fixes in this task — the single-`Message` executor change and
the `BaseExceptionGroup` unwrap in `mcp_tools.py` — had no test that would
fail if they regressed (review Findings 1 and 2). Fixed:

- `tests/e2e/test_settlement_agent_trace.py` gained
  `test_message_send_returns_the_full_settlement_reply`, which parses the
  actual `message/send` JSON reply and asserts `claimId`, `paymentId`,
  `amount`, `currency`, `status == "executed"`, and a non-empty `narrative`.
  Confirmed to fail against the brief's original three-`enqueue_event`
  executor (verbatim failure output in `task-4-report.md`'s fix-round-2
  section), restoring the file exactly afterward.
- `agents/settlement-agent/tests/test_mcp_tools.py` (new) pins `_unwrap` and
  `_leaf_exception`/`_call`'s exception-group handling with pure-unit and one
  async test; confirmed to fail if either fix is reverted (same report
  section has the verbatim failures).
- `test_agent_card_is_served` now diffs the full served card against
  `contracts/agent-cards/settlement-agent.json` rather than checking two
  fields (Finding 3), so a served/contract drift fails here instead of
  shipping silently.
- `_leaf_exception` now prefers a `ToolCallError` leaf over blind
  `exceptions[0]` (nit 9), and is covered by
  `test_leaf_exception_prefers_a_tool_call_error_over_other_siblings`.
- The e2e trace-ID comparison now zero-pads rather than double-`lstrip`s
  (nit 10's first point); the span-ordering timing concern (nit 10's second
  point) was assessed and left as-is — see task-4-report.md's fix-round-2
  section for the reasoning.
- §4a's misstated span parentage (Finding 4) and this file's overstated
  "20 spans, no exceptions" phrasing (nit 5) are corrected in place above,
  re-verified against a fresh live capture taken during this fix round.
- `bootstrap.py`'s stale "if the measurement shows it is needed" hedge
  (nit 8) is now a statement of fact, since the measurement was done and the
  opt-in is set.
