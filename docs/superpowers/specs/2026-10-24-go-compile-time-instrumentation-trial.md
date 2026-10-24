# Trial: compile-time instrumentation for payments-api

Date: 2026-10-24. Branch `worktree-agent-a457578ae3362eb45`, from
`m13-claims-intake` at `d06a379`. Local only: no cluster, no deploy scripts.

Question: can a Go compile-time instrumentation tool replace payments-api's
hand-written OpenTelemetry code and produce the same telemetry?

**Recommendation: don't adopt it now.** The tool reproduces the HTTP server
spans almost exactly, but the two domain attributes still need code. On top of
that, it adds four regressions that each need extra config to fix. The net
saving is about 80 lines of Go, and about 15 lines of config and a build tool
come back. Revisit after the KubeCon demo if upstream gets a per-route filter
and a flush that the process waits for (see "What would change the answer").
If it is adopted later, it is **adopt partially**: about 22 lines of Go
telemetry code stay.

## What payments-api emits today

From `systems/payments-api` at `d06a379`, confirmed by the capture below.

| Signal | What |
|---|---|
| Spans | One SERVER span per request on 7 routes, from `otelhttp v0.71.0` wrapping each route. The span name is the route template (`POST /payments/propose`, `POST /payments/{paymentId}/execute`, `.../refuse`, `.../reject`, `GET /payments/{paymentId}`, `GET /payments`). `GET /healthz` has no span, on purpose (handlers.go: probe noise was 79% of traces). |
| HTTP attributes | `http.request.method`, `http.response.status_code`, `http.route`, `url.path`, `url.scheme`, `server.address`, `server.port`, `client.address`, `network.peer.address`, `network.peer.port`, `network.protocol.version`, `user_agent.original`, `http.request.body.size`, `http.response.body.size`. Stable HTTP semconv spelling. |
| Status | 4xx leaves the server span `Unset`; 5xx sets `Error`. |
| `northstar.claim.id` | Copied from W3C baggage onto the server span by the `withClaimID` middleware (every route except `GET /payments` and `/healthz`). |
| `northstar.payment.refusal_cause` | Set on the execute span by `refuse()` for the three 409 causes (`amount_mismatch`, `already_executed`, `rejected`). Absent otherwise. |
| Resource | `service.name` plus `telemetry.sdk.*`. |
| Propagation | W3C `tracecontext` + `baggage`. |
| Metrics, logs | None. No MeterProvider (telemetry-contract.md, known limitation 2). Logs are `log.Printf` to stdout. |

Contract: the Weaver registry (`telemetry/registry/claim.yaml`) defines
`northstar.claim.id` and `northstar.payment.refusal_cause`. The refusal cause
is described as payments-api's own attribute. `tests/e2e/test_evidence_view.py`
reads the claim id off the `GET /payments/{paymentId}` and `reject` hops.
`tests/e2e/test_payments_api_trace.py` asserts the execute span's name, its
SERVER kind, that its parent is the injected span, `http.request.method` and
`http.response.status_code`.

Code that exists only for telemetry: **about 110 lines of Go** and **5 direct
plus 18 indirect `go.mod` requires**.

- `telemetry.go`: 62 lines (SDK, exporter, resource, propagators).
- `main.go`: 14 lines (init and deferred shutdown).
- `handlers.go`: about 34 lines (otelhttp import and the 3 otel API imports,
  the per-route `handle`/`handleNoClaimID` wrappers, `withClaimID`, and the
  `SetAttributes` call in `refuse`).

## Tools surveyed

Release status checked upstream with `gh release list` on 2026-10-24.

