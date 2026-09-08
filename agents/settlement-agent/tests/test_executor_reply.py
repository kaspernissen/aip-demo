"""The A2A reply shape is an interface: claims-agent and the browser parse it."""
from __future__ import annotations

import json

import pytest


class RecordingQueue:
    def __init__(self) -> None:
        self.events: list = []

    async def enqueue_event(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_exactly_one_message_is_enqueued_and_it_carries_the_full_reply(monkeypatch):
    """One Message only: a2a-sdk 0.3.7 drops anything enqueued after the first."""
    import executor as executor_mod

    async def fake_decide(claim, payments):
        return {
            "amount": 6200.0,
            "currency": "EUR",
            "status": "EXECUTED",
            "paymentId": "PAY-000001",
            "narrative": "Settled in full.",
        }

    monkeypatch.setattr(executor_mod, "decide_settlement", fake_decide)

    ex = executor_mod.SettlementAgentExecutor(payments=object())
    queue = RecordingQueue()

    class Ctx:
        @staticmethod
        def get_user_input():
            return json.dumps({"claimId": "CLM-1842", "amount": 6200, "currency": "EUR"})

    await ex.execute(Ctx(), queue)

    assert len(queue.events) == 1, f"expected exactly one Message, got {len(queue.events)}"
    payload = json.loads(queue.events[0].parts[0].root.text)
    assert payload == {
        "claimId": "CLM-1842",
        "paymentId": "PAY-000001",
        "amount": 6200.0,
        "currency": "EUR",
        "status": "EXECUTED",
        "narrative": "Settled in full.",
    }
