/* Mermaid rendering, for both places this portal shows a diagram.
 *
 * Before this, every diagram in the repo was ASCII art in a ```text fence.
 * That has one real virtue -- it is readable in a terminal, in `git diff`, and
 * in any editor -- and three costs that got paid repeatedly:
 *
 *   1. It is laid out by hand. Column alignment is maintained with the space
 *      bar, so an added node means re-aligning every row beneath it, and a
 *      renamed service means re-aligning the whole picture.
 *   2. It does not reflow. A `pre` wider than its container is CLIPPED, not
 *      scrolled, which is how the home page shipped a call-path diagram
 *      missing its right-hand column with no scrollbar to say so.
 *   3. It cannot be read by anything. A box-drawing diagram is a picture of a
 *      graph, not a graph -- nothing can check that it still matches the
 *      catalog, the code, or the other eleven diagrams drawn of the same
 *      system.
 *
 * Mermaid fixes 1 and 2 outright: the layout is computed, and the output is an
 * SVG that scales to its box. It makes 3 possible rather than solving it --
 * `graph LR; a --> b` is at least parseable text -- and tests/unit/
 * test_mermaid_diagrams.py takes the first bite by parsing every diagram in
 * the tree.
 *
 * Two consumers, one renderer:
 *
 *   - TechDocs pages, via the addon in MermaidAddon.tsx. Every diagram in
 *     docs/ is written as a raw `<pre class="mermaid">` block rather than the
 *     idiomatic ```mermaid fence, and that is forced rather than chosen: a
 *     fence needs MkDocs to be told about it, which needs a `!!python/name:`
 *     tag in mkdocs.yml, and Backstage parses mkdocs.yml ITSELF before MkDocs
 *     ever sees it -- refusing every Python YAML tag outside a hard-coded
 *     allowlist of three, and 404ing the whole site. A raw HTML block needs no
 *     configuration at all and passes through python-markdown verbatim. The
 *     guard is test_mermaid_diagrams.py, whose docstring tells the story.
 *
 *   - The home page, via `<Mermaid>`. That card renders through
 *     `MarkdownContent`, which handles no blocks of either kind, so it takes
 *     the component directly.
 */
import { useEffect, useState } from 'react';
import { makeStyles, useTheme } from '@material-ui/core/styles';
import mermaid from 'mermaid';

/* One counter for the whole app. Mermaid requires a DOM id per render and
 * reuses it as an SVG element id, so two diagrams sharing one would collide in
 * the same document -- and on a TechDocs page there are up to four at once. */
let nextId = 0;
export const nextMermaidId = () => nextId++;

const useStyles = makeStyles(theme => ({
  root: {
    // The SVG carries its own intrinsic size. Letting it fill the width and
    // keep its aspect ratio is what makes the diagram reflow in a narrow card
    // instead of clipping, which was the whole complaint against the ASCII it
    // replaces.
    '& svg': {
      width: '100%',
      height: 'auto',
      maxWidth: '100%',
      display: 'block',
    },
    margin: `${theme.spacing(2)}px 0`,
  },
  error: {
    // Loud on purpose. A diagram that fails to parse is the exact shape of
    // defect this repo keeps paying for: the page still loads, the pod stays
    // Ready, and the only symptom is a blank rectangle nobody mentions. The
    // source is printed underneath so the failure is diagnosable from the
    // screen it happened on.
    border: `1px solid ${theme.palette.error.main}`,
    borderRadius: theme.shape.borderRadius,
    padding: theme.spacing(2),
    color: theme.palette.error.main,
    fontFamily: 'monospace',
    fontSize: '0.85em',
    whiteSpace: 'pre-wrap',
    overflowX: 'auto',
  },
}));

/* Mermaid's theme is global state, set by `initialize`, and it is read at
 * render time rather than captured -- so this is called immediately before
 * every render rather than once at module load. Backstage's theme can change
 * under the page (the user setting is a toggle, not a reload), and a diagram
 * drawn with the light palette on a dark page is unreadable. */
export const configureMermaid = (dark: boolean, fontFamily: string) =>
  mermaid.initialize({
    startOnLoad: false,
    theme: dark ? 'dark' : 'default',
    fontFamily,
    // 'strict' would strip the labels: several of these diagrams put a
    // service name in backticks or an arrow in a label. 'loose' allows the
    // HTML that produces. Every diagram rendered here is authored in this
    // repo and baked into the image -- there is no user-supplied mermaid
    // anywhere in this app -- so the usual argument against 'loose' (someone
    // else's markdown) does not apply.
    securityLevel: 'loose',
    flowchart: { useMaxWidth: true, htmlLabels: true },
    sequence: { useMaxWidth: true },
  });

export const useMermaidSvg = (chart: string) => {
  const theme = useTheme();
  const [svg, setSvg] = useState<string>('');
  const [error, setError] = useState<string>('');

  const dark = theme.palette.type === 'dark';
  const fontFamily = String(theme.typography.fontFamily ?? 'sans-serif');

  useEffect(() => {
    let live = true;
    configureMermaid(dark, fontFamily);
    mermaid
      .render(`northstar-mermaid-${nextMermaidId()}`, chart)
      .then(result => {
        if (!live) return;
        setSvg(result.svg);
        setError('');
      })
      .catch((e: unknown) => {
        if (!live) return;
        setSvg('');
        setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      live = false;
    };
  }, [chart, dark, fontFamily]);

  return { svg, error };
};

/** A mermaid diagram, rendered wherever a React component fits. */
export const Mermaid = ({ chart }: { chart: string }) => {
  const classes = useStyles();
  const { svg, error } = useMermaidSvg(chart);

  if (error) {
    return (
      <div className={classes.error}>
        {`This diagram did not parse: ${error}\n\n${chart}`}
      </div>
    );
  }
  // eslint-disable-next-line react/no-danger
  return <div className={classes.root} dangerouslySetInnerHTML={{ __html: svg }} />;
};
