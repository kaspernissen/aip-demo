"""Every diagram in this repo is mermaid, and every one of them parses.

ASCII art in ```text fences is readable in a terminal and in `git diff`,
which is a real virtue, but it costs three things, paid over and over:

* **Layout by hand.** Columns were aligned with the space bar, so adding a node
  meant re-aligning every row under it.
* **No reflow.** A `pre` wider than its container is CLIPPED, not scrolled --
  which is exactly how the Backstage home page shipped a call-path diagram
  missing its right-hand column, with no scrollbar to say anything was gone.
* **Unreadable by anything.** A box-drawing picture of a graph is not a graph.
  Nothing could check that twelve hand-drawn pictures of the same system still
  agreed with each other, with the catalog, or with the code.

Mermaid fixes the first two outright and makes the third possible. This module
takes the first bite at the third: it finds every diagram in the tree and hands
each one to mermaid itself.

Three separate things have to hold for a diagram to reach a reader, and each is
its own way to ship a page that looks broken:

1. the diagram parses (`test_every_mermaid_diagram_parses`)
2. it is written as a raw `<pre class="mermaid">` block and NOT as a ```mermaid
   fence (`test_no_published_markdown_uses_a_mermaid_fence`)
3. it contains no `<`, because the browser parses the block's contents as HTML
   before anything reads them (`test_no_diagram_contains_html_the_browser_eats`)
4. the Backstage app registers the renderer, and it selects the element the
   docs actually contain (`test_the_app_registers_the_mermaid_renderer`)

All three fail the same quiet way: the page loads, the pod stays Ready, and a
human sees a wall of text where a picture should be.

**On point 2, which looks backwards.** The idiomatic way to write a mermaid
diagram in markdown is a ```mermaid fence, and that is what this repo tried
first. It requires MkDocs to be told that mermaid is a custom fence, which is
done with a `pymdownx.superfences` stanza whose `format` value is a
`!!python/name:` tag -- YAML's way of naming a callable. MkDocs accepts that
happily; the running image was checked, and the fence produced exactly the
`<pre class="mermaid">` wanted.

**Backstage never lets MkDocs see the file.** `plugin-techdocs-node`'s
`stages/generate/helpers` parses `mkdocs.yml` itself, in JavaScript, to inject
its own settings -- and it carries a hard allowlist of exactly three Python
YAML tags (`ALLOWED_PYTHON_YAML_TAGS`), which the superfences one is not on.
The result was `Building a newer version of this documentation failed. Error:
"Unsupported Python YAML tag ..."` and a 404 in place of every page of the
System's docs. No configuration gets past a hard-coded allowlist, so the fence
route is closed and the diagrams are raw HTML blocks instead -- which need no
MkDocs configuration of any kind, and which python-markdown passes through
verbatim (checked: `-->` and `<-->` survive `pymdownx.smartsymbols`).

That failure is also why `test_no_mkdocs_config_uses_a_python_yaml_tag`
exists. It was found by loading the page, after a green suite and a Ready pod.
"""
from __future__ import annotations

import json
import re
import subprocess

import pytest
import yaml

from test_catalog_matches_the_tree import REPO_ROOT
from test_every_component_has_techdocs import MkDocsLoader, PythonTag

PARSER = (
    REPO_ROOT / "platform" / "backstage" / "packages" / "app" / "scripts" / "parse-mermaid.mjs"
)

# Skipped wholesale: `docs/research/` is left out of the published documentation
# by the root mkdocs.yml, so it is left out here too.
SKIP_PARTS = {"node_modules", ".catalog-stage", "research", ".git", "site"}

# Mermaid's diagram keywords, as they appear on a diagram's first line. Used to
# recognise a mermaid source inside a TypeScript template literal, where there
# is no fence to key off.
DIAGRAM_KEYWORDS = (
    "flowchart",
    "graph",
    "sequenceDiagram",
    "stateDiagram",
    "stateDiagram-v2",
    "classDiagram",
    "erDiagram",
    "journey",
    "gantt",
    "pie",
    "mindmap",
    "timeline",
    "gitGraph",
    "quadrantChart",
)

_TEMPLATE_LITERAL = re.compile(r"`(\s*\n\s*(?:" + "|".join(DIAGRAM_KEYWORDS) + r")\b[^`]*)`")

