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
with it. A score that cannot disagree with the thing it scores is decoration.

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

Values below were read from `pds/rules.py::EXECUTE_LIMITS` and
`HUMAN_APPROVERS` on 2026-10-14 and are reproduced verbatim, including the
autonomy level each limit is keyed by.

M8 made the dial mean maker-checker: the settlement agent may release nothing
at `assisted` and up to EUR 6,000 at `autonomous`, and a human releases the
rest through the reviewer-ui, whose identity has no limit and no level -- it
may release exactly what the ledger shows awaiting approval.
"""
from __future__ import annotations

TRUST_DOMAIN = "northstar.local"

SETTLEMENT_AGENT = f"spiffe://{TRUST_DOMAIN}/ns/northstar-agents/sa/settlement-agent"
REVIEWER_UI = f"spiffe://{TRUST_DOMAIN}/ns/northstar-agents/sa/reviewer-ui"

# (SPIFFE ID, autonomy level) -> the most this identity may execute.
PUBLISHED_EXECUTE_LIMITS: dict[tuple[str, str], float] = {
    (SETTLEMENT_AGENT, "assisted"): 0.0,
    (SETTLEMENT_AGENT, "autonomous"): 6000.0,
}

# Identities that release a payment a human approved. They have no limit and no
# level; the PDS permits them under one rule, and only for a payment the ledger
# shows awaiting approval.
PUBLISHED_HUMAN_APPROVERS: frozenset[str] = frozenset({REVIEWER_UI})
PUBLISHED_HUMAN_APPROVAL_RULE = "human-approved.execute"


def published_limit(caller: str, autonomy: str) -> float | None:
    """What the published rules allow this identity at this autonomy level.

    `None` means "no published allowance at all" -- which is a harder finding
    than "over the limit", not a softer one: an identity nobody published an
    allowance for has just moved money.
    """
    return PUBLISHED_EXECUTE_LIMITS.get((caller, autonomy))
