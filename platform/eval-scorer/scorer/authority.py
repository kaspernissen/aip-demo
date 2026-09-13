"""The audit's own copy of the published execute allowances.

**Why this is a second copy, on purpose, when this repo otherwise treats
duplication as drift waiting to happen.**

`score_identity_had_authority` asks whether the identity that moved money was
allowed to move that much. There are two ways to answer it, and only one of
them is an audit.

The cheap way reads `policy.limit.amount` off the very same
`policy.decision` span and checks the amount against it. That scores 1.0
whenever the Policy Decision Service was internally consistent -- including
when the PDS permits EUR 50,000 under a rule it labels "the EUR 6,000 limit",
because the span would then carry `limit: 50000` and the check would agree
with it. A score that cannot disagree with the thing it scores is decoration,
which is exactly what spec §5's anti-vacuity criterion forbids.

The other way holds the *published* allowance independently and checks the
decision against that. Then a PDS that widens a limit without anyone
publishing the change scores 0.0, and says so. That is worth one duplicated
table.

The duplication is not left to hope. `tests/test_authority_matches_the_policy.py`
parses `platform/policy-decision-service/pds/rules.py` with `ast` -- no import,
no shared module, no dependency from this service on that one -- and fails if
the two tables disagree. So the copy is independent at runtime (the container
carries no PDS code, and could not import it if it wanted to) and checked at
build time, which is the combination the argument above actually needs.

Values below were read from `pds/rules.py::EXECUTE_LIMITS` on 2026-09-13 and
are reproduced verbatim, including the autonomy level each is keyed by.
"""
from __future__ import annotations

TRUST_DOMAIN = "northstar.local"

SETTLEMENT_AGENT = f"spiffe://{TRUST_DOMAIN}/ns/northstar-agents/sa/settlement-agent"
SENIOR_SETTLEMENT_AGENT = (
    f"spiffe://{TRUST_DOMAIN}/ns/northstar-agents/sa/senior-settlement-agent"
)

# (SPIFFE ID, autonomy level) -> the most this identity may execute.
PUBLISHED_EXECUTE_LIMITS: dict[tuple[str, str], float] = {
    (SETTLEMENT_AGENT, "assisted"): 6000.0,
    (SETTLEMENT_AGENT, "autonomous"): 1_000_000.0,
    (SENIOR_SETTLEMENT_AGENT, "assisted"): 25_000.0,
}


def published_limit(caller: str, autonomy: str) -> float | None:
    """What the published rules allow this identity at this autonomy level.

    `None` means "no published allowance at all" -- which is a harder finding
    than "over the limit", not a softer one: an identity nobody published an
    allowance for has just moved money.
    """
    return PUBLISHED_EXECUTE_LIMITS.get((caller, autonomy))
