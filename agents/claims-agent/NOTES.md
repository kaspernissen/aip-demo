# claims-agent — verified SDK surfaces and emitted telemetry

Evidence for an upstream-contribution workstream (A8). Every gap between what
`a2a-sdk` emits and the OTel GenAI/A2A semantic conventions define is recorded
here, verbatim, rather than silently patched with an invented attribute name
or a hand-rolled span. This agent is the first hop that is *both* an A2A
server (inbound, from a test client) and an A2A client (outbound, to
settlement-agent), so it is also where question 1 from the task brief —
whether outbound A2A calls propagate trace context at all — gets its first
real answer in this milestone.

## 1. Version pins: same `a2a-sdk[telemetry]==0.3.7` / OTel conflict Task 4 found

The brief pins `opentelemetry-sdk==1.29.0` /
`opentelemetry-exporter-otlp-proto-http==1.29.0` alongside
`a2a-sdk[telemetry]==0.3.7`. Reproduced independently for this agent:

```
$ uv pip install 'a2a-sdk[telemetry]==0.3.7' opentelemetry-sdk==1.29.0 opentelemetry-exporter-otlp-proto-http==1.29.0
  × No solution found when resolving dependencies:
  ╰─▶ Because a2a-sdk[telemetry]==0.3.7 depends on opentelemetry-sdk>=1.33.0
      and you require a2a-sdk[telemetry]==0.3.7, we can conclude that you
      require opentelemetry-sdk>=1.33.0.
      And because you require opentelemetry-sdk==1.29.0, we can conclude that
      your requirements are unsatisfiable.
```

Same root cause as settlement-agent's Task 4 finding (see
`agents/settlement-agent/NOTES.md` §1) — not a new defect, just reconfirmed
for this service. Pinned to the versions the resolver actually settles on:
`opentelemetry-sdk==1.44.0`, `opentelemetry-exporter-otlp-proto-http==1.44.0`,
`opentelemetry-instrumentation-httpx==0.65b0`,
`opentelemetry-instrumentation-starlette==0.65b0` — identical to
settlement-agent's pins (claims-agent needs no `mcp` package, so no separate
conflict there).

## 2. New defect (this task): `a2a-sdk[telemetry]` alone does not pull in `starlette`/`sse-starlette`

This is a genuine, previously-unrecorded plan defect (A19), found only because
claims-agent has no `mcp` dependency to mask it.

**What's true:** `a2a.server.apps.A2AStarletteApplication` (used by both
settlement-agent's and claims-agent's `main.py`, per the brief's own Step 6
sketch) requires `starlette` and `sse-starlette` to be importable. Verified in
`a2a/server/apps/jsonrpc/starlette_app.py`: it soft-imports both packages and
sets an internal `_package_starlette_installed = False` flag on failure, then
raises from `A2AStarletteApplication.__init__` if the flag is false:

```
$ python -c "
from a2a.server.apps import A2AStarletteApplication
...
app_wrapper = A2AStarletteApplication(agent_card=ac, http_handler=handler)
app_wrapper.build()
"
ImportError: Packages `starlette` and `sse-starlette` are required to use
the `A2AStarletteApplication`. It can be added as a part of `a2a-sdk`
optional dependencies, `a2a-sdk[http-server]`.
```

Checked the actual dependency metadata: `a2a-sdk`'s `telemetry` extra pulls in
only `opentelemetry-api>=1.33.0` and `opentelemetry-sdk>=1.33.0` —
`starlette`/`sse-starlette` are declared only under the separate
`http-server` (or `all`) extras. **settlement-agent's requirements.txt
(Task 4) only worked by accident**: `mcp==1.9.4` — a dependency claims-agent
does not have — pulls in `starlette>=0.27` and `sse-starlette>=1.6.1` as its
own direct dependencies, so settlement-agent's venv got starlette
transitively without ever declaring it. claims-agent has no such accidental
path, so a clean install of the brief's exact pin set (`a2a-sdk[telemetry]`
plus the brief's other packages, nothing else) leaves `A2AStarletteApplication`
unusable — not caught by `pip install` (no error there), only at first
`.build()` call, i.e. at container startup.

