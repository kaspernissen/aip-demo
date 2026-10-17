"""The policy, and nothing else.

Deliberately free of gRPC, OpenTelemetry and I/O so it can be exercised
exhaustively in microseconds. `server.py` adapts the wire format to this;
this module never learns what a wire is.
"""

import math
from dataclasses import dataclass

# The rule shape:
#   "settlement-agent, at autonomy level `autonomous`, may execute up to €6,000;
#    at `assisted` it may execute nothing."
# Keyed by (SPIFFE ID, autonomy level) so the identity is structurally required
# rather than merely consulted.
EXECUTE_LIMITS: dict[tuple[str, str], float] = {
    ("spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent",
     "assisted"): 0.0,
    ("spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent",
     "autonomous"): 6000.0,
}

# Maker-checker. The agent prepares every payment; above its band, a human
# releases it. The human's tool, reviewer-ui, has no limit and no autonomy
# level: it may release exactly the payments the ledger shows waiting for a
# human, and nothing else.
#
# `ledger_status` widens what the human may do, which the note above
# CALLER_SCOPED_TOOLS warns against. It is admissible for one reason: the only
# way a payment reaches `awaiting_approval` is refuse_payment, and
# CALLER_SCOPED_TOOLS lets only the settlement-agent call it. The caller this
# widens for cannot write the fact that widens it. For the agent it only
# narrows (see PROPOSED below). server.py reads the status from payments-api
# (pds/ledger.py) for every known caller of execute_payment.
#
# The same reasoning depends on a second fact, which is network and not code:
# reviewer-ui's Cilium L7 egress to payments-api allows only list, get and
# reject (deploy/platform/cilium-policies.yaml), so it cannot write a payment's
# status either. Loosen that egress and the widening above stops being safe.
HUMAN_APPROVERS: frozenset[str] = frozenset({
    "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui",
})
AWAITING_APPROVAL = "awaiting_approval"
PROPOSED = "proposed"
HUMAN_APPROVAL_RULE = "human-approved.execute"
AGENT_NOT_PROPOSED_RULE = "settlement-agent.payment_not_proposed"
# The ledger could not be read, so the agent's call is denied under a name of its
# own: "I could not look" is a different fact from "the payment is not proposed".
AGENT_LEDGER_UNREADABLE_RULE = "settlement-agent.ledger_unreadable"

# The status is read for EVERY execute_payment, and each caller class may
# execute from exactly one state: the settlement agent (the maker) only a
# payment still `proposed`, and within its amount limit; the human (the
# checker) only a payment `awaiting_approval`. So the maker cannot release a
# payment it routed to a human, even at `autonomous` and under the limit, and
# a payment that is `executed` or `rejected` is executable by nobody. An
# unreadable ledger is None and denies for both (the agent's denial is named
# `settlement-agent.ledger_unreadable`, the human's stays `human-approved.execute`).

# EXECUTE_LIMITS is keyed by (caller, level), so a miss below is ambiguous
# between "this caller has no row at all" and "this caller is known, but not
# at this level" -- a contract typo in the autonomy level (or an unreachable
# variant) would otherwise be reported under the same `unknown_caller` rule
# name as a genuinely unrecognised identity. The label names the wrong
# dimension in that second case: the caller IS known, the level is not, and
# in an audit trail that distinction is the whole point. Derived from
# EXECUTE_LIMITS' own keys, not retyped, so
# a caller added or removed there needs no matching edit here.
_KNOWN_CALLERS = frozenset(caller for caller, _level in EXECUTE_LIMITS)

# Tools with no world-changing effect. Proposing is reversible; executing moves
# money. Only the latter is limited.
#
# `refuse_payment` joins `propose_payment` here. It is settlement-agent
# recording, through the same governed MCP path everything else in this
# repo uses, the reason a payment already did not execute -- it moves no
# money and reverses nothing, so it is unlimited for the same reason
# proposing is.
#
# `get_payment` joins them too: the reviewer-ui and claims-agent read through
# it or alongside it, and a read that moves nothing and reverses nothing is
# exactly this set's own admission rule.
UNLIMITED_TOOLS = frozenset({"propose_payment", "refuse_payment", "get_payment"})
LIMITED_TOOLS = frozenset({"execute_payment"})

