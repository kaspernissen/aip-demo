import "./globals.css";

export const metadata = { title: "Northstar Insurance — File a Claim" };

/* Fonts are loaded from Google Fonts with a real fallback stack behind each
 * family (Spectral -> Georgia, IBM Plex Sans -> system sans, IBM Plex Mono ->
 * the platform mono). `display=swap` means text paints immediately in the
 * fallback, so a venue with no internet loses the typography and nothing
 * else -- the page never blocks on the request, and the Playwright suites
 * that drive this form are unaffected either way.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&family=Spectral:wght@500;600&display=swap"
        />
      </head>
      <body>
        <header className="masthead">
          <div className="masthead-inner">
            <NorthstarMark />
            <div className="wordmark">
              Northstar
              <span>Insurance</span>
            </div>
            <div className="masthead-meta">
              Claims intake
              <br />
              Motor &middot; vehicle damage
            </div>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}

function NorthstarMark() {
  return (
    <svg
      className="mark"
      width="34"
      height="34"
      viewBox="0 0 32 32"
      aria-hidden="true"
      focusable="false"
    >
      {/* an eight-point star: long vertical and horizontal rays, short
          diagonals — the "north star" the company is named for */}
      <path
        d="M16 0.5 L18.4 13.6 L31.5 16 L18.4 18.4 L16 31.5 L13.6 18.4 L0.5 16 L13.6 13.6 Z"
        fill="#ffffff"
      />
      <path
        d="M16 6.2 L17.3 14.7 L25.8 16 L17.3 17.3 L16 25.8 L14.7 17.3 L6.2 16 L14.7 14.7 Z"
        fill="#2b74c0"
      />
    </svg>
  );
}
