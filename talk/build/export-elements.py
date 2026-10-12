#!/usr/bin/env python3
"""Export the deck as PNG pieces for rebuilding it in Google Slides.

    python3 build/export-elements.py          # all slides
    python3 build/export-elements.py 7-12     # a range

Needs Playwright and Pillow (the system python3 here has both; the repo venv lacks Pillow).
Writes export/google-slides/ (git-ignored), every PNG at 2x (3840 x 2160 a slide):
  slides/NN.png              the whole slide with its text, reference only
  backgrounds/NN.png         the empty slide: ground, diagonal, footer axis; no text, no elements
  layouts/NN.png             every element in place, no text, ground included
  layouts-alpha/NN.png       the same on transparency (no slide ground)
  elements/NN/*.png          each element that paints, ALONE on transparency, text-free:
                             cards, panels, figures, images, screenshots, code panels, icons
  manifest.json              every element's and every text run's position in 1920 x 1080
  COPY.md                    every text run per slide, with font, size, colour, position

Each element is shot in isolation: everything else on the slide is hidden, so a crop
never carries a neighbour's pixels. Labels inside an inline <svg> are artwork and stay;
all other text is painted transparent (not removed, so every box keeps its real size).
"""
import functools
import http.server
import json
import pathlib
import re
import socketserver
import sys
import threading

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "export" / "google-slides"
MIN = 12  # px: smaller than this is a hairline or a dot, it travels inside its group

CLEAR = """() => {
  const ds = document.querySelector('deck-stage');
  const st = document.createElement('style');
  st.textContent = ':host,.canvas{background:transparent!important}';
  ds.shadowRoot.appendChild(st);
  const o = ds.shadowRoot.querySelector('.overlay'); if (o) o.style.display = 'none';
  const g = document.createElement('style');
  g.id = 'gs-base';
  g.textContent = 'html,body{background:transparent!important}';
  document.head.appendChild(g);
}"""
STRIP = """section.slide, section.slide *:not(svg):not(svg *)
  {color:transparent!important;-webkit-text-fill-color:transparent!important;text-shadow:none!important}
  section.slide .page-foot{display:none!important}
  section.slide .ph-duotone, section.slide .ph-duotone::before, section.slide .ph-duotone::after
  {-webkit-text-fill-color:currentColor!important}
  section.slide i.ph-duotone{color:var(--ocean-teal)!important}
  section.slide .x10-eq-loop i.ph-duotone, section.slide .x31-doors .shut i.ph-duotone{color:var(--ocean-deep)!important}
  section.slide .x31-doors .open i.ph-duotone{color:var(--color-neutral-500)!important}"""
ALPHA = "section.slide{background:transparent!important}"
EMPTY = "section.slide *{visibility:hidden!important}"
ISOLATE = """section.slide{background:transparent!important}
  section.slide::before{display:none!important}
  section.slide *{visibility:hidden!important}
  section.slide .gs-keep,section.slide .gs-keep *{visibility:visible!important}"""

