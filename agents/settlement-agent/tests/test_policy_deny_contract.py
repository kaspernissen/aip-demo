"""The deny-marker contract, asserted against **both** real implementations.

Fix round 1, finding F7. Before this file the marker was hand-duplicated: the
PDS wrote `"decision": "deny"` in `pds/server.py::build_result`, and
`tests/test_mcp_tools.py` carried a copied literal of the same body. Producer
and consumer of a contract with nothing asserting they agree is the classic
silent-drift shape, and it was measured: changing the PDS to emit `"denied"`
reddened two tests in the PDS suite and **zero** here, even though this
service is the thing that stops narrating a refusal when the marker changes.

So this file does not copy anything. It drives the real chain, end to end,
from the bytes the gateway measurably sends the PDS to the exception
`mcp_tools._policy_denial` measurably receives:

    the measured `mcp_request` bytes
      -> pds.server.parse_call          (the PDS's real wire parser)
      -> pds.rules.decide               (the real policy)
      -> pds.server.build_result        (the real deny body)
      -> the gateway's measured mapping (`_as_it_arrives_at_the_agent`)
      -> mcp.shared.exceptions.McpError (the real exception type)
      -> mcp_tools._policy_denial       (the real detector)

Only one hop in that chain is a stand-in, and it is the one hop that is not
Python: agentgateway. Its mapping is not invented here either -- Task 1
measured it byte-for-byte (`AuthorizationError.reason` -> `error.message`,
`mcp_error` parsed whole -> `error.data`, `error.code` -> the gateway's own
`-32001`, never the `-32003` inside the body) and `_as_it_arrives_at_the_agent`
is that mapping and nothing else.

**The import of `pds` is test-only.** Nothing in `agents/settlement-agent`'s
production code imports the PDS, and nothing may: the agent reaches the policy
through the gateway, never directly, and a Python-level dependency would
quietly contradict the architecture this milestone exists to demonstrate. The
`sys.path` insertion below happens in a test module, so it cannot reach
`mcp_tools.py`, `settlement.py` or the image.

Concrete regression check (measured -- see the task report's fix round):
change `"decision": "deny"` to `"denied"` in `pds/server.py::build_result` and
`test_the_pds_deny_body_is_recognised_by_the_agents_own_detector` fails here,
outside the PDS suite, which is the whole point of the file.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from mcp_tools import PolicyDenial, _deny_marker, _policy_denial

# --- the test-only path to the producer -------------------------------------

_PDS_DIR = (
    pathlib.Path(__file__).resolve().parents[3]
    / "platform"
    / "policy-decision-service"
)

_HOW = (
    "\n\n"
    "This is the producer/consumer contract test for the policy deny marker\n"
    "(M2.2 Task 5, fix round finding F7). It imports the PDS's own deny-body\n"
    "builder so that a change to the marker on the PDS side cannot leave this\n"
    "service's suite green.\n"
    "\n"
    "The generated ExtMCP gRPC stubs it needs are git-ignored on purpose\n"
    "(ruling R22). Generate them, then re-run:\n"
    "\n"
    "    cd platform/policy-decision-service && ./gen-stubs.sh\n"
    "\n"
    "Deliberately not an importorskip: a skipped contract test is a contract\n"
    "nobody is checking, and this project has already been burned by a\n"
    "silently-green suite full of skips.\n"
)

if str(_PDS_DIR) not in sys.path:
    sys.path.insert(0, str(_PDS_DIR))

try:  # pragma: no cover - the failure path is exercised by deleting the stubs
    from pds.rules import PolicyInput, decide
    from pds.server import AUTONOMY_LEVEL, build_result, parse_call
except ImportError as exc:
    raise RuntimeError(str(exc) + _HOW) from exc


AGENT = "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"

# The `mcp_request` payload shape Task 1 measured on the wire, with the float
# amount the gateway actually sends (`6200.0`, not `6200`). Built here the way
# the PDS's own servicer tests build it, so this file exercises the parser
# rather than assuming what it produces.
MEASURED_EXECUTE = json.dumps(
    {
        "name": "execute_payment",
        "arguments": {"payment_id": "PAY-000001", "amount": 6200.0, "currency": "EUR"},
    }
).encode()

MEASURED_PROPOSE = json.dumps(
    {
        "name": "propose_payment",
        "arguments": {"claim_id": "CLM-1842", "amount": 6200.0, "currency": "EUR"},
    }
).encode()


def _pds_decision(params: bytes):
    """The PDS's real answer to real wire bytes, exactly as its servicer asks.

    Mirrors `PolicyServicer.CheckRequest`'s own call sequence
    (`parse_call` -> `decide` -> `build_result`, with `requested=call.amount`),
    minus the gRPC and span plumbing, which is the PDS suite's business and not
    this contract's.
    """
    call = parse_call(params)
    decision = decide(
        PolicyInput(
            caller=AGENT,
            tool=call.tool or "",
            amount=call.amount,
            currency=call.currency,
            autonomy_level=AUTONOMY_LEVEL,
        )
    )
    result = build_result(
        permit=decision.permit,
        reason=decision.reason,
        rule=decision.rule,
        limit=decision.limit,
        requested=call.amount,
    )
    return decision, result


def _as_it_arrives_at_the_agent(result) -> McpError:
    """agentgateway's mapping, measured byte-for-byte in Task 1 finding 1.

    The one hop in this chain that is not Python. `reason` becomes
    `error.message`; `mcp_error` is parsed whole and becomes `error.data`; the
    code is the gateway's `-32001` (`PERMISSION_DENIED`) and never the
    `-32003` the PDS put inside the body.
    """
    return McpError(
        ErrorData(
            code=-32001,
            message=result.error.reason,
            data=json.loads(result.error.mcp_error),
        )
    )


# --- the contract -----------------------------------------------------------


def test_the_pds_deny_body_is_recognised_by_the_agents_own_detector():
    """The finding, closed. Nothing here is copied from the PDS.

    Change the marker key or its value in `pds/server.py::build_result` and
    this fails -- in `agents/settlement-agent`, not only in the PDS suite.
    """
    decision, result = _pds_decision(MEASURED_EXECUTE)
    assert decision.permit is False, "the fixture stopped being a denial at all"

    denial = _policy_denial(_as_it_arrives_at_the_agent(result))

    assert isinstance(denial, PolicyDenial), (
        "the PDS's real deny body was not recognised as a denial by "
        "mcp_tools._policy_denial -- producer and consumer have drifted"
    )
    # Every structured fact, taken from the PDS's own Decision rather than
    # from a literal typed here.
    assert denial.rule == decision.rule
    assert denial.limit == decision.limit
    assert denial.retryable is decision.retryable
    assert denial.requested == 6200.0
    # And the narration text is the PDS's reason, unaltered.
    assert decision.reason in str(denial)


def test_the_marker_the_two_sides_agree_on_is_decision_deny():
    """The contract stated as one assertion, in both parties' terms.

    `_deny_marker` is the consumer's whole notion of what a denial is; this
    pins that the producer's body satisfies exactly that and by that key.
    """
    _, result = _pds_decision(MEASURED_EXECUTE)
    body = json.loads(result.error.mcp_error)

    marker = _deny_marker(_as_it_arrives_at_the_agent(result))
    assert marker is not None
    assert marker["decision"] == "deny"
    # ...and it is the nested member, which is where the gateway's whole-payload
    # mapping puts it. A producer that moved it up a level would still be
    # recognised (the detector checks both), but this records where it is today.
    assert body["data"] is marker or body["data"] == marker


def test_the_pds_permit_result_gives_the_detector_nothing_to_find():
    """The other direction, from the real producer.

    A permit is `Pass` and carries no `error` at all, so there is no body for
    the detector to mistake for a refusal. Asserted against the real result
    rather than reasoned about, because "permit" and "deny" travelling in the
    same field would be the worst version of this contract.
    """
    decision, result = _pds_decision(MEASURED_PROPOSE)
    assert decision.permit is True
    assert result.HasField("error") is False, (
        "a permit grew an AuthorizationError -- the deny and permit paths are "
        "no longer distinguishable by shape"
    )
    assert result.WhichOneof("result") == "pass"


def test_the_hand_written_denial_fixtures_still_match_what_the_pds_emits():
    """`tests/test_mcp_tools.py`'s fixtures are literals, and stay honest here.

    Those literals exist so the unit tests can run without importing the PDS
    at all (they cover parsing, not the contract). This test is what stops
    them drifting: the reason string, every key, and every value are compared
    against what the real producer emits for the same request.
    """
    from test_mcp_tools import DENY_REASON, _denial_body

    _, result = _pds_decision(MEASURED_EXECUTE)
    real = json.loads(result.error.mcp_error)
    copied = _denial_body()

    assert result.error.reason == DENY_REASON, (
        "test_mcp_tools.DENY_REASON no longer matches the PDS's own refusal "
        "text; the model narrates that text, so update the fixture"
    )
    assert copied == real, (
        "test_mcp_tools._denial_body() has drifted from the body the PDS "
        f"actually emits:\n  fixture: {copied}\n  real:    {real}"
    )


def test_a_denial_with_an_unreadable_amount_still_carries_the_marker():
    """The `"requested": null` case, from the real producer.

    A hostile or unparseable amount is denied with a strictly-valid body whose
    `requested` is null. Both sides have to survive that: the PDS emits it, and
    the agent's detector must not raise while handling a denial.
    """
    hostile = b'{"name":"execute_payment","arguments":{"amount":"6200"}}'
    decision, result = _pds_decision(hostile)
    assert decision.permit is False
    assert json.loads(result.error.mcp_error)["data"]["requested"] is None

    denial = _policy_denial(_as_it_arrives_at_the_agent(result))
    assert isinstance(denial, PolicyDenial)
    assert denial.requested is None


def test_the_agent_does_not_import_the_pds_in_production():
    """This file's own licence to exist, guarded.

    The import above is test-only. If a production module ever grows a `pds`
    import, the agent has acquired a Python dependency on the policy service --
    which would contradict the architecture the milestone demonstrates, where
    the agent reaches the policy only through the gateway and cannot reach
    around it.
    """
    here = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for module in sorted(here.glob("*.py")):
        text = module.read_text()
        if "import pds" in text or "from pds" in text:
            offenders.append(module.name)
    assert offenders == [], (
        f"production modules import the policy service directly: {offenders}"
    )
