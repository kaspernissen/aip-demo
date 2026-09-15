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
  components: {
    // The catalog table breaks entity names mid-word when the column is
    // narrow -- "policy-decision-service" renders as "policy-decisio n-servic
    // e" at 1440px once the tags column is populated, because a hyphenated
    // name with no spaces will break anywhere the wrapper allows. Measured
    // from a screenshot of the running pod; it is the first thing on screen
    // in the demo's last beat.
    //
    // `nowrap` on the cell rather than a width on the column: the column set
    // belongs to the catalog page extension, and overriding that to change a
    // wrap is a far larger change than the defect warrants. Cells that then
    // do not fit get an ellipsis instead of a mangled word, which is the
    // better failure of the two.
    MuiTableCell: {
      styleOverrides: {
        root: {
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          maxWidth: '22rem',
        },
      },
    },
  },
});

const northstarDark = createUnifiedTheme({
  fontFamily:
    "'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif",
  palette: {
    ...palettes.dark,
    primary: { main: '#6aa9e0' },
    secondary: { main: '#8fb8dd' },
    warning: { main: '#e7a94a' },
    error: { main: '#ef8279' },
    success: { main: '#5ac2a0' },
    info: { main: '#6aa9e0' },
    background: { default: '#0b1620', paper: '#122232' },
    navigation: {
      background: '#07111a',
      indicator: '#2b74c0',
      color: '#b9cde0',
      selectedColor: '#ffffff',
      navItem: { hoverBackground: '#142739' },
    },
  },
  pageTheme,
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
