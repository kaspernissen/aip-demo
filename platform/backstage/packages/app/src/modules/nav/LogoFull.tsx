/* The Northstar lockup for the open sidebar: the mark, the name, and what
 * this particular surface is.
 *
 * "Internal Platform" is the label, not "Backstage". An engineer at this
 * fictional insurer opens their platform portal; that it is built on
 * Backstage is an implementation detail, exactly as the design says the
 * `FeatureFlag` being the platform API makes Backstage "just the view".
 *
 * Text is real SVG text rather than a traced wordmark path so it inherits
 * the theme's own font stack and stays legible at any zoom.
 */
import { makeStyles } from '@material-ui/core';

const useStyles = makeStyles({
  svg: {
    width: 'auto',
    height: 34,
  },
  outer: {
    fill: '#ffffff',
  },
  inner: {
    fill: '#2b74c0',
  },
  word: {
    fill: '#ffffff',
    fontFamily: "'Spectral', Georgia, serif",
    fontSize: 20,
    fontWeight: 600,
    letterSpacing: '0.01em',
  },
  sub: {
    fill: '#8fb8dd',
    fontFamily:
      "'IBM Plex Sans', -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif",
    fontSize: 8,
    fontWeight: 600,
    letterSpacing: '0.18em',
  },
});

export const LogoFull = () => {
  const classes = useStyles();

  return (
    <svg
      className={classes.svg}
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 210 36"
      role="img"
      aria-label="Northstar Internal Platform"
    >
      <g transform="translate(0,2)">
        <path
          className={classes.outer}
          d="M16 0.5 L18.4 13.6 L31.5 16 L18.4 18.4 L16 31.5 L13.6 18.4 L0.5 16 L13.6 13.6 Z"
        />
        <path
          className={classes.inner}
          d="M16 6.2 L17.3 14.7 L25.8 16 L17.3 17.3 L16 25.8 L14.7 17.3 L6.2 16 L14.7 14.7 Z"
        />
      </g>
      <text className={classes.word} x="42" y="19">
        Northstar
      </text>
      <text className={classes.sub} x="43" y="30">
        INTERNAL PLATFORM
      </text>
    </svg>
  );
};
