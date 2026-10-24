# Upstream findings

Behaviours of third-party projects that this repository ran into, written up so
each can be filed upstream as it stands. Nothing here has been filed: no issue,
comment or pull request has been posted to any upstream tracker. Trackers were
only read, so "existing upstream activity" below is a snapshot of what was open
or closed when it was last checked.

Each finding names the project and version, what it does, the evidence, why it
matters beyond this repository, what a fix would look like, and what this
repository does about it. Where a finding is closed (fixed upstream, or no
longer reproducible at the pinned version) it says so at the top.

The findings span several projects with different governance: independent Python
packages (1-5, 12-14), agentgateway, a Linux Foundation project (6-10, 17),
OpenTelemetry Go and Python, both CNCF projects (11), and Backstage (15-16).

| # | Subject | State at the pinned versions |
|---|---|---|
| 1 | `a2a-sdk` extracts no inbound trace context and sets no identity attributes | open |
| 2 | FastMCP never sets `gen_ai.operation.name` | open upstream (third-party issue) |
| 3 | FastMCP never reads `baggage` from `params._meta` | open |
| 4 | `mcp` client could not attach `params._meta` to a tool call | closed: fixed upstream, and obsolete at `mcp` 2.2.0 |
| 5 | Model client's HTTP hop had no CLIENT span (`httpx2`) | closed: instrumentor already shipped |
| 6 | agentgateway reads trace context from HTTP headers only | open |
| 7 | agentgateway span attribute names (`jwt.sub`) | HTTP half closed in v1.6.0; `jwt.sub` open |
| 8 | agentgateway ExtMCP guardrail has two config vocabularies and no standalone example | open |
| 9 | ExtMCP `mcp_error` documented as an override, nests under `error.data` | superseded by 17 |
| 10 | `requestHeaders.allowed` is an exhaustive allowlist and drops `traceparent` | open |
| 11 | OpenTelemetry Python Level 2 flag byte rejected by OpenTelemetry Go v1.41.0 | closed: fixed in Go v1.42.0 |
| 12 | Only one of the GenAI metrics is emitted by the frameworks in use | open |
| 13 | OpenFeature's two Python packages disagree on signal and attribute spelling | open |
| 14 | `TracingHook` never emits `feature_flag.set.id` / `feature_flag.version` | open |
| 15 | Backstage `$text` placeholder cannot inline a local, git-less file | open (deployment shape) |
| 16 | Backstage guest provider refuses to run outside development by default | by design |
| 17 | agentgateway v1.6.0 returns an ExtMCP deny as an `isError` tool result | behaviour change, recorded |

`docs/telemetry-contract.md` carries the measured telemetry inventories these
findings refer to.

---

## Finding 1: `a2a-sdk` does not extract inbound trace context, and its spans carry no identity attributes

**Project and version.** `a2a-sdk[telemetry]==0.3.7` (PyPI `a2a-sdk`, the
reference Python A2A SDK, `a2aproject/a2a-python`). Re-read at 1.2.3: both
halves still hold.

**What it does.** A2A exists for one agent to delegate to another across a
process boundary. The SDK's `telemetry` extra instruments its own call graph
but does two things wrong for that purpose:

- It never reads an inbound `traceparent` header, so every request starts a
  disconnected root trace whatever the caller sent. Grepping the installed
  package for `propagate`, `traceparent`, `extract` and `TextMapPropagator`
  finds no trace-context code. The spans come from `@trace_class` /
  `@trace_function` decorators (`a2a/utils/telemetry.py`) on the request
  handlers and all three client transports.
- Every span it produces, server and client side, carries no attribute beyond
  OpenTelemetry's own bookkeeping (`internal.span.format`, `otel.scope.name`,
  `otel.scope.version`, `otel.status_code`, `span.kind`): no operation name, no
  `contextId` or `taskId`, not even the agent's name. The decorators are applied
  without an `attributes=` argument, and `attribute_extractor=` has no call site
  outside `telemetry.py`.

**What the convention says.** The GenAI attribute registry
(`open-telemetry/semantic-conventions-genai`, `gen_ai.conversation.id`) says
instrumentations should populate the conversation id when they have one, and
should not invent one. A2A already has one: `contextId`, and `taskId` for the
task.

**Evidence.** A hand-minted `traceparent` sent to a real pod, and to an
in-process Starlette app, has no relationship to the trace id of the resulting
span; the entry span's `parent_id` is null. Reading every `a2a.server.*` /
`a2a.client.*` span back from Jaeger returns only the five bookkeeping keys
above, on the root span and the innermost helper alike.

**Why it matters.** Anyone instrumenting an A2A system gets a trace per hop that
the SDK cannot stitch into the caller's trace, and, once stitched some other
way, spans that cannot say which conversation, task or agent produced them. For
a delegation protocol that is the one question tracing should answer.

**What a fix would do.** Extract trace-context headers once, where the ASGI app
is built, ahead of every decorated call. Set `gen_ai.operation.name`
(`invoke_agent`) and `gen_ai.conversation.id` (from `contextId`) on the handler
and client entry spans.

**What this repository does.** The extraction half is worked around with
framework instrumentation, not a patch: `opentelemetry-instrumentation-starlette`
applied to the Starlette app `A2AStarletteApplication(...).build()` returns
(`.instrument_app()`). Any `a2a-sdk` user can do the same, but a general-purpose
SDK should not require every integrator to discover it. The attribute half is
left alone: no bespoke `a2a.*` attribute is invented to fill a convention gap.

**Version 1.x.** `a2a-sdk` 1.x implements A2A protocol v1.0, which changes the
JSON-RPC method names (`SendMessage` for `message/send`), makes `AgentCard` a
protobuf message (no `protocolVersion`, `url` or `preferredTransport`; it has
`supportedInterfaces`), removes `a2a.server.apps.A2AStarletteApplication` in
favour of `a2a.server.routes.create_jsonrpc_routes(...)`, and gives
`DefaultRequestHandler` a required `agent_card`. Both halves of this finding
survive: the 1.2.3 package tree still has no trace-context extraction and no
decorator passes `attributes=`. Moving to 1.x is a wire and API migration, not
a version bump, so the agents stay on 0.3.7. The newest release on PyPI can lag
the GitHub releases page; check PyPI for what is installable.

**Existing upstream activity.**

- GenAI conventions, all open: `semantic-conventions-genai` #70, #195, #254 and
  #447 each propose A2A identity (`gen_ai.conversation.id` for `contextId`,
  `gen_ai.operation.name` for the operation). Four independent proposals, so a
  fifth issue on the convention side would add nothing.
- `a2aproject/a2a-python` #168 ("A2AClient should propagate trace context?") and
  #281 ("EventQueue should propagate trace context?") are closed and neither
  covers the absence of extraction at the ASGI boundary. No open issue was found
  on that point.

