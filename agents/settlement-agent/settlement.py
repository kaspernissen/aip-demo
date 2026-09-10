"""Settlement decision — a real model call as of M1.2.

In M1.1 this was a hardcoded function. The model now decides whether and how
to pay, by calling the MCP payment tools itself.

Two deliberate properties:

  * The MCP call path is unchanged. `mcp_tools.PaymentsTools` still makes
    every call, so the verified `params._meta` trace-context propagation and
    error handling are exactly what M1.1 proved. The model chooses; our code
    calls.
  * The model narrates; `payments-api` states the amounts. The reply's
    amount/currency/status/paymentId come from what the ledger recorded, never
    from model output. See the plan's "the rule that makes this demo honest".

There is no €5,000 check here, and that is the point: M1 is ungoverned on
purpose. The model can propose any amount and nothing stops it. That hole is
what M2's Policy Decision Service fills.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent, RunContext

DEFAULT_MODEL = "anthropic:claude-sonnet-5"


@dataclass
class PaymentRecord:
    """What the ledger actually recorded, as opposed to what the model said."""

    proposed: dict[str, Any] | None = None
    executed: dict[str, Any] | None = None


@dataclass
class SettlementDeps:
    payments: Any  # mcp_tools.PaymentsTools, or a test double
    claim: dict[str, Any]
    record: PaymentRecord = field(default_factory=PaymentRecord)


settlement_agent = Agent(
    os.environ.get("NORTHSTAR_SETTLEMENT_MODEL", DEFAULT_MODEL),
    deps_type=SettlementDeps,
    # output_type is deliberately the default `str`, not a structured
    # BaseModel. Agent.__init__'s output_type defaults to `str` (verified via
    # inspect.signature against the installed 2.41.0). A structured
    # output_type forces the model's final reply to be JSON matching that
    # schema; this agent's final turn is free-form prose for the claimant,
    # so plain text is the correct output type, not an accidental omission.
    #
    # Defer model resolution to first run. Without this, constructing the
    # Agent at import time can require ANTHROPIC_API_KEY to be present, which
    # would break the token-free unit suite (it imports this module but always
    # runs under `settlement_agent.override(model=...)` and never resolves the
    # real model). Verified parameter on Agent.__init__ at 2.41.0.
    defer_model_check=True,
    instructions=(
        "You settle motor insurance claims for Northstar Insurance.\n"
        "You are given a claim that has already been assessed as covered.\n"
        "Settle it by calling propose_payment with the assessed amount, then "
        "execute_payment with the paymentId you get back and that same "
        "amount.\n"
        "Then reply with a short narrative for the claimant.\n"
        "Settle the assessed amount. Do not round it, discount it or "
        "renegotiate it. Do not state amounts in your narrative."
    ),
)


@settlement_agent.tool
async def propose_payment(
    ctx: RunContext[SettlementDeps], amount: float, currency: str = "EUR"
) -> dict[str, Any]:
    """Propose a payment against this claim. Returns the payment, including its paymentId."""
    result = await ctx.deps.payments.propose(ctx.deps.claim["claimId"], amount, currency)
    ctx.deps.record.proposed = result
    return result


@settlement_agent.tool
async def execute_payment(
    ctx: RunContext[SettlementDeps],
    payment_id: str,
    amount: float,
    currency: str = "EUR",
) -> dict[str, Any]:
    """Execute a proposed payment. This moves money and cannot be undone.

    Pass the amount and currency you proposed. The execution states what it is
    paying rather than naming an opaque id alone, so the payment system can
    confirm the instruction matches the proposal it recorded.
    """
    result = await ctx.deps.payments.execute(payment_id, amount, currency)
    ctx.deps.record.executed = result
    return result


def _validate(claim: dict[str, Any]) -> tuple[str, float, str]:
    """Validate before spending tokens. Contract preserved from M1.1."""
    claim_id = claim.get("claimId")
    if not claim_id:
        raise ValueError("claimId is required")

    amount = claim.get("amount")
    if amount is None:
        raise ValueError("amount is required")
    if amount < 0:
        raise ValueError("amount must not be negative")

    return claim_id, float(amount), claim.get("currency") or "EUR"


async def decide_settlement(claim: dict[str, Any], payments: Any) -> dict[str, Any]:
    """Settle a claim. Raises ValueError on a malformed claim, before any model call.

    Lets mcp_tools.ToolCallError propagate: a2a-sdk turns it into a JSON-RPC
    error carrying str(exc). A model apologising in prose would hide a
    failed payment.
    """
    claim_id, amount, currency = _validate(claim)

    deps = SettlementDeps(
        payments=payments,
        claim={"claimId": claim_id, "amount": amount, "currency": currency},
    )

    result = await settlement_agent.run(
        f"Claim {claim_id} is assessed as covered for {amount:.0f} {currency}. Settle it.",
        deps=deps,
    )

    executed = deps.record.executed
    if executed is None:
        raise RuntimeError(
            f"claim {claim_id}: the model finished without executing a payment; "
            f"proposed={deps.record.proposed!r}"
        )

    return {
        "amount": executed["amount"],
        "currency": executed["currency"],
        "status": executed["status"],
        "paymentId": executed["paymentId"],
        "narrative": result.output,
    }
