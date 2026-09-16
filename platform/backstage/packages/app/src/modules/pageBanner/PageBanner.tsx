/* The coloured band across the top of every page.
 *
 * WHAT THIS FIXES. Backstage's familiar gradient header -- a wave or a burst
 * of colour behind the page title -- is drawn by the CLASSIC `<Page>` /
 * `<Header>` pair from core-components. Every page this portal has migrated to
 * the new frontend system draws a `bui-PluginHeader` instead, which is flat and
 * transparent by default, so the portal ended up with the gradient on exactly
 * the two pages that had NOT been migrated (`/catalog-graph`, and this repo's
 * own `/autonomy-contract`) and nowhere else. Read as a product rather than as
 * a migration, that looks like the two odd pages are decorated and the rest are
 * unfinished -- which is precisely how it was reported.
 *
 * WHY A BAND AND NOT A BANNER. The obvious fix is to stack a header of our own
 * above each page. Every new-system page already draws its own title row, so
 * that gives two titles on every page -- "Catalog" in the band and "Northstar
 * Catalog" underneath it. Painting the rows Backstage ALREADY draws costs no
 * duplication at all, and it inherits every future upstream change to what
 * those rows contain: breadcrumbs, tabs, action buttons, entity tags. The one
 * page that draws no title row of any kind is the landing page, and that is
 * the one place below where a real banner element is rendered.
 *
 * HOW THE PAINT REACHES THEM. Backstage UI ships its component CSS inside
 * `@layer components` (see `@backstage/ui/dist/components/PluginHeader/
 * PluginHeader.module.css.esm.js`). An UNLAYERED rule beats every layered one
 * regardless of specificity, and JSS injects unlayered `<style>` elements --
 * so the descendant selectors below win without a single `!important` and
 * without touching the hashed class names' hash. They match on the stable
 * `bui-` token that Backstage UI prefixes every class with, which is the
 * load-bearing assumption in this file and the thing most likely to need
 * attention at a Backstage bump.
 *
 * NO COLOUR IS NAMED HERE. `theme.getPageTheme({ themeId })` returns the
 * gradient and its font colour off the live theme, so the band follows the
 * light/dark toggle and any future Backstage restyle for free -- the same
 * decision, for the same reason, as `modules/mermaid` and as App.tsx's note
 * about not owning the colour system. `modules/pageBanner/pageThemes.ts`
 * decides WHICH of the ten built-ins each area wears.
 */