**Filing status.** The extraction gap and the missing attributes are not covered
by an open `a2a-python` issue and are ready to file.

---

## Finding 2: FastMCP never emits `gen_ai.operation.name` on tool-call spans

**Project and version.** `fastmcp==4.0.3` (PyPI `fastmcp`).

**What it does.** FastMCP's `tools/call` span carries `gen_ai.tool.name` but
never `gen_ai.operation.name`. The GenAI conventions expect
`gen_ai.operation.name = execute_tool` on a tool-execution span.
`fastmcp/telemetry.py` sets the first and not the second.

**Evidence.** A live `tools/call execute_payment` span read back from Jaeger
carries exactly: `fastmcp.component.key`, `fastmcp.component.type`,
`fastmcp.provider.type`, `fastmcp.server.name`, `fastmcp.span.seam`,
`gen_ai.tool.name`, `internal.span.format`, `mcp.method.name`,
`mcp.protocol.version`, `mcp.session.id`, `otel.scope.name`, `span.kind`. No
`gen_ai.operation.name`, on either tool, in any capture.

**Why it matters.** Cross-framework views of "which GenAI operations happened
where" cannot group FastMCP's tool spans by operation type, even though the
sibling attribute that names the tool is already correct. It reads as a
half-migrated convention surface.

**What a fix would do.** Set `gen_ai.operation.name = execute_tool` next to the
existing `gen_ai.tool.name` assignment.

**What this repository does.** Nothing. Setting the attribute locally would make
this system's telemetry disagree with what a fixed FastMCP emits natively.

