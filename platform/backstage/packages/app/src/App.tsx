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
// No theme module. This app deliberately runs Backstage's own default light
// and dark themes.
//
// There WAS one -- a full Northstar palette: navy surfaces on the brand hue,
// custom page banners for all nine page types, table and scrollbar overrides.
// It was removed 2026-09-16 after three rounds of trying to make it look
// right. The lesson is worth keeping, because the failures were not random:
// recolouring a design system means owning every surface it has, and each
// round fixed the surface in the screenshot while breaking one just outside
// it -- a table override that outgrew its card, page and card sat too close
// to separate, a browser scrollbar still painted for a light page. Backstage's
// defaults are internally consistent in a way a partial override is not.
//
// Northstar's identity now rides entirely on things that are NOT the
// colour system, and it survives every Backstage upgrade for free:
//   - the logo lockup in modules/nav (LogoFull/LogoIcon)
//   - `app.title` and `organization.name` in app-config.yaml
//   - the catalog's own content: the components, docs and links
import { homeModule } from './modules/home';
// M6 Task 3: the autonomy card, its own frontend plugin --
// modules/autonomyContract/plugin.tsx. Backend half: packages/backend/src/
// plugins/autonomyContract, registered in packages/backend/src/index.ts.
import { autonomyContractPlugin } from './modules/autonomyContract/plugin';
// Renders ```mermaid fences on TechDocs pages. A TechDocs addon rather than
// anything simpler because TechDocs puts the generated HTML behind a shadow
// root -- see modules/mermaid/MermaidAddon.tsx.
import { mermaidModule } from './modules/mermaid';
// Puts Backstage's own gradient page-theme band across the top of every page,
// not only the two that were never migrated off the classic `<Page>`/`<Header>`
// pair. This is the ONE module that overrides an upstream extension rather
// than adding to it (`app/layout`) -- modules/pageBanner/layout.tsx says what
// that costs and why the additive alternative did not fit.
import { pageBannerModule } from './modules/pageBanner';

export default createApp({
  features: [
    catalogPlugin,
    kubernetesPlugin,
    navModule,
    homeModule,
    autonomyContractPlugin,
    mermaidModule,
    pageBannerModule,
  ],
});
