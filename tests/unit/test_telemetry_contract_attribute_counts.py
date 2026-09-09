"""Guards `docs/telemetry-contract.md`'s published *attribute* counts against
the class of defect that `test_telemetry_contract_counts.py` structurally
cannot see.

**Why this file exists, precisely.** That sibling module guards published
*test* counts, and it exists because a published number drifted three times.
Attribute counts had no guard at all, and the consequence was measured in M2.1
Task 3's review: the instrumentation-table row for settlement-agent's model
spans said "**25 distinct `gen_ai.*` attribute names measured**" for the whole
of M1.2 and M2.1 while the inventory 165 lines below it said 28. 25 is the
**OFF**-mode figure; the row's own next clause states the shipped default is
**ON**. M1.2's SF-3 fix round corrected the inventory and never touched the
summary — the fifth time in this project that fixing one instance missed a
sibling, and the first time the sibling was in the same file.

**What this guard asserts, and why each check is shaped the way it is.**

1. `test_every_published_attribute_count_names_its_mode_and_matches_the_inventory`
   — the direct guard for the defect above. It parses each sentence that
   publishes a `gen_ai.*` attribute count into **(mode, number) pairs** and
   requires every pair to be right: the number governed by "at ON" must be the
   ON figure, the number governed by "at OFF" must be the OFF figure, and a
   sentence that names a mode must actually assign that mode a number. The two
   figures are derived from the document's own inventory, never hardcoded here:
   ON is however many names the inventory block lists, OFF is that minus however
   many the document's own delta table marks as content-gated.

   *Sentence*-scoped, not paragraph-scoped, deliberately. The stale row was a
   single table cell that **did** contain the words "default ON" — a
   paragraph-scoped check would have passed it, because the mode marker and the
   wrong number were 40 words apart in the same cell. Scoping to the sentence is
   what makes "25 distinct names" with no adjacent mode read as unqualified.

   **Pairs, not a set of numbers — this was a real gap, found in review.** The
   first version of this check tested only that a both-modes sentence *contained*
   the set `{28, 25}` somewhere in scope. Rewriting `28 at ON, 25 at OFF` as
   `25 at ON, 28 at OFF` left that set unchanged, so the swap stayed green: a
   set-membership check wearing an assignment check's name. It caught an
   unqualified number and a number wrong for a single-mode sentence — the
   original defect — and not the interchange, which is the other half of what
   this file promises. `_governing_mode()` and `_assignments()` below exist to
   close that, and the mutation set that proves this guard is now derived from
   *these functions' branches* rather than from edits a human might plausibly
   make to the document (see the task report's fix-round-2 branch table). The
   lesson worth keeping: mutation-testing a guard catches guards that **do not
   run**; it does not catch a guard that runs, asserts, and asserts the wrong
   proposition. Only branch coverage of the checker finds those.

2. `test_the_content_gated_delta_table_agrees_with_the_inventory` — the two
   places in the document that between them define ON and OFF must agree: every
   key the OQ-1 delta table marks `absent` at OFF must appear in the ON
   inventory, and the prose count of gated keys must match how many rows that
   table has. This is what makes it impossible to "fix" one site by editing the
   inventory while leaving the delta table describing a different set.

3. `test_the_agentgateway_attribute_blocks_match_their_own_stated_counts` —
   generalises the guard past `gen_ai.*` to the other attribute sets this
   document publishes (agentgateway's two `tools/call` span attribute lists,
   added by M2.1 Task 3). Same failure mode, different vocabulary.

**On guard inertness, which this repo has been bitten by five times in one
task.** Nothing here greps for a magic string and passes if it finds it. Every
check parses structure — fenced code blocks, markdown table rows, sentences —
and **asserts it found the thing it is about to check** before checking it, so
a document that stops containing an inventory fails loudly instead of passing
vacuously. There is no live cluster, no trace, no model call and no network
access involved: this reads one file.

This guard cannot verify that 28 is the number a *live trace* would produce —
that is what `tests/e2e/test_settlement_llm_trace.py` and the task reports do,
at the cost of a live model run. What it verifies is that the document does not
contradict itself, which is the failure that actually happened.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "docs" / "telemetry-contract.md"

# Words this document actually uses to say "capture is on/off". Matched
# case-sensitively for the bare `ON`/`OFF` spellings so ordinary prose ("on the
# `chat` span") cannot be mistaken for a mode marker.
ON_MARKERS = (
    "at ON",
    "ON default",
    "at the shipped default",
    "shipped ON default",
    "`NORTHSTAR_CAPTURE_CONTENT` ON",
    "include_content=True",
)
OFF_MARKERS = (
    "at OFF",
    "OFF-mode",
    "OFF mode",
    "`NORTHSTAR_CAPTURE_CONTENT` OFF",
    "include_content=False",
)

MODE_MARKERS = tuple((m, "ON") for m in ON_MARKERS) + tuple(
    (m, "OFF") for m in OFF_MARKERS
)

# The two phrasings this document uses to publish a gen_ai.* attribute count.
# Kept deliberately narrow: a pattern broad enough to catch every integer near
# the words "gen_ai" would fire on span counts, token counts and version
# numbers, and a guard that cries wolf gets deleted.
COUNT_PATTERNS = (
    re.compile(r"(\d+)\s+distinct\s+`gen_ai\.\*`"),
    re.compile(r"(\d+)\s+names\b"),
)

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "twenty": 20, "twenty-one": 21, "twenty-five": 25,
    "twenty-eight": 28,
}


def _contract_text() -> str:
    text = CONTRACT_PATH.read_text()
    assert text.strip(), f"{CONTRACT_PATH} is empty"
    return text


def _fenced_blocks(text: str) -> list[str]:
    """Every ```-fenced block's body, language tag tolerated."""
    return re.findall(r"^```[^\n]*\n(.*?)^```", text, re.S | re.M)


def _on_inventory(text: str) -> list[str]:
    """The document's canonical ON-mode `gen_ai.*` inventory.

    Identified structurally rather than by a heading or a line number: it is
    the fenced block whose every non-empty line is a bare `gen_ai.<name>` and
    nothing else. That is a shape no other block in this file has (the
    agentgateway blocks pair keys with values; the shell blocks are commands),
    and it survives the section being moved or retitled.
    """
    candidates = []
    for body in _fenced_blocks(text):
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if lines and all(re.fullmatch(r"gen_ai\.[a-z0-9_.]+", line) for line in lines):
            candidates.append(lines)
    assert len(candidates) == 1, (
        "expected exactly one fenced block in "
        f"{CONTRACT_PATH.relative_to(REPO_ROOT)} listing only bare `gen_ai.*` "
        f"names (the ON-mode inventory); found {len(candidates)}. This guard "
        "cannot check the document's attribute counts without it -- if the "
        "inventory moved or gained a second copy, fix that rather than "
        "loosening this assertion (a second copy is itself the drift this file "
        "exists to prevent)."
    )
    inventory = candidates[0]
    assert len(inventory) == len(set(inventory)), (
        f"the ON-mode inventory lists duplicates: "
        f"{sorted({n for n in inventory if inventory.count(n) > 1})}"
    )
    return inventory


def _content_gated_keys(text: str) -> list[str]:
    """Keys the document's own delta table marks present at ON, absent at OFF.

    Parses the markdown table rows, so it reads what the table *says* rather
    than trusting a count written beside it.
    """
    rows = re.findall(
        r"^\|\s*`(gen_ai\.[a-z0-9_.]+)`\s*\|\s*present\s*\|\s*\*\*absent\*\*\s*\|",
        text,
        re.M,
    )
    assert rows, (
        "found no `| `gen_ai.<key>` | present | **absent** |` rows in "
        f"{CONTRACT_PATH.relative_to(REPO_ROOT)} -- the OQ-1 content-capture "
        "delta table is what defines the OFF-mode figure, so this guard cannot "
        "check any OFF count without it"
    )
    assert len(rows) == len(set(rows)), f"the delta table lists a key twice: {rows}"
    return rows


def _sentences(text: str) -> list[str]:
    """Split into sentences, with each markdown table *cell* its own unit.

    Rough by design: the only property needed is that a mode marker 40 words
    away from a number does not count as qualifying it. Splitting on
    sentence-ending punctuation and on table-cell pipes achieves that without
    needing real NLP.

    **It deliberately does not split on a bare newline.** An earlier version
    did, and that let a claim escape the check entirely: this document hard-
    wraps prose, so `"25\\nnames"` became two fragments and the `(\\d+)\\s+names`
    pattern matched neither. Not splitting on newlines both closes that hole and
    is more correct about mode markers, which are routinely on the wrapped line
    next to the number they qualify. It does not weaken the check on the defect
    that prompted this file: markdown table rows are single lines, so the stale
    cell's number and its distant "default ON" are still separated by the `. `
    between them.
    """
    return [s for s in re.split(r"(?<=[.:;!?])\s+|\s\|\s", text) if s.strip()]


def _modes(sentence: str) -> set[str]:
    """Which capture modes this sentence names at all (order ignored)."""
    return {mode for marker, mode in MODE_MARKERS if marker in sentence}


def _marker_spans(sentence: str) -> list[tuple[int, int, str]]:
    """Every mode-marker occurrence as `(start, end, mode)`, left to right.

    Overlapping markers of the same mode (`"ON default"` inside
    `"shipped ON default"`) both appear; that is harmless, because they agree.
    """
    spans = []
    for marker, mode in MODE_MARKERS:
        start = 0
        while (index := sentence.find(marker, start)) != -1:
            spans.append((index, index + len(marker), mode))
            start = index + 1
    spans.sort()
    return spans


def _governing_mode(
    spans: list[tuple[int, int, str]], num_start: int, num_end: int
) -> str | None:
    """Which mode marker governs the number occupying `[num_start, num_end)`.

    **The rule, and why it is this rule.** This document writes the number
    first and the mode after it — "28 distinct `gen_ai.*` keys at ON, 25 at
    OFF", "28 ... measured at ON (25 at OFF)" — so the *first marker after the
    number* governs it. Where the document puts the mode first instead —
    "(`NORTHSTAR_CAPTURE_CONTENT` ON) — 28 names", "the **OFF-mode** figure ...
    \\"25 names\\"" — there is no marker after the number, and the *last marker
    before it* governs.

    Nearest-marker-in-either-direction was tried and rejected: in "28 ... at
    ON, 25 at OFF" the distance from `25` to "at ON" and to "at OFF" differs by
    one character, so a distance rule decides that sentence on a comma. Reading
    direction is the actual convention in the text, so the rule follows it.
    """
    after = [span for span in spans if span[0] >= num_end]
    if after:
        return after[0][2]
    before = [span for span in spans if span[1] <= num_start]
    if before:
        return before[-1][2]
    return None


def _assignments(
    sentence: str, on_count: int, off_count: int
) -> list[tuple[str | None, int, str, bool]]:
    """Parse a sentence into `(mode, number, quoted, is_primary)` assignments.

    Two kinds of number are picked up:

    * **primary** — matched by `COUNT_PATTERNS`, i.e. anchored on `gen_ai.*` or
      on the word "names". These are the published counts.
    * **secondary** — a bare integer that happens to equal one of the two mode
      figures. This is what makes the *second* half of "28 at ON, 25 at OFF"
      checkable: `25` there is anchored on nothing, so no count pattern sees
      it, yet it is exactly the number a swap moves. Restricting secondaries to
      `{on_count, off_count}` is what keeps the check from firing on the
      document's other mode-qualified numbers — "from **1599** characters at ON
      ... to **657** at OFF" is a real sentence in this file and is none of
      this guard's business.

    Keyed by the digits' offset so a number matched by both a count pattern and
    the bare-integer sweep is one assignment, not two.
    """
    spans = _marker_spans(sentence)
    found: dict[int, tuple[int, str, bool]] = {}
    for pattern in COUNT_PATTERNS:
        for match in pattern.finditer(sentence):
            found[match.start(1)] = (int(match.group(1)), match.group(0), True)
    for match in re.finditer(r"\b(\d+)\b", sentence):
        start = match.start(1)
        number = int(match.group(1))
        if start not in found and number in (on_count, off_count):
            found[start] = (number, match.group(0), False)

    assignments = []
    for start, (number, quoted, is_primary) in sorted(found.items()):
        end = start + len(str(number))
        assignments.append(
            (_governing_mode(spans, start, end), number, quoted, is_primary)
        )
    return assignments


def test_every_published_attribute_count_names_its_mode_and_matches_the_inventory():
    text = _contract_text()
    on_count = len(_on_inventory(text))
    off_count = on_count - len(_content_gated_keys(text))
    expected = {"ON": on_count, "OFF": off_count}

    problems = []
    primary_sites = 0
    for sentence in _sentences(text):
        pairs = _assignments(sentence, on_count, off_count)
        if not any(is_primary for _, _, _, is_primary in pairs):
            continue  # not a sentence that publishes an attribute count
        primary_sites += sum(1 for *_, is_primary in pairs if is_primary)
        excerpt = sentence.strip()[:180]

        for mode, number, quoted, is_primary in pairs:
            if mode is None:
                problems.append(
                    f"UNQUALIFIED: {quoted!r} names no capture mode in its own "
                    f"sentence, so a reader cannot tell whether {number} is the "
                    f"ON figure ({on_count}) or the OFF figure ({off_count}). "
                    f"This is the exact defect M2.1 Task 3's review found. "
                    f"In: {excerpt!r}"
                )
            elif number != expected[mode]:
                problems.append(
                    f"WRONG FOR {mode}: {quoted!r} is governed by a {mode} "
                    f"marker, but the {mode} figure is {expected[mode]} "
                    f"(ON = {on_count} inventory names; OFF = that minus "
                    f"{on_count - off_count} content-gated). If the two figures "
                    f"have been interchanged, that is what this says. "
                    f"In: {excerpt!r}"
                )

        named = _modes(sentence)
        assigned = {mode for mode, *_ in pairs if mode is not None}
        unassigned = named - assigned
        if unassigned:
            problems.append(
                f"AMBIGUOUS: this sentence names {sorted(named)} but gives a "
                f"number to only {sorted(assigned)} -- {sorted(unassigned)} is "
                f"left to inherit the other mode's figure by implication. State "
                f"both {on_count} (ON) and {off_count} (OFF). In: {excerpt!r}"
            )

    assert primary_sites, (
        "found no published `gen_ai.*` attribute count in "
        f"{CONTRACT_PATH.relative_to(REPO_ROOT)} at all -- this guard would "
        "then be checking nothing. If the phrasing changed, teach "
        "COUNT_PATTERNS the new one rather than deleting this assertion"
    )
    assert not problems, (
        f"docs/telemetry-contract.md publishes {primary_sites} `gen_ai.*` "
        "attribute count(s); the following disagree with the document's own "
        "inventory, are assigned to the wrong capture mode, or fail to name a "
        "mode at all:\n  " + "\n  ".join(problems)
    )


def test_the_content_gated_delta_table_agrees_with_the_inventory():
    """ON and OFF are defined in two different places; they must not drift.

    Without this, someone could correct every count in the document while the
    delta table still described a different set of gated keys -- the counts
    would agree with each other and with nothing real.
    """
    text = _contract_text()
    inventory = set(_on_inventory(text))
    gated = _content_gated_keys(text)

    missing = [key for key in gated if key not in inventory]
    assert not missing, (
        f"the OQ-1 delta table marks {missing} as content-gated, but they are "
        "not in the ON-mode inventory -- one of the two lists is wrong, and "
        "every OFF-mode count in this document is derived from their difference"
    )

    # The prose count beside that table ("**Three of these are content-gated**")
    # must match the table itself.
    prose = re.search(
        r"\*\*(\w+(?:-\w+)?) of these are content-gated\*\*", text, re.I
    )
    assert prose, (
        "expected a '**<N> of these are content-gated**' claim beside the "
        "inventory; without it this guard cannot check the prose against the "
        "delta table"
    )
    word = prose.group(1).lower()
    claimed = _WORD_NUMBERS.get(word, None)
    if claimed is None:
        assert word.isdigit(), (
            f"could not read {word!r} as a number in {prose.group(0)!r}; add it "
            "to _WORD_NUMBERS"
        )
        claimed = int(word)
    assert claimed == len(gated), (
        f"the document says {prose.group(0)!r} but its own delta table lists "
        f"{len(gated)} content-gated key(s): {sorted(gated)}"
    )


def test_the_agentgateway_attribute_blocks_match_their_own_stated_counts():
    """The same class of claim, for agentgateway's two attribute sets.

    Each of the two blocks is introduced by a sentence stating how many keys it
    holds ("21 keys, the complete set", "Six keys, and that is all"). Those
    counts get stale exactly the way the `gen_ai.*` one did, and
    `tests/e2e/test_gateway_trace.py` -- which asserts the *same* two sets by
    exact bidirectional equality against live spans -- cannot see the document
    at all. This is the join between them.
    """
    text = _contract_text()

    # Each block is a fenced body of `<key><whitespace><value>` lines whose keys
    # are dotted attribute names; identified by shape, then tied to the count in
    # the prose immediately above its own fence.
    pattern = re.compile(
        r"([^\n]*\b(?:(\d+)|([A-Za-z-]+))\s+keys\b[^\n]*)\n+^```[^\n]*\n(.*?)^```",
        re.S | re.M,
    )
    matches = pattern.findall(text)
    assert len(matches) >= 2, (
        "expected at least two '<N> keys' claims immediately above a fenced "
        f"attribute block in {CONTRACT_PATH.relative_to(REPO_ROOT)}; found "
        f"{len(matches)}. The agentgateway SERVER and CLIENT attribute sets are "
        "both published that way -- if the phrasing changed, update this guard "
        "rather than dropping the check"
    )

    problems = []
    for intro, digits, word, body in matches:
        claimed = int(digits) if digits else _WORD_NUMBERS.get(word.lower())
        if claimed is None:
            continue  # not a number we can read; e.g. "the keys below"
        keys = [
            line.split()[0]
            for line in body.splitlines()
            if line.strip() and re.match(r"^[a-z][a-z0-9_.]*\s", line)
        ]
        if not keys:
            problems.append(
                f"{intro.strip()[:90]!r} introduces a block this guard read as "
                "holding no attribute keys at all"
            )
        elif len(keys) != claimed:
            problems.append(
                f"{intro.strip()[:90]!r} claims {claimed} keys but its block "
                f"lists {len(keys)}: {keys}"
            )
        elif len(set(keys)) != len(keys):
            problems.append(f"{intro.strip()[:90]!r}'s block lists a key twice: {keys}")

    assert not problems, (
        "docs/telemetry-contract.md's agentgateway attribute blocks disagree "
        "with their own stated key counts:\n  " + "\n  ".join(problems)
    )
