# Northstar Telemetry Contract

Status: **M12 (Linkerd for reach)** -- current milestone, in progress, branch
`m12-linkerd`: Cilium leaves; ingress is authorized by Linkerd, egress denied by
NetworkPolicy. Proxy tracing is on (2026-10-24): the Linkerd proxies add their own
spans, see "Linkerd proxy spans" below. Before it
**M10 (Span links)**, branch `m10-span-links`: a claim that waits for a person is two traces and a link; trace A ends at
the hand-off, trace B carries the release, the resume and the A2A push. Before it **M9 (Weaver
registry)** (branch `m9-weaver-registry`): the registry in `telemetry/registry/` is built and checked;
the rename wave (every owned name is now `northstar.*`), live-check and the Jaeger
evidence read are done. **Measured and dated records below keep the names they were
measured under**; the current names are in the generated tables ("Registry-defined
attributes, spans and metrics") and in the rename map right after this paragraph.
Before it **M8 (Human in the
loop)** (branch `m8-human-in-the-loop`), on top of **M7 (Bounded)** (branch `m7-bounded`);
before it **M6 (Golden path)** (branch `m6-golden-path`, tagged `m6-verified`);
M5 merged 2026-09-15 at `e62238f`. Full revision history follows.

**Renamed in M9 (old → new).** Attributes: `policy.decision` → `northstar.policy.decision`;
`policy.rule`, `policy.caller.spiffe_id`, `policy.tool.name`, `policy.request.amount`,
`policy.request.currency`, `policy.limit.amount`, `policy.autonomy.level`,
`policy.autonomy.reason`, `policy.ledger.status` → the same under `northstar.policy.`;
`policy.decision.retryable` → `northstar.policy.retryable`; `review.decision`,
`review.payment.id`, `review.reviewer.id`, `review.reviewer.spiffe_id`, `review.resume_only` → the same
under `northstar.review.`; `review.elapsed_ms` (int, ms) → `northstar.review.wait.duration`
(double, seconds); `review.refused.trace_id` dropped (the span link carries it);
`k8s.resource.name` (the flag's name) → `northstar.contract.flag.name`;
`northstar.policy.number` → `northstar.claim.policy_number`. Spans: `policy.decision`,
`review.decision`, `review.queue`, `claim.submit`, `claim.wait` → the same under `northstar.`.
Histogram labels: `decision` (`approved`/`rejected`) → `northstar.review.decision`
(`approve`/`reject`), `autonomy.level` → `northstar.policy.autonomy.level`.

Originally M1.1, revised through M1.2 for the settlement-agent's model-driven
decision logic, through M2.1 for agentgateway's interposition and SPIRE's
workload identity, through M2.2 (Decided) for the Policy Decision
Service, the `policy.decision` span (which closes Gap 3), and agentgateway's
own guardrail telemetry, and through **M3 (Supervised)** for the two spans
this repo owns outright on the human-in-the-loop path —
`settlement.execute_approved` (with this project's first span link) and
`review.decision` — which between them close Gap 2, and through **M4
(Observable)** for the metrics this system actually emits (one of six GenAI
agent metrics, the rest published as gaps), the policy-adherence score spans,
and **OQ-1, answered**, and through **M5 (Progressive autonomy)** for the
`feature_flag.evaluation` span event the OpenFeature hook puts on every
autonomy-level resolution. Revised at every milestone. OpenSearch, its
Dashboards evidence view and the PDS's own `feature_flag.evaluation` log
record were removed on 2026-10-24; sections below that describe them are
records of what was measured.

**M8 (Human in the loop): `senior-settlement-agent` is retired.** Every
mention of it below, of the "senior identity", of `settlement.execute_approved`
and of the A2A approval hop is **dated history of M3 to M7**: it records what
was measured then and is not rewritten. What changed is that the reviewer-ui
now releases an approved payment itself, calling `execute_payment` through
agentgateway under its own SPIFFE ID, so the `policy.decision` span for a human
release carries `policy.caller.spiffe_id` = the reviewer-ui, `policy.rule` =
`human-approved.execute`, and a new attribute, `policy.ledger.status`: what the
PDS read from payments-api for that payment (`awaiting_approval` for a permit; the
settlement agent's own execute decision carries it too, `proposed` for a permit).
The approver is also a different `service.name`, so the "same service name for
both settlement Deployments" caveats below no longer apply.

**M10: trace A ends at the hand-off, and nothing polls the wait (this supersedes the
M8 Task 2 measurement that follows).** The claims-agent records `input-required` and
`execute()` returns, so trace A, the agent's work, is finished at the hand-off: no
SDK span stays open for the wait and the 0.5 s `EventQueue.dequeue_event` polling
spans are gone. The person's work is trace B: `northstar.review.decision`, the
release through agentgateway under `human-approved.execute`, then the resume. The
resume is the claims-agent span `northstar.a2a.resume` (registry span, internal),
which carries **one span link** back to the hand-off span in trace A, with link
attribute `northstar.a2a.link.reason = resume`. When the task completes, a2a-sdk
pushes the Task (the outcome only: claims-agent's `TerminalPushNotificationSender`
sends terminal states only, so trace A holds no push and no `customer-ui-server`
span) to the customer-ui server (`POST /a2a/push`, service
`customer-ui-server`, token in `X-A2A-Notification-Token`), and the browser's
`GET /claims/events` ends with the outcome. The parked span context and the parked
reply travel in the input-required update's task metadata
(`northstar.a2a.parked_span_context`, `northstar.a2a.parked_reply`): A2A task
metadata, not telemetry, so neither is a registry attribute. The push token is on no
span (a test asserts it). The `TaskStore` is still in memory: a claims-agent restart
loses the waiting claims. `tests/harness/jaeger.py` no longer has
`WAIT_POLLING_SPANS` or `steady_ignoring`, and the gated tests
`test_trace_a_is_finished_at_the_hand_off` and
`test_the_resume_runs_in_trace_b_and_links_to_trace_a` assert the above. Predicted by
the gated tests, not yet measured live: the gate run after the final review measures it.

**M8 Task 2 (superseded by M10, kept as the dated measurement): trace A kept one SDK span open for the whole wait.** The
claims-agent parked the first `execute()` while a task was `input-required`
(`agents/claims-agent/executor.py`), and a2a-sdk 0.3.7's `DefaultRequestHandler`
is `@trace_class(kind=SERVER)`, which wraps `_run_event_stream`. So in trace A
the claims-agent's SERVER span
`a2a.server.request_handlers.default_request_handler.DefaultRequestHandler._run_event_stream`
lasts as long as the wait: **83.2 s** for a 75 s wait (claim `CLM-M8T2-001`, trace
`41f75218242b4029ba2435b62bc72de1`), and `..._cleanup_producer` 74.1 s. The
claim's own `POST /` span ended at 9.1 s, when the customer closed the claim
stream at `input-required`. This was accepted, not worked around: the span is not
dropped (never sample) and the wait is not polled. Wherever this document says
trace A "ends" at `input-required`, that is the browser's `claim.submit` span and
the claim stream, not every span in the trace.

Two consequences, both measured. While a claim waits, the spans created under that
open span read as **orphans** in Jaeger (their parent has not been exported yet),
and the SDK's background consumer adds an
`a2a.server.events.event_queue.EventQueue.dequeue_event` SERVER span about every
0.5 s (it polls the task's queue with a 0.5 s timeout after the customer closes the
stream), so trace A **keeps growing for the whole wait**: 313 spans for the 83 s
claim above. Both stop when the human decides. So a test that asserts on a settled
trace decides the claim first (`tests/harness/live.py::release_wait`), and
`JaegerClient.wait_for_trace_id(..., steady_ignoring=WAIT_POLLING_SPANS)` does not
count those polling spans as movement (it still returns them).

## The governing rule

Use the telemetry the frameworks actually emit. Verify per component. Instrument
only our own code. Where a convention gap exists, report it upstream — never
paper over it with a bespoke convention.

## Running the tests

`tests/unit` is hermetic: no cluster, no API key, no network.

A bare `pytest tests/` needs the cluster and the port-forwards that
`./scripts/start-demo.sh` opens. Without them it fails with
`httpx.ConnectError` and `kubectl` errors rather than skipping: a suite that
skips when it cannot reach what it asserts on looks green while proving
nothing. Every e2e module that spends Anthropic tokens or launches a browser
sits behind `RUN_BROWSER_TESTS=1`, so a bare run costs no tokens and proves
nothing about the A2A chain or the browser hop.

`./scripts/smoke-test.sh` is the gate. It runs every per-service suite, then
`RUN_BROWSER_TESTS=1 pytest tests -v` over the whole tree, and fails if
anything was skipped.

One xfail is expected:
`tests/e2e/test_claim_correlation.py::test_claim_id_is_on_every_mcp_hop_span`
documents the FastMCP baggage gap (Gap 4 below). It calls `pytest.xfail()`
inside a `try/except AssertionError` around that one assertion, so a failure in
the fixture's setup still shows as an error, and its `else` branch fails the
run the day the gap closes upstream.

## Enabled instrumentation, as verified

| Component | Instrumentation | Version | Verified span names | Notes |
|---|---|---|---|---|
| customer-ui | OpenTelemetry JS web, manual root span | `@opentelemetry/sdk-trace-web` 2.8.0, `@opentelemetry/context-zone` 1.30.0, `@opentelemetry/exporter-trace-otlp-http` 0.219.0, `@opentelemetry/resources` 2.8.0 | `claim.submit` | Owns the trace root. Requires `traceparent`/`tracestate`/`baggage` in claims-agent's own CORS `allow_headers` (`agents/claims-agent/main.py`) so the browser's request survives preflight — the collector's own CORS requirement is different: it needs `http://localhost:8080` in `allowed_origins` (its `allowed_headers` is already wildcarded), since the browser's OTLP export to the collector carries no `traceparent` header of its own. Requires a *static* import of the injection helper — a dynamic `import()` (or any other `await`) before reading the active span loses it under `ZoneContextManager` (`apps/customer-ui/NOTES.md` §2; pinned by that service's own vitest regression test). |
| claims-agent | `a2a-sdk[telemetry]` + `opentelemetry-instrumentation-starlette` | `a2a-sdk[telemetry]==0.3.7`, `opentelemetry-instrumentation-starlette==0.65b0` | ASGI entry span `POST /`; `a2a.server.request_handlers.jsonrpc_handler.JSONRPCHandler.on_message_send` and the rest of the `a2a.server.*` call-graph spans (`DefaultRequestHandler.on_message_send`, `EventQueue.enqueue_event`/`.dequeue_event`/`.close`, `EventConsumer.consume_all`, etc.) | `DISABLE_OTEL_INSTRUMENTATION` must remain unset; `bootstrap.configure()` raises if it is `true`. Inbound W3C trace-context extraction comes from `StarletteInstrumentor` wrapping the built ASGI app (A18) — `a2a-sdk` itself extracts nothing (see Gap 1). **`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set** (`deploy/k8s/claims-agent.yaml`) — OTel Python's HTTP instrumentation defaults to the legacy spelling (`http.method`/`http.url`/`http.status_code`) on both the ASGI entry span and the outbound httpx span; without this, this service alone would mix conventions with the Go hop (`agents/claims-agent/NOTES.md` §7, which also records a measurement mistake made while proving the opt-in was genuinely needed, not assumed). |
| settlement-agent | `a2a-sdk[telemetry]` + `opentelemetry-instrumentation-starlette` + `mcp` client (**which now instruments itself**) | `a2a-sdk[telemetry]==0.3.7`, `mcp==2.2.0` (2026-09-11; was `1.28.1`, and `1.9.4` before that), `opentelemetry-instrumentation-starlette==0.65b0` | Same shape as claims-agent (`POST /` ASGI entry span, `a2a.server.*` call-graph spans), plus outbound `httpx2` CLIENT spans (`POST`/`GET`/`DELETE`) to payments-mcp, plus — as of M1.2 Task 8 — outbound `httpx2` CLIENT spans (`POST`) to `api.anthropic.com`, nested under each `chat` span, plus — **new with `mcp` 2.2.0** — the SDK's own CLIENT spans `MCP send initialize` and `MCP send tools/call <tool>`, one per JSON-RPC request it sends | Only agent permitted to reach payments-mcp. Same `StarletteInstrumentor` fix as claims-agent for inbound extraction. **`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set** (`deploy/k8s/settlement-agent.yaml`), same reason as claims-agent — measured legacy-by-default, confirmed stable after the opt-in (`agents/settlement-agent/NOTES.md` §6); the same opt-in applies to both `HTTPXClientInstrumentor` and `HTTPX2ClientInstrumentor`, since both share one `get_tracer` call in the same module. **2026-09-11 correction: the outbound spans to payments-mcp used to come from `HTTPXClientInstrumentor` and now come from `HTTPX2ClientInstrumentor`.** `mcp` 2.x's streamable-HTTP client is built on `httpx2`, a distribution separate from `httpx`; the `httpx2` instrumentor was already applied here (M1.2 Task 8, for `anthropic`), so nothing had to be added, but the attribution in this cell was wrong the moment the bump landed. See "the `mcp` client's own telemetry, as measured" below for the new spans' full inventory. |
| settlement-agent (model) | PydanticAI `Agent.instrument_all(InstrumentationSettings(...))` (`bootstrap.py`) | `pydantic-ai-slim==2.42.0` (2026-09-11; `2.41.0` until then), `version=5` (the library default, pinned explicitly) | `invoke_agent settlement_agent` (internal, ×1/request), `chat claude-sonnet-5` (client, ×3/request — one per model turn: propose, execute, final narrative), `execute_tool propose_payment`, `execute_tool execute_payment` (internal, ×1 each) | As of M1.2: the settlement decision itself, not only its transport. **28 distinct `gen_ai.*` attribute names measured at ON (25 at OFF)** across these four span shapes — full inventory and per-span breakdown below. `include_content` is **not** hardcoded — it reads `NORTHSTAR_CAPTURE_CONTENT` (default ON; see "Content capture" below) — and `include_model_request_parameters` is left at its library default (`True`), never passed explicitly. |
| claims-agent (intake model) | PydanticAI `Agent.instrument_all(InstrumentationSettings(...))` plus `HTTPX2ClientInstrumentor` (`agents/claims-agent/bootstrap.py`, held code-identical to settlement-agent's by `test_instrumentation_settings.py`) | `pydantic-ai-slim==2.42.0`, `version=5` | Only for a claim written as free text: `invoke_agent claims-agent` (internal) and one `chat claude-sonnet-5` (client) per round, with the model's HTTP hop as its child. A JSON claim emits none of these | Not yet measured on a live cluster. The claim id is in baggage before the model runs, so these spans should carry `northstar.claim.id`; `gen_ai.conversation.id` is the claim id. The same `NORTHSTAR_CAPTURE_CONTENT` switch applies, and with it on the `chat` span carries the customer's own description (see "Content capture"). |
| agentgateway | native, `frontendPolicies.tracing` in its own config | `cr.agentgateway.dev/agentgateway:v1.5.0` (Resource `service.version=1.5.0`; scope `agentgateway` with **no** `otel.scope.version`; Resource `telemetry.sdk.language=rust`, `telemetry.sdk.name=opentelemetry`, `telemetry.sdk.version=0.32.1`) | Two spans per MCP message: a SERVER span named after the JSON-RPC method (`initialize`, `notifications/initialized`, `tools/call`) or, for non-JSON-RPC HTTP, after method-and-route (`GET /*`, `DELETE /*`); and a CLIENT span named `<method> <target>` (`initialize payments-mcp`), which for tool calls becomes `tools/call <target>_<tool>` (`tools/call payments-mcp_propose_payment`) | Interposed by M2.1 Task 3; **verifies a SPIRE-minted JWT-SVID as of Task 4** (`jwtAuth`, `mode: strict`); and, **as of M2.2, calls out to the Policy Decision Service on every `tools/call`** (`mcpGuardrails`, `kind: remote`, `failureMode: failClosed`) and refuses the call when the answer is deny. That third job is visible in the trace as one extra span per tool call, a CLIENT span named `Guardrail` — see "agentgateway's guardrail telemetry, as measured" below; it is agentgateway's own span and nothing here invented it. The verification is visible in the trace, not only in a 401: its SERVER spans gained one attribute, `jwt.sub`, carrying the verified token's subject — for a JWT-SVID, the caller's SPIFFE ID (measured `spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent`). The gateway derives it from the token it validated; nothing in this project sets it. Measured **absent** from the CLIENT spans, consistent with the gateway stripping the credential on the outbound leg (`if !self.preserve_token { self.location.remove(req)?; }`, `crates/agentgateway/src/http/jwt.rs`) — so payments-mcp receives no identity to check. Emits **a third HTTP attribute vocabulary**, neither stable nor OTel-legacy — see "agentgateway instrumentation, as measured" below, which is the one place this document's HTTP-convention claim has an exception. Exports OTLP/**gRPC** to the collector on 4317, the only component here that does not use OTLP/HTTP on 4318. Task 2 shipped it with no `tracing` block at all, i.e. emitting nothing; Task 3 added one, which is why this row did not exist before. |
| payments-mcp | FastMCP native | `fastmcp==4.0.3` | `tools/call propose_payment`, `tools/call execute_payment` | TracerProvider does **not** need to be installed before importing fastmcp on this version — `fastmcp.telemetry.get_tracer()` calls `get_tracer_provider()` fresh on every span, not once at import (measured; the brief's premise here was wrong for 4.0.3 — kept as a code convention anyway, not a correctness requirement). `FASTMCP_TELEMETRY_MODE=native` is set explicitly even though it is already the default. **`OTEL_SEMCONV_STABILITY_OPT_IN=http` is also set** (`deploy/k8s/payments-mcp.yaml`) — this governs only the *outbound httpx* span to payments-api (FastMCP's own native MCP spans are unaffected; this knob is httpx-instrumentation-specific), measured legacy-by-default, confirmed stable after (`tools/payments-mcp/NOTES.md` §3). |
| policy-decision-service | OTel Python SDK, applied by this project's own `pds/telemetry.py` | `opentelemetry-sdk==1.44.0` (Resource carries `service.name`, `service.instance.id`, `telemetry.sdk.language=python`, `telemetry.sdk.name=opentelemetry`, `telemetry.sdk.version=1.44.0` — and **no** `service.version`; scope `northstar.policy-decision-service` with no `otel.scope.version`) | one span, `policy.decision`, SERVER kind, one per `tools/call` the gateway sends for a decision | **The one component in this system whose span is entirely this project's own** — no framework emits a policy decision, which is what Gap 3 below was about. It opens the span by hand because there is nothing wrapping it that would: the gRPC servicer is the top of its own call stack. Everything below that name is the real SDK — `TracerProvider`/`BatchSpanProcessor`/OTLP-HTTP exporter, endpoint and protocol from the standard `OTEL_EXPORTER_OTLP_*` env vars, and a `CompositePropagator(TraceContextTextMapPropagator, W3CBaggagePropagator)` registered explicitly. It joins the caller's trace because agentgateway forwards `traceparent` to the guardrail hook (measured, M2.2 Task 1 run C), so the decision span is a genuine **child of the gateway's `tools/call` SERVER span** — a sibling of the `Guardrail` CLIENT span, not its child, because the gateway propagates the context it received rather than its own client span's. **The span status stays `Unset` on a deny, deliberately:** a refusal is this service working, and an ERROR span for the one thing this milestone exists to do would read as a fault. Attribute inventory below. |
| payments-api | `otelhttp` | `otelhttp v0.71.0`, otel core `v1.46.0` (**2026-09-11 (M3): was `otelhttp v0.66.0`, otel core `v1.41.0`.** The bump is what retired the reviewer-ui trace-flags workaround — see Finding 11 and the section below) | `POST /payments/propose`, `POST /payments/{paymentId}/execute`, `GET /payments/{paymentId}` | HTTP attribute spelling as emitted by the pinned version: **stable** — `http.request.method` / `http.response.status_code` / `http.route`, not the legacy `http.method`/`http.status_code`. `network.protocol.name` is **not** emitted by this otelhttp version (only `network.protocol.version` is); do not assert its presence. No `OTEL_SEMCONV_STABILITY_OPT_IN` is set or needed — this version emits the stable spelling by default (A12), unlike every Python service in this table. |
| reviewer-ui (M3 Task 4; corrected M3 final-review fix round) | OTel Python SDK, applied by this project's own `bootstrap.py`; `HTTPXClientInstrumentor` for outbound calls | `opentelemetry-sdk==1.44.0` | `review.decision`, SERVER kind, one per submitted approve/reject decision; `review.queue`, SERVER kind, one per `GET /` | **Two** owned spans, and no framework instrumentation: no `opentelemetry-instrumentation-fastapi`/`-starlette` is applied, the same way `policy-decision-service`'s own row above is "entirely this project's own". Both are hand-created (`review.py`, `main.py`) and each is the root of its own trace, matching apps/customer-ui's `claim.submit` pattern rather than PDS's (a child of the caller's propagated context). `GET /healthz` still produces nothing. `HTTPXClientInstrumentor` runs, so the outbound calls to payments-api and to senior-settlement-agent's A2A endpoint get CLIENT spans and carry `traceparent`. **This cell read "`review.decision`, the only span" and "`GET /` and `GET /healthz` produce no spans at all" from Task 4 until the final-review fix round (R36), and both halves were wrong — `GET /` always produced the outbound CLIENT span this same cell then went on to describe, and that span, having no parent, rooted a trace of its own.** **2026-09-11 (M3): this cell used to end "`bootstrap.py` additionally registers `_Level1CompatibleTraceContextPropagator`, which masks the injected trace-flags byte to `SAMPLED`".** It no longer does, and there is no longer anything to mask: payments-api moved to otel-go v1.46.0, whose extractor accepts a Level 2 `-03` flag byte, so `bootstrap.py` registers the stock `TraceContextTextMapPropagator` like every other service here and reviewer-ui's outbound `traceparent` goes out as `…-03`. Measured inside the deployed pod: `get_global_textmap()._propagators` is `['TraceContextTextMapPropagator', 'W3CBaggagePropagator']` and an injection from a real sampled span produced `00-4fd1495b2de7214c10469a745ba986aa-cdb290f086f73d06-03`. The removal is under "reviewer-ui's own instrumentation" below. Attribute inventory below. |

**Operational fact that keeps *this project's own services* on one HTTP
convention — and, as of M2.1 Task 3, the one component it cannot reach:**
`OTEL_SEMCONV_STABILITY_OPT_IN=http` is set on all three Python services
(claims-agent, settlement-agent, payments-mcp) and on none of the others.
Without it, OTel Python's HTTP instrumentation defaults to the legacy
attribute spelling (`http.method`/`http.url`/`http.status_code`) while
payments-api's Go `otelhttp v0.71.0` (v0.66.0 until 2026-09-11) emits the stable spelling by default
with no opt-in available to change that — measured, not assumed, for each
service independently (`agents/claims-agent/NOTES.md` §7,
`agents/settlement-agent/NOTES.md` §6, `tools/payments-mcp/NOTES.md` §3),
each showing the legacy capture before the opt-in and the stable capture
after. This single non-default env var is the only reason this project's
five own services agree on one HTTP semantic convention instead of silently
splitting across languages — exactly the class of breakage A13 exists to
catch. Any service added later without it would reintroduce a mixed-convention
chain invisibly, since both spellings are individually well-formed telemetry.

**M2.1 Task 3 correction: the chain is no longer all one HTTP convention, and
this project has no lever that would make it so.** Until the gateway was
interposed, "five services agree" described the whole trace. It now describes
the five services this project *builds*. agentgateway is a sixth participant
this project only configures, and **in the pinned `v1.5.0`, and only there,** it
emits `http.method` / `http.path` /
`http.status` / `http.host` / `http.version` — measured off live spans, not
inferred — which is neither the stable spelling the other five converge on nor
OTel's legacy spelling (that one is `http.status_code` / `http.url`). The
version qualifier is load-bearing, not pedantry: upstream PR #3182 is already
merged and moves the access log — and these span attributes with it — onto
stable semconv behind an opt-in, so this sentence becomes **false** at the next
image bump rather than merely stale. There is
no `OTEL_SEMCONV_STABILITY_OPT_IN` equivalent to set: those are raw string keys
in its own Rust source, not routed through a semconv layer that could be
switched. So this is stated as a measured property of the deployed system
rather than fixed, and it is pinned by
`tests/e2e/test_gateway_trace.py::test_gateway_span_attributes_are_exactly_what_was_measured`
so the day it changes upstream, a test says so. Full detail in the section
below.

**OTel library version skew, flagged as a decision, not an accident (N2/N3,
final-review.md):** the table above pins each service's own OTel version
independently, and they do not agree. On the Python side, payments-mcp runs
`opentelemetry-sdk==1.29.0`/`opentelemetry-instrumentation-httpx==0.50b0`
while both agents run `1.44.0`/`0.65b0`, and the repo-root `tests/`
harness pins `1.29.0` — three Python processes on two OTel generations, in a
milestone whose whole product is the telemetry these libraries emit.
(**2026-09-11 (M3): the Python half of this skew is closed.** `payments-mcp`
and the repo-root `tests/` harness are both on `1.44.0`/`0.65b0` now, so every
Python process in this repo runs one OTel generation — the current PyPI
releases, published 2026-07-16. The paragraph's closing argument, that
harmonizing the pins "carries real regression risk for zero measured
benefit", was answered rather than ignored: it **did** carry a measured
benefit, since `payments-mcp`'s 1.29.0 turned out to be load-bearing by
accident (Finding 11), and the re-verification discipline it asks for was
performed — every affected service rebuilt and redeployed, every suite re-run,
and live traces re-captured. The browser-side skew is untouched and still
stands as written.) On the
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

**Architectural fact, as it stood through M1.1 and M1.2, and with M2.2's one
deliberate exception stated immediately after it:** none of the components
above creates a span of its own. Every span in this milestone comes from
library instrumentation — `StarletteInstrumentor`, `a2a-sdk`'s own `@trace_class`/
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

**M2.2 adds the second hand-written span in this project, and the reason is
the same shape as the browser's.** `platform/policy-decision-service/pds/`
calls `start_as_current_span("policy.decision")` directly, because — exactly
like customer-ui's root — nothing else could have created it. The PDS is a
gRPC servicer at the top of its own call stack, and no instrumentation
library anywhere emits "a policy decided this", which is the substance of
Gap 3 below. The grep claim in the paragraph above is unchanged and was
re-run for M2.2: there is still no `get_tracer`/`start_span`/
`start_as_current_span` in `agents/`, `tools/` or `systems/`. The new site is
in `platform/`, a directory that did not exist when that sentence was
written, and it is named here rather than allowed to make the sentence
quietly false by living outside the three directories it enumerates.

That prediction held, and this is where M1.2 changes it: PydanticAI's own
`invoke_agent`/`chat`/`execute_tool` spans are the first spans in this project
opened by a library specifically to represent *our own agent's reasoning*,
not a network call our code happened to make.

## Export destinations: Jaeger, Prometheus and Dash0

The collector runs `deploy/observability/collector-config.yaml`. When
`DASH0_AUTH_TOKEN` is set, `scripts/setup-kind.sh` also passes
`deploy/observability/collector-dash0-overlay.yaml` as a second `--config`.
The collector merges the two: the overlay declares `otlp_grpc/dash0` and
restates each pipeline's exporter list with it added
(`tests/unit/test_collector_dash0_overlay.py` checks the merged result).
The pipelines, read from those two files:

| Pipeline | Exporters |
|---|---|
| `traces` | `otlp_grpc/jaeger`, `otlp_grpc/weaver` (stage 4 only), `otlp_http/agentevals` (stage 4 only), `otlp_grpc/dash0` (with the overlay), `debug` |
| `metrics` | `otlp_http/prometheus` (stage 4 only), `otlp_grpc/weaver` (stage 4 only), `otlp_grpc/dash0` (with the overlay), `debug` |
| `logs` | `otlp_grpc/weaver` (stage 4 only), `otlp_grpc/dash0` (with the overlay), `debug` |

**Traces go to Jaeger and Dash0; metrics go to Prometheus and Dash0; logs go
to Dash0.** The logs are AgentEvals' `gen_ai.evaluation.result` events. Every
signal also goes, as a copy and never a sample, to Weaver live-check
(`otlp_grpc/weaver`), which checks it against the registry. No processor
renames an attribute, so every destination gets the canonical names. Below
stage 4, `scripts/setup-kind.sh` strips every region of both files bracketed
by `# BEGIN stage-4-only` / `# END stage-4-only`, because a collector must not
name a backend its cluster does not have.

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
a bug that was a review technique. **This paragraph is transcribed from this
project's own progress ledger's 2026-09-08 entry ("Dash0 export — already
live, verified rather than assumed"), not re-measured for this revision** —
the ledger (`.superpowers/sdd/2026-09-08-m1.2-intelligence/progress.md`) is
git-ignored working history, not a file distributed with this repository, so
cite it as the dated ledger entry it is rather than a path a reader can
necessarily open.

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
The emitting library is `pydantic-ai-slim==2.42.0`
(`agents/settlement-agent/requirements.txt`) — `2.41.0` until 2026-09-11.

**The 2.41.0 → 2.42.0 bump changed nothing in this section, and that is a
measurement, not an assumption.** The published inventory below is the reason
this bump got its own step: if the emitted attribute set had moved, this
document would have been wrong the moment the pod restarted. It was re-measured
either side of the bump against the same scenario (a refused €6,200 settlement,
content capture ON) by reading every `pydantic-ai`-scoped span's whole attribute
set out of Jaeger and diffing: trace `e086b1efd1ae4fba819455c5d9f5fa75` at
2.41.0 against trace `e5e160bd3e3d4189a951419b52df8bc7` at 2.42.0. The two
differ in exactly one key across the whole comparison — `otel.scope.version`,
`2.41.0` → `2.42.0`. Same 28 `gen_ai.*` names, same four span shapes, same
per-span attribute sets, same counts per request. Upstream's own v2.42.0 release
notes list one compatibility note, one feature and four bug fixes, none of them
touching instrumentation — but the diff above is what this section rests on, not
that list.

**Fix round (final-review.md SF-3): this inventory previously published the
OFF-mode figure as though it were the whole vocabulary — "25 names...
complete" — and said "two further attributes are content-gated."** Both
halves were wrong. The 25 were measured under Task 4's `include_content=False`
default; Task 5 flipped the shipped default to ON and nobody re-derived the
set, so the document contradicted itself 30-odd lines later (`gen_ai.system_
instructions`'s own gating section, below) without anyone reconciling the two.

**The complete `gen_ai.*` attribute vocabulary at the shipped default
(`NORTHSTAR_CAPTURE_CONTENT` ON) — 28 names, re-measured for this fix round
off two live traces in Jaeger, identical in both:**
`21ca441e8fec4a8f89bb8ec51cc6e1ca` (2026-09-08 16:03 UTC, direct-A2A
scenario) and `18372fc00318f9ca75d4ae3e58bf9db7` (2026-09-08 15:34 UTC,
browser-rooted scenario) — every name below was read off those two spans
sets, none predicted:

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
gen_ai.system_instructions
gen_ai.tool.call.arguments
gen_ai.tool.call.id
gen_ai.tool.call.result
gen_ai.tool.definitions
gen_ai.tool.name
gen_ai.usage.details.cache_creation_input_tokens
gen_ai.usage.details.cache_read_input_tokens
gen_ai.usage.details.thinking_tokens
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
```

**Three of these are content-gated** and disappear when
`NORTHSTAR_CAPTURE_CONTENT` is OFF — `gen_ai.tool.call.arguments` and
`gen_ai.tool.call.result` on the `execute_tool` spans, and
`gen_ai.system_instructions` on the `chat`/`invoke_agent` spans (see its own
gating section below for the source). With those three removed, the
remaining 25 are exactly Task 4's original OFF-mode measurement — that
number was never wrong, only mislabeled as "complete" when it was one mode's
subset.

**Four span shapes carry `gen_ai.*`**, emitted by scope `pydantic-ai` 2.42.0
(settlement-agent) and scope `fastmcp` (payments-mcp, pre-existing from
M1.1, unaffected by any of this):

| Span | Kind | Count/request | Carries |
|---|---|---|---|
| `invoke_agent settlement_agent` | internal | 1 | `gen_ai.agent.call.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `gen_ai.operation.name=invoke_agent`, the five `gen_ai.aggregated_usage.*` roll-ups. Also the non-`gen_ai` `agent_name`, `model_name`, `logfire.msg`, `logfire.json_schema`, and `pydantic_ai.all_messages` (the full conversation, redacted to shape only unless capture is ON). |
| `chat claude-sonnet-5` | client | 3 (one per model turn: propose, execute, final narrative) | `gen_ai.agent.call.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `gen_ai.operation.name=chat`, `gen_ai.provider.name`/`gen_ai.system` (both `anthropic` — `gen_ai.system` is the deprecated spelling; `version=5` emits both), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.tool.definitions`, the five `gen_ai.usage.*` fields (`details.thinking_tokens` only on turns where the model actually thought — deliberately not asserted, since that is the model's behaviour, not our wiring). Also `server.address=api.anthropic.com`, `operation.cost` (e.g. `0.002894`), and `model_request_parameters` (see "Content capture"). **As of M1.2 Task 8**, each of these three spans has exactly one httpx-instrumented CLIENT child (`otel.scope.name=opentelemetry.instrumentation.httpx`, `POST`, stable HTTP semconv — see Gap 7 below), the model's own wire-level hop. |
| `execute_tool propose_payment` / `execute_tool execute_payment` | internal | 1 each | `gen_ai.agent.call.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `gen_ai.operation.name=execute_tool`, `gen_ai.tool.call.id`, `gen_ai.tool.name`, plus `gen_ai.tool.call.arguments`/`gen_ai.tool.call.result` when capture is ON. |
| `tools/call propose_payment` / `tools/call execute_payment` (payments-mcp) | server | 1 each | `gen_ai.tool.name` only — pre-existing FastMCP behaviour from M1.1 (Gap 5 below covers what's still missing from it). |

Two things worth recording because the governing rule is to write down what
was measured, not what was expected: `gen_ai.aggregated_usage.*` is a
pydantic-ai extension with no counterpart in the GenAI semantic conventions
(`open-telemetry/semantic-conventions-genai` — pydantic-ai's own source agrees,
calling it "a custom namespace, not part of the OpenTelemetry Semantic
Conventions"), carrying per-run token roll-ups on the `invoke_agent` span; and
`gen_ai.tool.definitions` is **not** redacted by `include_content=False` —
see "Content capture" for what it carries and why that means neither
`include_content` nor `include_model_request_parameters` is, on its own, a
complete redaction switch.

**For M3:** `version=5` (the default, and what this project pins) stops
`CallDeferred`/`ApprovalRequired` from marking their span `ERROR` — treated
as control flow instead of a failure, per the installed library's own
`version` docstring (verified against `pydantic_ai/_instrumentation.py`;
cross-checked in this project's own working notes, dated 2026-09-08
("m1.2-pydanticai-verification") — git-ignored working history, not a file
distributed with this repository, so cite it as the dated note it is rather
than a path a reader can necessarily open).
This matters directly to the human-approval milestone: an approval step that
pauses execution should not read as a span-level error in this project's
trace, and pinning `version=5` (rather than a later default drifting under
us) already buys that for free, with no code of our own needed. `version=6`
would additionally emit tool results as `role='tool'` messages instead of
`role='user'` — opt-in, not adopted here, and not needed for M3's purposes.

**M2.1 Task 3: the shape below now has a proxy hop in the middle of it.**
`payments-mcp`'s `tools/call <tool>` SERVER span is no longer a direct child of
settlement-agent's `execute_tool <tool>` span. The measured chain is:

```
settlement-agent  execute_tool propose_payment            internal
  settlement-agent  MCP send tools/call propose_payment   client   (mcp 2.2.0's own span)
    settlement-agent  POST                                client   (httpx2, to agentgateway)
      agentgateway    tools/call                           server
        agentgateway  tools/call payments-mcp_propose_payment client
          payments-mcp  tools/call propose_payment          server
            payments-mcp  POST                              client
              payments-api  POST /payments/propose          server
```

**2026-09-11: one level was added to this chain by a dependency bump, not by
this project.** The block above had seven rows, `execute_tool` straight to the
httpx `POST`, until `mcp` went 1.28.1 → 2.2.0. 2.x ships OpenTelemetry on by
default and opens its own `MCP send tools/call <tool>` CLIENT span around each
request, so the httpx span is now that span's child — and it is an `httpx2`
span, because 2.x's transport is built on `httpx2`. Re-measured off trace
`e086b1efd1ae4fba819455c5d9f5fa75`, not edited to match a changelog. The two
e2e assertions described immediately below are unaffected: both are ancestry
checks, not parent-of checks.

One trace id throughout, every span parented on the one directly above it,
`agentgateway` added as a **sixth service**. Two pre-existing e2e assertions
were made true again rather than loosened for this:
`test_settlement_agent_trace.py`'s outbound-target needle now names the
gateway, and `test_settlement_llm_trace.py::test_mcp_hop_still_carries_the_trace`
moved from "direct child of `execute_tool`" to "descends from `execute_tool`
**and** its immediate parent is an agentgateway span" — ancestry plus a new
constraint, so net strictness is unchanged. The rest of this section's shape
claims are unaffected: still one root through the browser path, still zero
orphans beyond A17's known synthetic one, and still no published span total.

**The trace's shape, with a model in the path.** This paragraph's original
figures (M1.2, verified independently twice, by the implementer and by the
reviewer, on two different live runs — task-4-report.md §7 and
task-4-review.md §7 **of M1.2's own working history**, not M2.1's) were **5
services** (`customer-ui`, `claims-agent`, `settlement-agent`, `payments-mcp`,
`payments-api`), before the gateway existed. **M2.1 correction, re-derived
live rather than carried forward:** querying Jaeger directly
(`GET /api/traces?service=customer-ui&operation=claim.submit&lookback=48h`)
returns 4 browser-rooted traces; the 3 post-reroute ones each show **6
services** (`agentgateway` interposed between `settlement-agent` and
`payments-mcp`, matching the "sixth service" correction above), **1 root**
(`customer-ui:claim.submit`, matching the span id observed in the browser's
own `traceparent` header), **0 orphans**, and 20 `agentgateway` spans; the one
remaining pre-reroute trace still shows the original 5-service, 0-gateway-span
shape. Both shapes contain `invoke_agent settlement_agent` ×1,
`chat claude-sonnet-5` ×3, both `execute_tool` spans, both `tools/call` spans,
and payments-api's propose (`201`) and execute (`200`). **No exact span count
is published here, deliberately:** the M1.2 implementer measured 88 spans on
the browser exit-criterion run and the M1.2 reviewer measured 86 on an
identical scenario; re-derived for M2.1 across the three post-reroute traces
above, the whole-trace totals were 111, 117 and 113. Same shape every time,
different total — real per-run variance (whether a given turn produced a
`thinking` part, MCP session bookkeeping, the `a2a-sdk` polling span), not a
defect, and not a number this document commits to. The shape above — which
services, which operations, one root, zero orphans — is the property this
project actually tests and guarantees; a specific span total is not, and
publishing one here would only become the
next stale number this contract has already had to correct twice.

**M2.2 correction: `policy-decision-service` is a seventh service in this
trace, and it is in the denied trace as well as the permitted one.** Measured
by querying Jaeger for browser-rooted traces directly
(`GET /api/traces?service=customer-ui&operation=claim.submit&lookback=48h`)
and reading each one's service set, roots, orphans and `policy.decision`
attributes — four such traces carry all seven services: the denials
`6eed5ffa79e3eb758b4367eb75cb5a0d` and `4ccb95666e75dafc0700639baf37ead0`
(€6,200, `policy.decision=deny` on `execute_payment`) and the permits
`3b78201273e80fed79ff01264892ca78` and `59850646e3c7d3d35b74e5072ccc23c0`
(€5,800, permitted and executed). All four show **1 root**
(`customer-ui:claim.submit`) and **0 orphans**. The shape claim, and still
only the shape:

- **`policy.decision` ×2 per settlement**, one per tool call, each a child of
  the gateway's own `tools/call` SERVER span for that call. (**N5,
  whole-branch review: this is the M2.2-era count, from before M3's
  `refuse_payment` call existed** — it is pre-`refuse_payment`, describing the
  two-call `propose_payment`/`execute_payment` shape only. See "So **two or
  three `policy.decision` spans appear per settlement**" further below for
  the current figure, which accounts for `refuse_payment`'s third call.)
- **`Guardrail` ×2 per settlement**, agentgateway's own CLIENT span for the
  policy hop, sibling to the `policy.decision` span under the same parent.
- **`payments-api` is in the denied trace too**, with the propose (`201`) and
  **no** execute. That is the precise sense in which "no payment is executed"
  is a claim about the execute leg: the proposal really is recorded, and only
  the execution is refused.
- One root, zero orphans, one trace id throughout, exactly as before.

**Still no whole-trace span total, and M2.2 hardens the reason rather than
softening it.** Across the four browser-rooted traces above the totals are
125, 123, 119 and 117 — same shape every time. The two direct-A2A reference
traces hold 67 (denied) and 92 (permitted), and Task 7's own script-driven
permit run measured 116. Seven numbers, one shape. Two independent sources of
variance are now stacked: the `a2a-sdk` `dequeue_event` polling span
(measured 10–16) that this document has always declined to publish a total
against, and — new in M2.2 — a denial short-circuiting the second tool call's
entire subtree, so a denied run is genuinely smaller than a permitted one by a
margin that itself varies. Publishing a total here would be publishing a
number that changes with the verdict.

## The `mcp` client's own telemetry, as measured (M3 remaining-bumps, step 1)

**New as of 2026-09-11, and not authored here.** `mcp` 1.28.1 shipped no
OpenTelemetry code at all — `grep -rl opentelemetry` over the installed 1.28.1
package tree returns nothing. `mcp` 2.2.0 ships three such modules
(`mcp/shared/_otel.py`, `mcp/server/_otel.py`,
`mcp/shared/jsonrpc_dispatcher.py`), and upstream's v2.0.0 release notes say so
outright: "OpenTelemetry tracing ships on by default". So settlement-agent
gained spans it does not create, from a library bump, which is exactly the
class of change this document exists to record rather than discover later.

Measured off a live refused settlement (trace
`e086b1efd1ae4fba819455c5d9f5fa75`, 145 spans, six services, one orphan and
that orphan the test's own synthetic root):

| Span name | Kind | Count per settlement | Attributes, verbatim |
|---|---|---|---|
| `MCP send initialize` | CLIENT | 3 (one per `_call`, because this client opens a fresh session per tool call) | `mcp.method.name=initialize`, `jsonrpc.request.id=1` |
| `MCP send tools/call <tool>` | CLIENT | 3 (`propose_payment`, `execute_payment`, `refuse_payment`) | `mcp.method.name=tools/call`, `jsonrpc.request.id=2` |

Both carry `otel.scope.name=mcp-python-sdk` with **no** `otel.scope.version`,
and both carry `northstar.claim.id` — that last one is *this project's*
`ClaimIdSpanProcessor` stamping every span in the process, not the SDK.

Where they sit: `execute_tool <tool>` (PydanticAI) → **`MCP send tools/call
<tool>`** → `POST` (the httpx2 CLIENT span) → agentgateway. They are a new
level in the chain, not a replacement for one, and the hop still survives them
— the parent chains further down this section were re-measured after the bump.

**Two convention gaps, recorded as they appear rather than as they should be**
(same rule as the agentgateway section below):

- The span name is `MCP send {method} {target}`, not the OTel MCP semantic
  conventions' `{mcp.method.name} {target}` that payments-mcp's own server-side
  spans follow (`tools/call execute_payment`). The `MCP send ` prefix is a
  literal in `jsonrpc_dispatcher.py`.
- No `gen_ai.tool.name`. The tool name appears only inside the span *name*,
  so it is not queryable as an attribute on the client side — where
  payments-mcp's server span does carry it. The attribute set is the two keys
  in the table and nothing else.

**And one behaviour that is not a gap but changes what this repo's own code is
for.** `mcp` 2.2.0's dispatcher injects W3C trace context into `params._meta`
itself, immediately after opening the span above:

```python
span_name = f"MCP send {method}{f' {target}' if isinstance(target, str) else ''}"
with otel_span(span_name, kind=SpanKind.CLIENT, attributes={...}):
    # SEP-414: inject W3C trace context; `_meta` stays on the wire even with a no-op tracer.
    inject_trace_context(out_meta)
```

`agents/settlement-agent/mcp_tools.py` has hand-injected exactly that since
M1.1 Task 7, because at the time nothing else would. It still does, and the
SDK's own injection now overwrites it with a strictly deeper parent (the `MCP
send` span) in the same trace — so the wire is correct either way and nothing
was broken by leaving it. **It is redundant, and that is a finding, not a
silent deletion:** see Gap 6 below, and `docs/upstream-findings.md` Finding 4.
Removing it is its own change with its own proof, deliberately not folded into
a dependency bump.

## agentgateway instrumentation, as measured (M2.1 Task 3)

`cr.agentgateway.dev/agentgateway:v1.6.0` (bumped from v1.5.0 in M8 Task 0),
standalone (ConfigMap + Deployment +
Service, not the Helm chart), in `northstar-platform`, with
`deploy/platform/agentgateway/config.yaml`'s `frontendPolicies.tracing`
exporting OTLP/gRPC to `otel-collector.observability.svc.cluster.local:4317`.
`settlement-agent`'s `PAYMENTS_MCP_URL` names it, so every MCP call the model
makes crosses it.

**Every name in this section was read off a live span in Jaeger and dumped in
full before being written down. None was predicted from agentgateway's source
or its documentation.** agentgateway is not this project's component, so the
no-invented-telemetry rule applies to it without exception. In-tree evidence:
`tests/e2e/test_gateway_trace.py`, which asserts exact set equality on the
attribute keys below in both directions — a key appearing is a failure as much
as a key disappearing.

**Re-measured on v1.6.0, 2026-10-13.** v1.6.0 moved the gateway's SERVER spans
to the stable HTTP semantic conventions (upstream PR
[`#3182`](https://github.com/agentgateway/agentgateway/pull/3182)). Six
attribute keys left and six arrived; the tables below are the v1.6.0 dumps, and
`tests/e2e/test_gateway_trace.py` pins them. The span shape, the CLIENT spans and
the Guardrail span are unchanged, and the Guardrail span still carries the
legacy `http.host`, `http.method` and `http.path` (see its own section).
Anything below that names `http.status`, `http.method`, `http.path`,
`http.host`, `http.version` or `src.addr` on a SERVER span was measured on
v1.5.0 and says so.

### Span shape

Two spans per MCP message — one for the request received, one for the request
made upstream:

| Span | Kind | Parent | Count/request |
|---|---|---|---|
| `initialize` | server | settlement-agent's httpx `POST` CLIENT span | 2 (one MCP session per tool call) |
| `initialize payments-mcp` | client | agentgateway `initialize` | 2 |
| `notifications/initialized` | server | settlement-agent's httpx `POST` | 2 |
| `notifications/initialized payments-mcp` | client | agentgateway `notifications/initialized` | 2 |
| `GET /*` | server | settlement-agent's httpx `GET` | 2 |
| `GET payments-mcp` | client | agentgateway `GET /*` | 2 |
| `tools/call` | server | settlement-agent's httpx `POST` | 2 (one per tool the model called) |
| `tools/call payments-mcp_<tool>` | client | agentgateway `tools/call` | 1 each for `propose_payment`, `execute_payment` |
| `DELETE /*` | server | settlement-agent's httpx `DELETE` | 2 |
| `DELETE payments-mcp` | client | agentgateway `DELETE /*` | 2 |

Three things in that table are worth stating outright because they are easy to
get wrong from the outside:

- **The SERVER span for a tool call is named just `tools/call`** — the JSON-RPC
  method, with no tool name in it. An operator filtering Jaeger by operation
  name therefore sees two identically-named `tools/call` spans per settlement
  and must read `gen_ai.tool.name` to tell `propose_payment` from
  `execute_payment`. Only the CLIENT span carries the tool in its name.
- **`payments-mcp_<tool>` is agentgateway's own multiplexing prefix**
  (`<target>_<tool>`), not a name payments-mcp would recognise. payments-mcp's
  own span for the same call is `tools/call propose_payment`.
- **`GET /*` and `DELETE /*` fall back to method-and-route naming** because
  those two HTTP verbs of the streamable-HTTP transport carry no JSON-RPC
  method to name the span after; `/*` is the configured route's match pattern.

### Attributes on the `tools/call` SERVER span

Read off a live span on v1.6.0; 22 keys, the complete set, nothing omitted
(Jaeger's own `span.kind` and `internal.span.format` excluded, as below):

```
client.address               e.g. "10.244.0.15"
duration                     e.g. "3ms"  (a string; see the note below)
gateway                      "default/default"
gen_ai.tool.name             "propose_payment" | "execute_payment"
http.request.method          "POST"
http.response.status_code    200
jwt.sub                      "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
listener                     "listener0"
mcp.method.name              "tools/call"
mcp.resource.type            "tool"
mcp.session.id               (base64 JSON, see the note below)
mcp.target                   "payments-mcp"
network.protocol.version     "1.1"
otel.scope.name              "agentgateway"
protocol                     "mcp"
route                        "default/route0"
server.address               "agentgateway.northstar-platform.svc.cluster.local"
server.port                  3000
span.id                      the span's own id, repeated as an attribute
trace.id                     the trace's own id, repeated as an attribute
url.path                     "/mcp"
url.scheme                   "http"
```

The v1.5.0 set had `http.host`, `http.method`, `http.path`, `http.status`,
`http.version` and `src.addr` where this one has the six stable keys. `src.addr`
was `ip:port`; `client.address` is the bare IP.

**`jwt.sub` is the newest of those and the only one M2.1 Task 4 added** — this
block held one fewer key when Task 3 measured it, and the key appeared because
Task 4 gave the route a `jwtAuth` policy, not because the gateway was upgraded.
It carries the `sub` claim of the token the gateway verified, which for a
SPIRE-minted JWT-SVID is the caller's SPIFFE ID. Two properties of it are
measured rather than assumed, and both are asserted in
`tests/e2e/test_gateway_trace.py`: the value is the **caller's** identity
(`spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent`) and not
the audience the token was minted for, and the key is **absent from the CLIENT
span** below — the gateway strips the credential before making the upstream
call, so on the outbound leg there is no verified token to attribute anything
to. The practical consequence, recorded plainly because it is a real limit of
this design: payments-mcp learns nothing about who called it.

### Attributes on the `tools/call payments-mcp_<tool>` CLIENT span

Six keys, and that is all — no HTTP attributes, no duration, no session id:

```
agentgateway.outbound.kind      "Primary"
agentgateway.outbound.subtype   "Mcp"
gen_ai.tool.name                "propose_payment" | "execute_payment"
mcp.method.name                 "tools/call"
mcp.target                      "payments-mcp"
otel.scope.name                 "agentgateway"
```

### Attributes on a *refused* call's SERVER span (M2.1 Task 5)

Every span above comes from a call the gateway **accepted**. A refused call
produces a span too, and it is a different shape — worth publishing because
"can you see the refusal in the trace?" is the question this whole project
exists to answer, and here the answer is yes.

Measured by sending an MCP `initialize` with **no** `Authorization` header
and a hand-minted `traceparent`, then looking the trace up by that exact id.
The gateway joined that trace rather than starting one of its own, and emitted
exactly one span. Re-measured on v1.6.0 on 2026-10-13 (trace
`0e23d152d01236e676d13f8b3fbc160e`); its attribute set, with Jaeger's own two
excluded as described below — 21 keys, the complete set, nothing omitted:

```
client.address                  "127.0.0.1"
duration                        "0ms"
error                           true
error.type                      "JwtAuth"
gateway                         "default/default"
http.request.method             "POST"
http.response.status_code       401
listener                        "listener0"
network.protocol.version        "1.1"
otel.scope.name                 "agentgateway"
otel.status_code                "ERROR"
otel.status_description         "authentication failure: no bearer token found"
protocol                        "http"
reason                          "JwtAuth"
route                           "default/route0"
server.address                  "localhost"  (the Host header the probe sent)
server.port                     18777  (the probe's port-forward)
span.id                         "f7a0162c65f6dbd2"
trace.id                        "0e23d152d01236e676d13f8b3fbc160e"
url.path                        "/mcp"
url.scheme                      "http"
```

(On v1.5.0 the same span had 20 keys: the legacy `http.*` six, `src.addr`, and
no `error.type`. `error.type` is new in v1.6.0 and repeats `reason`. It also
made `error` and `error.type` a scalar-and-object pair that OpenSearch could
not map, which mattered until OpenSearch was removed.)

Four things differ from the accepted path, and each matters diagnostically:

- **The span is named `POST /*`, not `initialize`.** The `jwtAuth` policy runs
  as an HTTP filter before the MCP backend is consulted, so the request is
  never parsed as JSON-RPC and there is no method name to name the span
  after. Consequently **there are no `mcp.*` attributes at all** — no
  `mcp.method.name`, no `mcp.session.id`, no `mcp.target`, no
  `gen_ai.tool.name`. An operator cannot tell *which tool* was refused from
  this span, only that a POST to `/mcp` was rejected.
- **No `jwt.sub`.** Nothing was verified, so there is no subject to attribute,
  and a refused call is correctly anonymous in the trace. That is the honest
  behaviour and also a real limitation: the trace shows *that* an
  unauthenticated caller was refused, never *who* tried.
- **`otel.status_code = ERROR`, with `otel.status_description` carrying the
  reason verbatim** — the same `TokenError` text the HTTP response body
  returns. This is the one place the refusal reason is machine-readable from
  telemetry alone.
- **`reason = "JwtAuth"`**, a coarse category naming the policy that refused,
  and **`error = true`** (from v1.6.0 also `error.type = "JwtAuth"`). None of
  these appears on any accepted span.

There is **no CLIENT span**, which is the trace-level evidence that the
refusal was a refusal rather than a failed forward.
`tests/e2e/test_caller_identity.py` asserts the behavioural half of this (no
tool result comes back); this section records the telemetry half. Deliberately
*not* asserted by a test: pinning `otel.status_description` would pin an
upstream error string with no added safety, since the behavioural assertions
already cover the property.

### A ceiling-denied call's SERVER span, in brief (M8 Task 4)

A `tools/call` for `execute_payment` above the gateway's CEL ceiling
(`authorization` on the route, see `docs/architecture.md`, "Two ways to say no")
is answered by the gateway itself: `403`, `Content-Type: text/plain`, body
`authorization failed`. Read from Jaeger on the live cluster, by trace id: **one
span**, service `agentgateway`, operation `POST /*` (not `tools/call`: the MCP
context is not attached when an HTTP policy rejects), no parent. Its
distinguishing attributes: `error.type=Authorization`, `reason=Authorization`,
`otel.status_code=ERROR`, `otel.status_description=authorization failed`,
`http.response.status_code=403`, `url.path=/mcp`, and `jwt.sub` (the verified
caller). **There is no `policy.decision` span in that trace**, because the
Policy Decision Service is never asked;
`tests/e2e/test_gateway_ceiling.py::test_the_403_leaves_no_policy_decision_span`
asserts it. No attribute names the rule, the limit or the amount.

The rule opens with `request.method != "POST"`. Measured live after the rule
was first shipped: without it every MCP session's `GET` and `DELETE` on `/mcp`
was a 403 in the gateway's log. With it they pass (`GET` 200, `DELETE` 202);
`tests/e2e/test_gateway_ceiling.py::test_a_sessions_get_and_delete_are_not_stopped_by_the_ceiling`
asserts it.

### What this project does *not* claim about those spans

- **`internal.span.format` and `span.kind` are Jaeger's, not
  agentgateway's**, and this was measured rather than assumed: in the
  reference trace they appear on 20/20 agentgateway spans *and* on 52/52
  settlement-agent, 6/6 payments-mcp and 2/2 payments-api spans — every span
  from every service. The first is Jaeger's record of the receive format; the
  second is its representation of the OTel `SpanKind` this document's Kind
  column already states. Neither is listed above.
- **The gateway sets no `otel.scope.version`** — measured on v1.5.0: 0 of 20
  spans, against settlement-agent 49/52, payments-mcp 2/6, payments-api 2/2. The
  version is on the Resource (measured on v1.5.0: `service.version=1.5.0`), not
  the scope. Not re-measured on v1.6.0, which is the pinned image since M8.

### Findings, in the same shape as Finding 1 (`a2a-sdk`)

1. **A third HTTP attribute vocabulary — in the pinned v1.5.0, and only
   there. Superseded at v1.6.0 (2026-10-13): the SERVER span now speaks stable
   semconv, see the re-measured tables above; kept below as history.** `http.method`, `http.path`,
   `http.status`, `http.host`, `http.version` — not stable semconv
   (`http.request.method`, `url.path`, `http.response.status_code`,
   `server.address`), and not OTel legacy either (`http.status_code`,
   `http.url`). There is no URL attribute at all. Two keys *are* correct stable
   semconv, `url.scheme` and `network.protocol.version`, and that is exactly
   what v1.5.0's source predicts: those two are the only keys it routes through
   its own `semconv` module
   (`crates/agentgateway/src/telemetry/trc.rs` — the `PROTOCOL_VERSION` and
   `URL_SCHEME` `Key` constants); everything else is a raw string key. Not
   worked around: nothing in this repo rewrites the gateway's attributes.
   **This is version-scoped on purpose.** Upstream PR
   [`#3182`](https://github.com/agentgateway/agentgateway/pull/3182) renames
   all six for traces and OTLP logs and is already merged — but on 2026-09-01,
   after v1.5.0 shipped on 2026-08-27, so none of it is in the pinned image.
   `tests/e2e/test_gateway_trace.py`'s exact set equality is what will go red
   on the next image bump, which is the intended way for this project to find
   out rather than discovering it from a stale document.
2. **`span.id`, `trace.id` and `duration` are emitted as span attributes**,
   duplicating information the span already carries structurally, and `duration`
   as a human-formatted string (`"3ms"`) rather than a number. Measured on 10 of
   20 spans — the SERVER spans only. Harmless, but it is per-span payload spent
   restating the envelope.
3. **`mcp.session.id` changes format mid-session.** On the `initialize` span it
   is a plain UUID (`0ef21c0f-8a03-4ec8-92f3-78073ae2b74c`); on every later span
   in the same session it is a base64-encoded JSON object naming the target and
   an upstream session id
   (`eyJ0IjoibWNwIiwicyI6W3sidCI6InBheW1lbnRzLW1jcCIsInMiOiI5MjRhYWUxOWFlY2E0NTQyOTc0NTBjMWM0NzdlY2NjYSJ9XX0`,
   which decodes to `{"t":"mcp","s":[{"t":"payments-mcp","s":"924aae19…"}]}`).
   So the same attribute key does not join the `initialize` span to the rest of
   its own session. Recorded, not worked around.
4. **`host.name` on the Resource is `default.default.svc.cluster.local`** — not
   the pod's hostname and not a name that resolves to anything in this cluster.
   It is the gateway's own default identity string, unset by our config.
   Cosmetic here (Jaeger keys on `service.name`), but it would misattribute the
   gateway's spans in any view that groups by host.
5. **A benign gain, and the only unexpected one: payments-mcp's `initialize`
   spans now join the trace, and did not before.** Pre-reroute traces contain
   no `payments-mcp:initialize` span at all;
   `agents/settlement-agent/mcp_tools.py` injects `params._meta` only on the
   tool call, so FastMCP had no context to extract on `initialize`.
   Post-reroute there are two, parented under
   `agentgateway:initialize payments-mcp` — because agentgateway's
   `stamp_trace_context` (`crates/agentgateway/src/mcp/upstream/mod.rs`, added
   by its PR #2520 for SEP-414) copies `traceparent` into the outbound `_meta`
   of **every** MCP message it forwards, not just tool calls. The interposed
   proxy incidentally closes a correlation gap this project had. Note the
   asymmetry that makes it work: agentgateway *writes* SEP-414 `_meta` on
   egress but only *reads* HTTP headers on ingress (see below).
6. **`jwt.sub` is not a semconv attribute name** (M2.1 Task 4). The
   authenticated principal has a stable spelling — `user.id` — and the
   gateway does not use it; it names the attribute after the JWT claim it read.
   This is the same class of divergence as finding 1, one attribute wide, and
   is recorded rather than worked around for the same reason: nothing in this
   repo rewrites the gateway's attributes, and inventing a `user.id` alias
   would make this document describe telemetry that does not exist. It also
   means a query written against `user.id` finds every service here except the
   one that actually authenticates anything.

### Why the trace survives this hop, stated so the two mechanisms are not conflated

This is the load-bearing mechanism of M2.1 and it is genuinely two independent
carriers, not one:

- This repo's own MCP trace-context convention is **SEP-414**: `traceparent` /
  `tracestate` / `baggage` as unprefixed keys inside JSON-RPC `params._meta`,
  never HTTP headers (see "Conventions adopted"). `mcp_tools.py` injects that
  by hand.
- **agentgateway does not read that.** v1.5.0's `TraceParent::from_request`
  (`crates/agentgateway/src/telemetry/trc.rs`) reads the HTTP `traceparent`
  header and nothing else — it never inspects the request body. Read in
  isolation that predicts a fractured trace, and the M2 spike's first pass
  concluded exactly that, wrongly.
- It survives because `bootstrap.py` instruments the httpx client the MCP
  streamable-HTTP transport is built on, so every outbound MCP request
  **also** carries an auto-injected `traceparent` HTTP header. Measured
  against an echo server:
  `00-df60a027741941a16675b35edbcf0a32-5675a787e51b87e6-03`, with no manual
  injection anywhere in the call path. (**2026-09-11:** this said
  "`HTTPXClientInstrumentor().instrument()`" specifically. Since `mcp` 2.2.0 the
  transport is built on `httpx2`, so the instrumentor that does this is
  `HTTPX2ClientInstrumentor` — also already applied in the same file. The
  mechanism and the conclusion are unchanged; the name was not.)

So nothing here should be read as agentgateway supporting SEP-414 on ingress.
It does not, and a future MCP client carrying context in `_meta` *alone* — a
stdio transport with no HTTP layer, or a client library that does not
auto-instrument httpx — would fragment at this hop. settlement-agent is not
that client, which is the entire reason this works.

One measured sampling consequence of the same header-only design, from
v1.5.0's `TraceSampler::trace_sampled`
(`crates/agentgateway/src/telemetry/log.rs`): a request arriving **with** an
incoming `traceparent` is sampled by default, and a request arriving
**without** one is dropped by default. `randomSampling: true` is set in our
config so a headerless caller is visible too rather than silently untraced;
it changes nothing for settlement-agent's own path.

## agentgateway's guardrail telemetry, as measured (M2.2)

**Every name in this section was read off a live span in Jaeger and dumped in
full before being written down, exactly as the M2.1 section above was.**
agentgateway is not this project's component, so the no-invented-telemetry rule
applies to it without exception, and that rule is why this section records
`reason = "MCP"` rather than the more informative string a designer would have
picked. The reference trace is `b05167ba11744cff81bcedce615cb379`, a real
denial of `CLM-1842` at €6,200 on the pinned
`cr.agentgateway.dev/agentgateway:v1.5.0`, re-read span-by-span for this
section in Task 8 rather than copied from any earlier task's report. **It is a
direct-A2A run, not a browser one** — stated because it matters for one thing
only: it carries no `customer-ui` root and its one "orphan" is the
script-minted parent of `settlement-agent`'s `POST /`, exactly the artifact
this document already describes for script-driven runs. Nothing about the
gateway's own attributes depends on how the settlement was started, and the
browser-rooted denial `6eed5ffa79e3eb758b4367eb75cb5a0d` carries the identical
key sets — checked, not assumed.

M2.2 adds one policy hop to the gateway's configuration —
`mcpGuardrails.processors[0]`, `kind: remote`, `methods: {"tools/call":
request}`, `host: policy-decision-service.northstar-platform.svc.cluster.local:9100`,
`failureMode: failClosed` — and that hop is visible in the trace as one extra
span per tool call plus four extra attributes on the SERVER span of a call
that gets refused. Nothing else about the gateway's telemetry changed.

### Attributes on the `Guardrail` span, the gateway's own view of the policy hop

A CLIENT span named `Guardrail`, one per `tools/call`, a **child of the
gateway's `tools/call` SERVER span**. Nine keys — the complete set, nothing
omitted, with Jaeger's own two excluded as described under "What this project
does *not* claim about those spans" above:

```
agentgateway.outbound.kind      "Policy"
agentgateway.outbound.subtype   "Guardrail"
grpc.status                     0
http.host                       "policy-decision-service.northstar-platform.svc.cluster.local"
http.method                     "POST"
http.path                       "/agentgateway.dev.ext_mcp.ExtMcp/CheckRequest"
mcp.method.name                 "tools/call"
mcp.target                      "payments-mcp"
otel.scope.name                 "agentgateway"
```

Four things follow from that list, and each is a measurement rather than a
reading of the source:

- **`http.path` is literally the ExtMCP gRPC method.** An operator who has
  never seen this config can tell from one span that the gateway asked an
  external service for a decision, and which RPC it used.
- **`grpc.status = 0` reports the transport, not the decision.** The gRPC call
  succeeded; the deny is in the payload. That distinction is diagnostically
  load-bearing under `failureMode: failClosed`: a refusal caused by an
  *unreachable* PDS produces a different `grpc.status` here, so "the policy
  said no" and "the policy service was down" are separable in the trace rather
  than both surfacing as a blocked call.
- **The span appears on a permitted call too**, with the identical key set —
  the decision is not in this span at all. Confirmed on the permit trace
  `88cdfbcf5720487c9597ba573c02c33d` (€5,800, executed).
- **Two attributes are absent and are recorded as absent rather than guessed:**
  nothing names the guardrail *processor* or the `metadata` keys it was
  configured with, and nothing carries the JSON-RPC error code or the
  `mcp_error` payload the PDS returned. If you want the decision, you read the
  `policy.decision` span below, or the parent's `otel.status_description`.

### Attributes on a policy-denied `tools/call` SERVER span

The accepted-call block earlier in this document lists 22 attribute names. A
call the policy refuses carries **27 keys** on the same span on v1.6.0 — those
same 22 plus exactly five, and the five are the whole of the difference
(v1.5.0 carried 26: the same four without `error.type`). Re-measured
2026-10-13, trace `a835b2c045184df4b6288d3822f14c81`:

```
client.address               "10.244.0.15"
duration                     "4ms"
error                        true
error.type                   "MCP"
gateway                      "default/default"
gen_ai.tool.name             "execute_payment"
http.request.method          "POST"
http.response.status_code    200
jwt.sub                      "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
listener                     "listener0"
mcp.method.name              "tools/call"
mcp.resource.type            "tool"
mcp.session.id               (base64 JSON, same form as the accepted span)
mcp.target                   "payments-mcp"
network.protocol.version     "1.1"
otel.scope.name              "agentgateway"
otel.status_code             "ERROR"
otel.status_description      "mcp: mcpGuardrails rejected: requested 6200 EUR exceeds the 6000 EUR limit for this agent at autonomy level assisted [policy-denial rule=settlement-agent.assisted.execute_limit limit=6000.00 requested=6200.00]"
protocol                     "mcp"
reason                       "MCP"
route                        "default/route0"
server.address               "agentgateway.northstar-platform.svc.cluster.local"
server.port                  3000
span.id                      "c1121277ac4b582c"
trace.id                     "a835b2c045184df4b6288d3822f14c81"
url.path                     "/mcp"
url.scheme                   "http"
```

The five that only a refusal carries are `error`, `error.type`,
`otel.status_code`, `otel.status_description` and `reason`. Observations, all
measured:

- **`http.response.status_code` is 200, not 4xx.** On v1.6.0 ExtMCP's refusal is
  an MCP **tool result marked `isError`** inside a successful HTTP response
  (upstream PR #3526; on v1.5.0 it was a JSON-RPC error, code `-32001`, with the
  PDS's body under `error.data`, which v1.6.0 no longer sends). It matches
  neither the native CEL `mcpAuthorization` mechanism's 400 nor `jwtAuth`'s 401.
  Anyone alerting on HTTP status alone will not see this refusal at all. The
  rule, the limit and the requested amount now reach the client only as the
  `[policy-denial ...]` tag at the end of the result's text
  (`pds/deny_text.py::format_deny`).
- **`reason = "MCP"`, and that is all the categorisation there is.** On the
  M2.1 jwtAuth refusal documented above the same key reads `"JwtAuth"`. So the
  key does separate an authentication refusal from a policy refusal, but it
  names the *layer*, not the rule — `"MCP"` is as specific as v1.5.0 and
  v1.6.0 get. v1.6.0 repeats it as `error.type`.
- **`otel.status_description` carries the PDS's own reason string verbatim**,
  behind a fixed `mcp: mcpGuardrails rejected: ` prefix the gateway adds,
  followed on v1.6.0 by the `[policy-denial rule=... limit=... requested=...]`
  tag. `AuthorizationError.reason` is what lands there. This is the one place the
  refusal's *reason* is machine-readable from gateway telemetry alone, and it
  is a free-text string, not a code.
- **Unlike the M2.1 jwtAuth refusal, every `mcp.*` attribute is present.** The
  guardrail hook runs after the message has been parsed as JSON-RPC, so
  `mcp.method.name`, `mcp.session.id`, `mcp.target` and — critically —
  `gen_ai.tool.name` are all on the span. An operator can tell *which tool* was
  refused, which the authentication refusal could not tell them. `jwt.sub` is
  present for the same reason: the caller was authenticated, then refused.
- **There is no CLIENT span to payments-mcp**, and no payments-mcp span for
  this tool call anywhere in the trace. That absence is the trace-level
  evidence that the execution never happened rather than happening and failing.

`tests/e2e/test_policy_denial.py` asserts the behavioural half of all of this
and pins the gateway's child-span shape by exact equality on the sorted
`(service, operation, kind)` triples of the SERVER span's children — measured
as three, not one: `Guardrail` (CLIENT, agentgateway) and `policy.decision`
(SERVER, policy-decision-service) are **siblings** under it, because the
gateway propagates the context it received to the hook rather than its own
client span's context.

Deliberately *not* asserted by any test: the exact text of
`otel.status_description`. Pinning it would pin a concatenation of an upstream
prefix with this project's own reason string, and both halves are already
covered — the prefix by the child-shape assertion, the reason by the PDS's own
unit tests.

## The policy decision, as measured (M2.2) — Gap 3, closed

Gap 3 below records that no OpenTelemetry semantic convention exists for a
policy or autonomy decision. That is still true; what changed in M2.2 is that
this project now emits such a span, so the gap moves from "deferred" to
"closed by defining our own names, in the open, and saying so".

**The attribute keys are defined in exactly one place —
`platform/policy-decision-service/pds/telemetry.py` — and this document cites
that file rather than re-typing them**, which is the whole reason they were
put in a module-level `ATTRIBUTES` tuple: the same tuple is what
`platform/policy-decision-service/tests/test_servicer.py`'s
`test_every_attribute_we_emit_is_declared_and_visibly_ours` asserts against,
so the source, the guard and this contract cannot drift into three
different lists.
**Dated note, 2026-09-14 (M5 Task 3): corrected — the previous reference
here, `tests/unit/test_stage2_pds_config.py`, asserts nothing about
`ATTRIBUTES`; verified by reading it. `test_servicer.py`, not a line number
within it, is the reference this document now cites.** The `policy.`
prefix is deliberate and so is its absence of a `northstar.` one: a reader
scanning a span must be able to tell at a glance which attributes came from a
framework (`gen_ai.*`, `mcp.*`, `http.*`) and which this project invented, and
this whole section is the second kind.

**Read off a live span, not off that file.** The block below is the deny
decision from trace `c6666319b97c70487bccf8af1ba73b8e`, dumped from Jaeger and
compared key-for-key against `telemetry.py`'s declared `ATTRIBUTES`. They
agree, and the check was worth making in that direction: the source declares
ten `policy.*` keys, and how many of them a given span actually carries
depends on the decision, because `server.py` writes the optional ones only
when it has a value (`if decision.limit is not None: span.set_attribute(...)`,
and likewise for the caller, tool, amount and currency). Absent reads as
absent; a zero amount would read as a request for nothing.

**Dated note, 2026-09-14 (M5 Task 3):** the source now declares **ten**
`policy.*` keys, not nine — this task's commit added `policy.autonomy.reason`
alongside `policy.autonomy.level`. Measured, not retyped:
`len(telemetry.INVENTED_ATTRIBUTES) == 10` in
`platform/policy-decision-service/pds/telemetry.py`.
*Renamed in M9:* `policy.autonomy.reason` → `northstar.policy.autonomy.reason`,
`policy.autonomy.level` → `northstar.policy.autonomy.level`; the count is still ten.

**Dated note, 2026-09-15 (M5 Task 6): re-derived, not flagged again.** The
"nine keys"/`b05167ba…`/`88cdfbcf…` inventory the note above left in place has
now been replaced below with a fresh pull against the live cluster
(Jaeger's own `GET /api/traces/{traceID}` HTTP query API), closing ruling
M5-R21. Two traces anchor it: `c6666319b97c70487bccf8af1ba73b8e`, an
assisted-mode denial produced the same way the M2.2 reference trace was, and
`08d797e48e8b41e67a022f84dd38a732`, an autonomous-mode permit produced by
this task's own anti-vacuity e2e (`tests/e2e/test_progressive_autonomy.py`)
flipping the live contract — the first trace this project has that shows
`policy.autonomy.level` as anything other than `"assisted"`.

### Attributes on the `northstar.policy.decision` span

Eleven keys on a limit-rule decision — the ten `policy.*` names plus the
scope — which is the complete set for the span below, nothing omitted, with
Jaeger's own `internal.span.format` and `span.kind` excluded by the same
convention as the agentgateway blocks:

```
otel.scope.name              "northstar.policy-decision-service"
policy.autonomy.level        "assisted"
policy.autonomy.reason       "TARGETING_MATCH"
policy.caller.spiffe_id      "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"
policy.decision              "deny"
policy.decision.retryable    false
policy.limit.amount          6000
policy.request.amount        6200
policy.request.currency      "EUR"
policy.rule                  "settlement-agent.assisted.execute_limit"
policy.tool.name             "execute_payment"
```

*Renamed in M9 (this block is as measured, under the old names):* every `policy.X` key below is now
`northstar.policy.X`, except `policy.decision.retryable` → `northstar.policy.retryable`.

**What varies, measured across both reference traces rather than reasoned
about.** `policy.decision` is `"permit"` or `"deny"`; `policy.rule` names the
rule that fired; `policy.autonomy.level` is whatever the flag resolved to for
that call; and `policy.limit.amount` and the two `policy.request.*` keys are
each **absent** whenever the rule or the tool has nothing to state for them.
Concretely, from the two traces:

| Trace | Tool | `policy.rule` | `policy.autonomy.level` | `policy.decision` | `policy.limit.amount` | Keys |
|---|---|---|---|---|---|---|
| `c6666319…` (deny) | `propose_payment` | `unlimited_tool` | `assisted` | `permit` | absent | 10 |
| `c6666319…` (deny) | `refuse_payment` | `unlimited_tool` | `assisted` | `permit` | absent | 8 |
| `c6666319…` (deny) | `execute_payment` | `settlement-agent.assisted.execute_limit` | `assisted` | `deny` | `6000` | 11 |
| `08d797e4…` (permit) | `propose_payment` | `unlimited_tool` | `autonomous` | `permit` | absent | 10 |
| `08d797e4…` (permit) | `execute_payment` | `settlement-agent.autonomous.execute_limit` | `autonomous` | `permit` | `1000000` | 11 |

So **two or three `policy.decision` spans appear per settlement**, one per
tool call the settlement agent makes, and the set is the interesting artefact
rather than any one span alone. In the denial trace there are three: the
agent is permitted to *propose* €6,200, refused permission to *execute* it,
and then permitted to call `refuse_payment` — its terminal, no-money branch
tool that tells the customer why — which is why that row's key count (8) is
the smallest: `refuse_payment` carries neither `policy.request.*` key nor
`policy.limit.amount` because it moves nothing. The permit trace has only
two, because there is no refusal branch to take. `propose_payment` and
`refuse_payment` are both in the PDS's `UNLIMITED_TOOLS` set because neither
moves money; `execute_payment` is the one tool a limit rule ever fires
against, whether the limit that rule states is €6,000 (assisted) or
€1,000,000 (autonomous) — the trace shows that distinction being made rather
than asserted.

**M8 adds one attribute and two rules to this span.** A `tools/call
execute_payment` is now decided with the ledger's own word for the payment, and
the span carries `policy.ledger.status`: the status the PDS read from
payments-api itself, on **both** kinds of execute decision.

- A caller in the PDS's `HUMAN_APPROVERS` set (the reviewer-ui) is decided by
  `policy.rule` = `human-approved.execute`, which permits only
  `awaiting_approval`; the status reads `awaiting_approval` on a permit and
  `executed`, `rejected`, `proposed` or `unreadable` on a deny. The rule states
  no limit, so `policy.limit.amount` is absent on a human release.
- The settlement agent, after its limit passes, may execute only a payment that
  is still `proposed`. Any other status is denied under the rule
  **`settlement-agent.payment_not_proposed`**, with the limit still on the span.
  The status reads `proposed` on a permit. A ledger the PDS could not read
  (status `unreadable`) has its own rule, **`settlement-agent.ledger_unreadable`**.

So the limit-rule block above, which listed eleven keys, now has **twelve** on an
`execute_payment` decision: those eleven plus `policy.ledger.status`. A decision
on `propose_payment` or `refuse_payment` reads no ledger and carries no such
key, so those rows are unchanged. The values after M8's limit change are:
`settlement-agent` at `assisted` EUR 0, at `autonomous` EUR 6,000; the
EUR 1,000,000 and EUR 25,000 figures in the dated measurements above are
history.

Four properties worth stating because they are easy to get wrong from the
outside:

- **`policy.request.amount` is a float**, `6200` rather than `"6200"` or
  `6200.0` as a string. The amount arrives at the PDS as a JSON number inside
  `mcp_request` (measured, M2.2 Task 1: `{"name":"propose_payment",
  "arguments":{"claim_id":"CLM-1842","amount":6200.0,"currency":"EUR"}}`), and
  nothing stringifies it on the way to the span.
- **`policy.decision.retryable` is always `false`**, and is on the span rather
  than only in the response body because it is the field the agent keys its
  terminal branch off. A denial the agent may retry is a denial it can sit on
  stage retrying.
  **Until M9 it was also the one attribute in this system that any backend
  refused to store** (M4 Task 1): its old key, `policy.decision.retryable`, was a
  dotted prefix of `policy.decision`'s, and OpenSearch expands dotted keys into
  nested objects at index time, so a single document could not hold both. It
  reached OpenSearch as `policy.decision_retryable`, rewritten by the
  `attributes/opensearch-compat` processor. M9's rename to
  `northstar.policy.retryable` removed the collision and the rewrite. Of the 114 distinct
  span attribute keys Jaeger held across all ten services when this was
  measured (2026-09-13), it was the only such collision among our own keys.
- **`policy.caller.spiffe_id` is the *caller's* attested identity**, not the
  PDS's own and not the audience — the same string the gateway records as
  `jwt.sub` one span up, handed to the PDS through the guardrail's
  `metadata: {caller: 'jwt.sub'}` CEL expression rather than re-verified. The
  PDS validates no token itself and presents none: it decides on an identity
  the gateway already established.
- **`policy.autonomy.level` is a constant today** (`assisted`), and is on the
  span anyway. It is the field M5 turns into a feature flag; recording it now
  means the traces M5 changes the behaviour of already say what the behaviour
  was keyed on.
  **Dated note, 2026-09-14 (M5 Task 3): no longer true.** This is exactly the
  commit that stopped it being a constant — `policy.autonomy.level` is now
  resolved per decision from the `autonomy-level` flag through
  `pds/autonomy.py::resolve_autonomy`, not read off a compiled-in module
  constant. The sentence above describes the pre-M5 (M2.2) shape; left in
  place as the historical reason the field exists, not as the field's
  current behaviour.
  **Dated note, 2026-09-15 (M5 Task 6): observed, not just resolvable.**
  Trace `08d797e48e8b41e67a022f84dd38a732` (above) is this project's first
  live span with `policy.autonomy.level = "autonomous"` — the live
  `northstar-autonomy` `FeatureFlag` object was targeted to `autonomous` for
  the duration of one settlement (`tests/harness/contract.py`'s
  `set_targeted_branch`), the trace was captured, and the flag was reverted
  to its shipped, refusing (`assisted`) state before this task finished.

**No `policy.*` attribute is set anywhere except in the PDS.** In particular
nothing in `settlement-agent`, `payments-mcp` or `payments-api` writes one, and
nothing rewrites agentgateway's spans to add one — the enrichment boundary
described under "Domain attributes we own" applies here unchanged.

## SPIRE instrumentation, as measured (M2.1 Task 4)

**SPIRE 1.15.3 emits no spans.** Not "we did not wire tracing up" — there is no
tracing to wire up. Its telemetry configuration accepts Prometheus, DogStatsD,
Statsd, M3 and an in-memory sink, and nothing else; there is no OTLP exporter,
no trace exporter of any kind, and no `traceparent` handling anywhere in the
Workload API path. So the JWT-SVID fetch that now precedes every MCP call
appears in this system's traces only as latency inside settlement-agent's own
`execute_tool` span, not as a hop of its own. That is stated here rather than
quietly omitted, because a reader of this document's other sections would
reasonably expect a span per hop, and no attribute below was invented to fill
the gap.

What SPIRE does emit is Prometheus, and the chart ships it **off** by default.
`spire-server.telemetry.prometheus.enabled` and the matching `spire-agent` key
are both set to `true` in `deploy/platform/spire/values.yaml`, two ClusterIP
Services in `deploy/platform/spire/metrics.yaml` expose the two ports (the
chart publishes none), and the collector scrapes them through a
`prometheus/spire` receiver in both collector configs. Without that last piece
the metrics are exposed and thrown away — the same seam M2.1 Task 3 found in
agentgateway, which had tracing available and unconfigured for a whole task.

**How the metrics arrive, read off the collector's own debug exporter.** The
`job_name` becomes `service.name`, which is what puts SPIRE in the same service
vocabulary as everything else here. Four consecutive lines of
`kubectl -n observability logs deployment/otel-collector`, at the committed
`exporters.debug.verbosity: normal`, with the tab separators the collector
writes rendered as spaces and nothing else altered:

```
2026-09-09T14:25:02.633Z  info  Metrics  {"resource": {"service.instance.id": "b9237a32-4649-4154-b0c2-bdd49f2b040f", "service.name": "otelcol-contrib", "service.version": "0.158.0"}, "otelcol.component.id": "debug", "otelcol.component.kind": "exporter", "otelcol.signal": "metrics", "resource metrics": 1, "metrics": 91, "data points": 95}
2026-09-09T14:25:02.633Z  info  ResourceMetrics #0 service.name=spire-agent server.address=spire-agent-metrics.northstar-platform.svc.cluster.local service.instance.id=spire-agent-metrics.northstar-platform.svc.cluster.local:9988 server.port=9988 url.scheme=http
ScopeMetrics #0 github.com/open-telemetry/opentelemetry-collector-contrib/receiver/prometheusreceiver@0.158.0
spire_agent_runtime_total_gc_pause_ns{host=northstar-control-plane} 7.2153768e+07
```

An earlier revision of this block showed only the two middle lines with the
timestamps stripped, which made it not reproducible from the command that
produced it. Two details of the full form are the collector's rather than
SPIRE's and are worth naming so it is not misread: the `"resource"` object on
the first line describes the **collector** (`service.name: otelcol-contrib`) —
it is the logger's own context, not the scraped target's, whose resource is the
`ResourceMetrics #0` line below it — and `ScopeMetrics #0` names the Prometheus
receiver, because SPIRE carries no OTel SDK to name a scope of its own. The
same four-line shape appears with `service.name=spire-server`.

**The series that make this task's mechanism visible**, quoted verbatim from
`/metrics` on the running pods rather than paraphrased. From spire-agent, the
workload attestation that turns a socket connection into an identity, and the
JWT-SVID path specifically:

```
spire_agent_workload_api_workload_attestor{attestor="k8s",host="northstar-control-plane",status="OK"} 388
spire_agent_workload_api_workload_attestation{host="northstar-control-plane",status="OK"} 388
spire_agent_workload_api_discovered_selectors_sum{host="northstar-control-plane"} 7296
spire_agent_rpc_workload_api_fetch_jwtsvid{host="northstar-control-plane",status="OK"} 2
spire_agent_jwt_svid_cache_size{host="northstar-control-plane"} 1
```

`attestor="k8s"` is the label that says the identity came from the kubelet-based
workload attestor — the pod's namespace and service account — rather than from
anything the workload asserted about itself. From spire-server, the mint:

```
spire_server_rpc_svid_v1_svid_new_jwtsvid{host="spire-server-0",status="OK"} 1
spire_server_server_ca_sign_jwt_svid{host="spire-server-0"} 1
spire_server_entry_cache_reload{host="spire-server-0",status="OK"} 663
```

Two counts in there are worth reading carefully rather than as round numbers,
because they say something about the design. `fetch_jwtsvid` at the agent is
**2** while `new_jwtsvid` at the server is **1**: the agent caches, so a repeat
fetch for the same audience is answered locally and never reaches the server —
which is also why `svid.py` keeps its own in-process cache with a refresh
margin rather than treating a fetch as free. And `workload_attestation` in the
hundreds against a JWT-SVID count in single digits is the chart's own OIDC
discovery provider holding a long-lived X509-SVID stream open, not this
project's traffic; attestation happens per Workload API connection, not per
token.

No absolute value above should be read as a fixture. These are counters on a
long-running cluster: they were rising while this was written, and a re-run
produces different numbers. What is stable, and what this section actually
claims, is the *series* — the metric names and their label keys, which is the
part a dashboard or an alert would be built on.

## Metrics, as measured (M4 Task 2) — and the five that nothing emits

**Until M4 Task 2 this system emitted no metric at all.** Measured during the
M4 design (spec §6) and true of every commit before this one: no `MeterProvider`
in any Python or Go service, no `OTEL_METRICS_*` in any manifest, and a
collector `metrics` pipeline exporting to `debug` alone — so SPIRE's scrape
(the section above) was received and discarded, and nothing else was produced.

M4 Task 2 installs a `MeterProvider` in all five Python services then in the
repo — `platform/eval-scorer` arrived in Task 4 and has none, by the argument in
limitation 2 below — and routes the
collector's `metrics` pipeline to Prometheus over OTLP. That is the wiring. The
*finding* is what turned up once the wiring existed, and it is a gap report
rather than a dashboard full of green — which spec §6 predicted in advance, in
writing, so this is not a disappointment being dressed up as a result.

### Where the conventions live now

The GenAI semantic conventions are **no longer in the main
`open-telemetry/semantic-conventions` repository**. They live in
[`open-telemetry/semantic-conventions-genai`](https://github.com/open-telemetry/semantic-conventions-genai),
which also carries the **MCP** conventions this document cites elsewhere. This
is not an inference from where the files happen to be: the old location says so
itself. `docs/gen-ai/README.md` and `docs/gen-ai/gen-ai-metrics.md` in the main
repo are now both stubs titled "Moved: Generative AI semantic conventions",
carrying one sentence and no definitions (fetched 2026-09-13):

> GenAI semantic conventions have moved to the
> [OpenTelemetry GenAI semantic conventions repository](https://github.com/open-telemetry/semantic-conventions-genai).
> This page has moved and is no longer maintained in this repository.

Worth knowing because the old URLs still return **200**, not 404 — a reader
following a stale link lands on a page that looks authoritative and is
explicitly unmaintained. Every convention claim in this section was read from
the new repository at `main` (`0c87594975`, 2026-09-10), with
`docs/gen-ai/gen-ai-metrics.md` last changed at `8d3e4a0f3c34`, 2026-08-10.

`open-telemetry/semantic-conventions-genai` defines **twelve** GenAI metrics.
**Every one is Development stability**, and that qualifier is load-bearing
rather than decorative: a Development metric can be renamed upstream, and a
sentence written without it becomes false at the next bump with nothing here to
catch it. Spec §6 asks about six of the twelve; the other six
(`gen_ai.client.operation.time_to_first_chunk`,
`gen_ai.client.operation.time_per_output_chunk`, `gen_ai.server.request.duration`,
`gen_ai.server.time_per_output_token`, `gen_ai.server.time_to_first_token`,
`gen_ai.invoke_workflow.duration`) are named here so a reader knows the six
below are a chosen subset, not the whole convention.

### The §6 inventory, per metric, with the layer each verdict was verified at

| Metric (OTel name) | Type | Unit | Stability | Emitted? | Verified at |
|---|---|---|---|---|---|
| `gen_ai.invoke_agent.duration` | Histogram | `s` | Development | **No** | SDK — created by nothing |
| `gen_ai.invoke_agent.inference_calls` | Histogram | `{inference_call}` | Development | **No** | SDK — created by nothing |
| `gen_ai.invoke_agent.tool_calls` | Histogram | `{tool_call}` | Development | **No** | SDK — created by nothing |
| `gen_ai.execute_tool.duration` | Histogram | `s` | Development | **No** | SDK — created by nothing |
| `gen_ai.client.token.usage` | Histogram | `{token}` | Development | **Yes** | Prometheus — series present |
| `gen_ai.client.operation.duration` | Histogram | `s` | Development | **No** | SDK — created by nothing |

**One of six.** "Created by nothing" is the precise claim and it is the
strongest of the available ones: the metric is not merely absent from
Prometheus, it is never brought into existence in any process in this system,
so no reader, exporter, collector pipeline or Prometheus receiver is a candidate
explanation. Measured 2026-09-13 by reading the four installed frameworks'
source — not one of those five names appears anywhere in `pydantic-ai-slim`
2.42.0, `a2a-sdk` 0.3.7, `fastmcp` 4.0.3 or `mcp` 2.2.0, and the last three
contain no `get_meter(` call at all. The full sweep, and the argument for why
this is a framework gap rather than a scope boundary, is
[`docs/upstream-findings.md`](upstream-findings.md) **Finding 12**.

### What PydanticAI 2.42.0 actually creates — all three of them

Read out of the deployed image rather than a local venv, by constructing this
project's own `InstrumentationSettings` inside the running pod and enumerating
the instruments it built:

```
$ kubectl -n northstar-agents exec deploy/senior-settlement-agent -- python -c '...'
pydantic_ai version: 2.42.0
provider: opentelemetry.sdk.metrics._internal.MeterProvider
INSTRUMENTS PydanticAI CREATED:
    ('cost_histogram', 'operation.cost', '{USD}', '_Histogram')
    ('time_to_first_chunk_histogram', 'gen_ai.client.operation.time_to_first_chunk', 's', '_Histogram')
    ('tokens_histogram', 'gen_ai.client.token.usage', '{token}', '_Histogram')
```

| Instrument | Unit | In the conventions? | Reaches Prometheus? |
|---|---|---|---|
| `gen_ai.client.token.usage` | `{token}` | Yes — Development | **Yes** |
| `gen_ai.client.operation.time_to_first_chunk` | `s` | Yes — Development | **No**, and not because anything is broken |
| `operation.cost` | `{USD}` | **No** — PydanticAI's own name, in no OTel namespace | **Yes** |

Two of those rows need their reason stated rather than left to be guessed at.

**`gen_ai.client.operation.time_to_first_chunk` is created and never recorded
into.** `record_metrics` guards it with `if time_to_first_chunk is not None`
(`pydantic_ai/models/instrumented.py`), and that value is only produced for a
*streaming* request. This demo streams nothing, so the histogram takes no
measurement, and the OTel SDK exports no data point for an instrument with
none. **Created is not emitted**, and this row is the reason that distinction is
drawn explicitly everywhere in this section: a survey that equated the two
would have published this metric as supported, and a dashboard built on it
would have been permanently empty.

**`operation.cost` is a real series and a non-convention name.** It carries the
model call's monetary cost (the same figure the `chat` span's `operation.cost`
attribute carries, recorded above under "PydanticAI instrumentation"). It is
published here because it is measured and present, and flagged because a reader
building on it should know it is PydanticAI's invention, not a convention, and
carries none of a convention's stability promise — PydanticAI's own source says
as much about its sibling `gen_ai.aggregated_usage.*` namespace: *"a custom
namespace, not part of the OpenTelemetry Semantic Conventions. It may be updated
if OTel introduces an official convention."*

### `gen_ai.client.token.usage`, as it actually arrives

The one row of §6's table that is not a gap. Read from Prometheus's
`/api/v1/series`, one series verbatim, whitespace only reformatted:

```
{"__name__": "gen_ai_client_token_usage_sum",
 "gen_ai_operation_name": "chat",
 "gen_ai_provider_name": "anthropic",
 "gen_ai_request_model": "claude-sonnet-5",
 "gen_ai_response_model": "claude-sonnet-5",
 "gen_ai_system": "anthropic",
 "gen_ai_token_type": "input",
 "instance": "98dec6ce-a261-4647-8614-2532c564cee6",
 "job": "settlement-agent"}
```

34 series in total across the histogram's `_bucket`/`_sum`/`_count` families and
the two `gen_ai.token.type` values (`input`, `output`). `gen_ai_system` is the
**deprecated** spelling of `gen_ai_provider_name` and both appear, exactly as
they do on the `chat` span — `version=5` emits both, which this document already
records for spans and which is now also true of this metric's attributes.

**Names: OTel's and Prometheus's are not the same, and both are published here
deliberately.** Prometheus v3.14.0's default OTLP translation strategy is
`UnderscoreEscapingWithSuffixes` (`config/config.go:280`), which this cluster
leaves at its default. So the dotted OTel name becomes underscores plus a
histogram suffix, and dotted *attributes* become underscored *labels*. The OTel
name is what the SDK emits and what a convention claim is about; the Prometheus
name is what a query has to use. A document publishing only one of them would
mislead whichever reader needed the other.

| OTel name | Prometheus series |
|---|---|
| `gen_ai.client.token.usage` | `gen_ai_client_token_usage_bucket`, `_sum`, `_count` |
| `operation.cost` | `operation_cost_bucket`, `_sum`, `_count` |

### Which services emit anything at all, and why that question is the whole method

**A "no such metric" result is worthless unless the path it would have taken is
known to work.** M4 Task 1 shipped a milestone-blocking defect of exactly this
shape — "no errors" concluded from a 200-line tail of a 17,173-line log — so
every absence above is paired with a presence measured in the same window.
Measured 2026-09-13, per `job` label (Prometheus's rendering of OTLP's
`service.name`):

| `job` | Distinct metric names | Control — what proves its path works |
|---|---|---|
| `spire-server`, `spire-agent` | 271 `spire_*` plus 40 Go-runtime | Collector-scraped, pushed over OTLP; **no Python service involved**, so it proves the collector→Prometheus hop on its own |
| `settlement-agent` | 20 | `gen_ai_client_token_usage_*` and `operation_cost_*` from real model calls |
| `claims-agent` | 14 | `http_server_*`/`http_client_*` from the ASGI and httpx instrumentations |
| `payments-mcp` | 4 | `http_client_request_duration_seconds_*` on every call to payments-api |
| `reviewer-ui` | 4 | `http_client_duration_milliseconds_*` on every ledger read |
| `policy-decision-service` | 1 | **No metric of its own exists to find**, so a probe was sent instead — see below |

`payments-mcp`'s row is the one that decides `gen_ai.execute_tool.duration`:
tool execution happens in that process, under FastMCP, so that is where the
metric would be recorded. Its MeterProvider demonstrably gets other metrics all
the way to Prometheus in the same window, which is what makes the absence
FastMCP's rather than ours.

**The two services with nothing to find got a sent probe rather than a found
one**, because an empty result from a service that records nothing is
indistinguishable from an empty result from a broken exporter. A counter named
`northstar.meterprovider.probe` was recorded inside each pod, through that
service's own telemetry module, and arrived:

```
{"__name__": "northstar_meterprovider_probe_total", "job": "policy-decision-service",
 "northstar_probe_reason": "prove-pds-export-path", "instance": "0b212e42-..."}
{"__name__": "northstar_meterprovider_probe_total", "job": "settlement-agent",
 "northstar_probe_reason": "prove-senior-agent-export-path", "instance": "856688d9-..."}
```

Those two series are in the cluster's Prometheus and are named for what they
are. They are a measurement artefact, not telemetry this system emits, and
nothing queries them.

**This evidence is point-in-time, and deliberately not turned into a standing
test** (M4 Task 2 review finding F8). The probes were sent by hand on
2026-09-13 and age out of Prometheus's 15-day retention
(`--storage.tsdb.retention.time=15d`, read off the running container), so after
2026-09-28 the two series above will not be in the store and nothing will go
red about it. That is the honest state of this row rather than an oversight:
`tests/e2e/test_prometheus_metrics_ingest.py::test_each_service_demonstrably_gets_a_metric_all_the_way_to_prometheus`
parametrises the **four** services that record an instrument, and it cannot
parametrise these two, because the thing being proved is precisely that they
record nothing. The standing alternative would be a test that `kubectl exec`s
a counter into two pods on every gate run — a suite that otherwise only reads
the cluster would start writing to it, to re-prove a property the source
already states and `tests/unit/test_metrics_wiring.py` already guards
(`_configure_metrics` exists and is called in all five services). What a reader
should take from this row is what it says: on 2026-09-13, both export paths
carried a metric end to end. If that needs re-establishing later, re-send the
probe — do not read an expired series as an absence.

**`senior-settlement-agent` has no `job` of its own, by design.**
`deploy/k8s/senior-settlement-agent.yaml` sets `OTEL_SERVICE_NAME=settlement-agent`
deliberately — the demo's point is that the code is byte-identical and the
platform tells the two identities apart by *identity*, on the `policy.decision`
span, not by service name. So its probe above lands under
`job="settlement-agent"` with its own `instance`, which is the expected result
and not a misconfiguration.

### Two gaps in our own configuration, found by measuring metrics

Neither is an upstream defect; both are this project's own, recorded here
rather than quietly fixed or quietly ignored.

**1. `reviewer-ui` emits the LEGACY HTTP metric name.** It is the only service
whose Deployment sets no `OTEL_SEMCONV_STABILITY_OPT_IN`, measured
2026-09-13 across all four manifests:

```
reviewer-ui       | OTEL_SEMCONV_STABILITY_OPT_IN = None
settlement-agent  | OTEL_SEMCONV_STABILITY_OPT_IN = http
claims-agent      | OTEL_SEMCONV_STABILITY_OPT_IN = http
payments-mcp      | OTEL_SEMCONV_STABILITY_OPT_IN = http
```

The consequence was invisible while this system emitted no metrics and is
visible now: `reviewer-ui` reports `http_client_duration_milliseconds_*`
(legacy, milliseconds) where every other service reports
`http_client_request_duration_seconds_*` (stable, seconds). A panel summing
"HTTP client latency" across services would silently mix units. **Not fixed in
M4 Task 2**, deliberately: setting that variable also changes reviewer-ui's
*span* attributes, which this document pins elsewhere and which several e2e
assertions read, so it is a telemetry-contract change that belongs in its own
task rather than as a side effect of adding a MeterProvider.

**2. One service still has no `MeterProvider`: `systems/payments-api` (Go).**
M4 Task 2 covered the five Python services that existed then. payments-api
emits spans and no metrics, so the money path has no metric of its own. None of
§6's six metrics is a concern for it — every one is GenAI, and no agent runs
in it — so this does not affect the inventory above, but the spec's "no
`MeterProvider` in any Python **or Go** service" is only half-closed and should
not be read as fully closed.

(From M4 Task 4 to 2026-10-24 this entry also named `platform/eval-scorer`,
which installed a `TracerProvider` and no `MeterProvider`. That service has
been removed.)

### How this is kept true

`tests/e2e/test_prometheus_metrics_ingest.py` re-verifies the whole inventory
against the live cluster, and is written so that a false negative cannot pass
quietly: every absence assertion is paired with a positive control **in the
same test** (it was "in the same run" until review finding F3 showed three
tests passing against a completely empty store), the controls are bounded to a
recent window (an unbounded check would stay green for Prometheus's full 15-day
retention after everything stopped emitting), a test fails if any `gen_ai` name
turns up that the file does not already account for, and "below stage 4" now
means the API server answered `NotFound` rather than any non-zero `kubectl`
exit, so an unreachable cluster cannot skip the whole file quietly (finding
F7). If a framework implements one of the five, that file goes red and this
table is what has to change.

`tests/unit/test_metrics_wiring.py` covers what the cluster cannot show: that
all five services install the provider, that the five copies of the wiring have
not drifted, that nothing configures a view or temporality that would discard
data, that the chart pin and the collector's endpoint agree with each other,
that no `deploy/k8s/` manifest changes the temporality, the export interval or
the exporter from outside the Python that was measured, and that nothing under
`agents/`, `apps/`, `platform/`, `tools/` or `systems/` *authors* an instrument
under a namespace an instrumentation library owns — the last two added in the
fix round, and the second of them is the only layer that can see who wrote a
metric at all.

## settlement-agent's own instrumentation: `execute_approved` (M3 Task 3)

M3 Task 3 adds one span this repo owns outright -- not agentgateway's, not
a2a-sdk's, not the Policy Decision Service's -- created by
`agents/settlement-agent/settlement.py`'s `execute_approved` itself, around
the human-approved execution. Named `settlement.execute_approved`, one per
approved execution, on both settlement Deployments (the same code, run under
two identities).

### Attributes on the `settlement.execute_approved` span

Two keys, both `northstar.`-prefixed because this is domain data no
OpenTelemetry semantic convention covers, set only by this repo's own code
and nowhere else:

```
northstar.payment.id             "PAY-000001"
northstar.approval.reviewer_id   "alex.mercer"
```

`northstar.payment.id` is the ledger's own payment id, read off the approval
payload -- not derived from a model call, because `execute_approved` makes
none. `northstar.approval.reviewer_id` is the reviewer's identity exactly as
the approval payload states it: a claim carried on the span, not a verified
credential -- the M3 Task 3 review fix round's ledger check
(`_verify_refusal`) constrains what this span's *execution* can cause; it
does not turn this string into an authenticated identity, and this document
does not claim otherwise.

**This span also carries this project's first span link.** Every other span
named in this document relates to its neighbours by parent/child alone.
`execute_approved` additionally links to the settlement-agent span that was
active in `decide_settlement` when the refusal was recorded -- read back off
Task 2's ledger fields `refusedTraceId`/`refusedSpanId` and built into a real,
valid remote `SpanContext` in `settlement.py`'s `_refusal_link` (see that
function's own docstring for the measurement: a `SpanContext` built with
`span_id=0` is silently dropped by the SDK's own validity check before an
exporter ever sees it, which is why a real span id has to travel, not just a
trace id). A link, not a parent: a human takes minutes to review a refusal,
the original process is long gone by the time they act on it, and a span held
open across that gap would misstate both the duration and what was executing.

The linked span is **settlement-agent's own A2A server span**, active at the
moment the refusal was recorded -- not the Policy Decision Service's
`policy.decision` span, and not the denied `execute_payment` client span
(M3 Task 3 review, finding 3 / R13; `systems/payments-api/store.go`'s
`RefusedSpanID` doc comment and `agents/settlement-agent/mcp_tools.py`'s
`refuse` doc comment previously overstated this as "the exact span that
recorded the refusal", which reads as the PDS's own decision span). It is
still the right span for a reviewer to land on for this milestone's stage
demo -- clicking the link lands in the refusal's own trace -- and this
document names it for what it actually is rather than for what would sound
more impressive.

Also new on this span (M3 Task 3 review, the question / R16): when the
ledger record `execute_approved` reads carries a `claimId` -- it always does,
propose always writes one -- this sets `northstar.claim.id` as W3C baggage on
the context the span and the downstream `execute_payment` call run under,
and detaches it again before returning. The approval payload itself carries
no claim id (unlike a claim submission, which arrives with it already in
inbound A2A baggage), so this is this flow's own point of intake for that
fact -- without it, a reviewer correlating by claim id would see the refusal
half of the story and not the approval half.

## reviewer-ui's own instrumentation: `review.decision` (M3 Task 4)

M3 Task 4 adds the second span this repo owns outright, created by
`apps/reviewer-ui/review.py`'s `decide()` around a reviewer's submitted
approve/reject decision. Named `review.decision`, SpanKind SERVER, one per
submitted decision — and, unlike `settlement.execute_approved`, this is the
actual **root** of a new trace: reviewer-ui applies no ASGI/Starlette
auto-instrumentation (see the table row above), so there is no framework
entry span for it to nest inside.

### Attributes on the `northstar.review.decision` span

Six keys, all `review.`-prefixed for the same reason `northstar.*` and
`policy.*` are prefixed in this document's other rows — this is domain data
no OpenTelemetry semantic convention covers, set only by this span and
nowhere else:

```
review.payment.id            "PAY-000053"
review.decision               "approve"
review.reviewer.id            "alex.mercer"
review.reviewer.spiffe_id     "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"
review.refused.trace_id       "331edba8cb21223e9de2f244360c42ef"
review.elapsed_ms             312000
```

*Renamed in M9 (this dump is as measured in M3):* `review.payment.id` → `northstar.review.payment.id`,
`review.decision` → `northstar.review.decision`, `review.reviewer.id` → `northstar.review.reviewer.id`,
`review.reviewer.spiffe_id` → `northstar.review.reviewer.spiffe_id`, `review.elapsed_ms` (int, ms) →
`northstar.review.wait.duration` (double, **seconds**); `review.refused.trace_id` was dropped, the span
link to the refusal trace carries it. Five keys since M9.

Every value here is read off the ledger record `review.decide()` fetches
fresh at decision time (`ledger.get(payment_id)`), except `review.decision`
(the reviewer's own choice) and `review.reviewer.id` (the name typed into the
form — a claim carried on the span, not a verified credential, the same
qualification `settlement.execute_approved`'s `northstar.approval.reviewer_id`
carries above). `review.reviewer.spiffe_id` is reviewer-ui's own workload
identity, read from the SPIFFE Workload API (`apps/reviewer-ui/identity.py`)
— not a JWT-SVID for any audience (this service calls no audience-gated
service and holds no `EXECUTE_LIMITS` row), but the same X.509 SVID every
workload in this mesh already carries, read back rather than assembled from
the namespace/ServiceAccount strings this process merely believes it was
deployed with.

**`review.elapsed_ms` is computed from the ledger's own `refusedAt`, not from
a timer started when the listing page loaded** — the property the spec calls
out by name (§6, §7 criterion 5). `apps/reviewer-ui/review.py`'s `_elapsed_ms`
takes no argument from the HTTP request at all; its only inputs are the
ledger's `refusedAt` string and a `now`, so there is no code path by which a
browser tab's dwell time could reach this attribute.

This span carries **no span link of its own** — the link to the refusal trace
lives one hop downstream, on settlement-agent's `settlement.execute_approved`
span (documented above), which is where `refusedTraceId`/`refusedSpanId`
travel to next inside the approval payload. `review.decision` is a *cause* of
that call, connected to it by ordinary parent/child propagation (the outbound
A2A POST carries `traceparent` while `review.decision` is current), not by a
link of its own.

**Dated note, M8 Task 3: both statements above are superseded.** The span now
carries **one span link of its own**, to the refusal span in trace A (ids read
from the ledger's `refusal.refusedTraceId` and `refusedSpanId`), and a seventh
key, `northstar.claim.id`, read from the ledger's `claimId`. The link is added
after the span starts, because the ids come from the ledger read, which must run
inside the span. Nothing is linked when the record holds no ids. See "Trace B:
the human release (M8 Task 3)" below.

### `review.queue`, and the two reasons the ledger reads were untraceable (M3 final-review fix round, R36)

**The second owned span this service creates**, added in the M3 final-review
fix round: `review.queue`, SpanKind SERVER, one per `GET /` — the reviewer's
queue of refusals awaiting a decision. It carries **no attributes**. Every
figure on that page is a ledger record, and payments-api's own `GET /payments`
span (now in the same trace, which is the point) is what describes the read;
a seventh `review.*` key publishing a count would widen the inventory above
for a question nobody asked. The name follows this document's own convention
for owned spans — `<domain>.<the thing this span is>`, as in `claim.submit`,
`policy.decision` and `review.decision` — and deliberately not `GET /`, which
is a framework's name for a route, in a service that applies no framework
instrumentation.

**What it is for.** `main.py`'s `index()` used to call `list_refused()` with
no span current, and `review.py`'s `decide()` used to call `ledger.get()`
*before* `tracer.start_as_current_span`, on an explicit rule that a review
which never happened is not telemetry this service owns to invent. The cost
of that rule was not looked for until the whole-branch review looked:
`HTTPXClientInstrumentor`'s CLIENT span for each read became **the root of a
trace of its own**, and the `traceparent` it injected named that root.

**Measured before the fix**, over the 30 most recent traces of each shape:

```
reviewer-ui   CLIENT "GET"            30 traces, every one a 1-span root
payments-api  SERVER "GET /payments"  30 traces, every one a 1-span root
shared trace ids                      0
```

and one live approval trace of 67 spans (`5c17694d8d4d202159a9a57a3eec55b7`)
containing no reviewer-ui ledger read at all. `decide()` now opens
`review.decision` first and reads the ledger inside it, which means a stale
link or a race with another reviewer **does** now produce a span — one ending
ERROR with the exception on it, carrying the four attributes known before the
read and not the two read off the record. That is a change in what this
service claims, stated here rather than absorbed silently.

**And that alone did not fix it, which is the part the finding did not
anticipate.** With the parent spans in place, payments-api *still* minted a
fresh root for every ledger read. Two pins, one minor release apart across
the W3C Trace Context Level 1/Level 2 boundary:

* `opentelemetry-sdk==1.44.0` sets `TraceFlags.SAMPLED | RANDOM_TRACE_ID`
  (`0x01 | 0x02` — Level 2's "this trace id was generated randomly" bit,
  `opentelemetry/trace/span.py`), so the header reads `00-…-03`. Measured on
  a local repro of `ledger.py`'s own client against a capturing HTTP server.
* `systems/payments-api` pins `go.opentelemetry.io/otel v1.41.0`, whose
  extractor refuses any version-00 traceparent with a flag byte above
  `0x02` — `propagation/trace_context.go`: `if version == 0 && (h != "" ||
  opts[0] > 2) { return trace.SpanContext{} }`. An empty SpanContext is "no
  parent", so `otelhttp` starts a new root.

Measured directly through the repo-managed port-forward, two requests
differing only in the flag byte: `…-01` produced a `GET /payments` span
parented to the injected span id; `…-03` produced **no span under that trace
id at all**.

**Upstream already fixed this — and 2026-09-11 (M3) this repo took the fix.**
otel-go **v1.42.0** widened that line to `opts[0] > 3`; `systems/payments-api`
is now on **v1.46.0** (2026-08-25), the newest stable release, with
`otelhttp v0.71.0` (2026-08-26) and `semconv/v1.43.0`. This section used to
close the paragraph above with:

> Every release from v1.42.0 onward declares `go 1.25.0`, and this repo builds
> payments-api on `golang:1.24-alpine` and runs its tests under
> `GOTOOLCHAIN=local` against a host go1.24.10 — so adopting it is a Go
> *toolchain* bump, not a module bump. That is carried debt, recorded here
> rather than taken in a fix round.

That debt is paid. `go.mod` declares `go 1.25.0` with `toolchain go1.27.1`,
the Dockerfile builds on `golang:1.27.1-alpine`, and `scripts/smoke-test.sh`
runs the Go suite under `GOTOOLCHAIN=auto` — which is what turns the toolchain
back into a property of this repository rather than of the operator's PATH.
The 31-test Go suite is unchanged in count and still passes.

**The workaround is gone.** This section used to describe
`apps/reviewer-ui/bootstrap.py`'s `_Level1CompatibleTraceContextPropagator`,
which masked the injected flag byte to `SAMPLED`, and closed **"Delete both
the moment payments-api reaches otel-go ≥ v1.42.0."** That condition is met
and both are deleted: the class, and the three tests in
`apps/reviewer-ui/tests/test_bootstrap.py` that existed only to pin it (plus
two more that pinned inherited behaviour the override touched). `bootstrap.py`
now registers the stock `TraceContextTextMapPropagator`, and its module
docstring holds the dated record of what the mask was. Two replacement tests
pin the *wanted* state instead: that the registered trace-context propagator
is upstream's own class rather than a subclass of it, and that the injected
flag byte is `03`.

**Re-measured on the live cluster the day the mask came out**, the same
two-request experiment as above through the repo-managed port-forward,
differing only in the flag byte:

```
00-df5aa13c6d534c0f9c36ef5c187d014d-31966bf1692149b0-01
  -> payments-api GET /payments SERVER da5830097b2aa97e, parent 31966bf1692149b0
00-9b855ee5e0a442619dc8d48118324644-51665f1dbec74c0b-03
  -> payments-api GET /payments SERVER ca981a384c75fc22, parent 51665f1dbec74c0b
```

The `-03` row produced **no span at all** before the bump. And inside the
deployed reviewer-ui pod, `get_global_textmap()` is
`['TraceContextTextMapPropagator', 'W3CBaggagePropagator']` and injects
`00-4fd1495b2de7214c10469a745ba986aa-cdb290f086f73d06-03` — so the `-03`
being accepted above is the byte this service actually sends, not a synthetic
one.

**Measured end to end after the mask was removed**, one live listing request
(`GET http://localhost:8081/`) — trace `eacff63cc15a0868cee60332b1728735`,
three spans:

```
reviewer-ui   review.queue     SERVER  e40bafb90b4a5483  root
reviewer-ui   GET              CLIENT  9280bc1b88bb4120  parent=e40bafb90b4a5483, http.url=…/payments?status=refused
payments-api  GET /payments    SERVER  a418e6258a9c1bac  parent=9280bc1b88bb4120
```

This replaces the pre-bump capture, which is kept for the record: trace
`c05d71f98aecafddaaf0fa4e3901213c`, the same three spans in the same shape,
but reached through the mask.

reviewer-ui was the only service where this could bite: it is the one
`opentelemetry-sdk` 1.44 service that speaks **directly** to the Go system of
record. `payments-mcp`, the other service that calls payments-api, was pinned
at `opentelemetry-sdk==1.29.0` and emitted `…-01`; and agentgateway re-stamps
the context with its own before the hop to `payments-mcp` anyway.

**2026-09-11 (M3): `payments-mcp` is on `opentelemetry-sdk==1.44.0` too**, and
the question Finding 11 left open — whether that bump would fragment the
money-path trace — is now answered by measurement rather than left as a loaded
gun. Measured inside the deployed payments-mcp pod, the 1.44.0 inheritance
mechanism doing exactly what Finding 11 predicted it would:

```
child of a `…-03` remote parent injects 00-4bf92f3577b34da6a3ce929d0e0e4736-ff9fecefb59e0be7-03
child of a `…-01` remote parent injects 00-4bf92f3577b34da6a3ce929d0e0e4736-d8b6b402ca358a12-01
```

So payments-mcp now forwards whatever flag byte it receives, `0x02` included.
Driven end to end with a `-03` parent injected at the MCP boundary
(`params._meta.traceparent`), trace `967b8b72abbd4b6383409a747c0b43f0`:

```
a5a0e0df10bc2223 parent=0e2bb76df975444f (injected)  payments-mcp  tools/call execute_payment  SERVER
1d3cb4342ba6e48e parent=a5a0e0df10bc2223             payments-mcp  POST                        CLIENT
1346117449d1a702 parent=1d3cb4342ba6e48e             payments-api  POST /payments/{paymentId}/execute SERVER
```

Three spans, one trace, full ancestry — on the flag byte that used to be
dropped. The `-01` control run is trace `09809e3142874fb09ce8111c5366c9f6`,
the same shape.

`tests/e2e/test_supervised.py`'s criterion 5 asserts the decision-path
equivalent on every gate run, identified by **parentage** rather than by span
name: the approved-execution trace contains a second payments-api
`GET /payments/{paymentId}` span, from the `get_payment` MCP call
`execute_approved` makes one hop downstream, and only one of the two is a
child of reviewer-ui's own CLIENT span.

## Backstage's own instrumentation: `northstar.autonomy_contract.change` (M6 Task 3/4)

**The ninth service, and the first UI in this document that emits telemetry
about its own write rather than about a request it served.** M6 Task 3 adds
one span this repo owns outright, emitted by
`platform/backstage/packages/backend/src/plugins/autonomyContract/
telemetry.ts`'s `emitContractChangeSpan`, called once from `router.ts`'s
`POST /variant` handler, after the FeatureFlag patch that flips the contract
already succeeded. Named `northstar.autonomy_contract.change`
(`SPAN_NAME` in `telemetry.ts`), one per successful write to the
`northstar-autonomy` FeatureFlag through the autonomy-card UI -- not per read,
and not for a write attempted with an invalid `variant` (rejected `400` before
the k8s client is ever called, so no span either).

This is the third of three independent signals design §4 asks for ("the write
must be auditable and distinguishable from a `kubectl` edit"), and the module
docstring is explicit about why it is the one worth trusting as history: the
object's own `metadata.managedFields` field-manager entry is overwritten by
the very next write (from either this client or `kubectl`), and Backstage's
`coreServices.auditor` event lives only in this one pod's logs, backed by a
`:memory:` database with no shipping. The span survives both.

**Hand-rolled OTLP/HTTP JSON, not the Node OTel SDK.** This plugin emits at
most a few spans per process lifetime -- one per contract write, not a
sustained request path -- so there is no batching or sampling case to justify
a tracer-provider lifecycle; `buildOtlpTracePayload`/`emitContractChangeSpan`
construct and `POST` a `resourceSpans` payload to
`${OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces` directly. `service.name` on the
resource is `OTEL_SERVICE_NAME`, unset in this deployment, so it falls back to
the literal `'backstage'`. Not counted among "which services emit anything at
all" below: that section's method (a MeterProvider probe, per-`job` metric
names in Prometheus) is scoped to the five Python services hosting the four
GenAI frameworks in the path (§6's question), and Backstage is neither Python
nor a candidate for any of the twelve GenAI metrics that section is about —
it is a ninth *span*-emitting service, a different inventory this document
does not otherwise total in one place.

**"100% telemetry, never sampled" still applies to a hand-rolled exporter with
no retry queue.** `emitContractChangeSpan` never throws -- a telemetry outage
must not block a policy-contract write that already landed in the cluster --
but it always returns whether the `POST` was attempted and whether the
collector answered `2xx`, and `router.ts` logs a failure loudly
(`autonomy-contract-write: OTLP span was NOT delivered ...`) rather than
swallowing it. There is no local queue and no retry: a collector outage at the
moment of the write drops that one span for good, reported instead of hidden.
The k8s write and the auditor event are unaffected either way.

### Attributes on the `northstar.autonomy_contract.change` span

Six keys, none of them a `feature_flag.*` OpenFeature/OTel semantic-convention
name -- this span is not an evaluation, it is an operator's write to the
*definition* the evaluation will read next, a different claim with no
convention of its own to cite:

```
northstar.contract.previous_variant   "assisted"
northstar.contract.new_variant        "autonomous"
northstar.contract.field_manager      "backstage-autonomy-card"
northstar.contract.actor              "backstage-autonomy-card"
k8s.namespace.name                    "northstar-platform"
k8s.resource.name                     "northstar-autonomy"
```

*Renamed in M9 (as measured in M6):* `k8s.resource.name` → `northstar.contract.flag.name`.

`northstar.contract.previous_variant`/`new_variant` are read straight off the
live object immediately before and the requested value the route validated
(`isValidVariant`), not assumed from a prior response. `k8s.namespace.name`
is a semantic-convention attribute, reused (`AUTONOMY_NAMESPACE` in
`k8sClient.ts`). The flag's name was `k8s.resource.name` until M9; that name
carries a Kubernetes meaning this value (an OpenFeature `FeatureFlag` object's
name) does not have, so the registry names it `northstar.contract.flag.name`
(`AUTONOMY_NAME`).
`northstar.contract.field_manager` is `FIELD_MANAGER` (`k8sClient.ts`) --
the same string the patch itself sets as its Server-Side Apply field manager,
so a reader can match this span's claim against the live object's own
`metadata.managedFields` while both still agree. `northstar.contract.actor`
is a fixed literal, not a caller identity: **this project's own limitation to
state plainly (design §10) is that Backstage's guest auth means every write
through this UI is indistinguishable from every other** -- there is no signed
user identity upstream of this route for the span to carry honestly, so it
does not manufacture one.



## reviewer-ui's `northstar.approval.wait.duration` (M8 Task 3)

**One histogram, recorded at each human decision that took effect.** Created in
`apps/reviewer-ui/approval_metrics.py`, recorded by `review.decide()` after a
successful release or reject. A failed execute records nothing: no decision took
effect. The value is the span's own `northstar.review.wait.duration`, in seconds (the span's value is the same number, unconverted since M9; it was `review.elapsed_ms` divided by 1000 until then), so the
metric and the span cannot disagree: both read the ledger's `awaitingSince`.

| Field | Value |
|---|---|
| OpenTelemetry instrument | `northstar.approval.wait.duration` |
| Kind and unit | histogram, `s` |
| Attributes | `northstar.review.decision` (`approve` or `reject`; `approved`/`rejected` before M9), `northstar.policy.autonomy.level` (`assisted`, `autonomous` or `unknown`, read off the refusal's rule; `assisted` at rest; was `autonomy.level`) |
| Deliberately absent | the reviewer's id: one series per person would be high-cardinality |
| Aggregation | the SDK default (explicit bucket histogram); `bootstrap.py` installs the provider with no views, and nothing is sampled |

**MEASURED, read off the running Prometheus on 2026-10-14 (never predicted):**
after one approved claim, `label/__name__/values` held exactly three names with
`approval` in them:

```
northstar_approval_wait_duration_seconds_bucket
northstar_approval_wait_duration_seconds_count
northstar_approval_wait_duration_seconds_sum
```

The series carried `job="reviewer-ui"`, `decision="approved"` and
**`autonomy_level="assisted"`** (the pre-M9 labels; since M9 they are
`northstar_review_decision="approve"` and `northstar_policy_autonomy_level="assisted"`:
Prometheus rewrites every dot in an attribute key to an underscore). The default buckets arrived as `le` = 0, 5, 10, 25, 50, 75, 100,
250, 500, 750, 1000, 2500, 5000, 7500, 10000 and +Inf. No collector change was
needed: the `metrics` pipeline already exported to Prometheus at stage 4 and up
(`otlp_http/prometheus`), and both collector configs are unchanged.
`tests/e2e/test_human_in_the_loop.py::test_the_histogram_has_a_sample_for_the_approval`
asserts the `_count` series with `decision="approved"`.

## Trace B: the human release (M8 Task 3)

A refused claim waits in the ledger. The human's decision is a separate trace,
**trace B**, rooted at reviewer-ui's `review.decision` and joined to trace A by a
span link. Measured on claim `CLM-DEMO-E131FE`, payment `PAY-000001`, trace B
`6a27c66630644534a6111b3c5339b608`, trace A `ecb8a9ac6f2b4c0a9f3f48ad30d887e5`:

```
review.decision [reviewer-ui, SERVER]            link -> trace A, span fcbfc06e2358b36c
  GET [reviewer-ui, CLIENT]                      the ledger read
    GET /payments/{paymentId} [payments-api]
  POST [reviewer-ui, CLIENT]  x3                 initialize, notifications/initialized, tools/call
    tools/call [agentgateway]
      policy.decision [policy-decision-service]  rule human-approved.execute
        GET /payments/{paymentId} [payments-api] the PDS's own ledger read
      Guardrail [agentgateway]
      tools/call payments-mcp_execute_payment [agentgateway]
        tools/call execute_payment [payments-mcp]
          POST [payments-mcp] -> POST /payments/{paymentId}/execute [payments-api]
  POST [reviewer-ui, CLIENT] -> POST / [claims-agent]   tasks/get, then message/send (the resume)
```

(Trimmed to the spans this milestone depends on; the a2a-sdk's own spans under the
claims-agent `POST /` are listed in "A2A task states on claims-agent spans" below.)

### Attributes on trace B's root span, northstar.review.decision (M8 Task 3)

Seven keys (plus Jaeger's `span.kind`), all of them:

```
northstar.claim.id           "CLM-DEMO-E131FE"
review.decision              "approve"
review.elapsed_ms            22998
review.payment.id            "PAY-000001"
review.refused.trace_id      "ecb8a9ac6f2b4c0a9f3f48ad30d887e5"
review.reviewer.id           "alex.mercer"
review.reviewer.spiffe_id    "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"
```

*Renamed in M9 (this dump is as measured in M8):* `review.X` → `northstar.review.X`;
`review.elapsed_ms` (`22998`, milliseconds) → `northstar.review.wait.duration` (`22.998`, **seconds**);
`review.refused.trace_id` was dropped (the span link carries it). Six keys since M9.

The claim id is also **baggage** for the gateway call and the resume, so the
downstream spans that run `ClaimIdSpanProcessor` carry it.

### Attributes on trace B's permitting decision span, northstar.policy.decision (M8 Task 3)

Eleven keys, the scope included, all of them:

```
otel.scope.name              "northstar.policy-decision-service"
policy.autonomy.level        "assisted"
policy.autonomy.reason       "TARGETING_MATCH"
policy.caller.spiffe_id      "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"
policy.decision              "permit"
policy.decision.retryable    false
policy.ledger.status         "awaiting_approval"
policy.request.amount        5800
policy.request.currency      "EUR"
policy.rule                  "human-approved.execute"
policy.tool.name             "execute_payment"
```

*Renamed in M9 (this block is as measured, under the old names):* every `policy.X` key below is now
`northstar.policy.X`, except `policy.decision.retryable` → `northstar.policy.retryable`.

(`policy.limit.amount` is absent: the human rule states no limit.) Trace A holds
the other half: the agent's `policy.decision` for `execute_payment` is a **deny**
with `policy.limit.amount` 0 and no `POST /payments/{paymentId}/execute` span.

**The link, as Jaeger shows it.** Jaeger renders a span link as a `FOLLOWS_FROM`
reference carrying the target's `traceID` and `spanID`; a parent is `CHILD_OF`.
The harness's `Span.links` reads the former and `Span.parent_id` the latter.

## The customer's wait: `claim.wait` and `tasks/resubscribe` (M8 Task 3; since M10 the wait ends on a push, not a resubscribe)

*M10: the browser no longer sends `tasks/resubscribe`. `claim.wait` still opens at `input-required` and ends when the outcome arrives, now over `GET /claims/events` after the A2A push; the claims-agent `POST /` resubscribe span and the polling spans described below no longer exist. The record below is as measured in M8.*

Only the browser emits `claim.wait`, so it is asserted in
`tests/e2e/test_policy_denial.py`'s browser run. The customer-ui opens
`claim.wait` when the stream returns `input-required` and ends it when the
decision arrives over `tasks/resubscribe`. It is its **own trace**, linked to the
`claim.submit` trace (one link), and carries `northstar.claim.id`. Measured on a
run with a 6 s "human": `claim.wait` 6.287 s, root of its own trace, one link whose
trace id is the `claim.submit` trace; `claim.submit` itself 7.73 s
(measured under these names; M9 renamed the spans `northstar.claim.wait` and `northstar.claim.submit`). The
`tasks/resubscribe` request appears under it as a claims-agent `POST /` server span
(6.285 s, ending with an `http.disconnect`) whose children are the a2a-sdk's
`on_resubscribe_to_task` handler spans, an `EventQueue.dequeue_event` span about
every 0.5 s while the wait lasts, and a last `dequeue_event` that ends ERROR with
`QueueShutDown` when the queue closes (the SDK's own shutdown, kept, not dropped).
The test asserts the wait is at least as long as the test measured between the page
showing "Awaiting human approval" and the approval, less one second.

## A2A task states on claims-agent spans (M8 Task 3)

claims-agent writes two attributes, owned by this project, on the SDK's
`DefaultRequestHandler._run_event_stream` SERVER span (the one that lasts the whole
wait; see the M8 Task 2 measurement at the top of this document):

| Attribute | Value | Meaning |
|---|---|---|
| `northstar.a2a.task.id` | the task's id (a UUID) | the A2A task the customer streams |
| `northstar.a2a.task.state` | `input-required` on a waiting claim | the state the task was in when the executor last set it |

**Since the free-text intake, a third attribute says which wait.**
`northstar.a2a.parked_reason` is `awaiting_approval` (a payment waits for a
reviewer) or `needs_information` (the claim waits for the customer to answer
a question). claims-agent sets it on the span that parks the task and on the
`northstar.a2a.resume` span that resumes it; the resume span links back to the
parking span with `northstar.a2a.link.reason = resume` in both cases. The
customer's answer arrives in the browser's own root span,
`northstar.claim.answer`, linked to the round that asked. Defined in
`telemetry/registry/claim.yaml`; not yet measured live.

Measured on trace `0178866fbd4dac2d34722175166b1b3e` (a 14.0 s `_run_event_stream`):
`northstar.a2a.task.id` `2cd8d414-7290-4c6d-a91a-31c656f5126e`,
`northstar.a2a.task.state` `input-required`. Nothing else in that trace carries
either key. The a2a-sdk's own spans add no `rpc.*` or `gen_ai.*` attributes: they
carry `otel.scope.name` `a2a-python-sdk` and, on success, `otel.status_code` `OK`.

## Conventions adopted

**Where each one lives, corrected M4 Task 2.** This list used to name "GenAI
semconv" and "MCP semconv" without saying which repository defines them, which
left a reader to assume the main `open-telemetry/semantic-conventions` — where
neither has been maintained for some time. Both now live in
[`open-telemetry/semantic-conventions-genai`](https://github.com/open-telemetry/semantic-conventions-genai),
and the old paths in the main repo are stubs that still return 200 while saying
"This page has moved and is no longer maintained in this repository". The
evidence, and the revision every convention claim in this document was read at,
is in "Metrics, as measured (M4 Task 2)" above.

- **GenAI semconv** (`open-telemetry/semantic-conventions-genai`) for model
  calls, exercised as of M1.2 — see "PydanticAI instrumentation, as measured"
  above for the full attribute inventory, and "Metrics, as measured (M4 Task 2)"
  for which of its twelve metrics anything actually emits (one).
- **MCP semconv** (`mcp.method.name`, `mcp.session.id`, `mcp.protocol.version`),
  with tool names in `gen_ai.tool.name`. `mcp.tool.name` does not exist. Also
  `open-telemetry/semantic-conventions-genai`, at `docs/gen-ai/mcp.md` — that
  repository's own README lists MCP among what it carries, and the main repo
  has no `docs/mcp/` at all (checked 2026-09-13: 404).
- **HTTP semconv** for the Go system of record and A2A's transport spans. This
  one *is* the main `open-telemetry/semantic-conventions` repository — the move
  is GenAI's and MCP's, not a general one. Note the one service that does not
  opt in to the stable HTTP spelling, and what it costs, under "Two gaps in our
  own configuration" above.
- **MCP trace context via `params._meta`** (SEP-414), keys unprefixed. Not HTTP headers.

## Domain attributes we own

The authoritative inventory of what this project owns is the registry in `telemetry/registry/`, rendered as tables in "Registry-defined attributes, spans and metrics (generated)" above. The table below is the historical, per-milestone account: where it was measured and where each name does and does not propagate.

| Attribute | Set by | Propagation | Applies to |
|---|---|---|---|
| `northstar.claim.id` | claims-agent at intake | W3C baggage | See "Real, measured coverage" below — **not** every span everywhere |
| `northstar.claim.policy_number` | customer-ui root span | none | The root span only |
| `northstar.policy.*` (ten keys, M2.2 nine + M5 Task 3's `northstar.policy.autonomy.reason`) plus one borrowed key, `feature_flag.version` (M5-R6, an existing OTel semantic convention, not `policy.*`-prefixed because it is not this project's own to name) | policy-decision-service, on its own `northstar.policy.decision` span | none — set locally, never propagated | That one span only, `feature_flag.version` only when flagd's response actually carries a version (measured against the live sidecar in M5 Task 3: it never does for this deployment's `autonomy-level` flag, so it is routinely absent). Keys declared in `platform/policy-decision-service/pds/telemetry.py`'s `ATTRIBUTES`/`INVENTED_ATTRIBUTES`. **Dated note, 2026-09-14 (M5 Task 3): corrected — that section is above, not below.** **Dated note, 2026-09-15 (M5 Task 6): re-derived.** The worked example and live-trace inventory in "The policy decision, as measured (M2.2)" above now cites two fresh M5 traces (`c6666319…` assisted-deny, `08d797e4…` autonomous-permit) instead of the pre-M5 nine-key shape; the eleven-key (ten `policy.*` plus scope) count there is current, not historical, and `feature_flag.version`'s routine absence was re-confirmed against both traces |
| `northstar.payment.refusal_cause` (M2.2 Task 4) | payments-api, on its own server span for a refused execute; two closed values, `amount_mismatch` and `already_executed` (`systems/payments-api/handlers.go`) | none | payments-api's execute span, and **only when it refuses** — absent on success, so its presence means "refused here". Read off a real recorded span by that service's own Go tests via an in-memory `tracetest.SpanRecorder`; it appears on **none** of the six M2.2 traces named in this document, because neither the denied nor the permitted scenario reaches a 409 |
| `northstar.payment.id`, `northstar.approval.reviewer_id` (M3 Task 3) | settlement-agent, on its own `settlement.execute_approved` span (`agents/settlement-agent/settlement.py`) | none | That span only, on the human-approved execution path. See "settlement-agent's own instrumentation" above, including this milestone's first span **link** and the same span's `northstar.claim.id` baggage (M3 Task 3 review, R16) |
| `northstar.review.*` (five keys since M9, six before it: M3 Task 4; M8 Task 3 adds a span link and `northstar.claim.id` to the same span) | reviewer-ui, on its own `northstar.review.decision` span (`apps/reviewer-ui/review.py`) | none | That span only, one per submitted approve/reject decision — reviewer-ui's second owned span, `northstar.review.queue` (M3 final-review fix round), deliberately carries **none** of them, and no attributes at all. `northstar.review.wait.duration` is now set *after* the span opens rather than at creation, because the ledger read it comes from moved inside the span; a decision that never reaches a refused record therefore emits the other four and an ERROR status. See "reviewer-ui's own instrumentation" above |
| `northstar.a2a.task.id`, `northstar.a2a.task.state` (M8 Task 2) | claims-agent, on the a2a-sdk's `_run_event_stream` SERVER span (`agents/claims-agent/executor.py`) | none | That span only. See "A2A task states on claims-agent spans (M8 Task 3)" |
| `northstar.approval.wait.duration` histogram, attributes `decision` and `autonomy.level` as measured in M8 Task 3; since M9 `northstar.review.decision` and `northstar.policy.autonomy.level` (Prometheus labels `northstar_review_decision`, `northstar_policy_autonomy_level`; the measured record below keeps the names it was measured under) | reviewer-ui (`apps/reviewer-ui/approval_metrics.py`) | none (a metric, not a span attribute) | One sample per human decision that took effect; no reviewer id. See "reviewer-ui's `northstar.approval.wait.duration`" |
| `northstar.contract.previous_variant`, `northstar.contract.new_variant`, `northstar.contract.field_manager`, `northstar.contract.actor` (M6 Task 3/4) | Backstage's autonomyContract plugin, on its own `northstar.autonomy_contract.change` span (`platform/backstage/packages/backend/src/plugins/autonomyContract/telemetry.ts`) | none | That span only, one per successful write to the `northstar-autonomy` FeatureFlag through the autonomy card. `northstar.contract.actor` is a fixed literal (`"backstage-autonomy-card"`), not a caller identity — Backstage's guest auth (design §10) means there is no signed user upstream of this route to name honestly. See "Backstage's own instrumentation" above |

**Two M2.2 notes on this table, both measured rather than reasoned.** (M9
note: the first reasoning below was reversed. The family is `northstar.policy.*`
now, because the registry requires every owned name to be `northstar.*` and
live-check judges the retired un-prefixed namespaces as violations; the prose is
the M2.2 record, which keeps the names it was measured under.) The
`policy.*` family is prefixed `policy.` and not `northstar.` on purpose: it is
a decision, not a domain fact about a Northstar claim, and the prefix is what
tells a reader which of the two it is. And **`northstar.policy.decision` spans carry no
`northstar.claim.id`** — confirmed on all six M2.2 traces named in this
document. That is correct rather than a gap: the PDS is not one of the three
Python services running `ClaimIdSpanProcessor`, and agentgateway forwards only
`traceparent` (plus `:authority`/`:path`) to the guardrail hook, never
`baggage` — measured in M2.2 Task 1, run C, which removed the header filter
entirely and got ten headers with no `baggage` among them. So the decision is
correlated to the claim by trace id, exactly as agentgateway's own spans are.

**The mechanism, as of 2026-10-24: upstream `BaggageSpanProcessor`.** The
three Python services (claims-agent, settlement-agent, payments-mcp) no longer
carry their own `ClaimIdSpanProcessor`; each `correlation.py` builds
`BaggageSpanProcessor` from `opentelemetry-processor-baggage==0.65b0` with a
predicate that admits `northstar.claim.id` and no other key
(`correlation.is_claim_id`), and `bootstrap.configure()` adds it to the tracer
provider. Behaviour is the same: on span start it reads the parent context's
baggage, so a span gets the id only if baggage already carries it in that
process. The predicate is deliberate: `ALLOW_ALL_BAGGAGE_KEYS` would copy any
entry an upstream caller put in baggage onto every span and export it. Pinned by
each service's `tests/test_claim_id_processor.py` (the processor is upstream's,
its predicate admits only the claim id, a second baggage key does not reach the
span, and `configure()` adds it). The coverage figures and the prose below were
measured with the custom class and keep its name; the copying rule they describe
has not changed.

| Service | What copies `northstar.claim.id` onto its spans |
|---|---|
| claims-agent | `BaggageSpanProcessor(is_claim_id)` (`correlation.py`); also sets the baggage at intake (`set_claim_id`) |
| settlement-agent | `BaggageSpanProcessor(is_claim_id)` (`correlation.py`) |
| payments-mcp | `BaggageSpanProcessor(is_claim_id)` (`correlation.py`); finds nothing in practice, see below |
| reviewer-ui | its own spans set the attribute where it acts on a claim (`review.py`); no baggage processor |
| PDS, payments-api, agentgateway, Linkerd proxies | none: correlated to the claim by trace id |

**Real, measured coverage, one live capture (trace
`8a59dfee2b192408f4282a07a0938f43`). No whole-trace span total is published
here, deliberately — the same convention as "The exit criterion" above, and
for the same measured reason:** `a2a-sdk`'s
`a2a.server.events.event_queue.EventQueue.dequeue_event` is a polling span,
measured varying **10–16** across identically-shaped runs, so a trace total
is not a number this document commits to. The coverage *shape* is what is
stable, and these are that capture's per-service ratios:
customer-ui 1/1, claims-agent 2/23, settlement-agent 32/32,
payments-mcp 0/4, payments-api 0/2. Read the pattern — which services reach
100% and which reach zero, which is a property of the mechanism — rather than
the denominators, which belong to one capture and move with the polling span.
(This paragraph published "62 spans" undisclaimed until M2.1 Task 5's fix
round: the sum of those denominators, presented as a fact about the trace
rather than about one capture of it, and contrary to the rule the section
above already followed. Pre-existing, and not exempted by being
pre-existing — it shipped.) This is asymmetric by design, not by
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
xfail (see "Running the tests" above) — not a blanket "every
span in every service" claim.

**Enrichment boundary:** the baggage span processor runs only in our own service
processes. Nothing is injected into telemetry emitted by infrastructure we do not
run in-process. In M2 that means agentgateway's own spans are correlated by trace
ID, not enriched.

**M2.1 Task 3: that last sentence stopped being a prediction and is now a
measurement.** With the gateway in the path, all 20 of its spans in the
reference trace carry no `northstar.claim.id` — `ClaimIdSpanProcessor` runs only
inside this project's own Python processes, and nothing reaches into
agentgateway's telemetry to add anything. This is the desired behaviour, not a
gap to close, and it is now asserted rather than only written down:
`tests/e2e/test_gateway_trace.py::test_the_gateway_carries_no_northstar_claim_id`
fails if a future task ever starts enriching third-party infrastructure
telemetry, so that becomes a deliberate decision instead of a quiet one. (The
test also asserts gateway spans exist first, so it cannot pass vacuously on a
trace where the gateway dropped out of the path — verified by mutation.)

## Registry-defined attributes, spans and metrics (generated)

The attributes, spans and the one metric below are generated from the
OpenTelemetry Weaver registry in `telemetry/registry/`, which is the checked
definition of every name this project owns. Every owned name starts with
`northstar.`; the hand-written sections of this document use the same names
(M9 renamed them everywhere at once, with no compatibility period). Do not edit
between the markers: `scripts/check-registry.sh` regenerates the region and
fails when the committed text differs. This is the inventory: where a
hand-written section and the generated tables disagree, the registry wins.

<!-- BEGIN weaver-generated -->

### `northstar.claim`

The insurance claim a trace is about, and the A2A task that carries it.

| Attribute | Type | Stability | Description | Examples / values |
|---|---|---|---|---|
| `northstar.a2a.link.reason` | enum | development | Why this span links to another trace. A link attribute, not a span attribute. | `resume` |
| `northstar.a2a.parked_reason` | enum | development | Why claims-agent parked the task at input-required, on the span that parked it and on the span that resumed it. | `awaiting_approval`, `needs_information` |
| `northstar.a2a.task.id` | string | development | The A2A task id of a claim being processed by claims-agent. | `f2d33c09-7ffb-406b-8491-7fc94b9c6693` |
| `northstar.a2a.task.state` | enum | development | The A2A task state claims-agent reported on its span. | `failed`, `completed`, `input-required` |
| `northstar.claim.id` | string | development | The claim a span belongs to. Travels as W3C baggage; the span processors copy it onto spans. | `CLM-1842` |
| `northstar.claim.policy_number` | string | development | The insurance policy number the claim is made under (not the authorization policy). | `POL-77120` |
| `northstar.payment.refusal_cause` | enum | development | Why payments-api refused a request with 409. Absent on a request it did not refuse. | `amount_mismatch`, `already_executed`, `rejected` |

### `northstar.contract`

A change to the autonomy contract, made through Backstage.

| Attribute | Type | Stability | Description | Examples / values |
|---|---|---|---|---|
| `northstar.contract.actor` | string | development | Who made the change. | `backstage-autonomy-card` |
| `northstar.contract.field_manager` | string | development | The Kubernetes field manager the change was applied as. | `backstage-autonomy-card` |
| `northstar.contract.flag.name` | string | development | The name of the flag resource the change was made to. The k8s semantic conventions have no generic resource-name attribute. | `northstar-autonomy` |
| `northstar.contract.new_variant` | enum | development | The autonomy variant after the change. | `assisted`, `autonomous` |
| `northstar.contract.previous_variant` | enum | development | The autonomy variant before the change. | `assisted`, `autonomous` |

### `northstar.policy`

A policy decision the platform made about one MCP tools/call.

| Attribute | Type | Stability | Description | Examples / values |
|---|---|---|---|---|
| `northstar.policy.autonomy.level` | enum | development | The autonomy level in effect, resolved from the autonomy-contract flag at decision time. | `assisted`, `autonomous`, `unknown` |
| `northstar.policy.autonomy.reason` | string | development | The OpenFeature reason the autonomy level came back with. | `TARGETING_MATCH`, `STATIC`, `DEFAULT`, `ERROR` |
| `northstar.policy.caller.spiffe_id` | string | development | The SPIFFE ID of the workload the decision was made about. | `spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent` |
| `northstar.policy.decision` | enum | development | What the policy-decision-service decided. | `permit`, `deny` |
| `northstar.policy.ledger.status` | enum | development | What the ledger showed for the payment an execute_payment call asked about. | `proposed`, `awaiting_approval`, `executed`, `rejected`, `unreadable` |
| `northstar.policy.limit.amount` | double | development | The limit in EUR the rule applied. Absent when the rule has no limit. | `6000.0` |
| `northstar.policy.request.amount` | double | development | The payment amount the tool call asked for. | `6800.0` |
| `northstar.policy.request.currency` | string | development | The currency of the requested amount. | `EUR` |
| `northstar.policy.retryable` | boolean | development | Whether the caller may retry the same call and expect a different answer. |  |
| `northstar.policy.rule` | string | development | The rule that decided, e.g. settlement-agent.assisted.execute_limit. | `settlement-agent.assisted.execute_limit`, `human-approved.execute` |
| `northstar.policy.tool.name` | string | development | The MCP tool the call asked for. gen_ai.tool.name moved out of the core registry in semantic-conventions v1.44.0, so it cannot be referenced. | `propose_payment`, `execute_payment` |

### `northstar.review`

A human's decision about one payment that policy refused to let an agent execute.

| Attribute | Type | Stability | Description | Examples / values |
|---|---|---|---|---|
| `northstar.review.decision` | enum | development | What the human reviewer decided. | `approve`, `reject` |
| `northstar.review.payment.id` | string | development | The ledger id of the payment under review. | `PAY-000043` |
| `northstar.review.resume_only` | boolean | development | True when the decision was already on the ledger and the call only re-sent the resume to the claim. |  |
| `northstar.review.reviewer.id` | string | development | The id the reviewer signed the decision with. | `kasper.nissen` |
| `northstar.review.reviewer.spiffe_id` | string | development | The SPIFFE ID of the reviewer-ui workload that took the decision. Absent when the workload cannot prove its identity. | `spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui` |
| `northstar.review.wait.duration` | double | development | Seconds from the payment awaiting approval to the human's decision. Absent on a resume-only call. | `10.931` |



### Spans

| Span | Kind | Brief | Attributes (requirement level) |
|---|---|---|---|
| `northstar.a2a.resume` | internal | The resume of a claim that waited, either for a reviewer's decision or for the customer's answer to a question. Runs in that person's trace and carries one link back to the span that parked the claim, with northstar.a2a.link.reason on the link. | `northstar.a2a.parked_reason` (recommended); `northstar.a2a.task.id` (recommended); `northstar.a2a.task.state` (recommended); `northstar.claim.id` (recommended) |
| `northstar.autonomy_contract.change` | internal | One flip of the autonomy contract. Starts and ends in the same instant. | `k8s.namespace.name` (recommended); `northstar.contract.actor` (required); `northstar.contract.field_manager` (required); `northstar.contract.flag.name` (recommended); `northstar.contract.new_variant` (required); `northstar.contract.previous_variant` (required) |
| `northstar.claim.answer` | internal | The customer answers claims-agent's question about a free-text claim, on the same A2A task. Its own trace, linked to the span of the round that asked. | `northstar.claim.id` (required) |
| `northstar.claim.submit` | internal | The customer submits a claim. Root of the claim's first trace. A claim written as free text gets its id from claims-agent, so the id is set once the first reply names it, and the policy number only if the customer gave one. | `northstar.claim.id` (required); `northstar.claim.policy_number` (recommended) |
| `northstar.claim.wait` | internal | The customer waits for a human decision. Its own trace, linked to the span that sent the claim to review (the submit span, or the answer span of a free-text claim). | `northstar.claim.id` (required) |
| `northstar.policy.decision` | server | One decision, a child of the gateway's tools/call span. | `feature_flag.version` (recommended); `northstar.policy.autonomy.level` (recommended); `northstar.policy.autonomy.reason` (recommended); `northstar.policy.caller.spiffe_id` (recommended); `northstar.policy.decision` (required); `northstar.policy.ledger.status` (conditionally required: when the call is an execute_payment by a caller a rule knows); `northstar.policy.limit.amount` (conditionally required: when the rule has a limit); `northstar.policy.request.amount` (recommended); `northstar.policy.request.currency` (recommended); `northstar.policy.retryable` (recommended); `northstar.policy.rule` (required); `northstar.policy.tool.name` (recommended) |
| `northstar.review.decision` | server | One human decision. Links back to the span where policy refused the payment. | `northstar.claim.id` (recommended); `northstar.review.decision` (required); `northstar.review.payment.id` (required); `northstar.review.resume_only` (conditionally required: when the decision was already on the ledger); `northstar.review.reviewer.id` (required); `northstar.review.reviewer.spiffe_id` (recommended); `northstar.review.wait.duration` (conditionally required: when money moves) |
| `northstar.review.queue` | server | The reviewer's queue of payments awaiting a decision. Carries no attributes of its own. |  |


### Metrics

| Metric | Instrument | Unit | Brief | Attributes (requirement level) |
|---|---|---|---|---|
| `northstar.approval.wait.duration` | histogram | `s` | How long a human took, from the payment awaiting approval to the decision. | `northstar.policy.autonomy.level` (required); `northstar.review.decision` (required) |

<!-- END weaver-generated -->

### What live-check checks, and what it does not (measured, M9)

Weaver live-check (`otel/weaver:v0.28.0`, in-cluster, fed a copy of every signal
by the collector's `otlp_grpc/weaver` exporter; policy in `telemetry/live-check.toml`)
judges the names this project owns against the registry above. Its limits were
measured, not assumed, and they are listed here so that a green gate is read for
what it says.

* **A missing required attribute is not caught.** With a v1 registry,
  live-check v0.28.0 does not report `required_attribute_not_present`. Measured
  on 2026-10-17: a mutation that dropped `northstar.policy.rule` (`required` on
  `northstar.policy.decision`) from the decision span survived. The report
  held four decision spans and no `required_attribute_not_present` finding.
  What does pin the required attributes are the behaviour tests, which read them
  off real spans: `tests/e2e/test_policy_denial.py` (the decision span's
  `northstar.policy.decision`, `northstar.policy.rule`,
  `northstar.policy.limit.amount` and `northstar.policy.request.amount`, criterion
  1), `tests/e2e/test_human_in_the_loop.py` (the review span is found by
  `northstar.review.reviewer.id`, trace B's decision carries
  `northstar.policy.rule` and `northstar.policy.ledger.status`, and the histogram is
  asserted by `northstar_review_decision`), and `tests/e2e/test_demo_flow.py`
  (the evidence step reads rule, limit, autonomy level, caller, reviewer id and
  `northstar.review.wait.duration` back out of Jaeger). The
  `finding_level_overrides` entry for `required_attribute_not_present` in
  `telemetry/live-check.toml` exists so that a Weaver release that starts
  checking it is not silently lowered.
* **Span names are not judged by live-check, so a test checks them.** Live-check
  does not compare a span's name with the registry, so a service that went back
  to an un-prefixed span name raises no finding. The live-check e2e test
  (`tests/e2e/test_registry_live_check.py`) therefore fails on any span name in the
  report that starts with one of the retired prefixes
  (`livecheck.legacy_span_names`), and pins the two names
  `northstar.policy.decision` and `northstar.review.decision` as seen. A
  misspelled `northstar.claim.submit`, `northstar.claim.wait` or
  `northstar.autonomy_contract.change` is still not caught.
* **Memory grows without bound while it runs, and the semconv fetch is not
  cached (measured 2026-10-17).** Live-check keeps every sample it receives.
  On an idle demo cluster (background traffic only: about 490 metric points and
  60 spans a minute sent to it by the collector) the container held 210.4 MB at
  12:58:38, 252.3 MB at 13:08:59 and 271.9 MB at 13:14:46 (cgroup
  `memory.current` on the kind node), about 3.8 MB a minute or 220 MiB an hour,
  from 13 MiB at start. The former 768Mi limit would have been reached in about
  3.5 hours, so the limit is 4Gi (about 17 hours) and the pod is recycled at every
  `scripts/start-demo.sh` and every gate run. Every weaver start also clones the
  semconv v1.44.0 dependency from github.com: the checkout lives in a temporary
  directory that is deleted after use (a container restart inside the same pod,
  with `/tmp` an `emptyDir` that survived, still created a new `repo*` directory),
  so it is fetched again on every start and needs egress.
* **Standard names are not validated, only ours.** Semantic-convention names
  (`gen_ai.*`, `http.*`, `k8s.*`) appear in the report as "does not exist in the registry" at
  `information` level. That is the policy working as intended: it says what the
  registry does not describe and judges none of it.
* **Operating it.** Re-running the live-check test alone needs a `restart()` of
  the pod and fresh traffic first, or it fails with "live-check received
  nothing". The collector-log loss guards go red if they run within about 60
  seconds after a manual `report()`, because reading the report restarts the
  weaver pod and the collector's exporter retries in a burst. The gate's order
  (`scripts/smoke-test.sh`: restart, the rest of `tests/`, then the live-check
  file on its own) avoids both.
* **`gen_ai.tool.name` cannot be referenced by the registry.** Semantic
  conventions v1.44.0 excludes `gen_ai.*` from dependency resolution, so the
  policy span's tool name is the owned `northstar.policy.tool.name` rather than
  a reference to the standard attribute.


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
visibly on stage, for one consequential tool.

**claims-agent reads the same switch** (`agents/claims-agent/bootstrap.py`,
a copy of settlement-agent's, deployed at `"true"` in
`deploy/k8s/claims-agent.yaml`). What it captures is different in kind:
settlement-agent's prompt holds only a claim id, an amount and a currency,
while claims-agent's intake prompt holds **the customer's own words**, every
message they sent on the task, and its output holds the extracted policy
number, amount, date and description. A free-text claim is therefore the one
place where a customer's free text lands in span attributes
(`gen_ai.input.messages`, `gen_ai.output.messages` on the intake `chat` span).
A JSON claim never reaches the model and is captured nowhere. Unset defaults to ON; the
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
`pydantic-ai-slim` 2.42.0 — 2.41.0 when the review cited it; the guard below is
byte-identical at both, re-read at the 2026-09-11 bump rather than assumed):
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
- **Re-checked at `a2a-sdk` 1.1.2, 2026-09-11 (M3 remaining-bumps, step 3) —
  both halves survive the major version, so this gap does not close and A18
  stays.** The check existed to answer one question: does 1.x extract inbound
  trace context, and could `StarletteInstrumentor` therefore be removed? No.
  Re-running this gap's own greps over the installed
  `a2a-sdk[telemetry,http-server]==1.1.2` tree gives `propagate` 0,
  `traceparent` 0, `TextMapPropagator` 0, and every one of 17 decorated modules
  still carries a bare `@trace_class(kind=SpanKind.SERVER|CLIENT)` with no
  `attributes=` and no `attribute_extractor=` call site anywhere outside
  `telemetry.py`. **The bump was not taken**: 1.x is A2A protocol v1.0 (the
  JSON-RPC method is `SendMessage`, not `message/send`), its `AgentCard` is a
  protobuf message that cannot parse this repo's own committed card
  (`ParseError: ... has no field named "protocolVersion"`), and
  `a2a.server.apps.A2AStarletteApplication` — the object A18 attaches
  `StarletteInstrumentor` to — no longer exists. Full evidence and the size of
  that migration are in `docs/upstream-findings.md` Finding 1's re-check note.
  Both agents stay on `a2a-sdk[telemetry]==0.3.7`.

### Gap 2 (closed, M3): human-in-the-loop approval spans

- **Evidence:** No convention found, and re-checked for M3 rather than
  assumed still true: OpenTelemetry's semantic conventions have no span, no
  attribute namespace and no registry entry for an approval, an escalation, or
  a human-interrupt step inside an agent trajectory. The GenAI conventions
  cover the model call, the tool call and (in open proposals) agent-to-agent
  identity; none of them has a place to say *a person decided this, at this
  time, and the work resumed under a different principal*. The nearest
  neighbours are about the principal (`user.id`, the deprecated `enduser.*`),
  not about a decision — the same shape of absence Gap 3 found for policy
  decisions. So `review.decision`, the six `review.*` attributes, and
  `settlement.execute_approved`'s two `northstar.*` attributes are this
  project's own to define, and there is nothing upstream to defer to or to
  file against.
- *(Names in this entry are as defined in M3; see the M9 rename map at the top of this document.)*
- **Status: closed in M3, by defining them in the open — and the deferral it
  replaces was honoured rather than quietly dropped.** This entry read
  *"Deferred to M3, when we have a working implementation to argue from"* for
  the whole of M2. M3 built the implementation, so the argument below is made
  from what the spans actually carry, measured, rather than from what would be
  nice:
  - **`review.decision`** (SpanKind SERVER, on `reviewer-ui`) is the human
    decision itself: which payment (`review.payment.id`), which way
    (`review.decision`), who says so (`review.reviewer.id`), under which
    workload identity (`review.reviewer.spiffe_id`), against which earlier
    trace (`review.refused.trace_id`), and how long the human took
    (`review.elapsed_ms`). The measured inventory is in "reviewer-ui's own
    instrumentation" above.
  - **`settlement.execute_approved`** (on `settlement-agent`) is the resumed
    work, and it carries this project's first span **link** — to the span
    that recorded the refusal, read back off the ledger's own
    `refusedTraceId`/`refusedSpanId`. See "settlement-agent's own
    instrumentation" above, including the measurement that forced a real span
    id to be stored: a `Link` built from a `SpanContext` with `span_id=0` is
    dropped by the SDK before any exporter sees it.
  - **The link, not a parent, is the part a convention would have to get
    right**, and it is the part this project has something to say about: a
    human takes minutes, the original process is gone, and a span held open
    across that gap would misstate both the duration and what was executing.
    Two linked traces are the honest shape. Nothing upstream says so either
    way; this is the recorded position, not a claimed convention.
- **Why this is a closure and not an invented convention.** Same distinction
  Gap 3's closure draws, applied unchanged: inventing telemetry *for a
  third-party component* is forbidden without exception, and instrumenting our
  own code where no framework does it for us is not the same act.
  `reviewer-ui` and `settlement-agent` are both this repository's code, no
  library emits an approval span, and the prefixes (`review.`, `northstar.`)
  are chosen precisely so a reader can tell these apart from the `gen_ai.*`,
  `mcp.*` and `http.*` attributes the frameworks emit. Nothing was renamed,
  aliased or back-filled onto anyone else's spans: agentgateway's and the
  PDS's spans are unchanged by this milestone.
- **What would make this reopen.** Exactly what would reopen Gap 3. If
  OpenTelemetry ever specifies a human-approval or agent-handoff convention,
  these names become divergence rather than invention, and the honest move
  then is to adopt theirs and record the rename here. Nothing in this project
  would notice the day such a convention lands, which is worth knowing — and
  the GenAI conventions are actively growing towards agent governance (see
  Gap 1's four open A2A-identity proposals).
- **What is still NOT covered, so the closure is not overread.** This closes
  the *representation* gap for one approval shape: one reviewer, one decision,
  one resumed execution. It says nothing about multi-party approval, an
  approval that times out, a rejection that re-enters the trajectory, or
  per-human identity in the SVID rather than in the payload — all of which
  M3's own spec puts out of scope, and none of which this attribute set would
  be sufficient for.

### Gap 3 (closed, M2.2): policy and autonomy decision spans

- **Evidence:** No convention found, and re-checked for M2.2 rather than
  assumed still true: OpenTelemetry's semantic conventions have no span, no
  attribute namespace and no registry entry for a policy, authorization or
  autonomy decision. The nearest neighbours are all about the *principal*
  (`user.id`, the deprecated `enduser.*`), not about a verdict. So
  `policy.decision` and the nine `policy.*` attributes are this project's own
  to define, and there is nothing upstream to defer to or to file against.
- **Status: closed in M2.2, by defining them in the open.** The span is
  emitted by `platform/policy-decision-service`, its attribute keys are
  declared once in `platform/policy-decision-service/pds/telemetry.py` (which
  is the source this document cites rather than re-typing), and the full
  measured inventory — read off two live traces, with the conditional keys
  identified by measurement rather than from the source's declared tuple —
  is in "The policy decision, as measured (M2.2)" above.
- **Why this is a closure and not an invented convention.** The distinction
  this project's governing rule draws is between inventing telemetry *for a
  third-party component* — forbidden, without exception — and instrumenting
  our own code where no framework does it for us. A policy decision is the
  second: no library emits one, so either this project names the attributes or
  the decision is invisible. The names are prefixed `policy.` precisely so a
  reader can tell them apart from the `gen_ai.*`, `mcp.*` and `http.*`
  attributes the frameworks emit, and this document says outright that they
  are ours. Nothing was renamed, aliased or back-filled onto anyone else's
  spans to make the story neater.
- **What would make this reopen.** If OpenTelemetry ever specifies a
  policy-decision convention, these names become divergence rather than
  invention, and the honest move then is to adopt theirs and record the
  rename here. That is a real possibility rather than a formality: the GenAI
  semantic conventions are actively growing towards agent governance, and the
  same repository already carries four independent open proposals for A2A
  identity (see Gap 1). Nothing in this project would notice the day such a
  convention lands, which is worth knowing.

### Gap 4: baggage propagation across the MCP boundary

**Dated note, M8 Task 3 (measured): payments-api's ledger READS now carry the claim id; its money hops still do not.** The claims-agent resumes a waiting claim with `northstar.claim.id` in baggage, and its `GET /payments/{paymentId}` reaches payments-api with it: four such server spans carried the claim id after one approval run, each parented by a claims-agent `GET`. `POST /payments/propose` and `POST /payments/{paymentId}/execute` carry none, and no `policy.decision` span does, so the trace-id join below is still what reaches the money hops.

- **Evidence:** Neither. `northstar.claim.id` reaches payments-mcp on **0** of
  its tool-call spans, and payments-api on **0** of its spans, in a live
  capture of the full chain, asserted by
  `tests/e2e/test_claim_correlation.py::test_claim_id_is_on_every_mcp_hop_span`.
  (Originally recorded as "Where `northstar.claim.id` lands" in the **M1.1**
  milestone's Task 7 report — a git-ignored SDD working artifact, cited as
  provenance rather than as a document you can open; the runnable test above
  is the evidence. Note that M1.2 has its own, unrelated Task 7.)
  The `traceparent`/`tracestate` crossing is intact — this is a
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
  `@pytest.mark.xfail(strict=True)` (see "Running the tests"
  above for why), narrowed to this one assertion per a review finding — a
  fixture-setup failure upstream of it now surfaces as an ordinary `ERROR`,
  not a false-quiet `xfailed`. `scripts/demo evidence` is built around this
  gap: it finds a claim's traces by claim id, then the decision and the money
  hops by trace id.

### Gap 5: FastMCP never emits `gen_ai.operation.name` on tool-call spans

- **Evidence:** FastMCP 4.0.3's `tools/call propose_payment`/`execute_payment`
  spans carry `gen_ai.tool.name` (e.g. `execute_payment`) correctly, but never
  `gen_ai.operation.name`, where the GenAI semconv
  (`open-telemetry/semantic-conventions-genai`) specifies `execute_tool`
  for a tool invocation (`tools/payments-mcp/NOTES.md` §2). The sibling
  attribute is done properly; the operation classifier is simply absent.
  **M4 Task 2 found the metrics-side counterpart of this gap**: the same
  FastMCP 4.0.3 records no `gen_ai.execute_tool.duration` either — indeed no
  metric of any kind, since the package contains no `get_meter(` call at all.
  See Gap 8 below.
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
- **Update (Task 7, M1.2):** the version bump described above as future work
  has happened — `mcp` moved `1.9.4` -> `1.28.1`, clearing four pip-audit
  advisories (PYSEC-2026-1617/-1618/-3482/-3483), isolated from every other
  M1.2 change so a propagation regression would have exactly one suspect
  (`tests/e2e/test_payments_mcp_trace.py`, re-verified green at `1.28.1`,
  including a deliberately-broken `_meta` key proven to fail first). `1.28.1`
  does carry `#1231`'s merged `call_tool(meta=...)`, confirmed directly
  against the installed distribution's signature — but `mcp_tools.py` was
  deliberately left unchanged, byte-for-byte, in the same task that bumped
  the pin: adopting the simpler call path is still available future work,
  now recorded in `docs/upstream-findings.md` Finding 4, not decided here.
- **Update (2026-09-11, M3 remaining-bumps step 1): `mcp` is now 2.2.0, and
  the gap is not merely fixed, it is obsolete.** The SDK no longer needs to be
  *asked* to attach trace context to `params._meta`: its JSON-RPC dispatcher
  injects W3C trace context into `_meta` on every outbound request
  unconditionally, with a source comment naming SEP-414
  (`mcp/shared/jsonrpc_dispatcher.py`, read from the installed 2.2.0 — quoted
  in "The `mcp` client's own telemetry, as measured" above). Measured live, not
  read off a changelog: the injection is what parents payments-mcp's server
  span under the client's new `MCP send tools/call <tool>` span.
  **Consequence for this repo, stated rather than acted on:**
  `mcp_tools.py`'s hand-built `send_request` path — the one piece of production
  code here that exists because of this gap — is now redundant. It is not
  harmful (the SDK's own injection overwrites the same keys with a deeper
  parent in the same trace, and the hand-built path is still what the unit
  tests pin), and it was **left in place**: deleting it is a behavioural change
  that deserves its own step and its own live proof, not a passenger on a
  dependency bump. Recorded here and in `docs/upstream-findings.md` Finding 4
  so the next reader finds a decision rather than an accident.

### Gap 7 (closed, M1.2 Task 8): the model's own outbound HTTP hop had no CLIENT span — already fixed upstream, and now applied here too

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
- **Where the actual gap lived:** entirely in this project's own
  `bootstrap.py`, which called `HTTPXClientInstrumentor()` and not also
  `HTTPX2ClientInstrumentor()`. **Closed by M1.2 Task 8**: `bootstrap.py` now also
  calls `HTTPX2ClientInstrumentor().instrument()` alongside the existing
  `HTTPXClientInstrumentor()` call — the one-line addition this section
  originally predicted, with the version already installed, no dependency
  bump, no upstream wait.
- **Measured after the fix (M1.2 Task 8, live capture, before/after diff against a
  saved pre-fix trace dump):** each `chat claude-sonnet-5` span now has
  exactly one httpx-instrumented CLIENT span nested directly under it — three
  such spans in a three-turn conversation, matching the three `chat` spans
  one-for-one, no duplicates. This project's httpx instrumentation runs with
  `OTEL_SEMCONV_STABILITY_OPT_IN=http`
  (`deploy/k8s/settlement-agent.yaml`), so the new span carries the
  **stable** HTTP semconv spelling already used for this service's MCP-bound
  httpx spans, not the legacy one — read from a live attribute dump, not
  assumed: `http.request.method=POST`, `http.response.status_code=200`,
  `server.address=api.anthropic.com`, `server.port` absent (HTTPS, no
  explicit port in the URL), `url.full=https://api.anthropic.com/v1/messages?beta=true`,
  `network.peer.address=api.anthropic.com`,
  `network.protocol.version=1.1`, `otel.scope.name=opentelemetry.instrumentation.httpx`,
  `otel.scope.version=0.65b0`. `HTTPXClientInstrumentor` and
  `HTTPX2ClientInstrumentor` are two classes in the same
  `opentelemetry.instrumentation.httpx` module and share its
  `get_tracer(__name__, ...)` call, so `otel.scope.name` alone cannot
  distinguish an httpx span from an httpx2 span — the model-bound span is
  identified by `server.address == "api.anthropic.com"` instead. Pinned by
  `tests/e2e/test_settlement_llm_trace.py::test_anthropic_client_span_exists_and_is_parented_to_chat`,
  which replaces the predecessor test named below (retired, per its own
  docstring's instruction, the day this fix landed).
- **No duplicate span.** The concern going in was that pydantic-ai's own
  `chat` span might already be the raw HTTP request, and adding
  `HTTPX2ClientInstrumentor` would double-count that hop's latency. Measured:
  it is not — `chat` is a separate, outer CLIENT span (pydantic-ai's logical
  "call the model" operation, carrying `gen_ai.*` attributes) with the new
  httpx CLIENT span nested one level inside it (carrying `http.*`
  attributes). Two CLIENT-kind spans at two different nesting depths,
  representing two different things, is the expected and desired shape, not
  a duplicate.
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
- **Status: closed (M1.2 Task 8), and there was never anything to file.** Unlike
  Findings 1 and 3 in `docs/upstream-findings.md`, this was never
  filing-ready material — filing would have asked upstream to do something it
  already did. The one remaining action was local — apply
  `HTTPX2ClientInstrumentor()` in this project's own `bootstrap.py` — and
  M1.2 Task 8 did exactly that; the omission was this project's, never an
  upstream gap. See `docs/upstream-findings.md` Finding 5 for the full
  write-up in that document's format, updated to match.

### Gap 8 (M4 Task 2): five of the six §6 GenAI metrics are emitted by nothing

- **Evidence:** with a `MeterProvider` installed in all five Python services
  that existed at M4 Task 2 (`platform/eval-scorer` arrived in Task 4 with
  none — limitation 2) and Prometheus receiving OTLP directly, exactly **one**
  of the six metrics M4
  spec §6 asks about reaches it — `gen_ai.client.token.usage`, from
  PydanticAI. `gen_ai.invoke_agent.duration`,
  `gen_ai.invoke_agent.inference_calls`, `gen_ai.invoke_agent.tool_calls`,
  `gen_ai.execute_tool.duration` and `gen_ai.client.operation.duration` are not
  merely absent from Prometheus: **not one of those names appears anywhere in
  `pydantic-ai-slim` 2.42.0, `a2a-sdk` 0.3.7, `fastmcp` 4.0.3 or `mcp` 2.2.0**,
  and the last three contain no `get_meter(` call at all, so they create no
  instrument of any kind. The full per-metric table, the per-service positive
  controls that make the negatives readable, and the two probes sent to prove
  the export path of the services that record nothing, are under "Metrics, as
  measured (M4 Task 2)" above.
- **Upstream check:** the conventions are not in draft — all twelve GenAI
  metrics, these six included, are published in
  `open-telemetry/semantic-conventions-genai` at Development stability. So this
  is a framework-coverage gap against a published convention, not this project
  asking for something unspecified. The trackers were searched; see
  `docs/upstream-findings.md` **Finding 12** for what was found and what was
  not.
- **Action:** nothing hand-rolled. §6's standing rule is that no metric is
  invented to fill a hole in a framework's coverage, and none was — a
  `gen_ai.*`-named instrument authored here would make this project's telemetry
  disagree with what a future, fixed framework emits natively, which is the
  same argument Gap 5 makes for not hand-setting `gen_ai.operation.name` on
  FastMCP's spans. **Two tests enforce that, because it takes two layers** —
  and the split is stated because an earlier version of this bullet cited only
  the first and claimed more for it than it can do (M4 Task 2 review finding
  F5). `tests/e2e/test_prometheus_metrics_ingest.py::
  test_no_metric_was_invented_to_fill_a_framework_gap` asserts that Prometheus
  holds no `gen_ai.*` name beyond the one PydanticAI emits; it reads a store,
  so it can see what a series is *called* and never who authored it.
  `tests/unit/test_metrics_wiring.py::
  test_no_service_authors_a_metric_under_a_convention_namespace` is the half
  that sees origin — it walks the AST of every Python file under `agents/`,
  `apps/`, `platform/`, `tools/` and `systems/` for an instrument created under
  a namespace an instrumentation library owns, and runs without a cluster.

## Known limitations, recorded rather than fixed (M2.2)

Every item below is a **deliberate scope boundary**, not a defect discovered
late: items 1–9 were each found and argued during M2.2 Tasks 3, 5 or 7,
decided against fixing in that task, and logged for this section. Task 8
re-verified every one of them against the source and — where it is a runtime
property — against the live cluster, rather than transcribing the list it was
handed. That re-check earned its keep twice: **one item on the list did not
survive verification and is corrected in place below** (item 5, which had been
attributed to the wrong service), and **item 10 is not on the list at all** —
it was found by the re-check itself. **Item 11 is later still**: it was found
by the final whole-branch review, after Task 8 closed, and item 10 was rewritten
in the same round because the review demonstrated that its stated cause was
false.

**M3 (Supervised) re-read all eleven and added none — but amended three, and
one of those amendments is a finding rather than an update.** Item 3 said
narrowing `_latch` was "M3's problem"; M3 sidestepped it instead, so the item
stays open and now says which case is still exposed. Item 10's published PDS
test count and its "what the gate does not run" list both moved, and it gained
the M3 recurrence of its own defect (`reviewer-ui`, same omission, one
milestone later). **Item 11's attribution turned out to be false**: it blames
ruling R4 for the Policy Decision Service's image shipping `pytest`, and
measuring all five Python Dockerfiles shows every service in this repository
does the same and always has — the cause is a repo-wide convention, not that
ruling, and the item describes five images rather than one. The correction is
appended to the item rather than swapped into it. Everything else here was
re-checked against the source or the live cluster and is unchanged.

None of these is a telemetry-invention issue and none of them makes a
published claim in this document false. They are the places a reader who
pushes on this system will find its edges.

**1. The policy limit is currency-blind.** `platform/policy-decision-service/pds/rules.py`
keys `EXECUTE_LIMITS` on `(SPIFFE ID, autonomy level)` and compares
`req.amount > limit` with no reference to `req.currency` at all — the currency
reaches `decide()` and is used only to render the refusal text (`_money`). So
a request for 6,200 of any unit is judged against a limit denominated in
euros. It is bounded today by two things outside the PDS, and by nothing
inside it: payments-api's execute handler confirms the stated currency against
the recorded proposal (`e062c10`), and `contracts/openapi/payments-api.yaml`
puts `enum: [EUR]` on the *propose* body, so a non-EUR proposal is refused
before it can be executed. Both of those are real and both are somewhere else.
The honest statement is that the platform, as a whole, is EUR-only; the policy
engine, on its own, does not know what a euro is. Fixing it properly means
either a per-currency limit table or an explicit refusal of any currency the
table is not denominated in — a design decision, and M5's autonomy work is
where the limit table becomes configurable anyway.

**2. Unbounded attacker-supplied strings reach a span attribute.**
`decide()`'s refusal texts interpolate values that came off the wire:
`f"unknown tool {req.tool!r}"` echoes the tool name, and `_money()` echoes the
currency. `parse_call` type-checks both (a non-string becomes `None` or `""`)
but does not length-check either. That string becomes
`AuthorizationError.reason`, which agentgateway v1.5.0 copies verbatim into
`otel.status_description` on its `tools/call` SERVER span — measured, see
"agentgateway's guardrail telemetry, as measured" above. So a caller that can
reach the gateway and pass authentication can put an arbitrary-length string
of its choosing into this system's telemetry. The blast radius is bounded by
who can get that far — only a workload holding a valid JWT-SVID for the
gateway's audience, which today is `settlement-agent` alone — and the failure
mode is a fat span rather than an injection, since it lands as an OTLP
attribute value and not in a query or a shell. Recorded because "telemetry is
a sink for untrusted input" is exactly the kind of thing that is obvious once
stated and invisible until then.

**3. `_latch` latches the whole run, not the refused call.**
`agents/settlement-agent/settlement.py`'s `_latch` exists so that a model
cannot sit on stage retrying a payment it will never be allowed to make: after
any refusal, *no further payment call* leaves the process for that claim, and
the second attempt is logged and handed the same terminal refusal. The
granularity is deliberately coarse for M2.2, where the only correct next move
after a refusal is to narrate it. It becomes wrong the moment a future
milestone wants a re-propose at a compliant amount, or an escalate-then-retry
path after a human approves — both of which are legitimate follow-on calls
that this latch would refuse. Narrowing it to the specific refused call is the
fix, and it is M3's problem, not a bug today.

**M3 update, and it is a sidestep rather than the fix this item asked for.**
M3 did not narrow `_latch`; it is byte-identical. The escalate-then-retry path
this item predicted would collide with it does not, and the reason is
structural: `_latch` is called from exactly two places, the bodies of the two
`@settlement_agent.tool` functions, and `execute_approved` **is not a model
run** — it makes no model call, builds no `RunContext`, and calls
`PaymentsTools.execute` directly, so the latch is not on that code path at
all. (It also runs in a different process under a different identity,
`senior-settlement-agent`, but that is not what saves it; a same-process
approval would be equally unlatched.) The collision was avoided by the
milestone's architecture rather than by a change here. **The item stays open
exactly as written**: a *re-propose at a compliant amount by the original
agent*, on the same claim, is still refused by this latch, and that is still
the case a future milestone will hit. What has changed is only that M3 is no
longer the milestone that was going to fix it (M3 Task 5B, re-read against
`settlement.py`, not inferred).

**4. `_leaf_exception` can drop a denial-bearing `McpError` in a mixed
exception group.** `agents/settlement-agent/mcp_tools.py`'s unwrapper prefers a
`ToolCallError` leaf anywhere in the group and otherwise takes `leaves[0]`.
`McpError` — which is what a policy denial arrives as (measured, M2.2 Task 1)
— is not a `ToolCallError`, so a group holding both would surface the
`ToolCallError` and lose the denial, and the `except McpError` clause below
does not help because the group clause wins first. Not reachable today: every
exception group measured on this path has held exactly one leaf. Left alone
because the preference is *correct* for the case it was written for (M1.1's
tool-failure text) and changing it needs a real mixed-group case to reason
from rather than a hypothetical.

**5. `settlement-agent` configures no logging, so its own `logger.info` calls
are inert in-pod. The Policy Decision Service does not have this problem —
this item was carried into Task 8 attributed to the wrong service, and the
re-check is what caught it.** Measured on the live cluster: `settlement-agent`
has no `logging.basicConfig`, `logging.config` or `dictConfig` anywhere in its
source, so its root logger sits at WARNING with no handlers and only
`logging.lastResort` reaches stderr. `decide_settlement`'s refusal line and
`executor.py`'s reply line are both INFO and print nothing; `_latch`'s WARNING
does appear, which is what makes the absence of the other two real evidence
rather than an assumption about log levels. (The uvicorn `INFO:` lines visible
in that pod's log are uvicorn configuring its own logger, not this project's.)
The PDS, by contrast, calls `logging.basicConfig(level=os.environ.get("LOG_LEVEL",
"INFO"))` in `serve()` — and its pod log carries one
`decision=… rule=… tool=… caller=…` line per decision, read live. The levels
in `settlement-agent` were left at INFO rather than inflated to make them
visible, which is the correct level for a normal-operation event; the decision
that is genuinely open is whether that service gets a logging config at all.

**6. Criterion 4's structural guard keys its allowlist on file basename.**
`tests/e2e/test_policy_denial.py`'s `ALLOWED_MONEY_COMPARISONS` is keyed
`(component, path.name, source)`. `path.name` is the basename, so a future
subpackage file sharing a basename with an allowlisted one — a second
`settlement.py` under `agents/settlement-agent/something/` — would silently
inherit its two entries and could carry an unargued money comparison past the
guard. The guard already walks recursively (`rglob("*.py")`), so the file
would be *scanned*; only the allowlist lookup is too generous. The fix is one
line, keying on the path relative to the component root instead, and it is
suggested rather than required now because no such subpackage exists in either
governed component.

**7. Criterion 4's structural guard reads only `*.py`.** Same test, same
`_source_files`: a threshold expressed anywhere other than Python — a
Dockerfile `ENV`, a ConfigMap, a Helm value, a YAML default — is invisible to
the vocabulary, literal and comparison checks alike. Entirely latent today,
because the €6,000 allowance lives in `rules.py` and nowhere else and neither
governed component has a config file that could carry a number like it. It
becomes relevant at **M5**, when the execute allowance becomes a feature flag
and therefore configuration: at that point a limit could legitimately be
expressed outside Python, and this guard would stop covering the thing it is
named for. Recorded here so that is a decision M5 makes rather than a hole M5
walks into.

**8. `test_gateway_is_the_only_route.py`'s bypass probe is now enforced at a
different layer than the one it was written to test.**
`test_an_unprivileged_workload_cannot_reach_the_tool_directly` probes
`claims-agent → payments-mcp:8000` and asserts it fails. It was written for
`d17309c`'s **ingress** policy on payments-mcp. Task 7's
`claims-agent-egress-allowlist` gives `claims-agent` no egress to
`payments-mcp` at all, so the connection is now dropped at **egress**, before
the ingress policy it was written for is ever consulted. The assertion still
passes and still fails loudly if *either* layer breaks — which is defence in
depth working, and is why the test was not changed — but it no longer
attributes the block to the ingress half on its own. Task 8 corrected that
test's docstring to say which layer is actually being exercised; the
assertions are untouched. The same asymmetry, from the other side, is recorded
in Task 7's own mutation 4: adding a `payments-mcp:8000` egress allowance left
the behavioural probe green because the ingress policy was still dropping the
packet, and only the BPF-map assertion caught it.

**9. There is no Hubble flow evidence anywhere in this project, and no
document should imply otherwise.** `enable-hubble: false` in this cluster's
`kube-system/cilium-config` — read live, not from the chart default. Cilium
emits Prometheus metrics and Hubble flows; only the first is available here.
Every network claim in this repository therefore rests on one of two things:
reads of the BPF policy map (`cilium-dbg bpf policy get <endpoint>`, which is
what pins the egress allowlists by set equality) or TCP probes run from inside
the pods. No Cilium telemetry is asserted as a span anywhere, and none was
invented — which is the same rule this document applies to agentgateway and
SPIRE, applied to a third component that also has no OTel story.

*M12 update (2026-10-21, design and expected behaviour; the demo cluster had not
yet run on Linkerd).* Cilium is gone, so the paragraph above is the record of
M2.2 to M11. What replaces it: an ingress refusal is now an HTTP 403 (a gRPC
`PERMISSION_DENIED` at the PDS) **counted** by the destination proxy as
`inbound_http_authz_deny_total{srv_name, route_name, client_id}`. It is still
**not a span** on its own. An egress drop (NetworkPolicy, enforced by kindnet)
is silent: nothing counts it. Every network claim now rests on an HTTP status
plus that counter, or on a probe from inside a pod.

*2026-10-24 update: proxy tracing is on.* The paragraph above was written with
proxy tracing off. It is now on (`deploy/platform/linkerd/values.yaml`), so a
refused request that carries a sampled `traceparent` is expected to leave an
inbound proxy SERVER span with `http.response.status_code` 403 (the span layer
wraps the authorization). Requests without a `traceparent`, such as the reach
probes, still leave only the counter. Expected, to be measured on the first live
run. See "Linkerd proxy spans" below.

**Linkerd proxy spans (2026-10-24; expected, to be measured on the first live
run).** Read off the pinned proxy's source (linkerd2-proxy `v2.370.0`, which
`edge-26.10.1` ships: `linkerd/trace-context/src/service.rs`,
`linkerd/app/core/src/http_tracing.rs`, `linkerd/app/src/env/trace.rs`) and the
chart's templates, not off a running cluster:

| What | Expected value |
|---|---|
| Export | OTLP gRPC to `otel-collector.observability.svc.cluster.local:4317`, over mTLS; the proxy checks the collector's identity `otel-collector.observability.serviceaccount.identity.linkerd.cluster.local`, which is why the collector pod is meshed |
| Sampling | none of its own: a span only for a request whose incoming `traceparent` is sampled, never a new trace. The apps sample everything, so nothing is sampled away. A full export queue drops a span with an `info` log ("Span dropped"); that is the one loss path, and it is not measured yet |
| Spans per meshed hop | four: the caller's proxy emits an outbound SERVER and an outbound CLIENT span, the callee's proxy an inbound SERVER and an inbound CLIENT span. Each layer re-parents the request, so the callee's app SERVER span becomes a child of the inbound CLIENT proxy span. A hop with only one meshed end has two |
| Span name | the request path (`/`, `/mcp`, `/payments/propose`, `/envoy.service.ext_mcp.v1.ExtMcp/Check`, ...). Not a registry name, so live-check judges none of them |
| Span attributes | `direction` (`inbound`/`outbound`), `http.request.method`, `url.scheme`, `url.path`, `url.query`, `url.full`, `network.transport`, `server.address`, `server.port`, `user_agent.original`, `http.request.header.content-type`, `http.request.header.content-length`, `http.request.header.l5d-orig-proto`, `http.response.status_code`. No other header is copied (no `authorization`, no A2A push token) |
| Resource attributes | `service.name=linkerd-proxy`, `k8s.pod.ip`, `k8s.pod.uid`, `k8s.container.name`, and every label of the pod (downward API), which includes `app`, `pod-template-hash`, the injector's `linkerd.io/*` labels and this repo's `northstar.io/plane` and `northstar.io/owner` |

What the repo does about it: the e2e harness (`tests/harness/jaeger.py`,
`Trace`) leaves `linkerd-proxy` spans out of `spans`, points each app span at its
nearest app ancestor, and keeps the proxy spans in `proxy_spans`; every
propagation assertion therefore still means "the app's context reached the next
app", and the settle loops count both (`span_count`). `telemetry/live-check.toml`
lowers `northstar.io/*` (pod labels on the proxy's resource) to information
before the owned-names override, and `tests/harness/livecheck.py` leaves them
out of the owned violations. Nothing is added to the Weaver registry: the proxy
emits no owned name. The controllers' own tracing stays off: linkerd2's Go
controllers export with the OpenCensus agent exporter (`pkg/trace`), not OTLP.

To measure on the first live run: that proxy spans reach Jaeger under
`linkerd-proxy`; the span names and attributes above; the count per driven
claim (it replaces the "about 140" estimate written when tracing was off); that
no app span is orphaned once the trace settles; and that live-check reports no
owned violation.

**10. `tests/unit` is no longer self-sufficient on a fresh clone: it needs one
generated artifact that only a script outside it produces.** Rewritten, not
retired, in the M2.2 final-review fix round — and the rewrite is the point, so
what it replaced is stated first.

**What this item used to say, and why it was wrong.** Through M2.2 it read
*"The Policy Decision Service's own 95-test suite is run by neither the gate
nor any drift guard"*, and gave as the cause that running that suite *"needs a
generation step that neither the gate's per-service loop nor the hermetic
`tests/unit` subset is willing to take on"*. **That cause was false in its
premise.** The final whole-branch review demonstrated (by moving the stubs
aside) that both already *require* the generated stubs, because
`agents/settlement-agent/tests/test_policy_deny_contract.py` imports
`pds.server` — the settlement-agent suite aborts at collection, 0 of 79 tests
run, and `tests/unit`'s own per-service count guard goes red. So the trade the
item described had already been made and was collecting none of its benefit,
and `README.md` compounded it by telling a new operator the PDS setup was
*deliberately* not needed for the gate, which meant a fresh clone following the
README verbatim could not run `scripts/smoke-test.sh` at all. That half is
fixed: the gate now requires the PDS venv, runs `./gen-stubs.sh` ahead of every
suite, and runs the PDS's suite (96 tests when that round wrote this sentence;
105 today, re-measured for M3 Task 5B); `_PER_SERVICE_SUITES` has a sixth row;
the README says what is actually true. The stubs were **not** committed — that
re-opens the image/repo divergence ruling R22 exists to prevent.

**M3 Task 5B note: the same omission recurred one milestone later, with
`reviewer-ui`, and was closed the same way.** M3 Task 4 shipped
`apps/reviewer-ui` with 50 tests of its own, in neither the gate's per-service
loop nor `_PER_SERVICE_SUITES` nor this document's per-service row. Unlike the
PDS's case there was no hidden dependency running any of them, so the gate
could have gone green with that component's suite entirely broken. All three
sites now carry a seventh row. It is recorded here rather than only in the
per-service row because *this* is the item a reader checking "what is the gate
not running" would come to.

**Coverage was never the issue and still is not.** The decision this milestone
is named for is covered end to end from the repo-root tree —
`tests/e2e/test_policy_denial.py` drives a real denial through the deployed
PDS, and `test_policy_deny_contract.py` drives the real `decide`/`build_result`
chain with a test-only PDS import.

**11. The deployed Policy Decision Service image carries its own build and test
tooling.** Found by the final whole-branch review (finding 8), and a
consequence of ruling R4 rather than an oversight in it.
`platform/policy-decision-service/requirements.txt` is deliberately **one**
file for runtime and test — R4 resolved a single transitive closure for Tasks 2
and 3 together instead of two possibly-incompatible ones — and
`platform/policy-decision-service/Dockerfile:4` installs that whole file with
`pip install --no-cache-dir -r requirements.txt`. So the running image contains
`grpcio-tools==1.83.1` (plus its `setuptools==84.0.0` build dependency) and
`pytest==9.1.1` (**J6, whole-branch review, 2026-09-15: was `9.0.3` here until
M5 Task 1's bump; corrected rather than left to drift a second time**) with
its four-package closure, none of which `pds/server.py`
imports: `grpcio-tools` is needed only at build time, by the `RUN
./gen-stubs.sh python` layer six lines below, and `pytest` is needed only by a
developer running the suite from the source tree. The service's ingress is
otherwise carefully minimised — its own policy (a Cilium document until M12,
which admitted agentgateway and the node's kubelet probe, ruling R6; since M12 a
Linkerd Server that admits only agentgateway's identity) lets nothing else in the
cluster reach 9100 — so this is the one place the component is broader
than it needs to be. It is recorded rather than fixed because every fix is a real
restructuring with its own cost: a multi-stage build that generates the stubs
in a builder image and copies them forward would re-open the image/repo
divergence R22 exists to prevent unless the copy is provably from the same
vendored proto, and splitting `requirements.txt` into runtime and test halves
re-opens exactly the two-closures problem R4 closed. Neither is a five-line
change, and neither belongs in a milestone whose subject is the decision. What
is *not* claimed here: that this is exploitable. No measurement of that was
made. What is measured: the image runs as a non-root uid
(`useradd --uid 10001 app` / `USER app` in the same Dockerfile), and the
serving path spawns no subprocess at all — `grep -rn 'subprocess\|os.system'
platform/policy-decision-service/pds/` returns nothing.

**M3 Task 5B correction, left standing with this note rather than rewritten:
this item's attribution is wrong, and it is wrong in the direction that makes
the problem look smaller than it is.** It presents the shipped test tooling as
"a consequence of ruling R4", i.e. as specific to the Policy Decision Service
and to the decision that gave it one requirements file for runtime and test.
That is not the cause. **Every Python service in this repository does the same
thing, and always has** — measured 2026-09-15 (whole-branch review, J6) by
reading all six Dockerfiles and all six requirements files, re-dated from the
M3 Task 5B measurement below, which read five of each and predates
`platform/eval-scorer` (removed 2026-10-24, so its row is gone from the table):

| Service | Dockerfile install | `pytest` in that file |
|---|---|---|
| `agents/settlement-agent` | `pip install --no-cache-dir -r requirements.txt` | `pytest==9.1.1`, `pytest-asyncio==1.4.0` |
| `agents/claims-agent` | same | `pytest==9.1.1`, `pytest-asyncio==1.4.0` |
| `tools/payments-mcp` | same | `pytest==9.1.1` |
| `platform/policy-decision-service` | same | `pytest==9.1.1` |
| `apps/reviewer-ui` (M3 Task 4) | same | `pytest==9.1.1`, `pytest-asyncio==1.4.0` |

**J6, whole-branch review: this table published `pytest==9.0.3` for all five
original rows, and was missing the sixth (`eval-scorer`) entirely, until this
fix wave.** M5 Task 1 bumped all seven of this repo's `requirements.txt` files
(the six above, plus the root `tests/requirements.txt`) to `9.1.1`, clearing
PYSEC-2026-1845; every venv, including the root one, measures `pytest 9.1.1`
today. The preamble's own
provenance claim — "measured by reading all five Dockerfiles and all five
requirements files" — decayed the same way the export-destinations table's
did (C1): the moment the files it named moved, without the table it produced
moving with them. Corrected here rather than silently, per this document's
own standing convention.

So the pattern is this repository's uniform one-requirements-file convention,
not R4; R4 only decided that the PDS would not be the exception. The `pytest`
half of this item therefore describes five images, including the one M3 added.
The **`grpcio-tools`/`setuptools`** half *is* PDS-specific and *is* a
consequence of the `gen-stubs.sh` build layer, exactly as written — no other
service generates anything at build time. And the sentence "this is the one
place the component is broader than it needs to be" is true read narrowly (the
PDS's ingress is minimised and this is where *that component* is broad) and
misleading read at the speed a reader reads it, which is how it survived a
whole-branch review.

Not fixed, for the reason the item already gives and one more: splitting
`requirements.txt` per service re-opens R4's two-closures problem five times
over rather than once, and the same thing is still not claimed here that was
not claimed before — nobody has measured whether any of it is exploitable.
What is now accurate is the *scope*.

## Open questions

### OQ-1: Content capture is a trade — ANSWERED 2026-09-13 (M4 Task 5), default unchanged

**Answered, not acted on.** `NORTHSTAR_CAPTURE_CONTENT` still defaults to `true`
in `deploy/k8s/settlement-agent.yaml` and
`deploy/k8s/senior-settlement-agent.yaml`, and M4 changed neither. What follows
is the deliverable M4 spec §7.7 asked for: *exactly* which claims the evidence
view can no longer support at `NORTHSTAR_CAPTURE_CONTENT=false`. The four-step
experiment's shape is preserved below, along with everything M2.1 Task 3
measured; this section is the answer on top of it.

#### The answer, stated as narrowly as it was measured

**Zero. The evidence view loses nothing.** Every field of every panel is
populated identically at OFF. (The view was removed on 2026-10-24;
`scripts/demo evidence` reads the same platform-emitted fields from Jaeger, so
the answer carries over.)

Measured as a matched pair on this cluster, both runs the same supervised
`CLM-1842` story at €6,200 — refused by `settlement-agent`, escalated,
approved by a human, executed by `senior-settlement-agent` — and read back
through `tests/e2e/test_evidence_view.py::evidence_for`, which is the function
whose docstring maps each key to the panel of `evidence.ndjson` that renders
it. So this is the dashboard's own query path, not a paraphrase of it. Mode was
read off the **running pods** with `jsonpath` before each run, never off the
Deployment spec (`kubectl diff` cannot prove a pod is serving a value; M2.1's
step 5 records why).

| Panel field | ON (`5ba013da…`, `69b3186f…`) | OFF (`6084d46e…`, `8e3be791…`) |
|---|---|---|
| 1 · trace ids | 2 | 2 |
| 2 · `policy_decision` / `rule` | `deny` / `settlement-agent.assisted.execute_limit` | identical |
| 2 · `limit` / `requested` | 6000 / 6200 | identical |
| 3 · `reviewer` / `review_decision` | present / `approve` | present / `approve` |
| 2 · `executing_identity` | `.../ns/northstar-agents/sa/senior-settlement-agent` (retired in M8) | identical |
| 2 · `executing_rule` | `settlement-agent.assisted.execute_limit` | identical |
| 4 · `money_hops` | propose → refuse → GET → GET → execute | identical |
| spans in the join | 214 | 216 |

**That result is the finding, and it is a statement about the design rather
than about redaction.** Not one of the evidence view's panels reads a `gen_ai.*`
attribute — every field it aggregates on is `attributes.policy.*`,
`attributes.review.*`, `attributes.northstar.claim.id`, `traceId`, `name` or
`resource.service.name`, all of them emitted by the platform (the PDS, the
reviewer UI, payments-api) and none of them gated on `include_content`. The
audit trail this milestone's headline rests on is reconstructed from what the
*platform decided*, not from what the *model said*. Content capture is
orthogonal to it.

#### What is lost, one step behind the view

The same pair, same traces, measured on the `gen_ai.*` layer an operator
reaches by following a trace id out of panel 1 — 28 distinct keys at ON, 25 at
OFF, reproducing M2.1 Task 3's figures on this milestone's own runs and index:

1. **"Did the agent ask for the amount the platform judged?"**
   `gen_ai.tool.call.arguments` carries `{"amount":6200,"currency":"EUR"}` on
   `execute_tool propose_payment` and
   `{"payment_id":"PAY-000137","amount":6200,"currency":"EUR"}` on
   `execute_tool execute_payment` at ON; at OFF the key is absent from the whole
   trace. Panel 2 still shows `requested: 6200` — that is the PDS's own record
   of the request — so what is lost is not the number but the **independent
   corroboration** of it. At OFF, the platform's account of what was asked for
   is the only account there is, and nothing in telemetry can contradict it.
   This is the one genuinely load-bearing loss, and it is an audit property,
   not a debugging convenience.
2. **"What was the agent told it could do about *this* claim?"**
   `gen_ai.system_instructions` absent at OFF.
   `model_request_parameters` survives (1,974 characters, identical in both
   modes) and holds the static instructions and both tool schemas — what the
   agent was told it could do *in general*. The claim-specific half is gone.
3. **"What did the ledger say back?"** `gen_ai.tool.call.result` absent at OFF —
   so neither what the model proposed nor what the ledger recorded survives on
   the `execute_tool` spans.

Plus the gutting that key-level survival hides, re-measured on this pair:
`gen_ai.input.messages` text/thinking parts carrying `content` **5 of 5 → 0 of
5**; its `tool_call` parts carrying `arguments` **3 of 3 → 0 of 3**;
`gen_ai.output.messages` **2 of 2 → 0 of 2** and **2 of 2 → 0 of 2**;
`pydantic_ai.all_messages` **1,857 → 657 characters**.

**Restored and verified.** Both Deployments were returned by
`kubectl apply -f` of the committed manifests, and the serving pods read back
`NORTHSTAR_CAPTURE_CONTENT=true` via `jsonpath` — with the pod-template hashes
back at `58bc5d8988` and `559785b65b`, the same hashes as before the
experiment, which is independent proof the spec is byte-identical to what was
committed rather than merely carrying the same value.

#### The working alternative, cited as a measured example rather than a hypothesis

The all-or-nothing switch is not the only shape available, and the middle
ground this section has called for since M2.1 already exists in the project
owner's own `otel-platform-demo`: the **traces** pipeline there carries an
`attributes/redact` processor with `action: hash` on `user.email`
(`infrastructure/collector/deployment-values.yaml:166-169`). That is redaction
in the collector, per attribute, after the span is emitted — so the SDK keeps
emitting everything, the backend never sees the sensitive field, and the choice
is made once in the platform rather than once per service in an env var. It is
the concrete answer to the "narrower middle ground" bullet below, and M4 records
it as an existing, running example rather than proposing it. Building it here is
not M4's (spec §2 rejected redaction-as-a-demo-beat to keep the milestone at
three things); knowing it works is.

#### Why this closes as *answered* rather than as *decided*

The trade is now sized in the one place that matters for this demo: the
governance story — who decided, under which rule, at which limit, approved by
whom, executed under which identity, and where the money went — survives full
content redaction intact. The **model's own account of itself** does not. An
insurer that wants the first and not the second can run OFF today and lose
nothing this repo puts on a dashboard. Whether it *should* is still a decision
its owner makes, and this document does not make it: the default stays ON,
because on this demo the second question ("why that amount?") is the one the
talk is about.

---

*The original statement of the question, preserved.*

Task 5 flips the default to ON (see "Content capture" above), on the
reasoning that every attribute redacted is diagnostic capability spent. That
reasoning is not the end of the discussion — it is the honest statement of
one side of a trade this project makes deliberately and visibly, not a claim
that ON is correct for every deployment. The open question this milestone
leaves to be answered with more than a demo's worth of evidence
(**originally written as "for M2/M3 to answer"; M2 and M3 have both closed
without answering it, and M3's own spec §8 moved it explicitly to M4, "where
evals make the trade-off concrete" — restated here rather than left naming
milestones that have already passed**):

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

**The experiment this open question called for** (the project owner's own
framing, preserved verbatim as the shape of the test). M4 Task 5 ran the
OFF/ON comparison at step 4's level of detail — see "The answer" above — on a
*successful* run rather than an induced failure, which is the one departure
from this shape and is named as such:

1. Induce a genuine settlement failure or a wrong-amount run.
2. Attempt root-cause diagnosis from the trace with capture **off**. Record
   exactly what cannot be determined.
3. Repeat with capture **on**. Record what became determinable, and what
   claimant data that required retaining to determine it.
4. The deliverable is the difference — the specific diagnostic questions
   answerable only by retaining data a regulated insurer would rather not
   keep. That difference is the argument, not an assertion that ON or OFF is
   simply correct.

#### What M2.1 Task 3 measured, and what it deliberately did not decide

**Superseded as the answer by M4 Task 5's section above; kept because every
figure below is still the measurement it was, and because the attribute-level
detail here is what the M4 answer rests on rather than repeats.** At the time
it was written, OQ-1 was open. `NORTHSTAR_CAPTURE_CONTENT` still defaults to
`true` (`deploy/k8s/settlement-agent.yaml`) and nothing below changes it —
that part has not changed and M4 did not change it either.** What
follows sizes the trade; it does not resolve it. The four-step experiment above
is still unrun — steps 1 and 2 need an *induced failure*, and nothing here
induced one. What was run is the narrower, cheaper half that was worth doing on
the back of a live run this task already paid for: **the same successful
scenario twice, once at the shipped default `true` and once at `false`,
comparing which `gen_ai.*` attribute keys survive.**

**The two runs this section quotes, named once so every figure below has one
owner** (fix round 1, Finding 2 — an earlier revision of this section mixed
figures from three traces while claiming two, which is this project's signature
defect: the measurements were real and the labels on them were not):

| Mode | Trace | Services in it |
|---|---|---|
| ON (`NORTHSTAR_CAPTURE_CONTENT=true`, the shipped default) | `a227b3e224e6407e9846e014476456e2` | agentgateway, payments-api, payments-mcp, settlement-agent |
| OFF (`=false`, temporary, reverted) | `d52806ff6e6147df9609d5fc6265be0d` | agentgateway, payments-api, payments-mcp, settlement-agent |

Both are the same A2A settlement of €6,200, both post-reroute (through
agentgateway — verified by the service lists above, not assumed), both
`status: executed`. Mode read back off the *running* pod with `jsonpath`, not
off the Deployment spec, before each run. **Every ON-vs-OFF figure below comes
from one of those two traces**, and every figure states which.

One other trace appears further down, named at each mention and used only to
corroborate the ON key count — it never supplies half of a comparison:
`ebccbc73929c42b195e4d778a053e61b`, the **pre-reroute** run that
proved `tests/e2e/test_gateway_trace.py` RED, also measures 28 distinct
`gen_ai.*` keys at ON. It contains no agentgateway spans, so it is deliberately
not the ON trace named above — the gateway is irrelevant to what
`NORTHSTAR_CAPTURE_CONTENT` gates, but a like-for-like ON/OFF pair should differ
in exactly one variable, and this one differs in two.

**Measured: 28 distinct `gen_ai.*` keys at ON, 25 at OFF. Exactly three
disappear:**

| Key | ON | OFF | Span(s) it lives on |
|---|---|---|---|
| `gen_ai.system_instructions` | present | **absent** | `chat claude-sonnet-5` ×3, `invoke_agent settlement_agent` |
| `gen_ai.tool.call.arguments` | present | **absent** | `execute_tool propose_payment`, `execute_tool execute_payment` |
| `gen_ai.tool.call.result` | present | **absent** | `execute_tool propose_payment`, `execute_tool execute_payment` |

That is exactly the three this document's own inventory section above already
listed as content-gated, now confirmed by direct OFF-mode measurement rather
than from library source alone. The other 25 are the identical set in both
modes: `gen_ai.agent.call.id`,
`gen_ai.agent.name`, the five `gen_ai.aggregated_usage.*`, `gen_ai.conversation.id`,
`gen_ai.input.messages`, `gen_ai.operation.name`, `gen_ai.output.messages`,
`gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.finish_reasons`,
`gen_ai.response.id`, `gen_ai.response.model`, `gen_ai.system`,
`gen_ai.tool.call.id`, `gen_ai.tool.definitions`, `gen_ai.tool.name`, and the
five `gen_ai.usage.*`.

**Key-level survival understates the redaction, and that is the most important
thing this measurement found.** Three keys vanish, but two of the survivors are
gutted in place:

- `gen_ai.input.messages` / `gen_ai.output.messages` survive at both settings
  and still carry every part type (`text`, `thinking`, `tool_call`,
  `tool_call_response`), but at OFF **no part carries a `content` key and no
  `tool_call` part carries an `arguments` key** — measured across all three
  chat turns of each run, on the two traces named above:

  | Surface | ON (`a227b3e2…`) | OFF (`d52806ff…`) |
  |---|---|---|
  | `gen_ai.input.messages`, `text`/`thinking` parts carrying `content` | 8 of 8 | **0 of 5** |
  | `gen_ai.input.messages`, `tool_call` parts carrying `arguments` | 3 of 3 | **0 of 3** |
  | `gen_ai.output.messages`, `text`/`thinking` parts carrying `content` | 4 of 4 | **0 of 2** |
  | `gen_ai.output.messages`, `tool_call` parts carrying `arguments` | 2 of 2 | **0 of 2** |
  | `pydantic_ai.all_messages`, `text`/`thinking` parts carrying `content` | 5 of 5 | **0 of 3** |
  | `pydantic_ai.all_messages`, `tool_call` parts carrying `arguments` | 2 of 2 | **0 of 2** |

  The denominators differ between the two columns because the model produced a
  different number of parts on each run — these are two live runs of one
  scenario, not one run replayed. That is why the load-bearing statement is
  "every one at ON, none at OFF" rather than a ratio comparison. Denominators
  count only the part types that can carry the key at all: `text`/`thinking`
  for `content`, `tool_call` for `arguments` (a `tool_call` part never carries
  `content` at either setting, so counting it in that denominator would
  understate ON).
- `pydantic_ai.all_messages` on the `invoke_agent` span — the full transcript,
  and the single highest-risk surface in the trace — survives too, and shrank
  from **1599** characters at ON (`a227b3e2…`) to **657** at OFF
  (`d52806ff…`). Same part types, no content. (For completeness, since the
  corroborating pre-reroute ON trace is a different run: on `ebccbc73…` the
  same attribute is 1318 characters. Run-to-run variation in how much the model
  wrote — not a mode effect, which is exactly why the ON figure published here
  is the one from the named ON trace.)

So a reader who checks only "which keys are present" would conclude that
redaction removed 3 of 28 attributes, roughly a tenth. What it actually removed
is every piece of model-authored and model-received prose plus every tool
argument and result, while leaving the skeleton that proves the calls happened.

**`model_request_parameters` is the floor, and its name is not what the
prediction said it was.** This task was handed a falsifiable prediction derived
from reading pydantic-ai 2.41.0's source, and recording where it was wrong
matters more than where it was right:

- ✅ **Right:** `gen_ai.tool.call.arguments` is `include_content`-gated.
  Confirmed — absent at OFF.
- ✅ **Right:** `gen_ai.system_instructions` is `include_content`-gated.
  Confirmed — absent at OFF.
- ✅ **Right:** `gen_ai.tool.definitions` is emitted regardless. Confirmed —
  present in both sets.
- ✅ **Right:** 28 distinct `gen_ai.*` keys at the shipped default. Confirmed
  independently on this milestone's own live run, matching the previous
  milestone's count.
- ❌ **Wrong on the name.** The prediction called it
  `gen_ai.model_request_parameters`. The attribute pydantic-ai actually emits is
  `model_request_parameters`, with **no `gen_ai.` prefix** — so it appears in
  neither the ON set nor the OFF set, because it is not a `gen_ai.*` key at all.
  (This document's own earlier sections already used the unprefixed spelling;
  the prediction's prefixed form does not exist anywhere in a live trace.)
- ❌ **Wrong on "the sole OFF-mode survivor of the content-bearing keys."**
  Measured: `model_request_parameters` does survive OFF — 1974 characters on
  every `chat` span, identical in both modes, which is the same figure this
  document already records — so the *gating* half of that bullet holds. But it
  is not the sole survivor. `gen_ai.tool.definitions` survives fully, and
  `gen_ai.input.messages`, `gen_ai.output.messages` and
  `pydantic_ai.all_messages` all survive in redacted form. Calling it the sole
  survivor would have made OFF mode sound emptier than it is.
- ➕ **Missed by the prediction:** `gen_ai.tool.call.result` is content-gated
  too. The prediction named two content-gated keys; there are three. (This
  document's inventory section above already had all three, from the previous
  milestone — so the gap was in the prediction handed to this task, not in the
  project's own record. Worth stating because the missed one is the attribute
  carrying the ledger's record back, e.g.
  `{"paymentId":"PAY-…","amount":6200,…}`: at OFF, *neither* what the model
  proposed nor what the ledger recorded survives on the `execute_tool` spans.)

**What this does and does not settle.** It sizes the trade precisely: at maximum
redaction this project still retains what the agent was *told it could do*
(`model_request_parameters`, `gen_ai.tool.definitions`), that it called both
tools and in what order (`gen_ai.tool.name`, `gen_ai.tool.call.id`, message
shape), what it cost (`gen_ai.usage.*`, `operation.cost`), and the full trace
topology. It retains nothing about *this claim* or *this amount*. The
diagnostic question "why €6,200?" is, as this section already said,
unanswerable rather than merely harder — now confirmed by measurement in both
directions rather than from source reading alone. Whether that trade is
acceptable for a regulated insurer is still a decision this project's owner
makes, not one a measurement makes, and it is still open.

This is not academic bookkeeping: M2's Policy Decision Service and a later
reviewer UI both need to know *what the agent was told* to do their own
jobs — a policy engine cannot weigh a decision it cannot see the grounds for,
and a human reviewer cannot approve or reject what they were never shown.
Whichever way this trade is eventually resolved constrains how those two
components get designed, not only how this milestone's one env var happens
to default.

**M3 update: that reviewer UI now exists, and the prediction above is half
right in a way worth recording rather than claiming as a hit.** `reviewer-ui`
does show the human what the model said — the refusal page renders the
`narrative` verbatim — but it reads it from **payments-api's ledger**, not from
telemetry: `settlement.py`'s refused branch POSTs the model's own
`result.output` to `/payments/{id}/refuse`, the ledger stores it, and
`apps/reviewer-ui/ledger.py` fetches it back over plain HTTP. So the reviewer's
view of "what the agent said" is entirely independent of
`NORTHSTAR_CAPTURE_CONTENT`: flipping capture OFF would strip
`gen_ai.output.messages`' content and leave the reviewer page unchanged. That
is not a refutation of the paragraph above — the reviewer still cannot see
*what the agent was told*, only what it concluded, and "why €6,200?" is exactly
as unanswerable to the human as it is to the trace at OFF. But the coupling the
paragraph predicted did not materialise as a coupling: M3 routed the one piece
of model prose a human needs through the ledger, where the honesty rule already
governs it, instead of through spans. Recorded because the prediction is the
kind that quietly gets read as confirmed.

### Ruling 5: retired (M2.1 Task 6), not applied a second milestone running

M1.2's final review recorded, as observation O-1, that Ruling 5 — settling on
one asyncio test style rather than mixing explicit `@pytest.mark.asyncio`
markers with `pytest.ini`'s `asyncio_mode = auto` inside the same service —
was decided and never carried out. M2's own design spec broadened that same
open item to the repo-wide shape it actually has today: two genuinely
different asyncio test styles coexist, not just mixed markers within one
suite. Measured directly (`grep -rln "async def test_\|pytest.mark.asyncio"`
vs. a suite-by-suite check for the absence of `async def test_` alongside
the presence of `anyio.run`/`asyncio.run`):

- **`async def test_*` under `asyncio_mode = auto`** — `agents/claims-agent/tests/test_delegation.py`,
  `agents/settlement-agent/tests/test_executor_reply.py`,
  `agents/settlement-agent/tests/test_mcp_tools.py` (no explicit markers,
  relying on `auto` alone — the exact O-1 shape), and
  `agents/settlement-agent/tests/test_settlement.py` (explicit markers,
  also the O-1 shape).
- **Synchronous tests wrapping `anyio.run()`/`asyncio.run()`** —
  `tests/e2e/test_payments_mcp_trace.py` and every test in
  `tools/payments-mcp/tests/`. Neither directory defines any `async def
  test_*` function and neither carries a `pytest.ini`/`pyproject.toml`
  `asyncio_mode` setting — `pytest-asyncio` is not in the execution path for
  either at all.

**Retired rather than applied**, for the reason the M1.2 Task 7 review
already established and this task did not need to re-derive: converting the
synchronous-wrapper suites to `async def test_*` would buy nothing but
cosmetic consistency, while the wrapper style already has zero exposure to a
`pytest-asyncio` version bump — Task 7's own review confirmed pytest-asyncio
is not on `tests/requirements.txt`'s or `tools/payments-mcp/requirements.txt`'s
dependency list at all, so bumping `pytest-asyncio` anywhere else in this
repo "carries zero risk for these suites regardless" (verbatim from that
review). That is a genuine argument in the mixed styles' favour, not a
defect papered over: the two styles exist along exactly the boundary between
"a service with its own `pytest-asyncio` plugin already in its dependency
closure" and "a suite with no such plugin, driving its async calls by hand."
Converting either direction crosses that boundary for no functional gain and
real risk of breaking a suite that currently has none of pytest-asyncio's
own version-compatibility surface to worry about. A decision recorded twice
and applied never is worse than a decision withdrawn once, cleanly, with the
reasoning attached — this is that withdrawal. No further action is expected
of a future milestone on this point.
