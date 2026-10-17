"""The registry's own shape, checked without Weaver (hermetic)."""
import re
from pathlib import Path

import yaml

REG = Path(__file__).resolve().parents[2] / "telemetry" / "registry"


def _groups():
    for f in sorted(REG.glob("*.yaml")):
        if f.name == "manifest.yaml":
            continue
        yield from (yaml.safe_load(f.read_text()) or {}).get("groups", [])


def _owned_attribute_ids():
    for g in _groups():
        for a in g.get("attributes", []):
            if "id" in a:
                yield a["id"]


def test_manifest_pins_semconv_v1_44_0():
    m = yaml.safe_load((REG / "manifest.yaml").read_text())
    assert m["name"] == "northstar"
    deps = m["dependencies"]
    assert any("v1.44.0" in d["registry_path"] for d in deps)


def test_every_owned_attribute_is_prefixed():
    ids = list(_owned_attribute_ids())
    assert ids, "no attributes defined"
    assert all(i.startswith("northstar.") for i in ids), [i for i in ids if not i.startswith("northstar.")]


def test_no_attribute_is_also_a_namespace():
    ids = set(_owned_attribute_ids())
    clashes = sorted(a for a in ids for b in ids if b.startswith(a + "."))
    assert not clashes, clashes


def test_every_owned_definition_is_development():
    for g in _groups():
        assert g.get("stability") == "development", g["id"]
        for a in g.get("attributes", []):
            if "id" in a:
                assert a.get("stability") == "development", a["id"]


def test_every_enum_member_is_snake_case():
    snake = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
    members = [
        m["id"]
        for g in _groups()
        for a in g.get("attributes", [])
        if isinstance(a.get("type"), dict)
        for m in a["type"].get("members", [])
    ]
    assert members, "no enum members defined"
    assert all(snake.match(m) for m in members), [m for m in members if not snake.match(m)]


def test_every_owned_signal_and_group_name_is_prefixed():
    """Span names, metric names and group ids are names too (naming rule 2)."""
    bad = []
    for g in _groups():
        gid = g["id"]
        if not gid.startswith(("registry.northstar.", "span.northstar.", "metric.northstar.")):
            bad.append(("group id", gid))
        if g.get("type") == "span" and not g.get("name", "").startswith("northstar."):
            bad.append(("span name", g.get("name")))
        if g.get("type") == "metric" and not g.get("metric_name", "").startswith("northstar."):
            bad.append(("metric_name", g.get("metric_name")))
    assert not bad, bad
