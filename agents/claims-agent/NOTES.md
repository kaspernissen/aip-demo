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

**Fixed:** `requirements.txt` pins explicit `starlette==1.6.0` /
`sse-starlette==3.4.11` (the exact versions `mcp==1.9.4` resolves
settlement-agent to, kept identical here for consistency) directly, rather
than through the `a2a-sdk[http-server]` extra. **Fix round 1 correction:** an
earlier version of this fix used `a2a-sdk[telemetry,http-server]==0.3.7`,
which resolves that extra's *entire* dependency set — including `fastapi`
and `annotated-doc`, neither of which claims-agent needs (it uses only
`A2AStarletteApplication`, never `A2AFastAPIApplication`; settlement-agent
runs the identical `A2AStarletteApplication` with no `fastapi` installed at
all). The two explicit pins alone are sufficient — confirmed by two clean
`uv venv --python 3.13 .venv && uv pip install -r requirements.txt` builds,
one per pin set: `a2a-sdk[telemetry]` alone resolves **48** packages;
`a2a-sdk[telemetry,http-server]` resolves **50** — a difference of exactly
two, `fastapi` and `annotated-doc`, both absent from the trimmed venv, both
present in the other. (An earlier version of this note claimed "33
packages, down from 34," and that `click` was also absent from the trimmed
venv — both wrong. `click` is present in **both** venvs; it is `uvicorn`'s
own unconditional dependency and has nothing to do with which `a2a-sdk`
extra is chosen. Caught and corrected while re-verifying for this same fix
round, before committing.) `A2AStarletteApplication(...).build()` still
succeeds against the trimmed venv.

**Regression test (A19), and a correction to what "fails" actually means
here (review finding S1):** `agents/claims-agent/tests/test_main.py::
test_build_app_constructs_a_starlette_app_without_import_error` calls
`main.build_app()` directly and asserts it returns a `Starlette` instance
without raising.

**This is not the same failure as the `ImportError` quoted above.** An
earlier version of this section (and the matching test docstring and
`task-5-report.md`) claimed the regression test "fails with the exact
`ImportError`" shown at the top of this section. That is wrong, and a
reviewer caught it by direct reproduction. `main.py:12` does
`from opentelemetry.instrumentation.starlette import StarletteInstrumentor`
(the A18 instrumentation, required — see NOTES.md §4 below) **before**
`A2AStarletteApplication` is ever constructed, and
`opentelemetry.instrumentation.starlette.__init__` itself does
`from starlette import applications` at import time. So if `requirements.txt`
reverts to the brief's original pin set (no explicit `starlette`/
`sse-starlette`, no `http-server` extra) and the venv is reinstalled clean,
`import main` fails at **collection**, not inside `build_app()`, with:

```
tests/test_main.py:30: in <module>   import main
main.py:12: in <module>              from opentelemetry.instrumentation.starlette import StarletteInstrumentor
E   ModuleNotFoundError: No module named 'starlette'
ERROR tests/test_main.py
!!!! Interrupted: 1 error during collection !!!!
```

