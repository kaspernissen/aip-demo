/* The Northstar mark, collapsed-sidebar size.
 *
 * The same eight-point star the claimant portal and the reviewer console
 * carry (apps/customer-ui/src/app/layout.tsx, apps/reviewer-ui/render.py's
 * _MARK) -- long vertical and horizontal rays, short diagonals. One company,
 * three surfaces, one mark; the whole point of this milestone is that
 * Backstage is Northstar's internal view of its own platform, not a separate
 * product bolted on.
 */
import { makeStyles } from '@material-ui/core';

const useStyles = makeStyles({
  svg: {
    width: 'auto',
    height: 28,
  },
  outer: {
    fill: '#ffffff',
  },
  inner: {
    fill: '#2b74c0',
  },
});

export const LogoIcon = () => {
  const classes = useStyles();

  return (
    <svg
      className={classes.svg}
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      role="img"
      aria-label="Northstar"
    >
      <path
        className={classes.outer}
        d="M16 0.5 L18.4 13.6 L31.5 16 L18.4 18.4 L16 31.5 L13.6 18.4 L0.5 16 L13.6 13.6 Z"
      />
      <path
        className={classes.inner}
        d="M16 6.2 L17.3 14.7 L25.8 16 L17.3 17.3 L16 25.8 L14.7 17.3 L6.2 16 L14.7 14.7 Z"
      />
    </svg>
  );
};
