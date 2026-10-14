import express from 'express';
import request from 'supertest';
import { createAutonomyContractRouter } from './router';
import * as telemetry from './telemetry';
import type { AutonomyContractClient } from './k8sClient';

jest.mock('./telemetry');

const SETTLEMENT_AGENT =
  'spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent';
// `elseBranch` is a required argument, not hardcoded to 'assisted': if
// then and else were ever byte-identical across every fixture, a mutation
// swapping router.ts's `if?.[1]` for `if?.[2]` (reading the ELSE branch
// instead of the THEN branch) would pass undetected, because nothing could
// tell the difference. Requiring the argument keeps a caller from falling
// back into that tautology by omission.
function flag(thenBranch: string, elseBranch: string) {
  return flagWithCallers([SETTLEMENT_AGENT], thenBranch, elseBranch);
}

function flagWithCallers(
  callerIds: string[],
  thenBranch: string,
  elseBranch: string,
) {
  return {
    apiVersion: 'core.openfeature.dev/v1beta1',
    kind: 'FeatureFlag',
    metadata: { name: 'northstar-autonomy', namespace: 'northstar-platform' },
    spec: {
      flagSpec: {
        flags: {
          'autonomy-level': {
            state: 'ENABLED',
            variants: { assisted: 'assisted', autonomous: 'autonomous' },
            defaultVariant: 'assisted',
            targeting: {
              if: [
                { in: [{ var: 'spiffeId' }, callerIds] },
                thenBranch,
                elseBranch,
              ],
            },
          },
        },
      },
    },
  };
}

function fakeLogger() {
  return { error: jest.fn(), warn: jest.fn(), info: jest.fn(), debug: jest.fn() } as any;
}

function fakeAuditor() {
  const events: any[] = [];
  return {
    events,
    createEvent: jest.fn(async (opts: any) => {
      const event = { opts, success: jest.fn(async () => {}), fail: jest.fn(async () => {}) };
      events.push(event);
      return event;
    }),
  } as any;
}

function buildApp(client: Partial<AutonomyContractClient>, logger = fakeLogger(), auditor = fakeAuditor()) {
  const app = express();
  app.use(
    '/api/autonomy-contract',
    createAutonomyContractRouter({
      client: client as AutonomyContractClient,
      logger,
      auditor,
    }),
  );
  return { app, logger, auditor };
}

beforeEach(() => {
  (telemetry.emitContractChangeSpan as jest.Mock).mockReset();
  (telemetry.emitContractChangeSpan as jest.Mock).mockResolvedValue({ attempted: true, ok: true });
});

describe('GET /status', () => {
  it('resolves the identity from the live object, not a cached copy', async () => {
    const getFlag = jest.fn(async () => flag('assisted', 'assisted'));
    const { app, auditor } = buildApp({ getFlag });

    const res = await request(app).get('/api/autonomy-contract/status');

    expect(res.status).toBe(200);
    expect(getFlag).toHaveBeenCalledTimes(1);
    expect(res.body.perIdentity['settlement-agent']).toEqual({
      level: 'assisted',
      branch: 'targeting-then',
    });
    expect(Object.keys(res.body.perIdentity)).toEqual(['settlement-agent']);
    expect(auditor.createEvent).toHaveBeenCalledWith(
      expect.objectContaining({ eventId: 'contract-read' }),
    );
    expect(auditor.events[0].success).toHaveBeenCalled();
  });

  it('reflects a live flip immediately -- no caching layer of its own', async () => {
    const getFlag = jest.fn(async () => flag('autonomous', 'assisted'));
    const { app } = buildApp({ getFlag });

    const res = await request(app).get('/api/autonomy-contract/status');

    expect(res.body.perIdentity['settlement-agent'].level).toBe('autonomous');
  });

  it('pins the settlement-agent SPIFFE literal: it lands on the then-branch only if router.ts names exactly this id', async () => {
    // A corrupted literal in TARGETED_IDENTITIES would stop matching this
    // fixture's callerIds and fall through to the else-branch ('assisted')
    // instead of the then-branch ('autonomous') asserted below.
    const getFlag = jest.fn(async () =>
      flagWithCallers([SETTLEMENT_AGENT], 'autonomous', 'assisted'),
    );
    const { app } = buildApp({ getFlag });

    const res = await request(app).get('/api/autonomy-contract/status');

    expect(res.body.perIdentity).toEqual({
      'settlement-agent': { level: 'autonomous', branch: 'targeting-then' },
    });
  });

  it('answers 502 and fails the audit event rather than guessing at an unsupported shape', async () => {
    const getFlag = jest.fn(async () => ({
      spec: { flagSpec: { flags: { 'autonomy-level': { state: 'ENABLED', defaultVariant: 'assisted', targeting: { if: [{ '==': [1, 1] }, 'a', 'b'] } } } } },
    }));
    const { app, auditor } = buildApp({ getFlag });

    const res = await request(app).get('/api/autonomy-contract/status');

    expect(res.status).toBe(502);
    expect(auditor.events[0].fail).toHaveBeenCalled();
  });
});

