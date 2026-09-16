"""Every page of the portal wears one of Backstage's own page-theme gradients.

Backstage's familiar coloured header is drawn by the CLASSIC `<Page>` /
`<Header>` pair. Pages migrated to the new frontend system draw a
`bui-PluginHeader` instead, which is transparent -- so this portal ended up
with the gradient on exactly the two pages that had NOT been migrated
(`/catalog-graph`, and this repo's own `/autonomy-contract`) and flat grey
everywhere else. That reads as two decorated pages and nine unfinished ones,
which is how it was reported: *"why isn't this top element on all the
backstage pages"*.

`packages/app/src/modules/pageBanner` paints the rows Backstage already draws,
with the gradient Backstage already ships, chosen per route. Four things have
to hold for that to reach a screen, and **every one of them fails silently** --
the app builds, the pod goes Ready, the suite stays green, and the only symptom
is a page that looks exactly like it did before:

1. the module is listed in `App.tsx`'s `features` array. `createApp` ignores an
   imported-but-unlisted feature without a word.
2. the override claims the id `app/layout`. That id is composed from the
   MODULE's `pluginId` and the EXTENSION's `name`; either one wrong and the
   module attaches to nothing.
3. the CSS selectors still match the elements Backstage UI emits -- including
   their trailing `__`, which is what separates the wrapper from its children.
4. every `themeId` names a real built-in page theme. `getPageTheme` resolves an
   unknown id to the DEFAULT theme rather than throwing, so a typo is a page
   quietly wearing the wrong colour.

This module is the cheap tripwire for all four. It is not the proof: the proof
is `tests/e2e/test_backstage_page_bands.py`, which opens the running portal and
reads the computed `background-image` off the element a human is looking at.
The division is deliberate and is this repo's most expensive lesson -- five
stage-6 defects were invisible to a Ready pod and a green suite, and every one
was found by fetching the running thing.
"""
from __future__ import annotations

import re

import pytest

from test_catalog_matches_the_tree import REPO_ROOT

APP_SRC = REPO_ROOT / "platform" / "backstage" / "packages" / "app" / "src"
MODULE = APP_SRC / "modules" / "pageBanner"

# Backstage's ten built-in page themes, from `@backstage/theme`'s `pageTheme`
# record. Copied here so this module still guards a typo in a tree with no
# node_modules installed -- and cross-checked against the installed package by
# `test_the_copied_list_of_page_themes_still_matches_upstream` below, so the
# copy cannot rot the way a retyped figure does.
BUILTIN_PAGE_THEMES = {
    "home",
    "documentation",
    "tool",
    "service",
    "website",
    "library",
    "other",
    "app",
    "apis",
    "card",
}

UPSTREAM_PAGE_THEMES = (
    REPO_ROOT
    / "platform" / "backstage" / "node_modules" / "@backstage" / "theme"
    / "dist" / "base" / "pageTheme.esm.js"
)


def _module_sources() -> list:
    return sorted(MODULE.glob("*.ts")) + sorted(MODULE.glob("*.tsx"))


def _code(path) -> str:
    """A TypeScript source with its comments removed.

    Every file in this module carries a long header comment that QUOTES the
    code it is explaining -- `pluginId: 'app'`, `name: 'layout'`, the CSS
    selectors -- because the whole point of those comments is to say which
    exact strings are load-bearing and why.

    A mutation round caught what that does to a guard reading the raw text:
    changing `pluginId: 'app'` to `pluginId: 'app-banner'` in the CODE left the
    same string sitting in the COMMENT four lines above, and every assertion
    below stayed green against a module that had stopped attaching to anything.
    The guard was reading a different string than Backstage was. That is the
    same defect shape as the mermaid pass's `<br/>`, and it is why nothing here
    matches against a file's raw text.
    """
    text = path.read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _offsets_of(haystack: str, needle: str) -> list[int]:
    """Every index at which `needle` starts. `str.find` in a loop, because the
    assertions below care about each occurrence separately rather than about
    whether one exists."""
    out, at = [], haystack.find(needle)
    while at != -1:
        out.append(at)
        at = haystack.find(needle, at + 1)
    return out


