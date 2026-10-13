"""The one deny-message format. agentgateway v1.6.0 drops `error.data`, so the
rule, the limit and the requested amount travel in the message text itself."""
from pds.deny_text import DENY_TAG, format_deny


def test_the_format_is_reason_then_one_bracketed_tag_at_the_end():
    text = format_deny(
        "requested 6200 EUR exceeds the 6000 EUR limit",
        "settlement-agent.assisted.execute_limit", 6000.0, 6200.0,
    )
    assert text == (
        "requested 6200 EUR exceeds the 6000 EUR limit "
        "[policy-denial rule=settlement-agent.assisted.execute_limit "
        "limit=6000.00 requested=6200.00]"
    )


def test_a_missing_figure_is_written_none_not_zero():
    text = format_deny("cannot evaluate", "internal_error", None, None)
    assert text.endswith("[policy-denial rule=internal_error limit=none requested=none]")


def test_a_zero_limit_is_written_as_a_number():
    # M8's assisted row is EUR 0: zero is a real limit, not an absent one.
    assert "limit=0.00" in format_deny("r", "settlement-agent.assisted.execute_limit", 0.0, 5800.0)


def test_the_tag_name_is_the_documented_one():
    assert DENY_TAG == "policy-denial"