# Without this table, `refuse_payment` would be unconditional for ANY
# caller, including `caller=None` -- so any holder of a valid SVID (or, in
# principle, none at all) could mark an arbitrary `proposed` payment `refused`
# with attacker-chosen `rule`/`limit`/`requested`/`narrative`, inject it into
# the reviewer's queue, and overwrite an existing refusal's
# `refusedTraceId`/`refusedSpanId` -- authorship of the exact surface a human
# reads before releasing money.
#
# A caller restriction here does not need a branch in `decide()`: the
# no-new-logic constraint exists to stop the policy engine acquiring an
# input that WIDENS what is permitted -- an override flag, an approval
# token. A caller restriction on an already-unlimited tool NARROWS what is
# permitted; it is data, exactly like EXECUTE_LIMITS above, not a widening
# input. So this is one more table, consulted by decide() as one more
# lookup -- never a tool-name branch.
#
# Only tools actually named here are restricted; a tool in UNLIMITED_TOOLS but
# absent from this mapping (propose_payment, get_payment) stays unconditional,
# because nothing about proposing or reading widens what a human reviews.
CALLER_SCOPED_TOOLS: dict[str, frozenset[str]] = {
    "refuse_payment": frozenset({
        "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent",
    }),
}


@dataclass(frozen=True)
class PolicyInput:
    caller: str | None
    tool: str
    amount: float | None
    currency: str
    autonomy_level: str
    # What payments-api shows for the payment, read once and only for a caller
    # in HUMAN_APPROVERS (see the note above HUMAN_APPROVERS). None when not
    # read or unreadable.
    ledger_status: str | None = None


@dataclass(frozen=True)
class Decision:
    permit: bool
    reason: str
    rule: str
    limit: float | None = None
    # Always False. A denial the agent may retry is a denial it can sit on
    # stage retrying; see the spec's §5 hazard.
    retryable: bool = False


def _money(amount: float, currency: str) -> str:
    """`6200 EUR`, or `6200` when no currency was sent.

    The refusal text is narrated to a customer, so it must not read
    "requested 6200  exceeds the 6000  limit" -- a double space where the unit
    should be. `server.py`'s `ToolCall` deliberately falls back to an empty
    currency rather than inventing "EUR" for a request that did not carry one,
    and this is where that honest empty string is made to read properly.

    Not reachable in normal operation: `settlement-agent` always serialises
    `currency`. A latent wart, fixed because it is one helper.
    """
    return f"{amount:.0f} {currency}".strip()


