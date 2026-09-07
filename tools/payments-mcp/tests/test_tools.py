"""Tests the tool functions directly, and the advertised tool schema.

The schema test is the important one: destructiveHint on execute_payment is
what the Policy Decision Service reads in M2.

Adapted for fastmcp==4.0.3 (amendment A16): `FastMCP.get_tools()` from the
original brief was renamed `list_tools()` and returns a `list[FunctionTool]`
rather than a `dict[str, FunctionTool]`, and `FunctionTool.annotations` is a
`ToolAnnotations` pydantic model whose Python attributes are snake_case
(`destructive_hint`) with camelCase aliases for the wire format. We read the
annotation via `model_dump(by_alias=True)` so the assertion checks exactly
what is advertised to clients over MCP (`destructiveHint`), matching the
brief's own instruction that the requirement is the wire-format spelling, not
a particular Python attribute name. The assertion itself is unchanged from
the brief; only the lookup mechanics are adapted, per the brief's own
contingency clause.
"""
import asyncio

import pytest


@pytest.fixture
def server_module(monkeypatch):
    monkeypatch.setenv("PAYMENTS_API_URL", "http://payments-api:8080")
    import server

    return server


class FakePayments:
    def __init__(self):
        self.calls = []

    def propose(self, claim_id, amount, currency="EUR"):
        self.calls.append(("propose", claim_id, amount, currency))
        return {
            "paymentId": "PAY-000001",
            "claimId": claim_id,
            "amount": amount,
            "currency": currency,
            "status": "proposed",
        }

    def execute(self, payment_id):
        self.calls.append(("execute", payment_id))
        return {
            "paymentId": payment_id,
            "claimId": "CLM-1842",
            "amount": 6200,
            "currency": "EUR",
            "status": "executed",
        }


def test_propose_payment_delegates_to_the_system_of_record(server_module, monkeypatch):
    fake = FakePayments()
    monkeypatch.setattr(server_module, "payments", fake)

    result = server_module.propose_payment("CLM-1842", 6200, "EUR")

    assert fake.calls == [("propose", "CLM-1842", 6200, "EUR")]
    assert result["status"] == "proposed"
    assert result["paymentId"] == "PAY-000001"


def test_execute_payment_delegates_to_the_system_of_record(server_module, monkeypatch):
    fake = FakePayments()
    monkeypatch.setattr(server_module, "payments", fake)

    result = server_module.execute_payment("PAY-000001")

    assert fake.calls == [("execute", "PAY-000001")]
    assert result["status"] == "executed"


def _tool_annotations(server_module, name: str) -> dict:
    tools = asyncio.run(server_module.mcp.list_tools())
    by_name = {t.name: t for t in tools}
    return by_name[name].annotations.model_dump(by_alias=True)


def test_execute_payment_is_advertised_as_destructive(server_module):
    annotations = _tool_annotations(server_module, "execute_payment")
    assert annotations.get("destructiveHint") is True, (
        "execute_payment must advertise destructiveHint=true; the M2 policy "
        "service reads this annotation instead of matching tool names"
    )


def test_propose_payment_is_not_advertised_as_destructive(server_module):
    annotations = _tool_annotations(server_module, "propose_payment")
    assert annotations.get("destructiveHint") is False
