"""`docs/architecture.md`'s header/status line and `docs/telemetry-contract.md`'s
status line each name the current milestone -- checked directly here, since a
status line naming the current milestone is a fact this repo already has one
canonical source for, and checking it costs nothing next to a live cluster.

Derived from the newest implementation plan in `docs/superpowers/plans/`,
never retyped: those filenames are date-prefixed and milestone-named (e.g.
`2026-09-14-m5-progressive-autonomy.md`), they are committed and shipped, and
one is written before any milestone's first task. The milestone tag is
therefore readable on a feature branch AND on `main`, which matters more than
it looks.

**Why not the branch name.** `git rev-parse --abbrev-ref HEAD` answers
`m5-progressive-autonomy` on a feature branch but `main` immediately after the
merge, where no branch names a milestone. `main` is the *only* branch where a
stale status line actually reaches a reader, so this guard must never skip:
if the milestone cannot be derived, that is a failure, because it means the
plans directory this repo runs on is missing or misnamed -- not a condition
to step around.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLANS_DIR = REPO_ROOT / "docs" / "superpowers" / "plans"

_PLAN_MILESTONE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-(?:northstar-)?m(\d+)(?:\.\d+)?-", re.I)


def _current_milestone_tag() -> str:
    """The current milestone tag, e.g. `M5`, from the newest
    `docs/superpowers/plans/<date>-m<N>-*.md`.

    Never returns a sentinel and never skips: every failure mode here is a
    real defect in the repo's layout, not a condition to step around.
    """
    assert PLANS_DIR.is_dir(), (
        f"{PLANS_DIR} does not exist -- this guard derives the current "
        "milestone from the plans directory, so its absence is the defect."
    )
    dated = sorted(
        (path for path in PLANS_DIR.glob("*.md")
         if _PLAN_MILESTONE_RE.match(path.name)),
        key=lambda path: path.name,
    )
    assert dated, (
        f"no plan in {PLANS_DIR} is named `<YYYY-MM-DD>-m<N>-...md`, so the "
        "current milestone cannot be derived. Every milestone writes one "
        "before its first task."
    )
    match = _PLAN_MILESTONE_RE.match(dated[-1].name)
    assert match is not None  # guaranteed by the filter above
    return f"M{match.group(1)}"


def test_architecture_mds_title_and_status_line_name_the_current_milestone():
    """Checks the H1 title (line 1) and the `Status:` line individually --
    not "does the milestone tag appear anywhere in the first few lines" --
    because a recurrence note elsewhere in the file can narrate a past,
    wrong milestone by name (e.g. "said 'M4 (Observable)' on branch
    `m4-observable`"). A blob-search over the whole head of the file would
    read that narration as evidence the current line is correct; mutating
    the H1 line with `scripts/mutate-guard.sh` is how to confirm this test
    would actually catch that."""
    milestone = _current_milestone_tag()

    path = REPO_ROOT / "docs" / "architecture.md"
    lines = path.read_text().splitlines()
    title_line = lines[0]
    status_line = next((line for line in lines if line.startswith("Status:")), None)

    assert milestone in title_line, (
        f"docs/architecture.md's title line does not mention {milestone!r}, "
        f"the milestone the newest plan names: {title_line!r}"
    )
    assert status_line is not None, (
        "docs/architecture.md has no line starting 'Status:' at all"
    )
    assert milestone in status_line, (
        f"docs/architecture.md's Status: line does not mention {milestone!r}, "
        f"the milestone the newest plan names: {status_line!r}"
    )


def test_telemetry_contracts_status_paragraph_names_the_current_milestone():
    """Same check for the telemetry contract's status paragraph, which is one
    paragraph (not a single line) starting 'Status:' and running to the first
    blank line -- narrower than "anywhere in the first 20 lines" for the same
    reason as above: a recurrence note elsewhere in the file can narrate a
    past milestone by name."""
    milestone = _current_milestone_tag()

    path = REPO_ROOT / "docs" / "telemetry-contract.md"
    text = path.read_text()
    match = re.search(r"^Status:.*?(?=\n\n)", text, re.S | re.M)
    assert match, "docs/telemetry-contract.md has no 'Status:' paragraph"
    assert milestone in match.group(0), (
        f"docs/telemetry-contract.md's Status paragraph does not mention "
        f"{milestone!r}, the milestone the newest plan names:\n"
        f"{match.group(0)}"
    )