# Every element on the active slide that paints something of its own.
CANDIDATES = """(min) => {
  document.querySelectorAll('[data-gs-id]').forEach(e => delete e.dataset.gsId);
  const sec = document.querySelector('section.slide[data-deck-active]') ||
              [...document.querySelectorAll('section.slide')].find(s => s.offsetParent !== null);
  const r0 = sec.getBoundingClientRect(), k = 1920 / r0.width;
  const out = [];
  const paints = el => {
    const t = el.tagName.toLowerCase();
    if (['svg','img','canvas','video','table','pre','figure','image-slot'].includes(t)) return t;
    if (el.closest('svg')) return null;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return null;
    const bg = cs.backgroundColor, bi = cs.backgroundImage;
    if (bi && bi !== 'none') return 'image';
    if (bg && !/rgba\\(.*,\\s*0\\)$/.test(bg) && bg !== 'transparent') return 'box';
    if (['Top','Right','Bottom','Left'].some(s => parseFloat(cs['border'+s+'Width']) > 0 &&
        !/rgba\\(.*,\\s*0\\)$/.test(cs['border'+s+'Color']))) return 'outline';
    if (cs.boxShadow && cs.boxShadow !== 'none') return 'box';
    if (el.tagName === 'I' && /\\bph-/.test(el.className)) return 'icon';
    return null;
  };
  const walk = (el, depth) => {
    for (const c of el.children) {
      const kind = paints(c);
      const r = c.getBoundingClientRect();
      if (kind && r.width >= min && r.height >= min) {
        c.dataset.gsId = out.length;
        out.push({id: out.length, kind, tag: c.tagName.toLowerCase(), cls: String(c.className.baseVal ?? c.className).slice(0, 60),
                  x: Math.round((r.left - r0.left) * k), y: Math.round((r.top - r0.top) * k),
                  w: Math.round(r.width * k), h: Math.round(r.height * k), depth});
      }
      if (c.tagName.toLowerCase() !== 'svg') walk(c, depth + 1);
    }
  };
  walk(sec, 0);
  return out;
}"""

TEXT = """() => {
  const sec = document.querySelector('section.slide[data-deck-active]') ||
              [...document.querySelectorAll('section.slide')].find(s => s.offsetParent !== null);
  const r0 = sec.getBoundingClientRect(), k = 1920 / r0.width;
  const hex = c => { const m = c.match(/\\d+(\\.\\d+)?/g); if (!m) return c;
    const [r,g,b,a] = m.map(Number); const h = '#' + [r,g,b].map(v => v.toString(16).padStart(2,'0')).join('').toUpperCase();
    return (a !== undefined && a < 1) ? h + ' @' + Math.round(a*100) + '%' : h; };
  const runs = [], seen = new Set();
  const w = document.createTreeWalker(sec, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    const n = w.currentNode, el = n.parentElement;
    if (!n.textContent.trim() || el.closest('svg') || seen.has(el)) continue;
    if (el.closest('.page-foot')) continue;
    seen.add(el);
    const cs = getComputedStyle(el), r = el.getBoundingClientRect();
    if (!r.width) continue;
    runs.push({t: el.innerText.replace(/\\s+/g, ' ').trim(), font: cs.fontFamily.split(',')[0].replace(/"/g, ''),
               size: Math.round(parseFloat(cs.fontSize) * k), weight: cs.fontWeight, color: hex(cs.color),
               upper: cs.textTransform === 'uppercase',
               x: Math.round((r.left - r0.left) * k), y: Math.round((r.top - r0.top) * k),
               w: Math.round(r.width * k), h: Math.round(r.height * k)});
  }
  return runs;
}"""


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    httpd = socketserver.TCPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def style(pg, css):
    pg.evaluate("""css => { let s = document.getElementById('gs-mode');
      if (!s) { s = document.createElement('style'); s.id = 'gs-mode'; document.head.appendChild(s); }
      s.textContent = css; }""", css)


