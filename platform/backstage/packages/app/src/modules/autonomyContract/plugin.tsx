/**
 * Registers the autonomy card as its own page, `/autonomy-contract`,
 * following `@backstage/plugin-app-visualizer`'s own minimal `PageBlueprint`
 * pattern (that plugin's `packages/app-visualizer/src/plugin.tsx` is the
 * template this file copies the shape of). Picked up by the Sidebar's
 * `nav.rest({ sortBy: 'title' })` (packages/app/src/modules/nav/Sidebar.tsx).
 *
 * **`icon` is required for that to happen.** The Sidebar renders each nav
 * item as `<SidebarItem icon={() => item.icon} ... />`, so an item with no
 * icon contributes nothing visible: the route would stay live,
 * `/autonomy-contract` would answer 200, the backend API would return the
 * real contract, and the page would simply be unreachable from the UI --
 * no link to it anywhere in the portal. The demo's whole last beat is
 * opening this page and flipping the flag, so the icon here is load-bearing,
 * not decorative.
 */
import { createRouteRef, createFrontendPlugin, PageBlueprint } from '@backstage/frontend-plugin-api';
import { RiToggleLine } from '@remixicon/react';

const rootRouteRef = createRouteRef();

const autonomyContractPage = PageBlueprint.make({
  params: {
    path: '/autonomy-contract',
    routeRef: rootRouteRef,
    // "OpenFeature", not "Autonomy contract" and not "Autonomy contract
    // (OpenFeature)". The rail is ~16 characters wide before it ellipsises
    // ("Register Exist..." is the widest thing that fits), so the longer
    // title rendered as "Autonomy co..." -- which hides the one word somebody
    // scanning for the feature-flag surface is actually looking for. The page
    // header still reads "Autonomy contract"; the rail names the mechanism,
    // the page names what it does.
    title: 'OpenFeature',
    icon: <RiToggleLine />,
    loader: () =>
      import('./AutonomyContractPage').then(m => <m.AutonomyContractPage />),
  },
});

export const autonomyContractPlugin = createFrontendPlugin({
  pluginId: 'autonomy-contract',
  extensions: [autonomyContractPage],
});

export default autonomyContractPlugin;