# The published form of a diagram: a raw HTML block, passed through markdown
# untouched, and selected by the Backstage addon as `pre.mermaid`.
_RAW_HTML_BLOCK = re.compile(r'<pre class="mermaid">\n(.*?)</pre>', re.DOTALL)

# The one ASCII drawing that stays ASCII, named here rather than left to be
# rediscovered. `docs/demo-runbook.md`'s autonomy-contract panel is a WIREFRAME
# of a screen -- a titled box with two rows of labelled state and two buttons
# in it -- not a graph of anything. Mermaid draws relationships between nodes;
# it has no honest way to say "this is what the card looks like", and forcing
# the picture into a flowchart would replace an accurate sketch with an
# inaccurate diagram. The test below allows exactly this one, by content.
ASCII_ALLOWED = ("┌─ Autonomy contract",)


def _published_markdown():
    """The documentation a reader can actually reach.

    `docs/*.md` is the System's TechDocs site, each component's `docs/` is its
    own, and README.md is the front door for anyone with a checkout. Anything
    under SKIP_PARTS is not published and is not checked.
    """
    return sorted(
        p
        for p in REPO_ROOT.rglob("*.md")
        if not SKIP_PARTS & set(p.parts)
        and (p.name == "README.md" or "docs" in p.parts)
    )


def _fenced_blocks(text: str):
    """Yield `(fence_info, start_line, body)` for every fenced block.

    A block left open at end of file is yielded too. That is not tidiness: if
    a diagram's opening `<pre class="mermaid">` were replaced with a
    ```mermaid fence while its `</pre>` was left behind, the fence would never
    close -- and a walk that yields only on the closing fence would silently
    drop it and report the file clean.
    """
    lines = text.splitlines()
    inside = False
    info = ""
    start = 0
    body: list[str] = []
    for i, line in enumerate(lines, start=1):
        if line.startswith("```"):
            if inside:
                yield info, start, "\n".join(body)
                inside = False
            else:
                inside, info, start, body = True, line[3:].strip(), i, []
        elif inside:
            body.append(line)
    if inside:
        yield info, start, "\n".join(body)


def _diagram_sources() -> list[tuple[str, str]]:
    """Every mermaid diagram in the tree, as `(where, source)`.

Two shapes, because there are two renderers. A documentation page carries
    its diagram as a raw `<pre class="mermaid">` block (see the module
    docstring for why it is not a fence). The Backstage home page carries its
    own as a TypeScript template literal handed straight to the `<Mermaid>`
    component, because that card renders through `MarkdownContent`, which
    handles no blocks of either kind. Both are found here, so that neither can
    drift into being unparseable without this failing.
    """
    found: list[tuple[str, str]] = []

    for path in _published_markdown():
        text = path.read_text()
        for match in _RAW_HTML_BLOCK.finditer(text):
            line = text[: match.start()].count("\n") + 1
            found.append((f"{path.relative_to(REPO_ROOT)}:{line}", match.group(1)))

    app_src = REPO_ROOT / "platform" / "backstage" / "packages" / "app" / "src"
    for path in sorted(app_src.rglob("*.tsx")):
        for match in _TEMPLATE_LITERAL.finditer(path.read_text()):
            line = path.read_text()[: match.start()].count("\n") + 1
            found.append((f"{path.relative_to(REPO_ROOT)}:{line}", match.group(1)))

    return found


def _mkdocs_configs():
    return sorted(
        p for p in REPO_ROOT.rglob("mkdocs.yml") if not SKIP_PARTS & set(p.parts)
    )


def test_the_walk_is_not_vacuous():
    """The first test every guard in this repo carries: a walk that finds
    nothing passes on every tree, including an empty one."""
    diagrams = _diagram_sources()
    assert len(diagrams) >= 10, (
        f"only {len(diagrams)} mermaid diagrams were found, fewer than the "
        "floor this guard expects. Either the walk does not reach them or "
        "they have been removed -- in both cases the assertions below are "
        f"weaker than they look. Found: {[w for w, _ in diagrams]}"
    )
    assert _mkdocs_configs(), "no mkdocs.yml files found; has the tree moved?"