verified directly in a clean venv built from exactly the brief's pin set
(plus the A18-required `opentelemetry-instrumentation-starlette`, which A18
forbids dropping). The a2a-sdk `ImportError` quoted at the top of this
section is real and was reproduced too (calling
`A2AStarletteApplication(...).build()` directly, bypassing `main.py`'s own
import order) — it is just not the error path this particular test exercises,
because `main.py` never gets that far without `starlette` already being
importable. **The regression coverage itself is not weakened by this
correction**: the test still goes from a hard collection error to a clean
pass across the same before/after pin change: it is the *documented reason*
that was wrong, not the test's effectiveness.

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
    "client.port": 35814,
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
- **settlement-agent → payments-mcp, as of this task (Task 5): still does not
  propagate** (Task 4's finding, unaffected by anything in this task — see
  `agents/settlement-agent/NOTES.md` §5). This stayed Task 7's job via the
  shared `correlation.py` (A6), per the coordinator's explicit ruling not to
  fix that hop here. As of this task, `tests/e2e/test_claims_chain_trace.py`'s
  `test_all_four_backend_services_are_in_one_trace` and
  `test_payment_was_executed_with_no_governance` were `xfail(strict=True)`,
  citing this one gap.
  **Superseded by Task 7 (final-review.md finding S2) — flagged here, not
  left to read as current:** settlement-agent's MCP client now injects
  `params._meta` (traceparent + baggage) via the low-level request API, and
  FastMCP's server-side extraction joins the trace. Both tests named above
  have not been `xfail(strict=True)` since Task 7 landed — they became plain
  passing assertions, confirmed at the final whole-branch review
  (`55 passed, 1 xfailed` at the first final-review round; `62 passed, 1
  xfailed` after the second round added seven more tests — see
  `docs/telemetry-contract.md`).
  **Superseded again by M2.2 (commit `477e7fb`), so the second name above no
  longer resolves:** `test_payment_was_executed_with_no_governance` was
  **retired**, not renamed and not moved. Its whole assertion was that a
  €6,200 payment reaches `status: "executed"` with nothing in the chain
  deciding whether it should — a true statement about M1 and a false one
  about M2.2, where the same payment is refused by policy. A test that
  asserts the absence of governance cannot survive governance arriving, so
  it was deleted rather than inverted, and `tests/e2e/test_claims_chain_trace.py`
  carries a `RETIRED IN M2.2` block at its site explaining that. Its
  companion, `test_all_four_backend_services_are_in_one_trace`, is
  unaffected and still passing. Only the name is stale here; everything this
  bullet says about trace propagation still holds. The one remaining gap at this boundary
  is narrower than "does not propagate": the trace joins fully; only the
  `northstar.claim.id` *attribute* still does not cross it (FastMCP never
  reads `baggage` out of `params._meta` — see `tools/payments-mcp/NOTES.md`
  §6, `agents/settlement-agent/NOTES.md` §11, and
  `docs/telemetry-contract.md` Gap 4), pinned by exactly one remaining test,
  `tests/e2e/test_claim_correlation.py::test_claim_id_is_on_every_mcp_hop_span`.
- **payments-mcp → payments-api: propagates** (Task 3's finding, untouched).

**Net result of this task (Task 5):** the trace now connects continuously
from the test client through claims-agent and into settlement-agent — two
full A2A hops, not one — and, as of this task, broke at exactly the same
single, already-known point (the MCP boundary) as before. No new gap was
introduced or found on the A2A side. **That MCP-boundary trace-joining gap is
closed as of Task 7** (see the correction above) — the chain is five services
in one trace today, not four; only the claim-id attribute gap at that same
boundary remains open.

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
pod startup log (confirmed via `kubectl logs`).

**Correction (review finding S2):** an earlier version of this section
claimed the warning appears "never in this repo's own test output." That is
only half true, and stated too broadly. It is **absent from the repo-root
`tests/` suite** (`.venv/bin/python -m pytest tests/ -q` — the
`28 passed, 5 xfailed` run in `task-5-report.md` — genuinely carries no
warnings section, since nothing under `tests/` imports `a2a.server.apps`).
It **is present in claims-agent's own unit suite**:
`agents/claims-agent/tests/test_main.py` imports `main`, which imports
`a2a.server.apps` at collection time, and that run ends
`11 passed, 1 warning` with exactly this warning printed. Verified directly,
both before and after this fix round's `requirements.txt` change (dropping
the unused `fastapi` dependency, N2, does not remove the warning — it
originates in `a2a-sdk`'s own `fastapi_app.py` module importing a deprecated
Starlette constant, independent of whether the `fastapi` *package* is
installed). Recorded here as upstream evidence per A8; not worked around (a
`filterwarnings` suppression would hide, not fix, an upstream naming drift).

## 11. Review finding S3 — `message/stream` also lost its progress narration; documented, not fixed

