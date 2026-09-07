# payments-mcp — verified SDK surfaces and emitted telemetry

This file is evidence for an upstream-contribution workstream (A8): every gap
between what FastMCP emits and what the OTel MCP semantic conventions define
is recorded here, verbatim, rather than silently patched with an invented
attribute name.

## 1. FastMCP version: bumped from the brief's `2.11.3` to `4.0.3` (amendment A16)

Empirical check (`python -c "import fastmcp.telemetry"`) found that **`fastmcp==2.11.3`
has no OpenTelemetry support at all**: no `fastmcp.telemetry` module, and zero
occurrences of `telemetry`/`opentelemetry`/`OTEL` anywhere in the installed package
tree. Bisecting the version line:

| Version | `fastmcp.telemetry` module |
|---|---|
| 2.11.3 (brief's pin) | absent |
| 2.12.0, 2.13.0 | absent |
| 2.14.0 | (untested — unrelated broken `pydantic_settings` dependency) |
| 3.0.0 | present (`SpanKind`, `telemetry_mode`, `native_spans_enabled` land later) |
| 4.0.3 (current stable) | present, fullest API |

Ruled by the coordinator (amendment A16): pin `fastmcp==4.0.3`, the current stable
release, since this milestone's whole point is verifying the most complete real
implementation of native MCP telemetry, not staying close to a brief already known
to be wrong.

### API surface actually verified on 4.0.3 (Python 3.13.5)

```
FastMCP.tool signature includes: annotations: 'ToolAnnotations | dict[str, Any] | None' = None
FastMCP.run signature: (self, transport: 'Transport | None' = None, show_banner: 'bool | None' = None, **transport_kwargs) -> None
Transport = Literal["stdio", "http", "sse", "streamable-http"]   # "http" still valid, as in the brief
```

- `annotations={"destructiveHint": True, ...}` (plain dict) is still accepted, same as
  the brief. It is stored internally as a `ToolAnnotations` pydantic model whose
  **Python attributes are snake_case** (`destructive_hint`), with camelCase
  **aliases for the wire format**. `tool.annotations.model_dump(by_alias=True)`
  returns `{"destructiveHint": true, ...}` — the exact shape advertised to MCP
  clients. `destructiveHint: true` on `execute_payment` is confirmed present in the
  advertised schema (see `tests/test_tools.py`).
- **`FastMCP.get_tools()` → `FastMCP.list_tools()`** in 3.x+, and it returns a
  `list[FunctionTool]` instead of a `dict[str, FunctionTool]`. `tests/test_tools.py`
  was adapted to `list_tools()` + a name lookup; the assertion itself (destructiveHint
  is/isn't `true`) is unchanged, per the brief's own contingency clause.

### Telemetry enablement mechanism (verified, not assumed — A16 point 2)

- Controlled by `fastmcp.settings.telemetry_mode`, a pydantic-settings field with
  `env_prefix="FASTMCP_"` — so **`FASTMCP_TELEMETRY_MODE` is still the correct env
  var name** in 4.0.3, exactly as the brief guessed. Verified directly:
  `FASTMCP_TELEMETRY_MODE=off python -c "import fastmcp; print(fastmcp.settings.telemetry_mode)"`
  → `off`; unset → `native` (confirmed default).
- `native` (the default) — quoting the installed docstring: *"FastMCP creates MCP
  spans and propagates trace context through request `_meta`. FastMCP uses only the
  OpenTelemetry API, so span creation is a no-op with negligible overhead unless an
  SDK and exporter are configured."* Other values: `propagation_only`, `off`.
- The deployment manifest still sets `FASTMCP_TELEMETRY_MODE=native` explicitly (belt
  and braces against a future upstream default change), even though it is already the
  default.
- `DISABLE_OTEL_INSTRUMENTATION` is never set.

### "TracerProvider must be set before importing fastmcp" — re-checked against 4.0.3 (A16 point 3)

**No longer load-bearing on this version.** `fastmcp.telemetry.get_tracer()` calls
`opentelemetry.trace.get_tracer_provider()` **fresh on every span creation** (see
`fastmcp/telemetry.py`), not once at import time. Verified empirically: imported
`fastmcp` *before* configuring a `TracerProvider`, then configured the provider
afterward, and a span created via `fastmcp.telemetry.get_tracer()` still exported
correctly to the newly-configured provider. `server.py` still imports `bootstrap`
before `fastmcp` for clarity and because it costs nothing, but this is now a
convention, not a correctness requirement, for the pinned version.

### Client-side protocol skew check (A16 point 4)

fastmcp 4.0.3's server dependency resolves to `mcp==2.1.1`. Task 5's settlement-agent
(and this task's own e2e test) uses the standalone `mcp` package as an MCP *client*,
pinned at `mcp==1.9.4` per the original brief (`tests/requirements.txt`). Verified
interop directly: started a `fastmcp==4.0.3` streamable-HTTP server in one venv and
connected to it with an `mcp==1.9.4` `ClientSession`/`streamablehttp_client` from a
completely separate venv (no shared install). Result: `initialize()`, `list_tools()`,
and `call_tool()` all succeeded; negotiated `protocolVersion: 2025-03-26`.
**No skew found — `mcp==1.9.4` interoperates cleanly with a `fastmcp==4.0.3` server.**
Task 5 can use the same client pin without surprise.

## 2. FastMCP's actually-emitted telemetry vs. the OTel MCP semantic conventions

Captured from a real deployed pod (`kubectl -n northstar-tools port-forward
svc/payments-mcp 18000:8000`), driving both tools once each through a real MCP
`ClientSession` over streamable HTTP, and reading the resulting trace back from
Jaeger.

### Span names, kinds, services

| Span name | Service | Kind |
|---|---|---|
| `tools/call propose_payment` | payments-mcp | server |
| `tools/call execute_payment` | payments-mcp | server |
| `POST` (outbound to payments-api) | payments-mcp | client |

Format matches the convention `{mcp.method.name} {target}` exactly:
`mcp.method.name` = `tools/call`, target = the tool name.

### Full attribute map — `tools/call execute_payment` (verbatim from Jaeger)

```
fastmcp.component.key   = tool:execute_payment@
fastmcp.component.type  = tool
fastmcp.provider.type   = LocalProvider
fastmcp.server.name     = payments-mcp
fastmcp.span.seam       = True
gen_ai.tool.name        = execute_payment
internal.span.format    = otlp
mcp.method.name         = tools/call
mcp.protocol.version    = 2025-03-26
mcp.session.id          = 962209c96d0340c39fd4b259641807f3
otel.scope.name         = fastmcp
span.kind               = server
```

(`tools/call propose_payment` carries the identical attribute set with
`gen_ai.tool.name = propose_payment` and `fastmcp.component.key = tool:propose_payment@`.)

### Point-by-point comparison against the conventions

| Convention | Emitted by FastMCP 4.0.3? | Verdict |
|---|---|---|
| Tool name lives in `gen_ai.tool.name` | Yes — `gen_ai.tool.name = execute_payment` | **Matches.** |
| `mcp.tool.name` does not exist | Confirmed absent from every span's attribute set | **Matches** (correctly omitted). |
| `mcp.method.name` exists | Yes — `mcp.method.name = tools/call` | **Matches.** |
| `mcp.protocol.version` exists | Yes — `mcp.protocol.version = 2025-03-26` | **Matches** (attribute name matches; note the *value* is protocol version `2025-03-26`, not a FastMCP version). |
| `mcp.session.id` exists | Yes — a 32-char hex session ID | **Matches.** |
| Span name is `{mcp.method.name} {target}` | Yes — `tools/call execute_payment` | **Matches.** |
| Tool execution carries `gen_ai.operation.name = execute_tool` | **Absent.** No `gen_ai.operation.name` attribute appears anywhere on the tool-call span. | **GAP.** |

**The one real gap found: FastMCP 4.0.3 does not emit `gen_ai.operation.name` on its
tool-call spans**, despite emitting the sibling `gen_ai.tool.name` attribute from the
same GenAI semantic-convention namespace. This is exactly the kind of half-migrated
convention surface this project commits to reporting rather than working around: no
bespoke `gen_ai.operation.name` (or equivalent) is added to `server.py`. This is a
candidate upstream FastMCP issue/PR — the sibling attribute is done properly but the
operation classifier is missing.

FastMCP also emits several of its own attributes that are not part of the MCP
semconv at all (`fastmcp.component.key`, `fastmcp.component.type`,
`fastmcp.provider.type`, `fastmcp.server.name`, `fastmcp.span.seam`). These are
additive, FastMCP-specific context and do not conflict with anything in the
convention; `fastmcp.span.seam = True` in particular reads like FastMCP's own
internal marker for "this is the seam where an MCP protocol-level span meets a
GenAI-level span," which is consistent with the `gen_ai.operation.name` gap above —
FastMCP appears to be mid-migration on fully modeling the GenAI operation dimension.

## 3. Outbound HTTP semconv spelling (A13)

`opentelemetry-instrumentation-httpx==0.50b0`'s default spelling, captured with a
`ConsoleSpanExporter` against a local HTTP server (no opt-in set):

```
http.method       = GET
http.url          = http://127.0.0.1:.../foo
http.status_code  = 200
```

This is the **legacy** spelling — the same conclusion Task 2 (A12) reached for the Go
side before pinning `otelhttp v0.66.0`. Setting `OTEL_SEMCONV_STABILITY_OPT_IN=http`
converges it onto the stable spelling, verified with the same local-server harness:

```
http.request.method      = GET
url.full                 = http://127.0.0.1:.../foo
server.address           = 127.0.0.1
server.port              = <port>
network.peer.address     = 127.0.0.1
network.peer.port        = <port>
http.response.status_code = 200
network.protocol.version = 1.0
```

**`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set on payments-mcp's deployment manifest**
(`deploy/k8s/payments-mcp.yaml`). Confirmed against the real deployed pod's outbound
call to payments-api (from the captured trace above):

```
http.request.method     = POST
http.response.status_code = 200
network.peer.address    = payments-api.northstar-systems.svc.cluster.local
network.peer.port       = 8080
network.protocol.version = 1.1
server.address           = payments-api.northstar-systems.svc.cluster.local
server.port              = 8080
url.full                 = http://payments-api.northstar-systems.svc.cluster.local:8080/payments/PAY-000002/execute
```

This now matches payments-api's own (Go, otelhttp v0.66.0, A12) stable spelling on
the same hop — both sides of the payments-mcp ↔ payments-api HTTP call use
`http.request.method` / `http.response.status_code` / `url.full` / `server.address`,
so Task 7/8's cross-service attribute queries see one convention, not two.

## 4. Does MCP trace context cross the client → payments-mcp boundary?

**Updated in the fix round (A14a).** The original version of this section reported "no,
untested" because the e2e test's plain `mcp.ClientSession` sent no `traceparent` and
injected nothing into `params._meta`. A reviewer correctly identified that this was an
avoidable gap, not a genuine "cannot be known up front" case (see task-3-review.md
finding 1 and amendment A14a): `mcp.types.CallToolRequestParams` has a `meta` field
aliased to the wire key `_meta` (`RequestParams.Meta`, which allows extra fields), and
FastMCP's `_get_parent_trace_context()` reads exactly that key
(`TRACE_PARENT_KEY = "traceparent"`, `fastmcp/telemetry.py`). The test now injects a
self-minted `traceparent` into `params._meta` via `mcp`'s lower-level
`ClientSession.send_request()` with a hand-built `CallToolRequest` (see
`tests/e2e/test_payments_mcp_trace.py`), rather than assuming this could only be
verified by production code.

**Result: server-side `params._meta` extraction already works on FastMCP 4.0.3.**
`test_tool_call_span_is_parented_to_the_injected_context` asserts the `tools/call
execute_payment` span's `parent_id` equals the span ID this test invented and sent —
and it passes, repeatably, against the real deployed pod. FastMCP correctly extracts
`traceparent` from an incoming `params._meta` and parents its own span there. Combined
with the pre-existing finding that everything *downstream* of that span (the outbound
httpx call and payments-api's own server span) is correctly parented via ordinary W3C
HTTP-header propagation, this means: **the entire chain from an inbound
`params._meta.traceparent` through to payments-api's server span is provably intact end
to end**, when something injects that `_meta` field.

**This narrows Task 7's remaining work to the client side.** The one piece not yet
exercised in production is the settlement-agent's own MCP client actually *performing*
this injection when it calls payments-mcp — this test proves the mechanism works, it
does not make settlement-agent do it. Per A14a, a test injecting `_meta` for itself is
test infrastructure, not a pre-emption of Task 7's job. No HTTP-header workaround was
used anywhere; MCP does not propagate trace context in HTTP headers, and faking that
would produce a passing test with a false architecture.

## 5. Trace-assembly timing note (unrelated to any of the above)

Even with `wait_for_trace_id` (by-ID lookup, added in the fix round), the e2e trace
test intermittently found `payments-api`'s server span missing from the
freshly-fetched trace (`api_spans == []`) on the very first fetch — `wait_for_trace_id`
returns as soon as the span named in its `operation` argument (`tools/call
execute_payment`) is visible, not once every expected descendant span (exported by a
*different* process's own `BatchSpanProcessor`) has also landed. This reproduced in a
real fix-round test run (see the fix-round report) even though propagation itself is
correct — confirmed by immediately re-querying the same trace ID and seeing the
complete 3-span trace with no orphans moments later. `tests/e2e/test_payments_mcp_trace.py`'s
`executed_call` fixture therefore polls the same trace ID (via the harness's existing
public `get_trace`) for up to 30s after `wait_for_trace_id` returns, until the
payments-api span is present, before handing the trace to the assertions. No change to
`tests/harness/jaeger.py` itself. Empirically this step is still necessary — it is not
made redundant by `wait_for_trace_id`'s own polling, since that only waits for one
named span, not for spans from a second service.
