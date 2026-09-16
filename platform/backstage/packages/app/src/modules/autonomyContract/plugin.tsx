/**
 * M6 Task 3: registers the autonomy card as its own page, `/autonomy-contract`,
 * following `@backstage/plugin-app-visualizer`'s own minimal `PageBlueprint`
 * pattern (that plugin's `packages/app-visualizer/src/plugin.tsx` is the
 * template this file copies the shape of). Picked up by the Sidebar's
 * `nav.rest({ sortBy: 'title' })` (packages/app/src/modules/nav/Sidebar.tsx).
 *
 * **`icon` is required for that to happen, and this file shipped without one.**
 * The Sidebar renders each nav item as
 * `<SidebarItem icon={() => item.icon} ... />`, so an item with no icon
 * contributes nothing visible: the route stayed live, `/autonomy-contract`
 * answered 200, the backend API returned the real contract, and the page was
 * simply unreachable from the UI -- there was no link to it anywhere in the
 * portal. The demo's whole last beat is opening this page and flipping the
 * flag, and nothing on screen said it existed. Found 2026-09-16 by a human
 * asking where OpenFeature was; the template it was copied from passes an
 * icon, and that difference is the entire defect.
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
