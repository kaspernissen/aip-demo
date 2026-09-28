"""Content capture is configuration, and its default is ON.

The point of testing this is that a silent flip to redaction would cost
diagnostic capability without anyone noticing.
"""
from __future__ import annotations

import pytest

from bootstrap import capture_content_enabled


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),      # unset -> ON
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
    ],
)
def test_capture_content_enabled_reads_the_env(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("NORTHSTAR_CAPTURE_CONTENT", raising=False)
    else:
        monkeypatch.setenv("NORTHSTAR_CAPTURE_CONTENT", value)
    assert capture_content_enabled() is expected
