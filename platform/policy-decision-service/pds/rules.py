"""The policy, and nothing else.

Deliberately free of gRPC, OpenTelemetry and I/O so it can be exercised
exhaustively in microseconds. `server.py` adapts the wire format to this;
this module never learns what a wire is.
"""

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
}

# Tools with no world-changing effect. Proposing is reversible; executing moves
# money. Only the latter is limited.
UNLIMITED_TOOLS = frozenset({"propose_payment"})
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

    if req.amount > limit:
        return Decision(
            False,
            f"requested {req.amount:.0f} {req.currency} exceeds the "
            f"{limit:.0f} {req.currency} limit for this agent at autonomy "
            f"level {req.autonomy_level}",
            rule="settlement-agent.assisted.execute_limit",
            limit=limit,
        )

    return Decision(True, f"{req.amount:.0f} {req.currency} is within the "
                    f"{limit:.0f} {req.currency} limit",
                    rule="settlement-agent.assisted.execute_limit", limit=limit)
