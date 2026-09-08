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
| Default (bypasses the gate) | `pytest tests/` (no env var) | `58 passed, 17 skipped, 1 xfailed` |
| The gate itself | `./scripts/smoke-test.sh` | `75 passed, 1 xfailed` |

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
Finding 2), and that file shares the same gate. Task 5's fix round 1 grew it
once more, 16 to 17: `tests/e2e/test_settlement_llm_trace.py` gained
`test_tool_call_arguments_carries_the_settled_amount`, pinning
`gen_ai.tool.call.arguments` (fix-round Finding 2). **These are counts only;
M1.2 Task 5 also revises this document's substantive content** — including
what content capture does and does not redact now that it is configuration
rather than a hardcoded constant, which
`tests/e2e/test_settlement_llm_trace.py` measures rather than assumes.
Task 6 grew the **passed** count in both rows by one, 74→75 and 57→58: it
strengthened `tests/unit/test_telemetry_contract_counts.py` with a second
test (see below) that needs no cluster and no browser, so it runs — and
passes — in a bare `pytest tests/` too, not only behind the gate's flag.

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

**M1.2 Task 6 closed that blind spot rather than re-stating the number a
fourth time.** The stale-at-19 defect above was the third recurrence of the
same failure mode (a published count drifting because nothing automated
checks it), and the first two recurrences both went through
`test_telemetry_contract_counts.py`'s whole-tree check — which is exactly
why that check exists and why this row, structurally invisible to it, kept
drifting instead.
`test_per_service_counts_match_what_each_suites_own_runner_collects`
(same file) now parses this exact row and, for each service, calls that
service's own test runner in collect/list mode — `pytest --collect-only`
for the three Python services, `go test ./... -list '.*'` for payments-api,
`npx vitest list` for customer-ui — the same command `scripts/smoke-test.sh`
uses for each, just asking what exists rather than running it. Verified by
deliberately corrupting `settlement-agent 31` to `settlement-agent 30` in
this file: the new test went **RED**
(`settlement-agent: the contract says 30, but its own test runner collects
31 in agents/settlement-agent`), confirming it actually reads this row and
actually compares against a live count, not a cached or hand-maintained
one; reverted immediately, `__pycache__` swept, and confirmed green again
before this document was committed.

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
| settlement-agent (model) | PydanticAI `Agent.instrument_all(InstrumentationSettings(...))` (`bootstrap.py`) | `pydantic-ai-slim==2.41.0`, `version=5` (the library default, pinned explicitly) | `invoke_agent settlement_agent` (internal, ×1/request), `chat claude-sonnet-5` (client, ×3/request — one per model turn: propose, execute, final narrative), `execute_tool propose_payment`, `execute_tool execute_payment` (internal, ×1 each) | As of M1.2: the settlement decision itself, not only its transport. **25 distinct `gen_ai.*` attribute names measured** across these four span shapes — full inventory and per-span breakdown below. `include_content` is **not** hardcoded — it reads `NORTHSTAR_CAPTURE_CONTENT` (default ON; see "Content capture" below) — and `include_model_request_parameters` is left at its library default (`True`), never passed explicitly. |
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

That prediction held, and this is where M1.2 changes it: PydanticAI's own
`invoke_agent`/`chat`/`execute_tool` spans are the first spans in this project
opened by a library specifically to represent *our own agent's reasoning*,
not a network call our code happened to make.

## Export destinations: Jaeger and Dash0, and they are not symmetric

