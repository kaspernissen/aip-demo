# payments-mcp: SDK behaviour and emitted telemetry

What this service relies on in FastMCP, what it emits, and where FastMCP falls
short of the OpenTelemetry MCP semantic conventions. Gaps are recorded as
found; no attribute has been invented to hide one. Section numbers are
referenced from other files.

## 1. FastMCP version

`fastmcp==4.0.3`. Earlier lines have no OpenTelemetry support: 2.11 to 2.13 have
no `fastmcp.telemetry` module at all; it appears in 3.x and is fullest in 4.x.

- `FastMCP.tool(annotations={...})` accepts a plain dict, stored as a
  `ToolAnnotations` model with snake_case attributes and camelCase wire aliases
  (`destructiveHint: true` on `execute_payment`, checked in `tests/test_tools.py`).
- `FastMCP.list_tools()` returns a list of `FunctionTool`.
- Telemetry is controlled by `fastmcp.settings.telemetry_mode`, env var
  `FASTMCP_TELEMETRY_MODE`: `native` (default; creates MCP spans and propagates
  context through request `_meta`), `propagation_only` or `off`. The deployment
  sets `native` explicitly. `DISABLE_OTEL_INSTRUMENTATION` is never set.
- `fastmcp.telemetry.get_tracer()` looks up the tracer provider on every span
  creation, so importing FastMCP before the provider is configured is harmless.
  `server.py` still imports `bootstrap` first by convention.
- A standalone `mcp` client interoperates with this server (initialize,
  list_tools and call_tool all work).

## 2. What FastMCP emits versus the MCP conventions

Spans per tool call: `tools/call propose_payment` and `tools/call execute_payment`
(SERVER, service payments-mcp) plus an outbound `POST` CLIENT span to payments-api.

Attributes on a `tools/call` span: `fastmcp.component.key`,
`fastmcp.component.type`, `fastmcp.provider.type`, `fastmcp.server.name`,
`fastmcp.span.seam`, `gen_ai.tool.name`, `mcp.method.name`,
`mcp.protocol.version`, `mcp.session.id`, plus scope bookkeeping.

Matches the conventions: span name `{mcp.method.name} {target}`, tool name in
`gen_ai.tool.name` (there is no `mcp.tool.name`), `mcp.method.name`,
`mcp.protocol.version`, `mcp.session.id`.

Gap: FastMCP does not set `gen_ai.operation.name` (`execute_tool`) on tool-call
spans, although it sets the sibling `gen_ai.tool.name`. No substitute attribute
is added in `server.py`. The `fastmcp.*` attributes are additive and conflict
with nothing.

## 3. Outbound HTTP semconv spelling

`opentelemetry-instrumentation-httpx` emits the legacy names (`http.method`,
`http.url`, `http.status_code`) by default. `OTEL_SEMCONV_STABILITY_OPT_IN=http`
(set in `deploy/k8s/payments-mcp.yaml`) switches to `http.request.method`,
`http.response.status_code`, `url.full`, `server.address`/`server.port`,
`network.peer.address`/`network.peer.port` and `network.protocol.version`. This
matches payments-api's otelhttp spelling on the same hop.

## 4. MCP trace context crosses into payments-mcp

FastMCP extracts `traceparent` from `params._meta` and parents its `tools/call`
span there; everything downstream (the outbound httpx call and payments-api's
server span) joins through ordinary HTTP header propagation.
`tests/e2e/test_payments_mcp_trace.py` injects its own `traceparent` into
`_meta` with the low-level `ClientSession.send_request()` and asserts the parent
link. MCP does not carry trace context in HTTP headers.

## 5. Trace assembly timing

`wait_for_trace_id` returns as soon as the named span is visible, not when spans
from another process have also been exported. The e2e `executed_call` fixture
therefore polls the trace for up to 30 seconds until payments-api's span is
present.

## 6. `_meta` extraction never reads `baggage`

FastMCP's `extract_trace_context()` copies only `traceparent` and `tracestate`
from `_meta`; `baggage` is never read, and `inject_trace_context()` omits it
too. The `tools/call` spans join the caller's trace but never carry
`northstar.claim.id`, and this server's outbound call to payments-api carries no
`baggage` header. No workaround is added in `server.py`. See
`agents/settlement-agent/NOTES.md` section 11 for the client side.
