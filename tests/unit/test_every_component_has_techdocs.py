"""Every component in the tree carries its own documentation, and the entity
points at it.

The sibling guard `test_catalog_matches_the_tree.py` makes the same argument
for `catalog-info.yaml`: a catalog is exactly the kind of list that silently
rots, so the expectation is a **directory walk** rather than a list of names.
Documentation rots the same way and for the same reason, so this module reuses
that guard's own `_component_dirs` rather than reimplementing the walk -- one
definition of "what the components are", used by both.

Three things have to line up for a docs tab to actually render, and each of
them is a separate way to ship a component whose Docs tab is empty:

1. `mkdocs.yml` beside the component's `catalog-info.yaml`
2. `docs/index.md` -- MkDocs' own entry point; a `docs/` with no index builds
   an empty site
3. `backstage.io/techdocs-ref: dir:.` on the **Component** entity, which is
   what makes Backstage look for 1 and 2 at all

A component can satisfy any two and still show nothing, which is why all three
are asserted per component rather than "docs exist somewhere".

There is a fourth link in that chain this module deliberately does NOT check,
because it is not a property of the tree: `scripts/rebuild-and-deploy.sh` has
to stage `mkdocs.yml` and `docs/` into the image beside the staged
`catalog-info.yaml`, or the running instance sees 1-3 in the repo and nothing
in the container. That is covered by the staging assertions in
`test_stage6_backstage_config.py`.
"""
from __future__ import annotations

import yaml

from test_catalog_matches_the_tree import REPO_ROOT, _component_dirs


def _component_entities(path):
    """Every `kind: Component` document in a multi-document catalog file.

    The agent components put a second entity (`kind: API`, the agent card) in
    the same file, and only the Component carries the techdocs annotation --
    so this filters rather than taking the first document.
    """
    docs = yaml.safe_load_all(path.read_text())
    return [d for d in docs if d and d.get("kind") == "Component"]


def test_the_walk_is_not_vacuous():
    """Inherited from the sibling guard's own first test, and for the same
    reason: a guard that walks nothing passes on every tree, including an
    empty one. If this list is ever empty the assertions below are all
    vacuously true and this file is decoration."""
    dirs = _component_dirs(REPO_ROOT)
    assert dirs, (
        "the component walk found no directories at all, so every assertion "
        "in this module would pass vacuously. Has the tree layout moved?"
    )


def test_every_component_ships_an_mkdocs_config_and_an_index_page():
    dirs = _component_dirs(REPO_ROOT)

    missing_mkdocs = [d.relative_to(REPO_ROOT) for d in dirs if not (d / "mkdocs.yml").is_file()]
    assert not missing_mkdocs, (
        f"these components have no mkdocs.yml, so their Docs tab renders "
        f"nothing: {missing_mkdocs}. Backstage resolves "
        "`backstage.io/techdocs-ref: dir:.` to the directory holding the "
        "component's catalog-info.yaml and expects the MkDocs config there."
    )

    missing_index = [
        d.relative_to(REPO_ROOT) for d in dirs if not (d / "docs" / "index.md").is_file()
    ]
    assert not missing_index, (
        f"these components have an mkdocs.yml but no docs/index.md: "
        f"{missing_index}. MkDocs builds an empty site from a docs/ directory "
        "with no entry point, which reads to a user as 'documentation is "
        "broken' rather than 'documentation was not written'."
    )