§3's fix (log the narration, enqueue exactly one `Message`) was chosen to
stop `message/send` from silently dropping the JSON reply. A reviewer
pointed out, correctly, that this same fix also removes the progress
narration from **`message/stream`** — a distinct transport that the brief's
Produces § says this agent supports ("streams progress") and that the
committed agent card still advertises (`"streaming": true`). Before this fix
round, neither `NOTES.md` §3 nor `task-5-report.md` said so; both described
the brief's two-`enqueue_event` shape as simply "broken," which is only true
for `message/send`.

**Verified directly, this fix round**, against the live deployed pod, a raw
`message/stream` call with `Accept: text/event-stream`:

```
content-type: text/event-stream; charset=utf-8
data: {"id":"...","jsonrpc":"2.0","result":{"kind":"message","messageId":"...",
       "parts":[{"kind":"text","text":"{\"claimId\": \"CLM-STREAM-...\", ...,
                  \"status\": \"executed\", ...}"}],"role":"agent"}}
```

Exactly one `data:` frame, the final settlement JSON, nothing before it. Had
the brief's original two-`enqueue_event` executor been kept, `message/stream`
would have delivered **both** frames (unlike `message/send`, which the
a2a-sdk internals only ever resolve to the first `Message`) — the narration
frame first, then the JSON — genuinely satisfying the brief's "streams
progress" language for that one transport, at the cost of `message/send`
silently returning only the narration (§3's bug).

**Why this was not fixed in this round, deliberately:** the two transports
share one `AgentExecutor.execute()` implementation and one sequence of
enqueued events; `RequestContext`/`EventQueue` give the executor no signal
about which transport a given call arrived over. A single hard-coded
sequence of `enqueue_event` calls therefore cannot simultaneously give
`message/send` exactly one reply message (its own internal aggregation logic
demands this) **and** give `message/stream` a genuine progress frame ahead
of the final one — satisfying one transport's semantics costs the other's.
The one construct that could resolve this without picking a transport at
authoring time is `a2a.server.tasks.TaskStatusUpdateEvent` (a2a-sdk's own
documented mechanism for interim progress, distinct from a terminal
`Message` — noted but not exercised in
`agents/settlement-agent/NOTES.md` §8): if `TaskUpdater`/`TaskStatusUpdateEvent`
genuinely doesn't trip `ResultAggregator.consume_and_break_on_interrupt`'s
first-`Message`-wins rule the way a second `Message` event does, it could
give `message/stream` its progress event back without touching
`message/send`'s single-JSON-reply guarantee. That is untested here and is a
nontrivial behavioural change to an already-fixed, already-tested code path
— not something to make as an incidental part of a fix round without an
explicit ruling, especially since Task 4's settlement-agent carries the
identical unfixed gap and a fix really wants to cover both agents
consistently (the reviewer's own framing). **Left open, flagged plainly here
and in `task-5-report.md`, for the coordinator's ruling** rather than
resolved unilaterally in either direction.

## 12. Cross-task fix (this round, coordinator-authorised): `agents/settlement-agent/requirements.txt` hardened the same way

§2 above already established that settlement-agent's `requirements.txt`
(Task 4) gets `starlette`/`sse-starlette` only by accident, via `mcp==1.9.4`
declaring them as its own unconditional dependencies. That is a real, if
currently dormant, fragility in a closed task's own tree: the day anything
in settlement-agent's dependency graph drops or replaces `mcp`, the service
stops starting with nothing in its own manifest to explain why. The
coordinator authorised fixing it as part of this round rather than leaving
it as a landmine.

**Fix:** `agents/settlement-agent/requirements.txt` gained two explicit
lines, `starlette==1.6.0` and `sse-starlette==3.4.11` — the exact versions
already present in that service's venv (confirmed via
`ls .venv/lib/python3.13/site-packages/ | grep -iE "starlette|sse"` before
the change: `starlette-1.6.0.dist-info`, `sse_starlette-3.4.11.dist-info`),
so this is a determinism fix, not a version bump. A clean
`uv venv --python 3.13 .venv && uv pip install -r requirements.txt` resolves
with the same package set as before (`mcp` still pulls the same two packages
transitively; the explicit pins just make the dependency direct rather than
incidental) and zero conflicts. settlement-agent was rebuilt and redeployed
(`./scripts/rebuild-and-deploy.sh settlement-agent`) and its own test suite
re-run against the live pod — see `task-5-report.md`'s fix-round section for
the command and verbatim output.

## 13. Task 7 finding: `a2a-sdk` 0.3.7 runs the executor in a *separate* asyncio task, so baggage attached inside it never reaches this agent's own request-handling spans

Task 7 attaches `northstar.claim.id` to OTel baggage inside
`ClaimsAgentExecutor.execute()`, at intake (`otel_context.attach(set_claim_id(...))`).
Empirical capture (real deployed pod, full chain end to end — see
`task-7-report.md`) showed the attribute reaching only **2 of claims-agent's
~23 own spans for a given request**: the outbound A2A `CLIENT` span to
settlement-agent, and the `EventQueue.enqueue_event` call for the final JSON
reply — both created directly inside the executor's own coroutine, after the
attach. Every other claims-agent span for the same request — the ASGI entry
span (`POST /`, StarletteInstrumentor) and every `a2a.server.*` span
(`DefaultRequestHandler.on_message_send`, `EventQueue.dequeue_event`,
`EventConsumer.consume_all`, etc.) — lacks it.

**Root cause, confirmed by an isolated reproduction** (a minimal script
driving `a2a.server.request_handlers.DefaultRequestHandler` directly against
a real `AgentExecutor`, with an `InMemorySpanExporter`, no network involved):
`DefaultRequestHandler._setup_message_execution` (`default_request_handler.py`
line ~278) starts the executor via `asyncio.create_task(...)` — a producer
task — while a *separate* consumer loop (in the same "main" task that also
owns the ASGI entry span) drains the `EventQueue` until it sees the first
`Message`-kind event. `asyncio.create_task()` copies the current
`contextvars.Context` at the moment it is called, which is *before* the
producer task has had any chance to run `execute()` — so nothing the executor
attaches to its own context after that point can retroactively reach the
consumer/main task's context, or any span that task creates. The isolated
reproduction confirmed this exactly: a span opened before the executor's
`context.attach()` call, and every span opened by the consumer/main task
throughout the request, showed no baggage; a span opened directly inside the
executor's own coroutine *after* the attach did.

This is not a bug in `correlation.py`, `executor.py`, or `bootstrap.py` — it
is intrinsic to how `a2a-sdk` 0.3.7 splits request handling across tasks. It
is also not something this project's own `ClaimIdSpanProcessor` should try to
route around: `a2a-sdk`'s own spans are third-party telemetry (per this
task's brief), and reaching into a2a-sdk's task-spawning behaviour to force
context to cross that boundary would be exactly the kind of hand-rolled
workaround this milestone avoids elsewhere (contrast: A18's ASGI
instrumentation fix, which used *real* framework instrumentation rather than
patching a2a-sdk).

**Correction (fix round 1, review finding — a precision error, not a scope
change):** the task-boundary explanation above is accurate for 20 of the 21
uncorrelated spans, but not for one of them: `EventQueue.close`. Read
directly, `DefaultRequestHandler._run_event_stream`
(`default_request_handler.py` lines 204-214) is exactly
`await self.agent_executor.execute(request, queue)` followed by
`await queue.close()` — both awaited sequentially inside the *same*
coroutine that `_setup_message_execution` passes to `asyncio.create_task`.
`EventQueue.close`'s span therefore runs in the producer task, not the
separate consumer/main task the rest of this section describes — the
create_task-context-copy explanation above does not apply to it. It still
lacks `northstar.claim.id`, but for a different, self-caused reason: by the
time `queue.close()` runs, `executor.py`'s own `finally: otel_context.detach(token)`
(`executor.py` lines 106-107) has already executed, because `execute()` must
return before `_run_event_stream` can reach the `queue.close()` line that
follows it. This is our own context hygiene — the `attach`/`finally: detach`
pairing that correctly scopes baggage to a single request — running one
statement too early for this one third-party span's benefit, not a2a-sdk's
task-spawning model. An earlier version of this section folded
`EventQueue.close` into "every a2a-sdk-owned span... lacks it" without
singling it out, which over-attributed a gap this project's own code causes
to a third party; corrected here (and in `task-7-report.md`) after a
reviewer traced `_run_event_stream`'s source directly. Not fixed, because
there is nothing to fix: widening `executor.py`'s attach/detach scope to
also cover code it doesn't call directly (`_run_event_stream`'s own
`queue.close()` line, in a2a-sdk) purely to grow this one span's coverage
is not something this task's brief or amendments ask for.

