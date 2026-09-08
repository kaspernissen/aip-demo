# Northstar Telemetry Contract

Status: M1.1. Revised at every milestone.

## The governing rule

Use the telemetry the frameworks actually emit. Verify per component. Instrument
only our own code. Where a convention gap exists, report it upstream — never
paper over it with a bespoke convention.

## What the default suite proves, and what only the full run proves

A bare `pytest tests/` run (no environment variable set) skips
`tests/e2e/test_browser_root_span.py` entirely — that file's own `pytestmark`
is gated behind `RUN_BROWSER_TESTS=1` (it needs a real Chromium via
Playwright). That default run proves the chain from claims-agent down to
payments-api: claims-agent → settlement-agent → payments-mcp → payments-api,
one trace, no orphans, `northstar.claim.id` correlated as far as the boundary
below states. **It proves nothing about the browser.** It does not start a
browser, does not exercise CORS, and does not confirm the root span the
milestone's whole claim depends on — that customer-ui, not a test harness,
owns the trace root.

**`./scripts/smoke-test.sh` — the actual M1.1 gate — has no default mode that
skips this.** It runs one unconditional invocation,
`RUN_BROWSER_TESTS=1 pytest tests -v`, over the whole `tests/` tree, every
time it is run. There is no environment variable, flag, or code path that
lets a caller run the gate itself and skip the browser hop — that is the
entire point of A22. A future reader who sees `./scripts/smoke-test.sh` finish
green has confirmed the browser hop; a reader who instead runs a bare
`pytest tests/` by hand — bypassing the gate — gets the weaker, default
result below, and should not conclude the top of the chain was checked.

Measured counts, from a clean cluster (`./scripts/teardown.sh &&
./scripts/setup-kind.sh --stage 1`):

| Run | Command | Result |
|---|---|---|
| Default (bypasses the gate) | `pytest tests/` (no env var) | `57 passed, 16 skipped, 1 xfailed` |
| The gate itself | `./scripts/smoke-test.sh` | `73 passed, 1 xfailed` |

(Second final-review round, finding 3: these two rows previously read `49
passed, 6 skipped, 1 xfailed` and `55 passed, 1 xfailed` — stale from before
a commit added seven tests to the suite without updating the one document
whose entire purpose is stating these numbers.
`tests/unit/test_telemetry_contract_counts.py` now guards this: it runs
`pytest --collect-only` and fails loudly if either row's sum ever again
diverges from what pytest actually collects.)

M1.2 note: the skip count in the default row grew from 7 to 15 (Task 4)
because `tests/e2e/test_settlement_llm_trace.py` (8 tests) is gated behind
the same `RUN_BROWSER_TESTS=1` flag — it needs the live cluster and spends
real model tokens on every run, so a bare `pytest tests/` must not trigger
it, while the gate (which sets that flag unconditionally) always does. Task 5
grew it again, 15 to 16: `tests/e2e/test_browser_root_span.py` gained one
test (`test_the_browser_path_settles_the_expected_amount`, asserting the
€6,200 outcome through the browser path specifically — Task 4 review,
Finding 2), and that file shares the same gate. **These are counts only;
M1.2 Task 5 also revises this document's substantive content** — including
what content capture does and does not redact now that it is configuration
rather than a hardcoded constant, which
`tests/e2e/test_settlement_llm_trace.py` measures rather than assumes.

`tests/unit/test_smoke_test_gate.py` (2 tests, counted in both rows above)
pins this distinction directly: it reads `scripts/smoke-test.sh`'s own source
and fails if the gate's pytest target ever reverts to `tests/e2e` (dropping
the 19 other `tests/unit/` harness-correctness tests silently) or if
`RUN_BROWSER_TESTS=1` ever becomes something the gate reads from the caller's
environment rather than forcing itself (A19 — this is the guard for exactly
the plan defect described two paragraphs up).

