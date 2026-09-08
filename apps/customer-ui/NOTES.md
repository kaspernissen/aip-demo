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

## 2. Plan defect (A19): the brief's Step 6 crosses an await before injecting
   trace context, losing the active span across the fetch call — traceparent
   injection silently produces nothing

**Correction (fix round 1):** an earlier version of this section attributed the
cause to *dynamic import* specifically. That framing was too narrow. A reviewer's
own follow-up experiment proved the cause is crossing **any** `await` before
`injectContext` runs: a *static* import of `injectContext` plus one bare, unrelated
`await Promise.resolve();` inserted in the same spot reproduces the identical
dropped header, with no dynamic import anywhere in the picture. The brief's Step 6
code happened to use a dynamic `import()` as its particular await, but that was
incidental, not causal — this section is corrected below to say so plainly, since
this is A8 upstream-contribution evidence and must not overstate what was shown.

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

**Further confirmed by a reviewer, more cleanly, in fix round 1:** every await used
in the diagnostic above still happened to be an `await import(...)` of some
module. A reviewer went one step further and removed dynamic import from the
experiment entirely — a *static* top-level import of `injectContext` plus a single
bare `await Promise.resolve();` inserted immediately before the fetch call, with no
`import()` anywhere in the function — and reproduced the exact same failure (no
`traceparent` header on the outbound request). This is the more precise proof: the
mechanism is "crosses an await," full stop; dynamic import was never a necessary
ingredient, only what the brief's own Step 6 sketch happened to use.

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

**Fix round 1 note, as of that round (before Task 7):** the capture above
predates the A21 OTel bump (§6) and the `new Resource(...)` call it names no
longer exists — `@opentelemetry/resources` 2.x removed the constructable
class. Re-captured post-bump (see §6a): the same three `telemetry.sdk.*`
attributes are present, now reading `telemetry.sdk.version: "2.8.0"`,
alongside the identical `claim.submit` span shape, 1 root, 0 orphans, and —
**as of fix round 1, before Task 7 added the MCP hop's client-side
propagation** — the same three services in one trace. The gaps table below
(§3, "Comparison against OTel conventions") is unaffected by the bump —
those are pin-list gaps (no fetch instrumentation, no browser resource
detector), not something the OTel version itself changes.
(plus the implicit `service.name = customer-ui` used as the process key itself).

**Superseded by Task 7 (final-review.md finding S2, second round):** "three
services in one trace" was accurate for fix round 1's capture and is not
accurate now. §4 below documents the current, reconfirmed state: one root,
zero orphans, five services in one trace. The span shape and resource
attributes captured above are otherwise still current — only the
service-count claim in this paragraph was stale.

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
- **settlement-agent → payments-mcp → payments-api, as of this task (Task 6):**
  **Still does not propagate.** Same confirmed gap as every earlier task in
  this milestone (settlement-agent's MCP client does not inject
  `params._meta`; FastMCP only ever extracts trace context from there, never
  from an HTTP header). This was Task 7's job (shared `correlation.py`,
  A6/A18) and was **not** touched here. Per this task's own instructions, the
  corresponding assertion (`test_all_five_services_are_in_one_trace`) was
  `xfail(strict=True)`, mirroring `tests/e2e/test_claims_chain_trace.py`'s
  existing pair of xfails for the exact same gap.
  **Superseded by Task 7 (final-review.md finding S2) — flagged here, not
  left to read as current:** Task 7 later made settlement-agent's MCP client
  inject `params._meta` (traceparent + baggage) via the low-level request
  API, and FastMCP's server-side extraction (already proven working) joins
  the trace. `test_all_five_services_are_in_one_trace` has not been
  `xfail(strict=True)` since Task 7 landed — it is a plain passing assertion
  today. See the historical/current split below.

**Historical net result, as of this task (Task 6), before Task 7's fix:** the
complete chain formed ONE trace with ONE root and NO orphans, for the
reachable four-fifths of it (customer-ui, claims-agent, settlement-agent —
payments-mcp/payments-api remained a separate, detached trace of their own).
Verified directly against a real captured trace (`trace_id` redacted per-run,
reproducible on demand): `roots() == [customer-ui: claim.submit]`,
`orphans() == []`, `services() == {customer-ui, claims-agent,
settlement-agent}`. That was the M1.1 claim as it stood after this task: sound
and complete for the A2A hops, with the MCP hop as the one deliberate,
already-known gap.

