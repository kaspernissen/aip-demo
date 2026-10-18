"""The deck must not say what M7 said.

M7's autonomy table was assisted EUR 6,000 / autonomous EUR 1,000,000 with a
`senior-settlement-agent` that finished what the junior could not, and the demo's
first claim was `demo claim 6200 --expect refused`. M8 replaced all of it: assisted
EUR 0 / autonomous EUR 6,000, a human signs above the limit, and `--expect refused`
is not an option any more (argparse rejects it). A slide, a speaker note or the
generated index.html that still carries one of those is a wrong thing said on stage,
next to a live block that says the opposite.

M9 added a second class of wrong thing on stage: a telemetry name from before the
registry rename (the un-prefixed `policy.*`, `review.*` and `claim.*` names,
the old elapsed-milliseconds attribute, the flag's old Kubernetes-named attribute). Every owned name is `northstar.*`
now (telemetry/registry/), and the deck's slides, notes and generated index must
use them. Same scan, same files.

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
    "a pre-registry (un-prefixed) telemetry name": re.compile(
        r"(?<![A-Za-z0-9_.\-/])"
        r"(?:policy\.(?:decision|rule|caller|tool|request|limit|autonomy|ledger)"
        r"|review\.(?:decision|payment|reviewer|elapsed_ms|resume_only|refused|queue)"
        r"|claim\.(?:submit|wait)|k8s\.resource\.name|northstar\.policy\.number)"
        r"(?![A-Za-z0-9_])"),
    "a pre-registry wildcard (policy.* / review.* without the northstar. prefix)":
        re.compile(r"(?<![A-Za-z0-9_.\-/])(?:policy|review)\.\*"),
    "the truncated span name autonomy_contract.change (the span is northstar.autonomy_contract.change)":
        re.compile(r"(?<![A-Za-z0-9_.\-/])autonomy_contract\.change"),
    # agentgateway v1.6.0 moved the SERVER span to http.response.status_code.
    "the pre-v1.6.0 gateway attribute http.status": re.compile(r"(?<![A-Za-z0-9_.])http\.status(?![A-Za-z0-9_])"),
    # M9 renamed the histogram's Prometheus labels; a query on the old ones returns nothing.
    "a pre-M9 Prometheus label (autonomy_level without northstar_policy_)":
        re.compile(r"(?<!northstar_policy_)\bautonomy_level\b"),
    'the pre-M9 histogram label decision="approved"': re.compile(r'decision="approved"'),
    'the pre-M9 histogram label decision="rejected"': re.compile(r'decision="rejected"'),
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


def test_the_registry_slide_quotes_the_real_registry():
    """The slide "The decision contract is a published schema" shows an excerpt of
    telemetry/registry/policy.yaml. Every line of it (the dots aside) must be a line
    of that file, in the same order, so the excerpt cannot drift from the schema."""
    import html
    root = TALK.parent
    slide = (TALK / "slides" / "40-act4.html").read_text()
    section = slide.split('data-label="The decision contract is a published schema"')[1].split("</section>")[0]
    pre = re.search(r'<pre class="term x40-reg">(.*?)</pre>', section, re.S).group(1)
    pre = re.sub(r"<[^>]+>", "", pre)
    excerpt = [l for l in html.unescape(pre).splitlines()[1:] if l.strip() and l.strip() != "\u2026"]
    assert len(excerpt) >= 8, excerpt
    source = (root / "telemetry" / "registry" / ("policy" + ".yaml")).read_text().splitlines()
    at = -1
    for line in excerpt:
        assert line in source[at + 1:], f"not in policy.yaml after line {at + 1}: {line!r}"
        at = source.index(line, at + 1)
    assert "jaeger-policy-decision-m9.png" in section
    assert (TALK / "assets" / "shots" / "jaeger-policy-decision-m9.png").is_file()
