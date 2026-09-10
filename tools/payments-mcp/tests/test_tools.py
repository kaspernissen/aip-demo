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

    def execute(self, payment_id, amount, currency="EUR"):
        self.calls.append(("execute", payment_id, amount, currency))
        return {
            "paymentId": payment_id,
            "claimId": "CLM-1842",
            "amount": amount,
            "currency": currency,
            "status": "executed",
        }

    def refuse(self, payment_id, *, rule, limit, requested, narrative,
               refused_trace_id, refused_span_id):
        self.calls.append((
            "refuse", payment_id, rule, limit, requested, narrative,
            refused_trace_id, refused_span_id,
        ))
        return {
            "paymentId": payment_id,
            "claimId": "CLM-1842",
            "amount": requested,
            "currency": "EUR",
            "status": "refused",
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

    result = server_module.execute_payment("PAY-000001", 6200.0, "EUR")

    assert fake.calls == [("execute", "PAY-000001", 6200.0, "EUR")]
    assert result["status"] == "executed"
    assert result["amount"] == 6200.0


def test_execute_payment_advertises_the_amount_as_a_required_argument(server_module):
    """The amount is what makes the execute call governable and confirmable.

    A tool advertised as taking only an opaque payment id gives the policy
    hook at the gateway nothing to decide on and payments-api nothing to
    confirm against, which is the defect this change exists to fix. If the
    argument silently becomes optional, the model can omit it and both
    checks quietly stop applying.
    """
    tools = asyncio.run(server_module.mcp.list_tools())
    schema = {t.name: t for t in tools}["execute_payment"].parameters

    assert "amount" in schema["properties"]
    assert schema["properties"]["amount"]["type"] == "number"
    assert "amount" in schema["required"]
    assert "currency" in schema["properties"]


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


# ---------------------------------------------------------------------------
# M3 Task 3: refuse_payment records a decision the PDS already made; it moves
# no money and reverses nothing, so it is advertised exactly like
# propose_payment -- not destructive, and (asserted in test_rules.py on the
# PDS side) unlimited.
# ---------------------------------------------------------------------------


def test_refuse_payment_delegates_to_the_system_of_record(server_module, monkeypatch):
    fake = FakePayments()
    monkeypatch.setattr(server_module, "payments", fake)

    result = server_module.refuse_payment(
        "PAY-000001",
        rule="execute_limit",
        limit=6000.0,
        requested=6200.0,
        narrative="narrative",
        refused_trace_id="331edba8cb21223e9de2f244360c42ef",
        refused_span_id="b7997877797c1b27",
    )

    assert fake.calls == [(
        "refuse", "PAY-000001", "execute_limit", 6000.0, 6200.0, "narrative",
        "331edba8cb21223e9de2f244360c42ef", "b7997877797c1b27",
    )]
    assert result["status"] == "refused"


def test_refuse_payment_is_not_advertised_as_destructive(server_module):
    annotations = _tool_annotations(server_module, "refuse_payment")
    assert annotations.get("destructiveHint") is False
