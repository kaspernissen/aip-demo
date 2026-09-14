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
# M3 Task 3: `refuse_payment` joins `propose_payment` here. It is
# settlement-agent recording, through the same governed MCP path everything
# else in this repo uses, the reason a payment already did not execute -- it
# moves no money and reverses nothing, so it is unlimited for the same reason
# proposing is.
#
# M3 Task 3 fix round (R11): `get_payment` joins them too. `execute_approved`
# (agents/settlement-agent/settlement.py) reads the ledger back before
# executing an approval, and a read that moves nothing and reverses nothing is
# exactly this set's own admission rule.
UNLIMITED_TOOLS = frozenset({"propose_payment", "refuse_payment", "get_payment"})
LIMITED_TOOLS = frozenset({"execute_payment"})

# M3 Task 3 fix round (R12b, finding 2): `refuse_payment` was unconditional for
# ANY caller, including `caller=None` -- so any holder of a valid SVID (or, in
# principle, none at all) could mark an arbitrary `proposed` payment `refused`
# with attacker-chosen `rule`/`limit`/`requested`/`narrative`, inject it into
# the reviewer's queue, and overwrite an existing refusal's
# `refusedTraceId`/`refusedSpanId` -- authorship of the exact surface a human
# reads before releasing money.
#
# The reviewer put a caller restriction on the milestone's debt list because
# it "needs a branch in decide()", which Task 3 was rightly forbidden from
# adding. That reading is overruled (ruling R12): the no-new-logic constraint
# exists to stop the policy engine acquiring an input that WIDENS what is
# permitted -- an override flag, an approval token. A caller restriction on an
# already-unlimited tool NARROWS what is permitted; it is data, exactly like
# EXECUTE_LIMITS above, not a widening input. So this is one more table,
# consulted by decide() as one more lookup -- never a tool-name branch.
#
# Only tools actually named here are restricted; a tool in UNLIMITED_TOOLS but
# absent from this mapping (propose_payment, get_payment) stays unconditional,
# because nothing about proposing or reading widens what a human reviews.
CALLER_SCOPED_TOOLS: dict[str, frozenset[str]] = {
    "refuse_payment": frozenset({
        "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent",
        "spiffe://northstar.local/ns/northstar-agents/sa/senior-settlement-agent",
    }),
}


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

    # **`rule` used to be a literal that mislabelled one of the three
    # principals that can reach here. Read this before changing it again** --
    # `policy.rule` on the `policy.decision` span is what a reader on stage
    # reads off the trace.
    #
    # M5 composes only the autonomy segment: `f"settlement-agent.
    # {req.autonomy_level}.execute_limit"`. The lookup above is keyed by
    # `(caller, autonomy_level)` and EXECUTE_LIMITS holds three rows, and this
    # composition was chosen to touch exactly one of them:
    #
    #   * `(settlement-agent, assisted)` -> composes back to the same string
    #     the old literal was, byte for byte: unchanged.
    #   * `(senior-settlement-agent, assisted)` -> ALSO composes to
    #     `settlement-agent.assisted.execute_limit`, unchanged, because the
    #     composition is keyed on `autonomy_level` only, never on `caller`.
    #     That the name still names the junior is **deliberate and
    #     load-bearing**, not an oversight this task left alone: M3's whole
    #     claim is that approval changes *who is asking*, not what is
    #     allowed, and the shared rule name across two different callers is
    #     what makes that visible in one span. `tests/e2e/test_supervised.py`
    #     criterion 4 asserts this exact string on the permitting decision.
    #   * `(settlement-agent, autonomous)` -> now composes to
    #     `settlement-agent.autonomous.execute_limit`, which is the first
    #     honest name this row has ever had. It was a plain mislabel before
    #     M5 (the row landed with this file itself, `fb3e294`, M2.2, and
    #     nothing in this repo could drive an `autonomous` request until M5's
    #     `pds/autonomy.py` made the level a per-decision resolution instead
    #     of `server.py`'s old `AUTONOMY_LEVEL = "assisted"` constant, so no
    #     emitted span had ever actually carried the wrong name against this
    #     row) -- M5 is the milestone that makes `autonomous` reachable, and
    #     therefore the milestone in which composing the segment, rather than
    #     leaving it a literal, stops being able to break anything: the only
    #     caller-segment string this file ever emits is `settlement-agent`,
    #     for both settlement identities, so composing just the autonomy
    #     segment cannot touch the senior/junior naming criterion 4 depends
    #     on.
    if req.amount > limit:
        return Decision(
            False,
            f"requested {_money(req.amount, req.currency)} exceeds the "
            f"{_money(limit, req.currency)} limit for this agent at autonomy "
            f"level {req.autonomy_level}",
            rule=f"settlement-agent.{req.autonomy_level}.execute_limit",
            limit=limit,
        )

    return Decision(True,
                    f"{_money(req.amount, req.currency)} is within the "
                    f"{_money(limit, req.currency)} limit",
                    rule=f"settlement-agent.{req.autonomy_level}.execute_limit",
                    limit=limit)
