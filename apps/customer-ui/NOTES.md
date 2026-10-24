# customer-ui: SDK behaviour and emitted telemetry

What the browser app and its small server rely on, what the browser emits, and
where the browser OpenTelemetry setup falls short of the semantic conventions.
Gaps are recorded as found; no attribute has been invented to hide one.

## 1. `@a2a-js/sdk` is not used

The current `@a2a-js/sdk` line (1.x) implements A2A protocol 1.0 and treats
`0.3` as a legacy compatibility path, while claims-agent's agent card advertises
`protocolVersion: "0.3.0"`. Its client looks browser-safe (it takes an injected
`fetch`), but using it would mean reconciling a protocol version skew. The app
keeps a small fetch-based JSON-RPC client (`src/a2a.ts`).

## 2. Inject trace context before the first `await`

`@opentelemetry/context-zone`'s `ZoneContextManager` relies on `zone.js`
patching the global `Promise`. Native `async`/`await` resumes through the
engine's own `%Promise%`, so crossing any `await` (even a dynamic `import()` of
an already-loaded module, or `await Promise.resolve()`) loses the Zone-tracked
active span. A `traceparent` injected after such an await is silently empty: the
request is sent, the claim succeeds, and the browser span and the backend end up
in separate traces.

So `a2a.ts` imports `injectContext` statically and calls it synchronously while
building the `fetch` init object, before `sendClaim`'s first `await`.
`src/a2a.test.ts` ("injects the active span's traceparent into the outbound
request, surviving sendClaim's own await boundary") uses the real
`initTelemetry()` and asserts the header on the mocked `fetch`.

## 3. What the browser emits

One span of its own, `northstar.claim.submit`, carrying `northstar.claim.id` and
`northstar.claim.policy_number`, and it is the trace root. Resource attributes
are `service.name = customer-ui` plus the SDK's `telemetry.sdk.*`.

Gaps against the conventions, left unfilled:

- No CLIENT-kind HTTP span for the `fetch` to claims-agent
  (`@opentelemetry/instrumentation-fetch` is not installed).
- No browser resource attributes (`browser.brand`, `browser.platform`,
  `browser.mobile`); no browser resource detector is installed.
- No `user_agent.original` on any customer-ui span, for the same reason.

## 4. The browser's telemetry reaches the collector and joins the chain

- Browser to the OTel Collector (OTLP/HTTP, port 4318): works. The kind cluster
  maps host ports 4318 and 8080 to NodePorts, and the collector's CORS
  `allowed_origins` lists `http://localhost:8080`.
- Browser to claims-agent: works. claims-agent's CORS middleware allows the
  `traceparent`, `tracestate` and `baggage` headers.
- Downstream hops propagate (see the agents' notes): the claim is one trace with
  one root (`northstar.claim.submit`), no orphans, and all five services
  (customer-ui, claims-agent, settlement-agent, payments-mcp, payments-api).
  Only the `northstar.claim.id` attribute is missing on the MCP hop.

## 5. HTTP semconv opt-in

customer-ui emits no instrumented HTTP client spans (section 3), so there is no
`OTEL_SEMCONV_STABILITY_OPT_IN` setting to make.

## 6. Dependency pins

Direct pins are held at releases with no published advisory:
`next` 15.5.21 (the lowest 15.x release with no remaining `next` advisory) and
`@opentelemetry/sdk-trace-web` / `@opentelemetry/resources` 2.8.0,
`@opentelemetry/exporter-trace-otlp-http` 0.219.0 (these clear the unbounded
memory allocation in W3C Baggage propagation, GHSA-8988-4f7v-96qf, fixed only in
`@opentelemetry/core` 2.8.0). Dev-only and transitive advisories are left alone.

OpenTelemetry JS 2.x behaves differently from 1.x in two ways that `telemetry.ts`
accounts for:

- `new Resource(...)` is gone; use `resourceFromAttributes(...)`.
- The tracer provider no longer merges a supplied resource with the SDK
  defaults, so `telemetry.sdk.*` would silently disappear. `telemetry.ts` merges
  explicitly: `defaultResource().merge(resourceFromAttributes({ ... }))`.

## 7. Test harness helpers

The e2e harness (`tests/harness/jaeger.py`) provides
`Trace.span(operation, service=None)` and `spans_named(...)`, where `service`
disambiguates same-named spans such as the two `POST /` entry spans, and
`JaegerClient.wait_for_trace_id(..., services=None)`, which waits until every
named service is present and the span count has held steady for
`QUIET_WINDOW_S` (10 s, longer than the 5 s default batch export delay).

## 8. Server, push and token flow

A small Starlette server (`server/`, service name `customer-ui-server`) serves
the static export, hands the browser a push endpoint, receives claims-agent's A2A
push and relays it to the browser:

- `POST /claims/push-config` returns `{url, token}`.
- `POST /a2a/push` receives the push; the token is in
  `X-A2A-Notification-Token` and is checked with a constant-time compare before
  the body is parsed. Unknown token is 401, bad JSON 400, a second task for a
  token 409.
- `GET /claims/events` streams to the browser. The token goes in an
  `x-claim-token` header, never the URL: a `?token=` query is ignored (401), but a
  client that sent one would still have it recorded in the SERVER span's
  `url.query`.
- Uvicorn's access log is off. The browser holds `/claims/events` open instead of
  resubscribing, and falls back to one `tasks/get` if the stream fails.

The relay holds a stream open on an event with no timer, so a waiting claim
costs no spans. `push-config` is unauthenticated and the relay is in memory and
single-replica (1000 calls evict live claims; a restart loses every token).
Memory limits are 64/128 Mi because Python plus OpenTelemetry does not fit in
less.
