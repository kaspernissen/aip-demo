/**
 * The autonomy-contract card's backend half. Registers
 * `/api/autonomy-contract/status` (GET) and `/api/autonomy-contract/variant`
 * (POST) -- see router.ts for what each does and why.
 *
 * Deliberately its own plugin, not folded into the scaffold's existing
 * backend (packages/backend/src/index.ts just adds this one more import,
 * the same way it adds every other plugin). That keeps the write path in one
 * reviewable module, matching how deploy/k8s/backstage.yaml keeps the write
 * RBAC in its own ServiceAccount/Role/RoleBinding rather than folded into
 * the read-only objects.
 *
 * No custom Backstage permission is registered here. This app already runs
 * one blanket policy for everything (`@backstage/plugin-permission-backend-
 * module-allow-all-policy`, packages/backend/src/index.ts): Backstage here is
 * unauthenticated in the only sense that matters (guest auth,
 * `dangerouslyAllowOutsideDevelopment`), so a second, narrower permission on
 * top of a blanket allow-all would be decoration. `httpRouter`'s DEFAULT
 * behaviour (no `addAuthPolicy` call below) still requires a valid Backstage
 * credential on every request here -- that is real, even if guest auth makes
 * it trivial to obtain: it is the difference between "reached this port and
 * went through Backstage's own auth flow" and "reached this port with a
 * bare, credential-less HTTP client", the same distinction that matters for
 * the write's auditability more broadly.
 *
 * `init` must not call `buildInClusterWriterKubeConfig()` unguarded: that
 * throws whenever `KUBERNETES_SERVICE_HOST`/`PORT` are unset (any local
 * `yarn start`) or the writer token file is not yet mounted (a cold bring-up
 * racing the token controller). A thrown `init` becomes, via
 * `@backstage/backend-app-api`'s `BackendInitializer`, a fatal
 * `BackendStartupError`: the whole backend process exits, taking
 * scaffolder/techdocs/catalog/everything down for a credential only THIS
 * plugin needs. `initAutonomyContract` below is exported and exercised
 * directly by `index.test.ts` so this stays guarded without needing the full
 * Backstage backend-wiring test harness.
 *
 * Guarding `init` is not enough on its own: catching the throw and degrading
 * immediately would convert a loud crash into a SILENT degradation. The
 * writer-token Secret's `token` key is populated asynchronously by the token
 * controller (deploy/k8s/backstage.yaml mounts the Secret `optional: false`,
 * which only requires the object to exist, not the key), so a cold bring-up
 * that mounts before the key lands would still degrade this plugin to
 * permanent 503 -- and the pod is Ready regardless (the readiness probe hits
 * Backstage's own health endpoint, not this plugin), so a bring-up could
 * report healthy over a card that can never write. Retrying with a bounded
 * backoff before degrading removes the race: a token that lands a second or
 * two late costs nothing, and only a credential that is STILL missing after
 * every retry degrades.
 */
import type {
  AuditorService,
  HttpRouterService,
  LoggerService,
} from '@backstage/backend-plugin-api';
import { coreServices, createBackendPlugin } from '@backstage/backend-plugin-api';
import {
  AutonomyContractClient,
  buildInClusterWriterKubeConfig,
  makeCustomObjectsClient,
} from './k8sClient';
import {
  createAutonomyContractRouter,
  createDegradedAutonomyContractRouter,
} from './router';

export interface InitAutonomyContractDeps {
  httpRouter: HttpRouterService;
  logger: LoggerService;
  auditor: AuditorService;
}

/** Bounded backoff for the writer-credential race: `RETRIES` attempts,
 * `RETRY_DELAY_MS` apart, production defaults sized for a token controller
 * that is at most a couple of seconds slow. Overridable so `index.test.ts`
 * can prove the retry-then-degrade path without a real multi-second
 * sleep. */
const DEFAULT_CREDENTIAL_RETRIES = 5;
const DEFAULT_CREDENTIAL_RETRY_DELAY_MS = 1000;

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export async function initAutonomyContract(
  { httpRouter, logger, auditor }: InitAutonomyContractDeps,
  retryOptions: { retries?: number; retryDelayMs?: number } = {},
): Promise<void> {
  const retries = retryOptions.retries ?? DEFAULT_CREDENTIAL_RETRIES;
  const retryDelayMs =
    retryOptions.retryDelayMs ?? DEFAULT_CREDENTIAL_RETRY_DELAY_MS;

  let lastErr: Error = new Error('unreachable: retries must be >= 1');
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const kc = buildInClusterWriterKubeConfig();
      const client = new AutonomyContractClient(makeCustomObjectsClient(kc));
      httpRouter.use(createAutonomyContractRouter({ client, logger, auditor }));
      return;
    } catch (e) {
      lastErr = e instanceof Error ? e : new Error(String(e));
      if (attempt < retries) {
        logger.warn(
          `autonomy-contract: writer credential not ready yet (attempt ` +
            `${attempt}/${retries}), retrying in ${retryDelayMs}ms -- ` +
            `${lastErr.message}`,
        );
        await sleep(retryDelayMs);
      }
    }
  }

  logger.error(
    `autonomy-contract: could not acquire the writer credential after ` +
      `${retries} attempts -- degrading THIS plugin only (503 on every ` +
      'route) instead of failing the whole backend',
    lastErr,
  );
  httpRouter.use(
    createDegradedAutonomyContractRouter({ reason: lastErr.message }),
  );
}

export const autonomyContractPlugin = createBackendPlugin({
  pluginId: 'autonomy-contract',
  register(env) {
    env.registerInit({
      deps: {
        httpRouter: coreServices.httpRouter,
        logger: coreServices.logger,
        auditor: coreServices.auditor,
      },
      init: initAutonomyContract,
    });
  },
});

export default autonomyContractPlugin;
