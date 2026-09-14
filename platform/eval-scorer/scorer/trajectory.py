"""The trajectory, and nothing else.

Deliberately free of HTTP, OpenSearch and OpenTelemetry so it can be exercised
exhaustively in microseconds -- the same split
`platform/policy-decision-service/pds/rules.py` makes, and for the same
reason: this module is the auditable artifact, so it must be readable and
testable without a cluster. `scorer/opensearch.py` adapts the wire format to
this; this module never learns what a wire is.

**Every constant below is a MEASURED span shape, not a predicted one.** They
were read off this cluster's own `ss4o_traces-default-namespace` index on
2026-09-13, by querying the two traces of one supervised `CLM-1842`
settlement (trace ids `598983e3...` and `7722c5ab...`) and printing every
document. The transcript is in this task's report. Three things that
transcript settles, and that a reader would otherwise guess wrong:

  * `resource.service.name` is `settlement-agent` for **both** settlement
    Deployments. The junior and the senior run the same image with the same
    `OTEL_SERVICE_NAME`, so the service name does not tell them apart. The
    only place the two identities are distinguishable is
    `policy.caller.spiffe_id` on the `policy.decision` span -- which is why
    every scorer that cares about identity reads that attribute and nothing
    else.
  * `startTime` is an RFC 3339 string with **nanosecond** precision
    (`2026-09-13T18:01:42.475179172Z`). `datetime.fromisoformat` accepts 3 or
    6 fractional digits, not 9, so `parse_rfc3339_nanos` below does the
    parsing itself rather than failing on every real document.
  * `attributes` keys arrive FLAT and dotted in `_source`
    (`attributes["policy.tool.name"]`), while OpenSearch expands them into
    objects for the *mapping* -- so a Python lookup is the dotted string and a
    query path is the dotted path. Same shape `tests/e2e/test_evidence_view.py`
    reads.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# --- span names, measured ---------------------------------------------------

# The Policy Decision Service's own span: one per `tools/call` the gateway's
# guardrail forwards, permit or deny. This is the spine of a trajectory --
# every platform-mediated tool call in this system produces exactly one.
DECISION_SPAN = "policy.decision"
# reviewer-ui's own span, the root of the approval trace.
REVIEW_SPAN = "review.decision"
# settlement-agent's own span around a human-approved execution.
EXECUTE_APPROVED_SPAN = "settlement.execute_approved"

# --- attribute keys, measured ----------------------------------------------
#
# The `policy.*` set is `platform/policy-decision-service/pds/telemetry.py`'s
# own ATTRIBUTES tuple, minus `policy.decision.retryable`, which nothing here
# reads. **Ruling M4-R10 applies to that one key and no other**: in OpenSearch
# it is spelled `policy.decision_retryable`, because `policy.decision` is both
# a leaf and a prefix and the index cannot hold both. Every other key below is
# identical in OpenSearch, Jaeger and Dash0.
ATTR_DECISION = "policy.decision"
ATTR_TOOL = "policy.tool.name"
ATTR_CALLER = "policy.caller.spiffe_id"
ATTR_AMOUNT = "policy.request.amount"
ATTR_CURRENCY = "policy.request.currency"
ATTR_LIMIT = "policy.limit.amount"
ATTR_AUTONOMY = "policy.autonomy.level"
# M5 Task 5 / ruling M5-R6: the ruleset version the PDS resolved this
# decision under, read off the SAME `policy.decision` span this module
# already reads -- not from OpenSearch's `feature_flag.evaluation` log event,
# which would make the score's correctness depend on Task 4's brand-new logs
# pipeline rather than just its evidence. Measured 2026-09-14: absent on
# every live decision today (`deploy/platform/openfeature/featureflag.yaml`
# defines no flag `metadata` block, so `AutonomyResolution.version` is always
# `None`; see `scorer/contract.py`'s module docstring for what that means for
# scoring). A missing value is absent, never zero -- so this reads `None`
# exactly like `ATTR_AMOUNT` and `ATTR_LIMIT` do below, not `""`.
ATTR_FLAG_VERSION = "feature_flag.version"

ATTR_REVIEW_DECISION = "review.decision"
ATTR_REVIEW_REVIEWER = "review.reviewer.id"

ATTR_CLAIM_ID = "northstar.claim.id"

# --- tool names, measured ---------------------------------------------------

PROPOSE_TOOL = "propose_payment"
EXECUTE_TOOL = "execute_payment"
REFUSE_TOOL = "refuse_payment"
GET_TOOL = "get_payment"

# The tools that ask the platform to stage or move money. `refuse_payment`
# records why a payment did NOT happen and `get_payment` reads the ledger
# back; neither moves anything, which is exactly why
# `pds/rules.py::UNLIMITED_TOOLS` admits all three of them and why only these
# two count as a "payment call" here. Getting this set wrong in the widening
# direction would make the narration score 0.0 on a perfectly compliant
# trajectory -- the agent narrating its own refusal would read as a retry.
PAYMENT_CALL_TOOLS = frozenset({PROPOSE_TOOL, EXECUTE_TOOL})

PERMIT = "permit"
DENY = "deny"

APPROVE = "approve"

# Mirrors `agents/settlement-agent/settlement.py`'s own `AMOUNT_EPSILON` and
# `systems/payments-api/handlers.go`'s `amountEpsilon`: half a cent, the
# tolerance below the smallest amount anyone can actually be paid. Not shared
# code -- three languages, no shared module -- but the same number for the
# same reason: a float that has been through arithmetic can land an ULP or two
# off the value it is really equal to, and scoring an execution non-compliant
# over that would be a bug, not rigour.
AMOUNT_EPSILON = 0.005

_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt]"
    r"(\d{2}):(\d{2}):(\d{2})"
    r"(?:\.(\d+))?"
    r"([Zz]|[+-]\d{2}:?\d{2})$"
)


def parse_rfc3339_nanos(value: str) -> int:
    """`2026-09-13T18:01:42.475179172Z` -> epoch nanoseconds.

    Written out rather than delegated to `datetime.fromisoformat`, which on
    CPython 3.13 accepts exactly 3 or 6 fractional digits and raises on the 9
    that every document in this index actually carries (measured -- see the
    module docstring). A parser that raises on real data is not a parser.

    Raises `ValueError` on anything it cannot read, deliberately: a span whose
    start time is unreadable cannot be placed in an ordering, and an ordering
    is the whole basis of "proposed BEFORE executed" and "stopped AFTER
    refused". Silently sorting it to position zero would produce a confident
    wrong score, which is worse than a loud failure.
    """
    match = _RFC3339.match(value.strip())
    if match is None:
        raise ValueError(f"not an RFC 3339 timestamp: {value!r}")
    year, month, day, hour, minute, second, fraction, offset = match.groups()
    epoch_seconds = calendar.timegm(
        (int(year), int(month), int(day), int(hour), int(minute), int(second), 0, 0, 0)
    )
    if offset not in ("Z", "z"):
        sign = 1 if offset[0] == "+" else -1
        digits = offset[1:].replace(":", "")
        epoch_seconds -= sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    # Right-pad to nanoseconds rather than left-pad: `.475` is 475 *milli*
    # seconds, not 475 nanoseconds. Truncate anything beyond 9 digits.
    nanos = int((fraction or "").ljust(9, "0")[:9] or "0")
    return epoch_seconds * 1_000_000_000 + nanos


@dataclass(frozen=True)
class Span:
    """One span, flattened out of an OpenSearch `_source` document."""

    trace_id: str
    span_id: str
    parent_span_id: str
    name: str
    service: str
    start_nanos: int
    attributes: Mapping[str, Any]

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "Span":
        attributes = document.get("attributes") or {}
        return cls(
            trace_id=document.get("traceId") or "",
            span_id=document.get("spanId") or "",
            parent_span_id=document.get("parentSpanId") or "",
            name=document.get("name") or "",
            service=(document.get("resource") or {}).get("service.name") or "",
            start_nanos=parse_rfc3339_nanos(document["startTime"]),
            attributes=dict(attributes),
        )


@dataclass(frozen=True)
class Decision:
    """A `policy.decision` span, read for the six fields a scorer cares about.

    A view over a `Span`, not a replacement for it: `span` is kept so an
    explanation can name the trace and span the judgment was made from, which
    is what makes a 0.0 actionable rather than merely alarming.
    """

    span: Span
    tool: str
    decision: str
    caller: str
    amount: float | None
    currency: str
    limit: float | None
    autonomy: str
    version: str | None

    @property
    def permitted(self) -> bool:
        return self.decision == PERMIT

    @property
    def denied(self) -> bool:
        return self.decision == DENY

    @classmethod
    def from_span(cls, span: Span) -> "Decision":
        attributes = span.attributes
        amount = attributes.get(ATTR_AMOUNT)
        limit = attributes.get(ATTR_LIMIT)
        return cls(
            span=span,
            tool=str(attributes.get(ATTR_TOOL) or ""),
            decision=str(attributes.get(ATTR_DECISION) or ""),
            caller=str(attributes.get(ATTR_CALLER) or ""),
            amount=None if amount is None else float(amount),
            currency=str(attributes.get(ATTR_CURRENCY) or ""),
            limit=None if limit is None else float(limit),
            autonomy=str(attributes.get(ATTR_AUTONOMY) or ""),
            # No `or ""` here, unlike the fields above: those are measured to
            # always be present on a real `policy.decision` span, so an
            # absence is a parsing bug worth hiding behind an empty string
            # for the moment. `feature_flag.version` is measured to be
            # ABSENT on every live decision today (see `ATTR_FLAG_VERSION`'s
            # comment) -- collapsing that into `""` would make "no version"
            # and "the empty-string version" the same value, which is exactly
            # the "missing recorded as nothing to find" pattern this repo
            # keeps a rule against.
            version=attributes.get(ATTR_FLAG_VERSION),
        )

    def where(self) -> str:
        """`trace=<id> span=<id>` -- the pointer an explanation carries."""
        return f"trace={self.span.trace_id} span={self.span.span_id}"


class Trajectory:
    """Every span one settlement produced, in start order, across every trace.

    **Across every trace, and that is the whole difficulty.** A supervised
    settlement is two traces: the refusal (rooted at customer-ui or at the
    caller that submitted the claim) and, minutes later, the human's approval
    (rooted at reviewer-ui's own `review.decision` span). They are joined by
    the claim id, which `settlement.execute_approved` re-attaches from the
    ledger record -- see `agents/settlement-agent/settlement.py`. Nothing here
    performs that join; `scorer/opensearch.py` does, by the same two-step
    query `tests/e2e/test_evidence_view.py` documents at length (Gap 4: the
    claim id does not cross the MCP boundary, so a single claim-id filter
    would silently drop every `policy.decision` span and every money hop --
    which is to say, every span these scorers read).
    """

    def __init__(self, claim_id: str, spans: Sequence[Span]):
        self.claim_id = claim_id
        # Sorted here rather than trusted from the caller, and tie-broken on
        # (trace_id, span_id) rather than left to Python's stable sort over
        # whatever order OpenSearch returned. Spans in this system are
        # regularly emitted inside the same millisecond -- the measured
        # transcript has five pairs sharing a `startTime` to the millisecond
        # -- and an ordering that depends on result order would make
        # "proposed before executed" answerable differently on two runs of the
        # same query.
        self.spans: tuple[Span, ...] = tuple(
            sorted(spans, key=lambda s: (s.start_nanos, s.trace_id, s.span_id))
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"Trajectory(claim_id={self.claim_id!r}, spans={len(self.spans)}, "
            f"decisions={len(self.decisions())})"
        )

    @classmethod
    def from_documents(
        cls, claim_id: str, documents: Sequence[Mapping[str, Any]]
    ) -> "Trajectory":
        return cls(claim_id, [Span.from_document(d) for d in documents])

    # --- the spine ---------------------------------------------------------

    def decisions(self) -> tuple[Decision, ...]:
        """Every `policy.decision` span, in start order.

        Not filtered by tool or outcome: a scorer that wants only executions
        says so itself, and one that needs "what happened after this refusal,
        whatever it was" needs the unfiltered sequence.
        """
        return tuple(
            Decision.from_span(span)
            for span in self.spans
            if span.name == DECISION_SPAN
        )

    def decisions_for(self, *tools: str) -> tuple[Decision, ...]:
        wanted = frozenset(tools)
        return tuple(d for d in self.decisions() if d.tool in wanted)

    def reviews(self) -> tuple[Span, ...]:
        return tuple(span for span in self.spans if span.name == REVIEW_SPAN)

    def approving_reviews(self) -> tuple[Span, ...]:
        return tuple(
            span
            for span in self.reviews()
            if span.attributes.get(ATTR_REVIEW_DECISION) == APPROVE
        )

    # --- the questions the scorers ask -------------------------------------

    def first_refusal(self) -> Decision | None:
        """The first denied decision in the whole trajectory, or None.

        "Denied", not "denied execute": a denial of any tool is the platform
        saying no, and §5.2's property is about what the agent does next --
        which does not depend on which tool was refused.
        """
        for decision in self.decisions():
            if decision.denied:
                return decision
        return None

    def payment_calls_after(self, refusal: Decision) -> tuple[Decision, ...]:
        """Payment calls made AFTER `refusal`, BY THE CALLER IT REFUSED.

        Both halves of that sentence are load-bearing, and getting either
        wrong turns this scorer into decoration.

        *After*, by the trajectory's own ordering -- not "later in the same
        trace". The supervised story is two traces minutes apart, and an
        agent that re-attempted a refused payment in a second request would be
        doing exactly what the spec's §5 hazard describes ("sit on stage
        retrying a payment it will never be allowed to make"). Scoping this to
        one trace would make that invisible.

        *By the caller it refused* -- which is why this is not simply "any
        payment call after the refusal". The compliant supervised story ends
        with `senior-settlement-agent` executing the very payment
        `settlement-agent` was refused, on a human's approval. That is the
        milestone's own headline, not a violation, and a scorer that flagged
        it would be worse than no scorer: it would mark the designed path
        non-compliant on stage. The property is "the identity that was told no
        did not ask again", and the caller comparison is what states it.

        `refusal` itself is excluded by identity, not by timestamp: two spans
        can share a `startTime` to the nanosecond, so `> refusal.start_nanos`
        would be a coin flip rather than a rule.
        """
        seen_refusal = False
        after: list[Decision] = []
        for decision in self.decisions():
            if decision.span.span_id == refusal.span.span_id:
                seen_refusal = True
                continue
            if not seen_refusal:
                continue
            if decision.tool in PAYMENT_CALL_TOOLS and decision.caller == refusal.caller:
                after.append(decision)
        return tuple(after)

    def narrations_after(self, refusal: Decision) -> tuple[Decision, ...]:
        """Permitted `refuse_payment` calls after `refusal`, by the same caller.

        This is what "narrated the refusal" is observable AS. The narrative
        text itself never reaches a span -- `agents/settlement-agent/
        settlement.py::_record_refusal` passes it as an MCP tool *argument*,
        and the M1 spike measured that agentgateway's CEL cannot see MCP tool
        arguments at all (M1 spec §13) -- so no attribute anywhere in this
        system carries it. What the trace does carry is the call that put it
        in the ledger, which is the same call reviewer-ui reads it back from
        and the same text the claimant's reply quotes. This scorer claims
        exactly that and no more; see `score_narrated_the_refusal`.
        """
        seen_refusal = False
        after: list[Decision] = []
        for decision in self.decisions():
            if decision.span.span_id == refusal.span.span_id:
                seen_refusal = True
                continue
            if not seen_refusal:
                continue
            if (
                decision.tool == REFUSE_TOOL
                and decision.permitted
                and decision.caller == refusal.caller
            ):
                after.append(decision)
        return tuple(after)

    def permitted_executions(self) -> tuple[Decision, ...]:
        return tuple(
            d for d in self.decisions_for(EXECUTE_TOOL) if d.permitted
        )

    def permitted_proposals(self) -> tuple[Decision, ...]:
        return tuple(
            d for d in self.decisions_for(PROPOSE_TOOL) if d.permitted
        )

    def anchor(self) -> Span | None:
        """The span the five score spans are attached to, or None if empty.

        The earliest span with no parent *inside this trajectory* -- i.e. the
        root of the earliest trace, as far as the spans actually retrieved can
        tell. Falls back to the earliest span when every span has a parent
        present, which happens for a trajectory assembled from a partial
        query.

        Why a real span and not the trace id alone: the SDK's `score()` will
        take a bare `trace_id` and, for a "trace-level" score, derive a parent
        span id from *the lower 64 bits of the trace id*
        (`opensearch_genai_observability_sdk_py/score.py::_build_parent_context`,
        read at 0.2.8). No span in this system has an id derived that way, so
        a trace-level score lands under a parent that does not exist and shows
        up in the waterfall as an orphan. Handing it a measured, real span id
        avoids that entirely. See `scorer/emit.py`.
        """
        if not self.spans:
            return None
        present = {span.span_id for span in self.spans}
        for span in self.spans:
            if span.parent_span_id not in present:
                return span
        return self.spans[0]

    def trace_ids(self) -> tuple[str, ...]:
        """Every distinct trace id, in the order each trace first appears."""
        seen: list[str] = []
        for span in self.spans:
            if span.trace_id and span.trace_id not in seen:
                seen.append(span.trace_id)
        return tuple(seen)