def main():
    html = (ROOT / "index.html").read_text()
    labels = re.findall(r'<section\b[^>]*data-label="([^"]*)"', html)
    lo, hi = 1, len(labels)
    if sys.argv[1:]:
        a = sys.argv[1].split("-")
        lo, hi = int(a[0]), int(a[-1])
    for d in ("slides", "backgrounds", "layouts", "layouts-alpha", "elements"):
        (OUT / d).mkdir(parents=True, exist_ok=True)
    mpath = OUT / "manifest.json"
    manifest = {m["n"]: m for m in json.loads(mpath.read_text())} if mpath.exists() and sys.argv[1:] else {}

    httpd = serve()
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=2)
        pg.goto(f"http://127.0.0.1:{httpd.server_address[1]}/index.html", wait_until="networkidle")
        pg.evaluate("document.fonts.ready")
        pg.evaluate(CLEAR)
        pg.wait_for_timeout(800)
        for n in range(lo, hi + 1):
            pg.evaluate(f"document.querySelector('deck-stage').goTo({n - 1})")
            pg.wait_for_timeout(450)
            tag = f"{n:02d}"
            style(pg, "")
            pg.screenshot(path=str(OUT / "slides" / f"{tag}.png"))
            text = pg.evaluate(TEXT)
            style(pg, EMPTY)
            pg.screenshot(path=str(OUT / "backgrounds" / f"{tag}.png"))
            style(pg, STRIP)
            pg.screenshot(path=str(OUT / "layouts" / f"{tag}.png"))
            style(pg, STRIP + ALPHA)
            pg.screenshot(path=str(OUT / "layouts-alpha" / f"{tag}.png"), omit_background=True)

            d = OUT / "elements" / tag
            if d.exists():
                for f in d.glob("*.png"):
                    f.unlink()
            d.mkdir(exist_ok=True)
            kept = []
            for e in pg.evaluate(CANDIDATES, MIN):
                style(pg, STRIP + ISOLATE)
                pg.evaluate("id => { document.querySelectorAll('.gs-keep').forEach(x => x.classList.remove('gs-keep'));"
                            "document.querySelector(`[data-gs-id=\"${id}\"]`).classList.add('gs-keep'); }", e["id"])
                x, y = max(0, e["x"]), max(0, e["y"])
                w, h = min(1920 - x, e["w"]), min(1080 - y, e["h"])
                if w < MIN or h < MIN:
                    continue
                fn = f"{len(kept) + 1:02d}-{e['kind']}.png"
                pg.screenshot(path=str(d / fn), omit_background=True,
                              clip={"x": x, "y": y, "width": w, "height": h})
                # trim to ink, and move the recorded box with it
                im = Image.open(d / fn)
                bb = im.getbbox()
                if not bb:
                    (d / fn).unlink()
                    continue
                x, y = x + bb[0] // 2, y + bb[1] // 2
                w, h = (bb[2] - bb[0] + 1) // 2, (bb[3] - bb[1] + 1) // 2
                # a wrapper that paints exactly what its child paints (figure > svg) ships once
                if any(abs(k["x"] - x) <= 2 and abs(k["y"] - y) <= 2 and abs(k["w"] - w) <= 2
                       and abs(k["h"] - h) <= 2 for k in kept):
                    (d / fn).unlink()
                    continue
                im.crop(bb).save(d / fn)
                kept.append({"file": f"elements/{tag}/{fn}", "kind": e["kind"], "tag": e["tag"],
                             "x": x, "y": y, "w": w, "h": h, "depth": e["depth"]})
            pg.evaluate("() => document.querySelectorAll('.gs-keep').forEach(x => x.classList.remove('gs-keep'))")
            manifest[n] = {"n": n, "label": labels[n - 1], "slide": f"slides/{tag}.png",
                           "background": f"backgrounds/{tag}.png", "layout": f"layouts/{tag}.png",
                           "layout_alpha": f"layouts-alpha/{tag}.png",
                           "elements": kept, "text": text}
            print(f"  {tag} {labels[n - 1][:50]}: {len(kept)} elements, {len(text)} text runs", flush=True)
        b.close()
    httpd.shutdown()

    ms = [manifest[k] for k in sorted(manifest)]
    mpath.write_text(json.dumps(ms, indent=1, ensure_ascii=False))
    lines = ["# Copy — every text run, per slide", "",
             "Positions and sizes are px in the 1920 × 1080 slide; with Slides page setup at",
             "13.333 × 7.5 in, halve every number to get points.", ""]
    for m in ms:
        lines += [f"## {m['n']:02d} · {m['label']}", ""]
        for r in m["text"]:
            up = " UPPERCASE" if r["upper"] else ""
            lines.append(f"- **{r['t']}** — {r['font']} {r['size']}px/{r['weight']}{up} {r['color']} @ ({r['x']}, {r['y']})")
        lines.append("")
    (OUT / "COPY.md").write_text("\n".join(lines))
    print(f"{len(ms)} slides, {sum(len(m['elements']) for m in ms)} element PNGs -> {OUT}")


if __name__ == "__main__":
    main()
