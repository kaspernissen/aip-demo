"""The autonomy level a refusal happened at, read off the PDS rule name.

The rule shape `settlement-agent.<level>.execute_limit` is composed in exactly
one place (platform/policy-decision-service/pds/rules.py::decide). Deriving the
level from it means the ledger needs no second copy of the same fact.
"""
from __future__ import annotations

import re

_RULE = re.compile(r"settlement-agent\.(?P<level>[a-z0-9_-]+)\.execute_limit")


def autonomy_level_of(rule: str | None) -> str:
    match = _RULE.fullmatch(rule or "")
    return match.group("level") if match else "unknown"
