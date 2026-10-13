from policy_denial_text import DenialText, parse_deny

TAG = "[policy-denial rule=settlement-agent.assisted.execute_limit limit=0.00 requested=5800.00]"


def test_parses_rule_limit_and_requested_from_the_end_of_the_text():
    got = parse_deny(f"requested 5800 EUR exceeds the 0 EUR limit {TAG}")
    assert got == DenialText(
        reason="requested 5800 EUR exceeds the 0 EUR limit",
        rule="settlement-agent.assisted.execute_limit",
        limit=0.0,
        requested=5800.0,
    )


def test_a_gateway_prefix_in_front_does_not_matter():
    got = parse_deny(f"mcp: mcpGuardrails rejected: some reason {TAG}")
    assert got is not None and got.rule == "settlement-agent.assisted.execute_limit"


def test_none_is_none():
    got = parse_deny("r [policy-denial rule=internal_error limit=none requested=none]")
    assert got.limit is None and got.requested is None


def test_an_ordinary_tool_error_is_not_a_denial():
    # payments-api's own 409, as payments-mcp surfaces it: a malfunction, never a policy no.
    assert parse_deny("Error calling tool 'execute_payment': Client error '409 Conflict'") is None


def test_a_tag_that_is_not_at_the_end_is_not_a_denial():
    assert parse_deny(f"{TAG} and then more text") is None


def test_a_malformed_amount_is_not_a_denial():
    assert parse_deny("r [policy-denial rule=x limit=6000 requested=none]") is None