def test_no_published_markdown_uses_a_mermaid_fence():
    """A ```mermaid fence is the obvious thing to write and it renders as text.

    Nothing in the pipeline objects to one. MkDocs has not been told that
    mermaid is a fence language, so `pymdownx.highlight` treats it as an
    unknown lexer and falls back to plain text -- emitting
    `<code class="language-text">`, the same HTML a ```text block gets. There
    is then no class in the DOM to tell the two apart, the Backstage addon has
    nothing to select, and the page shows a reader twenty lines of
    `flowchart TD` with no error anywhere.

    Telling MkDocs about the fence is the normal fix and is closed off here;
    the module docstring gives the reason at length. So diagrams are raw
    `<pre class="mermaid">` blocks, and this guard stops the idiomatic-looking
    mistake from being made by anyone who does not know that.
    """
    offending: list[str] = []
    for path in _published_markdown():
        for info, line, _ in _fenced_blocks(path.read_text()):
            if info == "mermaid":
                offending.append(f"{path.relative_to(REPO_ROOT)}:{line}")

    assert not offending, (
        "these are ```mermaid fences, which render as a block of plain text "
        f"with no error anywhere: {offending}. Write the diagram as a raw "
        'HTML block instead -- `<pre class="mermaid">` on its own line, the '
        "diagram, then `</pre>` -- which needs no MkDocs configuration and is "
        "what the Backstage addon selects."
    )


def test_no_mkdocs_config_uses_a_python_yaml_tag():
    """Backstage parses mkdocs.yml before MkDocs does, and refuses most of them.

    `@backstage/plugin-techdocs-node`'s `stages/generate/helpers` reads the
    config in JavaScript to inject its own settings, and throws
    `Unsupported Python YAML tag '<tag>'` for any `!!python/...` tag outside a
    hard-coded allowlist of three. The whole documentation site then 404s with
    a red banner, which is at least loud -- but it is loud in the browser only:
    the pod stays Ready, the image builds, and every check in this repo passed
    while the System's five pages were unreachable.

    The allowlist is copied here from that file rather than referenced, because
    it is compiled into a dependency and nothing else in this repo can read it.
    If Backstage widens it, this list is what needs updating.
    """
    # Copied verbatim from ALLOWED_PYTHON_YAML_TAGS in
    # @backstage/plugin-techdocs-node/dist/stages/generate/helpers.cjs.js.
    allowed = {
        "tag:yaml.org,2002:python/name:materialx.emoji.twemoji",
        "tag:yaml.org,2002:python/object.apply:materialx.emoji.to_svg",
        "tag:yaml.org,2002:python/object/apply:pymdownx.slugs.slugify",
    }

    offending: list[str] = []
    for path in _mkdocs_configs():
        for tag in _python_tags(yaml.load(path.read_text(), MkDocsLoader)):
            if tag not in allowed:
                offending.append(f"{path.relative_to(REPO_ROOT)}: {tag}")

    assert not offending, (
        "these mkdocs.yml files name a Python callable with a YAML tag that "
        "Backstage's TechDocs generator refuses, so the whole site fails to "
        f"build and every page of it 404s: {offending}. Backstage allows only "
        f"{sorted(allowed)}."
    )


def _python_tags(node):
    """Every `!!python/...` tag value in a parsed mkdocs config.

    `MkDocsLoader` constructs such a tag as a `PythonTag`, a `str` subclass, so
    the value is usable everywhere a string is and still says where it came
    from. Found by walking the parsed structure rather than by grepping the
    file, so that a tag at any nesting depth is reached.
    """
    if isinstance(node, dict):
        for value in node.values():
            yield from _python_tags(value)
    elif isinstance(node, list):
        for value in node:
            yield from _python_tags(value)
    elif isinstance(node, PythonTag):
        yield str(node)


