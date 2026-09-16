/* Northstar's own theme.
 *
 * The palette is the one the claimant portal and the reviewer console already
 * wear (apps/customer-ui/src/app/globals.css is the source these hex values
 * are mirrored from -- if one moves, move the others): deep institutional
 * navy #0a2342, accent blue #1b5fa8, on a blue-tinted white #eff4fa. The
 * scaffold's magenta/purple gradients are replaced outright, because a demo
 * that opens Backstage at its punchline should look like the same company
 * that filed the claim two minutes earlier.
 *
 * `pageTheme` is the part worth knowing about: those big coloured banners at
 * the top of every Backstage page are a `background-image` generated from a
 * colour pair and a shape, not a component you can restyle with CSS. The
 * scaffold ships seven named page themes (home, documentation, service,
 * website, library, other, app, apis) and any one left undefined falls back
 * to Backstage's own magenta -- so every key is defined here rather than the
 * two or three this demo happens to open.
 *
 * ThemeBlueprint is exported from @backstage/plugin-app-react in this version
 * (it moved out of @backstage/frontend-plugin-api -- see that package's
 * CHANGELOG), and it is documented as "limited to use by the app plugin",
 * which is why this module declares `pluginId: 'app'` exactly like the nav
 * module beside it.
 */
import { createFrontendModule } from '@backstage/frontend-plugin-api';
import { ThemeBlueprint } from '@backstage/plugin-app-react';
import {
  createUnifiedTheme,
  genPageTheme,
  palettes,
  shapes,
  UnifiedThemeProvider,
} from '@backstage/theme';

const NAVY_900 = '#0a2342';
const NAVY_700 = '#123963';
const BLUE_600 = '#1b5fa8';
const BLUE_050 = '#eff4fa';
const PAPER = '#ffffff';
const INK = '#0e1f33';

// Banner gradients. Every page type gets one, so nothing falls back to the
// scaffold's magenta.
const navyBanner = genPageTheme({
  colors: [NAVY_900, BLUE_600],
  shape: shapes.wave,
});
const navyBanner2 = genPageTheme({
  colors: [NAVY_700, BLUE_600],
  shape: shapes.wave2,
});
const navyRound = genPageTheme({
  colors: [NAVY_900, NAVY_700],
  shape: shapes.round,
});

const pageTheme = {
  home: navyBanner,
  app: navyBanner,
  apis: navyBanner2,
  documentation: navyBanner2,
  service: navyBanner,
  website: navyBanner2,
  library: navyRound,
  other: navyRound,
  tool: navyBanner,
};

/* Component overrides both themes share, parameterised by surface colour.
 *
 * Written once because the two themes had drifted: the dark theme carried a
 * table fix the light one did not, which is how a palette ends up with two
 * different ideas of what a table row is.
 *
 * What is NOT here any more, and why. An earlier version set
 * `white-space: nowrap; max-width: 22rem` on every table cell, to stop the
 * catalog breaking "policy-decision-service" into "policy-decisio n-servic e".
 * It stopped that and caused something worse: eight columns each free to grow
 * to 22rem made the table wider than the card holding it, so the catalog grew
 * a horizontal scrollbar straight across its middle and sliced the tags column
 * mid-word. A fix whose failure mode is uglier than the defect is not a fix.
 *
 * The narrow version is the correct one: the mangling was never about wrapping,
 * it was about breaking *inside a word*, so only that is turned off. A long
 * hyphenated name now wraps at its hyphens, like prose, and the table stays
 * inside its card at any width.
 */
const componentOverrides = (c: {
  page: string;
  border: string;
  divider: string;
  thumb: string;
  thumbHover: string;
  hover: string;
}) => ({
  MuiCssBaseline: {
    styleOverrides: {
      // The browser's default scrollbar is painted for a light page, and on
      // the dark theme it landed as a bright white bar across the catalog.
      '*::-webkit-scrollbar': { width: 10, height: 10 },
      '*::-webkit-scrollbar-track': { background: c.page },
      '*::-webkit-scrollbar-thumb': {
        background: c.thumb,
        borderRadius: 8,
        border: `2px solid ${c.page}`,
      },
      '*::-webkit-scrollbar-thumb:hover': { background: c.thumbHover },
    },
  },
  MuiPaper: {
    styleOverrides: {
      root: {
        // `backgroundImage: none` kills MUI's elevation overlay, which tints
        // dark surfaces a flat grey and was fighting the navy underneath it.
        // A 1px border does the separating instead -- with only ~6 points of
        // lightness between page and card, an edge is what makes a card read
        // as a card.
        backgroundImage: 'none',
        border: `1px solid ${c.border}`,
      },
    },
  },
  MuiTableCell: {
    styleOverrides: {
      root: {
        wordBreak: 'normal' as const,
        overflowWrap: 'normal' as const,
        borderBottomColor: c.divider,
      },
    },
  },
  MuiTableRow: {
    styleOverrides: {
      root: {
        // Zebra striping off: two near-identical slates alternating down a
        // table read as banding rather than as rows. Hover says where you are.
        backgroundColor: 'transparent',
        '&:hover': { backgroundColor: c.hover },
      },
    },
  },
});

const northstarLight = createUnifiedTheme({
  fontFamily:
    "'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif",
  palette: {
    ...palettes.light,
    primary: { main: BLUE_600, dark: NAVY_700, light: '#4a8ad0' },
    secondary: { main: NAVY_700 },
    // A refusal is the platform working, so `warning` carries the ochre the
    // other two surfaces use for it and `error` stays for genuine faults.
    warning: { main: '#8a5a00' },
    error: { main: '#a32a22' },
    success: { main: '#0f6b4f' },
    info: { main: BLUE_600 },
    background: { default: BLUE_050, paper: PAPER },
    text: { primary: INK, secondary: '#4c637e' },
    navigation: {
      background: NAVY_900,
      indicator: '#2b74c0',
      color: '#d6e4f2',
      selectedColor: '#ffffff',
      navItem: { hoverBackground: NAVY_700 },
    },
  },
  pageTheme,
  components: componentOverrides({
    page: '#f4f7fb',
    border: '#d5e0ec',
    divider: '#e2e9f2',
    thumb: '#b4c4d6',
    thumbHover: '#94a8c0',
    hover: '#eef3f9',
  }),
});

