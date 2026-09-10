import json

import pytest
from pds.server import build_result, parse_call

import ext_mcp_pb2 as pb

AGENT = "spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent"


def test_parses_tool_name_and_amount_from_a_tools_call():
    params = json.dumps({"name": "execute_payment",
                         "arguments": {"payment_id": "PAY-1", "amount": 6200}}).encode()
    call = parse_call(params)
    assert call.tool == "execute_payment"
    assert call.amount == 6200.0


def test_a_permit_returns_the_pass_variant():
    result = build_result(permit=True, reason="ok", rule="r", limit=6000.0)
    assert result.WhichOneof("result") == "pass"


def test_a_deny_returns_permission_denied_with_a_structured_body():
    result = build_result(permit=False, reason="too much", rule="r", limit=6000.0)
    assert result.WhichOneof("result") == "error"
    assert result.error.code == pb.AuthorizationError.PERMISSION_DENIED
    body = json.loads(result.error.mcp_error)
    assert body["data"]["retryable"] is False
    assert body["data"]["rule"] == "r"
    assert body["data"]["limit"] == 6000.0
    assert "too much" in body["message"]


def test_the_deny_body_marks_itself_non_retryable_explicitly():
    """`mcp_tools.py` keys its terminal branch off this field. If it ever
    stops being emitted, the agent can retry a refused payment on stage."""
    result = build_result(permit=False, reason="no", rule="r", limit=1.0)
    assert json.loads(result.error.mcp_error)["data"]["retryable"] is False


def test_malformed_params_do_not_raise():
    """A parse failure must become a denial, not a gRPC error -- a gRPC error
    hits the gateway's failureMode and is reported as an outage."""
    call = parse_call(b"not json")
    assert call.tool is None and call.amount is None


# --- Beyond the brief -------------------------------------------------------
# Ruling R13 and Task 1's measured deny mapping are the two things most likely
# to be broken by a well-meaning later edit, and neither is covered above.


def test_the_human_readable_reason_is_on_the_authorization_error_itself():
    """R13, and Task 1's measured mapping: the gateway puts
    `AuthorizationError.reason` in `error.message`, which is what survives
    `str(McpError)` and therefore the only part of the refusal that reaches the
    A2A caller and the model that has to narrate it. `mcp_error` lands whole in
    `error.data`, which `str(McpError)` drops. A refusal explained only in
    `mcp_error` would be invisible to the model.
    """
    reason = ("requested 6200 EUR exceeds the 6000 EUR limit for this agent "
              "at autonomy level assisted")
    result = build_result(permit=False, reason=reason, rule="r", limit=6000.0)
    assert result.error.reason == reason


def test_the_deny_body_carries_what_was_requested_not_only_the_limit():
    """A refusal a human has to act on needs both numbers. The reason string
    has them for the model; `data` has them typed, for anything keying off the
    decision rather than reading it."""
    body = json.loads(build_result(permit=False, reason="no", rule="r",
                                   limit=6000.0, requested=6200.0).error.mcp_error)
    assert body["data"]["requested"] == 6200.0
    assert body["data"]["decision"] == "deny"


def test_a_permit_carries_no_error_and_no_body():
    """`Pass` unmodified. The response side is unused in M2.2 and a permit must
    not put the PDS in the data path of a successful call."""
    result = build_result(permit=True, reason="within the limit", rule="r",
                          limit=6000.0, requested=100.0)
    assert not result.HasField("error")
    assert not result.HasField("header_mutation")


def test_parses_the_float_amount_the_agent_actually_sends():
    """Task 1 measured `6200.0`, not `6200`, because that is what
    settlement-agent serialises. Parsing must not assume an integer."""
    params = (b'{"name":"propose_payment","arguments":'
              b'{"claim_id":"CLM-1842","amount":6200.0,"currency":"EUR"}}')
    call = parse_call(params)
    assert call.tool == "propose_payment"
    assert call.amount == 6200.0
    assert call.currency == "EUR"


def test_a_string_amount_is_not_silently_coerced():
    """`"6200"` is not a number the policy may reason about. Coercing it would
    let a caller choose its own type; leaving it None makes `decide` deny on
    `malformed_request`, which is the honest outcome."""
    params = b'{"name":"execute_payment","arguments":{"amount":"6200"}}'
    assert parse_call(params).amount is None


@pytest.mark.parametrize("params", [
    None,
    b"",
    b"null",
    b"[]",
    b'{"name": 42}',
    b'{"name":"execute_payment","arguments":"not-an-object"}',
    b"\xff\xfe not utf-8",
    b'{"name":"execute_payment","arguments":{"amount":NaN}}',
    b'{"name":"execute_payment","arguments":{"amount":-Infinity}}',
])
def test_no_params_shape_makes_parse_call_raise(params):
    """Every one of these must become a `ToolCall` with holes in it, which
    `decide` then denies. None of them may raise: `parse_call` runs inside
    `CheckRequest`, and a raise there becomes a gRPC error the gateway reports
    as a PDS outage rather than as a policy decision."""
    call = parse_call(params)
    assert call.amount is None or isinstance(call.amount, float)
    assert isinstance(call.currency, str)


# --- F5, the parser's half of the fix ---------------------------------------


@pytest.mark.parametrize("literal", [b"NaN", b"Infinity", b"-Infinity",
                                     b"1e309", b"-1e309"])
def test_a_non_finite_amount_never_becomes_a_policy_input(literal):
    """Python's `json` accepts the non-standard literals `NaN`, `Infinity` and
    `-Infinity` by default, and `1e309` overflows to `inf`, so a caller really
    can put one of these on the wire -- the reviewer did.

    Refusing here is defence in depth behind `rules.decide`, and it earns its
    place twice: `json.dumps` would otherwise write `NaN` straight back out
    into `mcp_error.data.requested`, which is not valid JSON, so the gateway
    would drop the whole deny body with a `warn!` and the structured refusal
    Task 5 keys off would silently vanish.
    """
    params = b'{"name":"execute_payment","arguments":{"amount":' + literal + b"}}"
    call = parse_call(params)
    assert call.tool == "execute_payment", "the tool must still be recoverable"
    assert call.amount is None


def test_an_integer_too_large_for_a_float_does_not_raise():
    """A JSON integer literal this big raises `OverflowError` on `float()`
    rather than returning `inf`, and `parse_call` must never raise."""
    params = (b'{"name":"execute_payment","arguments":{"amount":'
              + b"9" * 400 + b"}}")
    assert parse_call(params).amount is None


def test_a_finite_amount_of_every_ordinary_shape_still_parses():
    """The guard must not eat real amounts. A zero or negative amount *does*
    still reach `decide` -- refusing it is the rule's job, and refusing it
    there is what puts `invalid_amount` in the audit record rather than the
    parser's `malformed_request`."""
    def amount(literal):
        return parse_call(b'{"name":"execute_payment","arguments":{"amount":'
                          + literal + b"}}").amount
    assert amount(b"6200.0") == 6200.0
    assert amount(b"6200") == 6200.0
    assert amount(b"0") == 0.0
    assert amount(b"-500000.0") == -500000.0
    assert amount(b"0.01") == 0.01


