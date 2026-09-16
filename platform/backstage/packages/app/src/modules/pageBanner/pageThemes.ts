/* Which of Backstage's ten built-in page themes each area of the portal wears.
 *
 * Backstage ships a `pageTheme` record -- ten named gradients, each a pair of
 * colours and a decorative SVG shape -- and `theme.getPageTheme({ themeId })`
 * hands one back off the live theme. Classic pages pick theirs with
 * `<Page themeId="tool">`; new-frontend-system pages have no equivalent, which
 * is why every page in this portal except two was flat grey at the top.
 *
 * This module is the missing selector: path in, `themeId` out. It does not
 * name a colour -- the colours belong to `@backstage/theme`, and the whole
 * point of borrowing them is that a Backstage restyle carries this portal with
 * it. Same rule as `modules/mermaid`: not one hex literal in this directory.
 *
 * Keyed on the FIRST PATH SEGMENT rather than a prefix list, which is not a
 * stylistic choice. A prefix list has to put `/catalog-graph` before
 * `/catalog` or the graph silently inherits the catalog's band, and nothing
 * about the list says so -- a reordering that looks like tidying is a defect.
 * One segment, one exact key, no order to get wrong.
 *
 * An unknown segment falls back to `home` rather than throwing. A new plugin
 * page should get a band that looks deliberate, not a white screen; the cost
 * is that adding a page and forgetting to name it here is invisible, which is
 * what `tests/unit/test_page_banner.py` exists to make loud.
 */

/** The path segment a route's band is chosen by. `/` maps to `''`. */
export const sectionOf = (pathname: string): string =>
  pathname.replace(/^\/+/, '').split('/')[0];

/* The assignment itself. Two rules held it together:
 *
 *   - the band's colour names the plugin, and the plugin's name is what is
 *     written on the band. `/catalog` and `/catalog/default/component/x` are
 *     both "Catalog" in teal because the toolbar says "Catalog" on both.
 *   - where a classic page already paints its own header, the band above it
 *     gets the SAME id, so the two read as one object rather than as two
 *     stacked stripes. `/autonomy-contract` renders `<Page themeId="tool">`
 *     (modules/autonomyContract/AutonomyContractPage.tsx) and so is `tool`
 *     here; `/catalog-graph` renders the catalog-graph plugin's own `home`
 *     header and so is `home`.
 */
export const PAGE_THEME_BY_SECTION: Readonly<Record<string, string>> = {
  '': 'home', // the landing page
  catalog: 'home',
  'catalog-graph': 'home',
  docs: 'documentation',
  'api-docs': 'apis',
  create: 'tool',
  search: 'home',
  settings: 'tool',
  'autonomy-contract': 'tool',
  notifications: 'apis',
  'app-visualizer': 'tool',
};

export const FALLBACK_PAGE_THEME = 'home';

export const pageThemeForPath = (pathname: string): string =>
  PAGE_THEME_BY_SECTION[sectionOf(pathname)] ?? FALLBACK_PAGE_THEME;
