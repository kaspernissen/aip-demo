"""Print latest version, upload date, licence and Python floor for PyPI packages.

Usage: python pypi_versions.py pkg [pkg ...]
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


def info(pkg: str) -> str:
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=20) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        return f"{pkg}\tNOT FOUND ({e.code})"
    i = d["info"]
    v = i["version"]
    files = d["releases"].get(v) or [{}]
    when = files[0].get("upload_time", "?")
    lic = i.get("license_expression") or (i.get("license") or "")[:40]
    deps = [x for x in (i.get("requires_dist") or []) if "extra ==" not in x]
    return f"{pkg}\t{v}\t{when}\t{lic}\tpy{i.get('requires_python')}\tdeps={deps}"


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(info(p))