**Fixed:** `requirements.txt` pins `a2a-sdk[telemetry,http-server]==0.3.7`
plus explicit `starlette==1.6.0` / `sse-starlette==3.4.11` (the exact versions
that extra resolves to). A clean `uv venv --python 3.13 .venv && uv pip
install -r requirements.txt` now resolves 34 packages with zero conflicts and
`A2AStarletteApplication(...).build()` succeeds.

**Regression test (A19):** `agents/claims-agent/tests/test_main.py::
test_build_app_constructs_a_starlette_app_without_import_error` calls
`main.build_app()` directly and asserts it returns a `Starlette` instance
without raising. This fails with the exact `ImportError` quoted above if
requirements.txt reverts to the brief's original pin set (`a2a-sdk[telemetry]`
without the `http-server` extra or the explicit starlette/sse-starlette
pins) and the venv is reinstalled clean — verified directly (see the
transcript above, produced against that exact reverted pin set before the
fix).

## 3. Same class of executor defect as Task 4's settlement-agent — fixed the same way

The brief's Step 6 `executor.py` sketch enqueues a "Claim ... received.
Investigating." narration event *before* the final JSON settlement result.
Task 4 established empirically (settlement-agent NOTES.md §8) that a2a-sdk
0.3.7's blocking `message/send` handler
(`ResultAggregator.consume_and_break_on_interrupt`) returns as soon as it
sees the **first** `Message`-kind event and closes the queue — anything
enqueued after is silently dropped (`Queue is closed. Event will not be
enqueued.`). The brief's claims-agent sketch has exactly the same shape as
the brief's settlement-agent sketch that Task 4 already proved broken; the
same bug would reproduce here for the same documented reason, so it was
fixed the same way without needing to re-derive it from scratch: the
narration is logged via the standard `logging` module, and exactly one
`Message` — the final JSON result — is enqueued.

**Regression test (A19):**
`tests/e2e/test_claims_chain_trace.py::test_message_send_returns_the_settlement_json`
parses the actual `message/send` reply and asserts `claimId`,
`policyNumber`, `paymentId`, `amount`, `currency`, `status == "executed"`,
and a non-empty `narrative` — substance, not merely the absence of an error.
Verified to fail against the brief's original two-`enqueue_event` executor:
restoring the "received" narration as an enqueued event ahead of the JSON
result and rerunning the e2e test against the real deployed pod reproduces
exactly Task 4's failure mode — the response's `result.parts[0].text` is
`"Claim CLM-TEST-... received. Investigating."` (fails every
substance assertion in that test) rather than the JSON object. The file was
restored to its fixed form immediately afterward and redeployed;
`git diff` showed no residual change to the committed source.

## 4. A18 applied to claims-agent — StarletteInstrumentor, verified by capture

