#!/usr/bin/env python3
"""Regenerate PLAN.md's slide table from the fragments' comments and the notes.

Usage, from talk/:  python3 build/plan.py           rewrites the table in PLAN.md
                    python3 build/plan.py --check   exits 1 if PLAN.md is stale
Run it after build/build.py whenever a slide, its comment or its note changes.
Row: # | Fragment | Who | Start | secs | Slide | Origin | What it shows
- Origin and What come from the comment right before each <section>: <!-- id · ORIGIN · what -->
- Who comes from the note's speaker names (WILLIAM / KASPER), in order of first mention.
- Start is the running time; a 0 s slide shows a dash.
"""
import html
import json
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parent.parent
rows = []
t = 0
n = 0
cut = []
for f in sorted(root.glob("slides/[0-9][0-9]-*.html")):
    text = f.read_text()
    notes = json.loads((root / "notes" / (f.stem + ".json")).read_text())
    secs = list(re.finditer(r"<section\b.*?</section>", text, re.S))
    assert len(secs) == len(notes), f.name
    prev_end = 0
    for m, note in zip(secs, notes):
        before = text[prev_end:m.start()]
        prev_end = m.end()
        com = re.findall(r"<!--(.*?)-->", before, re.S)
        origin, what = "", ""
        if com:
            parts = [p.strip() for p in com[-1].split(" · ", 2)]
            if len(parts) == 3 and parts[1] in ("NEW", "TAMING", "DID WHAT", "BOUNDED") or (
                    len(parts) == 3 and re.fullmatch(r"[A-Z +]+", parts[1])):
                origin, what = parts[1], parts[2]
        label = html.unescape(re.search(r'data-label="([^"]*)"', m.group(0)).group(1))
        if "data-cut" in m.group(0).split(">", 1)[0]:
            cut.append(f"{label} ({f.stem})")
            continue
        body = note["note"]
        first = body.splitlines()[0]
        who = []
        for name, ab in (("WILLIAM", "W"), ("KASPER", "K")):
            pos = first.find(name) if name in first else -1
            if name in first:
                who.append((pos, ab))
        if len(who) == 1 or not who:
            # a mixed note names both speakers in its body
            spoken = [(body.find(nm + ":"), ab) for nm, ab in (("WILLIAM", "W"), ("KASPER", "K")) if (nm + ":") in body]
            if len(spoken) > 1:
                who = spoken
        who = "+".join(ab for _, ab in sorted(who))
        n += 1
        s = int(note["secs"])
        start = f"{t // 60}:{t % 60:02d}" if s else "—"
        t += s
        rows.append(f"| {n:02d} | {f.stem} | {who} | {start} | {s} | {label} | {origin} | {what} |")

rows.append(f"| — | hidden | | | 0 | {len(cut)} slides carry data-cut and are not built: " + "; ".join(cut) + " | | |")
table = "\n".join(["| # | Fragment | Who | Start | secs | Slide | Origin | What it shows |",
                   "|---|---|---|---|---|---|---|---|"] + rows)
plan = root / "PLAN.md"
cur = plan.read_text()
new = re.sub(r"\| # \| Fragment.*?(?=\n\n)", lambda _: table, cur, count=1, flags=re.S)
if "--check" in sys.argv:
    if new != cur:
        sys.exit("PLAN.md is stale: run python3 build/plan.py")
    print(f"PLAN.md is current: {n} rows, {t // 60}:{t % 60:02d}")
else:
    plan.write_text(new)
    print(f"wrote PLAN.md: {n} rows, {t // 60}:{t % 60:02d}")
