/**
 * M6 Task 3: the autonomy-contract card's backend half. Registers
 * `/api/autonomy-contract/status` (GET) and `/api/autonomy-contract/variant`
 * (POST) -- see router.ts for what each does and why.
 *
 * Deliberately its own plugin, not folded into the scaffold's existing
 * backend (packages/backend/src/index.ts just adds this one more import,
 * the same way it adds every other plugin). That keeps the write path in one
 * reviewable module, matching how deploy/k8s/backstage.yaml keeps the write
 * RBAC in its own ServiceAccount/Role/RoleBinding rather than folded into
 * Task 1's read-only objects.
 *
 * No custom Backstage permission is registered here. This app already runs
 * one blanket policy for everything (`@backstage/plugin-permission-backend-
 * module-allow-all-policy`, packages/backend/src/index.ts), consistent with
 * design §10's stated posture: Backstage here is unauthenticated in the only
 * sense that matters (guest auth, `dangerouslyAllowOutsideDevelopment`), so a
 * second, narrower permission on top of a blanket allow-all would be
 * decoration. `httpRouter`'s DEFAULT behaviour (no `addAuthPolicy` call
 * below) still requires a valid Backstage credential on every request here --
 * that is real, even if guest auth makes it trivial to obtain: it is the
 * difference between "reached this port and went through Backstage's own
 * auth flow" and "reached this port with a bare, credential-less HTTP
 * client", which is the same distinction the design draws for the write's
 * auditability more broadly.
 *
 * M6 fix round (M5): `init` used to call `buildInClusterWriterKubeConfig()`
 * unguarded. That throws whenever `KUBERNETES_SERVICE_HOST`/`PORT` are unset
 * (any local `yarn start`) or the writer token file is not yet mounted (a
 * cold `--stage 6` bring-up racing the token controller -- M6-R6). A thrown
 * `init` becomes, via `@backstage/backend-app-api`'s `BackendInitializer`, a
 * fatal `BackendStartupError`: the whole backend process exits, taking
 * scaffolder/techdocs/catalog/everything down for a credential only THIS
 * plugin needs. `initAutonomyContract` below is exported and exercised
 * directly by `index.test.ts` so this stays guarded without needing the full
 * Backstage backend-wiring test harness.
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

export async function initAutonomyContract({
  httpRouter,
  logger,
  auditor,
}: InitAutonomyContractDeps): Promise<void> {
  try {
    const kc = buildInClusterWriterKubeConfig();
    const client = new AutonomyContractClient(makeCustomObjectsClient(kc));
    httpRouter.use(createAutonomyContractRouter({ client, logger, auditor }));
  } catch (e) {
    const err = e instanceof Error ? e : new Error(String(e));
    logger.error(
      'autonomy-contract: could not acquire the writer credential at ' +
        'startup -- degrading THIS plugin only (503 on every route) ' +
        'instead of failing the whole backend (M6 fix round, M5)',
      err,
    );
    httpRouter.use(
      createDegradedAutonomyContractRouter({ reason: err.message }),
    );
  }
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