Per-service unit suites (also run by `smoke-test.sh`, before any e2e test):
claims-agent 14, settlement-agent 31, payments-mcp 7, payments-api (Go) 7,
customer-ui (vitest) 6 — all passing. (This row was stale at 19 for
settlement-agent from before M1.2 Tasks 1-4 added the PydanticAI agent's own
tests — `tests/unit/test_telemetry_contract_counts.py` cannot see per-service
rows, only the whole-tree total, so nothing caught the drift until Task 5
re-measured it directly: `cd agents/settlement-agent && .venv/bin/python -m
pytest tests -q` → 31 passed, including this task's own 7-test
`tests/test_instrumentation_settings.py`.) customer-ui's suite includes its own
A19 regression test for the traceparent-across-await fix described below
(`apps/customer-ui/NOTES.md` §2) — worth naming explicitly, since it's the
one unit suite that pins a real, previously-silent defect in this project's
own code, not only in a third-party library's behavior.

The one remaining xfail in both runs is
`tests/e2e/test_claim_correlation.py::test_claim_id_is_on_every_mcp_hop_span`,
documenting the FastMCP baggage gap below (Gap 4). It is deliberately narrow —
pinned to that one assertion, via a manual `pytest.xfail()` inside a
`try/except AssertionError`, not `@pytest.mark.xfail(strict=True)` (S3,
final-review.md — a decorator on this test would also swallow a failure
during the module-scoped fixture's own setup, e.g. the MCP hop dropping the
trace connection entirely, as a quiet `xfailed` indistinguishable from this
test's own documented condition; the manual form lets that surface as a loud
`ERROR` instead). Its behaviour is `strict=True`-equivalent in the direction
that matters: the `else` branch calls `pytest.fail` if the assertion ever
stops failing, so the day this gap closes upstream, the run fails until the
marker is removed, rather than staying silently green.

## Enabled instrumentation, as verified

