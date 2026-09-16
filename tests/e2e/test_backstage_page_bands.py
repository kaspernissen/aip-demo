"""The page-theme band is on the running portal, on every page, in the colour
the route is supposed to wear.

**Why this file exists rather than just the unit guard.**
`tests/unit/test_page_banner.py` reads the module's source: that the app lists
it, that the override claims `app/layout`, that the selectors keep their
trailing `__`, that every `themeId` is real. All four of those can hold while
nothing is painted, because what actually decides whether the gradient lands is
a CSS cascade inside a build -- Backstage UI ships its component CSS in
`@layer components`, this module's rules are unlayered, and unlayered wins.
Nothing in the repo proves that; only the browser does.

That division is this repo's most expensive lesson, recorded at length in
`tests/e2e/test_golden_path.py`'s own docstring: five stage-6 defects survived
a Ready pod and a green suite, and every one was found by fetching the running
thing. A band that silently stopped painting would look exactly like the state
this work was done to fix, and would say nothing anywhere.

**What it asserts, and what it deliberately does not.** It reads the computed
`background-image` off the element a human is looking at and checks two things
per route: that it carries a `linear-gradient` at all, and that the gradient's
colours are the ones `@backstage/theme` defines for that route's page theme.
Those colours are read out of the installed `@backstage/theme` at test time,
never typed here -- a literal in this file would be a second place the palette
lives, and the whole design of the module under test is that there is not one.

It does not assert anything about how the band LOOKS: height, contrast,
legibility of the tabs on a saturated ground. Those were settled by looking at
screenshots of all ten routes in both themes, which is the right instrument for
them and is not something to pretend a test is doing.

**Cost.** One headless browser, one page load per route, no model call and no
settlement. Gated behind `RUN_BROWSER_TESTS=1` like every other browser test
here, for the playwright dependency rather than for the runtime.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
import requests

from tests.harness.kube import absent_or_fail

REPO_ROOT = Path(__file__).resolve().parents[2]

KUBE_CONTEXT = "kind-northstar"
PLATFORM_NAMESPACE = "northstar-platform"
BACKSTAGE_PORT = 7007

# The palette lives in exactly one place, and it is not this file. Reading it
# out of the installed package is the same discipline the mermaid guard uses
# for parsing (hand the diagram to mermaid, not to a reimplementation of its
# grammar): a checker that carries its own copy of the thing it is checking
# agrees with itself rather than with the consumer.
UPSTREAM_PAGE_THEMES = (
    REPO_ROOT
    / "platform" / "backstage" / "node_modules" / "@backstage" / "theme"
    / "dist" / "base" / "pageTheme.esm.js"
)

# Route -> the themeId modules/pageBanner/pageThemes.ts assigns it. Written out
# here rather than parsed out of the TypeScript on purpose: this is the
# EXPECTATION, and a test that derives its expectation from the thing under
# test asserts only that the module is self-consistent. The unit guard already
# checks the module's internal shape; this one checks the result against an
# independently stated intention.
EXPECTED_BAND = {
    "/": "home",
    "/catalog": "home",
    "/catalog-graph": "home",
    "/docs": "documentation",
    "/api-docs": "apis",
    "/create": "tool",
    "/settings": "tool",
    "/autonomy-contract": "tool",
    "/catalog/default/component/settlement-agent": "home",
    "/docs/default/system/northstar": "documentation",
}

# The landing page draws no plugin-header row -- it is a bare widget grid -- so
# it is the one route whose band is an element of the module's own, and it is
# found by a different selector.
HOME_BANNER_SELECTOR = "header"
BAND_SELECTOR = '[class*="bui-PluginHeader__"]'

browser_test = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="set RUN_BROWSER_TESTS=1 and install playwright to run browser tests",
)


def _kubectl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module", autouse=True)
def stage_6_backstage_deployed_or_a_reason():
    """Skip if there is no live cluster, and skip if Backstage is simply not
    deployed -- the absence of stage 6 is not this file's regression. Same
    shape as `test_golden_path.py`'s own fixture, minus the Role check, which
    nothing here depends on.
    """
    reachable = _kubectl("cluster-info", timeout=15)
    if reachable.returncode != 0:
        pytest.skip(
            f"needs the live cluster: cannot reach {KUBE_CONTEXT} "
            f"({reachable.stderr.strip() or 'no output'})"
        )

    # `absent_or_fail`, not a raw `returncode`: ruling M4-R14's one definition
    # of "does this object exist?". A non-zero exit is a different question --
    # an unreachable API server, a wrong context and an expired credential all
    # land there, and reading the code alone turns each of them into a silent
    # "stage 6 is not installed" skip.
    deployment = _kubectl(
        "-n", PLATFORM_NAMESPACE, "get", "deployment", "backstage", "-o", "name",
    )
    if not absent_or_fail(deployment, f"deployment/backstage in {PLATFORM_NAMESPACE}"):
        pytest.skip(
            f"needs stage 6: deployment/backstage does not exist in "
            f"{PLATFORM_NAMESPACE}. Run ./scripts/setup-kind.sh --stage 6."
        )


@pytest.fixture(scope="module")
def backstage_url():
    """An ephemeral port-forward this file owns and tears down.

    `:PORT` with no local port lets kubectl pick a free one, so this can never
    collide with the repo-managed 7007 forward `scripts/start-demo.sh` may
    already hold -- the same convention `test_golden_path.py::backstage_url`
    and `tests/harness/contract.py` both already use.
    """
    proc = subprocess.Popen(
        [
            "kubectl", "--context", KUBE_CONTEXT, "-n", PLATFORM_NAMESPACE,
            "port-forward", "svc/backstage", f":{BACKSTAGE_PORT}",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    port = None
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            match = re.search(
                rf"Forwarding from 127\.0\.0\.1:(\d+) -> {BACKSTAGE_PORT}", line
            )
            if match:
                port = int(match.group(1))
                break
        assert port, (
            f"could not establish a port-forward to svc/backstage:{BACKSTAGE_PORT} "
            "within 30s"
        )
        base = f"http://127.0.0.1:{port}"
        # A bound socket proves nothing; only an answering one does.
        deadline = time.monotonic() + 30
        last_exc = None
        while time.monotonic() < deadline:
            try:
                requests.get(f"{base}/", timeout=3)
                break
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(1)
        else:
            raise AssertionError(
                f"svc/backstage never answered a plain GET / within 30s of "
                f"the port-forward binding: {last_exc}"
            )
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _upstream_palette() -> dict[str, list[str]]:
    """themeId -> its colour list, read out of the installed @backstage/theme.

    `pageTheme.esm.js` declares a `colorVariants` record and then a `pageTheme`
    record whose entries name one of those variants:

        const colorVariants = { teal: ["#005B4B"], … };
        const pageTheme = { home: genPageTheme({ colors: colorVariants.teal, … }), … };

    so resolving one needs both. Parsed rather than executed: this is a Python
    suite, the file is an ES module, and the two records are plain enough that
    a regex over them is honest about what it is doing.
    """
    source = UPSTREAM_PAGE_THEMES.read_text()

    variants_block = re.search(
        r"const colorVariants = \{(.*?)\n\};", source, re.DOTALL
    )
    assert variants_block, (
        f"{UPSTREAM_PAGE_THEMES} has no `const colorVariants = {{…}}` record; "
        "@backstage/theme has been restructured and this parse needs rewriting."
    )
    variants = {
        name: json.loads(f"[{colours}]")
        for name, colours in re.findall(
            r"(\w+): \[([^\]]*)\]", variants_block.group(1)
        )
    }

    themes_block = re.search(r"const pageTheme = \{(.*?)\n\};", source, re.DOTALL)
    assert themes_block, (
        f"{UPSTREAM_PAGE_THEMES} has no `const pageTheme = {{…}}` record; "
        "@backstage/theme has been restructured and this parse needs rewriting."
    )
    palette = {
        theme_id: variants[variant]
        for theme_id, variant in re.findall(
            r"'?([\w-]+)'?: genPageTheme\(\{\s*colors: colorVariants\.(\w+)",
            themes_block.group(1),
        )
    }
    return palette


def _expected_colours(palette: dict[str, list[str]], theme_id: str) -> list[str]:
    """The colours `genPageTheme` would put in the gradient.

    A single-colour variant is doubled (`teal` is one colour, and the gradient
    is `linear-gradient(90deg, #005B4B, #005B4B)`), which is upstream's own
    rule in `genPageTheme` and has to be mirrored here or the one-colour themes
    look like a mismatch.
    """
    colours = palette[theme_id]
    return colours if len(colours) > 1 else [colours[0], colours[0]]


def _as_computed(hex_colour: str) -> str:
    """`#005B4B` -> `rgb(0, 91, 75)`.

    `getComputedStyle` never hands back the hex the stylesheet was written in;
    CSSOM serialises every colour as `rgb(…)`. Comparing the theme's hex
    against that string matches nothing, and the mismatch looks exactly like a
    wrong page theme -- which is how this conversion came to be written: the
    first run reported all ten routes wearing the wrong colour while every one
    of them was correct.
    """
    value = hex_colour.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgb({r}, {g}, {b})"


def test_the_palette_parse_is_not_vacuous():
    """Every assertion below compares against this parse. If it ever comes back
    empty -- @backstage/theme restructured, node_modules absent -- the
    comparisons are all trivially satisfiable and this file proves nothing."""
    if not UPSTREAM_PAGE_THEMES.is_file():
        pytest.skip(
            "node_modules is not installed under platform/backstage, so the "
            "upstream palette cannot be read."
        )

    palette = _upstream_palette()
    assert palette, "parsed no page themes at all out of @backstage/theme"

    missing = sorted(set(EXPECTED_BAND.values()) - set(palette))
    assert not missing, (
        f"these expected page themes do not exist in the installed "
        f"@backstage/theme: {missing}. Either the expectation above is wrong "
        "or upstream removed a theme this portal is still asking for -- in "
        "which case `getPageTheme` is silently serving the default instead."
    )


def _goto(page, url: str, attempts: int = 4) -> None:
    """Navigate, retrying a transport failure.

    Not flake-tolerance dressed up: the failure being retried is the
    port-forward, not the portal. `kubectl port-forward` drops its connection
    whenever the pod is replaced or the API server hiccups, and the browser
    sees `ERR_CONNECTION_RESET` -- which is indistinguishable, at this level,
    from a page that genuinely did not load. Retrying separates them: a
    dropped tunnel comes back on the next attempt through the same forward,
    and a page that is actually broken fails all four the same way and is
    reported with the last error.
    """
    last = None
    for _ in range(attempts):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return
        except Exception as exc:  # noqa: BLE001 -- re-raised below if it persists
            last = exc
            page.wait_for_timeout(3000)
    raise AssertionError(f"could not load {url} in {attempts} attempts: {last}")


@browser_test
def test_every_page_wears_the_band_its_route_is_assigned(backstage_url):
    """The whole claim, read off the running portal.

    Two assertions per route, and they fail differently on purpose. "No
    gradient at all" means the cascade lost -- the module built, deployed and
    painted nothing, which is the regression this file exists for. "A gradient,
    but the wrong colours" means the route is resolving to a different page
    theme than intended, which is what `getPageTheme`'s silent fallback to the
    default theme looks like from outside.
    """
    if not UPSTREAM_PAGE_THEMES.is_file():
        pytest.skip(
            "node_modules is not installed under platform/backstage, so the "
            "expected colours cannot be read."
        )
    palette = _upstream_palette()

    from playwright.sync_api import sync_playwright

    unpainted: list[str] = []
    wrong: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        try:
            # The guest sign-in page stands in front of every route until it is
            # dismissed once; every navigation after that lands on the real
            # page.
            _goto(page, f"{backstage_url}/")
            try:
                page.get_by_role("button", name="Enter").click(timeout=10000)
            except Exception:  # noqa: BLE001 -- already signed in, nothing to dismiss
                pass
            page.wait_for_timeout(3000)

            for route, theme_id in EXPECTED_BAND.items():
                _goto(page, f"{backstage_url}{route}")
                selector = HOME_BANNER_SELECTOR if route == "/" else BAND_SELECTOR
                try:
                    page.wait_for_selector(selector, timeout=20000)
                except Exception:  # noqa: BLE001
                    unpainted.append(f"{route}: no `{selector}` element on the page")
                    continue
                background = page.eval_on_selector(
                    selector, "el => getComputedStyle(el).backgroundImage"
                )

                if "linear-gradient" not in background:
                    unpainted.append(f"{route}: background-image is {background!r}")
                    continue

                expected = [
                    _as_computed(c) for c in _expected_colours(palette, theme_id)
                ]
                missing = [c for c in expected if c not in background]
                if missing:
                    wrong.append(
                        f"{route}: expected the {theme_id!r} gradient "
                        f"{expected}, and {missing} are absent from "
                        f"{background[:120]!r}"
                    )
        finally:
            browser.close()

    assert not unpainted, (
        "these routes draw no gradient at all, so the page-theme band is not "
        f"reaching them: {unpainted}. The band is painted by unlayered CSS "
        "beating Backstage UI's own `@layer components` rules -- if that "
        "stopped working, or the `app/layout` override stopped attaching, this "
        "is the only place in the repo that can see it."
    )
    assert not wrong, (
        "these routes draw a gradient in the wrong colours, which is what "
        "`getPageTheme` resolving an unrecognised themeId to the DEFAULT page "
        f"theme looks like from outside: {wrong}."
    )
