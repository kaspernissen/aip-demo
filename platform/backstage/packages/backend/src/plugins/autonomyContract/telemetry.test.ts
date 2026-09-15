import { buildOtlpTracePayload, emitContractChangeSpan, SPAN_NAME } from './telemetry';

const EVENT = {
  previousVariant: 'assisted',
  newVariant: 'autonomous',
  fieldManager: 'backstage-autonomy-card',
  flagName: 'northstar-autonomy',
  flagNamespace: 'northstar-platform',
};

describe('buildOtlpTracePayload', () => {
  it('names the span and carries every attribute the audit trail needs', () => {
    const payload = buildOtlpTracePayload(EVENT, 'backstage');
    const span = payload.resourceSpans[0].scopeSpans[0].spans[0];
    expect(span.name).toBe(SPAN_NAME);
    const attrs = Object.fromEntries(
      span.attributes.map((a: any) => [a.key, a.value.stringValue]),
    );
    expect(attrs['northstar.contract.previous_variant']).toBe('assisted');
    expect(attrs['northstar.contract.new_variant']).toBe('autonomous');
    expect(attrs['northstar.contract.field_manager']).toBe('backstage-autonomy-card');
    expect(attrs['k8s.namespace.name']).toBe('northstar-platform');
    expect(attrs['k8s.resource.name']).toBe('northstar-autonomy');
    const serviceName = payload.resourceSpans[0].resource.attributes.find(
      (a: any) => a.key === 'service.name',
    );
    expect(serviceName).toBeDefined();
    expect(serviceName!.value.stringValue).toBe('backstage');
  });

  it('mints a fresh trace/span id on every call, never a fixed placeholder', () => {
    const first = buildOtlpTracePayload(EVENT, 'backstage');
    const second = buildOtlpTracePayload(EVENT, 'backstage');
    const id = (p: any) => p.resourceSpans[0].scopeSpans[0].spans[0].traceId;
    expect(id(first)).not.toBe(id(second));
    expect(id(first)).toMatch(/^[0-9a-f]{32}$/);
  });
});

describe('emitContractChangeSpan', () => {
  it('posts to <endpoint>/v1/traces and reports ok on a 200', async () => {
    const fetchImpl = jest.fn(async () => ({ ok: true, status: 200, statusText: 'OK' })) as any;
    const result = await emitContractChangeSpan(EVENT, {
      endpoint: 'http://otel-collector.observability.svc.cluster.local:4318',
      serviceName: 'backstage',
      fetchImpl,
    });
    expect(result).toEqual({ attempted: true, ok: true });
    expect(fetchImpl).toHaveBeenCalledWith(
      'http://otel-collector.observability.svc.cluster.local:4318/v1/traces',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('never throws when the collector is unreachable -- reports ok:false instead', async () => {
    const fetchImpl = jest.fn(async () => {
      throw new Error('ECONNREFUSED');
    }) as any;
    const result = await emitContractChangeSpan(EVENT, {
      endpoint: 'http://otel-collector.observability.svc.cluster.local:4318',
      fetchImpl,
    });
    expect(result.attempted).toBe(true);
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/ECONNREFUSED/);
  });

  it('never throws and reports ok:false on a non-2xx response', async () => {
    const fetchImpl = jest.fn(async () => ({ ok: false, status: 503, statusText: 'Service Unavailable' })) as any;
    const result = await emitContractChangeSpan(EVENT, {
      endpoint: 'http://otel-collector.observability.svc.cluster.local:4318',
      fetchImpl,
    });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/503/);
  });

  it('reports attempted:false when no collector endpoint is configured, rather than guessing one', async () => {
    const fetchImpl = jest.fn();
    const result = await emitContractChangeSpan(EVENT, { endpoint: undefined, fetchImpl: fetchImpl as any });
    expect(result).toEqual({ attempted: false, ok: false, error: expect.stringContaining('OTEL_EXPORTER_OTLP_ENDPOINT') });
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
