"use client";

import { context, propagation, trace } from "@opentelemetry/api";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
// @opentelemetry/resources 2.x (pinned to clear GHSA-8988-4f7v-96qf) dropped
// the constructable `Resource` class in favour of the `resourceFromAttributes`
// factory function: `Resource` is now only a type, not `new`-able.
//
// It also changed a second, easier-to-miss behaviour: 1.x's
// BasicTracerProvider silently merged any user-supplied `resource` with
// `Resource.default()` (which carries the `telemetry.sdk.language/name/version`
// SDK_INFO attributes) via a `mergeResourceWithDefaults` config flag that
// defaulted to true. 2.x's BasicTracerProvider does no such merge — it uses
// exactly the resource it is given, so the SDK_INFO attributes must be merged
// explicitly below, or exported spans carry no telemetry.sdk.* attributes at
// all.
import { defaultResource, resourceFromAttributes } from "@opentelemetry/resources";
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
    resource: defaultResource().merge(
      resourceFromAttributes({ [ATTR_SERVICE_NAME]: "customer-ui" }),
    ),
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
