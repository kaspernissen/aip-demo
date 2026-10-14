"""Settlement decision — a real model call.

The model decides whether and how to pay, by calling the MCP payment tools
itself.

Two deliberate properties:

  * `mcp_tools.PaymentsTools` makes every MCP call, so the verified
    `params._meta` trace-context propagation and error handling apply here
    too. The model chooses; our code calls.
  * The model narrates; `payments-api` states the amounts. The reply's
    amount/currency/status/paymentId come from what the ledger recorded,
    never from model output.

There is no spending limit in this file, deliberately: the model can propose
any amount and nothing here stops it. That check lives entirely at the
platform's Policy Decision Service, a choke point the agent cannot reach
around. The refusal comes from the platform, and this file's only job on
that path is to let the model *see* the refusal so it can explain it, and to
leave the payment waiting for a human (`awaiting_approval`). A human releases
it: the reviewer-ui, through the same gateway, under its own identity.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace
from pydantic_ai import Agent, RunContext

from mcp_tools import PolicyDenial

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic:claude-sonnet-5"


@dataclass
class PaymentRecord:
    """What the ledger actually recorded, as opposed to what the model said."""

    proposed: dict[str, Any] | None = None
    executed: dict[str, Any] | None = None
    # The platform's refusal, if there was one. Set by the tools below and read
    # by decide_settlement, which is how a refused settlement gets a reply
    # carrying the model's explanation instead of a JSON-RPC error envelope.
    refused: PolicyDenial | None = None


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
    # inspect.signature against the installed 2.42.0). A structured
    # output_type forces the model's final reply to be JSON matching that
    # schema; this agent's final turn is free-form prose for the claimant,
    # so plain text is the correct output type, not an accidental omission.
    #
    # Defer model resolution to first run. Without this, constructing the
    # Agent at import time can require ANTHROPIC_API_KEY to be present, which
    # would break the token-free unit suite (it imports this module but always
    # runs under `settlement_agent.override(model=...)` and never resolves the
    # real model). Verified parameter on Agent.__init__ at 2.42.0, default
    # False.
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


def _latch(ctx: RunContext[SettlementDeps], tool: str) -> None:
    """Once the platform has refused this settlement, send it nothing further.

    `PolicyDenial` is a `pydantic_ai.ToolFailed`, so the framework already
    declines to prompt the model to retry (see mcp_tools.PolicyDenial). But
    "the framework does not ask it to" is not "it cannot": nothing stops a
    model from calling execute_payment a second time on its own, and the spec's
    hazard is an agent that "can sit on stage retrying a payment it will never
    be allowed to make".

    So the guarantee is made here rather than assumed of the model: after a
    refusal, no further payment call leaves this process. The second attempt is
    not hidden — it is logged, and the model gets the same terminal refusal
    back, so it still knows to explain rather than keep trying.

    This state lives on the per-run `PaymentRecord`, never on `PaymentsTools`:
    that object is constructed once per process and shared by every settlement,
    so a latch there would refuse the *next* claim too.
    """
    refused = ctx.deps.record.refused
    if refused is None:
        return
    logger.warning(
        "claim %s: %s called again after a policy refusal (rule %s); "
        "not sent to the platform",
        ctx.deps.claim["claimId"],
        tool,
        refused.rule,
    )
    raise refused


@settlement_agent.tool
async def propose_payment(
    ctx: RunContext[SettlementDeps], amount: float, currency: str = "EUR"
) -> dict[str, Any]:
    """Propose a payment against this claim. Returns the payment, including its paymentId."""
    _latch(ctx, "propose_payment")
    try:
        result = await ctx.deps.payments.propose(
            ctx.deps.claim["claimId"], amount, currency
        )
    except PolicyDenial as denial:
        # Record, then re-raise unchanged. PydanticAI turns the ToolFailed into
        # a failed tool result the model reads (and does not prepend a retry
        # prompt to); decide_settlement reads the record to build a refused
        # reply rather than raising past the model's explanation.
        ctx.deps.record.refused = denial
        raise
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
    _latch(ctx, "execute_payment")
    try:
        result = await ctx.deps.payments.execute(payment_id, amount, currency)
    except PolicyDenial as denial:
        ctx.deps.record.refused = denial
        raise
    ctx.deps.record.executed = result
    return result


def _validate(claim: dict[str, Any]) -> tuple[str, float, str, str]:
    """Validate before spending tokens. The fourth value is the optional
    `a2aTaskId` (the customer's A2A task), kept on the ledger so the
    reviewer-ui can resume it."""
    claim_id = claim.get("claimId")
    if not claim_id:
        raise ValueError("claimId is required")

    amount = claim.get("amount")
    if amount is None:
        raise ValueError("amount is required")
    if amount < 0:
        raise ValueError("amount must not be negative")

    a2a_task_id = claim.get("a2aTaskId") or ""
    if not isinstance(a2a_task_id, str):
        raise ValueError("a2aTaskId must be a string")

    return claim_id, float(amount), claim.get("currency") or "EUR", a2a_task_id


def _current_trace_and_span_ids() -> tuple[str, str]:
    """The (trace_id, span_id) of whatever span is current, as lowercase hex
    -- or a pair of empty strings if there is none.

    `format(..., "032x")` on an invalid SpanContext's all-zero ids would
    produce a string that *looks* like a real, tiny id (`"0"*32`), which is
    indistinguishable from one that actually happened to be that small. So
    the empty-string case is checked explicitly (`is_valid`) rather than
    produced by formatting zero, and a caller that gets `("", "")` back knows
    there was nothing to record, not that a very unlucky id was minted.
    """
    span_ctx = trace.get_current_span().get_span_context()
    if not span_ctx.is_valid:
        return "", ""
    return format(span_ctx.trace_id, "032x"), format(span_ctx.span_id, "016x")


async def _record_refusal(
    payments: Any, payment_id: str, refused: PolicyDenial, *, narrative: str,
    a2a_task_id: str,
) -> None:
    """Call `POST /payments/{id}/refuse`, through the existing payments client
    -- not a new HTTP path. A failure here must not turn a
    refusal into an error the claimant sees: it is logged loudly and
    swallowed, because the ledger already shows the payment unexecuted, which
    is the safe state regardless of whether this record was written.
    """
    refused_trace_id, refused_span_id = _current_trace_and_span_ids()
    try:
        await payments.refuse(
            payment_id,
            rule=refused.rule,
            limit=refused.limit if refused.limit is not None else 0.0,
            requested=refused.requested if refused.requested is not None else 0.0,
            narrative=narrative,
            refused_trace_id=refused_trace_id,
            refused_span_id=refused_span_id,
            a2a_task_id=a2a_task_id,
        )
    except Exception:
        logger.exception(
            "payment %s: failed to record the refusal in the ledger (rule "
            "%s); the claimant still sees the refusal -- the ledger just "
            "does not carry the reviewer's facts for it",
            payment_id, refused.rule,
        )


async def decide_settlement(claim: dict[str, Any], payments: Any) -> dict[str, Any]:
    """Settle a claim. Raises ValueError on a malformed claim, before any model call.

    Lets mcp_tools.ToolCallError propagate: a2a-sdk turns it into a JSON-RPC
    error carrying str(exc). A model apologising in prose would hide a
    failed payment.

    mcp_tools.PolicyDenial is the deliberate exception to that, and the two are
    not the same kind of event. A ToolCallError is a *malfunction* -- the
    payment system failed to do what it was asked -- and prose over the top of
    it would hide a broken payout. A PolicyDenial is the platform working
    exactly as designed: the agent may not release this payment. The denial is
    final for the agent, but not for the claim: it routes the payment to a
    human. So it records the refusal, leaving the payment `awaiting_approval`,
    and returns a reply with that status carrying the model's explanation.
    """
    claim_id, amount, currency, a2a_task_id = _validate(claim)

    deps = SettlementDeps(
        payments=payments,
        claim={"claimId": claim_id, "amount": amount, "currency": currency},
    )

    result = await settlement_agent.run(
        f"Claim {claim_id} is assessed as covered for {amount:.0f} {currency}. Settle it.",
        deps=deps,
    )

    executed = deps.record.executed
    refused = deps.record.refused
    if executed is None and refused is not None:
        # The demo's moment: the platform refused, and the agent explains
        # rather than erroring. Without this, PolicyDenial (or a bare
        # MCPError) would propagate past this point and the A2A caller would
        # get a JSON-RPC -32603 with no `result` at all -- the reason legible
        # but nothing narrating it. The reply is a *reply*, and it says
        # awaiting_approval.
        #
        # The honesty rule is unchanged: nothing here comes from model output.
        # `amount`, `currency` and `paymentId` are what the ledger recorded for
        # the proposal -- which is exactly the state the ledger is in, since the
        # execute never happened -- and `status` is the platform's decision, not
        # the model's account of it. Only `narrative` is the model's.
        proposed = deps.record.proposed or {}
        logger.info(
            "claim %s: refused by policy (rule %s, limit %s, requested %s)",
            claim_id,
            refused.rule,
            refused.limit,
            refused.requested,
        )

        # Record the refusal in the ledger, so a human reviewer has
        # something to read (`POST /payments/{id}/refuse`, reached through
        # the same governed MCP path this file already uses for
        # propose/execute -- not a new HTTP path).
        #
        # Nothing writes this record until here, and there is nothing to
        # attach it to if propose_payment itself was what got refused: no
        # payment id exists yet in that case, so there is no record to write.
        payment_id = proposed.get("paymentId")
        if payment_id:
            await _record_refusal(
                payments, payment_id, refused, narrative=result.output,
                a2a_task_id=a2a_task_id,
            )
            # A failed recording attempt is logged inside _record_refusal and
            # swallowed there, deliberately: the ledger already shows this
            # payment unexecuted, which is the safe state, and a refusal the
            # claimant reads is not the moment to surface a platform's own
            # bookkeeping failure as their problem.
        else:
            logger.warning(
                "claim %s: refused before a payment existed to attach the "
                "refusal record to (rule %s); nothing was proposed",
                claim_id, refused.rule,
            )

        return {
            "amount": proposed.get("amount", amount),
            "currency": proposed.get("currency", currency),
            "status": "awaiting_approval",
            "paymentId": proposed.get("paymentId"),
            "narrative": result.output,
            "rule": refused.rule,
            "limit": refused.limit,
        }

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
        "rule": None,
        "limit": None,
    }