**Architectural fact, stated plainly for Task 8 (a substantive finding, not
a footnote):** claims-agent creates **no spans of its own**. Every span this
service emits for a request — the ASGI entry span, every `a2a.server.*`
span, the outbound A2A `CLIENT` span, `EventQueue.enqueue_event` — comes
from third-party instrumentation (`StarletteInstrumentor`, `a2a-sdk`'s own
`@trace_class`/`@trace_function` decorators, `HTTPXClientInstrumentor`).
There is no `get_tracer`, `start_span`, or `start_as_current_span` anywhere
in this project's own `agents/`, `tools/`, or `systems/` source; the only
hit for any of those three anywhere in the repo is `tests/e2e/test_harness.py`,
a harness probe, not production code. This is why `northstar.claim.id`'s
reach is governed entirely by *when* a given library's instrumentation reads
the ambient context relative to this task's `attach()`/`detach()` calls,
rather than by any span this project's own code controls the start or end
of — and it predicts where this changes shape: M1.2 introduces PydanticAI,
expected to be this project's first library that gives calling code spans to
open directly, rather than only ever wrapping calls from the outside.

**Practical consequence, by contrast with settlement-agent:** settlement-agent
receives `northstar.claim.id` via an *inbound* baggage header (from claims-agent's
outbound A2A call), extracted by `StarletteInstrumentor` at the ASGI boundary
— i.e., *before* any of settlement-agent's own internal a2a-sdk tasks are
spawned for that request. Every task spawned afterwards inherits it via the
normal context-copy-on-task-creation mechanism, so settlement-agent shows
**100% coverage** (32/32 spans in one live capture) with no exceptions, while
claims-agent — the one service that *mints* the id mid-request rather than
receiving it — cannot achieve that for its own pre-intake/consumer-task
spans. `tests/e2e/test_claim_correlation.py` asserts this real, capture-verified
boundary precisely rather than the brief's original blanket "every span in
every service" assertion (a plan defect per A19 — see `task-7-report.md`).

