package main

import (
	"context"
	"fmt"
	"os"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	// 2026-09-11 (M3): was `semconv/v1.39.0`, which matched the SDK this
	// service used to pin. It must track whatever `resource.Default()` was
	// built against, because `resource.Merge` below refuses two different
	// schema URLs. otel/sdk v1.46.0's `resource/builtin.go:16` imports
	// `semconv/v1.43.0` (read in the module cache), so leaving this at 1.39.0
	// made `initTracing` return "conflicting Schema URL:
	// https://opentelemetry.io/schemas/1.43.0 and .../1.39.0" and crash-looped
	// the pod on first rollout. Measured, not predicted: that is the literal
	// log line from payments-api-57cbb84945-x65n4.
	semconv "go.opentelemetry.io/otel/semconv/v1.43.0"
)

// initTracing configures the global TracerProvider from standard OTEL_*
// environment variables. Nothing custom: OTEL_EXPORTER_OTLP_ENDPOINT and
// OTEL_SERVICE_NAME are honoured by the SDK and by the manifests.
func initTracing(ctx context.Context) (func(context.Context) error, error) {
	serviceName := os.Getenv("OTEL_SERVICE_NAME")
	if serviceName == "" {
		serviceName = "payments-api"
	}

	exporter, err := otlptracehttp.New(ctx)
	if err != nil {
		return nil, fmt.Errorf("creating OTLP exporter: %w", err)
	}

	res, err := resource.Merge(
		resource.Default(),
		resource.NewWithAttributes(
			semconv.SchemaURL,
			semconv.ServiceName(serviceName),
		),
	)
	if err != nil {
		return nil, fmt.Errorf("building resource: %w", err)
	}

	provider := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithResource(res),
	)
	otel.SetTracerProvider(provider)

	// W3C trace context and baggage. Baggage matters from Task 7 onward:
	// northstar.claim.id rides in it.
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	))

	return provider.Shutdown, nil
}