describe('POST /variant', () => {
  it('rejects anything but the two declared variants, and never touches the cluster', async () => {
    const getFlag = jest.fn();
    const patchThenBranch = jest.fn();
    const { app, auditor } = buildApp({ getFlag, patchThenBranch });

    const res = await request(app)
      .post('/api/autonomy-contract/variant')
      .send({ variant: 'god-mode' });

    expect(res.status).toBe(400);
    expect(getFlag).not.toHaveBeenCalled();
    expect(patchThenBranch).not.toHaveBeenCalled();
    expect(auditor.events[0].fail).toHaveBeenCalled();
  });

  it('ignores any extra body field that looks like it names a path or a resource', async () => {
    // If this endpoint ever accepted a `path` or `name` from the request
    // body, this test would need to assert patchThenBranch received them --
    // it does not, on purpose, and the client (k8sClient.ts) hardcodes both.
    const getFlag = jest.fn(async () => flag('assisted', 'autonomous'));
    const patchThenBranch = jest.fn(async () => flag('autonomous', 'assisted'));
    const { app } = buildApp({ getFlag, patchThenBranch });

    await request(app)
      .post('/api/autonomy-contract/variant')
      .send({
        variant: 'autonomous',
        path: '/metadata/name',
        name: 'some-other-flag',
        resourceName: '*',
      });

    expect(patchThenBranch).toHaveBeenCalledTimes(1);
    expect(patchThenBranch).toHaveBeenCalledWith('autonomous');
  });

  it('writes the requested variant, reports the previous one, and audits success', async () => {
    const getFlag = jest.fn(async () => flag('assisted', 'autonomous'));
    const patchThenBranch = jest.fn(async () => flag('autonomous', 'assisted'));
    const { app, auditor } = buildApp({ getFlag, patchThenBranch });

    const res = await request(app)
      .post('/api/autonomy-contract/variant')
      .send({ variant: 'autonomous' });

    expect(res.status).toBe(200);
    expect(res.body).toEqual({
      previousVariant: 'assisted',
      newVariant: 'autonomous',
      telemetryEmitted: true,
    });
    expect(patchThenBranch).toHaveBeenCalledWith('autonomous');
    expect(auditor.events[0].opts.severityLevel).toBe('high');
    expect(auditor.events[0].success).toHaveBeenCalled();
  });

  it('still succeeds the write, but reports telemetryEmitted:false and logs loudly, when the OTLP export fails', async () => {
    (telemetry.emitContractChangeSpan as jest.Mock).mockResolvedValue({
      attempted: true,
      ok: false,
      error: 'collector unreachable',
    });
    const getFlag = jest.fn(async () => flag('assisted', 'autonomous'));
    const patchThenBranch = jest.fn(async () => flag('autonomous', 'assisted'));
    const { app, logger } = buildApp({ getFlag, patchThenBranch });

    const res = await request(app)
      .post('/api/autonomy-contract/variant')
      .send({ variant: 'autonomous' });

    expect(res.status).toBe(200);
    expect(res.body.telemetryEmitted).toBe(false);
    expect(logger.error).toHaveBeenCalledWith(
      expect.stringContaining('OTLP span was NOT delivered'),
    );
  });

  it('fails the request and the audit event if the k8s patch itself throws', async () => {
    const getFlag = jest.fn(async () => flag('assisted', 'autonomous'));
    const patchThenBranch = jest.fn(async () => {
      throw new Error('forbidden');
    });
    const { app, auditor } = buildApp({ getFlag, patchThenBranch });

    const res = await request(app)
      .post('/api/autonomy-contract/variant')
      .send({ variant: 'autonomous' });

    expect(res.status).toBe(500);
    expect(auditor.events[0].fail).toHaveBeenCalled();
  });
});