## M8 Task 2 measurements (2026-10-14): the waiting claim

Everything here was read from a2a-sdk 0.3.7's installed source and then measured.
Python 3.12.12 (the venv) and 3.13.5 (the image) gave the same results.

### Step 1: a stream held open for 10 minutes on every hop the browser uses

A throwaway SSE endpoint ran **inside the running claims-agent pod** (same image,
same uvicorn 0.34.0, same sse-starlette 3.4.11, with the claims-agent's CORS
origin), reached through `kubectl port-forward pod/... 19099:9099`. The endpoint
sleeps 600 s, then sends `done-after-600s`.

| Client | Result |
|---|---|
| `curl -sN --max-time 700` from the host | exit 0, final event received, **39** `: ping` comments. Wall time 664.6 s (the endpoint sleeps 600 s; the extra 64 s was not explained, the final event arrived). |
| Chromium (Playwright, headless) on `http://localhost:8080`, `fetch()` plus `getReader()` | **600.012 s**, final event received, **39** pings. |

Hops covered: browser, `kubectl port-forward`, kubelet stream, the pod's uvicorn,
sse-starlette. The 15 s `: ping` comment is sse-starlette's default
(`DEFAULT_PING_INTERVAL`). No hop's timeout had to be changed. `CORSMiddleware`'s
`allow_headers` was not widened: `Accept` is CORS-safelisted. Nothing in this
path goes through agentgateway or a Next server route.

### Step 2: the SDK's resubscribe and completion behaviour

Measured by `tests/test_task_lifecycle.py`, driving the real `DefaultRequestHandler`.

