"use client";

import { context, propagation, trace } from "@opentelemetry/api";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { Resource } from "@opentelemetry/resources";
import { BatchSpanProcessor, WebTracerProvider } from "@opentelemetry/sdk-trace-web";
import { ATTR_SERVICE_NAME } from "@opentelemetry/semantic-conventions";
import { ZoneContextManager } from "@opentelemetry/context-zone";

let initialised = false;

/**
 * Installs a browser TracerProvider.
 *
 * The collector must allow this origin and the traceparent/baggage headers, or
 * spans are silently dropped and the trace loses its root. See the cors block
 * in deploy/observability/collector-config.yaml and the CORSMiddleware in
 * agents/claims-agent/main.py.
 */
export function initTelemetry(): void {
  if (initialised) return;
  initialised = true;

  const collectorUrl =
    process.env.NEXT_PUBLIC_OTLP_ENDPOINT ?? "http://localhost:4318";

  const provider = new WebTracerProvider({
    resource: new Resource({ [ATTR_SERVICE_NAME]: "customer-ui" }),
    spanProcessors: [
      new BatchSpanProcessor(
        new OTLPTraceExporter({ url: `${collectorUrl}/v1/traces` }),
      ),
    ],
  });

  provider.register({ contextManager: new ZoneContextManager() });
}

export function getTracer() {
  return trace.getTracer("customer-ui");
}

/** Injects W3C traceparent and baggage into an outgoing header bag. */
export function injectContext(headers: Record<string, string>): Record<string, string> {
  propagation.inject(context.active(), headers);
  return headers;
}
