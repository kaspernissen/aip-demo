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
 */
import {
  coreServices,
  createBackendPlugin,
} from '@backstage/backend-plugin-api';
import {
  AutonomyContractClient,
  buildInClusterWriterKubeConfig,
  makeCustomObjectsClient,
} from './k8sClient';
import { createAutonomyContractRouter } from './router';

export const autonomyContractPlugin = createBackendPlugin({
  pluginId: 'autonomy-contract',
  register(env) {
    env.registerInit({
      deps: {
        httpRouter: coreServices.httpRouter,
        logger: coreServices.logger,
        auditor: coreServices.auditor,
      },
      async init({ httpRouter, logger, auditor }) {
        const kc = buildInClusterWriterKubeConfig();
        const client = new AutonomyContractClient(makeCustomObjectsClient(kc));
        httpRouter.use(
          createAutonomyContractRouter({ client, logger, auditor }),
        );
      },
    });
  },
});

export default autonomyContractPlugin;