def test_the_walk_is_not_vacuous():
    """Several assertions below iterate the module's own files. If that walk
    ever finds nothing -- the directory renamed, the module inlined -- they all
    pass on an empty list and this file becomes decoration."""
    sources = _module_sources()
    assert sources, (
        f"{MODULE.relative_to(REPO_ROOT)} contains no .ts or .tsx files, so "
        "every per-file assertion in this module would pass vacuously. Has the "
        "page-banner module moved or been removed?"
    )


def test_the_app_registers_the_page_banner():
    """Link 1: a module that is written, imported and never listed.

    The `features: [...]` array specifically, and with comments stripped first
    -- both of which a mutation round on the sibling mermaid guard proved
    necessary. Deleting the entry leaves the `import` line above it untouched,
    and `// pageBannerModule,` / `/* pageBannerModule, */` both sit inside the
    array and satisfy a plain membership test while registering nothing.
    """
    app_tsx = (APP_SRC / "App.tsx").read_text()

    features = re.search(r"features:\s*\[(.*?)\]", app_tsx, re.DOTALL)
    assert features, (
        "packages/app/src/App.tsx has no `features: [...]` array; has the app "
        "entry point been restructured?"
    )
    listed = re.sub(r"/\*.*?\*/", "", features.group(1), flags=re.DOTALL)
    listed = re.sub(r"//[^\n]*", "", listed)
    assert "pageBannerModule" in listed, (
        "packages/app/src/App.tsx does not list `pageBannerModule` among its "
        "`features`, so the layout override is never installed and every page "
        "renders with the flat grey top it had before."
    )


def test_the_override_claims_the_id_backstage_actually_renders():
    """Link 2: the id, which is assembled from two strings in two places.

    `@backstage/plugin-app` declares its layout as `createExtension({ name:
    'layout', attachTo: { id: 'app/root', input: 'children' } })`, and an
    extension of the same id replaces it. The id here is `pluginId` ('app',
    on the module) + `name` ('layout', on the extension). Either one wrong --
    `pluginId: 'app-layout'`, `name: 'app-layout'` -- and the module is
    well-formed, type-checks, builds, and attaches to nothing at all.

    `attachTo` is asserted too: the extension has to hang off the same input
    it is replacing, or it is a second layout rather than a replacement.
    """
    layout = _code(MODULE / "layout.tsx")

    for pattern, why in [
        (
            r"name:\s*'layout'",
            "the extension is not named 'layout', so its id is not "
            "`app/layout` and it replaces nothing",
        ),
        (
            r"pluginId:\s*'app'",
            "the module's pluginId is not 'app', so its extension's id is not "
            "`app/layout` and it replaces nothing",
        ),
        (
            r"id:\s*'app/root'",
            "the extension no longer attaches to `app/root`, so it is not in "
            "the tree Backstage renders",
        ),
        (
            r"input:\s*'children'",
            "the extension no longer attaches to `app/root`'s `children` "
            "input, which is the one the layout occupies",
        ),
    ]:
        assert re.search(pattern, layout), (
            f"modules/pageBanner/layout.tsx: {why}. This failure mode has no "
            "symptom other than the feature being absent from the screen."
        )

    # The override must still render both inputs. Dropping `nav` would lose
    # the sidebar; dropping `content` would lose every page.
    for needed in ("inputs.nav.get", "inputs.content.get", "SidebarPage"):
        assert needed in layout, (
            f"modules/pageBanner/layout.tsx no longer renders `{needed}`. This "
            "extension REPLACES Backstage's own layout, so anything it stops "
            "rendering is simply gone from the app."
        )