/* The dark surfaces, all on ONE hue.
 *
 * The first version of this theme mixed them: navigation sat at hue 208.4,
 * the page at 208.6, cards at 210.0, while the brand navy this is all
 * supposed to be is 213.2. Four surfaces, four hues, none of them the
 * brand's -- which is what "the grey" was. A few degrees of hue drift
 * between large adjacent fields does not read as a palette, it reads as
 * dirt, and no single colour in it looks wrong on its own.
 *
 * So every surface below is hue 213 -- the brand navy's own hue, measured
 * from #0a2342 rather than chosen -- at deliberate lightness steps, with
 * saturation easing off as the surfaces get lighter so the pale end stays
 * neutral instead of turning into blue paint:
 *
 *   navigation   hsl(213, 50%,  5.0%)  #060c13   darkest, anchors the rail
 *   page         hsl(213, 45%,  7.5%)  #0b121c
 *   paper/card   hsl(213, 38%, 14.0%)  #162231   + a 1px border, see below
 *   raised/hover hsl(213, 32%, 18.5%)  #202e3e
 *   border       hsl(213, 26%, 22.5%)  #2a3848
 *   divider      hsl(213, 24%, 19.0%)  #252f3c
 *   text 2nd     hsl(213, 22%, 72.0%)  #a8b6c7
 *   text 1st     hsl(213, 30%, 94.0%)  #ebeff4   near-white, faint blue cast
 *
 * The page/card step was 4.0 points of lightness in the first version and is
 * 6.5 here. At 4.0 the big "All Components" panel did not read as a panel at
 * all -- it floated on a ground almost its own colour -- so the card also
 * takes a 1px border now. Secondary text went up 6 points for the same
 * reason: the description column was dimmer than the rows it described.
 *
 * The blue stays: #65a1e2 is the light theme's #1b5fa8 lifted to carry on a
 * dark ground, same hue family, so a link is recognisably the same blue in
 * both themes.
 */
const DARK_NAV = '#060c13';
const DARK_PAGE = '#0b121c';
const DARK_PAPER = '#162231';
const DARK_RAISED = '#202e3e';
const DARK_BORDER = '#2a3848';
const DARK_DIVIDER = '#252f3c';
const DARK_TEXT = '#ebeff4';
const DARK_TEXT_2 = '#a8b6c7';
const DARK_BLUE = '#65a1e2';

const northstarDark = createUnifiedTheme({
  fontFamily:
    "'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif",
  palette: {
    ...palettes.dark,
    primary: { main: DARK_BLUE, dark: '#3f82c9', light: '#84b6eb' },
    secondary: { main: '#84b6eb' },
    // Ochre reads as mud on a navy ground, so the dark theme's refusal colour
    // is the same hue family lifted into amber. It still must not be red --
    // a refusal is the platform working (see the light theme above).
    warning: { main: '#e0a458' },
    error: { main: '#ef8279' },
    success: { main: '#5ac2a0' },
    info: { main: DARK_BLUE },
    background: { default: DARK_PAGE, paper: DARK_PAPER },
    text: { primary: DARK_TEXT, secondary: DARK_TEXT_2 },
    divider: DARK_DIVIDER,
    navigation: {
      background: DARK_NAV,
      indicator: '#65a1e2',
      color: DARK_TEXT_2,
      selectedColor: '#ffffff',
      navItem: { hoverBackground: DARK_RAISED },
    },
  },
  pageTheme,
  components: componentOverrides({
    page: DARK_PAGE,
    border: DARK_BORDER,
    divider: DARK_DIVIDER,
    thumb: '#3c4b5d',
    thumbHover: '#4d5f74',
    hover: DARK_RAISED,
  }),
});

// `name: 'light'` / `name: 'dark'`, NOT 'northstar-light'. A ThemeBlueprint's
// name becomes the extension id (`app/theme:light`), and the app ships its own
// extensions at exactly those two ids. A theme registered under a new name is
// *added* beside the built-ins rather than replacing them -- which is what the
// first version of this file did, and the result passed every check available
// to it: the module loaded, the extension registered, the theme appeared in the
// picker, and the app still rendered Backstage's default green banner because
// nothing had selected the new theme. Measured 2026-09-15 from a screenshot of
// the running pod, which was the only thing that could have shown it.
//
// Taking the built-in ids overrides those extensions, so the default theme IS
// this one and no picker interaction is needed on stage.
const lightTheme = ThemeBlueprint.make({
  name: 'light',
  params: {
    theme: {
      id: 'light',
      title: 'Northstar',
      variant: 'light',
      Provider: ({ children }) => (
        <UnifiedThemeProvider theme={northstarLight}>{children}</UnifiedThemeProvider>
      ),
    },
  },
});

const darkTheme = ThemeBlueprint.make({
  name: 'dark',
  params: {
    theme: {
      id: 'dark',
      title: 'Northstar dark',
      variant: 'dark',
      Provider: ({ children }) => (
        <UnifiedThemeProvider theme={northstarDark}>{children}</UnifiedThemeProvider>
      ),
    },
  },
});

export const themeModule = createFrontendModule({
  pluginId: 'app',
  extensions: [lightTheme, darkTheme],
});
