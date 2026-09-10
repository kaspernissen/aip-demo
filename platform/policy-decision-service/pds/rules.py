"""The policy, and nothing else.

Deliberately free of gRPC, OpenTelemetry and I/O so it can be exercised
exhaustively in microseconds. `server.py` adapts the wire format to this;
this module never learns what a wire is.
"""

import math
from dataclasses import dataclass

# The rule shape the spec fixes, and the one M5 mutates:
#   "settlement-agent, at autonomy level `assisted`, may execute up to €6,000."
# Keyed by (SPIFFE ID, autonomy level) so the identity is structurally required
# rather than merely consulted.
EXECUTE_LIMITS: dict[tuple[str, str], float] = {
    ("spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent",
     "assisted"): 6000.0,
    ("spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent",
     "autonomous"): 1_000_000.0,
    # M3: the colleague with higher approval authority the agent already
    # narrates. Higher limit, same rule, same code path. Approval changes who
    # asks; it does not change what is allowed. `decide()` below is
    # untouched -- this row is the entire M3 change to this file's policy.
    ("spiffe://northstar.local/ns/northstar-agents/sa/senior-settlement-agent",
     "assisted"): 25_000.0,
}

# Tools with no world-changing effect. Proposing is reversible; executing moves
# money. Only the latter is limited.
#
# M3: `refuse_payment` joins `propose_payment` here. It is settlement-agent
# recording, through the same governed MCP path everything else in this repo
# uses, the reason a payment already did not execute -- it moves no money and
# reverses nothing, so it is unlimited for the same reason proposing is.
UNLIMITED_TOOLS = frozenset({"propose_payment", "refuse_payment"})
LIMITED_TOOLS = frozenset({"execute_payment"})


@dataclass(frozen=True)
class PolicyInput:
    caller: str | None
    tool: str
    amount: float | None
    currency: str
    autonomy_level: str


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
        return Decision(True, f"{req.tool} has no effect to authorise",
                        rule="unlimited_tool")

    if req.tool not in LIMITED_TOOLS:
        return Decision(False, f"unknown tool {req.tool!r}", rule="unknown_tool")

    limit = EXECUTE_LIMITS.get((req.caller or "", req.autonomy_level))
    if limit is None:
        return Decision(
            False,
            f"no execute allowance for caller {req.caller!r} at autonomy "
            f"level {req.autonomy_level!r}",
            rule="unknown_caller",
        )

    if req.amount is None:
        return Decision(False, "no amount present on an execute request",
                        rule="malformed_request", limit=limit)

    # Fail closed on anything that is not money. The limit test below is
    # `amount > limit`, so *every* value that is merely "not greater" would
    # otherwise permit -- and NaN is not greater than anything, so `NaN`,
    # `-Infinity`, `0` and `-500000` all used to be approved, and approved
    # under the rule name `settlement-agent.assisted.execute_limit`, which made
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
    # hold for a second caller (M5's autonomy work, a REST facade, a harness)
    # that never goes through that parser.
    if not math.isfinite(req.amount) or req.amount <= 0:
        return Decision(
            False,
            f"requested amount {req.amount!r} is not a payable amount",
            rule="invalid_amount",
            limit=limit,
        )

    if req.amount > limit:
        return Decision(
            False,
            f"requested {_money(req.amount, req.currency)} exceeds the "
            f"{_money(limit, req.currency)} limit for this agent at autonomy "
            f"level {req.autonomy_level}",
            rule="settlement-agent.assisted.execute_limit",
            limit=limit,
        )

    return Decision(True,
                    f"{_money(req.amount, req.currency)} is within the "
                    f"{_money(limit, req.currency)} limit",
                    rule="settlement-agent.assisted.execute_limit", limit=limit)