def test_every_mkdocs_config_actually_parses():
    """An `mkdocs.yml` that exists but does not parse fails exactly like one
    that is missing, and reports itself worse.

    Two of the nine written for this pass had an unquoted `site_description`
    whose value contained a colon --

        site_description: The one decision point: permit or deny, ...

    -- which YAML reads as a nested mapping and rejects. The component's page
    still loads, its Docs tab still appears, and the failure surfaces only as
    `bad indentation of a mapping entry (13:41)` streamed into the tab's own
    build log, at the moment a person clicks it. On stage, that is the click.

    The test above asserts the file is *there*; it was, in both cases, and
    stayed green. Existence and validity are separate claims, and the cheap
    one was the only one being made.
    """
    dirs = _component_dirs(REPO_ROOT)

    broken: list[str] = []
    for d in dirs:
        config = d / "mkdocs.yml"
        if not config.is_file():
            # Owned by the test above, which names it; reporting it twice
            # would present one defect as two.
            continue
        try:
            parsed = yaml.safe_load(config.read_text())
        except yaml.YAMLError as exc:
            first_line = str(exc).splitlines()[0]
            broken.append(f"{config.relative_to(REPO_ROOT)}: {first_line}")
            continue
        if not isinstance(parsed, dict) or not parsed.get("site_name"):
            broken.append(
                f"{config.relative_to(REPO_ROOT)}: parses, but has no `site_name`"
            )

    assert not broken, (
        "these mkdocs.yml files do not parse as a YAML mapping with a "
        f"site_name, so TechDocs fails at generation time -- inside the pod, "
        f"in a log only the person clicking the Docs tab ever sees: {broken}. "
        "The usual cause is an unquoted value containing a colon."
    )


def test_the_system_documents_the_solution():
    """The nine components each document themselves. The System documents
    what they add up to.

    A portal that can tell you what `settlement-agent` does but not what
    Northstar *is* is half a portal, and that was the state until the System
    entity got its own TechDocs. The same three links have to hold as for a
    component -- entity annotation, mkdocs.yml, docs entry point -- except the
    System's entity lives at the repo root, so `dir:.` resolves there.

    The fourth link, staging the root mkdocs.yml and docs/ into the image
    beside the staged system entity, is asserted by
    `test_rebuild_and_deploy_guard.py`'s catalog-stage test, for the reason
    this module's docstring gives.
    """
    system_entities = [
        d
        for d in yaml.safe_load_all((REPO_ROOT / "catalog-info.yaml").read_text())
        if d and d.get("kind") == "System"
    ]
    assert system_entities, (
        "the repo-root catalog-info.yaml declares no System entity, so this "
        "test proves nothing. Has the catalog layout moved?"
    )

    for entity in system_entities:
        ref = (entity.get("metadata", {}).get("annotations", {}) or {}).get(
            "backstage.io/techdocs-ref"
        )
        assert ref == "dir:.", (
            f"the System entity {entity['metadata']['name']!r} carries "
            f"techdocs-ref {ref!r}, not 'dir:.' -- so the solution "
            "documentation at the repo root is never rendered, and the portal "
            "can describe every component but not the system."
        )

    assert (REPO_ROOT / "mkdocs.yml").is_file(), (
        "no mkdocs.yml at the repo root, so the System's `techdocs-ref: dir:.` "
        "resolves to a directory with no MkDocs config in it."
    )
    assert (REPO_ROOT / "docs" / "index.md").is_file(), (
        "no docs/index.md at the repo root. The root mkdocs.yml declares "
        "`docs_dir: docs`, and MkDocs builds an empty site from a docs/ "
        "directory with no entry point."
    )


def test_every_component_entity_points_at_its_own_docs():
    dirs = _component_dirs(REPO_ROOT)

    offending: list[str] = []
    for d in dirs:
        catalog = d / "catalog-info.yaml"
        if not catalog.is_file():
            # The sibling guard owns this failure and names the path itself;
            # duplicating it here would report one defect as two.
            continue
        for entity in _component_entities(catalog):
            ref = (entity.get("metadata", {}).get("annotations", {}) or {}).get(
                "backstage.io/techdocs-ref"
            )
            if ref != "dir:.":
                offending.append(
                    f"{catalog.relative_to(REPO_ROOT)} ({entity['metadata']['name']}): {ref!r}"
                )

    assert not offending, (
        "these Component entities do not carry "
        "`backstage.io/techdocs-ref: dir:.`, so Backstage never looks for the "
        f"mkdocs.yml and docs/ sitting right beside them: {offending}. "
        "`dir:.` rather than a path: the annotation resolves relative to the "
        "entity's own location, which is this directory both in the repo and "
        "in the image the catalog is staged into."
    )
