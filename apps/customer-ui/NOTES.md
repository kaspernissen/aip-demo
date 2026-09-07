# customer-ui — verified SDK surfaces and emitted telemetry

Evidence for an upstream-contribution workstream (A8). Every gap between what the
pinned browser OTel SDK actually emits and the OTel semantic conventions define is
recorded here, verbatim, rather than silently patched with an invented attribute
name. This is the top of the whole M1.1 chain: the browser owns the trace root, and
this task is the first point in the milestone where the complete
browser→claims-agent→settlement-agent hop can be observed end to end.

## 1. Step 1 finding: `@a2a-js/sdk`'s browser-safety, and why it was not used

`npm view @a2a-js/sdk versions --json` shows the current line is `1.1.0` (0.3.x is
the deprecated line matching claims-agent's own `a2a-sdk` 0.3.7 protocol
generation). Inspecting the published tarball (`npm pack @a2a-js/sdk@1.1.0`):

- No `"browser"` field in `package.json`.
- `dist/client/index.js` (the `./client` export) takes an injected `fetchImpl`
  parameter rather than hard-coding any Node built-in — `grep -rlE` over
  `dist/client` for `require(['"](node:)?(fs|http|https|net|tls|child_process|dns)`
  found nothing. Static analysis suggests the client transport is browser-safe.
- **But** the package declares `A2A_PROTOCOL_VERSION = "1.0"` with `"0.3"` only as
  a `compat/v0_3` legacy path, while claims-agent's committed agent card (Task 5)
  advertises `protocolVersion: "0.3.0"` — the exact version this SDK treats as
  legacy. Using it would mean reconciling a real wire-protocol version skew for a
  conference-demo UI, not a browser-safety problem.

