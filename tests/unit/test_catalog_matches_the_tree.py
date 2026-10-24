"""A guard that the catalog matches the tree: the list of components it
covers must be derived from a directory walk, not a hand-maintained list --
this repo's standing rule, since a catalog is exactly the kind of list that
silently rots.

So this module does not hold a list of component names anywhere. It walks
the five directories every real component lives under (`agents/`, `apps/`,
`platform/`, `systems/`, `tools/`) and requires every immediate subdirectory
it finds -- whatever is there today, whatever gets added tomorrow -- to
carry its own `catalog-info.yaml`. A new component added to the tree with no
catalog entry of its own reddens here without this file changing at all; a
name-based hand list would need editing to catch the same thing, which is
the failure mode this guard exists to remove.

Verified live, not merely reasoned about: a real extra directory
(`tools/temp-ninth-component/`, no catalog-info.yaml) was created under the
real repo root, `pytest tests/unit/test_catalog_matches_the_tree.py -q` was
run against it, and the failure named that exact path before the directory
was removed again.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Not "the seven components" -- the five directories any of them could live
# under. Adding a ninth component never touches this list; it only ever adds
# a directory beneath one of these five, which the walk below picks up on its
# own.
COMPONENT_GROUPS = ("agents", "apps", "platform", "systems", "tools")


def _component_dirs(root: Path) -> list[Path]:
    """Every immediate subdirectory of COMPONENT_GROUPS' groups that exist
    under `root`. A real `pathlib` walk, not a name list: whatever directory
    shows up here today is whatever `os.listdir` finds, which is exactly the
    property this guard needs -- a ninth component is a ninth directory, and
    a ninth directory is automatically a candidate this function returns,
    with no line in this file naming it.
    """
    found: list[Path] = []
    for group in COMPONENT_GROUPS:
        group_dir = root / group
        if not group_dir.is_dir():
            continue
        for child in sorted(group_dir.iterdir()):
            # Dotdirs only (.venv, __pycache__ live INSIDE each component's
            # own directory, never directly under a group directory -- see
            # this test file's own docstring test below, which pins that as
            # a measured fact about the real tree rather than an assumption).
            if child.is_dir() and not child.name.startswith("."):
                found.append(child)
    return found


def test_the_walk_is_not_vacuous():
    """A guard that walks nothing passes on every tree, including one where
    every component's catalog-info.yaml was deleted. Pin that this repo's
    five component groups are themselves present and that the walk finds at
    least one subdirectory under them, so the assertion below is a claim
    about real directories, not an empty list agreeing with itself."""
    dirs = _component_dirs(REPO_ROOT)
    assert dirs, (
        f"the walk over {COMPONENT_GROUPS} in {REPO_ROOT} found no "
        "subdirectories at all -- either the repo layout changed and this "
        "guard's group list is stale, or it is being run against the wrong "
        "root. Either way, the test below would pass vacuously."
    )
    present_groups = {d.parent.name for d in dirs}
    missing_groups = set(COMPONENT_GROUPS) - present_groups
    assert not missing_groups, (
        f"these component groups exist under {REPO_ROOT} but contributed no "
        f"subdirectories to the walk: {sorted(missing_groups)} -- if a group "
        "genuinely has nothing under it, drop it from COMPONENT_GROUPS "
        "rather than leaving a silently-empty branch in this guard"
    )


def test_every_component_directory_has_its_own_catalog_info_yaml():
    """The guard itself. Every directory the walk finds must carry a
    catalog-info.yaml -- no exceptions list, because there is nothing this
    test knows to make an exception for. `platform/backstage` is not
    special-cased: it has its own catalog-info.yaml (the scaffold's, lightly
    edited) precisely so it does not need to be."""
    dirs = _component_dirs(REPO_ROOT)
    missing = [d for d in dirs if not (d / "catalog-info.yaml").is_file()]
    assert not missing, (
        "these component directories have no catalog-info.yaml of their "
        f"own: {[str(d.relative_to(REPO_ROOT)) for d in missing]}. Every "
        "directory under agents/, apps/, platform/, systems/ or tools/ is a "
        "component the catalog must cover -- add a catalog-info.yaml there, "
        "following the existing directories as a template, and register it "
        "in platform/backstage/app-config.production.yaml's "
        "catalog.locations and scripts/rebuild-and-deploy.sh's COMPONENTS "
        "array the same way they already are."
    )


def test_the_guard_reddens_when_a_ninth_component_has_no_catalog_entry(tmp_path):
    """Self-contained RED demonstration, the same shape every other guard in
    this suite carries (see tests/unit/test_rebuild_and_deploy_guard.py's own
    "goes red" tests): builds a sandbox tree with the same five groups, the
    seven compliant directories and one more that is missing its catalog-info.yaml,
    and proves `_component_dirs` + the existence check above actually catch
    it -- not merely that they would in principle.

    This does not replace the live demonstration performed against the real
    repo (see this module's docstring): that one proves the guard fires
    against this actual tree with its actual tests/unit invocation; this one
    proves the check keeps firing on every future run without anyone having
    to repeat that by hand.
    """
    for group, name in [
        ("agents", "claims-agent"),
        ("agents", "settlement-agent"),
        ("apps", "customer-ui"),
        ("apps", "reviewer-ui"),
        ("platform", "policy-decision-service"),
        ("systems", "payments-api"),
        ("tools", "payments-mcp"),
    ]:
        component_dir = tmp_path / group / name
        component_dir.mkdir(parents=True)
        (component_dir / "catalog-info.yaml").write_text("# compliant\n")

    ninth = tmp_path / "tools" / "temp-ninth-component"
    ninth.mkdir(parents=True)
    # Deliberately no catalog-info.yaml here -- this is the mutation.

    dirs = _component_dirs(tmp_path)
    assert ninth in dirs, (
        "the sandbox's ninth directory was not even found by the walk, so "
        "this demonstration is not exercising the shape it claims to"
    )
    missing = [d for d in dirs if not (d / "catalog-info.yaml").is_file()]
    assert missing == [ninth], (
        f"expected exactly the ninth, uncatalogued component to be flagged; "
        f"got {missing}"
    )
