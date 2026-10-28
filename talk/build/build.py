#!/usr/bin/env python3
"""Compose index.html from slides/*.html and notes/*.json.

Each slides/NN-name.html holds one or more <section> elements, in order.
Each notes/NN-name.json holds a list with one entry per section in that file:
    {"secs": 45, "note": "What to say."}
`--only slides/NN-name.html` writes preview-NN-name.html with just that fragment,
so parallel authors never race on index.html. The composer asserts one note per slide, stamps each note with its running time,
builds talk.css from the Broadsheet deck layer (build/broadsheet-deck.css,
build/talk-base-bs.css) plus any slides/NN-name.css, and prints
the total. Run it after any edit:  python3 build/build.py
"""
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
EVENT = "KubeCon + CloudNativeCon North America 2026 &middot; Salt Lake City"



def _write(path, text):
    """Write via a temp file and a rename, so a browser reloading mid-build never
    reads (and caches under the new ?v= hash) a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)

def mmss(s):
    return f"{s // 60}:{s % 60:02d}"


def main():
    only = None
    if "--only" in sys.argv:
        only = pathlib.Path(sys.argv[sys.argv.index("--only") + 1]).resolve()
    frags = [only] if only else sorted(ROOT.glob("slides/[0-9][0-9]-*.html"))
    if not frags:
        sys.exit("no slides/NN-*.html fragments")
    sections, notes, labels = [], [], []
    t = 0
    for f in frags:
        secs = re.findall(r"<section\b.*?</section>", f.read_text(), re.S)
        nfile = ROOT / "notes" / (f.stem + ".json")
        if not nfile.exists():
            sys.exit(f"missing notes file for {f.name}: notes/{nfile.name}")
        n = json.loads(nfile.read_text())
        if len(n) != len(secs):
            sys.exit(f"{f.name}: {len(secs)} slides but {len(n)} notes")
        for sec, entry in zip(secs, n):
            if "data-cut" in sec.split(">", 1)[0]:
                continue  # hidden: kept in the fragment for Q&A or a longer cut, not built
            start = t
            t += int(entry["secs"])
            label = re.search(r'data-label="([^"]*)"', sec)
            labels.append(label.group(1) if label else "?")
            note = entry["note"].strip()
            notes.append(f"{note}\n\n[{mmss(start)} → {mmss(t)} · {entry['secs']}s]")
            sections.append(sec.replace("@@EVENT@@", EVENT))

    css = [(BUILD / "broadsheet-deck.css").read_text(), (BUILD / "talk-base-bs.css").read_text()]
    for c in sorted(ROOT.glob("slides/[0-9][0-9]-*.css")):
        css.append(f"\n/* ===== {c.name} ===== */\n" + c.read_text())
    _write(ROOT / ("talk.css" if not only else f"preview-{only.stem}.css"), "".join(css))

    head = (BUILD / "head.html").read_text()
    out = (head +
           '\n<deck-stage width="1920" height="1080" data-footer="handles"\n'
           '            data-handles="/in/william-rizzo &middot; kaspernissen.xyz">\n\n' +
           "\n".join(sections) +
           "\n\n</deck-stage>\n\n<script type=\"application/json\" id=\"speaker-notes\">\n" +
           json.dumps(notes, ensure_ascii=False, indent=1) +
           "\n</script>\n<script src=\"chrome.js\"></script>\n</body>\n</html>\n")
    target = "index.html"
    if only:
        target = f"preview-{only.stem}.html"
        out = out.replace('href="talk.css"', f'href="preview-{only.stem}.css"')
    else:
        # Cache-bust each stylesheet with a hash of its content, so a plain reload
        # never shows new slides over an old stylesheet (2026-10-19: it did).
        import hashlib
        for css_name in ("talk.css", "ocean/ocean.css", "bs/styles.css"):
            digest = hashlib.sha1((ROOT / css_name).read_bytes()).hexdigest()[:10]
            out = out.replace(f'href="{css_name}"', f'href="{css_name}?v={digest}"')
        # The scripts too: http.server sends no Cache-Control, so Chrome kept an old
        # chrome.js and drew the folio unstyled (2026-10-27).
        for js_name in ("chrome.js", "deck-stage.js", "bs/print-plates.js"):
            digest = hashlib.sha1((ROOT / js_name).read_bytes()).hexdigest()[:10]
            out = out.replace(f'src="{js_name}"', f'src="{js_name}?v={digest}"')
    _write(ROOT / target, out)
    print(f"wrote {target}: {len(sections)} slides, running time {mmss(t)}")
    if "--list" in sys.argv:
        for i, lab in enumerate(labels, 1):
            print(f"  {i:02d}  {lab}")


if __name__ == "__main__":
    main()