def test_every_band_selector_keeps_its_trailing_underscores():
    """Link 3: the selectors, and the two characters that make them right.

    Backstage UI hashes its class names (`PluginHeader_bui-PluginHeader__4bec…`)
    but keeps a stable `bui-` token inside, which is what the module matches
    on. The trailing `__` is load-bearing and looks like noise:
    `[class*="bui-PluginHeader"]` ALSO matches `bui-PluginHeaderToolbar`,
    `bui-PluginHeaderToolbarName`, `bui-PluginHeaderToolbarContent` and every
    other descendant -- and since each is painted from its own origin, the
    gradient restarts inside each one and the band comes out as a row of
    repeated smears. It still looks deliberate enough to ship.

    EVERY selector, not "at least one of them". A mutation round trimmed the
    `__` off the first of the module's three and watched this test stay green:
    a membership check is satisfied by any surviving occurrence, so the one
    selector that actually paints the band was broken while two siblings held
    the assertion up. The check is now a property of the whole set.
    """
    banner = _code(MODULE / "PageBanner.tsx")

    selectors = re.findall(r'\[class\*="([^"]+)"\]', banner)
    assert selectors, (
        "modules/pageBanner/PageBanner.tsx contains no `[class*=…]` selector "
        "at all, so the band is painted on nothing and every assertion here "
        "about those selectors passes vacuously."
    )

    untrimmed = [s for s in selectors if not s.endswith("__")]
    assert not untrimmed, (
        f"these Backstage UI selectors have lost their trailing `__`: "
        f"{untrimmed}. `[class*=\"bui-PluginHeader\"]` matches the wrapper AND "
        "every descendant Backstage UI names after it, so the gradient is "
        "redrawn from each child's own origin instead of once across the row."
    )

    assert 'bui-PluginHeader__' in selectors, (
        'modules/pageBanner/PageBanner.tsx no longer selects '
        '`bui-PluginHeader__`, which is the one element every routed page '
        "draws and therefore the only thing the band can ride on. Either "
        "Backstage UI renamed the class, or the band is gone from every page."
    )

    # And it has to be the rule that PAINTS. A mutation round renamed just the
    # painting selector to `bui-PageHeader__`; the check above stayed green
    # because the sibling rule that only sets a text colour still named
    # `bui-PluginHeader__`. The band was drawn on nothing at all, which is the
    # single worst outcome this module has, and the membership test could not
    # see it -- for the same reason the `__` check above could not, one
    # mutation earlier.
    paints = [
        banner[at : at + 400]
        for at in _offsets_of(banner, '[class*="bui-PluginHeader__"]\'')
        if "backgroundImage" in banner[at : at + 400]
    ]
    assert paints, (
        "no rule keyed on `bui-PluginHeader__` sets a `backgroundImage` in "
        "modules/pageBanner/PageBanner.tsx. Some other selector is being "
        "painted instead, so the gradient lands on an element that is not the "
        "row every page draws -- and the pages go back to a flat grey top "
        "with nothing in the build, the pod or this suite to say so."
    )

    assert 'getPageTheme(' in banner, (
        "modules/pageBanner/PageBanner.tsx no longer asks the theme for its "
        "gradient. That call is the only thing keeping the band in step with "
        "the light/dark toggle and with any future Backstage restyle."
    )


def _named_page_themes() -> dict[str, str]:
    """Every `themeId` the module assigns, keyed by the section it is for."""
    source = _code(MODULE / "pageThemes.ts")
    table = re.search(
        r"PAGE_THEME_BY_SECTION[^=]*=\s*\{(.*?)\n\};", source, re.DOTALL
    )
    assert table, (
        "modules/pageBanner/pageThemes.ts has no `PAGE_THEME_BY_SECTION = {…}` "
        "object literal; has the module been restructured?"
    )
    body = table.group(1)
    named = dict(re.findall(r"'?([\w-]*)'?\s*:\s*'([\w-]+)'", body))

    fallback = re.search(r"FALLBACK_PAGE_THEME\s*=\s*'([\w-]+)'", source)
    assert fallback, (
        "modules/pageBanner/pageThemes.ts declares no FALLBACK_PAGE_THEME, so "
        "an unmapped route has no defined colour."
    )
    named["<fallback>"] = fallback.group(1)
    return named


