"""The deck must not say what M7 said.

M7's autonomy table was assisted EUR 6,000 / autonomous EUR 1,000,000 with a
`senior-settlement-agent` that finished what the junior could not, and the demo's
first claim was `demo claim 6200 --expect refused`. M8 replaced all of it: assisted
EUR 0 / autonomous EUR 6,000, a human signs above the limit, and `--expect refused`
is not an option any more (argparse rejects it). A slide, a speaker note or the
generated index.html that still carries one of those is a wrong thing said on stage,
next to a live block that says the opposite.

Scans every text file under talk/ except the cited source reports (talk/reports),
the generated exports and previews.
"""
from __future__ import annotations

import re
from pathlib import Path

TALK = Path(__file__).resolve().parents[2] / "talk"
SKIP_DIRS = {"reports", "export", "node_modules"}
TEXT_SUFFIXES = {".html", ".json", ".md", ".css", ".js", ".py", ".txt", ".sh"}

FORBIDDEN = {
    "the removed `--expect refused` flag": re.compile(r"--expect\s+refused"),
    "M7's EUR 1,000,000 autonomous limit": re.compile(r"1,000,000|1_000_000"),
    "the removed senior agent": re.compile(r"senior", re.IGNORECASE),
}


def _deck_files():
    for path in sorted(TALK.rglob("*")):
        rel = path.relative_to(TALK)
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if rel.parts[0] in SKIP_DIRS or rel.parts[0].startswith("preview-"):
            continue
        yield path


def _hits():
    found = []
    for path in _deck_files():
        text = path.read_text(errors="replace")
        for what, pattern in FORBIDDEN.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                found.append(f"{path.relative_to(TALK)}:{line}: {what}: {match.group(0)!r}")
    return found


def test_the_scan_covers_the_slides_the_notes_and_the_generated_index():
    names = {p.relative_to(TALK).as_posix() for p in _deck_files()}
    assert "index.html" in names
    assert any(n.startswith("slides/") for n in names)
    assert any(n.startswith("notes/") for n in names)
    assert not any(n.startswith("reports/") for n in names)


def test_the_deck_says_nothing_m7_said():
    assert not _hits(), "the deck still carries M7:\n" + "\n".join(_hits())