**Decision (per the brief's own Step 1 fallback clause):** keep the small
fetch-based JSON-RPC client (`a2a.ts`), matching what Tasks 1–5 already proved
interoperates with the deployed claims-agent. Not used, so no further protocol-skew
investigation was done — recorded here only so a future task knows this was
checked, not overlooked.

## 2. Plan defect (A19): the brief's Step 6 dynamic-import loses the active span
   across the fetch call — traceparent injection silently produces nothing

**What the brief said:** Step 6 has `sendClaim` read `injectContext` via
`const { injectContext } = await import("./telemetry");` as the very first thing
inside the function, immediately before constructing the `fetch` call, with the
comment "The dynamic import keeps `a2a.test.ts` runnable outside a browser."

**What is actually true, found by capture against the real deployed pod (Chromium,
via Playwright, console-instrumented):** with that exact code, `getTracer()
.startActiveSpan("claim.submit", async (span) => { ... })`'s callback correctly saw
the active span (`trace.getSpan(context.active())` returned a real span context)
right up to the point it called `sendClaim(...)`. Inside `sendClaim`, immediately
after `await import("./telemetry")` resolved, `trace.getSpan(context.active())` was
`undefined` — the active context was gone. Consequence: `propagation.inject`
(inside `injectContext`) had nothing to inject, and the actual POST request that
reached claims-agent carried **no `traceparent` header at all** (confirmed via
Playwright's `page.on("request")` capture: `headers.get("traceparent")` was
empty). The browser's `claim.submit` span and the whole backend chain ended up in
two separate traces — silently, with zero console errors and a fully successful
claim submission (the UI has no way to detect this).

**Root cause:** `@opentelemetry/context-zone`'s `ZoneContextManager` relies on
`zone.js` monkey-patching the global `Promise` constructor so that async
continuations resume "inside" whatever Zone was active when they were scheduled.
Native `async`/`await`, however, resumes via the JS engine's internal realm
intrinsic `%Promise%` — captured once at realm creation — not the mutable global
`Promise` binding `zone.js` overwrites. Crossing *any* `await` (even one that
resolves near-instantly, like a dynamic `import()` of an already-loaded ES module)
therefore loses Zone-tracked context. This is a documented, long-standing zone.js
limitation with native async/await (historically why Angular recommended
transpiling down for zone.js compatibility); it is not specific to dynamic import,
`fetch`, or this codebase, but the brief's Step 6 code triggers it on literally
every claim submission.

**Verified this is really about crossing the await, not about calling into another
module**: instrumented with `console.log` calls at (a) right before `sendClaim(...)`
is called (inside `page.tsx`, still fully synchronous — span present), (b) as the
first synchronous statement inside `sendClaim`, and (c) inside `injectContext`
after the `await import(...)` had resolved. (a) and an early version of (b) (before
any await had been added inside `sendClaim`) both showed the span; the moment any
`await` (even `await import("@opentelemetry/api")`, added only for this
diagnostic) preceded the check, the span was gone. Confirmed both in the real
deployed browser and, independently, under `vitest` (Node) using the exact same
`ZoneContextManager` — see the regression test below; the loss reproduces in both
environments, so it is fundamental to Zone.js + native async/await, not a
browser-only quirk.

**Fixed:** `a2a.ts` imports `injectContext` **statically**
(`import { injectContext } from "./telemetry";`) instead of dynamically. This means
`injectContext(headers)` — and the `context.active()` read inside it — runs
synchronously as part of constructing the `fetch(...)` call's `init` object,
*before* `sendClaim`'s own first `await` (`await fetch(...)`). No context ever
needs to survive an await for the injection to work.

**Regression test (A19):** `apps/customer-ui/src/a2a.test.ts`,
`describe("sendClaim") > "injects the active span's traceparent into the outbound
request, surviving sendClaim's own await boundary (A19)"`. It calls the *real*
`initTelemetry()` (the actual `ZoneContextManager` + `WebTracerProvider` used in
production, not a stand-in), wraps a `sendClaim` call in a real
`getTracer().startActiveSpan(...)`, and asserts the mocked `fetch` call's actual
`traceparent` header matches the W3C format and carries the active span's own
trace ID — substance, not merely the absence of an error. **Verified to fail
against the brief's original code**: reverting `sendClaim` to the dynamic-import
form (`const { injectContext } = await import("./telemetry");`) and rerunning
`npx vitest run` reproduces exactly this failure:
```
AssertionError: no traceparent header was sent at all: expected undefined to be truthy
```
The file was restored to its fixed form immediately afterward
(`npx vitest run` → 6 passed); `git diff` showed no residual change to the
committed source. Also verified end to end against the real deployed pod, both
before the fix (traceparent empty on the actual wire request, captured via
`page.on("request")`) and after (traceparent present, format-valid, and its trace
ID matches the trace Jaeger later shows).

## 3. What the browser actually emits — captured from the real deployed pod

One real (unweakened, per A17) trace, captured directly from Jaeger
(`http://localhost:16686/api/traces/<id>`) after a genuine Playwright-driven claim
submission against the deployed pod:

**The one span `customer-ui` emits, verbatim:**

```json
{
  "operation": "claim.submit",
  "service": "customer-ui",
  "kind": "internal",
  "parent_id": null,
  "attrs": {
    "internal.span.format": "otlp",
    "northstar.claim.id": "CLM-TEST-3f9b3b96",
    "northstar.policy.number": "POL-77120",
    "otel.scope.name": "customer-ui",
    "span.kind": "internal"
  }
}
```

**Resource attributes** (Jaeger `processes[].tags`, i.e. what
`new Resource({ [ATTR_SERVICE_NAME]: "customer-ui" })` plus the SDK's own
defaults produced):

```json
[
  { "key": "telemetry.sdk.language", "value": "webjs" },
  { "key": "telemetry.sdk.name", "value": "opentelemetry" },
  { "key": "telemetry.sdk.version", "value": "1.30.0" }
]
```
(plus the implicit `service.name = customer-ui` used as the process key itself).

### Comparison against OTel conventions — gaps, not filled

| Convention / expectation | Emitted? | Verdict |
|---|---|---|
| A `CLIENT`-kind HTTP span for the outbound `fetch` to claims-agent (`http.request.method`, `server.address`, etc., per the HTTP client semconv) | **Absent** | **GAP** — `@opentelemetry/instrumentation-fetch` was never added (not in the brief's Step 1 pin list); only the hand-written `claim.submit` INTERNAL span exists. The trace shows the browser→claims-agent hop only via the parent/child link on claims-agent's own server span, not via any client-side span of our own. |
| Browser resource attributes (`browser.brand`, `browser.platform`, `browser.mobile`, per the browser resource semconv) | **Absent** | **GAP** — only the manual `Resource({ service.name })` was supplied; no browser resource detector (e.g. `@opentelemetry/resource-detector-browser` or similar) was in the brief's pin list, so nothing besides `telemetry.sdk.*` and `service.name` is present. |
| `user_agent.original` on any customer-ui span | Absent | **GAP** — same root cause as the row above: no HTTP client instrumentation, so nothing captures it browser-side (claims-agent's own server span does carry `user_agent.original`, but that is claims-agent's instrumentation, not customer-ui's). |
| Meaningful custom span/attribute naming (`claim.submit`, `northstar.claim.id`, `northstar.policy.number`) | Present | Not a semconv gap — these are intentionally domain-specific, not conventions this project claims to follow; recorded for completeness only. |

None of these gaps were filled with an invented attribute. They are the direct,
predictable consequence of the brief's minimal SDK pin list (no fetch
instrumentation, no browser resource detector) — a legitimate, in-scope choice for
a five-file demo UI, not a defect, but worth recording precisely per A8 since it
narrows what an upstream-facing improvement would need to add.

## 4. Does the browser's telemetry actually reach the collector and join the chain?
   Hop by hop, plainly

- **Browser → OTel Collector (OTLP/HTTP, port 4318):** **Works.** The kind
  cluster's `extraPortMappings` (`deploy/kind/stage1.yaml`) map host `4318` and
  `8080` directly into the cluster's NodePorts — no port-forward needed for
  either. The collector's `otlp.protocols.http.cors.allowed_origins` (Task 1,
  `deploy/observability/collector-config.yaml`) already lists
  `http://localhost:8080`, so the browser's own OTLP POST to
  `http://localhost:4318/v1/traces` succeeds with no CORS rejection. Confirmed
  directly: `page.on("request")` captured a real `POST http://localhost:4318/v1/traces`
  once the `BatchSpanProcessor`'s 5s export timer fired.
- **Browser → claims-agent (CORS + `traceparent`):** **Works**, once §2's fix
  landed. claims-agent's `CORSMiddleware` (Task 5, `agents/claims-agent/main.py`)
  already allowed `traceparent`/`tracestate`/`baggage` in `allow_headers` and
  `http://localhost:8080` in `allow_origins` — that groundwork was already correct
  and needed no change here. Confirmed zero CORS console errors in every real
  capture, and (post-fix) a genuine `traceparent` header on the wire.
- **claims-agent → settlement-agent:** **Works** — unchanged from Task 5's own
  finding (`HTTPXClientInstrumentor`'s ambient-context injection); reconfirmed here
  now that a real browser, not a raw HTTP test client, originates the call.
- **settlement-agent → payments-mcp → payments-api:** **Still does not
  propagate.** Same confirmed gap as every earlier task in this milestone
  (settlement-agent's MCP client does not inject `params._meta`; FastMCP only ever
  extracts trace context from there, never from an HTTP header). This is Task 7's
  job (shared `correlation.py`, A6/A18) and was **not** touched here. Per this
  task's own instructions, the corresponding assertion
  (`test_all_five_services_are_in_one_trace`) is `xfail(strict=True)`, mirroring
  `tests/e2e/test_claims_chain_trace.py`'s existing pair of xfails for the exact
  same gap.

**Net result: the complete chain now forms ONE trace with ONE root and NO
orphans, for the reachable four-fifths of it** (customer-ui, claims-agent,
settlement-agent — payments-mcp/payments-api remain a separate, detached trace of
their own, unchanged from before). Verified directly against a real captured trace
(`trace_id` redacted per-run, reproducible on demand): `roots() == [customer-ui:
claim.submit]`, `orphans() == []`, `services() == {customer-ui, claims-agent,
settlement-agent}`. This is the M1.1 claim, stated plainly: sound and complete for
the A2A hops; the MCP hop remains the one deliberate, already-known gap.

## 5. HTTP semconv opt-in — not applicable here

customer-ui makes zero instrumented HTTP client spans of its own (§3), so A13's
"capture the HTTP attributes each Python service actually emits, opt in if
legacy" doesn't apply to this task — there is nothing to opt into. Recorded here
so a future reader doesn't wonder why no `OTEL_SEMCONV_STABILITY_OPT_IN` appears
anywhere in this app's build args or manifest.

## 6. `next@15.1.3` (the brief's exact pin) carries a known CVE

`npm install` warns: `next@15.1.3: This version has a security vulnerability...
See https://nextjs.org/blog/CVE-2025-66478`. `npm audit` additionally reports a
broad range of moderate/high/critical advisories spanning nearly the entire
`next` version history (`9.3.4-canary.0` through `16.3.0-preview.10`), which
reads as a low-precision advisory rather than a version-specific one. This is a
conference-demo app with no auth and no untrusted server-side rendering (static
`output: "export"`, served by plain nginx) — not a production deployment. Recorded
here rather than silently deviating from the brief's pinned version; not
upgraded, since the brief's version pin is otherwise exactly what was asked for
and the blast radius here is minimal. Flag for the coordinator if this pin should
be revisited before the demo ships.

## 7. The harness additions this task owns (A20/A20a)

Implemented additively in `tests/harness/jaeger.py` — see the module's own
docstrings for the exact contract:

- `Trace.span(operation, service=None)` / `Trace.spans_named(operation,
  service=None)` — the optional `service` filter disambiguates a same-named span
  across services (e.g. both claims-agent's and settlement-agent's own `"POST /"`
  entry span); ambiguity *within* one service still raises, unchanged.
- `JaegerClient.wait_for_trace_id(..., services=None)` — when `services` is
  given, polls until every named service is represented **and** the trace's total
  span count has then held steady for `QUIET_WINDOW_S` (10s — comfortably longer
  than the 5s `BatchSpanProcessor` default export delay every service in this repo
  uses unmodified). Fails closed on timeout, naming exactly which services never
  showed up. With `services` omitted, behaviour is byte-for-byte what it was
  before this task (verified: every existing e2e test — Tasks 3–5's — still
  passes unchanged, and a dedicated unit test pins the no-`services` case returns
  on the very first poll, matching the pre-A20 implementation).

**Why 10s, not less — proven, not assumed:** `tests/unit/test_jaeger_assertions.py`
uses a fake, fully-deterministic clock (no real sleeping) to simulate exactly the
failure A20a describes: a third service's span landing at t=6s, well inside a
naive 2s or 6s "looks quiet" window but short of a real 10s one. Verified by
*breaking* the implementation on purpose (temporarily setting
`QUIET_WINDOW_S = 2.0`) and confirming both quiet-window unit tests then fail —
one returns as early as t=0 (immediately, no window at all effectively behaves
like the pre-A20 immediate-return path once services are already present) and the
other returns at t=8 instead of waiting to t=16 — restoring `QUIET_WINDOW_S = 10.0`
makes both pass again. This is the same timing-simulation technique Task 5's own
follow-up review used to catch its original 2s/6s window; used here to justify
the harness's constant directly rather than tuning by trial and error against a
live cluster.

## 8. Port-8080 stray port-forward (A9) — found and fixed before this task started

Before any customer-ui work, `lsof -nP -iTCP:8080 -sTCP:LISTEN` showed a
`kubectl port-forward -n observability svc/perses 8080:8080` (PID confirmed via
`kubectl config get-contexts` and `kubectl --context kind-capybara -n observability
get svc` to belong to the unrelated `capybara` cluster, not `northstar`) already
bound to host port 8080 — the exact port kind's own `extraPortMappings` needs for
customer-ui's NodePort. Killed only that specific PID (never touched the
`capybara` cluster or the shared `kind-registry`), confirmed
`lsof -nP -iTCP:8080 -sTCP:LISTEN` afterward showed only Docker Desktop's own
proxy for kind's port mapping. Recorded per A9's own instruction to report this
plainly when it happens.
