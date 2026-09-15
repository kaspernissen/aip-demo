/**
 * M6 task-3 review, M5: `index.ts`'s `init` used to call
 * `buildInClusterWriterKubeConfig()` unguarded, so a missing writer token
 * (any local `yarn start`, or a cold `--stage 6` racing the token
 * controller -- M6-R6) threw out of `init` and
 * `@backstage/backend-app-api`'s `BackendInitializer` turned that into a
 * fatal `BackendStartupError` -- the whole backend process exits.
 *
 * These tests exercise `initAutonomyContract` directly, with the k8s client
 * module mocked, rather than standing up the full Backstage backend-wiring
 * harness: the property under test is "a thrown credential acquisition
 * degrades ONLY this plugin's routes", which does not need a running
 * backend to demonstrate.
 */
import express from 'express';
import request from 'supertest';
import { initAutonomyContract } from './index';
import * as k8sClient from './k8sClient';

jest.mock('./k8sClient', () => {
  const actual = jest.requireActual('./k8sClient');
  return {
    ...actual,
    buildInClusterWriterKubeConfig: jest.fn(),
    makeCustomObjectsClient: jest.fn(),
  };
});

function fakeLogger() {
  return { error: jest.fn(), warn: jest.fn(), info: jest.fn(), debug: jest.fn() } as any;
}

function fakeAuditor() {
  return {
    createEvent: jest.fn(async () => ({
      success: jest.fn(async () => {}),
      fail: jest.fn(async () => {}),
    })),
  } as any;
}

/** A minimal stand-in for Backstage's `HttpRouterService`: `.use()` mounts
 * the plugin's router onto a real express app, so the routes it registers
 * can be exercised with supertest exactly like router.test.ts does. */
function fakeHttpRouter() {
  const app = express();
  return {
    service: {
      use: (router: any) => app.use('/api/autonomy-contract', router),
    } as any,
    app,
  };
}

beforeEach(() => {
  (k8sClient.buildInClusterWriterKubeConfig as jest.Mock).mockReset();
  (k8sClient.makeCustomObjectsClient as jest.Mock).mockReset();
});

describe('initAutonomyContract', () => {
  it('degrades to 503 on every route, and does not throw, when the writer credential cannot be acquired', async () => {
    (k8sClient.buildInClusterWriterKubeConfig as jest.Mock).mockImplementation(
      () => {
        throw new Error('KUBERNETES_SERVICE_HOST/PORT are not set');
      },
    );
    const logger = fakeLogger();
    const { service: httpRouter, app } = fakeHttpRouter();

    await expect(
      initAutonomyContract({ httpRouter, logger, auditor: fakeAuditor() }),
    ).resolves.toBeUndefined();

    const status = await request(app).get('/api/autonomy-contract/status');
    expect(status.status).toBe(503);
    expect(status.body.reason).toContain('KUBERNETES_SERVICE_HOST');

    const variant = await request(app)
      .post('/api/autonomy-contract/variant')
      .send({ variant: 'autonomous' });
    expect(variant.status).toBe(503);

    expect(logger.error).toHaveBeenCalledWith(
      expect.stringContaining('could not acquire the writer credential'),
      expect.any(Error),
    );
  });

  it('mounts the real router (not the degraded one) when the credential is acquired successfully', async () => {
    (k8sClient.buildInClusterWriterKubeConfig as jest.Mock).mockReturnValue(
      {} as any,
    );
    (k8sClient.makeCustomObjectsClient as jest.Mock).mockReturnValue({
      getNamespacedCustomObject: jest.fn(async () => {
        throw new Error('not called in this test');
      }),
      patchNamespacedCustomObject: jest.fn(),
    });
    const { service: httpRouter, app } = fakeHttpRouter();

    await initAutonomyContract({
      httpRouter,
      logger: fakeLogger(),
      auditor: fakeAuditor(),
    });

    // The real router's GET /status calls client.getFlag() and fails with
    // 500/502 on a broken fixture -- the point here is that it is NOT 503,
    // which is the degraded router's one and only status code.
    const res = await request(app).get('/api/autonomy-contract/status');
    expect(res.status).not.toBe(503);
  });
});