**Current state (as of Task 7, reconfirmed at the final whole-branch review,
final-review.md): the chain is five services, not three.** Task 7's MCP
client fix (above) closes the gap this section originally described.
`test_all_five_services_are_in_one_trace` now asserts, and passes against, a
real captured trace: `roots() == [customer-ui: claim.submit]`,
`orphans() == []`, `services() == {customer-ui, claims-agent,
settlement-agent, payments-mcp, payments-api}` — one trace, one root, zero
orphans, all five services. **This is the M1.1 claim, stated plainly, as it
holds today.** What does *not* propagate across the MCP hop, still, is the
`northstar.claim.id` *attribute* (FastMCP never reads the `baggage` key out
of `params._meta`) — a distinct, narrower, still-open gap, tracked in
`docs/telemetry-contract.md` (Gap 4) and pinned by exactly one remaining test,
`tests/e2e/test_claim_correlation.py::test_claim_id_is_on_every_mcp_hop_span`
(a manual `pytest.xfail`, not a decorator — see that file's own docstring).
Trace-joining and attribute-correlation are two different claims; the first
is now fully closed, only the second remains open.

## 5. HTTP semconv opt-in — not applicable here

customer-ui makes zero instrumented HTTP client spans of its own (§3), so A13's
"capture the HTTP attributes each Python service actually emits, opt in if
legacy" doesn't apply to this task — there is nothing to opt into. Recorded here
so a future reader doesn't wonder why no `OTEL_SEMCONV_STABILITY_OPT_IN` appears
anywhere in this app's build args or manifest.

## 6. Dependency advisories found — what was bumped (A21) and what was
   deliberately left alone

`npm install` originally warned: `next@15.1.3: This version has a security
vulnerability... See https://nextjs.org/blog/CVE-2025-66478`. Task 6's first pass
recorded this CVE but did not bump the pin, pending the coordinator's ruling.
**A21 is that ruling** ("no component ships on a dependency with a published
CVE") and this section is now complete per that ruling, with every advisory
found — not just `next`'s — recorded below.

**N6 correction (fix round 1):** the first pass characterized `next`'s
`npm audit` output as "a broad range… which reads as a low-precision advisory
rather than a version-specific one." That was imprecise. It is not one low-
precision advisory; `npm audit --json` resolves it to **30 distinct GHSA
advisories**, each with its own specific affected-version range, that happen to
collectively span nearly `next`'s whole history because `next` has shipped a
steady stream of them. `15.5.21` is confirmed (by re-running the same audit
against `next@15.5.21`) to be the lowest release whose remaining `next`-specific
advisory count is zero — every one of the 30 below has an upper bound `<15.5.21`
or earlier.

### Bumped (A21) — direct pins with a published, version-bounded advisory

| Package | Old pin | New pin | Advisory / advisories |
|---|---|---|---|
| `next` | `15.1.3` | `15.5.21` | CVE-2025-66478 (GHSA-4342-x723-ch2f, SSRF via Middleware redirect handling) plus 29 further Next.js GHSA advisories with upper bounds from `<15.1.6` up to `<15.5.21` (cache poisoning, DoS, XSS, SSRF, auth-bypass, and cache-confusion families across the 15.x line) — full list: GHSA-3h52-269p-cp9r, GHSA-67rr-84xm-4c7r, GHSA-g5qg-72qw-gw5v, GHSA-xv57-4mr9-wg8v, GHSA-4342-x723-ch2f, GHSA-qpjv-v59x-3qc4, GHSA-9qr9-h5gf-34mp, GHSA-w37m-7fhw-fmv9, GHSA-mwv6-3258-q52c, GHSA-9g9p-9gw9-jx7f, GHSA-h25m-26qc-wcjf, GHSA-f82v-jwr5-mffw, GHSA-ggv3-7p47-pfv8, GHSA-3x4c-7xq6-9pq8, GHSA-q4gf-8mx6-v5v3, GHSA-8h8q-6873-q5fj, GHSA-3g8h-86w9-wvmq, GHSA-ffhc-5mcf-pf4q, GHSA-vfv6-92ff-j949, GHSA-gx5p-jg67-6x7h, GHSA-mg66-mrh9-m8jx, GHSA-h64f-5h5j-jqjh, GHSA-c4j6-fc7j-m34r, GHSA-wfc6-r584-vfw7, GHSA-36qx-fr4f-26g5, GHSA-m99w-x7hq-7vfj, GHSA-89xv-2m56-2m9x, GHSA-68g3-v927-f742, GHSA-4633-3j49-mh5q, GHSA-4c39-4ccg-62r3, GHSA-p9j2-gv94-2wf4, GHSA-955p-x3mx-jcvp. |
| `@opentelemetry/sdk-trace-web` | `1.30.0` | `2.8.0` | GHSA-8988-4f7v-96qf — "OpenTelemetry Core: Unbounded memory allocation in W3C Baggage propagation," rooted in the `@opentelemetry/core <2.8.0` dependency this package pins in lockstep. |
| `@opentelemetry/exporter-trace-otlp-http` | `0.57.0` | `0.219.0` | Same GHSA-8988-4f7v-96qf, via the same `@opentelemetry/core <2.8.0` transitive dependency. |
| `@opentelemetry/resources` | `1.30.0` | `2.8.0` | Same GHSA-8988-4f7v-96qf. Notable specifically because this is the exact baggage-propagation code path Task 7 leans on hardest (`northstar.claim.id` correlation via baggage), and `injectContext` in `telemetry.ts` already exercises it on every claim submission. |

Confirmed by `npm audit --json` before/after: `next`'s own advisory count and all
three `@opentelemetry/core <2.8.0` advisories are gone post-bump; `npm run build`
and `npx vitest run` both pass on the new pins (see the fix-round report for
verbatim output), and the end-to-end trace was re-captured (§6a) to confirm the
browser's span shape did not change.

**A21 divergence, reported plainly rather than silently complied with:** A21
literally says "bump it, within the same major version, to the lowest release
that clears the advisory." For the three OTel packages, no same-major-version fix
exists. `@opentelemetry/core`'s vulnerable range is `<2.8.0`; its last 1.x release
is `1.30.1`, with no later 1.x patch — the fix landed only in the 2.x line.
`sdk-trace-web` and `resources` pin `core` in lockstep (identical version numbers
every release), so they can only clear the advisory by moving to their own 2.x
lines. `exporter-trace-otlp-http` is a 0.x package, where npm/semver treats every
`0.x.0` bump as major regardless of size, so it has no "same major version" bump
available either — `0.219.0` is a major bump by that definition even though it is
the smallest one that clears the advisory. Proceeded with the major bump anyway,
since A21's overriding stated principle ("no component ships on a dependency with
a published CVE") outranks its own literal "same major version" phrasing when the
two conflict, and because leaving these three unpinned would leave Task 7's own
baggage code path on a vulnerable dependency. Flag for the coordinator if this
reading of A21 is wrong.

**Consequence of the OTel 2.x bump — an API break and a behavior break, both
fixed:** see §3's fix-round note and §6a.

### Deliberately left alone — dev-only or transitive, out of scope per A21

A21: "bump what the advisory names and nothing else." `npm audit` (post-bump)
still reports 8 vulnerabilities, all confirmed dev-only or transitive — none of
these are shipped in the built static export (`out/`) or the nginx runtime image,
only in the `node_modules` used to run `next build` and `vitest` locally:

| Package | Severity | Advisory / advisories | Why untouched |
|---|---|---|---|
| `vitest` | critical | GHSA-9crc-q9x8-hgqq, GHSA-5xrq-8626-4rwp | Dev/test-runner only; not in the shipped image. |
| `esbuild` | moderate | GHSA-67mh-4wv8-2f99 | Transitive, via `vite`/`vitest`'s dev server; not in the shipped image. |
| `vite` | high | GHSA-4w7w-66w2-5vf9, GHSA-v6wh-96g9-6wx3, GHSA-fx2h-pf6j-xcff | `vitest`'s transitive dependency; dev/test only. |
| `vite-node` | moderate | (rolled up via `vite`) | Same as `vite`. |
| `@vitest/mocker` | moderate | (rolled up via `vite`) | Same as `vite`. |
| `postcss` | high | GHSA-qx2v-qp2m-jg93, GHSA-6g55-p6wh-862q, GHSA-fxqj-rqcc-2cmp, GHSA-r28c-9q8g-f849 | `next`'s own transitive build-time dependency (CSS tooling); not a `customer-ui` direct pin, no upstream fix available at `next@15.5.21` without `next` itself moving it. |
| `sharp` | high | GHSA-f88m-g3jw-g9cj (inherited libvips CVEs) | `next`'s own optional transitive dependency (server-side image optimization); irrelevant here since this app is `output: "export"` with no Image Optimization API in use. |

This is a conference-demo app with no auth and no untrusted server-side rendering
(static `output: "export"`, served by plain nginx) — the dev-only tools above never
run in the deployed container, and the two transitive `next` deps (`postcss`,
`sharp`) are exercised only by a build feature (`next/image`'s optimizer) this app
does not use. Recorded here per A21's instruction to note, not silently fix, what
is out of scope. Flag for the coordinator if any of these should be revisited
before the demo ships.

## 6a. Fix round 1: the OTel 2.x bump's own regressions, found and fixed

Bumping `@opentelemetry/resources` across its 2.0 boundary (§6) surfaced two real
breaks in `telemetry.ts`, both found by re-capturing a live trace after the bump
and comparing it against §3's original pre-bump capture — exactly the
verification A21/the fix-round instructions asked for ("re-capture the end-to-end
trace to confirm the browser's span output did not change shape").

**1. API break — the constructable `Resource` class is gone.** 2.x replaced
`new Resource({...})` with a `resourceFromAttributes({...})` factory function;
`Resource` is now only a type, no longer `new`-able. Fixed by switching the import
and call site in `telemetry.ts` accordingly.

**2. Behavior break — the automatic merge with SDK defaults is gone.** This one
was not in any changelog line read up front; it was found empirically. 1.x's
`BasicTracerProvider` silently merged any user-supplied `resource` with
`Resource.default()` (which carries `telemetry.sdk.language`/`name`/`version`) via
a `mergeResourceWithDefaults` config flag that defaulted to `true` (confirmed by
reading `@opentelemetry/sdk-trace-base@1.30.1`'s own
`build/src/BasicTracerProvider.js`). 2.x's `BasicTracerProvider` does no such
merge — it uses exactly the resource it is given (confirmed by reading
`@opentelemetry/sdk-trace-base@2.8.0`'s equivalent source: `this._resource =
mergedConfig.resource ?? defaultResource();`, no merge step at all). Supplying
only `resourceFromAttributes({ service.name })`, as the first post-bump edit did,
therefore silently dropped `telemetry.sdk.*` from every trace: a first sound
re-capture (via `wait_for_trace_id`, not `find_trace` — see below) showed
`customer-ui resource tags: []`, empty, where the pre-bump capture in §3 had all
three `telemetry.sdk.*` attributes.

**Fixed** by merging explicitly:
```ts
resource: defaultResource().merge(
  resourceFromAttributes({ [ATTR_SERVICE_NAME]: "customer-ui" }),
),
```
confirmed against `@opentelemetry/resources@2.8.0`'s own type definitions
(`ResourceImpl.d.ts` exports `defaultResource`/`resourceFromAttributes`;
`Resource.d.ts`'s `Resource` interface has `merge(other: Resource | null):
Resource`) — restoring the old effective behavior explicitly rather than relying
on an implicit default that no longer exists.

**Re-verified sound, not by re-running `find_trace`:** the first check of this
bump used `find_trace(service="customer-ui", ..., lookback="5m")` as a quick
ad-hoc look — the exact unsound pattern A14 warns against, caught mid-investigation
before drawing any conclusion from it (it returned a trace consistent with
*before* the redeploy, which is precisely the staleness risk A14 names). Redone
the sound way used throughout this task: a fresh Playwright session observed the
real wire `traceparent`, and `wait_for_trace_id` looked the trace up by that exact
ID. That capture, post-fix, showed:

- `span_count: 56`, `services: ['claims-agent', 'customer-ui', 'settlement-agent']`
  — unchanged from the pre-bump shape.
- `roots: [('customer-ui', 'claim.submit', <span_id matching the observed
  traceparent>)]` — 1 root, unchanged.
- `orphans: []` — 0 orphans, unchanged.
- `customer-ui resource tags: {'telemetry.sdk.language': 'webjs',
  'telemetry.sdk.name': 'opentelemetry', 'telemetry.sdk.version': '2.8.0'}` —
  restored, now correctly reporting the new SDK version.

Full suite re-run after the fix: `RUN_BROWSER_TESTS=1 pytest tests/e2e/test_browser_root_span.py -q`
→ `5 passed, 1 xfailed` (the pre-existing MCP-hop xfail, untouched — see §4/§7).

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
