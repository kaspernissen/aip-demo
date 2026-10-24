# settlement-agent: SDK behaviour and emitted telemetry

What this service relies on in `a2a-sdk` 0.3.7 and the MCP client, what it
emits, and where upstream libraries fall short of the OpenTelemetry semantic
conventions. Gaps are recorded as found; no attribute or span has been invented
to hide one. Section numbers are referenced from other files.

## 1. Version pins

`a2a-sdk[telemetry]==0.3.7` requires `opentelemetry-sdk>=1.33.0`, so the
OpenTelemetry packages are pinned to what the resolver settles on
(`opentelemetry-sdk` / `-exporter-otlp-proto-http` 1.44.0,
`opentelemetry-instrumentation-httpx` / `-starlette` 0.65b0). Older OTel pins
are unsatisfiable alongside that a2a-sdk. `requirements.txt` explains why
a2a-sdk is held at 0.3.7 and why starlette/sse-starlette are pinned explicitly.

## 2. a2a-sdk 0.3.7 surface in use

- `A2AStarletteApplication`, `DefaultRequestHandler`, `InMemoryTaskStore`.
- Agent card path `/.well-known/agent-card.json`; the committed card
  (`contracts/agent-cards/settlement-agent.json`, protocol `0.3.0`) validates
  against `a2a.types.AgentCard`.
- `AgentExecutor.execute(context, event_queue)` / `cancel(...)`.
- `a2a.utils.telemetry` is real instrumentation (`@trace_class(kind=SERVER)` on
  the request handlers), not a no-op, unless `DISABLE_OTEL_INSTRUMENTATION` is
  set. It is not set.

## 3. What a2a-sdk emits

Roughly 18 to 20 `a2a.server.*` spans per `message/send`, named after the Python
call graph (`a2a.server.request_handlers.jsonrpc_handler.JSONRPCHandler.on_message_send`,
`...DefaultRequestHandler._run_event_stream`, `...EventQueue.enqueue_event`, and
so on). All are `SERVER` kind, including in-process helpers.

Their entire attribute set is `internal.span.format`, `otel.scope.name`,
`otel.scope.version` (on most), `otel.status_code` and `span.kind`. There is no
`gen_ai.operation.name`, no `gen_ai.conversation.id`, no task, context or agent
identity, and no `http.*` attribute. This is a gap against the GenAI/A2A
conventions and is upstream-issue material.

Outbound calls to payments-mcp come from `HTTPXClientInstrumentor` (CLIENT
spans named `POST`/`GET`/`DELETE`), not from a2a-sdk.

## 4. Inbound trace context

a2a-sdk 0.3.7 does not extract inbound W3C trace context: its spans use
whatever context is ambient, so every request starts a new trace regardless of
the `traceparent` header. The package contains no extraction code.

Fix: `main.py` calls `StarletteInstrumentor().instrument_app(app)` on the app
returned by `A2AStarletteApplication(...).build()` (a plain Starlette
instance), and `bootstrap.py` sets the global propagator to
`CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])`.
The entry span (`POST /`) is then a child of the injected span, and a2a-sdk's
spans nest under it. This fixes extraction only; the missing span attributes
(section 3) remain.

## 5. Propagation, hop by hop

- A2A caller to settlement-agent: propagates (section 4).
- Inside settlement-agent: one coherent trace.
- settlement-agent to payments-mcp (via agentgateway): propagates through
  `params._meta` (sections 10 and 11).
- payments-mcp to payments-api: propagates over HTTP headers.

The `northstar.claim.id` attribute does not cross the MCP hop (section 11).

## 6. HTTP semconv spelling

`opentelemetry-instrumentation-httpx` and `-starlette` emit the legacy
attribute names (`http.method`, `http.url`, `http.status_code`) by default.
`OTEL_SEMCONV_STABILITY_OPT_IN=http` on the deployment switches them to the
stable names (`http.request.method`, `url.full`, `server.address`, ...), which
matches payments-mcp and payments-api, so every hop uses one convention.

## 7. MCP tool-call errors (`mcp_tools.py`)

A failed tool call (payments-api 404 or 409) arrives as a `CallToolResult` with
`isError`. `_unwrap` raises `ToolCallError` with the tool's own text so the real
reason reaches the caller instead of a generic "no structured content" error.

The MCP client session and transport each run an `anyio` task group, so an
exception raised inside the `async with` body is wrapped in nested
`BaseExceptionGroup`s. `except*` cannot fix this, because PEP 654 re-wraps
whatever a handler raises. `_call()` therefore catches `BaseExceptionGroup`
and unwraps to the first leaf exception by hand (`_leaf_exception`).

## 8. `message/send` returns only the first `Message`

For a blocking `message/send`, `ResultAggregator.consume_and_break_on_interrupt`
treats the first bare `Message` event as the final answer and closes the queue;
later events are dropped ("Queue is closed. Event will not be enqueued").
`executor.py` therefore enqueues exactly one `Message` (the final JSON result)
and logs intermediate narration with `logging`.

## 9. End-to-end trace test

`tests/e2e/test_settlement_agent_trace.py` mints its own trace and span ID,
sends them as `traceparent`, and asserts the entry span is parented to the
injected span and the trace has no unexpected orphans. Without the header the
injected trace never exists, so the assertions cannot pass vacuously.

## 10. Client-side `params._meta` injection

`mcp_tools.py` builds its own `CallToolRequest` and calls
`ClientSession.send_request()`, because `ClientSession.call_tool()` has no
`_meta` parameter and never sets one. The carrier is produced by
`opentelemetry.propagate.inject()` on the ambient context, so it holds
`traceparent`, `tracestate` if present, and `baggage`. No claim-specific code
lives in `mcp_tools.py`. An optional `meta` argument on `call_tool()` would
remove the need to drop to the low-level API.

## 11. FastMCP does not carry `baggage` through `_meta`

FastMCP 4.0.3's `extract_trace_context()` copies only `traceparent` and
`tracestate` out of `_meta`; `baggage` is never read, and
`inject_trace_context()` has the same omission. The `tools/call` spans on
payments-mcp therefore join the caller's trace but do not carry
`northstar.claim.id`, and neither does the payments-api span below them.
`tests/e2e/test_claim_correlation.py::test_claim_id_is_on_every_mcp_hop_span`
pins this. The fix upstream would add a `baggage` key to both functions.
