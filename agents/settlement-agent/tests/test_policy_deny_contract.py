"""The deny-text contract, asserted against **both** real implementations.

The PDS writes the deny message in `pds/deny_text.py::format_deny`; the agent
reads it with `policy_denial_text.parse_deny` inside `mcp_tools._unwrap`.
Producer and consumer of a contract with nothing asserting they agree is the
classic silent-drift shape. So this file does not copy anything. It drives the
real chain, end to end, from the bytes the gateway measurably sends the PDS to
the exception `mcp_tools._unwrap` raises:

    the measured `mcp_request` bytes
      -> pds.server.parse_call          (the PDS's real wire parser)
      -> pds.rules.decide               (the real policy)
      -> pds.server.build_result        (the real deny, via format_deny)
      -> agentgateway v1.6.0's mapping  (`_as_it_arrives_at_the_agent`)
      -> mcp.types.CallToolResult       (the real result type)
      -> mcp_tools._unwrap              (the real reader, via parse_deny)

Only one hop in that chain is a stand-in, and it is the one hop that is not
Python: agentgateway. Its mapping was measured on v1.6.0: a PERMISSION_DENIED
rejection on `tools/call` becomes HTTP 200 with an `isError` tool result whose
text is the rejection message (upstream PR #3526). `error.data` is gone.

**The import of `pds` is test-only.** Nothing in `agents/settlement-agent`'s
production code imports the PDS, and nothing may: the agent reaches the policy
through the gateway, never directly, and a Python-level dependency would
quietly contradict that architecture. The `sys.path` insertion below happens
in a test module, so it cannot reach `mcp_tools.py`, `settlement.py` or the
image.

`pds/server.py` imports `openfeature` at module scope (the autonomy-level
client it builds once), which is not a dependency this file's own venv
(`agents/settlement-agent/.venv`) carries and never should -- house style is
one clean venv per service, and this file borrows the PDS's *source* rather
than pip-installing it for exactly that reason. So the
`sys.path` insertion below also appends the PDS's own already-built `.venv`
site-packages -- `append`, not `insert(0, ...)`, so any package name the two
services' venvs happen to share (`grpcio`, `protobuf`,
`opentelemetry-{api,sdk}` -- all pinned to the same versions in both
`requirements.txt` files already, not a coincidence this file relies on) is
still resolved from this service's own venv first, and the PDS's copy is only
reached for names this venv genuinely has none of (`openfeature*` and its own
transitive closure). This does not add anything to
`agents/settlement-agent/requirements.txt` or install anything into this
service's `.venv` -- it reads a sibling venv's already-installed packages
exactly as it already reads a sibling service's already-written source.

Concrete regression check: change `DENY_TAG` in `pds/deny_text.py` and
`test_the_pds_deny_is_a_policy_denial_at_the_agent` fails here, outside the PDS
suite, which is the whole point of the file.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
from mcp.types import CallToolResult, TextContent

from mcp_tools import PolicyDenial, _unwrap

# --- the test-only path to the producer -------------------------------------

_PDS_DIR = (
    pathlib.Path(__file__).resolve().parents[3]
    / "platform"
    / "policy-decision-service"
)

_PDS_SITE_PACKAGES = sorted(_PDS_DIR.glob(".venv/lib/python3.*/site-packages"))

_HOW = (
    "\n\n"
    "This is the producer/consumer contract test for the policy deny marker.\n"
    "It imports the PDS's own deny-body builder so that a change to the\n"
    "marker on the PDS side cannot leave this service's suite green.\n"
    "\n"
    "The generated ExtMCP gRPC stubs it needs are git-ignored on purpose, and\n"
    "it also needs the PDS's own `.venv` built (for `openfeature`, imported\n"
    "at `pds/server.py` module scope).\n"
    "Generate/build both, then re-run:\n"
    "\n"
    "    cd platform/policy-decision-service && ./gen-stubs.sh\n"
    "    cd platform/policy-decision-service && rm -rf .venv "
    "&& uv venv --python 3.13 .venv && uv pip install -r requirements.txt\n"
    "\n"
    "Deliberately not an importorskip: a skipped contract test is a contract\n"
    "nobody is checking, and this project has already been burned by a\n"
    "silently-green suite full of skips.\n"
)

if str(_PDS_DIR) not in sys.path:
    sys.path.insert(0, str(_PDS_DIR))
for _site_packages in _PDS_SITE_PACKAGES:
    if str(_site_packages) not in sys.path:
        sys.path.append(str(_site_packages))

try:  # pragma: no cover - the failure path is exercised by deleting the stubs
    from pds.autonomy import RESTRICTIVE_LEVEL
    from pds.rules import PolicyInput, decide
    from pds import server
    from pds.server import build_result, parse_call
except ImportError as exc:
    raise RuntimeError(str(exc) + _HOW) from exc


AGENT = "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"

# The `mcp_request` payload shape measured on the wire, with the float
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
    this contract's. Also minus `resolve_autonomy`: this contract is about the
    deny-marker shape `parse_call`/`decide`/`build_result` produce, not about
    which autonomy level flagd hands back, so the level is fixed at
    `RESTRICTIVE_LEVEL` -- the "assisted" level every call in this file
    exercises.
    """
    call = parse_call(params)
    decision = decide(
        PolicyInput(
            caller=AGENT,
            tool=call.tool or "",
            amount=call.amount,
            currency=call.currency,
            autonomy_level=RESTRICTIVE_LEVEL,
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


def _as_it_arrives_at_the_agent(pds_result) -> CallToolResult:
    """agentgateway v1.6.0's mapping, measured: a PERMISSION_DENIED rejection on
    tools/call becomes HTTP 200, `result: {isError: true, content: [{type: text,
    text: <rejection message>}]}`. The PDS puts the same text in `reason` and in
    `mcp_error.message`, so this does not depend on which one the gateway reads."""
    assert pds_result.WhichOneof("result") == "error"
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=pds_result.error.reason)],
    )