This has been live and correct since before M1.2 started, and was previously
undocumented — recorded here rather than changed, because nothing about it
needed changing. `scripts/setup-kind.sh` (around line 159, "Step 7/9:
Installing observability backbone") switches the collector's own config on
whether `DASH0_AUTH_TOKEN` is present:

```bash
if [[ -n "${DASH0_AUTH_TOKEN:-}" ]]; then
  COLLECTOR_CONFIG="$ROOT/deploy/observability/collector-config-dash0.yaml"
  # ...creates dash0-secrets, then the collector loads this config...
else
  # exports to Jaeger only — the vendor-neutral default
fi
```

Read directly from `deploy/observability/collector-config-dash0.yaml`'s own
pipeline definitions — **the two pipelines are not symmetric**:

| Signal | Exporters |
|---|---|
| Traces | `otlp_grpc/jaeger`, `otlp_grpc/dash0`, `debug` |
| Metrics | `otlp_grpc/dash0`, `debug` |
| Logs | `otlp_grpc/dash0`, `debug` |

**Traces go to both Jaeger and Dash0; metrics and logs go to Dash0 only.**
Jaeger is a traces-only backend by design, so it was never a candidate for
the other two signals — this is not an oversight, it is what "traces to a
vendor-neutral OSS backend, everything else to whichever vendor is
configured" looks like when the OSS backend genuinely can't carry metrics or
logs. **Dash0 is therefore strictly richer than the on-stage Jaeger view**:
anyone looking at Dash0 sees the same traces plus metrics and logs that never
reach Jaeger at all.

Verified live, not inferred, 2026-09-08: `dash0-secrets` present with all
four keys; the collector pod carries all four `DASH0_*` env vars; the
collector pod itself **0 restarts**, running 5h31m at measurement time; zero
export failures and zero dropped spans across 4,000 collector log lines. All
five services appear in the Dash0 dataset with the dependency chain
correctly inferred (`customer-ui` → `claims-agent` → `settlement-agent` →
`payments-mcp` → `payments-api`), `customer-ui` classified as RUM/browser,
and the M1.2 model spans (`invoke_agent settlement_agent`,
`chat claude-sonnet-5` ×3, both `execute_tool` spans) arriving with full
token-usage attributes intact. The ~1.9% error rate briefly visible on
claims-agent and settlement-agent in that same window is the Task 4
reviewer's own deliberate severance experiments (payments-mcp scaled to
zero, the MCP URL mis-targeted) landing in the same Dash0 dataset, not a
production defect — recorded here so a future reader does not go looking for
a bug that was a review technique.

This matters beyond this milestone: M4 ("Observable & evaluable") needs
somewhere for metrics and eval scores to live, and this dual-export path is
already that home — it does not need to be built later, only pointed at. The
on-stage narrative stays vendor-neutral regardless of this: Jaeger is what
the talk shows, because it is what anyone can stand up locally with no
account; Dash0's presence is additive, opt-in via one env var
(`DASH0_AUTH_TOKEN`), and never a dependency of the demo working at all.

## PydanticAI instrumentation, as measured (M1.2)

`agents/settlement-agent/bootstrap.py` applies
`Agent.instrument_all(InstrumentationSettings(include_content=capture_content_enabled(), version=5))`.
`version` is pinned at the library default, `5`; `include_model_request_parameters`
is never passed and stays at its library default (`True`) — see "Content
capture" below for exactly what each of those two independent settings gates.
The emitting library is `pydantic-ai-slim==2.41.0`
(`agents/settlement-agent/requirements.txt`).

**The complete `gen_ai.*` attribute vocabulary observed — 25 names, measured
twice (a direct A2A run and the browser-rooted exit-criterion run) and
identical both times** (task-4-report.md §3.1; every name below was measured,
none predicted):

```
gen_ai.agent.call.id
gen_ai.agent.name
gen_ai.aggregated_usage.details.cache_creation_input_tokens
gen_ai.aggregated_usage.details.cache_read_input_tokens
gen_ai.aggregated_usage.details.thinking_tokens
gen_ai.aggregated_usage.input_tokens
gen_ai.aggregated_usage.output_tokens
gen_ai.conversation.id
gen_ai.input.messages
gen_ai.operation.name
gen_ai.output.messages
gen_ai.provider.name
gen_ai.request.model
gen_ai.response.finish_reasons
gen_ai.response.id
gen_ai.response.model
gen_ai.system
gen_ai.tool.call.id
gen_ai.tool.definitions
gen_ai.tool.name
gen_ai.usage.details.cache_creation_input_tokens
gen_ai.usage.details.cache_read_input_tokens
gen_ai.usage.details.thinking_tokens
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
```