Same fix as settlement-agent's Task 4 fix round 1: `main.py` calls
`StarletteInstrumentor().instrument_app(app)` right after
`A2AStarletteApplication(...).build()` returns (the Starlette instance
doesn't exist until then), and `bootstrap.py` registers
`CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])`
globally, matching settlement-agent and payments-api's Go side.

**Verified by capture against the real deployed pod** — injected
`traceparent` `00-<trace_id>-<span_id>-01` on a `message/send` POST to
`http://localhost:19001/`:

```json
{
  "operationName": "POST /",
  "service": "claims-agent",
  "parent_id": "<the injected span_id>",
  "attrs": {
    "client.address": "127.0.0.1",
    "http.request.method": "POST",
    "http.response.status_code": 200,
    "http.route": "/",
    "internal.span.format": "otlp",
    "network.protocol.version": "1.1",
    "otel.scope.name": "opentelemetry.instrumentation.starlette",
    "otel.scope.version": "0.65b0",
    "server.address": "127.0.0.1:9001",
    "server.port": 9001,
    "span.kind": "server",
    "url.path": "/",
    "url.scheme": "http",
    "user_agent.original": "python-httpx/0.28.1"
  }
}
```

`entry.parent_id == <injected span_id>` and `trace.trace_id == <injected
trace_id>` (zero-padded) — the injected header genuinely produces a child
span, not a disconnected root. Asserted directly by
`tests/e2e/test_claims_chain_trace.py::
test_claims_agent_entry_span_is_parented_to_the_injected_context` against the
live cluster, stable across four consecutive runs.

**Wrinkle not present in settlement-agent's single-hop trace:** once a second
A2A hop joins the same trace (this task's whole point), **both** claims-agent
and settlement-agent name their entry span `"POST /"` — `Trace.span()`
(the harness method) matches by operation name alone across the *whole*
trace and raises `AssertionError: 2 spans named 'POST /', expected one` the
moment both are present. The e2e test filters by `.service` as well as
`.operation` instead of calling `trace.span(ENTRY_SPAN)` directly. This is a
test-file-only adjustment (the harness itself is not modified, per its
"import it, do not modify it" contract) — worth flagging because a
third A2A hop (Task 6, customer-ui or a further agent) will hit the same
ambiguity and should filter by service the same way.

## 5. Question 1 (task brief): does the outbound A2A call propagate trace context? — Yes, verified by capture

Nobody had measured this before this task. `delegation.A2ADelegate` uses a
plain `httpx.AsyncClient` (not `a2a-sdk`'s own client — see §6 for why), and
`bootstrap.py` calls `HTTPXClientInstrumentor().instrument()` globally, the
same mechanism settlement-agent's Task 4 bootstrap.py uses for its own
outbound MCP calls. Because that instrumentation reads the *ambient* OTel
context at call time and injects it into outbound headers, and because §4's
fix means the ambient context at the point `execute()` calls
`self._delegate("settlement-agent").send(...)` is the real externally-injected
context (not a disconnected root), the outbound call to settlement-agent
carries a real `traceparent` header for free — no injection code was written
by hand anywhere in `delegation.py`.

**Verified by capture, real deployed pods** (both port-forwarded, one
`message/send` call with an injected `traceparent`):

```json
{
  "operationName": "POST",
  "service": "claims-agent",
  "span.kind": "client",
  "attrs": {
    "http.request.method": "POST",
    "http.response.status_code": 200,
    "network.peer.address": "settlement-agent.northstar-agents.svc.cluster.local",
    "network.peer.port": 9002,
    "network.protocol.version": "1.1",
    "otel.scope.name": "opentelemetry.instrumentation.httpx",
    "otel.scope.version": "0.65b0",
    "server.address": "settlement-agent.northstar-agents.svc.cluster.local",
    "server.port": 9002,
    "url.full": "http://settlement-agent.northstar-agents.svc.cluster.local:9002/"
  }
}
```

and, in the **same trace ID**, settlement-agent's own `"POST /"` entry span
(service `settlement-agent`) descending from this client span, and from
claims-agent's own entry span beneath that. Asserted directly by
`tests/e2e/test_claims_chain_trace.py::
test_claims_agent_delegates_to_settlement_agent_in_the_same_trace`
(`"settlement-agent" in trace.services()`, plus an explicit ancestor-chain
check that a settlement-agent span descends from a claims-agent span) — not
merely that both service names appear somewhere in the store, which searching
by tag alone would not distinguish from two unrelated traces sharing a
Jaeger query window.

**Hop-by-hop status, this task's contribution to the full picture:**

- **Test client → claims-agent: propagates.** §4, this task, mirroring
  settlement-agent's A18 fix.
- **claims-agent → settlement-agent: propagates.** This section — the new
  hop, and the answer to question 1. `HTTPXClientInstrumentor`'s ordinary
  ambient-context injection is sufficient; no hand-rolled injection was
  needed or added.
- **settlement-agent → payments-mcp: still does not propagate** (Task 4's
  finding, unaffected by anything in this task — see
  `agents/settlement-agent/NOTES.md` §5). This stays Task 7's job via the
  shared `correlation.py` (A6), per the coordinator's explicit ruling not to
  fix that hop here. `tests/e2e/test_claims_chain_trace.py`'s
  `test_all_four_backend_services_are_in_one_trace` and
  `test_payment_was_executed_with_no_governance` remain `xfail(strict=True)`,
  citing this one gap.
- **payments-mcp → payments-api: propagates** (Task 3's finding, untouched).

**Net result of this task:** the trace now connects continuously from the
test client through claims-agent and into settlement-agent — two full A2A
hops, not one — and breaks at exactly the same single, already-known point
(the MCP boundary) as before. No new gap was introduced or found on the A2A
side.

## 6. Question 2 (task brief): what does `a2a-sdk` emit on the client side?

**Production code does not use `a2a-sdk`'s own client.** `delegation.py`
hand-rolls the A2A JSON-RPC `message/send` envelope over a plain
`httpx.AsyncClient`, matching the brief's given `A2ADelegate`
implementation and the unit tests it ships with
(`tests/test_delegation.py`, which posts to `httpx.MockTransport` and asserts
the exact wire body) — a design that predates and is independent of this
investigation. `a2a-sdk` does ship a real client with its own telemetry
(`a2a.client.ClientFactory` / `a2a.client.transports.jsonrpc.JsonRpcTransport`,
the latter decorated `@trace_class(kind=SpanKind.CLIENT)`, confirmed by
`grep -rn trace_class` over the installed package: the same decorator
pattern Task 4 found on the *server* side), but it was never wired into this
agent's production path — its typed response objects and
`A2AClientHTTPError`/`A2AClientJSONError` error model don't match the plain
dict-in/dict-out, JSON-RPC-`error`-key contract `delegation.py`'s tests
require. `a2a.client.A2AClient` (the older non-`ClientFactory` entry point)
is itself marked `[DEPRECATED]` in the installed package's own source.

**What it emits, captured directly** (throwaway script, `ClientFactory` +
`JsonRpcTransport`, plain `httpx.AsyncClient`, `ConsoleSpanExporter`, real
`message/send` call against the deployed settlement-agent via its
port-forward):

```
a2a.client.transports.jsonrpc.JsonRpcTransport.send_message            [kind=CLIENT] (root)
a2a.client.transports.jsonrpc.JsonRpcTransport._apply_interceptors     [kind=CLIENT]
a2a.client.transports.jsonrpc.JsonRpcTransport._get_http_args          [kind=CLIENT]
a2a.client.transports.jsonrpc.JsonRpcTransport._send_request           [kind=CLIENT]
```

Full attribute set on **every one** of these spans, verbatim: `{}` — empty.
No `otel.scope.*`/`internal.span.format` bookkeeping shown either in this
capture (a `ConsoleSpanExporter` artifact of not exporting via OTLP, not a
finding); the substantive result is that **zero** attributes of any kind are
present, matching exactly the pattern Task 4 found on the *server* side
(`agents/settlement-agent/NOTES.md` §3: `a2a-sdk`'s own spans there also
carried nothing beyond OTel scope bookkeeping). Confirmed by direct
`grep -rn "trace_class\|trace_function"` over the installed package that
`a2a.client.transports.grpc` and `a2a.client.transports.rest` carry the
identical `@trace_class(kind=SpanKind.CLIENT)` decorator — this pattern is
uniform across all three of `a2a-sdk`'s transport implementations, not
specific to JSON-RPC.

### Comparison against the OTel GenAI/A2A conventions

| Convention | Emitted by `a2a-sdk` 0.3.7's client transports? | Verdict |
|---|---|---|
| `gen_ai.operation.name = invoke_agent` | Absent | **GAP** (same as server side) |
| `gen_ai.conversation.id` | Absent | **GAP** |
| Agent identity (name, id, target URL) on any span | Absent — not even the destination URL | **GAP** |
| Task/context identity (`taskId`, `contextId`, `messageId`) | Absent | **GAP** |
| Any attribute at all | Absent | **GAP (total)** — identical finding to the server side |
| Meaningful span names | Yes — Python-call-graph-shaped (`a2a.client.transports.jsonrpc.JsonRpcTransport.<method>`), symmetric with the server's `a2a.server.*` naming | Useful for reading a trace; not a semconv attribute |

**This is the same upstream gap Task 4 already identified on the server
side, now confirmed to be symmetric on the client side too** — not a new,
independent finding, but worth recording precisely rather than assuming it
carries over unverified. No `a2a.*`/`gen_ai.*` attribute was invented
anywhere to fill this; it stays open upstream-issue material, same as
`agents/settlement-agent/NOTES.md` §3.

## 7. Outbound HTTP semconv spelling (A13) — measured, not assumed; opt-in genuinely needed

**Local, clean-env baseline** (no `OTEL_SEMCONV_STABILITY_OPT_IN` set,
`opentelemetry-instrumentation-httpx==0.65b0`, throwaway local HTTP server):

```
http.method       = GET
http.url          = http://127.0.0.1:.../foo
http.status_code  = 200
```

Legacy spelling — same conclusion as settlement-agent's own measurement
(Task 4 NOTES.md §6) and payments-mcp's (Task 3), despite this being a
different service.

**A real measurement mistake caught mid-task, worth recording honestly:**
this agent's manifest was deployed once with `OTEL_SEMCONV_STABILITY_OPT_IN`
already set, and an early "let's check if it's actually needed" experiment
(`kubectl set env deployment/claims-agent OTEL_SEMCONV_STABILITY_OPT_IN-`,
confirmed absent via `kubectl exec ... -- env`, then a fresh `message/send`
call) initially *appeared* to still show stable spelling — which would have
been a genuinely new and surprising finding (this exact same instrumentation
version needing no opt-in here but needing one for settlement-agent).
Re-querying Jaeger by the *specific* trace ID the test request minted
(rather than "last N minutes for this service", which can return a stale
trace still sitting in Jaeger from before the env var was removed) showed the
opt-in truly absent trace correctly carrying the **legacy** spelling
(`http.method`, `http.status_code`, `http.url`, `http.flavor`, `http.host`,
`net.peer.ip`, `net.peer.port`, `net.host.port`) on both the
`StarletteInstrumentor` entry span and the outbound `HTTPXClientInstrumentor`
client span:

```json
{
  "operationName": "POST /",
  "http.flavor": "1.1", "http.host": "127.0.0.1:9001", "http.method": "POST",
  "http.route": "/", "http.scheme": "http", "http.server_name": "localhost:19001",
  "http.status_code": 200, "http.target": "/", "http.url": "http://localhost:19001/",
  "net.host.port": 9001, "net.peer.ip": "127.0.0.1", "net.peer.port": 51074
}
```
```json
{
  "operationName": "POST",
  "http.method": "POST", "http.status_code": 200,
  "http.url": "http://settlement-agent.northstar-agents.svc.cluster.local:9002/"
}
```

**The opt-in is genuinely needed and was restored** on
`deploy/k8s/claims-agent.yaml` (`OTEL_SEMCONV_STABILITY_OPT_IN=http`),
re-verified after redeploy against a fresh trace ID: stable spelling
(`http.request.method`, `http.response.status_code`, `server.address`,
`url.path`/`url.full`) on both spans, legacy names absent — matching the
capture in §4/§5 above and the full 28-passed/5-xfailed suite run recorded in
`task-5-report.md`. The lesson generalizes: a semconv (or any trace-shape)
measurement taken via a service+time-window Jaeger query rather than an
exact trace ID is exactly the A14 unsoundness pattern applied to a different
kind of assertion — it caught itself here only because the result looked
suspicious enough to double-check.

## 8. Settlement-failure surfacing — verified end to end, not just at the unit level

The task brief's quality bar: a settlement failure must reach claims-agent's
own caller with the underlying reason intact, not flattened into a success
or swallowed. `tests/test_delegation.py::test_send_raises_on_a_jsonrpc_error`
already pins this at the unit level (`A2ADelegate.send` raises `RuntimeError`
carrying the far side's error message); `executor.py` has no `try`/`except`
around the delegate call, so nothing in this agent's own code catches it.

**Verified end to end** against the real deployed pods:
`tests/e2e/test_claims_chain_trace.py::
test_settlement_failure_reaches_the_caller_with_its_real_reason` sends a
negative `estimatedAmount`, which makes settlement-agent's
`decide_settlement` raise `ValueError("amount must not be negative")`.
Captured directly from the resulting trace — settlement-agent's own
`_cleanup_producer` span:

```json
{
  "otel.status_code": "ERROR",
  "otel.status_description": "ValueError: amount must not be negative",
  "exception.type": "ValueError",
  "exception.message": "amount must not be negative"
}
```

and the actual JSON-RPC response claims-agent's own caller receives:

```json
{"error": {"code": -32603, "message": "settlement-agent returned an error: amount must not be negative"}}
```

confirming the full round trip: settlement-agent's unhandled `ValueError`
becomes a JSON-RPC error (a2a-sdk's own request handler, verified in Task 4);
`A2ADelegate.send` turns that into `RuntimeError("settlement-agent returned
an error: amount must not be negative")`; `executor.py` doesn't catch it;
claims-agent's own a2a-sdk request handler does the same thing one hop
further out. The real reason survives two hops, verbatim.

## 9. Trace-completeness race in the e2e fixture — both processes export independently

Both claims-agent's and settlement-agent's `BatchSpanProcessor`s use the OTel
SDK's default 5-second scheduled export delay, and each process exports on
its own timer, independent of the other and of the outbound httpx client
span's own end time. `wait_for_trace_id(trace_id, operation="POST /")`
returns as soon as *any* span named `"POST /"` appears (claims-agent's own,
typically first, since it doesn't wait on settlement-agent's export) —
observed directly across repeated runs: one query returned
`services() == {"claims-agent"}` only; a later query for the identical
`trace_id` (same trace, more had landed) returned both, but with
settlement-agent's entry span temporarily an orphan (its declared parent —
claims-agent's own outbound httpx `CLIENT` span — hadn't landed yet either).

**This is an export-timing race, not a propagation defect**: given enough
time, the same trace_id always converges to the same fully-connected shape
(confirmed across 7+ repeated full runs during this task, see
`task-5-report.md`). The fixture in `tests/e2e/test_claims_chain_trace.py`
polls the *same* trace ID (never a fresh search — A14 is not violated) until
`"settlement-agent" in trace.services()` **and** the only orphan present is
the one A17 expects, or a 45s deadline elapses, before handing the trace to
any assertion. Recorded here because a naive read of the harness's
`wait_for_trace_id(operation=...)` contract could reasonably assume
"operation present" means "trace complete" once more than one service is
involved — it means only "at least one span with that name has landed."

## 10. Upstream `a2a-sdk` / Starlette deprecation warning — not new to this task

Importing `a2a.server.apps` (which imports both its `starlette_app.py` and
`fastapi_app.py` submodules unconditionally) triggers:

```
.../a2a/server/apps/jsonrpc/fastapi_app.py:21: StarletteDeprecationWarning:
'HTTP_413_REQUEST_ENTITY_TOO_LARGE' is deprecated. Use
'HTTP_413_CONTENT_TOO_LARGE' instead.
```

Reproduced identically in settlement-agent's own venv
(`python -c "import a2a.server.apps"`) — this is a pre-existing upstream
issue (an `a2a-sdk` internal module using a Starlette constant that
`starlette==1.6.0` has since renamed), not introduced by anything in this
task, and not specific to claims-agent. It surfaces once in this agent's own
pod startup log (confirmed via `kubectl logs`) but never in this repo's own
test output, since no test in `tests/` or `agents/*/tests/` imports
`a2a.server.apps` at collection time in a way that would print it to a test
run — the full `28 passed, 5 xfailed` run recorded in `task-5-report.md`
carries no warnings section. Recorded here as upstream evidence per A8; not
worked around (a `filterwarnings` suppression would hide, not fix, an
upstream naming drift).