# --- the contract -----------------------------------------------------------


def test_the_pds_deny_is_a_policy_denial_at_the_agent():
    """Nothing here is copied from the PDS. Change the tag or its fields in
    `pds/deny_text.py` and this fails -- in `agents/settlement-agent`, not only
    in the PDS suite."""
    decision, result = _pds_decision(MEASURED_EXECUTE)
    assert decision.permit is False, "the fixture stopped being a denial at all"

    with pytest.raises(PolicyDenial) as raised:
        _unwrap(_as_it_arrives_at_the_agent(result))

    assert raised.value.rule == decision.rule
    assert raised.value.limit == decision.limit
    assert raised.value.requested == 6200.0
    assert raised.value.retryable is False
    assert decision.reason in str(raised.value)


def test_reason_and_mcp_error_message_are_the_same_text_for_the_real_pds():
    """Which of the two the gateway reads is not ours to know; both must parse."""
    _, result = _pds_decision(MEASURED_EXECUTE)
    assert result.error.reason == json.loads(result.error.mcp_error)["message"]


def test_the_pds_internal_error_deny_is_also_a_policy_denial():
    pds_result = server._internal_error_result()
    with pytest.raises(PolicyDenial) as raised:
        _unwrap(_as_it_arrives_at_the_agent(pds_result))
    assert raised.value.rule == "internal_error"


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


def test_a_denial_with_an_unreadable_amount_still_parses():
    """A hostile or unparseable amount is denied with `requested=none`. Both
    sides have to survive that: the PDS writes it, and the agent must not raise
    anything but PolicyDenial while handling it."""
    hostile = b'{"name":"execute_payment","arguments":{"amount":"6200"}}'
    decision, result = _pds_decision(hostile)
    assert decision.permit is False

    with pytest.raises(PolicyDenial) as raised:
        _unwrap(_as_it_arrives_at_the_agent(result))
    assert raised.value.requested is None


def test_the_agent_does_not_import_the_pds_in_production():
    """This file's own licence to exist, guarded.

    The import above is test-only. If a production module ever grows a `pds`
    import, the agent has acquired a Python dependency on the policy service --
    which would contradict the architecture this demonstrates, where the
    agent reaches the policy only through the gateway and cannot reach around
    it.

    **Recursive, deliberately.** A glob scoped to the component root would
    pass a `pds` import from any future subpackage under
    `agents/settlement-agent/` -- a blind spot, not a guard. Its sibling
    guard, `tests/e2e/test_policy_denial.py::_source_files`, uses `rglob` for
    the same reason ("so that a future subpackage cannot become a blind
    spot"); this matches it, including the same exclusion set and the same
    reasons:

    * `tests/` is excluded because *this file* is the licensed exception -- it
      imports `pds` on purpose, three lines from the top.
    * `.venv`/`venv` are excluded because each component keeps its own pinned
      virtualenv inside its own directory; those are third-party dependencies,
      not this component's code, and walking them would be both wrong and slow.
    * `__pycache__` and `node_modules` for the same reason.

    Reported as paths relative to the component root rather than bare names,
    since with recursion a bare `settlement.py` no longer says where it lives.
    """
    here = pathlib.Path(__file__).resolve().parents[1]
    skip = {"tests", ".venv", "venv", "__pycache__", "node_modules"}
    offenders = []
    for module in sorted(here.rglob("*.py")):
        relative = module.relative_to(here)
        if skip & set(relative.parts):
            continue
        text = module.read_text()
        if "import pds" in text or "from pds" in text:
            offenders.append(str(relative))
    assert offenders == [], (
        f"production modules import the policy service directly: {offenders}"
    )
