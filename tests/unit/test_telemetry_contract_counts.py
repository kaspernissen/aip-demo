"""Guards `docs/telemetry-contract.md`'s published test counts against the
exact staleness the second final-review round found (finding 3): the
document still said `49 passed, 6 skipped, 1 xfailed` / `55 passed, 1
xfailed` after a commit had already taken the real numbers to `55/7/1` and
`62/1` — the commit message and the task report were both updated, but the
one artefact whose entire purpose is stating what is true was not.

**What this test can and cannot verify, and why it stops there:** the
contract publishes two numbers for the *same* fixed set of collected
tests — what a bare `pytest tests/` run proves (passed + skipped + xfailed)
and what the gate's `RUN_BROWSER_TESTS=1` run proves (passed + xfailed, no
skips, since nothing is gated out). Both totals must equal each other and
must equal how many tests `pytest` actually collects under `tests/` today.
`--collect-only` needs no live cluster, no `.env`, and no port-forwards —
it only imports test modules and enumerates test IDs — so this can run
anywhere the rest of `tests/unit/` runs. What it deliberately does NOT do is
re-run the full suite to confirm every documented "passed" is still true:
that would require the live cluster and the browser, making this "unit"
test as heavyweight and environment-coupled as the e2e suite it would be
checking, for a check `smoke-test.sh` already performs directly every time
it runs. A collection-count cross-check catches the specific, cheap,
frequent failure mode (someone adds or removes a test and forgets the two
numbers in the doc) without trying to be a second copy of the gate.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "docs" / "telemetry-contract.md"


def _collected_test_count() -> int:
    # A5: the bare `python3` on PATH is the too-old system 3.10.4, which
    # lacks every package this suite installs into `.venv` (amendment A5
    # forbids relying on it) -- re-exec with the *running* interpreter
    # instead, so this always collects with the same environment pytest
    # itself was just launched under.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "`pytest --collect-only -q tests/` failed, so this test cannot "
        f"verify the contract's published counts:\nstdout:\n{result.stdout}"
        f"\nstderr:\n{result.stderr}"
    )
    match = re.search(r"(\d+) tests? collected", result.stdout)
    assert match, (
        "could not parse a '<N> tests collected' summary line out of "
        f"pytest's --collect-only output:\n{result.stdout}"
    )
    return int(match.group(1))


def test_contracts_published_counts_match_what_pytest_actually_collects():
    collected = _collected_test_count()
    text = CONTRACT_PATH.read_text()

    bare_match = re.search(r"`(\d+) passed, (\d+) skipped, (\d+) xfailed`", text)
    assert bare_match, (
        "expected to find the bare-suite result as "
        "'`<N> passed, <N> skipped, <N> xfailed`' in "
        f"{CONTRACT_PATH.relative_to(REPO_ROOT)}"
    )
    bare_total = sum(int(g) for g in bare_match.groups())

    gate_match = re.search(r"`(\d+) passed, (\d+) xfailed`", text)
    assert gate_match, (
        "expected to find the gate's result as '`<N> passed, <N> xfailed`' "
        f"in {CONTRACT_PATH.relative_to(REPO_ROOT)}"
    )
    gate_total = sum(int(g) for g in gate_match.groups())

    assert bare_total == collected, (
        f"the contract's bare-suite row sums to {bare_total} tests "
        f"({bare_match.group(0)}), but `pytest --collect-only tests/` "
        f"actually collects {collected} today -- the document has drifted "
        f"from the suite (this is exactly finding 3, second final-review "
        f"round); update {CONTRACT_PATH.relative_to(REPO_ROOT)}"
    )
    assert gate_total == collected, (
        f"the contract's gate row sums to {gate_total} tests "
        f"({gate_match.group(0)}), but `pytest --collect-only tests/` "
        f"actually collects {collected} today -- the document has drifted "
        f"from the suite; update {CONTRACT_PATH.relative_to(REPO_ROOT)}"
    )