| Component | Instrumentation | Version | Verified span names | Notes |
|---|---|---|---|---|
| customer-ui | OpenTelemetry JS web, manual root span | `@opentelemetry/sdk-trace-web` 2.8.0, `@opentelemetry/context-zone` 1.30.0, `@opentelemetry/exporter-trace-otlp-http` 0.219.0, `@opentelemetry/resources` 2.8.0 | `claim.submit` | Owns the trace root. Requires `traceparent`/`tracestate`/`baggage` in claims-agent's own CORS `allow_headers` (`agents/claims-agent/main.py`) so the browser's request survives preflight — the collector's own CORS requirement is different: it needs `http://localhost:8080` in `allowed_origins` (its `allowed_headers` is already wildcarded), since the browser's OTLP export to the collector carries no `traceparent` header of its own. Requires a *static* import of the injection helper — a dynamic `import()` (or any other `await`) before reading the active span loses it under `ZoneContextManager` (`apps/customer-ui/NOTES.md` §2; pinned by that service's own vitest regression test). |
| claims-agent | `a2a-sdk[telemetry]` + `opentelemetry-instrumentation-starlette` | `a2a-sdk[telemetry]==0.3.7`, `opentelemetry-instrumentation-starlette==0.65b0` | ASGI entry span `POST /`; `a2a.server.request_handlers.jsonrpc_handler.JSONRPCHandler.on_message_send` and the rest of the `a2a.server.*` call-graph spans (`DefaultRequestHandler.on_message_send`, `EventQueue.enqueue_event`/`.dequeue_event`/`.close`, `EventConsumer.consume_all`, etc.) | `DISABLE_OTEL_INSTRUMENTATION` must remain unset; `bootstrap.configure()` raises if it is `true`. Inbound W3C trace-context extraction comes from `StarletteInstrumentor` wrapping the built ASGI app (A18) — `a2a-sdk` itself extracts nothing (see Gap 1). **`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set** (`deploy/k8s/claims-agent.yaml`) — OTel Python's HTTP instrumentation defaults to the legacy spelling (`http.method`/`http.url`/`http.status_code`) on both the ASGI entry span and the outbound httpx span; without this, this service alone would mix conventions with the Go hop (`agents/claims-agent/NOTES.md` §7, which also records a measurement mistake made while proving the opt-in was genuinely needed, not assumed). |
| settlement-agent | `a2a-sdk[telemetry]` + `opentelemetry-instrumentation-starlette` + `mcp` client | `a2a-sdk[telemetry]==0.3.7`, `mcp==1.9.4`, `opentelemetry-instrumentation-starlette==0.65b0` | Same shape as claims-agent (`POST /` ASGI entry span, `a2a.server.*` call-graph spans), plus outbound `httpx` CLIENT spans (`POST`) to payments-mcp | Only agent permitted to reach payments-mcp. Same `StarletteInstrumentor` fix as claims-agent for inbound extraction. **`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set** (`deploy/k8s/settlement-agent.yaml`), same reason as claims-agent — measured legacy-by-default, confirmed stable after the opt-in (`agents/settlement-agent/NOTES.md` §6). |
| payments-mcp | FastMCP native | `fastmcp==4.0.3` | `tools/call propose_payment`, `tools/call execute_payment` | TracerProvider does **not** need to be installed before importing fastmcp on this version — `fastmcp.telemetry.get_tracer()` calls `get_tracer_provider()` fresh on every span, not once at import (measured; the brief's premise here was wrong for 4.0.3 — kept as a code convention anyway, not a correctness requirement). `FASTMCP_TELEMETRY_MODE=native` is set explicitly even though it is already the default. **`OTEL_SEMCONV_STABILITY_OPT_IN=http` is also set** (`deploy/k8s/payments-mcp.yaml`) — this governs only the *outbound httpx* span to payments-api (FastMCP's own native MCP spans are unaffected; this knob is httpx-instrumentation-specific), measured legacy-by-default, confirmed stable after (`tools/payments-mcp/NOTES.md` §3). |
| payments-api | `otelhttp` | `otelhttp v0.66.0`, otel core `v1.41.0` | `POST /payments/propose`, `POST /payments/{paymentId}/execute`, `GET /payments/{paymentId}` | HTTP attribute spelling as emitted by the pinned version: **stable** — `http.request.method` / `http.response.status_code` / `http.route`, not the legacy `http.method`/`http.status_code`. `network.protocol.name` is **not** emitted by this otelhttp version (only `network.protocol.version` is); do not assert its presence. No `OTEL_SEMCONV_STABILITY_OPT_IN` is set or needed — this version emits the stable spelling by default (A12), unlike every Python service in this table. |

**Operational fact that keeps the chain on one HTTP convention:**
`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set on all three Python services
(claims-agent, settlement-agent, payments-mcp) and on none of the others.
Without it, OTel Python's HTTP instrumentation defaults to the legacy
attribute spelling (`http.method`/`http.url`/`http.status_code`) while
payments-api's Go `otelhttp v0.66.0` emits the stable spelling by default
with no opt-in available to change that — measured, not assumed, for each
service independently (`agents/claims-agent/NOTES.md` §7,
`agents/settlement-agent/NOTES.md` §6, `tools/payments-mcp/NOTES.md` §3),
each showing the legacy capture before the opt-in and the stable capture
after. This single non-default env var is the only reason this milestone's
five services agree on one HTTP semantic convention instead of silently
splitting across languages — exactly the class of breakage A13 exists to
catch. Any service added later without it would reintroduce a mixed-convention
chain invisibly, since both spellings are individually well-formed telemetry.

**OTel library version skew, flagged as a decision, not an accident (N2/N3,
final-review.md):** the table above pins each service's own OTel version
independently, and they do not agree. On the Python side, payments-mcp runs
`opentelemetry-sdk==1.29.0`/`opentelemetry-instrumentation-httpx==0.50b0`
while both agents run `1.44.0`/`0.65b0`, and the repo-root `tests/`
harness pins `1.29.0` — three Python processes on two OTel generations, in a
milestone whose whole product is the telemetry these libraries emit. On the
browser side, `@opentelemetry/context-zone` sits at `1.30.0` alongside
`sdk-trace-web`/`resources` at `2.8.0`, and the top-level
`@opentelemetry/semantic-conventions` pin (`1.28.0`) differs from what
transitives resolve (`1.43.0`). Neither skew is a live defect today: A13
requires measuring each service's own emitted spelling independently rather
than assuming one service's pin carries over, which is exactly why the skew
hasn't silently broken anything; and the lockfile carries exactly one
`@opentelemetry/core` (`2.8.0`, confirmed by `package-lock.json`), so no
vulnerable 1.x `core` rides along the browser's `context-zone` pin (A21a
holds). Left unreconciled deliberately in this fix round rather than bumped:
harmonizing three services' dependency pins this late, with no A21-style
published-CVE forcing function, carries real regression risk for zero
measured benefit, and would need the same re-verification discipline A21/A21a
already require for a version bump (rebuild every affected service, re-run
the full suite, re-capture a live trace to confirm span shape is unchanged).
Recorded here so M1.2's planner inherits an explicit, considered choice
rather than rediscovering the skew as an accident.

**Architectural fact, applying to every component above:** none of them creates
a span of its own. Every span in this milestone comes from library
instrumentation — `StarletteInstrumentor`, `a2a-sdk`'s own `@trace_class`/
`@trace_function` decorators, `HTTPXClientInstrumentor`, FastMCP's native
tracer, `otelhttp`, and customer-ui's one hand-written `claim.submit` span
(the sole exception — it is the root, and nothing upstream of the browser
could have created it instead). Besides that one exception's own two files
(`apps/customer-ui/src/telemetry.ts`, `apps/customer-ui/src/app/page.tsx`)
and one docstring mention in `tools/payments-mcp/bootstrap.py`, there is no
`get_tracer`/`start_span`/`start_as_current_span` anywhere in this project's
own `agents/`, `tools/`, or `systems/` source; the only other hits anywhere in
the repo are both test files — a harness test probe
(`tests/e2e/test_harness.py`) and customer-ui's own regression test for the
A19 traceparent-across-await fix (`apps/customer-ui/src/a2a.test.ts`, which
opens a real span to drive the same code path `page.tsx` uses — N8,
final-review.md, an earlier version of this sentence named only the harness
probe). This predicts where M1.2 changes shape: PydanticAI is expected to be
the first library that gives this project's own code spans to open directly,
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
| `northstar.claim.id` | claims-agent at intake | W3C baggage | See "Real, measured coverage" below — **not** every span everywhere |
| `northstar.policy.number` | customer-ui root span | none | The root span only |

**Real, measured coverage, one live capture (trace `8a59dfee2b192408f4282a07a0938f43`,
62 spans):** customer-ui 1/1, claims-agent 2/23, settlement-agent 32/32,
payments-mcp 0/4, payments-api 0/2. This is asymmetric by design, not by
accident, and the true shape is more interesting than a blanket "every span"
claim would be — it is **correct by this project's own correlation rule**
(a reviewer adjudicated this explicitly): `ClaimIdSpanProcessor` copies
`northstar.claim.id` onto a span only if that value is already present as
OTel baggage in the ambient context the moment the span starts — it never
reaches outside the process to fetch it. All three Python services
(claims-agent, settlement-agent, payments-mcp) run this processor; the
counts differ only by whether baggage had actually arrived in that process's
ambient context by then. Everywhere the count is less than 100%, the reason
is either that the span predates the id being minted (customer-ui, and
claims-agent's own pre-intake spans), or that baggage never crossed the
boundary into that process at all (payments-mcp/payments-api — Gap 4) —
never a bug in the processor itself, and never something this project routes
around by reaching into third-party telemetry it does not own.

**Documented exemption 1 — customer-ui (1/1, but not as an enriched
attribute):** the root span does not carry `northstar.claim.id` as something
*added* to it — the id is minted at claims-agent's intake, after the browser
span already exists, so it predates the id entirely. The browser instead sets
it explicitly as its own attribute at creation time. Correlation for that
span is by trace ID; propagation is "none" in the table above because nothing
is *propagated into* it — the value there is authored locally, not inherited.

**Documented exemption 2 — claims-agent (2/23):** of claims-agent's own ~23
spans for a request, only 2 carry `northstar.claim.id` — the outbound A2A
`CLIENT` span and the final `EventQueue.enqueue_event` call, both created
inside the executor's own coroutine *after* the attach. The other 21 (the
ASGI entry span and every `a2a.server.*` span) run in a separate asyncio task
that `a2a-sdk` 0.3.7 spawns via `asyncio.create_task()` before the executor
has attached anything to the ambient context — `create_task` copies the
context at call time, so nothing attached afterward can reach it
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

**Documented exemption 3 — payments-mcp and payments-api (0/4, 0/2):** this
is Gap 4 below, not a bug either. `northstar.claim.id` never reaches these two
services at all, because FastMCP 4.0.3 never reads the `baggage` key out of
MCP's `params._meta` — the trace still joins correctly (traceparent crosses
the MCP boundary intact), only the domain attribute doesn't. Trace-ID
correlation is intact regardless of this attribute's absence; see Gap 4 for
the full evidence. **payments-api is not without an enrichment mechanism of
its own** (N8, final-review.md — an earlier version of this section read as
if it had none): `systems/payments-api/handlers.go`'s `withClaimID`
middleware is the Go equivalent of `ClaimIdSpanProcessor` — Go's SDK has no
baggage span processor, so this project's own middleware reads
`baggage.FromContext(r.Context())` and sets the span attribute explicitly,
correct per spec §8's rule that our own spans set the attribute rather than
relying on a library to do it. It is currently dormant for the same reason
`ClaimIdSpanProcessor` finds nothing on payments-mcp: baggage never crosses
the MCP boundary at all (Gap 4), so there is nothing in `r.Context()`'s
baggage for `withClaimID` to read. The mechanism is correct and in place;
only its input never arrives.

`tests/e2e/test_claim_correlation.py` asserts this real, capture-verified
boundary precisely — customer-ui, claims-agent's 2 spans, and settlement-agent's
32 spans, positively; payments-mcp/payments-api's absence via a narrow manual
xfail (see "What the default suite proves" above, S3) — not a blanket "every
span in every service" claim.

**Enrichment boundary:** the baggage span processor runs only in our own service
processes. Nothing is injected into telemetry emitted by infrastructure we do not
run in-process. In M2 that means agentgateway's own spans are correlated by trace
ID, not enriched.

## Content capture

**On by default, as of M1.2 Task 5** — reversed from the M1.1 spec's original
`include_content=False` default. In M1.1 there was no model to capture. The
M1.1 spec and this document both committed to `False` as the shipped default,
reasoning that "recording everything a model was told is itself a compliance
problem in insurance". That reasoning is sound but answers only half the
question: every attribute redacted is diagnostic capability spent. With
content off, a span still proves *that* €6,200 was executed and by which
tool, but not *why that amount*, on what stated grounds, or what the model
was told about the claim. Redaction is a trade, not a free safety win — so
this milestone shows the trade rather than asserting redaction is correct by
default.

`NORTHSTAR_CAPTURE_CONTENT` (`agents/settlement-agent/bootstrap.py`'s
`capture_content_enabled()`, read at `configure()` time and handed to
PydanticAI's `InstrumentationSettings(include_content=...)`) is the single
switch, an env var rather than a hardcoded constant — deployed at `"true"`
(`deploy/k8s/settlement-agent.yaml`) and meant to be flipped deliberately and
visibly on stage, for one consequential tool. Unset defaults to ON; the
values `"false"`/`"0"`/`"no"`/`"off"` (case-insensitively) turn it OFF;
anything else is ON.

`InstrumentationSettings`'s separate `include_model_request_parameters`
setting is left at its library default (`True`) regardless of
`NORTHSTAR_CAPTURE_CONTENT` — a deliberate ruling, not an oversight: it
carries the agent's static system instructions and the full tool JSON
schemas, verified byte-identical (1974 bytes) across all three `chat` spans
in a live run, and containing no claim-specific data. It is an audit asset,
not a redaction target. Separately, and regardless of either setting,
`gen_ai.tool.definitions` is emitted on every `chat` span unconditionally —
it is not gated by `include_model_request_parameters` at all.

**Measured shape, both directions** (`tests/e2e/test_settlement_llm_trace.py`,
live capture, three chat turns): `gen_ai.input.messages` /
`gen_ai.output.messages` and the `invoke_agent` span's own
`pydantic_ai.all_messages` always carry message *shape* — `role`,
`parts[].type`, tool-call ids/names/arguments — at either setting. Only
`text`/`thinking` parts additionally carry a `content` key, present (the
model's actual prose, or an empty string for a `thinking` part with no
visible extended thinking) when capture is ON, absent entirely when OFF.
`tool_call` parts never carry a `content` key at either setting — they carry
`id`/`name`/`arguments` instead — so a chat turn that is purely a tool call
(e.g. the turn that calls `execute_payment`) carries no content-bearing part
in its output message regardless of the setting; that is not a leak or a gap,
just a turn with no prose to capture.

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
- **Action:** Not filed by this project — why, stated plainly (S4,
  final-review.md): filing an upstream issue is a decision this project
  reserves for its maintainers, not one any implementation task makes
  unilaterally; this section exists so that decision can be made from
  complete, precisely-scoped evidence, not to make the decision itself. For
  the semconv-side half specifically, filing would also be low-value even if
  it were this project's call: the proposals above are already upstream and
  already argue our exact position, so a new issue would duplicate rather
  than add. The `a2a-python` zero-attribute finding and the extraction gap
  are not covered by any open issue on that exact point (searched, see
  above) and remain filing-ready whenever that decision is made. No
  `a2a.*`/`gen_ai.*` attribute was invented anywhere in this repo to fill the
  gap in the meantime.

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
  never read or written as a `_meta` key anywhere in the installed package
  (`grep -rn baggage` over the package tree returns exactly **one** match,
  `fastmcp/telemetry.py:297` — a code comment about preserving already-active
  baggage on extract, not a `_meta` key read or write). SEP-414 (which this
  project's own MCP trace-context convention already follows) documents
  `baggage` as one of the three unprefixed `_meta` keys, alongside
  `traceparent`/`tracestate`.
- **Upstream check:** `PrefectHQ/fastmcp#3998` ("OpenTelemetry: FastMCP client
  does not propagate trace context on initialize and list methods", open)
  **already reports the client side of exactly this gap** — its own body
  states the server "does not receive `traceparent` / `baggage` through MCP
  `_meta`" and asks that FastMCP's *client* requests "propagate
  `_meta.traceparent` and `_meta.baggage`" on `initialize`/`list` calls. Its
  premise is that `tools/call` (the method this project's chain actually
  uses) already propagates baggage correctly — which our own live capture
  shows is not true for the `baggage` key specifically, only for
  `traceparent`/`tracestate`. No open issue reports that the **server** side
  never reads `baggage` out of `_meta` at all, which is our precise finding;
  [#4358](https://github.com/PrefectHQ/fastmcp/issues/4358) is on the same
  file's trace-context handling but does not mention `baggage` anywhere. A
  gap independently reported by a third party (even on a narrower slice of
  it) is stronger evidence that this is real than if we were the only ones to
  have found it. The MCP spec repo has a related but distinct open proposal,
  [SEP-2028 "Automatic `_meta` to HTTP header forwarding for distributed
  tracing"](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/2028),
  about forwarding `_meta` context (including `baggage`) to *downstream HTTP
  calls a server makes*, not about a server reading `baggage` out of `_meta`
  into its own OTel context in the first place — adjacent motivation,
  different gap.
- **Action:** Not filed by this project — same reason as Gap 1 (S4,
  final-review.md): filing upstream is a decision this project reserves for
  its maintainers, not one an implementation task makes unilaterally. Unlike
  Gap 1's semconv half, filing here would not be low-value if it were this
  project's call — this is a precisely-scoped, previously-unreported gap
  (the client-side half is independently corroborated by #3998, but no open
  issue covers the server-side omission this project found) — so it is
  recorded complete and filing-ready: installed version, exact file and
  function, what is read versus not, and how it differs from #3998's and
  #4358's adjacent territory. Recorded here as upstream-contribution evidence
  (`tools/payments-mcp/NOTES.md` §6, `agents/settlement-agent/NOTES.md`
  §11). A natural upstream fix would add a `baggage` key to both
  `extract_trace_context()` and `inject_trace_context()`, symmetric with how
  they already handle `traceparent`/`tracestate` — the same fix #3998 is
  already asking for on the client side. Not worked around: no code in this
  repo reaches into FastMCP's own `_meta` handling to bridge this gap. Pinned
  by `tests/e2e/test_claim_correlation.py::
  test_claim_id_is_on_every_mcp_hop_span` — a manual `pytest.xfail()`, not
  `@pytest.mark.xfail(strict=True)` (S3; see "What the default suite proves"
  above for why), narrowed to this one assertion per a review finding — a
  fixture-setup failure upstream of it now surfaces as an ordinary `ERROR`,
  not a false-quiet `xfailed`.

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

### Gap 6: `mcp==1.9.4`'s high-level client has no route to attach `params._meta`

- **Evidence:** this project's own finding, previously recorded only in
  `agents/settlement-agent/NOTES.md` §10 and omitted from this list until now
  (S4, final-review.md). `mcp==1.9.4`'s `ClientSession.call_tool(name,
  arguments, read_timeout_seconds, progress_callback)` has no `_meta`
  parameter at all and always builds `CallToolRequestParams(name=name,
  arguments=arguments)` internally with no way to set `params._meta`
  (verified by reading `mcp/client/session.py`'s signature directly). The
  only way to attach `_meta` to a tool call on this version is to hand-build
  a `CallToolRequestParams(..., _meta=types.RequestParams.Meta(**carrier))`
  and call the lower-level `send_request(ClientRequest(CallToolRequest(...)),
  CallToolResult)` instead — duplicating `call_tool()`'s own
  name/arguments/result-type/timeout/progress-callback plumbing. This is
  exactly the technique `agents/settlement-agent/mcp_tools.py` (Task 7) and
  `tests/e2e/test_payments_mcp_trace.py` (A14a) both use for the same reason.
- **Upstream check (searched the tracker directly, not assumed):**
  [`modelcontextprotocol/python-sdk#1216` "Expose `_meta` in `ClientSession`
  methods"](https://github.com/modelcontextprotocol/python-sdk/issues/1216)
  proposes exactly this — an optional `meta: dict | None` parameter on
  `call_tool()`, mirroring what `CallToolRequestParams` already supports
  internally, which is precisely the natural fix this project's own finding
  would have asked for. **It is already resolved**: closed
  2025-10-17 (`stateReason: COMPLETED`) via
  [`#1231` "Expose `RequestParams._meta` in
  `ClientSession.call_tool`"](https://github.com/modelcontextprotocol/python-sdk/pull/1231),
  merged the same day. That fix landed in an `mcp` release newer than
  `1.9.4` — this project's pin (`tests/requirements.txt`,
  `agents/settlement-agent/requirements.txt`) — so the gap this project found
  independently is real for the version actually running here, and already
  fixed for anyone on a current `mcp` release.
- **Action:** Nothing to file — the fix already exists upstream and merged
  before this section was written. No workaround was added to route around
  it: `mcp_tools.py`'s `send_request()` approach is the documented, correct
  way to reach `params._meta` on `mcp==1.9.4`, not a patch over a bug. Bumping
  the `mcp` pin to pick up `#1231`'s simpler `call_tool(meta=...)` interface
  is a real, available simplification but is deliberately out of scope for
  this documentation-accuracy pass — verifying a new `mcp` version against
  the rest of this project's pin set is A21 territory (a version bump with
  its own re-verification), not something to fold unexplained into a
  contract-wording fix.

## Open questions

### OQ-1: Content capture is a trade, not a settled answer

Task 5 flips the default to ON (see "Content capture" above), on the
reasoning that every attribute redacted is diagnostic capability spent. That
reasoning is not the end of the discussion — it is the honest statement of
one side of a trade this project makes deliberately and visibly, not a claim
that ON is correct for every deployment. The open question this milestone
leaves for M2/M3 to answer with more than a demo's worth of evidence:

- Whether a real insurer would run this ON by default in production, given
  that `gen_ai.input.messages`/`gen_ai.output.messages` and
  `pydantic_ai.all_messages` (with capture ON) carry the full claim narrative
  the model was given and the full text it produced — plaintext, in a
  telemetry backend, for however long that backend retains spans.
- Whether a narrower middle ground exists — e.g. redacting only fields a
  policy engine (M2's Policy Decision Service) marks as sensitive, rather
  than an all-or-nothing switch — that this milestone's one env var
  deliberately does not attempt to build.

Recorded here, rather than resolved, because resolving it needs a governance
model this milestone does not yet have (see settlement.py's own "no €5,000
check" note — M1 is ungoverned by design, and this switch inherits that same
honesty: it is a lever, not a policy).
