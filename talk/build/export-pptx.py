#!/usr/bin/env python3
"""Build a PowerPoint from export/google-slides/, for importing into Google Slides.

    python3 build/export-elements.py              # first: the layers and manifest.json
    <venv with python-pptx>/bin/python build/export-pptx.py [out.pptx]

Each slide is the exporter's text-free layout (every element in place, the ground
included) as one full-bleed picture, with every text run from manifest.json laid
over it as an editable text box: same position, font, size, weight and colour.
Speaker notes come from the built index.html, so they carry the running-time stamp.

What does not survive: animations (the MCP/A2A loop, the card flip are still
frames), and text drawn inside inline SVG figures, which is part of the picture.

The slide is 13.333 x 7.5 in, the deck's 1920 x 1080 at 6350 EMU a pixel; a font
size in px is half that in points. Text boxes get a little slack on the right,
because Slides' font metrics are not the browser's.
"""
import io
import json
import pathlib
import re
import sys

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Emu, Pt

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "export" / "google-slides"
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "export" / "northstar-kubecon.pptx"
PX = 6350          # EMU per deck pixel
SLACK = 1.06       # extra text-box width, for font-metric drift


def picture_bytes(path: pathlib.Path) -> io.BytesIO:
    """The 2x layout as a JPEG: an order of magnitude smaller than the PNG, crisp at 2x."""
    img = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, optimize=True)
    buf.seek(0)
    return buf


INK, PAPER = (0x0A, 0x11, 0x28), (0xFE, 0xFC, 0xFB)


def text_rgb(color: str) -> str:
    """A hex colour for Slides. The exporter writes the deck's color-mix() greys as an
    unparsed srgb triple plus an opacity ("#0.0A... @60%"): those are ink on paper (or
    paper on ink, on a dark sheet) at that opacity, blended here to a flat colour."""
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        return color[1:].upper()
    pct = re.search(r"@(\d+)%", color)
    a = int(pct.group(1)) / 100 if pct else 0.6
    fg, bg = (PAPER, INK) if color.startswith("#0.F") else (INK, PAPER)
    return "".join(f"{round(f * a + g * (1 - a)):02X}" for f, g in zip(fg, bg))


def _inside(child: dict, parent: dict) -> bool:
    """The span starts inside the sentence's box. (Its own box can hang below it: a
    span's line box is measured with its own, larger line height.)"""
    slack = max(2, child["size"] * 0.1)  # a larger line box can also start a little above
    return (parent["x"] - 2 <= child["x"] <= parent["x"] + parent["w"]
            and parent["y"] - slack <= child["y"] <= parent["y"] + parent["h"])


def nest(runs: list[dict]) -> tuple[list[dict], dict]:
    """The exporter lists an element's whole text and, again, each styled span inside
    it (a highlighted phrase). Drawn as is, the span prints twice. Each span whose text
    is in a larger run and whose box sits inside that run's box is folded into it:
    `inner` maps the span's index to True and ("in", parent) to the span indices."""
    inner: dict = {}
    for i, child in enumerate(runs):
        hosts = [j for j, parent in enumerate(runs)
                 if j != i and len(parent["t"]) > len(child["t"]) and child["t"] in parent["t"]
                 and _inside(child, parent)]
        if hosts:
            host = min(hosts, key=lambda j: len(runs[j]["t"]))  # the tightest parent
            inner[i] = True
            inner.setdefault(("in", host), []).append(i)
    return runs, inner


def segments(run: dict, children: list[dict]):
    """The run's text split so each folded span keeps its own colour and weight."""
    text, pos, out = run["t"], 0, []
    for child in sorted(children, key=lambda c: text.find(c["t"])):
        at = text.find(child["t"], pos)
        if at < 0:
            continue
        if at > pos:
            out.append((text[pos:at], run))
        # A span that starts at the run's left edge, below its first line, began a
        # new line in the deck (a block span, or a wrap): keep the break.
        if child["x"] <= run["x"] + 4 and child["y"] > run["y"] + run["size"] * 0.6 and out:
            out[-1] = (out[-1][0].rstrip(" "), out[-1][1])
            out.append(("\n", child))
        out.append((child["t"], child))
        pos = at + len(child["t"])
    if pos < len(text):
        out.append((text[pos:], run))
    return out or [(text, run)]


def line_count(run: dict) -> int:
    """How many lines the deck set this run on. The box height alone can't say: a
    three-line title at line-height 0.93 is as tall as two lines at 1.4. Every count
    that gives a plausible line height (0.9 to 2 times the font size) is a
    candidate; the text's rough width (0.55 em a character) picks among them."""
    h, size = run["h"], run["size"]
    guess = len(run["t"]) * 0.55 * size / max(run["w"], 1)
    fits = [n for n in range(1, 12) if 0.9 <= h / n / size <= 2.0]
    return min(fits, key=lambda n: abs(n - guess)) if fits else max(1, round(h / (size * 1.2)))


def speaker_notes() -> list[str]:
    html = (ROOT / "index.html").read_text()
    match = re.search(r'<script type="application/json" id="speaker-notes">(.*?)</script>', html, re.S)
    return json.loads(match.group(1)) if match else []


def main() -> None:
    manifest = json.loads((SRC / "manifest.json").read_text())
    notes = speaker_notes()
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(1920 * PX), Emu(1080 * PX)
    blank = deck.slide_layouts[6]
    for k, entry in enumerate(manifest):
        slide = deck.slides.add_slide(blank)
        slide.shapes.add_picture(picture_bytes(SRC / entry["layout"]), 0, 0,
                                 width=deck.slide_width, height=deck.slide_height)
        runs, inner = nest(entry["text"])
        for i, run in enumerate(runs):
            if i in inner:
                continue
            lines = line_count(run)
            box = slide.shapes.add_textbox(Emu(int(run["x"] * PX)), Emu(int(run["y"] * PX)),
                                           Emu(int(run["w"] * (SLACK if lines > 1 else 1.3) * PX)), Emu(int(max(run["h"], 1) * PX)))
            frame = box.text_frame
            # The deck's own line height, measured: the box height over its line
            # count. Slides' default spacing is taller and pushes a three-line title
            # into the line below it. A one-line label never wraps: Slides measures
            # the font a little wider and would break "William Rizzo" in two.
            frame.word_wrap = lines > 1
            frame.auto_size = MSO_AUTO_SIZE.NONE
            frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
            para = frame.paragraphs[0]
            # As a multiple of single spacing (1.2 em), not in points: Slides scales
            # an exact point spacing by about 1.2 again on import.
            para.line_spacing = max(0.7, run["h"] / lines / (run["size"] * 1.2))
            for text, style in segments(run, [runs[j] for j in inner.get(("in", i), [])]):
                if text == "\n":
                    para.add_line_break()
                    continue
                r = para.add_run()
                r.text = text.upper() if style.get("upper") else text
                font = r.font
                font.name = style["font"]
                font.size = Pt(style["size"] / 2)
                font.bold = int(str(style.get("weight") or 400)[:3]) >= 600
                font.color.rgb = RGBColor.from_string(text_rgb(style["color"]))
        if k < len(notes):
            slide.notes_slide.notes_text_frame.text = notes[k]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    deck.save(OUT)
    print(f"wrote {OUT}: {len(manifest)} slides, {OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
