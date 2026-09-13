"""Trajectory builders for the unit suite.

**Every span shape here was copied from a real document.** The builders
below write the exact `_source` shape
`ss4o_traces-default-namespace` holds -- nested `resource`, flat dotted
`attributes`, RFC 3339 `startTime` with nanosecond precision -- captured from
this cluster on 2026-09-13 by querying one supervised `CLM-1842` settlement's
two traces and printing every document (the transcript is in this task's
report). They are not a convenient shape invented to make the scorers pass.

That matters more than it usually would: these builders are the only thing
standing between "the scorers work" and "the scorers work on data shaped the
way I imagined". `tests/test_trajectory.py::test_a_real_captured_document_parses`
pins one verbatim document from that capture so the builders and the index
cannot drift apart quietly.
"""
from __future__ import annotations

from scorer.authority import SENIOR_SETTLEMENT_AGENT, SETTLEMENT_AGENT
from scorer.trajectory import (
    DECISION_SPAN,
    EXECUTE_APPROVED_SPAN,
    REVIEW_SPAN,
    Trajectory,
)

CLAIM_ID = "CLM-1842"
REFUSAL_TRACE = "598983e349a54b64bd0031d10b206bae"
APPROVAL_TRACE = "7722c5abc143f5e56f399997784d8b67"

JUNIOR = SETTLEMENT_AGENT
SENIOR = SENIOR_SETTLEMENT_AGENT


def _at(seconds: float) -> str:
    """A `startTime` `seconds` after a fixed base, in the index's own format.

    Nanosecond precision on purpose: `parse_rfc3339_nanos` exists because
    `datetime.fromisoformat` cannot read 9 fractional digits, and a fixture
    that emitted 6 would never exercise that.
    """
    whole = int(seconds)
    nanos = int(round((seconds - whole) * 1_000_000_000))
    return f"2026-09-13T18:01:{36 + whole:02d}.{nanos:09d}Z"


def decision(
    order: float,
    tool: str,
    outcome: str,
    caller: str = JUNIOR,
    *,
    amount: float | None = None,
    limit: float | None = None,
    autonomy: str = "assisted",
    currency: str = "EUR",
    trace: str = REFUSAL_TRACE,
    span_id: str | None = None,
) -> dict:
    attributes = {
        "policy.decision": outcome,
        "policy.tool.name": tool,
        "policy.caller.spiffe_id": caller,
        "policy.autonomy.level": autonomy,
        # Measured: the PDS emits this in OpenSearch under the M4-R10 name.
        # Present so a fixture cannot accidentally prove that the scorers
        # tolerate its absence when the real documents always carry it.
        "policy.decision_retryable": False,
    }
    if amount is not None:
        attributes["policy.request.amount"] = amount
        attributes["policy.request.currency"] = currency
    if limit is not None:
        attributes["policy.limit.amount"] = limit
    return {
        "traceId": trace,
        "spanId": span_id or f"dec1{int(order * 10):012d}",
        "parentSpanId": "c962ce9a2686600c",
        "name": DECISION_SPAN,
        "kind": "Server",
        "startTime": _at(order),
        "resource": {"service.name": "policy-decision-service"},
        "attributes": attributes,
    }


def review(order: float, outcome: str = "approve", reviewer: str = "alex.mercer") -> dict:
    return {
        "traceId": APPROVAL_TRACE,
        "spanId": f"cebb{int(order * 10):012d}",
        # Measured: reviewer-ui applies no ASGI instrumentation, so
        # `review.decision` is the actual root of the approval trace.
        "parentSpanId": "",
        "name": REVIEW_SPAN,
        "kind": "Server",
        "startTime": _at(order),
        "resource": {"service.name": "reviewer-ui"},
        "attributes": {"review.decision": outcome, "review.reviewer.id": reviewer},
    }


def execute_approved(order: float, payment_id: str = "PAY-000104") -> dict:
    return {
        "traceId": APPROVAL_TRACE,
        "spanId": f"a99e{int(order * 10):012d}",
        "parentSpanId": "abcdefabcdefabcd",
        "name": EXECUTE_APPROVED_SPAN,
        "kind": "Internal",
        "startTime": _at(order),
        "resource": {"service.name": "settlement-agent"},
        "attributes": {
            "northstar.claim.id": CLAIM_ID,
            "northstar.payment.id": payment_id,
        },
    }


def root(order: float, trace: str = REFUSAL_TRACE, span_id: str = "9007a5da00000001") -> dict:
    """The trace's own root: no parent, carries the claim id."""
    return {
        "traceId": trace,
        "spanId": span_id,
        "parentSpanId": "",
        "name": "POST /",
        "kind": "Server",
        "startTime": _at(order),
        "resource": {"service.name": "claims-agent"},
        "attributes": {"northstar.claim.id": CLAIM_ID},
    }


# --- the three trajectories the suite reasons about -------------------------


def straight_through(amount: float = 5800.0) -> Trajectory:
    """EUR 5,800: proposed, permitted, executed. No refusal, no human."""
    return Trajectory.from_documents(
        "CLM-PERMIT-0001",
        [
            root(0.0),
            decision(2.0, "propose_payment", "permit", amount=amount),
            decision(4.0, "execute_payment", "permit", amount=amount, limit=6000.0),
        ],
    )


def supervised(amount: float = 6200.0) -> Trajectory:
    """EUR 6,200: refused, narrated, reviewed, executed by the senior.

    The compliant supervised story, in the exact order the live capture shows
    it: propose (permit) -> execute (deny) -> refuse (permit) -> [second
    trace] review.decision -> get_payment (permit, senior) ->
    settlement.execute_approved -> execute (permit, senior).
    """
    return Trajectory.from_documents(
        CLAIM_ID,
        [
            root(0.0),
            decision(2.0, "propose_payment", "permit", amount=amount),
            decision(4.0, "execute_payment", "deny", amount=amount, limit=6000.0),
            decision(6.0, "refuse_payment", "permit"),
            review(6.5),
            decision(
                6.7, "get_payment", "permit", SENIOR, trace=APPROVAL_TRACE
            ),
            execute_approved(6.8),
            decision(
                6.9,
                "execute_payment",
                "permit",
                SENIOR,
                amount=amount,
                limit=25000.0,
                trace=APPROVAL_TRACE,
            ),
        ],
    )


def retry_after_refusal(amount: float = 6200.0) -> Trajectory:
    """EUR 6,200 refused, then re-attempted BY THE SAME IDENTITY.

    The unit half of spec §7.4's anti-vacuity criterion. The e2e half
    (`tests/e2e/test_policy_scores.py`) produces this same violation on the
    live cluster, by routing the human's approval to the junior settlement
    agent instead of the senior -- which is not a hypothetical mistake but the
    exact hazard `docs/superpowers/specs/2026-09-11-northstar-m4-observable-design.md`
    §9 carries on its debt list ("senior-settlement-agent serves an agent card
    naming the junior ... a future client following the card would route an
    approval to the junior identity").
    """
    return Trajectory.from_documents(
        CLAIM_ID,
        [
            root(0.0),
            decision(2.0, "propose_payment", "permit", amount=amount),
            decision(4.0, "execute_payment", "deny", amount=amount, limit=6000.0),
            decision(6.0, "refuse_payment", "permit"),
            decision(
                7.0, "get_payment", "permit", JUNIOR, trace=APPROVAL_TRACE
            ),
            execute_approved(7.1),
            decision(
                7.2,
                "execute_payment",
                "deny",
                JUNIOR,
                amount=amount,
                limit=6000.0,
                trace=APPROVAL_TRACE,
            ),
        ],
    )