def test_every_mermaid_diagram_parses():
    """Handed to mermaid itself, not to a reimplementation of it.

    A diagram that does not parse renders as an error box or as nothing at
    all, depending on where it is -- and in both cases the page around it is
    fine, so nothing else in this repo would notice.
    """
    diagrams = _diagram_sources()
    payload = json.dumps([{"id": where, "source": src} for where, src in diagrams])

    if not (PARSER.parent.parent / "node_modules").exists() and not (
        REPO_ROOT / "platform" / "backstage" / "node_modules"
    ).exists():
        pytest.fail(
            "the Backstage workspace has no node_modules, so mermaid cannot be "
            f"asked whether these diagrams parse. Run `yarn install` in "
            "platform/backstage. This is a failure rather than a skip on "
            "purpose: a skipped diagram check and a passing one look identical "
            "in the summary line, and this repo has been caught by that before."
        )

    proc = subprocess.run(
        ["node", str(PARSER)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"the mermaid parser exited {proc.returncode}:\n{proc.stderr[-2000:]}"
    )

    results = json.loads(proc.stdout)
    assert len(results) == len(diagrams), (
        f"asked mermaid about {len(diagrams)} diagrams and got "
        f"{len(results)} answers back"
    )

    broken = [f"{r['id']}: {r['error']}" for r in results if r["error"]]
    assert not broken, (
        "these diagrams do not parse, so they will render as an error box or "
        f"as nothing: {broken}"
    )


def test_no_diagram_contains_html_the_browser_eats():
    """The contents of a raw HTML block are HTML, and the browser reads them
    first.

    This is the second defect the raw-block approach produced, and it is worse
    than the first because it is invisible from the file. The diagrams were
    written with `<br/>` for line breaks, which is the normal way to break a
    mermaid label. In the markdown file, and to the parser guard above, that is
    four literal characters. In the DOM it is an ELEMENT, and the addon reads
    the block with `textContent` -- which returns the text of the descendants
    and drops the tags. So mermaid received

        browser["browserTS, manual root span"]

    and drew it: a box with no line break and no error, on a page that looked
    fine. A guard that reads the file validates a different string than the one
    mermaid gets, which is precisely the failure this module's own docstring
    warns about in another form.

    `<` is the only dangerous character. `>` alone is text, and `<--` is text
    too -- the HTML tokenizer only opens a tag when `<` is followed by a letter,
    `!`, `/` or `?` -- so mermaid's `<-->` survives, checked in the running
    page. Everything else that needs markup has a form that is not HTML: a
    markdown string in backticks for line breaks and `**bold**`, and mermaid's
    own `#60;` / `#62;` numeric entities for a literal angle bracket.
    """
    offending: list[str] = []
    for where, source in _diagram_sources():
        if where.endswith(".tsx") or ".tsx:" in where:
            # A TypeScript template literal is handed to mermaid as a JS
            # string. It never passes through an HTML parser, so `<` is safe
            # there and this rule does not apply.
            continue
        for i, line in enumerate(source.splitlines(), start=1):
            for col, char in enumerate(line):
                if char != "<":
                    continue
                # `<--`, `<-.` and `<==` are mermaid's left-pointing arrows and
                # are not tag openers: the HTML tokenizer needs a letter, `!`,
                # `/` or `?` after the `<`.
                if line[col + 1 : col + 2] in {"-", "="}:
                    continue
                offending.append(f"{where} (+{i}): {line.strip()[:90]}")

    assert not offending, (
        "these diagram lines contain a `<` that opens an HTML tag. The diagram "
        "lives inside a raw `<pre class=\"mermaid\">` block, so the browser "
        "parses it as HTML and the tag is GONE by the time the renderer reads "
        f"the block's textContent -- silently, and only in the browser: "
        f"{offending}. Use a markdown string in backticks for line breaks and "
        "bold, and `#60;` / `#62;` for a literal angle bracket."
    )


def test_the_app_registers_the_mermaid_renderer():
    """The third link: a parsed diagram inside a correctly-fenced page still
    draws nothing if the app never mounts the addon.

    Checked as text rather than by importing the module, because this is a
    Python suite and that is a TypeScript app -- the real proof that the addon
    mounts is a rendered page, and this is the cheap tripwire that catches the
    wiring being deleted or renamed. The two selectors are the load-bearing
    part: `pre.mermaid` is the exact element MkDocs emits for the custom fence
    declared above, and `AddonBlueprint` at `TechDocsAddonLocations.Content` is
    what puts the component where the shadow root is.
    """
    app_root = REPO_ROOT / "platform" / "backstage" / "packages" / "app" / "src"

    app_tsx = (app_root / "App.tsx").read_text()
    # The `features: [...]` array specifically, not the file: deleting
    # `mermaidModule` from the array leaves the `import { mermaidModule }`
    # line above it untouched, so a check for the name anywhere in the file
    # would stay green while the addon is not registered and no diagram is
    # drawn. `createApp` ignores an imported-but-unlisted feature silently,
    # which is the whole problem.
    features = re.search(r"features:\s*\[(.*?)\]", app_tsx, re.DOTALL)
    assert features, (
        "packages/app/src/App.tsx has no `features: [...]` array; has the app "
        "entry point been restructured?"
    )
    # Comments stripped first, both forms: `// mermaidModule,` and
    # `/* mermaidModule, */` both sit inside the array and would satisfy a
    # plain membership test while registering nothing.
    listed = re.sub(r"/\*.*?\*/", "", features.group(1), flags=re.DOTALL)
    listed = re.sub(r"//[^\n]*", "", listed)
    assert "mermaidModule" in listed, (
        "packages/app/src/App.tsx does not list `mermaidModule` among its "
        "`features`, so no TechDocs page draws a diagram -- every ```mermaid "
        "block renders as the block of text MkDocs put in the HTML."
    )

    addon = (app_root / "modules" / "mermaid" / "MermaidAddon.tsx").read_text()
    for needle, why in [
        (
            "'pre.mermaid'",
            "the addon does not select `pre.mermaid`, which is the element "
            "the superfences custom fence emits -- it will find nothing",
        ),
        (
            "TechDocsAddonLocations.Content",
            "the addon is not mounted at the Content location, so it never "
            "runs where the rendered documentation is",
        ),
        (
            "AddonBlueprint",
            "the addon is not declared as a TechDocs addon at all",
        ),
    ]:
        assert needle in addon, f"modules/mermaid/MermaidAddon.tsx: {why}"

    home = (app_root / "modules" / "home" / "homeModule.tsx").read_text()
    assert "<Mermaid" in home, (
        "the home page card does not render `<Mermaid>`. That card goes "
        "through `MarkdownContent`, which has no fence handling, so the "
        "component is the only way its diagram is drawn."
    )


def test_the_diagrams_borrow_backstage_colours_rather_than_owning_them():
    """No colour literal anywhere in the mermaid module.

    Mermaid's own `dark` and `default` themes are self-consistent and belong to
    a different product: `dark` draws near-black boxes with white rules, which
    on a Backstage page reads as a screenshot from somewhere else. The fix is
    mermaid's `base` theme plus a palette -- and the palette is read off the
    live MUI theme at render time, never written down.

    That is the same decision `packages/app/src/App.tsx` records at length
    about the app as a whole: a custom Northstar palette was built, tried for
    three rounds and removed, because recolouring a design system means owning
    every surface it has. A hex literal in this module would be that mistake
    coming back one colour at a time -- and it would be invisible, because a
    hard-coded colour looks right in whichever theme it was picked in and
    wrong only in the other one.
    """
    module = (
        REPO_ROOT
        / "platform" / "backstage" / "packages" / "app" / "src" / "modules" / "mermaid"
    )
    colour = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")

    offending: list[str] = []
    for path in sorted(module.glob("*.tsx")) + sorted(module.glob("*.ts")):
        for i, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("*", "//", "/*")):
                continue  # prose may name a colour; code may not
            if colour.search(line):
                offending.append(f"{path.relative_to(REPO_ROOT)}:{i}: {stripped[:80]}")

    assert not offending, (
        "these lines hard-code a colour in the mermaid renderer, which is "
        "supposed to take every colour from the live Backstage theme so the "
        "diagrams follow the portal through a theme toggle and through any "
        f"future restyle: {offending}. Use a `palette.*` slot instead."
    )


def test_no_ascii_diagram_survives_in_the_published_docs():
    """The conversion was all of them, not most of them.

    A half-converted tree is worse than an unconverted one: two drawing styles
    on the same page reads as neglect, and the remaining ASCII keeps every
    problem the conversion was for. One drawing is deliberately exempt and is
    named in ASCII_ALLOWED with the reason.
    """
    box_drawing = re.compile(r"[─│┌┐└┘├┤┬┴┼╔╗╚╝║═▶◀▲▼]")

    offending: list[str] = []
    for path in _published_markdown():
        for info, line, body in _fenced_blocks(path.read_text()):
            if info == "mermaid" or not box_drawing.search(body):
                continue
            if any(allowed in body for allowed in ASCII_ALLOWED):
                continue
            offending.append(f"{path.relative_to(REPO_ROOT)}:{line} (```{info})")

    assert not offending, (
        "these fenced blocks are still ASCII diagrams and should be mermaid: "
        f"{offending}. If one of them genuinely cannot be a mermaid diagram -- "
        "a screen wireframe, say, rather than a graph -- add it to "
        "ASCII_ALLOWED with the reason, the way the autonomy-contract panel is."
    )
