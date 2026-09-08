"""Settlement agent behaviour, with no API key and no network.

FunctionModel drives an exact tool-call script for every case below,
including the happy path. It never touches Anthropic.
"""
from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from mcp_tools import ToolCallError
from settlement import decide_settlement, settlement_agent

CLAIM = {"claimId": "CLM-1842", "amount": 6200, "currency": "EUR"}


class FakePayments:
    """Stands in for PaymentsTools. Records calls; returns payments-api shapes."""

    def __init__(self, *, execute_raises: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self._execute_raises = execute_raises

    async def propose(self, claim_id, amount, currency="EUR"):
        self.calls.append(("propose", claim_id, amount, currency))
        return {
            "paymentId": "PAY-000001",
            "claimId": claim_id,
            "amount": float(amount),
            "currency": currency,
            "status": "PROPOSED",
        }

    async def execute(self, payment_id):
        self.calls.append(("execute", payment_id))
        if self._execute_raises:
            raise self._execute_raises
        return {
            "paymentId": payment_id,
            "claimId": "CLM-1842",
            "amount": 6200.0,
            "currency": "EUR",
            "status": "EXECUTED",
        }


def _script(*, amount: float = 6200.0, narrative: str = "Settled in full."):
    """A FunctionModel that proposes, then executes, then narrates."""
    state = {"step": 0}

    def fake_model(messages, info):
        state["step"] += 1
        if state["step"] == 1:
            return ModelResponse(
                parts=[ToolCallPart("propose_payment", {"amount": amount, "currency": "EUR"})]
            )
        if state["step"] == 2:
            return ModelResponse(
                parts=[ToolCallPart("execute_payment", {"payment_id": "PAY-000001"})]
            )
        return ModelResponse(parts=[TextPart(content=narrative)])

    return FunctionModel(fake_model)


async def _run(claim, payments, model):
    with settlement_agent.override(model=model):
        return await decide_settlement(claim, payments)


@pytest.mark.asyncio
async def test_model_calls_propose_then_execute_in_that_order():
    payments = FakePayments()
    await _run(CLAIM, payments, _script())
    assert [c[0] for c in payments.calls] == ["propose", "execute"]


@pytest.mark.asyncio
async def test_reply_amount_comes_from_the_ledger_not_from_the_model():
    """The model narrates; payments-api states the amount.

    The scripted model proposes 4000 while FakePayments.execute reports 6200.
    The returned amount must be 6200 — what was actually recorded.
    """
    payments = FakePayments()
    result = await _run(CLAIM, payments, _script(amount=4000.0, narrative="Paid 4000."))
    assert result["amount"] == 6200.0
    assert result["status"] == "EXECUTED"
    assert result["paymentId"] == "PAY-000001"


@pytest.mark.asyncio
async def test_narrative_comes_from_the_model():
    payments = FakePayments()
    result = await _run(CLAIM, payments, _script(narrative="Rear-end collision, covered."))
    assert "Rear-end collision" in result["narrative"]


@pytest.mark.asyncio
async def test_tool_call_error_propagates_to_the_caller():
    """mcp_tools.ToolCallError must not be swallowed into a narrative.

    a2a-sdk turns a raised exception into a JSON-RPC error carrying str(exc);
    a model apologising in prose instead would hide a failed payment.
    """
    payments = FakePayments(execute_raises=ToolCallError("payment already executed"))
    with pytest.raises(ToolCallError, match="already executed"):
        await _run(CLAIM, payments, _script())


@pytest.mark.asyncio
async def test_missing_claim_id_is_rejected_before_the_model_runs():
    payments = FakePayments()
    with pytest.raises(ValueError, match="claimId"):
        await _run({"amount": 100}, payments, _script())
    assert payments.calls == []


@pytest.mark.asyncio
async def test_missing_amount_is_rejected_before_the_model_runs():
    payments = FakePayments()
    with pytest.raises(ValueError, match="amount"):
        await _run({"claimId": "CLM-1"}, payments, _script())
    assert payments.calls == []


@pytest.mark.asyncio
async def test_negative_amount_is_rejected_before_the_model_runs():
    payments = FakePayments()
    with pytest.raises(ValueError, match="negative"):
        await _run({"claimId": "CLM-1", "amount": -5}, payments, _script())
    assert payments.calls == []


@pytest.mark.asyncio
async def test_model_finishing_without_executing_a_payment_raises():
    """A model that only narrates, never calling the tools, must not be

    mistaken for a settlement. This is a real risk live on stage: nothing
    forces the model to call propose_payment/execute_payment before its
    final reply. decide_settlement must raise rather than return a reply
    with no ledger-backed amount/status/paymentId.
    """
    payments = FakePayments()

    def fake_model(messages, info):
        return ModelResponse(parts=[TextPart(content="This claim looks covered.")])

    with pytest.raises(RuntimeError, match=r"CLM-1842.*executing a payment.*proposed=None"):
        await _run(CLAIM, payments, FunctionModel(fake_model))
    assert payments.calls == []