def decide(req: PolicyInput) -> Decision:
    if req.tool in UNLIMITED_TOOLS:
        allowed_callers = CALLER_SCOPED_TOOLS.get(req.tool)
        if allowed_callers is not None and req.caller not in allowed_callers:
            return Decision(
                False,
                f"{req.tool} may not be called by caller {req.caller!r}",
                rule="unlimited_tool_caller_not_allowed",
            )
        return Decision(True, f"{req.tool} has no effect to authorise",
                        rule="unlimited_tool")

    if req.tool not in LIMITED_TOOLS:
        return Decision(False, f"unknown tool {req.tool!r}", rule="unknown_tool")

    if (req.caller or "") in HUMAN_APPROVERS:
        return _human_approval(req)

    limit = EXECUTE_LIMITS.get((req.caller or "", req.autonomy_level))
    if limit is None:
        # Fail-closed behaviour is identical either way -- a dict miss denies,
        # full stop. Only the audit-trail LABEL changes: `unknown_caller`
        # when the identity itself has no row at any level,
        # `unknown_autonomy_level` when the identity is known but this
        # particular level is not (see the note above `_KNOWN_CALLERS`).
        if (req.caller or "") not in _KNOWN_CALLERS:
            return Decision(
                False,
                f"no execute allowance for caller {req.caller!r} at autonomy "
                f"level {req.autonomy_level!r}",
                rule="unknown_caller",
            )
        return Decision(
            False,
            f"no execute allowance for caller {req.caller!r} at autonomy "
            f"level {req.autonomy_level!r}",
            rule="unknown_autonomy_level",
        )

    if req.amount is None:
        return Decision(False, "no amount present on an execute request",
                        rule="malformed_request", limit=limit)

    # Fail closed on anything that is not money. The limit test below is
    # `amount > limit`, so *every* value that is merely "not greater" would
    # otherwise permit -- and NaN is not greater than anything, so without
    # this check `NaN`, `-Infinity`, `0` and `-500000` would all be approved,
    # under the rule name `settlement-agent.assisted.execute_limit`, making
    # the audit record claim the limit rule had considered them and said yes.
    #
    # These are outside the domain the limit reasons about, not inside a
    # 6,000 allowance, so they get their own rule name: a reader of an audit
    # trail must be able to tell "the limit permitted this" from "this was
    # never a payable amount". A zero-euro or negative-euro "payment" is the
    # same defect class as a non-finite one.
    #
    # `server.py`'s `parse_call` independently refuses to build a `PolicyInput`
    # from a non-finite number, so this is defence in depth. It lives here as
    # well because `rules.py` is the auditable artifact -- someone reading the
    # rule must see that these are refused -- and because the guarantee must
    # hold for a second caller (a REST facade, a harness) that never goes
    # through that parser.
    if not math.isfinite(req.amount) or req.amount <= 0:
        return Decision(
            False,
            f"requested amount {req.amount!r} is not a payable amount",
            rule="invalid_amount",
            limit=limit,
        )

    # `rule` composes only the autonomy segment, `settlement-agent.<level>.
    # execute_limit`, so it never mislabels the one settlement identity at
    # either level: `northstar.policy.rule` on the `northstar.policy.decision` span is what a
    # reader on stage reads off the trace. The reviewer-ui is not here at all:
    # it was answered above by `_human_approval`, under its own rule name.
    if req.amount > limit:
        return Decision(
            False,
            f"requested {_money(req.amount, req.currency)} exceeds the "
            f"{_money(limit, req.currency)} limit for this agent at autonomy "
            f"level {req.autonomy_level}",
            rule=f"settlement-agent.{req.autonomy_level}.execute_limit",
            limit=limit,
        )

    # Everything the limit asks has passed; the last question is the ledger's.
    # Placed last so every existing denial keeps its own rule name, and this
    # one fires only for a payment the limit alone would have released.
    if req.ledger_status is None:
        return Decision(
            False,
            "the settlement agent may only execute a payment the ledger shows "
            "proposed, and the ledger could not be read",
            rule=AGENT_LEDGER_UNREADABLE_RULE,
            limit=limit,
        )
    if req.ledger_status != PROPOSED:
        return Decision(
            False,
            "the settlement agent may only execute a payment it proposed and "
            f"that is still proposed; the ledger shows {req.ledger_status!r}",
            rule=AGENT_NOT_PROPOSED_RULE,
            limit=limit,
        )

    return Decision(True,
                    f"{_money(req.amount, req.currency)} is within the "
                    f"{_money(limit, req.currency)} limit",
                    rule=f"settlement-agent.{req.autonomy_level}.execute_limit",
                    limit=limit)


def _human_approval(req: PolicyInput) -> Decision:
    if req.amount is None or not math.isfinite(req.amount) or req.amount <= 0:
        return Decision(False, f"requested amount {req.amount!r} is not a payable amount",
                        rule="invalid_amount")
    if req.ledger_status != AWAITING_APPROVAL:
        return Decision(
            False,
            "a human approval may only release a payment awaiting approval; "
            f"the ledger shows {req.ledger_status!r}",
            rule=HUMAN_APPROVAL_RULE,
        )
    return Decision(True, "a human released a payment the ledger shows awaiting approval",
                    rule=HUMAN_APPROVAL_RULE)