| Tool | Home, licence | Latest release | Mechanism | Fit for payments-api |
|---|---|---|---|---|
| **otelc**, `open-telemetry/opentelemetry-go-compile-instrumentation` | OpenTelemetry SIG (CNCF), Apache-2.0 | **v1.2.0**, 2026-10-12. v1.0.0 (2026-07-14) was retracted the same day for an `otelc pin` bug; v1.0.1 followed. v1.1.0 (2026-08-24) is the newest release older than two weeks. | `-toolexec` wrapper. Injects hooks into `net/http` `serverHandler.ServeHTTP` and `Transport.RoundTrip`, plus an SDK bootstrap configured by `OTEL_*` env. Also instruments gRPC, database/sql, gin, redis, mongo, kafka, openai/anthropic, log correlation and more. | **Tried.** The upstream, vendor-neutral successor that both donations feed into. |
| **loongsuite-go** (was `opentelemetry-go-auto-instrumentation`), `alibaba/loongsuite-go` | Alibaba, Apache-2.0 | v1.15.0, 2026-09-28 | Same approach (`otel go build`), with a wider library list. It is the code base donated to the SIG and lives on as Alibaba's distribution. | Not tried. otelc is the neutral home of the same design. |
| **Orchestrion**, `DataDog/orchestrion` | Datadog, Apache-2.0 | v1.14.0, 2026-10-13 | `-toolexec` engine. The engine itself is vendor-neutral, but `orchestrion pin` enables dd-trace-go integrations. | Not vendor-neutral by default: spans go through dd-trace-go, and OTLP export from Datadog SDKs is in Preview. Under Orchestrion, `trace.SpanFromContext` returned a no-op span (orchestrion#936); the fix is targeted at dd-trace-go v2.13.0. That would break both domain attributes. |
| **OBI**, `open-telemetry/opentelemetry-ebpf-instrumentation` (ex-Beyla) | OpenTelemetry, Apache-2.0 | v0.15.2, 2026-10-16 (v1.0.0-rc.2 is a pre-release) | eBPF, outside the process, needs `--privileged`, no rebuild. | Gives HTTP spans with no build change. It cannot set app-defined attributes such as the claim id or the refusal cause, so the code for those would need its own SDK. Not tried. |
| **go-instrumentation** (`go.opentelemetry.io/auto`) | OpenTelemetry, Apache-2.0 | v0.24.0, 2026-04-27 | eBPF; README says "work in progress". | No release in six months; OBI is where the eBPF work happens now. Not tried. |

## The trial

Setup: otelc v1.2.0 (release binary `otelc-darwin-arm64`, and
`go install go.opentelemetry.io/otelc/tool/cmd/otelc@v1.2.0` in Docker), Go
1.27.1, collector `otel/opentelemetry-collector-contrib:0.162.0` with an OTLP
HTTP receiver and `file` exporters. The same 17 requests went to each build,
each with its own `traceparent`, and 13 of them carried
`baggage: northstar.claim.id=...`. The requests: propose, execute (200),
execute again (409 already_executed), get, get missing (404), propose and
execute with the wrong amount (409 amount_mismatch), propose, refuse, list
awaiting, reject, execute rejected (409 rejected), malformed propose (400),
propose without baggage, and `GET /healthz` three times.

Builds compared:

- **today**: the code at `d06a379`.
- **otelc A (zero code)**: `telemetry.go`, the SDK init, the otelhttp
  wrappers, `withClaimID` and the refusal `SetAttributes` all removed.
- **otelc B (partial)**: like A, but `withClaimID` and the refusal
  `SetAttributes` are kept. They use the OTel API against the span that otelc
  puts in `r.Context()`.

### Span diff (today vs otelc B)

| | today | otelc B |
|---|---|---|
| Span count for the 17 requests | 14 | 18 (4 extra `GET /healthz`) |
| Names | route templates | **identical** (otelc renames the span from `r.Pattern` after the mux matches) |
| Kind | SERVER | **identical** |
| Parent = injected `traceparent` span | 14/14 | **14/14** |
| Status on 400/404/409 | Unset | **identical**; 5xx maps to Error in both (source-read, not exercised) |
| `http.route`, method, status code, `url.*`, `server.*`, `client.address`, `network.*`, `user_agent.original` | present | **identical values** |
| `http.request.body.size`, `http.response.body.size` | present | **missing** (otelc's server hook passes 0) |
| `url.query` | absent | added on `GET /payments?status=...` |
| `northstar.claim.id` | 13 spans | **13 spans, same values** (otelc's propagator extracts baggage; the kept middleware copies it) |
| `northstar.payment.refusal_cause` | 3 spans | **3 spans, same values** |
| Scope | `otelhttp 0.71.0` | `go.opentelemetry.io/otelc/instrumentation/net/http`, version `dev` |
| Resource | `service.name`, `telemetry.sdk.*` | plus 12: `host.name`, `os.*`, `process.*` (including `process.command_args`), `service.instance.id` |
| Metrics | none | defaults export 7 Go runtime metrics (`go.memory.*`, `go.goroutine.count`, and others) over OTLP; **no HTTP server metrics** |
| Logs | none | none (the logger provider is set up and exports nothing) |

otelc A has the same diff, plus no `northstar.claim.id` and no
`northstar.payment.refusal_cause` anywhere, so it fails the registry and the
evidence view. Zero code does not meet the contract.

### Regressions found

1. **Spans lost on SIGTERM.** The injected SDK flushes in its own signal
   goroutine and does not block. `main` returns once `srv.Shutdown` finishes,
   so the process exits before the flush ends. Exiting right after the
   requests exported 0 of 18 spans in 3 of 3 runs, locally and with
   `docker stop`. Today's build exported 14 of 14 every time. An extra
   `ForceFlush` in `main` did not fix it (18 spans in 1 run of 3), because the
   SDK's concurrent `Shutdown` makes `ForceFlush` a no-op. What does work is
   `OTEL_GO_SIMPLE_SPAN_PROCESSOR=true`: 18 of 18 in every run. It adds about
   0.3 ms to p50 against a local collector (0.26 ms to 0.59 ms, 300 requests).
   This hits the demo on every rollout.
2. **`GET /healthz` is traced.** The hook sits on `serverHandler.ServeHTTP`,
   so every request gets a span. There is no per-route opt-out ("filter
   support" is listed as planned), and `OTEL_GO_DISABLED_INSTRUMENTATIONS`
   only turns off the whole of `nethttp`. This undoes the deliberate
   no-probe-spans design. Fixes, each outside Go: a collector `filter`
   processor that drops payments-api's `GET /healthz` spans, or a
   `tcpSocket` readiness probe. The probe change also touches the kubelet row
   in `deploy/platform/reach/linkerd-policies.yaml` and
   `tests/unit/test_reach_policy.py`.
3. **Metrics and logs export by default.** Runtime metrics would appear in
   Prometheus as new names. To match today, set `OTEL_METRICS_EXPORTER=none`
   and `OTEL_LOGS_EXPORTER=none`.
4. **Dependencies leave `go.mod`.** The binary embeds 58 modules (today: 24),
   resolved by otelc's bundle rather than by this repo's `go.sum`. They
   include `google.golang.org/grpc v1.85.0-dev.0.20260825072537-93e31b48545e`,
   an unreleased gRPC pseudo-version, which breaks the repo's
   pin-released-versions rule. During each build otelc also prints "Bumped go
   version (1.25.0 -> 1.26.0)", and it leaves a `.otelc-build/` directory that
   needs a `.gitignore` entry.

Smaller points: the app can no longer install its own TracerProvider (otelc
hooks `otel.SetTracerProvider` and skips every call after its own). The 4
unit tests that read the refusal cause off a recorded span fail until the test
server wraps requests in a span, because `httptest` bypasses
`serverHandler`. With a 14-line `withServerSpan` helper in `handlers_test.go`
the suite passes.

## What adoption would look like (otelc B)

Go (whitespace-insensitive line counts):

| File | Removed | Added |
|---|---|---|
| `telemetry.go` | 62 | 0 |
| `main.go` | 14 | 0 |
| `handlers.go` | 27 | 10 |
| `handlers_test.go` | 1 | 14 |
| **Go total** | **104** | **24** (net −80) |
| `go.mod` / `go.sum` | 13 / 30 | 0 |

Kept in Go: `withClaimID` (13 lines with its comment), the 3-line
`SetAttributes` in `refuse`, the 3 otel API imports and a 3-line `handle`
helper, about 22 lines in all. `go.opentelemetry.io/otel/sdk` stays in
`go.mod` only for the tests.

Dockerfile:

```dockerfile
FROM golang:1.27.1-alpine AS build
WORKDIR /src
RUN go install go.opentelemetry.io/otelc/tool/cmd/otelc@v1.2.0
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 otelc go build -o /out/payments-api .
```

That is one new line and one changed line. On this machine: a no-cache image
build took 7.7 s before and 19.4 s after; the image grew from 24.7 MB to
27.2 MB.

Config that comes back: 3 env vars in `deploy/k8s/payments-api.yaml`
(`OTEL_GO_SIMPLE_SPAN_PROCESSOR=true`, `OTEL_METRICS_EXPORTER=none`,
`OTEL_LOGS_EXPORTER=none`, 6 lines), and about 6 to 8 lines for the healthz
fix (a collector filter, or the probe and reach-policy change). Docs to update:
the payments-api row in `docs/telemetry-contract.md` (instrumentation library,
attribute list, scope) and the comments in `handlers.go` about otelhttp and
`/healthz`.

Net: about 80 fewer lines of Go and 43 fewer `go.mod`/`go.sum` lines. In
return: about 15 lines of YAML across two or three files, a build-time tool,
and 34 more modules in the binary that the repo does not pin.

## Why not now

- The code the demo talks about (`northstar.claim.id` from baggage, the
  refusal cause) stays. The tool replaces only the boilerplate: SDK setup and
  route wrapping.
- Two of the regressions hit properties this repo measures: no lost spans,
  and no probe noise. Each fix is config that someone has to know is there.
- v1.2.0 is 12 days old. The committed-config workflow (`otelc pin`) is still
  marked under development upstream (#585).

## What would change the answer

- An otelc release where the process waits for the shutdown flush (or a
  documented hook the app can wait on). Worth reporting upstream with the
  reproduction above.
- A per-route or per-path server filter.
- A bundle that resolves to released dependency versions only.

With those three, adoption is the partial form above, with only the env vars
for metrics and logs left as config.
