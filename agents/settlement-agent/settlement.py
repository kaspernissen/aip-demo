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

There was no €5,000 check here in M1, and that was the point: M1 is ungoverned
on purpose. The model could propose any amount and nothing stopped it. That
hole is what M2's Policy Decision Service fills — and note where it is filled:
still not here. There is no limit in this file in M2.2 either. The refusal
comes from the platform, at a choke point the agent cannot reach around, and
this file's only job on that path is to let the model *see* the refusal so it
can explain it (M2.2 Task 5).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Link, SpanContext, TraceFlags
from pydantic_ai import Agent, RunContext

from correlation import set_claim_id
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
) -> None:
    """Call Task 2's `POST /payments/{id}/refuse`, through the existing
    payments client -- not a new HTTP path. A failure here must not turn a
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
        )
    except Exception:
        logger.exception(
            "payment %s: failed to record the refusal in the ledger (rule "
            "%s); the claimant still sees the refusal -- the ledger just "
            "does not carry the reviewer's facts for it",
            payment_id, refused.rule,
        )


def _refusal_link(refused_trace_id: str, refused_span_id: str) -> list[Link]:
    """A link, not a parent. A human takes minutes to review a refusal and
    the original process is gone by the time they act on it; a span held
    open across that gap would misstate both the duration and what was
    executing.

    **Measured, not assumed** (M3 Task 3): the plan's original sketch built
    this link from a `SpanContext` carrying the refusal's trace id and an
    explicit `span_id=0`, reasoning that Jaeger would render an invalid span
    id as a trace-level link. Checked directly against the installed
    opentelemetry-sdk (1.44.0): `Span._new_links` filters every link through
    `_is_valid_link(context, attributes)`, which is
    `bool(context and (context.is_valid or (attributes or context.trace_state)))`.
    A `SpanContext(span_id=0)` has `is_valid == False`, and a `Link` built
    with no `attributes` and no `trace_state` fails the OR entirely -- the
    link is dropped by the SDK, before a span processor or an exporter ever
    sees it. Confirmed by constructing exactly that span in-process and
    reading `span.links` back: it comes back empty.
    ('opentelemetry.sdk.trace' source, and a live TracerProvider probe --
    see this task's report.)

    So this carries the refusal's *span* id too (Task 2's `refusal` record
    grew a `refusedSpanId` field for exactly this in M3 Task 3), and builds a
    genuinely valid remote `SpanContext` -- both ids non-zero, so
    `context.is_valid` is `True` and the link survives `_new_links` on its
    own, no attribute workaround needed. Empty ids (no refusal to link to, or
    one recorded before this field existed) mean no link at all, rather than
    a link that would only be silently dropped.
    """
    if not refused_trace_id or not refused_span_id:
        return []
    return [
        Link(
            SpanContext(
                trace_id=int(refused_trace_id, 16),
                span_id=int(refused_span_id, 16),
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
        )
    ]


class UnverifiedApproval(RuntimeError):
    """`execute_approved` refuses to execute: the ledger carries no evidence
    that a human ever reviewed this payment.

    M3 Task 3 review, finding 1 (R11), the milestone's own anti-bypass
    criterion failing inside the milestone: `execute_approved` read no
    ledger state at all, and neither settlement Deployment had a Cilium
    ingress policy (`Allow Ingress ANY`, measured). Anything that could reach
    a settlement agent's A2A port could execute any proposed payment up to
    that identity's limit, on an unverified `reviewerId` string, with no
    model call, no proposal step and no review. This exception is what
    replaces "anything reaching the port" with "a real refusal record, for
    this exact amount, exists first" -- raised before any call to
    `payments.execute`, never after.
    """


# Mirrors systems/payments-api/handlers.go's own `amountEpsilon`: half a cent,
# the tolerance below the smallest amount anyone can actually be paid. Not
# shared code across the Go/Python boundary -- the two services do not share a
# module -- but the same number for the same reason: a float that has been
# through arithmetic somewhere upstream can land an ULP or two off the value
# it is really equal to, and refusing an execution over that would be a bug,
# not rigour. See handlers.go's own comment for the full justification.
AMOUNT_EPSILON = 0.005


async def _verify_refusal(payments: Any, payment_id: str, amount: float) -> dict[str, Any]:
    """Read the ledger back and refuse to proceed unless it shows a real,
    matching refusal. Returns the ledger record on success, so the caller can
    read facts (the claim id, in particular) that only the ledger knows.

    Lets `mcp_tools.ToolCallError` propagate unchanged when the payment does
    not exist at all (payments-api's 404, surfaced the same way every other
    ledger lookup in this repo surfaces it) -- that is already a loud
    failure and already executes nothing, so wrapping it would only relabel
    it. `UnverifiedApproval` is raised for the two cases a successful lookup
    can still fail: no refusal was ever recorded, or the amount this approval
    asks to execute does not match what was refused.
    """
    ledger_record = await payments.get(payment_id)

    if ledger_record.get("status") != "refused":
        raise UnverifiedApproval(
            f"payment {payment_id}: ledger status is "
            f"{ledger_record.get('status')!r}, not 'refused' -- no refusal "
            "record covers this payment, so execute_approved will not "
            "execute it"
        )

    ledger_amount = ledger_record.get("amount")
    if ledger_amount is None or abs(float(ledger_amount) - amount) > AMOUNT_EPSILON:
        raise UnverifiedApproval(
            f"payment {payment_id}: this approval asks to execute {amount!r}, "
            f"but the ledger's refused record is for {ledger_amount!r} -- the "
            "two must agree within the ledger's own amountEpsilon"
        )

    return ledger_record


async def execute_approved(
    payment_id: str, amount: float, currency: str, approval: dict[str, Any],
    payments: Any, refused_trace_id: str, refused_span_id: str,
) -> dict[str, Any]:
    """Execute a payment a human approved. Same tools, same gateway, different identity.

    No model call and no new prompt: the reasoning happened in the refused
    run, a human reviewed *that*, and re-deciding here would mean the human
    approved something other than what executes. This function's whole job
    is to carry an approved instruction to the payment system under an
    identity the policy permits -- nothing more. The identity is not a
    parameter here: it comes from which workload runs this process
    (`senior-settlement-agent`'s own SPIFFE ID, via SPIRE), the same way it
    always has for `execute_payment`.

    M3 Task 3 review fix round (R11): before any of that, this reads the
    ledger back and refuses unless it shows a real refusal record for this
    exact amount (`_verify_refusal`). `approval["reviewerId"]` is read first
    and still fails loudly with `KeyError` on a malformed payload, exactly as
    before this fix, and before any payment-system call at all -- the ledger
    check is an additional gate, not a replacement for that one.

    M3 Task 3 review, the question (R16): the approval payload carries no
    claim id -- unlike a claim submission, which arrives with `northstar.claim.id`
    already in inbound A2A baggage (set at claims-agent's intake) -- so this
    is the flow's own point of "intake" for that fact. The ledger record
    `_verify_refusal` already had to read carries `claimId`, so this sets it
    as baggage on the context the span and `payments.execute` run under, and
    detaches it again before returning, so it never bleeds into whatever
    request this process handles next. Without this, a reviewer correlating
    by claim id would see the refusal half of the story and not the approval
    half.
    """
    reviewer_id = approval["reviewerId"]

    ledger_record = await _verify_refusal(payments, payment_id, amount)

    claim_id = ledger_record.get("claimId")
    token = None
    if claim_id:
        token = otel_context.attach(set_claim_id(str(claim_id)))
    try:
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span(
            "settlement.execute_approved",
            links=_refusal_link(refused_trace_id, refused_span_id),
            attributes={
                "northstar.payment.id": payment_id,
                "northstar.approval.reviewer_id": reviewer_id,
            },
        ):
            # Straight to execute. No propose: the proposal is what was
            # reviewed.
            result = await payments.execute(payment_id, amount, currency)
    finally:
        if token is not None:
            otel_context.detach(token)
    return result


async def decide_settlement(claim: dict[str, Any], payments: Any) -> dict[str, Any]:
    """Settle a claim. Raises ValueError on a malformed claim, before any model call.

    Lets mcp_tools.ToolCallError propagate: a2a-sdk turns it into a JSON-RPC
    error carrying str(exc). A model apologising in prose would hide a
    failed payment.

    mcp_tools.PolicyDenial is the deliberate exception to that, and the two are
    not the same kind of event. A ToolCallError is a *malfunction* -- the
    payment system failed to do what it was asked -- and prose over the top of
    it would hide a broken payout. A PolicyDenial is the platform working
    exactly as designed: the answer is no, it is final, and the claimant is
    owed an explanation rather than a stack trace. So it returns a reply with
    status "refused" carrying the model's explanation.
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
    refused = deps.record.refused
    if executed is None and refused is not None:
        # The demo's moment: the platform refused, and the agent explains
        # rather than erroring. Before M2.2 the PolicyDenial (then a bare
        # McpError) propagated past this point and the A2A caller got a
        # JSON-RPC -32603 with no `result` at all -- the reason was legible but
        # nothing narrated it (Task 4 §11.4). The reply is a *reply*, and it
        # says refused.
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

        # M3 Task 3 Step 5: record the refusal in the ledger, so a human
        # reviewer has something to read (Task 2's `POST
        # /payments/{id}/refuse`, reached through the same governed MCP path
        # this file already uses for propose/execute -- not a new HTTP path).
        #
        # Nothing writes this record until here, and there is nothing to
        # attach it to if propose_payment itself was what got refused: no
        # payment id exists yet in that case, so there is no record to write.
        payment_id = proposed.get("paymentId")
        if payment_id:
            await _record_refusal(
                payments, payment_id, refused, narrative=result.output,
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
            "status": "refused",
            "paymentId": proposed.get("paymentId"),
            "narrative": result.output,
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
    }