import type { CSSProperties, ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { makeStyles, useTheme } from '@material-ui/core/styles';
import Typography from '@material-ui/core/Typography';
import { pageThemeForPath } from './pageThemes';

/* Both values are supplied per route as inline custom properties, so the
 * stylesheet below is static -- one rule set for the whole app, rather than a
 * fresh JSS sheet per themeId. */
const BAND_IMAGE = '--northstar-band-image';
const BAND_INK = '--northstar-band-ink';

const useStyles = makeStyles(theme => ({
  root: {
    // The wrapper draws nothing of its own. `display: contents` so it carries
    // the two custom properties and scopes the descendant selectors -- the
    // sidebar is outside it and is never painted -- without adding a box that
    // the content's own layout would have to survive.
    display: 'contents',

    /* THE BAND IS THE PLUGIN-HEADER ROW, AND ONLY THAT ROW. It is the one
     * element every routed page draws -- it carries the plugin's name, its
     * breadcrumbs and its tabs -- so painting it, and nothing else, gives one
     * rule that holds on all of them.
     *
     * The first attempt also painted `bui-HeaderContent__`, the title row that
     * some pages draw underneath, to get a taller band. Entity pages are why
     * that was reverted: there the two are not adjacent -- breadcrumbs sit
     * between them and a metadata row sits below -- so the "band" came out as
     * three coloured stripes with grey bands between, and the row's action
     * buttons, which take their own light background, turned into white
     * squares with white icons on them.
     *
     * The trailing `__` in the selector matters and looks like noise: without
     * it `[class*="bui-PluginHeader"]` also matches `bui-PluginHeaderToolbar`,
     * `bui-PluginHeaderToolbarName` and every other descendant, and the
     * gradient is redrawn from its own origin inside each one. */
    '& [class*="bui-PluginHeader__"]': {
      backgroundImage: `var(${BAND_IMAGE})`,
      // `cover` rather than the default `auto`: the shape SVG is authored
      // 1368x400, and at a band height of ~60px the default tiles it.
      backgroundSize: 'cover',
      backgroundPosition: 'center',
      backgroundRepeat: 'no-repeat',
      color: `var(${BAND_INK})`,
      borderBottom: 'none',
    },
    /* Everything written on the band takes the band's font colour. Backstage
     * sets a text colour on the plugin name, the breadcrumbs and the tabs
     * independently, each of which is tuned for the page ground and none of
     * which is legible on a saturated gradient. */
    '& [class*="bui-PluginHeader__"] *': {
      color: 'inherit',
      borderColor: 'currentColor',
    },
    /* The icon chip beside the plugin name is given an explicit neutral fill by
     * the same upstream stylesheet the rule above overrides, which on the
     * gradient reads as a grey hole punched in the band.
     *
     * Outlined rather than filled, and that is the second attempt too: filling
     * it with `currentColor` at `opacity: 0.25` dims the whole SUBTREE,
     * including the icon standing in it, so the chip became an empty pale
     * square. Opacity is not a colour channel. A transparent chip with a
     * `currentColor` rule keeps the icon at full strength and still names no
     * colour of its own. */
    '& [class*="bui-PluginHeaderToolbarIcon__"]': {
      backgroundColor: 'transparent',
      border: '1px solid currentColor',
    },
  },

  /* The landing page draws no toolbar and no title row -- it is a bare widget
   * grid -- so it is the one route that gets a header element of its own.
   * Sized and spaced like Backstage's own classic `Header` so that moving
   * between the landing page and any other page does not feel like moving
   * between two products. */
  homeBanner: {
    backgroundImage: `var(${BAND_IMAGE})`,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
    backgroundRepeat: 'no-repeat',
    color: `var(${BAND_INK})`,
    padding: theme.spacing(4),
    paddingTop: theme.spacing(5),
  },
  homeBannerSubtitle: {
    color: 'inherit',
    opacity: 0.8,
    maxWidth: '58em',
  },
}));

/* The landing page's own words. Deliberately NOT the opening sentence of the
 * overview card two inches below it (modules/home/homeModule.tsx) -- a banner
 * that repeats the first line of the first card is worse than no banner. This
 * says what the PORTAL is; the card says what the SYSTEM is. */
const HOME_TITLE = 'Northstar Internal Platform';
const HOME_SUBTITLE =
  'The catalog, the documentation and the autonomy contract for a ' +
  'motor-claims settlement system where agents do the work and the platform ' +
  'decides what they are allowed to do.';

export const PageBanner = ({ children }: { children: ReactNode }) => {
  const classes = useStyles();
  const theme = useTheme();
  const { pathname } = useLocation();

  const themeId = pageThemeForPath(pathname);
  const page = theme.getPageTheme({ themeId });

  const bandVars = {
    [BAND_IMAGE]: page.backgroundImage,
    [BAND_INK]: page.fontColor,
  } as CSSProperties;

  const isHome = pathname === '/';

  return (
    <div className={classes.root} style={bandVars}>
      {isHome && (
        <header className={classes.homeBanner}>
          <Typography variant="h3" component="h1">
            {HOME_TITLE}
          </Typography>
          <Typography variant="subtitle1" className={classes.homeBannerSubtitle}>
            {HOME_SUBTITLE}
          </Typography>
        </header>
      )}
      {children}
    </div>
  );
};
