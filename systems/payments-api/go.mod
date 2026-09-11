module github.com/kaspernissen/northstar/systems/payments-api

// 2026-09-11 (M3): was `go 1.24.0`. `go.opentelemetry.io/otel` v1.42.0 and
// later declare `go 1.25.0` -- v1.41.0's CHANGELOG says "This release is the
// last to support Go 1.24", v1.42.0's Removed section says "Drop support for
// Go 1.24. (#7984)" -- and v1.42.0 is the first release that stops discarding
// a W3C Trace Context Level 2 traceparent (docs/upstream-findings.md Finding
// 11). The `go` line is the minimum language version otel actually requires;
// the `toolchain` line pins the build to an exact Go release rather than
// letting it float, matching this repo's rule for its Python pins.
go 1.25.0

toolchain go1.27.1

require (
	go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp v0.71.0
	go.opentelemetry.io/otel v1.46.0
	go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp v1.46.0
	go.opentelemetry.io/otel/sdk v1.46.0
	go.opentelemetry.io/otel/trace v1.46.0
)

require (
	github.com/cenkalti/backoff/v5 v5.0.3 // indirect
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/felixge/httpsnoop v1.1.0 // indirect
	github.com/go-logr/logr v1.4.4 // indirect
	github.com/go-logr/stdr v1.2.2 // indirect
	github.com/google/uuid v1.6.0 // indirect
	github.com/grpc-ecosystem/grpc-gateway/v2 v2.30.0 // indirect
	go.opentelemetry.io/auto/sdk v1.2.1 // indirect
	go.opentelemetry.io/otel/exporters/otlp/otlptrace v1.46.0 // indirect
	go.opentelemetry.io/otel/metric v1.46.0 // indirect
	go.opentelemetry.io/proto/otlp v1.11.0 // indirect
	golang.org/x/net v0.58.0 // indirect
	golang.org/x/sys v0.47.0 // indirect
	golang.org/x/text v0.41.0 // indirect
	google.golang.org/genproto/googleapis/api v0.0.0-20260819154853-08b0e4226688 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260819154853-08b0e4226688 // indirect
	google.golang.org/grpc v1.83.1 // indirect
	google.golang.org/protobuf v1.36.12 // indirect
)
