/**
 * The write must be auditable and distinguishable from a `kubectl` edit.
 * Three independent signals carry that, and this module is the third and
 * the only DURABLE one (read before trusting the other two as history):
 *
 *   1. `k8sClient.ts`'s FIELD_MANAGER -- lands in the object's own
 *      `metadata.managedFields`, readable with plain `kubectl get -o yaml`.
 *      NOT a history: `managedFields` records current ownership of a field,
 *      not who has ever touched it. This client's write ERASES the prior
 *      `kubectl-patch` manager entry outright, and a `kubectl` revert of the
 *      same field erases this one right back -- point-in-time confirmation
 *      only.
 *   2. `router.ts`'s use of `coreServices.auditor` -- Backstage's own
 *      structured audit-event service, visible in this pod's logs. Real,
 *      but lives only in this pod's own logs, backed by a `:memory:`
 *      database with no log shipping -- gone the moment the pod is.
 *   3. This module -- a real span, in the SAME OTLP pipeline every other
 *      first-party service in this cluster exports into
 *      (deploy/observability/collector-config.yaml), so an operator looking
 *      at Jaeger sees the change too, not only an operator
 *      reading this one pod's logs or this one object's YAML. This is the
 *      signal that survives both the object being reverted and the pod
 *      being recycled -- the auditability claim should rest on THIS, not
 *      on (1).
 *
 * Hand-rolled OTLP/HTTP JSON rather than the `@opentelemetry/sdk-trace-node`
 * + exporter packages: this plugin emits at most a few spans per process
 * lifetime (one per write), so there is no batching/perf case to justify a
 * tracer-provider lifecycle, and a smaller dependency surface is worth
 * having on its own. The OTLP collector's `otlp` receiver
 * (deploy/observability/collector-config.yaml) accepts either protobuf or
 * JSON on its HTTP port by content negotiation, per the OTLP/HTTP spec --
 * this sends JSON.
 *
 * "100% telemetry, never sampled" (this repo's standing rule) means an
 * export failure must not be swallowed silently: `emitContractChangeSpan`
 * never throws (a telemetry outage must not block a policy-contract write,
 * the "an auditor that can stop a payment is not an auditor" tradeoff), but
 * it always reports success or
 * failure back to the caller so the caller can log it loudly instead.
 */
import * as crypto from 'crypto';

export const SPAN_NAME = 'northstar.autonomy_contract.change';

export interface ContractChangeEvent {
  previousVariant: string;
  newVariant: string;
  fieldManager: string;
  flagName: string;
  flagNamespace: string;
}

function hex(bytes: number): string {
  return crypto.randomBytes(bytes).toString('hex');
}

function attr(key: string, value: string) {
  return { key, value: { stringValue: value } };
}

export function buildOtlpTracePayload(
  event: ContractChangeEvent,
  serviceName: string,
  nowNanos: bigint = BigInt(Date.now()) * 1_000_000n,
) {
  const traceId = hex(16);
  const spanId = hex(8);
  return {
    resourceSpans: [
      {
        resource: {
          attributes: [attr('service.name', serviceName)],
        },
        scopeSpans: [
          {
            scope: { name: 'northstar.autonomy-contract' },
            spans: [
              {
                traceId,
                spanId,
                name: SPAN_NAME,
                kind: 1, // SPAN_KIND_INTERNAL
                startTimeUnixNano: nowNanos.toString(),
                endTimeUnixNano: nowNanos.toString(),
                attributes: [
                  attr('northstar.contract.previous_variant', event.previousVariant),
                  attr('northstar.contract.new_variant', event.newVariant),
                  attr('northstar.contract.field_manager', event.fieldManager),
                  attr('northstar.contract.actor', 'backstage-autonomy-card'),
                  attr('k8s.namespace.name', event.flagNamespace),
                  attr('northstar.contract.flag.name', event.flagName),
                ],
                status: { code: 1 }, // STATUS_CODE_OK
              },
            ],
          },
        ],
      },
    ],
  };
}

export interface EmitResult {
  attempted: boolean;
  ok: boolean;
  error?: string;
}

/**
 * Best-effort. Returns a result rather than throwing; the caller (router.ts)
 * logs a failure loudly rather than letting it vanish -- "watch for loss,
 * which looks identical to sampling from outside" is this repo's own
 * standing warning about exactly this kind of silent drop.
 */
export async function emitContractChangeSpan(
  event: ContractChangeEvent,
  options: {
    endpoint?: string;
    serviceName?: string;
    fetchImpl?: typeof fetch;
  } = {},
): Promise<EmitResult> {
  const endpoint = options.endpoint ?? process.env.OTEL_EXPORTER_OTLP_ENDPOINT;
  if (!endpoint) {
    return { attempted: false, ok: false, error: 'OTEL_EXPORTER_OTLP_ENDPOINT is not set' };
  }
  const serviceName = options.serviceName ?? process.env.OTEL_SERVICE_NAME ?? 'backstage';
  const doFetch = options.fetchImpl ?? fetch;
  const payload = buildOtlpTracePayload(event, serviceName);

  try {
    const res = await doFetch(`${endpoint.replace(/\/+$/, '')}/v1/traces`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      return {
        attempted: true,
        ok: false,
        error: `collector responded ${res.status} ${res.statusText}`,
      };
    }
    return { attempted: true, ok: true };
  } catch (error) {
    return {
      attempted: true,
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}
