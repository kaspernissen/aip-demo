"""Guards `docs/telemetry-contract.md`'s published *attribute* counts against
the class of defect that `test_telemetry_contract_counts.py` structurally
cannot see.

**Why this file exists, precisely.** That sibling module guards published
*test* counts. Attribute counts had no guard at all: the instrumentation-table
row for settlement-agent's model spans said "**25 distinct `gen_ai.*`
attribute names measured**" while the inventory 165 lines below it said 28.
25 is the **OFF**-mode figure; the row's own next clause states the shipped
default is **ON**. A prior correction fixed the inventory and never touched
the summary — fixing one instance while a sibling claim stays wrong is a
recurring failure shape in this repo, and this was the first time the
sibling was in the same file.

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

   **Pairs, not a set of numbers.** Testing only that a both-modes sentence
   *contains* the set `{28, 25}` somewhere in scope would be satisfied by
   `28 at ON, 25 at OFF` rewritten as `25 at ON, 28 at OFF` — a
   set-membership check wearing an assignment check's name, since the set is
   unchanged by the swap. That catches an unqualified number and a number
   wrong for a single-mode sentence, but not the interchange, which is the
   other half of what this file promises. `_governing_mode()` and
   `_assignments()` below exist to close that, and the mutation set that
   proves this guard is derived from *these functions' branches* rather than
   from edits a human might plausibly make to the document. The lesson worth
   keeping: mutation-testing a guard catches guards that **do not run**; it
   does not catch a guard that runs, asserts, and asserts the wrong
   proposition. Only branch coverage of the checker finds those.

2. `test_the_content_gated_delta_table_agrees_with_the_inventory` — the two
   places in the document that between them define ON and OFF must agree: every
   key the OQ-1 delta table marks `absent` at OFF must appear in the ON
   inventory, and the prose count of gated keys must match how many rows that
   table has. This is what makes it impossible to "fix" one site by editing the
   inventory while leaving the delta table describing a different set.

3. `test_every_attribute_block_agrees_with_its_own_stated_key_count`,
   `test_no_published_attribute_block_has_gone_missing` and
   `test_every_attribute_shaped_block_sits_in_a_section_this_guard_checks` —
   generalise the guard past `gen_ai.*` to the other attribute sets this
   document publishes (agentgateway's two `tools/call` span attribute lists,
   and the refused-call span). Same failure mode, different vocabulary.
   These three replace a single earlier check
   whose two defects — a claim locatable only on the line adjacent to its
   fence, and a `>= 2` floor that could not see a block deleted — are
   documented in full at their definition below, because both were
   *demonstrated* rather than argued and the second was found by a reviewer
   making the old guard green with a wrong published number.

4. `test_the_documents_own_count_of_its_attribute_count_sites_is_right` — the
   meta-count. The document states how many attribute-count sites it has, and
   that sentence was wrong ("nine"; measured six) and unguarded, so the wrong
   number could spread wherever the document was cited from. Guarding a
   count *of* claims is different from guarding the claims, and the absence of
   the former is exactly how this one drifted while every count it described
   stayed correct.

**On guard inertness, which this repo has been bitten by more than once.**
Nothing here greps for a magic string and passes if it finds it. Every
check parses structure — fenced code blocks, markdown table rows, sentences —
and **asserts it found the thing it is about to check** before checking it, so
a document that stops containing an inventory fails loudly instead of passing
vacuously. There is no live cluster, no trace, no model call and no network
access involved: this reads one file.

This guard cannot verify that 28 is the number a *live trace* would produce —
that is what `tests/e2e/test_settlement_llm_trace.py` does, at the cost of a
live model run. What it verifies is that the document does not
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

# English number words are a *closed* class, so this parses them rather than
# looking them up in a hand-maintained subset. A hand-maintained dict holding
# an arbitrary set of forms would silently drop any claim written with a word
# form outside it -- so the guard would report success on a claim it never
# examined. A parser over the whole class cannot have that gap, and
# `_read_number_word` below distinguishes the three cases a lookup table
# collapses into one.
_NUMBER_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_NUMBER_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def _read_number_word(token: str) -> tuple[str, int | None]:
    """Read one word as a count, returning `(kind, value)`.

    `kind` is deliberately three-valued, because the two failure modes must be
    treated differently and the old dict lookup could not tell them apart:

    * `"number"` -- a count this parser read; `value` is the integer.
    * `"not-a-number"` -- an ordinary English word that legitimately sits in
      front of "keys" without being a count at all. The document really does
      contain "attribute keys", "string keys", "only keys", "which keys",
      "label keys" and "unprefixed keys"; those must be skipped, and skipping
      them is correct rather than a gap.
    * `"unreadable"` -- built out of number morphemes yet not a number this
      parser can read ("twenty-twenty"). This is the case that must make the
      guard **fail loudly**: it looks exactly like a count, so silently
      dropping it would hide a real claim.

    Splitting on that distinction is what lets the guard fail on an unparseable
    count without also failing on every adjective in the document -- a guard
    that cried wolf on ordinary prose would be a padlock and would be deleted.
    """
    word = token.lower().strip()
    if word in _NUMBER_UNITS:
        return "number", _NUMBER_UNITS[word]
    if word in _NUMBER_TENS:
        return "number", _NUMBER_TENS[word]

    parts = word.split("-")
    if len(parts) > 1 and all(
        part in _NUMBER_UNITS or part in _NUMBER_TENS for part in parts
    ):
        # "twenty-eight" is the only shape that means anything here; anything
        # else assembled from number words ("twenty-twenty") is unreadable.
        if (
            len(parts) == 2
            and parts[0] in _NUMBER_TENS
            and parts[1] in _NUMBER_UNITS
            and 1 <= _NUMBER_UNITS[parts[1]] <= 9
        ):
            return "number", _NUMBER_TENS[parts[0]] + _NUMBER_UNITS[parts[1]]
        return "unreadable", None

    return "not-a-number", None


# Retained under its original name because two other checkers in this file read
# it, but now *derived* from the parser above rather than hand-listed, so the
# file holds one notion of "number word" instead of two that could drift. It is
# a strict superset of the 15 forms it replaced.
_WORD_NUMBERS = {
    **_NUMBER_UNITS,
    **_NUMBER_TENS,
    **{
        f"{tens}-{unit}": tens_value + unit_value
        for tens, tens_value in _NUMBER_TENS.items()
        for unit, unit_value in _NUMBER_UNITS.items()
        if 1 <= unit_value <= 9
    },
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


# --- Attribute blocks: located by section, never by line adjacency ----------
#
# Locating a block's key-count claim by line adjacency to its fence has two
# failure modes. (a) This document hard-wraps prose, so a claim that wraps
# onto an earlier line is simply *not matched* -- the pattern finds no pair,
# and the block goes unchecked while the guard stays green. (b) A
# `len(matches) >= 2` floor cannot notice a block being *deleted* -- a guard
# that cannot see something missing is not guarding presence.
#
# Both are avoided by changing the unit of enumeration to **the section**:
# every `### Attributes on ...` heading owns exactly one attribute-shaped
# fenced block, and its claim may sit anywhere in that section's prose before
# the fence, hard-wrapped or not, because the prose is whitespace-normalised
# before being searched. That matches the class of claim being guarded --
# "this published block holds N keys" is a property of a section, not of a
# line.

ATTRIBUTE_SECTION_RE = re.compile(r"^(#{2,})\s+(Attributes on .+?)\s*$", re.M)
ANY_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.M)

# A fenced block is "attribute-shaped" when every non-empty line is
# `<key><whitespace><value>` and at least one key is a dotted attribute name.
# Deliberately shape-based: it survives the sections being retitled or moved,
# and it excludes the `gen_ai.*` inventory block (bare names, no values), which
# `_on_inventory` guards by its own different shape.
_KEY_VALUE_LINE_RE = re.compile(r"^[a-z][a-z0-9_.]*\s+\S")

# The attribute blocks this document is known to publish, pinned by a
# distinguishing substring of their headings. This list is what makes a
# *deleted* block detectable: dropping a block, or dropping its whole section,
# removes the heading that matches one of these. Adding a new attribute block
# is deliberately fine -- it must simply carry a correct claim like the rest
# -- but each marker here must stay a substring of exactly one heading: two
# span kinds sharing a generic label (e.g. an accepted and a policy-denied
# `tools/call` SERVER span) need markers specific enough to tell them apart
# ("on the `tools/call` SERVER span" vs. "policy-denied").
#
# This is a hand-maintained list, so a newly published attribute section that
# is never added here is one this guard cannot see deleted -- every new
# attribute-shaped section needs a marker added in the same change that
# publishes it, not in a later sweep.
#
# Each marker is checked for ambiguity against every current heading before
# being added, which is what the len(matched) > 1 branch below demands: none
# of the markers below is a substring of any other, and in particular
# "`review.decision`" does not collide with the existing "`policy.decision`"
# marker.
REQUIRED_ATTRIBUTE_SECTIONS = (
    "on the `tools/call` SERVER span",
    "CLIENT span",
    "refused",
    "`Guardrail`",
    "policy-denied",
    "`policy.decision`",
    # The approved execution, and the span carrying the link back to the
    # refusal.
    "`settlement.execute_approved`",
    # The human's click. Between them these two are what closes Gap 2 (see
    # docs/telemetry-contract.md), so a deletion here would un-close a gap
    # this document declares closed.
    "`review.decision`",
    # The policy-adherence score span. Checked for ambiguity against every
    # current heading before being added, which is what the len(matched) > 1
    # branch below demands: "score span" is a substring of no other
    # `Attributes on ...` heading, and none of the markers above is a
    # substring of this one.
    "score span",
)

_KEY_COUNT_CLAIM_RE = re.compile(r"\b(?:(\d+)|([A-Za-z][A-Za-z-]*))\s+keys\b")


def _normalise(prose: str) -> str:
    """Collapse every whitespace run to one space.

    This single line is what closes defect (a) above: after it, a claim
    hard-wrapped across a newline reads exactly like one that is not, so where
    the claim happens to sit relative to its fence stops mattering.
    """
    return re.sub(r"\s+", " ", prose)


def _attribute_sections(text: str) -> list[dict]:
    """Every `### Attributes on ...` section, with its block and its claims.

    Returns one dict per section: `heading`, `keys` (the block's attribute
    names, in order), `claims` (every `<N> keys` number stated in the prose
    before the fence), and `blocks` (how many attribute-shaped blocks the
    section holds, so "exactly one" can be asserted rather than assumed).
    """
    starts = [(m.start(), m.group(2)) for m in ATTRIBUTE_SECTION_RE.finditer(text)]
    sections = []
    for index, (start, heading) in enumerate(starts):
        # The section runs to the next heading of any level, or to EOF.
        body_start = text.index("\n", start) + 1
        following = ANY_HEADING_RE.search(text, body_start)
        end = following.start() if following else len(text)
        body = text[body_start:end]

        blocks = []
        for match in re.finditer(r"^```[^\n]*\n(.*?)^```", body, re.S | re.M):
            lines = [line for line in match.group(1).splitlines() if line.strip()]
            if not lines:
                continue
            if all(_KEY_VALUE_LINE_RE.match(line) for line in lines) and any(
                "." in line.split()[0] for line in lines
            ):
                blocks.append((match.start(), [line.split()[0] for line in lines]))

        claims: list[int] = []
        unreadable: list[str] = []
        if blocks:
            prose_before_fence = _normalise(body[: blocks[0][0]])
            for match in _KEY_COUNT_CLAIM_RE.finditer(prose_before_fence):
                digits, word = match.group(1), match.group(2)
                if digits:
                    claims.append(int(digits))
                    continue
                kind, number = _read_number_word(word)
                if kind == "number":
                    claims.append(number)
                elif kind == "unreadable":
                    # Looks like a count but could not be read. Recorded rather
                    # than dropped, so the test can fail loudly naming it (F-5).
                    unreadable.append(word)
                # "not-a-number" ("attribute keys", "which keys") is genuinely
                # not a claim and is skipped on purpose.

        sections.append(
            {
                "heading": heading,
                "blocks": len(blocks),
                "keys": blocks[0][1] if blocks else [],
                "claims": claims,
                "unreadable": unreadable,
            }
        )
    return sections


def test_every_attribute_block_agrees_with_its_own_stated_key_count():
    """Each published attribute block must hold exactly as many keys as claimed.

    `tests/e2e/test_gateway_trace.py` asserts the same two agentgateway sets by
    exact bidirectional equality against live spans but cannot see this
    document at all; this is the join between them. The refused-call block
    has no e2e counterpart at all, so this is the only guard on it.

    **Every claim in the section's prose is checked, not just the nearest
    one.** A section that states one number and then restates a different one
    later is exactly as broken as a section that states one wrong number, and
    picking "the closest to the fence" would silently tolerate the stale twin
    -- which is this repository's single most-repeated defect.
    """
    text = _contract_text()
    sections = _attribute_sections(text)

    assert sections, (
        "found no '### Attributes on ...' section in "
        f"{CONTRACT_PATH.relative_to(REPO_ROOT)} at all, so this guard would be "
        "checking nothing. If the headings were retitled, teach "
        "ATTRIBUTE_SECTION_RE the new form rather than deleting this assertion."
    )

    problems = []
    for section in sections:
        where = section["heading"][:70]
        if section["blocks"] == 0:
            problems.append(
                f"{where!r} states it publishes attributes but contains no "
                "attribute-shaped fenced block at all"
            )
            continue
        if section["blocks"] > 1:
            problems.append(
                f"{where!r} contains {section['blocks']} attribute-shaped "
                "blocks; this guard pairs one claim with one block, so a "
                "second block would go unchecked"
            )
            continue

        keys = section["keys"]
        claims = section["claims"]

        # F-5: a claim this guard cannot read is a failure, never a skip. The
        # old code dropped an unrecognised word form silently, which meant the
        # guard passed on a claim it had never compared to anything.
        for token in section["unreadable"]:
            problems.append(
                f"{where!r} states {token!r} keys, which looks like a count but "
                "could not be read as a number, so this guard cannot check it. "
                "Write the count as digits, or teach _read_number_word the "
                "form -- do not leave it unreadable, because an unchecked "
                "claim is how every count in this document has drifted before."
            )

        if not claims and not section["unreadable"]:
            problems.append(
                f"{where!r} publishes a block of {len(keys)} keys but its prose "
                "states no '<N> keys' count anywhere before the fence, so "
                "nothing pins the block's size. State one."
            )
        for claimed in claims:
            if claimed != len(keys):
                problems.append(
                    f"{where!r} claims {claimed} keys but its block lists "
                    f"{len(keys)}: {keys}"
                )
        if len(set(keys)) != len(keys):
            duplicated = sorted({k for k in keys if keys.count(k) > 1})
            problems.append(f"{where!r}'s block lists a key twice: {duplicated}")

    assert not problems, (
        "docs/telemetry-contract.md's published attribute blocks disagree with "
        "their own stated key counts:\n  " + "\n  ".join(problems)
    )


def test_no_published_attribute_block_has_gone_missing():
    """A deleted block must fail, which counting to a floor cannot achieve.

    The check this replaces asserted `len(matches) >= 2`. With three blocks
    published that floor was satisfied by any two of them, so removing one was
    invisible -- and a guard that cannot notice something missing is not
    guarding presence, whatever its name says.

    Pinning the sections by a distinguishing substring rather than by a count
    keeps the two directions correctly asymmetric: **adding** a new attribute
    block stays green (it is held to the same claim rule as the others by the
    test above), while **removing** one of these three is a deliberate act that
    has to come here and say so.
    """
    text = _contract_text()
    headings = [section["heading"] for section in _attribute_sections(text)]

    problems = []
    for marker in REQUIRED_ATTRIBUTE_SECTIONS:
        matched = [heading for heading in headings if marker in heading]
        if not matched:
            problems.append(
                f"no '### Attributes on ...' section heading contains "
                f"{marker!r} -- a published attribute block has been removed or "
                f"retitled. Headings present: {headings}"
            )
        elif len(matched) > 1:
            problems.append(
                f"{marker!r} matches {len(matched)} headings ({matched}), so it "
                "no longer identifies one block; make the markers in "
                "REQUIRED_ATTRIBUTE_SECTIONS distinguishing again"
            )

    assert not problems, (
        "docs/telemetry-contract.md's inventory of published attribute blocks "
        "has changed:\n  " + "\n  ".join(problems)
    )


def test_every_attribute_shaped_block_sits_in_a_section_this_guard_checks():
    """No attribute block may hide outside the sections checked above.

    Without this, the two tests above are only as complete as the headings
    happen to be: a block added anywhere else in the document -- under a `##`
    of its own, or inside a section not named "Attributes on ..." -- would be
    published with no claim pinning it and nothing would notice. This counts
    attribute-shaped blocks **document-wide** and requires the total to equal
    what the sections account for, so the two enumerations cannot drift apart.
    """
    text = _contract_text()

    document_wide = 0
    for match in re.finditer(r"^```[^\n]*\n(.*?)^```", text, re.S | re.M):
        lines = [line for line in match.group(1).splitlines() if line.strip()]
        if not lines:
            continue
        if all(_KEY_VALUE_LINE_RE.match(line) for line in lines) and any(
            "." in line.split()[0] for line in lines
        ):
            document_wide += 1

    accounted = sum(section["blocks"] for section in _attribute_sections(text))
    assert document_wide == accounted, (
        f"{document_wide} attribute-shaped fenced block(s) exist in "
        f"docs/telemetry-contract.md, but only {accounted} sit inside an "
        "'### Attributes on ...' section. The unaccounted-for block(s) are "
        "published with nothing pinning their key count. Either move them under "
        "such a heading or extend this guard deliberately."
    )


# --- The document's own count of how many attribute counts it publishes -----
#
# A meta-count needs its own guard: the sentence that tells a reader how much
# of the document this guard covers can be wrong even when every count it
# describes is correct, precisely because nothing checks the count *of* them.


def test_the_documents_own_count_of_its_attribute_count_sites_is_right():
    """The meta-count: how many attribute counts this document publishes.

    **The unit matters and is stated in the document, because two defensible
    numbers exist here and they differ.** `_assignments()` yields two kinds of
    number: *primary* ones, anchored on `gen_ai.*` or on the word "names",
    which are the published counts a reader would call a "site"; and
    *secondary* ones, bare integers that merely equal one of the two mode
    figures, which the guard also holds to the right mode so that the second
    half of "28 at ON, 25 at OFF" is checkable. Counting sites gives six;
    counting every assignment the guard checks gives eight.

    "Site" is the right unit for the sentence in question -- it is telling a
    reader how many places state a count -- so six is the published number,
    with eight stated alongside it so neither figure can be mistaken for the
    other later. Both are derived here from the checker's own functions rather
    than hardcoded, so the assertion cannot drift from what the guard actually
    does.
    """
    text = _contract_text()
    on_count = len(_on_inventory(text))
    off_count = on_count - len(_content_gated_keys(text))

    sites = 0
    assignments = 0
    unqualified = 0
    for sentence in _sentences(text):
        pairs = _assignments(sentence, on_count, off_count)
        if not any(is_primary for *_, is_primary in pairs):
            continue
        for mode, _number, _quoted, is_primary in pairs:
            assignments += 1
            if is_primary:
                sites += 1
                if mode is None:
                    unqualified += 1

    # Sanity: the sibling test already forbids an unqualified primary count, so
    # every site here must be mode-carrying. Asserted rather than assumed, so
    # "mode-carrying sites" in the document cannot quietly become a different
    # quantity from "sites".
    assert unqualified == 0, (
        f"{unqualified} published attribute count(s) name no capture mode; "
        "test_every_published_attribute_count_names_its_mode_and_matches_the_"
        "inventory reports which"
    )

    # Optional `**` on either number so the markdown emphasis the document
    # actually uses is tolerated without the pattern depending on it.
    claim = re.search(
        r"there are \*{0,2}(\w+(?:-\w+)?)\*{0,2} mode-carrying such sites "
        r"\(\*{0,2}(\w+(?:-\w+)?)\*{0,2} assignments in total\)",
        text,
    )
    assert claim, (
        "expected docs/telemetry-contract.md to state its own attribute-count "
        "site total as 'there are <N> mode-carrying such sites ... (<M> "
        "assignments in total)', worded exactly so this guard can check it; "
        "if the wording must change, change it here too rather than dropping "
        "the check."
    )

    def _as_number(token: str) -> int:
        number = _WORD_NUMBERS.get(token.lower())
        if number is None:
            assert token.isdigit(), f"could not read {token!r} as a number"
            return int(token)
        return number

    published_sites = _as_number(claim.group(1))
    published_assignments = _as_number(claim.group(2))

    assert published_sites == sites, (
        f"docs/telemetry-contract.md says there are {published_sites} "
        f"mode-carrying attribute-count sites, but this guard finds {sites}. "
        "Re-measure rather than re-typing it."
    )
    assert published_assignments == assignments, (
        f"docs/telemetry-contract.md says {published_assignments} "
        f"assignments in total, but this guard checks {assignments}."
    )


# ---------------------------------------------------------------------------
# Every claim that agentgateway's HTTP attribute vocabulary diverges from
# semconv must name the pinned version.
#
# Why this needs a guard at all, rather than a careful edit. Upstream PR #3182
# is *already merged* and moves the access log -- and these span attributes
# with it -- onto stable semconv behind an opt-in. It landed 2026-09-01, after
# v1.5.0 shipped 2026-08-27. So an unqualified "agentgateway matches neither
# convention" does not merely go stale at the next image bump: it becomes
# **false**, and nothing in the suite would have said so, because
# `test_gateway_trace.py`'s set equality fails on the *attribute names* while
# saying nothing about the prose describing them.
#
# Enumerated over every site rather than spot-fixed, and checked
# whitespace-normalised per paragraph so a hard-wrapped claim is found.

VOCABULARY_CLAIM_DOCS = (
    REPO_ROOT / "docs" / "architecture.md",
    CONTRACT_PATH,
    REPO_ROOT / "docs" / "upstream-findings.md",
    REPO_ROOT / "tests" / "e2e" / "test_gateway_trace.py",
)

PINNED_GATEWAY_VERSION = "1.5.0"

# A genuine version qualifier, not a bare digit-substring:
# `PINNED_GATEWAY_VERSION in prose` would be satisfied by *any* occurrence of
# "1.5.0" in the paragraph, including one that has nothing to do with the
# gateway (mutation-proved: "the customer-ui form library is at 1.5.0" made a
# version-free claim read as scoped). Every genuine citation in
# this project writes the tag with its leading `v` -- `architecture.md:234`
# even states why: "the tag `1.5.0` does not exist on that registry" for
# agentgateway's own image, only `v1.5.0` does. Requiring `v1.5.0` as a
# word-bounded token is therefore a qualifier tied to the actual pin, not a
# coincidence of digits.
_VERSION_QUALIFIER_RE = re.compile(r"\bv1\.5\.0\b")


def _names_pinned_version(text: str) -> bool:
    return bool(_VERSION_QUALIFIER_RE.search(text))

# The assertion being guarded, in the forms this project actually writes it:
# "a third vocabulary", "matches neither", "belongs to neither", "not stable
# semconv", "three HTTP attribute vocabularies".
_VOCABULARY_CLAIM_RE = re.compile(
    r"(third (?:HTTP )?(?:attribute )?vocabular\w*"
    r"|match(?:ing|es)? neither"
    r"|belong(?:s)? to neither"
    r"|not stable semconv"
    r"|neither the stable"
    r"|three HTTP attribute vocabular\w*)",
    re.I,
)

_HEADING_LINE_RE = re.compile(r"^\s*#{1,6}\s+\S")


def _prose_paragraphs(text: str) -> list[tuple[int, str, str]]:
    """Blank-line-separated paragraphs as `(line number, prose, heading)`.

    `prose` is whitespace-normalised, so a claim hard-wrapped across lines
    reads exactly like one that is not. Leading `#`, `//` and `*` are stripped
    so the same splitter works on Markdown, comments and docstrings. `heading`
    is the nearest preceding Markdown heading, because a claim sitting under
    "Finding 7: agentgateway v1.5.0's span attributes ..." is already scoped by
    that heading and should not be forced to repeat the version inline.
    """
    lines = text.splitlines()
    paragraphs = []
    heading = ""
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        if _HEADING_LINE_RE.match(lines[index]):
            heading = lines[index].strip()
            index += 1
            continue
        start = index
        buffer = []
        while index < len(lines) and lines[index].strip():
            if _HEADING_LINE_RE.match(lines[index]):
                break
            buffer.append(re.sub(r"^\s*(?:#|//|\*)?\s*", "", lines[index]))
            index += 1
        paragraphs.append((start + 1, _normalise(" ".join(buffer)), heading))
    return paragraphs


def test_every_agentgateway_vocabulary_claim_names_the_pinned_version():
    """No divergence claim may be stated version-free.

    A claim counts as scoped if its own paragraph names the pinned version, or
    if the heading it sits under does -- "names" meaning a genuine `v1.5.0`
    token (`_names_pinned_version`), not any occurrence of the substring
    "1.5.0". Anything else is a sentence that turns false on the next image
    bump with nothing to catch it.
    """
    bare = []
    found_per_doc = {}

    for path in VOCABULARY_CLAIM_DOCS:
        assert path.exists(), f"{path} is missing; this guard cannot check it"
        hits = 0
        for line_number, prose, heading in _prose_paragraphs(path.read_text()):
            if not _VOCABULARY_CLAIM_RE.search(prose):
                continue
            hits += 1
            scoped = _names_pinned_version(prose) or _names_pinned_version(heading)
            if not scoped:
                claim = _VOCABULARY_CLAIM_RE.search(prose).group(0)
                bare.append(
                    f"{path.relative_to(REPO_ROOT)}:{line_number} asserts "
                    f"{claim!r} without naming v{PINNED_GATEWAY_VERSION} in the "
                    "paragraph or its heading"
                )
        found_per_doc[str(path.relative_to(REPO_ROOT))] = hits

    # Floor per document, so the guard cannot pass by finding nothing. If the
    # prose is reworded past this pattern the check goes quiet, which is the
    # "reports success on input it never examined" failure this milestone kept
    # hitting -- so make that state loud instead.
    empty = [doc for doc, hits in found_per_doc.items() if hits == 0]
    assert not empty, (
        f"found no agentgateway vocabulary claim in {empty}, so this guard is "
        "checking nothing there. Each of these documents states the divergence; "
        "if the wording changed, teach _VOCABULARY_CLAIM_RE the new form rather "
        f"than deleting this assertion. Counts: {found_per_doc}"
    )

    assert not bare, (
        "agentgateway's attribute-vocabulary divergence is stated without a "
        "version qualifier. Upstream PR #3182 is merged, so each of these "
        "becomes false -- not merely stale -- at the next image bump:\n  "
        + "\n  ".join(bare)
    )


# This guard catches a self-contradiction the existing e2e guard structurally
# cannot see: `docs/telemetry-contract.md:653-656` can publish "5 services"
# as the *current* trace shape eleven lines below the same document saying
# "agentgateway added as a **sixth service**".
# `test_all_five_services_are_in_one_trace`
# (tests/e2e/test_browser_root_span.py:239) asserts a *subset*
# (`expected <= services`), which stays green whether the trace has five
# services or six, so it can never catch a stale total. No other `tests/unit`
# guard reads this document's non-attribute counts at all -- the rest of this
# file checks *telemetry-attribute* claims, not service counts.
#
# This guard cross-checks the document against itself rather than against a
# live cluster, on purpose: a hermetic `tests/unit` test cannot depend on a
# kind cluster or Jaeger being up, so it cannot re-measure "how many services
# are actually in the trace today". What it *can* check is internal
# consistency -- if this document asserts, anywhere, that agentgateway was
# "added as a sixth service" (a claim about the current system), no
# unqualified "N services" total elsewhere in the same document may still say
# five, because those two sentences cannot both be true of the same trace.

_SERVICE_ORDINAL_VALUES = {
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
}
_SERVICE_ORDINAL_RE = re.compile(
    r"\b(third|fourth|fifth|sixth|seventh|eighth)\s+(?:HTTP\s+)?"
    r"(?:service|participant)s?\b",
    re.I,
)
_SERVICE_TOTAL_RE = re.compile(r"\b(\d+)\s+services\b")

# A total is exempt from the floor only if its own paragraph labels itself as
# describing a past state rather than the current system -- the same
# labelling `docs/architecture.md:983-997` uses to keep its superseded "62
# spans" figures ("Treat the counts as an M1.2-era illustration ... not as a
# measurement of the deployed system today"), and the shape this document
# uses to keep its own historical "5 services" figure.
_HISTORICAL_MARKERS = (
    "m1.1",
    "m1.2",
    "before the gateway",
    "pre-reroute",
    "original figures",
)


def test_no_unqualified_service_count_undercuts_the_documents_own_ordinal_claim():
    """A "N services" total may not sit below this document's own claim about
    how many services the trace currently has.

    Deliberately a self-consistency check, not a re-measurement: this is a
    `tests/unit` guard and must stay hermetic. It reads two things out of the
    same document -- the highest ordinal service/participant claim (e.g.
    "added as a **sixth service**", which asserts the current total is at
    least 6) as a floor, and every unqualified "N services" total -- and
    fails if any unqualified total sits below that floor. A total counts as
    qualified, and is exempt, only if its own paragraph names itself
    historical (see `_HISTORICAL_MARKERS`).

    Mutation-proved: reverting an unlabelled, stale "5 services" sentence
    back into the document makes this guard fail, because that sentence
    carries no historical marker and 5 is below the floor of 6 set by "sixth
    service" eleven lines above it.
    """
    text = CONTRACT_PATH.read_text()
    paragraphs = _prose_paragraphs(text)

    floor = 0
    floor_site = None
    for line_number, prose, _heading in paragraphs:
        for match in _SERVICE_ORDINAL_RE.finditer(prose):
            value = _SERVICE_ORDINAL_VALUES[match.group(1).lower()]
            if value > floor:
                floor = value
                floor_site = (
                    f"{CONTRACT_PATH.relative_to(REPO_ROOT)}:{line_number} "
                    f"({match.group(0)!r})"
                )

    assert floor, (
        f"{CONTRACT_PATH.relative_to(REPO_ROOT)} states no ordinal "
        "service/participant claim (e.g. 'added as a sixth service') for "
        "this guard to check a total against -- if the wording changed, "
        "teach _SERVICE_ORDINAL_RE the new form rather than deleting this "
        "assertion."
    )

    stale = []
    for line_number, prose, _heading in paragraphs:
        lowered = prose.lower()
        historical = any(marker in lowered for marker in _HISTORICAL_MARKERS)
        for match in _SERVICE_TOTAL_RE.finditer(prose):
            total = int(match.group(1))
            if total >= floor or historical:
                continue
            stale.append(
                f"{CONTRACT_PATH.relative_to(REPO_ROOT)}:{line_number} states "
                f"'{total} services' with no historical label, below the "
                f"floor of {floor} set by {floor_site}"
            )

    assert not stale, (
        "a service-count total contradicts this document's own claim about "
        "the trace's current shape:\n  " + "\n  ".join(stale)
    )
