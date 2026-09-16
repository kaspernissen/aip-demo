/* Overrides `app/layout` -- the one extension every routed page renders
 * inside -- so the band in PageBanner.tsx reaches all of them from a single
 * place.
 *
 * WHAT THIS REPLACES, EXACTLY. `@backstage/plugin-app`'s own AppLayout
 * (`dist/extensions/AppLayout.esm.js`) is
 *
 *     createExtension({
 *       name: 'layout',
 *       attachTo: { id: 'app/root', input: 'children' },
 *       inputs: { nav, content },      // both singleton react elements
 *       factory: () => <SidebarPage>{nav}{content}</SidebarPage>,
 *     })
 *
 * and an extension declared with the same id wins. The id is composed from the
 * MODULE's pluginId and the EXTENSION's name: `pluginId: 'app'` plus
 * `name: 'layout'` is what yields `app/layout`. Get either one wrong and there
 * is no error anywhere -- the module attaches nothing, the app renders exactly
 * as it did before, and the only symptom is that the feature you just built
 * is not on the screen. That silent shape is why
 * `tests/unit/test_page_banner.py` asserts both strings rather than trusting
 * the diff.
 *
 * THE COST, WRITTEN DOWN. This is the one place in the app that re-declares an
 * upstream extension rather than extending one. If a future Backstage changes
 * what `app/layout` renders -- a third input, a different root component --
 * this file keeps rendering the old shape and quietly drops the new part. It
 * is four lines of upstream code, copied deliberately, and it needs a look at
 * every Backstage bump. The alternative that does NOT carry that cost is
 * `AppRootWrapperBlueprint`, which is additive and supported; it was not used
 * because it wraps the sidebar as well as the content, and the landing page's
 * banner has to sit inside the content column, not above the whole app.
 */
import {
  coreExtensionData,
  createExtension,
  createExtensionInput,
  createFrontendModule,
} from '@backstage/frontend-plugin-api';
import { SidebarPage } from '@backstage/core-components';
import { PageBanner } from './PageBanner';

const bandedAppLayout = createExtension({
  name: 'layout',
  attachTo: { id: 'app/root', input: 'children' },
  inputs: {
    nav: createExtensionInput([coreExtensionData.reactElement], {
      singleton: true,
    }),
    content: createExtensionInput([coreExtensionData.reactElement], {
      singleton: true,
    }),
  },
  output: [coreExtensionData.reactElement],
  factory: ({ inputs }) => [
    coreExtensionData.reactElement(
      <SidebarPage>
        {inputs.nav.get(coreExtensionData.reactElement)}
        <PageBanner>
          {inputs.content.get(coreExtensionData.reactElement)}
        </PageBanner>
      </SidebarPage>,
    ),
  ],
});

export const pageBannerModule = createFrontendModule({
  pluginId: 'app',
  extensions: [bandedAppLayout],
});
