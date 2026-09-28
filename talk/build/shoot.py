#!/usr/bin/env python3
"""Screenshot slides to export/slides/NN.png, and build export/contact-sheet.png.

Needs Playwright + Chromium. The aip-demo venv has both:
  ../.venv/bin/python build/shoot.py          # all slides
  ../.venv/bin/python build/shoot.py 12-18    # a range
  ../.venv/bin/python build/shoot.py --deck preview-30-act3a.html     # a fragment preview -> export/preview-30-act3a/
It serves the deck over a local HTTP server on a free port, so fonts and assets load
exactly as they do under start.sh.
"""
import functools
import http.server
import pathlib
import re
import socketserver
import sys
import threading

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "export" / "slides"


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=str(ROOT))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    args = sys.argv[1:]
    deck, out = "index.html", OUT
    if "--deck" in args:
        i = args.index("--deck")
        deck = args[i + 1]
        del args[i:i + 2]
        out = ROOT / "export" / pathlib.Path(deck).stem
    total = len(re.findall(r"<section\b", (ROOT / deck).read_text()))
    lo, hi = 1, total
    if args:
        a = args[0].split("-")
        lo, hi = int(a[0]), int(a[-1])
    out.mkdir(parents=True, exist_ok=True)
    httpd = serve()
    port = httpd.server_address[1]
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        # load once, then drive deck-stage directly: ?slide=N races the index it restores from localStorage
        pg.goto(f"http://127.0.0.1:{port}/{deck}", wait_until="networkidle")
        pg.evaluate("document.fonts.ready")
        # hide the deck's nav overlay (it lives in deck-stage's shadow root)
        pg.evaluate("""() => { const o = document.querySelector('deck-stage')?.shadowRoot?.querySelector('.overlay');
                               if (o) o.style.display = 'none'; }""")
        pg.wait_for_timeout(800)
        for n in range(lo, hi + 1):
            pg.evaluate(f"document.querySelector('deck-stage').goTo({n - 1})")
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(out / f"{n:02d}.png"))
        if deck != "index.html":
            b.close()
            httpd.shutdown()
            print(f"shot {deck} slides {lo}-{hi} -> {out}")
            return
        # contact sheet: a grid of every exported slide, rendered by the browser itself
        shots = sorted(OUT.glob("*.png"))
        cells = "".join(
            f'<figure><img src="{s.as_uri()}"><figcaption>{s.stem}</figcaption></figure>' for s in shots)
        sheet = ROOT / "export" / "contact-sheet.html"
        sheet.write_text(
            "<style>body{margin:0;background:#111;display:grid;grid-template-columns:repeat(6,320px);gap:10px;padding:10px}"
            "figure{margin:0}img{width:320px;display:block}figcaption{color:#aaa;font:12px monospace}</style>" + cells)
        rows = -(-len(shots) // 6)
        pg.set_viewport_size({"width": 6 * 330 + 10, "height": rows * 205 + 20})
        pg.goto(sheet.as_uri())
        pg.screenshot(path=str(ROOT / "export" / "contact-sheet.png"), full_page=True)
        b.close()
    httpd.shutdown()
    print(f"shot slides {lo}-{hi} -> {OUT}; contact sheet -> export/contact-sheet.png")


if __name__ == "__main__":
    main()
