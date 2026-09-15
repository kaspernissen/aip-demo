"""A malformed `links[].url` deletes the entity, not the link.

This guard exists because the internal-platform pass shipped one:
`platform/policy-decision-service/catalog-info.yaml` carried
`- url: /autonomy-contract`, a relative path, and Backstage's entity policy
requires a valid absolute URL. The consequence is not a broken link on an
otherwise-fine page -- the entity **fails its policy check and is never
ingested at all**. `policy-decision-service` simply was not in the catalog:
no component page, no Docs tab, no Kubernetes card, nothing to click.

What makes it worth a test rather than a fix is how it reports itself:

* the backend logs one line at **warn**, not error;
* the pod stays **Ready** -- nothing restarts, nothing alerts;
* the catalog still loads, with one fewer entity than it should have;
* and every other check in this repo still passes, because
  `test_catalog_matches_the_tree.py` asserts the *file* exists beside the
  component and `test_every_component_has_techdocs.py` asserts its
  annotation -- both read the repo, and both were green while the running
  catalog was missing the service this whole demo is about.

The only detector was a human counting components in a list. This module is
that count, moved somewhere it cannot be forgotten: every `links[].url` in
every catalog file in the tree has to be a scheme-bearing absolute URL.

Scope note: this checks the one field whose malformation is *silent*. A
missing required field (`spec.type`, say) fails loudly and obviously the
first time anyone opens the catalog, so it needs no guard of its own.
"""
from __future__ import annotations

from urllib.parse import urlparse

import yaml

from test_catalog_matches_the_tree import REPO_ROOT

# The System entity at the repo root, plus every component's own file. Found
# by walking rather than listed, for the reason the sibling guards give at
# length: a hand-maintained list of catalog files is the thing that rots.
def _catalog_files():
    return sorted(REPO_ROOT.glob("catalog-info.yaml")) + sorted(
        p
        for p in REPO_ROOT.glob("*/*/catalog-info.yaml")
        if ".catalog-stage" not in p.parts and "node_modules" not in p.parts
    )


def test_the_walk_is_not_vacuous():
    """A guard that walks nothing passes on every tree, including an empty
    one -- the same first test its two sibling guards carry, for the same
    reason."""
    files = _catalog_files()
    assert files, (
        "the catalog-file walk found nothing, so every assertion below would "
        "pass vacuously. Has the tree layout moved?"
    )


def test_every_catalog_link_is_an_absolute_url():
    offending: list[str] = []
    checked = 0

    for path in _catalog_files():
        for entity in yaml.safe_load_all(path.read_text()):
            if not entity:
                continue
            links = (entity.get("metadata") or {}).get("links") or []
            for i, link in enumerate(links):
                url = link.get("url")
                checked += 1
                parsed = urlparse(str(url))
                # Both halves matter. A bare path ("/autonomy-contract") has
                # no scheme; a string like "localhost:7007/x" parses with
                # scheme "localhost" and no netloc, which is equally not a URL.
                if not parsed.scheme or not parsed.netloc:
                    name = (entity.get("metadata") or {}).get("name", "?")
                    offending.append(
                        f"{path.relative_to(REPO_ROOT)} ({name}) links[{i}]: {url!r}"
                    )

    assert not offending, (
        "these catalog links are not absolute URLs, and Backstage's entity "
        "policy rejects the ENTIRE entity over one of them -- the component "
        f"disappears from the catalog and the backend says so once, at warn: "
        f"{offending}. Write the full scheme and host."
    )

    # The count is asserted so that a future refactor which stops finding
    # links -- a renamed key, a changed walk -- fails here instead of going
    # quietly green on zero.
    assert checked, (
        "no `metadata.links` entries were found in any catalog file, so the "
        "assertion above proved nothing. Either every link was removed from "
        "the tree, or this walk no longer reaches them."
    )