**Existing upstream activity.** `PrefectHQ/fastmcp#3534` ("Support GenAI semantic
convention in open telemetry"), open, filed by a third party, asks for exactly
this. (It also asks for `gen_ai.tool.name`, which 4.0.3 already emits.)

**Filing status.** Already filed by someone else; a second issue adds nothing.

---

## Finding 3: FastMCP never reads the `baggage` key from JSON-RPC `params._meta`

**Project and version.** `fastmcp==4.0.3`.

**What it does.** MCP has no HTTP layer of its own to carry trace context, so
SEP-414 puts `traceparent`, `tracestate` and `baggage` as unprefixed keys in
`params._meta`. FastMCP's `extract_trace_context()` (`fastmcp/telemetry.py`)
copies only the first two into its carrier; `inject_trace_context()` has the same
omission on the client side. A repository-wide grep for `baggage` in the package
finds one match, a comment about preserving already-active baggage. The trace
joins across the MCP boundary, but any baggage the caller carried (a claim id, a
tenant id) is dropped there, with no error or warning.

**Evidence.** A live capture against the deployed server: a client injects a
`baggage` key into `params._meta` on `tools/call execute_payment` beside a
working `traceparent`. The server span is correctly parented but carries none of
the baggage value, on that span or on its downstream call, so the value never
entered the process's ambient context.
`tests/e2e/test_claim_correlation.py::test_claim_id_is_on_every_mcp_hop_span`
asserts that `northstar.claim.id` is on every settlement-agent span and on no
payments-mcp or payments-api span. A separate unit test
(`agents/settlement-agent/tests/test_mcp_tools.py`) shows the client does put
`baggage` in `_meta` before the wire, so the missing half is the server's.

**Why it matters.** Baggage is the standard way to carry an application
identifier across processes, and SEP-414 anticipates it. Anyone relying on it
across a FastMCP server loses it silently at that boundary.

**What a fix would do.** Read and write a `baggage` key in both
`extract_trace_context()` and `inject_trace_context()`, mirroring
`traceparent` and `tracestate`.

**What this repository does.** No workaround. Trace-id correlation stays intact
end to end; the domain attribute carried in baggage does not cross this one
boundary, and the tests assert that narrow boundary instead of a broader
"everything propagates" claim.

**Existing upstream activity.** `PrefectHQ/fastmcp#3998` (open) reports that the
server "does not receive `traceparent` / `baggage` through MCP `_meta`" but asks
only about `initialize` and list calls, assuming `tools/call` works. It does not
for `baggage`. `PrefectHQ/fastmcp#4358` (open) concerns incoming trace headers
reaching internal spans and does not mention baggage.
`modelcontextprotocol/modelcontextprotocol#2028` (SEP-2028, open) is about
forwarding `_meta` to downstream HTTP calls, a different gap. No open issue
reports that the server never reads `baggage` from `_meta` for any method.

**Filing status.** Ready to file; it is only partly covered by #3998.

---

## Finding 4 (closed): the `mcp` client's `call_tool()` had no way to attach `params._meta`

**Project and version.** `mcp==1.9.4` (the official Python MCP SDK). This
repository now pins `mcp==2.2.0`.

**What it did.** `ClientSession.call_tool(name, arguments, ...)` had no `meta`
parameter, and always built `CallToolRequestParams(name=..., arguments=...)`. The
type supports `_meta`; the convenience method did not. SEP-414 trace propagation
therefore needed a hand-built `CallToolRequestParams(..., _meta=...)` sent through
`ClientSession.send_request()`, duplicating `call_tool()`'s plumbing.
`agents/settlement-agent/mcp_tools.py` and
`tests/e2e/test_payments_mcp_trace.py` both do this.

**Upstream.** `modelcontextprotocol/python-sdk#1216` ("Expose `_meta` in
`ClientSession` methods") is closed; PR #1231 added the optional `meta`
parameter to `call_tool()` and merged in October 2025. `mcp` 1.28.1 has it.

**At 2.2.0 the finding is obsolete.** The JSON-RPC dispatcher
(`mcp/shared/jsonrpc_dispatcher.py`) injects W3C trace context into
`params._meta` on every outbound request, unconditionally, with a source comment
naming SEP-414. That injection parents payments-mcp's server span under the
SDK's own `MCP send tools/call <tool>` CLIENT span (see
`docs/telemetry-contract.md`). `mcp` 1.28.1 ships no OpenTelemetry code at all.
The repository's hand-built `_meta` injection is therefore redundant; it is kept
because removing production code that carries trace context needs its own proof.

---

## Finding 5 (closed): the model client's HTTP hop had no CLIENT span, and the fix was already shipped

**Project and version.** `anthropic==1.4.0` requires `httpx2<3,>=2.0.0`. `httpx2`
(`pydantic/httpx2`) is a Pydantic-maintained, API-compatible fork of `httpx`, a
separate distribution. The OpenTelemetry instrumentation package is
`opentelemetry-instrumentation-httpx==0.65b0`.

**What happened.** Applying only `HTTPXClientInstrumentor()` instruments `httpx`.
The Anthropic SDK (and PydanticAI's client) use `httpx2`, so the model call
produced no HTTP CLIENT span: no status code, no retry count, no wire-versus-SDK
latency. Every other outbound hop had one. The only trace of the call was
`server.address=api.anthropic.com` on PydanticAI's own `chat` span. Nothing
errors or warns; the span is simply absent.

**The fix was already installed.** `opentelemetry-instrumentation-httpx`'s module
docstring says "When instrumenting `httpx2` clients, use
`HTTPX2ClientInstrumentor`". It was added in `opentelemetry-python-contrib` PR
#4730 (closing #4635) and released as 1.44.0 / 0.65b0. This repository applies
`HTTPX2ClientInstrumentor().instrument()` beside `HTTPXClientInstrumentor()` in
`agents/settlement-agent/bootstrap.py`, which gives each `chat` span exactly one
httpx CLIENT span under it. It carries stable HTTP semantic-convention names
because `OTEL_SEMCONV_STABILITY_OPT_IN=http` is set. Both instrumentors share one
tracer scope, so `otel.scope.name` cannot tell them apart; the model-bound span is
identified by `server.address == "api.anthropic.com"`
(`tests/e2e/test_settlement_llm_trace.py::test_anthropic_client_span_exists_and_is_parented_to_chat`).

**Why it still matters.** Any integrator who instruments `httpx` in the ordinary
way, and depends on a library that moved to `httpx2`, gets a silent, total gap
for that hop. Many projects are migrating (`stripe-python`, `huggingface_hub`,
`litestar`, Airflow providers, among others), and other vendors' agents show the
same gap (`DataDog/dd-trace-py#18780`, `elastic/apm-agent-python#2706`, both
open). Applying both instrumentors is the answer; nothing forces or checks it.

**Filing status.** Nothing to file: the generic fix is shipped.

---

## Finding 6: agentgateway reads trace context from HTTP headers only, never from `params._meta`, and mints a new trace root when no header is present

**Project and version.** agentgateway, image
`cr.agentgateway.dev/agentgateway:v1.6.0` (source read at v1.5.0; the
header-only ingress still holds at v1.6.0). A Linux Foundation
project, not a CNCF one.

**What it does.** MCP carries W3C trace context in `params._meta`. The gateway
writes that carrier on the way out and never reads it on the way in.

- Ingress: `TraceParent::from_request`
  (`crates/agentgateway/src/telemetry/trc.rs`) reads the HTTP `traceparent`
  header and nothing else; the request body is never touched.
- Egress: `IncomingRequestContext::stamp_trace_context`
  (`crates/agentgateway/src/mcp/upstream/mod.rs`) copies the three W3C keys out
  of the outbound headers into `_meta`, unprefixed. Its own comment says why:
  "SEP-414 ... The only trace carrier for stdio upstreams, which have no request
  headers." It runs after `start_mcp_outbound_span` has injected the gateway's
  own CLIENT-span context, which is why the value in `_meta` is the gateway's
  outbound span and the upstream server parents under it.

So `_meta` is an output format only. A client that sends context only in `_meta`
(a stdio client, or an HTTP client whose layer is not instrumented) is treated as
having no parent, and the gateway roots a new trace for its own SERVER and CLIENT
spans while the untouched `_meta.traceparent` still carries the client's context
to the server behind it: two traces where there should be one, with the proxy hop
in neither alongside its caller. A related effect: a request with a `traceparent`
is sampled by default and one without is dropped, so this repository sets
`randomSampling: true` so a headerless caller is visible too.

**Convention.** SEP-414 defines `traceparent`, `tracestate` and `baggage` as the
three unprefixed `_meta` keys. An intermediary that consumes and produces MCP
messages would be expected to honour them in both directions.

**Evidence.** In this repository the MCP hop stays one trace only because two
carriers are present: settlement-agent injects `_meta` by hand for FastMCP, and
`HTTPX2ClientInstrumentor` separately injects a `traceparent` header because the
streamable-HTTP transport is httpx-based. The gateway reads the header. This
repository is not affected, and `tests/e2e/test_gateway_trace.py` records the
distinction so nobody concludes the gateway supports SEP-414 on ingress.

**Why it matters.** A proxy that silently re-roots a trace is worse than an
untraced one: the spans exist and look healthy, and the link to the caller is
gone. The symptom (an orphan trace per proxied call) does not point at
trace-context extraction as the cause.

**What a fix would do.** Extract from `params._meta` when no header is present,
preferring the header when both are, the mirror of `stamp_trace_context`.

**What this repository does.** Nothing; the header it sends is ordinary httpx
instrumentation.

**Existing upstream activity.** No issue asks for ingress extraction from `_meta`.
The nearest is `agentgateway/agentgateway#2904` (closed, fixed before v1.5.0): on
`mcp:` backends the gateway forwarded the stale inbound `traceparent` instead of
its own span. That fix is why payments-mcp parents under the gateway CLIENT span.

---

## Finding 7: agentgateway span attributes were its access-log schema, and the authenticated principal is `jwt.sub` rather than `user.id`

**Project and version.** agentgateway. The HTTP half was measured at v1.5.0 and is
fixed in v1.6.0, the pinned version. The `jwt.sub` half still holds at v1.6.0.

**One cause.** The gateway's span attributes were its structured access-log
fields reused verbatim: one list of `(key, value)` pairs in
`crates/agentgateway/src/telemetry/log.rs` fed the tracer, the OTLP logger and
the access log. A log vocabulary is not a semantic-convention vocabulary.

**HTTP half (v1.5.0, fixed).** Spans carried `http.method`, `http.path`,
`http.status`, `http.host` and `http.version`, which match neither the stable HTTP
conventions nor the legacy spelling:

| What | Stable | OTel legacy | agentgateway v1.5.0 |
|---|---|---|---|
| method | `http.request.method` | `http.method` | `http.method` |
| path | `url.path` | `http.target` | `http.path` |
| status | `http.response.status_code` | `http.status_code` | `http.status` |
| host | `server.address` | `http.host` | `http.host` |

`agentgateway/agentgateway#1151` reported the same divergences and PR #3182
("align HTTP attributes with OTel semconv") fixed them for request traces and
OTLP logs, adding `telemetry/semconv.rs` and an opt-in `accessLog.preset: Otel`.
At v1.6.0 the SERVER spans carry `http.request.method`,
`http.response.status_code`, `url.path`, `client.address`, `server.address` and
`server.port`. `tests/e2e/test_gateway_trace.py` pins the exact attribute sets and
fails if a legacy key returns.

**`jwt.sub` half (open).** The verified JWT subject is recorded as `jwt.sub`;
semantic conventions have `user.id` for the authenticated principal
(`enduser.id` is deprecated in its favour). PR #3182 touches neither. A query on
`user.id` finds every service in this system except the one that authenticates
anything. No upstream issue was found asking for it.

**Why it matters.** A mixed fleet needs one filter for status codes and one for
the principal; paying for naming drift at the query layer is a normalisation
problem, not a tracing one. The trace still holds together end to end across
components that do not agree on names. Trace context makes the chain one thing;
attribute naming makes it queryable, and the two are separable.

**What this repository does.** Publishes agentgateway's attributes under their
real names and asserts them by exact set equality. It does not rename them in the
collector: telemetry that disagrees with what the component emits is worse than
the divergence it hides.

**Filing status.** The HTTP half needs no filing. The `jwt.sub` half is
unreported and ready to file.

---

## Finding 8: agentgateway's ExtMCP guardrail has two config vocabularies with different casing, and no standalone example

**Project and version.** agentgateway v1.5.0 (git revision
`fe6732474a96a0363dfb9822859af4e9bab360fa`). The standalone config in this
repository validates unchanged on v1.6.0.

**What it does.** `mcpGuardrails` (the ExtMCP hook) can be configured through the
Kubernetes CRD (`AgentgatewayPolicy`) or a standalone `config.yaml`, and the two
spell the settings differently. At v1.5.0 the `examples/` tree (33 directories)
has no example of either: a search for `mcpGuardrails`, `ext_mcp` and `extMcp` in
every YAML under it returns nothing. The nearest, `examples/mcp-authorization`,
configures the native CEL `mcpAuthorization`, a different feature. The only worked
ExtMCP config is a CRD manifest under `controller/test/e2e/testdata/extmcp/`:

```yaml
spec:
  backend:
    mcp:
      guardrails:
        processors:
        - remote:
            backendRef: { name: ext-mcp, port: 4445 }
            failureMode: FailClosed
          methods:
            tools/call: Request
```

The standalone shape the same binary accepts:

```yaml
policies:
  mcpGuardrails:
    processors:
      - kind: remote
        host: policy-decision-service.northstar-platform.svc.cluster.local:9100
        failureMode: failClosed
        methods:
          "tools/call": request
        requestHeaders:
          allowed: ["traceparent", "tracestate"]
```

A reader who reconstructs the standalone config from the CRD example gets the
casing wrong one key at a time. Changing one token at a time against
`--validate-only`:

| Change | Validator output |
|---|---|
| `kind: remote` to `Remote` | ``unknown variant `Remote`, expected `remote` `` |
| `request` to `Request` | ``unknown variant `Request`, expected one of `off`, `request`, `response`, `full` `` |
| `failClosed` to `FailClosed` | ``unknown variant `FailClosed`, expected `failClosed` or `failOpen` `` |
| `allowed` to `allow` | ``unknown field `allow`, expected `allowed` or `disallowed` `` |

The policy server's address is `host` in standalone and `backendRef: {name, port}`
in the CRD. The casing is systematic: `schema!` and `schema_enum!`
(`crates/core/src/serdes.rs`) expand to
`#[serde(rename_all = "camelCase", deny_unknown_fields)]` for every
standalone-config type, while the CRD types are Go and follow Kubernetes
convention.

**Why it matters.** ExtMCP is the extension point for an external policy decision
on a tool call, a security control. A strict validator is good, but it is not a
substitute for one worked example; people give up and put the check somewhere
weaker, or use the CEL mechanism that has an example even where an external
decision is what they need.

**What a fix would do.** Add `examples/mcp-guardrails/` in the shape of the other
`mcp-*` examples (a standalone `config.yaml`, a README, and something on the other
end of the gRPC call; `test_helpers/extmcpmock.rs` is most of that), and a note in
the CRD testdata that its casing is CRD-specific.

**What this repository does.** `deploy/platform/agentgateway/config.yaml` records
the working shape and every rejected spelling. The validator checks the schema
before it fetches the JWKS, so reaching the JWKS stage proves the schema parsed.

**Existing upstream activity.** None found for missing examples or the vocabulary
skew (searches for `mcpGuardrails`, `ext_mcp`, `ExtMCP`, `guardrails example`
returned only unrelated guardrail-telemetry and replay issues).

---

## Finding 9 (superseded by Finding 17): `AuthorizationError.mcp_error` is documented as overriding the JSON-RPC error and does not

**Project and version.** agentgateway v1.5.0. At v1.6.0 a `tools/call` deny is an
`isError` tool result and `error.data` is not sent at all (Finding 17).

**What it did.** The ExtMCP proto comment on `AuthorizationError.mcp_error` says
"Optional explicit JSON-RPC error payload, as raw JSON bytes. Overrides the
code-derived default." At v1.5.0 it overrode nothing. `translate_error`
(`crates/agentgateway/src/mcp/guardrails/client.rs`) derived `code` from the
`AuthorizationError.Code` enum on every path, put `reason` in `message`, and nested
the whole parsed `mcp_error` under `data`. A policy server answering
`PERMISSION_DENIED` with `mcp_error = {"code": -32003, "message": "PDS-DENY-MARKER",
"data": {"marker": "task-1"}}` produced, on the wire:

```json
{"jsonrpc":"2.0","id":"7876ec8661114e6d97b45e39255dfb80","error":{"code":-32001,"message":"echo-pds denies everything","data":{"code":-32003,"message":"PDS-DENY-MARKER","data":{"marker":"task-1"}}}}
```

An unparseable `mcp_error` was dropped with a `warn!` and the error returned
regardless. Nothing in JSON-RPC or MCP requires either behaviour; the defect was
that the proto comment (the artifact a policy-server author reads, and which this
repository vendors verbatim) described one design and the code did the other.

**Why it mattered.** A policy server's most useful client-visible signal is
machine-readable ("refused by policy, do not retry" versus "the tool failed, try
again"). An author who believed `mcp_error.code` reached the client would encode
that in a code the agent never saw.

**Fix that would have worked.** Honour the comment, or correct it to say the
payload lands under `data`. Correcting the comment is cheaper and arguably better,
since overriding the code lets a server emit codes rmcp assigns to unrelated
conditions (the gateway's constants skip `-32002` for that reason).

**Upstream.** No issue mentions `mcp_error`.

---

## Finding 10: `mcpGuardrails.processors[].requestHeaders.allowed` is an exhaustive allowlist, so naming any header silently drops `traceparent`

**Project and version.** agentgateway v1.5.0 (code read at that revision;
documented behaviour, so unchanged expectations at v1.6.0).

**What it does.** `requestHeaders.allowed` reads like an addition to whatever the
gateway already forwards. It is a replacement: an empty list forwards everything,
a non-empty list forwards only what it names (`HeaderFilter` in
`crates/agentgateway/src/mcp/guardrails/mod.rs`; `disallowed` always wins). The doc
comment is accurate. It is a safe default and an unsafe first edit, in a field
whose name invites that edit. Naming one header for the policy server's benefit
stops W3C trace context being forwarded, and the only symptom is that the policy
server's spans are no longer in the caller's trace. Nothing fails, nothing is
logged, and the policy keeps working.

**Evidence.** One real `tools/call` with a minted JWT-SVID and a known incoming
`traceparent`:

| `requestHeaders` | Where the policy server's span landed |
|---|---|
| `allowed: ["authorization"]` | a new root trace of its own (orphan single-span traces) |
| block absent | parented under the gateway's `tools/call` span |
| `allowed: ["traceparent", "tracestate"]` | parented |

**Why it matters.** Both reasons to name headers (forwarding a credential,
trimming what a second service sees) are legitimate and are the first edit an
operator makes. The security-minded edit, "forward only `authorization`", costs
distributed tracing across the policy hop. `ext_authz`, which this filter mirrors,
has the same trap.

**What a fix would do.** At least say in the field's doc comment and in any shipped
example (Finding 8) that trace-context headers are subject to the same filter. A
stronger fix exempts `traceparent` / `tracestate` the way pseudo-headers are
handled, treating them as transport metadata. That is upstream's design call, with
a real counter-argument that explicit is better than magic.

**What this repository does.** Names the pair explicitly,
`allowed: ["traceparent", "tracestate"]`. Removing the block also restores
parenting but forwards every header, which was not measured. `authorization` is
deliberately not forwarded: the policy server reads identity from the gateway's
verified `metadata_context`, so a live bearer credential would add cost without
benefit.

**Upstream.** Searches for `requestHeaders` and `traceparent guardrails` found only
unrelated LLM-provider header bugs.

---

## Finding 11 (closed): OpenTelemetry Python's Level 2 flag byte is rejected by OpenTelemetry Go v1.41.0, so the hop silently starts a second trace

**Projects and versions.** The defect exists only in the pair:
`opentelemetry-sdk` 1.44.0 (OpenTelemetry Python) and `go.opentelemetry.io/otel`
v1.41.0 (OpenTelemetry Go). Both are CNCF projects and each is correct on its own.
`systems/payments-api` now runs otel-go v1.46.0, which carries the fix.

**What it did.** W3C Trace Context Level 2 adds a flag bit `0x02` ("trace id was
generated randomly"). `opentelemetry-sdk` 1.44.0 sets it alongside `SAMPLED`, so a
trace root injects `traceparent` ending `-03`
(`opentelemetry/trace/span.py`: `RANDOM_TRACE_ID = 0x02`). A child span inherits the
bit from its extracted remote parent rather than regenerating it
(`sdk/trace/__init__.py`). otel-go v1.41.0's extractor
(`propagation/trace_context.go`: `if version == 0 && (h != "" || opts[0] > 2) {
return trace.SpanContext{} }`) refuses any version-`00` header whose flag byte
exceeds 2 and returns an empty `SpanContext`, which `otelhttp` reads as "no parent"
and turns into a new root. Nothing errors or is logged; both services are healthy;
the spans are in two different traces. Level 2 tells senders to set a bit that
Level 1 receivers were told to reject.

**Evidence.** The same `GET /payments` request twice, differing in one hex digit:
a hand-injected `traceparent` ending `-01` produced a payments-api span correctly
parented; the byte-identical request ending `-03` produced no span under that
trace id at all, because a fresh root was minted instead. The header
reviewer-ui actually emitted was
`traceparent: 00-9946bc5707f7d4a0bbd77773b18f2a8f-9919a1db7019e831-03`.

**Fix upstream.** `open-telemetry/opentelemetry-go` PR #7834 ("Have SpanContext
support the new W3C random flag"), released in v1.42.0: "Preserve W3C TraceFlags
bitmask (including the random Trace ID flag) during trace context extraction and
injection". The check becomes `opts[0] > 3`. v1.42.0 requires Go 1.25, so
adopting it is a toolchain bump, not only a module bump (`go.mod` declares
`go 1.25.0` with `toolchain go1.27.1`, the Dockerfile builds on
`golang:1.27.1-alpine`, and the Go tests run under `GOTOOLCHAIN=auto`). The newer
`otel/sdk` also imports a newer `semconv` schema, and `resource.Merge` refuses two
different schema URLs, so the service's own `semconv` import has to move with it or
the pod crash-loops with `conflicting Schema URL`.

**Why it still matters.** Any fleet mixing OpenTelemetry Python 1.4x or later with
Go v1.41.0 or earlier has this on every Python-to-Go hop. A rejected parent does not
orphan a span, which a suite might notice; it starts a second trace, which looks
healthy from every angle except the one that asks whether the two are the same
trace. Strictness at a version boundary turns a forward-compatible extension into
silent data loss.

**What this repository does.** The Go service is on v1.46.0, all Python services
pin `opentelemetry-sdk==1.44.0`, and the stock `TraceContextTextMapPropagator` is
registered everywhere. `tests/e2e/test_payments_mcp_trace.py` asserts genuine
ancestry across the `payments-mcp` to `payments-api` hop, and a `-03` parent injected
at the MCP boundary was verified to hold all three spans in one trace.

**Not established.** Behaviour between otel-go v1.41.0 and v1.42.0 versions not
read, non-HTTP propagators, and a non-sampled parent (every measurement was on a
sampled trace). Nothing needs filing on the Python side: setting `0x02` is what
Level 2 requires. The interop observation itself is unreported.

---

## Finding 12: of the twelve GenAI metrics the conventions define, the frameworks in use emit one, and three of the four packages create no instrument at all

**Projects and versions.** `pydantic-ai-slim==2.42.0`, `a2a-sdk==0.3.7`,
`fastmcp==4.0.3`, `mcp==2.2.0`.

**What it does.** `open-telemetry/semantic-conventions-genai` defines twelve GenAI
metrics, all at Development stability (the GenAI conventions now live in that
repository; the old pages in `open-telemetry/semantic-conventions` are stubs that
still return HTTP 200). This system emits one of them, `gen_ai.client.token.usage`.
Three of the four packages contain no OpenTelemetry metrics instrumentation at all
(no `get_meter(` call anywhere), so there is no metrics-only path to "how long does
an agent invocation take", "how many tool calls does a run make" or "how long do
tool executions take". Those need spans, a tracing backend and a join, which is what
the metric definitions exist to avoid.

The six metrics checked, all histograms at Development:

| Metric | Unit |
|---|---|
| `gen_ai.invoke_agent.duration` | `s` |
| `gen_ai.invoke_agent.inference_calls` | `{inference_call}` |
| `gen_ai.invoke_agent.tool_calls` | `{tool_call}` |
| `gen_ai.execute_tool.duration` | `s` |
| `gen_ai.client.token.usage` | `{token}` |
| `gen_ai.client.operation.duration` | `s` |

**Evidence, at two layers that agree.**

1. Source. Scanning every `.py` file in each installed distribution: `pydantic_ai`
   (328 files) has one `get_meter(` and creates three instruments; `a2a-sdk` (80
   files), `fastmcp` (257) and `mcp` (123) have no `get_meter(` and create no
   instruments. Of the six names only `gen_ai.client.token.usage` appears (in
   `pydantic_ai/models/instrumented.py`).
2. Runtime. PydanticAI's instrumentation settings constructed inside the running pod
   create exactly three instruments: `operation.cost` (`{USD}`, PydanticAI's own name
   in no OTel namespace), `gen_ai.client.operation.time_to_first_chunk` (`s`,
   recorded only for streaming requests, which this system does not make) and
   `gen_ai.client.token.usage` (`{token}`).

**Not a measurement artefact.** The five Python services given a `MeterProvider`
(`settlement-agent`, `claims-agent`, `payments-mcp`, `reviewer-ui`,
`policy-decision-service`) had their export path demonstrated end to end: other
metrics from the same processes reach Prometheus under their own `job` labels, a
probe counter sent from inside the policy-decision-service pod arrived, and the
collector's and services' logs show no export failures. All four frameworks run
inside that set (`pydantic_ai` in settlement-agent, `a2a-sdk` in both agents,
`fastmcp` and `mcp` in payments-mcp). `docs/telemetry-contract.md`'s metrics section
has the transcript.

**Why it matters.** The GenAI metric set is what an operator would build a cost and
latency SLO on without a trace store. With one metric, per-model token volume is
measurable and agent behaviour is not. For MCP servers the gap is total. All of
these metrics are at Development stability, so an implementer is aiming at a target
that can still be renamed, which may be part of why coverage is thin.

**What a fix would do.** PydanticAI: create and record
`gen_ai.invoke_agent.duration`, `.inference_calls` and `.tool_calls` around the run
it already creates an `invoke_agent` span for, and
`gen_ai.client.operation.duration` beside the existing client histograms (the timing
is already computed for the spans). FastMCP and `mcp`: a meter and
`gen_ai.execute_tool.duration` around the tool-invocation path their spans already
wrap.

**What this repository does.** No `gen_ai.*` metric is hand-rolled, for the reason
given in Finding 2. Two tests enforce it at the two layers it takes:
`tests/e2e/test_prometheus_metrics_ingest.py::test_no_metric_was_invented_to_fill_a_framework_gap`
asserts Prometheus holds no `gen_ai.*` name beyond the one PydanticAI emits (it reads
a store, so it sees names, not authorship), and
`tests/unit/test_metrics_wiring.py::test_no_service_authors_a_metric_under_a_convention_namespace`
walks the AST of every Python file for an instrument created with a `gen_ai.` name.

**Existing upstream activity.**

- `PrefectHQ/fastmcp#3733` ("Support mcp.server.* OTEL metrics"), open and assigned:
  exactly the FastMCP half. `#3887` and `#3891` (open) are adjacent.
- `pydantic/pydantic-ai#6204` (open, assigned) asks for `gen_ai.agent.name` as a
  dimension on client metrics. It does not ask for the missing metrics, but it names
  the complete set as `gen_ai.client.token.usage`, `operation.cost` and the streaming
  `time_to_first_chunk`, which corroborates the measurement. It also states
  PydanticAI's position: the semconv metric attribute group deliberately excludes
  agent identity, which is specified only for `invoke_agent` spans, and per-agent
  aggregation comes from spans, "which is higher fidelity". The absence of
  `gen_ai.invoke_agent.*` metrics is therefore at least partly a deliberate position
  that agent-level telemetry is a span concern. That is the strongest argument
  against this finding and belongs with it.
- No open issue in `pydantic-ai`, `fastmcp` or `a2a-python` asks for
  `gen_ai.invoke_agent.duration`, `.inference_calls`, `.tool_calls` or
  `gen_ai.execute_tool.duration` by name.

**Filing status.** The FastMCP half is already filed. The PydanticAI half has not been
raised by name.

---

## Finding 13: OpenFeature's Python packages disagree on which OTel signal carries `feature_flag.evaluation`, and on attribute spelling

**Projects and versions.** `openfeature-hooks-opentelemetry==0.3.1`
(`open-feature/python-sdk-contrib`) and `openfeature-sdk==0.10.0`
(`open-feature/python-sdk`). An independent organization, not CNCF or Linux
Foundation.

**What it does.** Two packages that both claim to implement the specified
`feature_flag.evaluation` event disagree.

- `TracingHook.finally_after` builds an attribute dict and calls
  `current_span.add_event(...)`: a span event, never a log record. Its README says it
  is a "spec compliant way to automatically add a feature flag evaluation to a span as
  a span event", linking the OTel trace feature-flag conventions.
- `openfeature/telemetry/__init__.py` in the SDK defines `create_evaluation_event(hook_context,
  details)` returning a dataclass with separate `attributes` and `body`, which is the
  log-record data model. `body.py` cites the OTel feature-flags-logs conventions.
  Nothing in the hook calls it: `grep -rln "create_evaluation_event\|EvaluationEvent"`
  over the environment finds only its definition. It was added by
  `open-feature/python-sdk` #448 / PR #451 (February 2025), ten months before the hook
  package's two most recent releases (v0.3.0, v0.3.1).

**Attribute spellings**, read from the installed packages and cross-checked against
`opentelemetry.semconv._incubating.attributes.feature_flag_attributes`
(`opentelemetry-semantic-conventions==0.65b0`):

| Field | Current semconv | `TracingHook` (span event) | `openfeature.telemetry` (log-shaped) |
|---|---|---|---|
| provider name | `feature_flag.provider.name` | same | `feature_flag.provider_name` (deprecated) |
| variant | `feature_flag.result.variant` | same | `feature_flag.variant` (deprecated) |
| reason | `feature_flag.result.reason` | same | `feature_flag.evaluation.reason` (deprecated) |
| error message | `feature_flag.error.message` | `error.message` (neither current nor deprecated) | `feature_flag.evaluation.error.message` (deprecated) |

The hook gets three of four right and invents the fourth. The SDK's utility cites the
current page but uses deprecated spellings on every attribute that has one.

**Why it matters.** Anyone consuming `feature_flag.evaluation` has to know in advance
which signal to look on, and cannot query by current attribute names alone. A backend
that handles log events well and span events poorly gets a different experience from
which OpenFeature package an integrator happened to install.

**What a fix would do.** Either `TracingHook` adopts `create_evaluation_event()` (fixing
its error-message key and aligning signal choice), or the SDK utility moves to current
spellings and the hook stays the span-event path with its README saying which of the
spec's emission patterns it implements. What is not defensible is two co-maintained
packages citing different OTel pages as the compliance target for one event name.

**What this repository does.** Registers `TracingHook` unmodified on the policy decision
span, so the decision span carries its `feature_flag.evaluation` span event as the
package produces it. It does not patch the hook's spelling or error key.

**Existing upstream activity.** `open-feature/python-sdk-contrib#363` ("Add Span Hook",
open) asks for a hook that creates a whole span, a third shape that does not address the
span-event versus log-record split. No open issue asks the hook to adopt the SDK's
utility, fix its error-message key, or names the disagreement.

---

## Finding 14: `TracingHook` declares `feature_flag.set.id` and `feature_flag.version` and never emits either

**Project and version.** `openfeature-hooks-opentelemetry==0.3.1`, the package of Finding 13.

**What it does.** `EventAttributes` declares eight attribute names, including
`SET_ID = "feature_flag.set.id"` and `VERSION = "feature_flag.version"`. `finally_after`
sets the key, value, reason, variant, context id and provider name, and never
references `SET_ID` or `VERSION`. A grep of the hook package for `flag_metadata` finds
nothing, though `details.flag_metadata` carries both values on every call. The SDK's
own `create_evaluation_event` reads both correctly
(`details.flag_metadata.get(TelemetryFlagMetadata.FLAG_SET_ID)` and `.VERSION`).

**Convention.** OpenFeature Appendix D lists `flagSetId` and `version` as well-known
`flag_metadata` keys for telemetry; the OTel feature-flag conventions name the same
two attributes (both current in `opentelemetry-semantic-conventions==0.65b0`).

**Effect here.** The `autonomy-level` flag in `featureflag.yaml` defines no
`flag_metadata`, so the omission costs nothing observable today. It would cost something, silently, the
moment a flag defined a `flag_metadata` block: no error, the fields simply never appear
on the span event.

**Why it matters.** Anyone relying on `TracingHook`'s span event to answer "which
ruleset version produced this decision" or "which flag set was this in" gets neither,
for any provider, with no indication anything is missing.

**What a fix would do.** Two `if` blocks in `finally_after` reading
`details.flag_metadata.get("flagSetId")` and `.get("version")`, a port of the lines the
SDK already ships.

**What this repository does.** `pds/autonomy.py` reads `version` and the set id from
`details.flag_metadata` itself, so the current contract version does not depend on the
hook's span event carrying it.

**Existing upstream activity.** Nothing names this omission. Related history:
`python-sdk-contrib` PR #215 ("support for flagd flag metadata") and #211, both merged and
both about the flagd provider carrying metadata, not the hook reading it.

---

## Finding 15: Backstage's `$text` placeholder cannot inline a local, git-less file

**Projects and versions.** `@backstage/backend-defaults` `0.17.8` (declared `^0.17.7`),
`@backstage/plugin-catalog-backend` `3.9.1` (owns `PlaceholderProcessor`) and
`@backstage/integration` `2.1.1` (owns the URL resolver).

**What it does.** Pointing an API entity's `definition` at a committed JSON file with
`$text: ../../contracts/agent-cards/claims-agent.json` fails for a `type: file` location
with no git remote, for two stacked reasons.

1. `PlaceholderProcessor.preProcessEntity` resolves the URL via `relativeUrl` before
   reading it. `ScmIntegrations.resolveUrl` matches the location's base by host against
   the registered SCM integrations; a bare filesystem path matches none and falls to
   `defaultScmResolveUrl`, which tries `new URL(value)` (fails: relative path) then
   `new URL(value, base)`. The two-argument form needs an absolute `base`, and a
   `type: file` location's target is the bare path read from disk
   (`/app/catalog/agents/claims-agent/catalog-info.yaml`), so `new URL` throws
   `TypeError: Invalid URL` unconditionally.
2. Even with an explicit `file://` value, no reader would claim it: `UrlReaders.default()`
   registers the SCM and cloud-storage factories and a generic http(s) `FetchUrlReader`,
   none with a `file:` scheme.

**Evidence.** The running pod logs:
`Processor PlaceholderProcessor threw an error while preprocessing; caused by Error:
Placeholder $text could not form a URL out of /app/catalog/agents/claims-agent/catalog-info.yaml
and ../../contracts/agent-cards/claims-agent.json, TypeError: Invalid URL`.

**Why it matters.** `$text`, `$json` and `$yaml` are documented as general-purpose. A
deployment with committed catalog data and no git remote cannot use them, and the
failure (an unhandled `TypeError` behind a generic "processor threw" wrapper) names
neither the missing scheme support nor the two-step cause.

**What a fix would do.** `defaultScmResolveUrl` treating a scheme-less `base` as
absolute enough to resolve a relative value against, and a `file:` reader in
`UrlReaders.default()`, presumably behind an explicit opt-in given the filesystem-access
implications.

**What this repository does.** Neither package is patched. The claims-agent and
settlement-agent `catalog-info.yaml` files link the agent card through a
`Component providesApis API` edge, and the API's `definition` is a written pointer to
`contracts/agent-cards/*.json` rather than the inlined JSON.

**Existing upstream activity.** No open issue or PR names a `type: file` location's
bare-path target failing `$text`, or the absent `file:` reader. `FileReaderProcessor`
(issues #7706, #14042, PR #4534) was an older, since-removed mechanism. #9446 and #12251
are the same placeholder family failing against a configured SCM host, a different shape.
This is arguably a deployment shape the placeholder was never built for, so filing it
would be a feature request for local-file support.

---

## Finding 16: the guest auth provider refuses to run outside development unless explicitly allowed

**Project and version.** `@backstage/plugin-auth-backend-module-guest-provider` `0.2.22`.

**What it does.** This is working as designed. The provider checks
`process.env.NODE_ENV !== "development"` and
`auth.providers.guest.dangerouslyAllowOutsideDevelopment`, in the authenticator's
`initialize` and again in the sign-in resolver, and refuses (`NotAllowedError`,
`NotImplementedError`) unless the flag is set. The image is a `yarn build` output and
runs under `NODE_ENV=production`, so with the flag unset `POST /api/auth/guest/refresh`
answers `403` with "The guest provider cannot be used outside of a development
environment unless 'auth.providers.guest.dangerouslyAllowOutsideDevelopment' is enabled".

**Why it is recorded.** A correctly collated, RBAC-correct catalog was undeliverable
while every backend signal was green: no `PlaceholderProcessor` errors, "Collating
documents for software-catalog succeeded", the right Kubernetes labels. A backend health
signal is not the claim that the deliverable is reachable by its audience. The default
is right for nearly every deployment; it is wrong-shaped only for a single-operator kind
cluster reachable through a local port mapping, where "development" is a fair
description of the risk although the build is a production build.

**What this repository does.** `platform/backstage/app-config.production.yaml` sets
`dangerouslyAllowOutsideDevelopment: true` with a comment stating the trade: anyone who
can reach port 7007 can sign in as the guest user and, through the autonomy-contract
card, flip the one thing Backstage's writer ServiceAccount can change (`get`/`list`/
`patch` on `resourceNames: [northstar-autonomy]`, no delete or create). The kind host port
listens on 127.0.0.1 only.

**Upstream.** Nothing to fix and nothing to file. A tracker search for
`dangerouslyAllowOutsideDevelopment` found no discussion of the default being wrong.

---

## Finding 17: agentgateway v1.6.0 returns an ExtMCP deny on `tools/call` as an `isError` tool result, and the `error.data` that carried the deny marker is gone

**Project and version.** agentgateway v1.5.0 to v1.6.0 (`cr.agentgateway.dev/agentgateway`).
Upstream change: PR #3526, `tool_error_body` in `crates/agentgateway/src/mcp/mod.rs`. A
behaviour change between two releases, not a defect; recorded because it overturned the
v1.5.0 behaviour of Finding 9.

**What changed.** A `PERMISSION_DENIED` `AuthorizationError` from the `mcpGuardrails` hook
on a `tools/call` used to be HTTP 200 with a JSON-RPC error (`error.code = -32001`,
`error.message` the reason, `error.data` the `mcp_error` payload, Finding 9). On v1.6.0 it
is HTTP 200 with a JSON-RPC result carrying an MCP tool error, and nothing of `mcp_error`
survives. Only `AuthorizationError.Code == INTERNAL_ERROR` keeps the JSON-RPC error form;
a policy refusal is not an internal error, so this repository does not use it to recover
the old shape.

**Wire bytes** (direct `tools/call`, EUR 6,200 `execute_payment` at autonomy level
`assisted`):

```
HTTP/1.1 200 OK
{"jsonrpc":"2.0","id":"2","result":{"content":[{"type":"text","text":"requested 6200 EUR exceeds the 6000 EUR limit for this agent at autonomy level assisted [policy-denial rule=settlement-agent.assisted.execute_limit limit=6000.00 requested=6200.00]"}],"isError":true}}
```

The text is the policy server's `AuthorizationError.reason`, byte for byte. The response
text carries no gateway prefix (the SERVER span's `otel.status_description` still
carries `mcp: mcpGuardrails rejected: `).

**What this repository does.** The facts a client needs (rule, limit, requested amount)
travel in the message text in one fixed format, written by
`platform/policy-decision-service/pds/deny_text.py::format_deny` and read by
`agents/settlement-agent/policy_denial_text.py::parse_deny`:
`<reason> [policy-denial rule=<rule> limit=<amount|none> requested=<amount|none>]`. A
contract test feeds the policy service's real output into the agent's real parser, and an
`isError` result with no tag stays a malfunction, never a policy decision. The parser is
anchored on the tag at the end of the text, so it does not depend on the prefix.

**Two neighbours of the same upgrade.**

- The gateway's SERVER spans moved to the stable HTTP semantic conventions (PR #3182,
  Finding 7).
- Refused SERVER spans gained `error.type` (`"MCP"` for a policy refusal, `"JwtAuth"` for an
  authentication one) beside the existing `error = true`.

The ExtMCP proto is byte-identical between the two releases and
`deploy/platform/agentgateway/config.yaml` validates unchanged.

---

## Open questions

An entry here is a question that was asked and answered without turning up a defect, kept
with its answer.

### OQ-U1 (closed, not a bypass): does `mcpGuardrails` evaluate every message in a batched JSON-RPC array?

**Question.** JSON-RPC 2.0 lets a client send an array of requests in one payload. If the
gateway unpacked an array but handed the guardrail only the first message (or the array
as one undecidable unit), a batch of a benign `tools/call` followed by a governed one
could reach the upstream server with no decision on the second: a policy bypass.

**Why it could be asked from the contract alone.** The vendored `ext_mcp.proto` describes
`McpRequest` with a singular `method` and a singular `mcp_request`, so the gateway cannot
describe a batch in one call. The policy service denies any method other than `tools/call`
(`undecidable_method`) and refuses params it cannot read. The gateway config opts in only
to `methods: {"tools/call": request}`. What was not known was how the gateway's MCP layer
treats an array at all.

**Answer.** agentgateway rejects a JSON-RPC array outright, before the guardrail runs
(measured at v1.5.0): `HTTP 400`, `text/plain`, `mcp: fail to deserialize request body:
data did not match any variant of untagged enum JsonRpcMessage`. There is no batch bypass
on this transport.

**Probe.** Open a session with a SPIRE-minted JWT-SVID; create a bait payment with a single
`propose_payment` (EUR 6,200, `proposed`) so a successful bypass would have executed a
real over-limit payment; POST an array of a benign `propose_payment` then
`execute_payment(<bait>, 6200)` (EUR 200 over the assisted allowance) with a hand-minted
`traceparent`. No model tokens are spent.

| Observation | Value |
|---|---|
| status | `400`, `text/plain`, body as above |
| spans in the trace | 1: `agentgateway`, `POST /*`, SERVER (not `tools/call`: the request never reached MCP method dispatch) |
| `northstar.policy.decision` spans | 0 |
| `payments-mcp` spans | 0 |
| ledger afterwards | bait payment still `proposed` |

**Control.** The same two messages sent singly on the same session both worked: the
`propose_payment` was permitted with its own decision span, the `execute_payment` was
denied with its own decision span. A one-element array holding the first message got the
same `400`, so the rejection is attributable to the array container alone.

**Guard.** `tests/e2e/test_caller_identity.py::test_a_jsonrpc_batch_is_rejected_before_the_policy_hook_and_changes_nothing`
re-runs the probe on every gate run, including the control, and asserts the rejection,
zero decision spans in a trace fetched by an id the test mints, and the bait payment still
`proposed`. At v1.6.0 the route's CEL `authorization` ceiling runs before the MCP body is
parsed, and a JSON-RPC array carries no `mcp.methodName`, so a batch is now answered
`HTTP 403 authorization failed` and never reaches the deserializer; the test pins that
403 and its body (`BATCH_REJECTED_BY_CEILING`). The 400 is masked, not gone, and has not
been re-measured on v1.6.0. Still no bypass.

**Not established.** Why the deserializer rejects arrays (the source was not read at the
image's revision), behaviour of a future version, and other transports (stdio was never
probed). Nothing to file: refusing a payload shape the configuration does not opt into is
the gateway failing closed.
