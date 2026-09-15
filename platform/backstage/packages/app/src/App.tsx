import { createApp } from '@backstage/frontend-defaults';
import catalogPlugin from '@backstage/plugin-catalog/alpha';
// M6 Task 2: shows each catalog entity's live workload (design §2, "The
// Kubernetes plugin"). Registers its own `entity-content:kubernetes/kubernetes`
// tab on the entity page -- the new frontend system's plugins self-attach, so
// no manual EntityPage composition is needed the way the old system required.
// Backend half: packages/backend/src/index.ts already registers
// @backstage/plugin-kubernetes-backend (scaffold default); app-config.
// production.yaml's `kubernetes:` block is what actually points it at this
// cluster.
import kubernetesPlugin from '@backstage/plugin-kubernetes/alpha';
import { navModule } from './modules/nav';
import { homeModule } from './modules/home';

export default createApp({
  features: [catalogPlugin, kubernetesPlugin, navModule, homeModule],
});