def test_the_walk_of_named_page_themes_is_not_vacuous():
    named = _named_page_themes()
    assert len(named) > 1, (
        "no section-to-themeId assignments were found in "
        "modules/pageBanner/pageThemes.ts, so the assertion below passes "
        "vacuously. Has the table's shape changed?"
    )


def test_every_band_names_a_real_backstage_page_theme():
    """Link 4: the typo that produces a page, not an error.

    `createBaseThemeOptions` defines the lookup as
    `pageTheme[themeId] ?? pageTheme[defaultPageTheme]`. An id that does not
    exist therefore resolves to the DEFAULT theme: `documentaton` gets teal
    instead of pink, nothing is logged, and the page looks plausible to anyone
    who does not know what it was supposed to be.
    """
    named = _named_page_themes()

    offending = {
        section: theme_id
        for section, theme_id in named.items()
        if theme_id not in BUILTIN_PAGE_THEMES
    }
    assert not offending, (
        f"these sections name a page theme Backstage does not ship: "
        f"{offending}. `getPageTheme` falls back to the default theme rather "
        "than raising, so the page renders in the wrong colour and says "
        f"nothing about it. The ten built-ins are {sorted(BUILTIN_PAGE_THEMES)}."
    )


def test_the_copied_list_of_page_themes_still_matches_upstream():
    """BUILTIN_PAGE_THEMES above is a copy, and a copy is a figure that rots.

    Derived here from the installed `@backstage/theme` rather than trusted, so
    an upgrade that adds or removes a page theme fails HERE -- naming the
    difference -- instead of turning the guard above into either a false
    failure or a hole.
    """
    if not UPSTREAM_PAGE_THEMES.is_file():
        pytest.skip(
            f"{UPSTREAM_PAGE_THEMES.relative_to(REPO_ROOT)} is absent "
            "(node_modules not installed); the copied list cannot be checked "
            "against upstream in this tree."
        )

    source = UPSTREAM_PAGE_THEMES.read_text()
    record = re.search(r"const pageTheme = \{(.*?)\n\};", source, re.DOTALL)
    assert record, (
        f"{UPSTREAM_PAGE_THEMES.relative_to(REPO_ROOT)} no longer contains a "
        "`const pageTheme = {…}` record; @backstage/theme has been "
        "restructured and this cross-check needs rewriting."
    )
    upstream = set(re.findall(r"^\s{2}'?([\w-]+)'?:", record.group(1), re.MULTILINE))

    assert upstream == BUILTIN_PAGE_THEMES, (
        "the page themes @backstage/theme ships have changed. Upstream now "
        f"has {sorted(upstream)}; this module's copy says "
        f"{sorted(BUILTIN_PAGE_THEMES)}. Update BUILTIN_PAGE_THEMES -- and "
        "check whether a new theme suits one of the portal's sections better "
        "than what it wears now."
    )


def test_the_band_borrows_backstage_colours_rather_than_owning_them():
    """No colour literal anywhere in the module.

    The same rule, for the same reason, as `modules/mermaid` and as the note in
    `App.tsx`: a full Northstar palette was built, tried for three rounds and
    removed, because recolouring a design system means owning every surface it
    has. A hex literal here would be that mistake coming back one page at a
    time -- and invisibly, since a hard-coded colour looks right in whichever
    theme it was picked in and wrong only in the other one.
    """
    colour = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")

    offending: list[str] = []
    for path in _module_sources():
        for i, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("*", "//", "/*")):
                continue  # prose may name a colour; code may not
            if colour.search(line):
                offending.append(f"{path.relative_to(REPO_ROOT)}:{i}: {stripped[:80]}")

    assert not offending, (
        "these lines hard-code a colour in the page banner, which is supposed "
        "to take every colour from the live Backstage theme so the bands "
        "follow the portal through a theme toggle and through any future "
        f"restyle: {offending}. Use `theme.getPageTheme(...)` instead."
    )
