"""Tests the tool functions directly, and the advertised tool schema.

The schema test is the important one: destructiveHint on execute_payment is
what the Policy Decision Service reads.

On `fastmcp==4.0.3`, `FastMCP.list_tools()` returns a `list[FunctionTool]`
rather than a `dict[str, FunctionTool]`, and `FunctionTool.annotations` is a
`ToolAnnotations` pydantic model whose Python attributes are snake_case
(`destructive_hint`) with camelCase aliases for the wire format. We read the
annotation via `model_dump(by_alias=True)` so the assertion checks exactly
what is advertised to clients over MCP (`destructiveHint`), not a particular
Python attribute name.
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

    def get(self, payment_id):
        self.calls.append(("get", payment_id))
        return {
            "paymentId": payment_id,
            "claimId": "CLM-1842",
            "amount": 6200.0,
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
        "execute_payment must advertise destructiveHint=true; the policy "
        "decision service reads this annotation instead of matching tool names"
    )


def test_propose_payment_is_not_advertised_as_destructive(server_module):
    annotations = _tool_annotations(server_module, "propose_payment")
    assert annotations.get("destructiveHint") is False


# ---------------------------------------------------------------------------
# refuse_payment records a decision the PDS already made; it moves no money
# and reverses nothing, so it is advertised exactly like propose_payment --
# not destructive, and (asserted in test_rules.py on the PDS side) unlimited.
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


# ---------------------------------------------------------------------------
# `execute_approved` needs a way to read the ledger back before executing an
# approval. `get_payment` reads and moves nothing -- readOnlyHint=true,
# unlike every other tool here -- and is advertised unlimited on the PDS side
# (test_rules.py) for the same reason propose_payment is.
# ---------------------------------------------------------------------------


def test_get_payment_delegates_to_the_system_of_record(server_module, monkeypatch):
    fake = FakePayments()
    monkeypatch.setattr(server_module, "payments", fake)

    result = server_module.get_payment("PAY-000001")

    assert fake.calls == [("get", "PAY-000001")]
    assert result["paymentId"] == "PAY-000001"
    assert result["status"] == "refused"


def test_get_payment_is_advertised_as_read_only_and_not_destructive(server_module):
    annotations = _tool_annotations(server_module, "get_payment")
    assert annotations.get("readOnlyHint") is True
    assert annotations.get("destructiveHint") is False
