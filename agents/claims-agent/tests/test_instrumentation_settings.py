"""The intake model is instrumented exactly as settlement-agent's (M13).

Content capture is configuration, its default is ON, and one variable
(`NORTHSTAR_CAPTURE_CONTENT`) switches both agents: a silent flip to redaction
in one of them would cost diagnostic capability without anyone noticing, and
two agents capturing differently would make one claim's trace half-legible.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bootstrap import capture_content_enabled

HERE = Path(__file__).resolve().parents[1] / "bootstrap.py"
SETTLEMENT = Path(__file__).resolve().parents[2] / "settlement-agent" / "bootstrap.py"


@pytest.mark.parametrize(
    "value,expected",
    [(None, True), ("true", True), ("TRUE", True), ("1", True),
     ("false", False), ("FALSE", False), ("0", False)],
)
def test_capture_content_enabled_reads_the_env(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("NORTHSTAR_CAPTURE_CONTENT", raising=False)
    else:
        monkeypatch.setenv("NORTHSTAR_CAPTURE_CONTENT", value)
    assert capture_content_enabled() is expected


def _code(path: Path, name: str) -> str:
    """A function's body without its docstring, as an AST dump."""
    func = next(n for n in ast.walk(ast.parse(path.read_text()))
                if isinstance(n, ast.FunctionDef) and n.name == name)
    body = func.body[1:] if isinstance(func.body[0], ast.Expr) else func.body
    return "\n".join(ast.dump(n) for n in body)


@pytest.mark.parametrize("name", ["capture_content_enabled", "_instrument_pydantic_ai"])
def test_the_instrumentation_is_code_identical_to_settlement_agents(name):
    assert _code(HERE, name) == _code(SETTLEMENT, name)


def test_configure_turns_both_on():
    configure = next(n for n in ast.walk(ast.parse(HERE.read_text()))
                     if isinstance(n, ast.FunctionDef) and n.name == "configure")
    called = {ast.unparse(n.func) for n in ast.walk(configure) if isinstance(n, ast.Call)}
    assert "_instrument_pydantic_ai" in called
    assert "HTTPX2ClientInstrumentor().instrument" in called