(a) `tasks/resubscribe` on a parked `input-required` task stays open, then
receives `completed` when the resume arrives. The resubscribe stream carries only
the events published after it attached (one event, the `completed` update). The
customer-ui already holds the earlier state from its claim stream.

(b) After `execute()` has returned, a resubscribe to the finished task is
refused with `InvalidParams: Task ... is in terminal state: completed`, not
`TaskNotFound`. A task the store never held (a restarted claims-agent) is
`TaskNotFound`. Both are JSON-RPC errors; the customer-ui falls back to `tasks/get`.

(c) **The SDK keeps a SERVER span open in trace A for the whole wait.** Measured
live, claim `CLM-M8T2-001`, trace `41f75218242b4029ba2435b62bc72de1`, a 75 s wait
(decision at 75 s, the task completed at 83 s):

| Span (claims-agent, kind SERVER) | Starts | Duration |
|---|---|---|
| `POST /` (the ASGI span of the claim stream) | +0.0 s | 9.1 s (ends when the customer closes the stream at `input-required`) |
| `DefaultRequestHandler._run_event_stream` | +0.0 s | **83.2 s**, the wait |
| `DefaultRequestHandler._cleanup_producer` | +9.1 s | 74.1 s |

`DefaultRequestHandler` is `@trace_class(kind=SERVER)`, which wraps every
non-dunder method, so `_run_event_stream` lives as long as the parked
`execute()`. In-process the same span measured 3.01 s for a 3 s wait.

**Ruling (controller, binding):** accepted. The span is not dropped (never-sample
rule: dropping is loss) and polling is not used. The telemetry contract and spec
§3.4 say what was measured.

### Mutation findings

- Mutating the completion to `final=True` stayed GREEN in-process on 3.12 and
  3.13, even with a client still attached to the first stream and a second
  resubscribed. The SDK's immediate close of the root queue (read in
  `EventConsumer.consume_all`) did not lose the resubscriber's event in any
  ordering tried. `final=False` is kept as the conservative choice, and a test
  pins the flag (`completed[-1].final is False`) so the mutation is RED. The
  hazard the plan feared was **not reproduced**; it is a design pin, not a
  measured defect.
- Mutating `decision = OUTCOMES.get(...)` to `"approved"` goes RED (three tests).
- Resuming a task whose ledger status is still `awaiting_approval` is refused:
  `on_message_send` raises the executor's `RuntimeError` to the caller (so the
  reviewer-ui records it as `resume_error`) and the task keeps waiting; a later
  valid resume still completes it
  (`test_a_refused_resume_leaves_the_task_able_to_complete_on_a_real_decision`).
- A resume's own `_cleanup_producer` logs `NoTaskQueue` at ERROR when it races the
  parked producer's cleanup of the same queue. Noise, not a failure.
- **A defect only the live browser found:** sse-starlette ends every SSE line with
  CRLF, so a frame ends with a blank CRLF line, not `\n\n`. The customer-ui's first
  frame reader looked for `\n\n`, passed every vitest case (written with `\n`) and
  never saw an event in Chromium. `frames()` now normalises CRLF, and a vitest case
  feeds CRLF frames (mutating the normalisation is RED). The Python harness was not
  affected (httpx `iter_lines` handles CRLF).
- **The wait also costs trace A a polling span every 0.5 s.** After the customer
  closes the claim stream the SDK starts `background_consume`
  (`default_request_handler.py`, on `CancelledError`/`GeneratorExit`), which runs
  `consume_all` with a 0.5 s dequeue timeout, and each dequeue is an
  `EventQueue.dequeue_event` SERVER span: 313 spans for an 83 s wait, still growing
  while the claim waits. Accepted under the same ruling (never drop an SDK span).
  The harness settles a claim before asserting on its trace.
- **A settlement failure used to be a JSON-RPC error; on a stream it would drop the
  connection.** The task already exists when the delegation fails, so an exception out
  of `execute()` aborted the HTTP body ("incomplete chunked read", found by
  `test_settlement_failure_reaches_the_caller_with_its_real_reason`). The executor
  now fails the task and puts the real reason in the message's `error`.
