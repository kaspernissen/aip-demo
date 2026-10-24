"""Every Python module a service ships is in its image.

The Python services copy their modules into the image one by one
(`COPY a.py b.py ... ./`). A new module that is imported but not added to that
line passes every unit test -- they run from the source tree -- and fails only
when the pod starts: the pod crashes with `ModuleNotFoundError` and the
rollout times out.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Service directory -> the Dockerfile that builds it (customer-ui's server is
# built by the customer-ui image's second stage).
SERVICES = {
    "agents/claims-agent": "agents/claims-agent/Dockerfile",
    "agents/settlement-agent": "agents/settlement-agent/Dockerfile",
    "apps/reviewer-ui": "apps/reviewer-ui/Dockerfile",
    "apps/customer-ui/server": "apps/customer-ui/Dockerfile",
}


def _copied_modules(dockerfile: Path) -> set[str]:
    copied: set[str] = set()
    for line in dockerfile.read_text().splitlines():
        if line.startswith("COPY ") and "--from=" not in line:
            copied.update(Path(tok).name for tok in line.split()[1:-1] if tok.endswith(".py"))
    return copied


@pytest.mark.parametrize("service", sorted(SERVICES))
def test_every_top_level_module_is_copied_into_the_image(service):
    shipped = {p.name for p in (ROOT / service).glob("*.py")}
    assert shipped, f"{service} has no top-level modules; is the path right?"
    missing = shipped - _copied_modules(ROOT / SERVICES[service])
    assert not missing, (
        f"{SERVICES[service]} does not COPY {sorted(missing)}: the pod would fail to "
        f"import it at start-up while every unit test passes")


def test_the_check_reads_copy_lines_the_way_the_dockerfiles_write_them():
    sample = ROOT / "agents/claims-agent/Dockerfile"
    assert re.search(r"^COPY .*main\.py", sample.read_text(), re.M)
    assert "main.py" in _copied_modules(sample)