Two further attributes are content-gated and so not part of this
mode-independent inventory — `gen_ai.tool.call.arguments` and
`gen_ai.tool.call.result` on the `execute_tool` spans, present only when
`NORTHSTAR_CAPTURE_CONTENT` is ON — documented in full under "Content
capture" below, not repeated here.

**Four span shapes carry `gen_ai.*`**, emitted by scope `pydantic-ai` 2.41.0
(settlement-agent) and scope `fastmcp` (payments-mcp, pre-existing from
M1.1, unaffected by any of this):

| Span | Kind | Count/request | Carries |
|---|---|---|---|
| `invoke_agent settlement_agent` | internal | 1 | `gen_ai.agent.call.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `gen_ai.operation.name=invoke_agent`, the five `gen_ai.aggregated_usage.*` roll-ups. Also the non-`gen_ai` `agent_name`, `model_name`, `logfire.msg`, `logfire.json_schema`, and `pydantic_ai.all_messages` (the full conversation, redacted to shape only unless capture is ON). |
| `chat claude-sonnet-5` | client | 3 (one per model turn: propose, execute, final narrative) | `gen_ai.agent.call.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `gen_ai.operation.name=chat`, `gen_ai.provider.name`/`gen_ai.system` (both `anthropic` — `gen_ai.system` is the deprecated spelling; `version=5` emits both), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.tool.definitions`, the five `gen_ai.usage.*` fields (`details.thinking_tokens` only on turns where the model actually thought — deliberately not asserted, since that is the model's behaviour, not our wiring). Also `server.address=api.anthropic.com`, `operation.cost` (e.g. `0.002894`), and `model_request_parameters` (see "Content capture"). |
| `execute_tool propose_payment` / `execute_tool execute_payment` | internal | 1 each | `gen_ai.agent.call.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `gen_ai.operation.name=execute_tool`, `gen_ai.tool.call.id`, `gen_ai.tool.name`, plus `gen_ai.tool.call.arguments`/`gen_ai.tool.call.result` when capture is ON. |
| `tools/call propose_payment` / `tools/call execute_payment` (payments-mcp) | server | 1 each | `gen_ai.tool.name` only — pre-existing FastMCP behaviour from M1.1 (Gap 5 below covers what's still missing from it). |

Two things worth recording because the governing rule is to write down what
was measured, not what was expected: `gen_ai.aggregated_usage.*` is a
pydantic-ai extension with no counterpart in the GenAI semantic conventions,
carrying per-run token roll-ups on the `invoke_agent` span; and
`gen_ai.tool.definitions` is **not** redacted by `include_content=False` —
see "Content capture" for what it carries and why that means neither
`include_content` nor `include_model_request_parameters` is, on its own, a
complete redaction switch.

**For M3:** `version=5` (the default, and what this project pins) stops
`CallDeferred`/`ApprovalRequired` from marking their span `ERROR` — treated
as control flow instead of a failure, per the installed library's own
`version` docstring (verified against `pydantic_ai/_instrumentation.py`;
cross-checked in `.superpowers/research/m1.2-pydanticai-verification.md`).
This matters directly to the human-approval milestone: an approval step that
pauses execution should not read as a span-level error in this project's
trace, and pinning `version=5` (rather than a later default drifting under
us) already buys that for free, with no code of our own needed. `version=6`
would additionally emit tool results as `role='tool'` messages instead of
`role='user'` — opt-in, not adopted here, and not needed for M3's purposes.

**The trace's shape, with a model in the path** — verified independently
twice, by the implementer and by the reviewer, on two different live runs
(task-4-report.md §7, task-4-review.md §7): **5 services**
(`customer-ui`, `claims-agent`, `settlement-agent`, `payments-mcp`,
`payments-api`), **1 root** (`customer-ui:claim.submit`, matching the span id
observed in the browser's own `traceparent` header), **0 orphans**,
containing `invoke_agent settlement_agent` ×1, `chat claude-sonnet-5` ×3,
both `execute_tool` spans, both `tools/call` spans, and payments-api's
propose (`201`) and execute (`200`). **No exact span count is published
here, deliberately:** the implementer measured 88 spans on the browser
exit-criterion run; the reviewer, running the identical scenario
independently, measured 86. Same shape both times, different total — real
per-run variance (whether a given turn produced a `thinking` part, MCP
session bookkeeping), not a defect, and not a number this document commits
to. The shape above — which services, which operations, one root, zero
orphans — is the property this project actually tests and guarantees; a
specific span total is not, and publishing one here would only become the
next stale number this contract has already had to correct twice.

## Conventions adopted

- **GenAI semconv** for model calls, exercised as of M1.2 — see "PydanticAI
  instrumentation, as measured" above for the full attribute inventory.
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

**This is a recorded decision, not drift.**
`docs/superpowers/specs/2026-09-07-northstar-m1-connected-design.md`'s
§"Content capture" (lines 362-369 at the time of writing) still reads:
"Prompt and completion capture is **off by default** via
`InstrumentationSettings(include_content=False)`... Recording everything a
model was told is itself a compliance problem in insurance." That text was
correct when written and is not being corrected — the spec is not edited to
match this document, deliberately, so its own history stays legible. The
reversal is Ruling 9
(`.superpowers/sdd/2026-09-08-m1.2-intelligence/progress.md`), made after
Task 4 shipped `include_content=False` exactly matching the spec, on the
project owner's explicit instruction: *"for now we shouldn't limit or redact
things... in order to fully understand what the agent did, you might need to
trade in some information that potentially can be critical."* A future
reader who finds the spec and this contract disagreeing on the default
should read that as this milestone's own recorded reversal of an earlier,
reasoned decision — not as one of the two documents having drifted out of
date unnoticed.

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

**Measured shape** (`tests/e2e/test_settlement_llm_trace.py`, live capture,
three chat turns, ON only — the OFF-direction claims in this paragraph are
confirmed from library source below, not a second paid live run):
`gen_ai.input.messages` / `gen_ai.output.messages` and the `invoke_agent`
span's own `pydantic_ai.all_messages` always carry message *shape* — `role`,
`parts[].type`, and on a `tool_call` part, `id`/`name` — regardless of
`NORTHSTAR_CAPTURE_CONTENT`.

**Correction (Task 5 fix round 1, Finding 1): `arguments` on a `tool_call`
part is not shape, it is content, and an earlier revision of this paragraph
wrongly claimed it was present "at either setting."** It is gated by
`include_content` exactly like the `content` key on `text`/`thinking` parts.
Source (`pydantic_ai/messages.py`'s `_tool_call_otel_part`, installed
`pydantic-ai-slim` 2.41.0, the line the review cited):
```python
if settings.include_content and part.args is not None:
    ...
    call_part['arguments'] = ...
```
So with capture OFF, a `tool_call` part reduces to exactly `{"type":
"tool_call", "id": ..., "name": ...}` — no `arguments` key at all. With
capture ON (measured live), it carries the real call arguments, e.g.
`{"amount": 6200, "currency": "EUR"}` for this run's `propose_payment` call.

Only `text`/`thinking` parts carry a `content` key at all — present (the
model's actual prose, or an empty string for a `thinking` part with no
visible extended thinking) when capture is ON, absent entirely when OFF.
`tool_call` parts never carry a `content` key at either setting — they carry
`id`/`name`, plus `arguments` when ON — so a chat turn that is purely a tool
call (e.g. the turn that calls `execute_payment`) carries no content-bearing
part in its output message regardless of the setting; that is not a leak or
a gap, just a turn with no prose to capture.

**`gen_ai.tool.call.arguments` (Task 5 fix round 1, Finding 2) — the single
strongest argument for defaulting capture ON.** Set on the `execute_tool
<name>` INTERNAL span in settlement-agent, not on the `chat` span
(`pydantic_ai/capabilities/instrumentation.py`'s `_tool_span_attributes`;
`InstrumentationNames.for_version(5).tool_arguments_attr ==
'gen_ai.tool.call.arguments'`, `_instrumentation.py:751`), gated by the same
`include_content` flag:
```python
**({names.tool_arguments_attr: call.args_as_json_str()} if include_content else {})
```
Measured live, ON: the `execute_tool propose_payment` span carries
`gen_ai.tool.call.arguments = {"amount":6200,"currency":"EUR"}` — the literal
answer to "why that amount, on what stated grounds" that motivates this
task's default flip (see above). The sibling `gen_ai.tool.call.result`
(same span, same gating) carries the ledger's own record back — measured
live: `{"paymentId":"PAY-000104","claimId":"...","amount":6200,"currency":"EUR","status":"proposed"}`
on the `propose_payment` span, `status:"executed"` on `execute_payment`'s.
Pinned by `test_tool_call_arguments_carries_the_settled_amount` (ON-only;
the OFF-mode absence of both attributes follows from the same source-level
`include_content` gating just measured above and is not re-measured
span-by-span).

**`gen_ai.system_instructions`** is set on every `chat` span and on the
`invoke_agent` span (`pydantic_ai/models/instrumented.py`'s
`system_instructions_attributes()`), carrying the exact same static
instructions text as `model_request_parameters` (`"You settle motor
insurance claims for Northstar Insurance.\nYou are given a claim that has
already been assessed as covered...\n"`) — but, unlike
`model_request_parameters`, gated by `include_content`:
```python
def system_instructions_attributes(self, instructions):
    if instructions and self.include_content:
        return {'gen_ai.system_instructions': ...}
    return {}
```
So it disappears entirely when `NORTHSTAR_CAPTURE_CONTENT` is OFF;
`model_request_parameters` does not, because that one is controlled by the
separate `include_model_request_parameters` setting this project
deliberately leaves at `True` regardless (see above). Both carry the
identical instructions text when capture is ON; only `model_request_parameters`
carries it when OFF — which is the fact `test_content_capture_matches_the_deployed_mode`'s
OFF-mode needle margin depends on (see that test's own comment).

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

### Gap 7: the model's own outbound HTTP hop has no CLIENT span — already fixed upstream, not yet applied here

- **Evidence:** `anthropic==1.4.0` requires `httpx2<3,>=2.0.0` — a distribution
  genuinely separate from `httpx` — confirmed by reading the installed
  package (`anthropic/_base_client.py:41`:
  `from httpx2 import URL, Proxy, HTTPTransport, AsyncHTTPTransport`;
  `pydantic_ai/providers/anthropic.py` types its client as
  `httpx2.AsyncClient`). `bootstrap.py` applies only `HTTPXClientInstrumentor()`
  (`_module_name="httpx"`); the sibling `HTTPX2ClientInstrumentor`
  (`_module_name="httpx2"`), present in the **same already-installed**
  `opentelemetry-instrumentation-httpx==0.65b0` package, is never called.
  Measured consequence: 12 httpx-instrumented spans in a captured trace, all
  of them settlement-agent's or payments-mcp's calls to payments-mcp or
  payments-api — none to `api.anthropic.com` (task-4-report.md §6.4,
  task-4-review.md §5). The only trace of the Anthropic call anywhere is
  `server.address=api.anthropic.com` on pydantic-ai's own `chat` span.
- **What is lost:** HTTP status code (a 429 that succeeded on retry, a 529
  overload, a malformed 400 are all indistinguishable from a slow success);
  the Anthropic SDK's own internal retry count (retries collapse into one
  `chat` span's duration); wire-versus-logical latency (the `chat` span's
  duration mixes SDK serialization, retry backoff and streaming assembly
  with actual network time, with no way to separate them); and
  connection-level failure detail (DNS/TLS/connect-timeout failures surface
  only as an exception on the `chat` span, without the peer address/port an
  httpx span would carry).
- **What survives:** the hop's existence, position and duration in the
  trace; `server.address`; request/response model; response id; finish
  reasons; token counts; even `operation.cost`. The causal chain — that the
  model was called, in this position, with this outcome — is intact via
  pydantic-ai's own `chat` span. Only the transport-layer diagnostic view is
  gone.
- **Correction to how this gap was first framed in this milestone's own
  working notes:** it was initially treated as a candidate *upstream*
  finding — a library not supporting `httpx2`. **That framing does not
  survive a tracker search.** `httpx2` is `pydantic/httpx2`, a
  Pydantic-maintained, API-compatible fork of `httpx` created after concerns
  about `httpx`'s own maintenance (see
  [`open-telemetry/opentelemetry-python-contrib#4635`](https://github.com/open-telemetry/opentelemetry-python-contrib/issues/4635)
  for the history). Support for it was requested in that same issue, **fixed
  in [`#4730`](https://github.com/open-telemetry/opentelemetry-python-contrib/pull/4730)
  ("Feature/httpx2 instrumentor")**, and **shipped in the project's own
  changelog under "Version 1.44.0/0.65b0 (2026-07-16)"** — *the exact
  `opentelemetry-instrumentation-httpx` version already pinned and installed
  in this repository* (`docs/telemetry-contract.md`'s own instrumentation
  table, above). The library's installed docstring even documents the fix
  directly: "When instrumenting `httpx2` clients, use `HTTPX2ClientInstrumentor`."
  So there is no upstream gap to report here at all — the fix shipped before
  this milestone ever measured the absence.
- **Where the actual gap lives:** entirely in this project's own
  `bootstrap.py`, which calls `HTTPXClientInstrumentor()` and not also
  `HTTPX2ClientInstrumentor()`. A one-line addition
  (`HTTPX2ClientInstrumentor().instrument()`, alongside the existing call)
  would close it completely with the version already installed — no
  dependency bump, no upstream wait. Left unfixed this task, deliberately:
  `bootstrap.py` is out of scope for this milestone's plan, and the gap is
  pinned rather than silently fixed —
  `tests/e2e/test_settlement_llm_trace.py::test_the_models_own_http_hop_has_no_client_span`
  is written to fail loudly the day someone adds that line, which is the
  intended signal of the fix landing, not a regression.
- **Existing upstream activity (verified live, read-only, 2026-09-08, `gh`
  against the real repositories — no comment or issue posted anywhere):**
  the httpx2-instrumentation gap itself is closed and merged, as above. Two
  related, still-**open** issues on the same package show the httpx2 side of
  this instrumentor is actively maintained but not yet complete for every
  provider:
  [`open-telemetry/opentelemetry-python-contrib#4953`](https://github.com/open-telemetry/opentelemetry-python-contrib/issues/4953)
  ("`openai` 3.x: `server.address`/`server.port` are never recorded
  (`isinstance` check misses `httpx2.URL`)") and its companion fix-in-progress
  [`#4954`](https://github.com/open-telemetry/opentelemetry-python-contrib/pull/4954)
  — both about OpenAI's client specifically, not Anthropic's, and about a
  narrower attribute-extraction bug within the already-applied instrumentor,
  not about the instrumentor's existence. Two other observability vendors
  show the same "httpx2 exists, our tracer doesn't cover it yet" gap
  independently — [`DataDog/dd-trace-py#18780`](https://github.com/DataDog/dd-trace-py/issues/18780)
  ("[FEATURE]: Support httpx2", open) and
  [`elastic/apm-agent-python#2706`](https://github.com/elastic/apm-agent-python/issues/2706)
  ("feat: add httpx2 instrumentation", open) — corroborating that the
  httpx-to-httpx2 migration this finding sits on top of is a real,
  ecosystem-wide instrumentation transition, not a quirk unique to this
  project's dependency tree. No open issue was found on
  `anthropics/anthropic-sdk-python`, `pydantic/pydantic-ai`, or
  `open-telemetry/opentelemetry-python-contrib` asking specifically for the
  Anthropic client's own transport hop to gain a CLIENT span — this
  project's specific instance of the gap is unreported, because the generic
  fix that would close it is already shipped and merely unapplied here.
- **Status: not filed by this project, and there is nothing to file.**
  Unlike Findings 1 and 3 in `docs/upstream-findings.md`, this is not
  filing-ready material — filing would ask upstream to do something it
  already did. The only remaining action is local: apply
  `HTTPX2ClientInstrumentor()` in this project's own `bootstrap.py`, which is
  out of scope for this milestone by the plan's own boundary, not by any
  upstream limitation. See `docs/upstream-findings.md` Finding 5 for the
  full write-up in that document's format.

## Open questions

### OQ-1: Content capture is a trade, not a settled answer

Task 5 flips the default to ON (see "Content capture" above), on the
reasoning that every attribute redacted is diagnostic capability spent. That
reasoning is not the end of the discussion — it is the honest statement of
one side of a trade this project makes deliberately and visibly, not a claim
that ON is correct for every deployment. The open question this milestone
leaves for M2/M3 to answer with more than a demo's worth of evidence:

- Whether a real insurer would run this ON by default in production, given
  what capture ON actually puts in a telemetry backend as plaintext, for
  however long that backend retains spans:
  - the one templated settlement sentence the model is actually given
    (`settlement.py:122`, built only from `{claimId, amount, currency}` —
    **not** "the full claim narrative": the claim's free-text description
    never reaches the model at all, a fact `test_content_capture_matches_the_deployed_mode`'s
    own docstring relies on and an earlier revision of this paragraph
    contradicted, corrected in Task 5 fix round 1, Finding 3),
  - the full text the model produced, and
  - **the strongest item on this list**: `gen_ai.tool.call.arguments` on
    every `execute_tool` span — the literal payment amount and currency the
    model chose to propose and execute (see "Content capture" above), which
    is the concrete answer to "why that amount?" that this task's whole
    default-flip argument rests on.
- Whether a narrower middle ground exists — e.g. redacting only fields a
  policy engine (M2's Policy Decision Service) marks as sensitive, rather
  than an all-or-nothing switch — that this milestone's one env var
  deliberately does not attempt to build.

Recorded here, rather than resolved, because resolving it needs a governance
model this milestone does not yet have (see settlement.py's own "no €5,000
check" note — M1 is ungoverned by design, and this switch inherits that same
honesty: it is a lever, not a policy).

**The trade, stated concretely, now that the facts are known.** With capture
ON, `gen_ai.tool.call.arguments` on `execute_tool propose_payment` carries
`{"amount":6200,"currency":"EUR"}` — "why that amount?" is directly
answerable from the span, no reconstruction needed. With capture OFF, the
same tool-call part reduces to exactly `{"type":"tool_call","id":...,
"name":...}` (`pydantic_ai/messages.py`'s `_tool_call_otel_part`, gated on
`include_content`) and that question is unanswerable from spans — not
harder, unanswerable, because no other attribute recovers it.
`model_request_parameters` survives regardless of `NORTHSTAR_CAPTURE_CONTENT`
(gated only by the separate, always-`True` `include_model_request_parameters`)
and defines the floor of what this project retains even at maximum
redaction: the static system instructions and both tool schemas, never the
claimant's own data. That floor tells an auditor what the agent was *told it
could do*; it does not tell them what the agent was told about *this claim*,
or what it chose to do with it. Those two are genuinely different facts, and
only one of them survives full redaction.

**The experiment this open question calls for, and has not yet run** (the
project owner's own framing, preserved verbatim as the shape of the test):

1. Induce a genuine settlement failure or a wrong-amount run.
2. Attempt root-cause diagnosis from the trace with capture **off**. Record
   exactly what cannot be determined.
3. Repeat with capture **on**. Record what became determinable, and what
   claimant data that required retaining to determine it.
4. The deliverable is the difference — the specific diagnostic questions
   answerable only by retaining data a regulated insurer would rather not
   keep. That difference is the argument, not an assertion that ON or OFF is
   simply correct.

This is not academic bookkeeping: M2's Policy Decision Service and a later
reviewer UI both need to know *what the agent was told* to do their own
jobs — a policy engine cannot weigh a decision it cannot see the grounds for,
and a human reviewer cannot approve or reject what they were never shown.
Whichever way this trade is eventually resolved constrains how those two
components get designed, not only how this milestone's one env var happens
to default.
