/* The TechDocs half of mermaid rendering. See Mermaid.tsx for why mermaid at
 * all, and for why the diagrams in docs/ are raw `<pre class="mermaid">`
 * blocks rather than ```mermaid fences -- which is what makes the selector
 * below the right one.
 *
 * TechDocs renders the MkDocs HTML into a SHADOW ROOT, which is why this is an
 * addon and not a stylesheet or a `document.querySelector` somewhere: nothing
 * outside the shadow boundary can see or style those elements.
 * `useShadowRootElements` is the supported way in, and it re-runs as the
 * reader navigates between pages of a docs site -- which matters here, because
 * a TechDocs page transition does not remount the app.
 */
import { useEffect } from 'react';
import { useTheme } from '@material-ui/core/styles';
import mermaid from 'mermaid';
import {
  TechDocsAddonLocations,
  useShadowRootElements,
} from '@backstage/plugin-techdocs-react';
import { AddonBlueprint } from '@backstage/plugin-techdocs-react/alpha';
import { createFrontendModule } from '@backstage/frontend-plugin-api';
import { configureMermaid, nextMermaidId } from './Mermaid';

const MermaidAddon = () => {
  const theme = useTheme();
  const blocks = useShadowRootElements<HTMLPreElement>(['pre.mermaid']);

  const { palette } = theme;
  const fontFamily = String(theme.typography.fontFamily ?? 'sans-serif');
  const errorColour = palette.error.main;

  useEffect(() => {
    let live = true;
    configureMermaid(palette, fontFamily);

    blocks.forEach(async pre => {
      // The source is stashed on the element the first time it is seen. This
      // effect re-runs when the theme changes, and by then the element's
      // textContent is the SVG it was replaced with -- reading it again would
      // feed mermaid its own output.
      const source = pre.dataset.mermaidSource ?? pre.textContent ?? '';
      if (!source.trim()) return;
      pre.dataset.mermaidSource = source;

      try {
        const { svg } = await mermaid.render(
          `northstar-techdocs-mermaid-${nextMermaidId()}`,
          source,
        );
        if (!live) return;
        pre.innerHTML = svg;
        // The MkDocs theme styles a `pre` as a code block. It is a picture
        // now.
        pre.style.background = 'none';
        pre.style.border = 'none';
        pre.style.textAlign = 'center';
        pre.style.overflowX = 'auto';
        const el = pre.querySelector('svg');
        if (el) {
          el.style.maxWidth = '100%';
          el.style.height = 'auto';
        }
      } catch (e) {
        if (!live) return;
        // Loud, for the reason the sibling component gives at length: a
        // diagram that silently fails to draw leaves a page that still loads,
        // a pod that stays Ready, and a suite that stays green.
        pre.textContent = `This diagram did not parse: ${
          e instanceof Error ? e.message : String(e)
        }\n\n${source}`;
        pre.style.color = errorColour;
      }
    });

    return () => {
      live = false;
    };
  }, [blocks, palette, fontFamily, errorColour]);

  return null;
};

const mermaidAddon = AddonBlueprint.make({
  name: 'mermaid',
  params: {
    name: 'Mermaid',
    // Content, not a sidebar or the settings menu: this addon does not draw
    // anything of its own (it returns null), it rewrites elements inside the
    // rendered page, so it has to be mounted where that page is.
    location: TechDocsAddonLocations.Content,
    component: MermaidAddon,
  },
});

export const mermaidModule = createFrontendModule({
  pluginId: 'techdocs',
  extensions: [mermaidAddon],
});
