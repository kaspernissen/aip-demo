/**
 * M6 Task 3: registers the autonomy card as its own page, `/autonomy-contract`,
 * following `@backstage/plugin-app-visualizer`'s own minimal `PageBlueprint`
 * pattern (that plugin's `packages/app-visualizer/src/plugin.tsx` is the
 * template this file copies the shape of). Picked up automatically by the
 * Sidebar's `nav.rest({ sortBy: 'title' })`
 * (packages/app/src/modules/nav/Sidebar.tsx) -- no separate wiring needed
 * there, the same way `page:app-visualizer` needs none.
 */
import { createRouteRef, createFrontendPlugin, PageBlueprint } from '@backstage/frontend-plugin-api';

const rootRouteRef = createRouteRef();

const autonomyContractPage = PageBlueprint.make({
  params: {
    path: '/autonomy-contract',
    routeRef: rootRouteRef,
    title: 'Autonomy contract',
    loader: () =>
      import('./AutonomyContractPage').then(m => <m.AutonomyContractPage />),
  },
});

export const autonomyContractPlugin = createFrontendPlugin({
  pluginId: 'autonomy-contract',
  extensions: [autonomyContractPage],
});

export default autonomyContractPlugin;
