"""Shared helper for tests that read `scripts/setup-kind.sh` as text.

`effective_script()` strips the shebang and every whole-line comment, so no
assertion over it can be satisfied by prose: a comment naming a step cannot
stand in for the step. Trailing (end-of-line) comments are deliberately not
stripped -- doing that safely needs a shell parser, since `#` is an ordinary
character inside strings -- so assertions must be specific enough that a
trailing comment cannot supply them.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_KIND = REPO_ROOT / "scripts" / "setup-kind.sh"


def effective_script() -> str:
    return "\n".join(
        line
        for line in SETUP_KIND.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
