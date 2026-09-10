"""The A2A reply shape is an interface: claims-agent and the browser parse it."""
from __future__ import annotations

import json

import pytest


class RecordingQueue:
    def __init__(self) -> None:
        self.events: list = []

    async def enqueue_event(self, event):
        self.events.append(event)


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


# ---------------------------------------------------------------------------
# M3 Task 3 Step 6: routing an approval payload to `execute_approved`.
#
# senior-settlement-agent runs this exact image and this exact executor, so
# the dispatch has to live here: the same process handles a claim submission
# (routed to decide_settlement, above) and a reviewer's approval (routed to
# execute_approved), distinguished structurally by the payload shape -- an
# `approval` key present or absent -- never by which workload is running.
# ---------------------------------------------------------------------------


class Ctx:
    def __init__(self, payload: dict):
        self._payload = payload

    def get_user_input(self):
        return json.dumps(self._payload)


async def test_an_approval_payload_is_routed_to_execute_approved(monkeypatch):
    import executor as executor_mod

    seen = {}

    async def fake_execute_approved(payment_id, amount, currency, approval, payments, refused_trace_id, refused_span_id):
        seen["args"] = (payment_id, amount, currency, approval, refused_trace_id, refused_span_id)
        return {
            "paymentId": payment_id,
            "amount": amount,
            "currency": currency,
            "status": "executed",
        }

    monkeypatch.setattr(executor_mod, "execute_approved", fake_execute_approved)

    ex = executor_mod.SettlementAgentExecutor(payments=object())
    queue = RecordingQueue()
    payload = {
        "paymentId": "PAY-000053",
        "amount": 6200.0,
        "currency": "EUR",
        "approval": {"reviewerId": "alex.mercer"},
        "refusedTraceId": "331edba8cb21223e9de2f244360c42ef",
        "refusedSpanId": "b7997877797c1b27",
    }

    await ex.execute(Ctx(payload), queue)

    assert seen["args"] == (
        "PAY-000053", 6200.0, "EUR", {"reviewerId": "alex.mercer"},
        "331edba8cb21223e9de2f244360c42ef", "b7997877797c1b27",
    )
    assert len(queue.events) == 1
    reply = json.loads(queue.events[0].parts[0].root.text)
    assert reply == {
        "paymentId": "PAY-000053",
        "amount": 6200.0,
        "currency": "EUR",
        "status": "executed",
        "reviewerId": "alex.mercer",
    }


async def test_an_approval_payload_defaults_missing_trace_and_span_ids_to_empty(monkeypatch):
    """A payload with no refusedTraceId/refusedSpanId at all (not merely
    empty) must still reach execute_approved, with empty strings -- not
    crash on a missing key. execute_approved/_refusal_link already treat
    empty as "no link"."""
    import executor as executor_mod

    seen = {}

    async def fake_execute_approved(payment_id, amount, currency, approval, payments, refused_trace_id, refused_span_id):
        seen["ids"] = (refused_trace_id, refused_span_id)
        return {"paymentId": payment_id, "amount": amount, "currency": currency, "status": "executed"}

    monkeypatch.setattr(executor_mod, "execute_approved", fake_execute_approved)

    ex = executor_mod.SettlementAgentExecutor(payments=object())
    payload = {
        "paymentId": "PAY-000053", "amount": 6200.0, "currency": "EUR",
        "approval": {"reviewerId": "alex.mercer"},
    }

    await ex.execute(Ctx(payload), RecordingQueue())

    assert seen["ids"] == ("", "")


@pytest.mark.parametrize("payload", [
    {"amount": 6200.0, "currency": "EUR", "approval": {"reviewerId": "alex.mercer"}},
    {"paymentId": "PAY-000053", "currency": "EUR", "approval": {"reviewerId": "alex.mercer"}},
    {"paymentId": "PAY-000053", "amount": 6200.0, "approval": {"reviewerId": "alex.mercer"}},
    {"paymentId": "PAY-000053", "amount": 6200.0, "currency": "EUR", "approval": {}},
    {"paymentId": "PAY-000053", "amount": 6200.0, "currency": "EUR", "approval": "alex.mercer"},
])
async def test_a_malformed_approval_payload_fails_loudly(monkeypatch, payload):
    """Never default to executing. A missing field is a ValueError, which
    a2a-sdk turns into a JSON-RPC error -- the same failure shape a malformed
    claim already gets from decide_settlement's own _validate."""
    import executor as executor_mod

    async def must_not_be_called(*args, **kwargs):
        raise AssertionError("execute_approved must not run on a malformed approval")

    monkeypatch.setattr(executor_mod, "execute_approved", must_not_be_called)

    ex = executor_mod.SettlementAgentExecutor(payments=object())
    with pytest.raises(ValueError):
        await ex.execute(Ctx(payload), RecordingQueue())
