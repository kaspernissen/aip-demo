"""Read a PDS deny out of an MCP tool result's text.

The format is written by platform/policy-decision-service/pds/deny_text.py::
format_deny and documented there. This module is dependency-free on purpose:
the e2e harness (tests/harness/live.py) loads this exact file, so the agent and
the tests share one parser rather than two copies that can drift.

Recognition is by the tag at the very end of the text, never by the wording of
the reason. A reworded reason still parses; a tool error that does not end in
the tag is not a policy decision, and must stay a malfunction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_AMOUNT = r"none|-?\d+\.\d{2}"
_TAG_AT_END = re.compile(
    r"\s*\[policy-denial rule=(?P<rule>[A-Za-z0-9_.\-]+) "
    rf"limit=(?P<limit>{_AMOUNT}) requested=(?P<requested>{_AMOUNT})\]\s*\Z"
)


@dataclass(frozen=True)
class DenialText:
    reason: str
    rule: str
    limit: float | None
    requested: float | None


def _amount(raw: str) -> float | None:
    return None if raw == "none" else float(raw)


def parse_deny(text: str) -> DenialText | None:
    match = _TAG_AT_END.search(text)
    if match is None:
        return None
    return DenialText(
        reason=text[: match.start()].strip(),
        rule=match.group("rule"),
        limit=_amount(match.group("limit")),
        requested=_amount(match.group("requested")),
    )
