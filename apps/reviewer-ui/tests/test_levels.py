from levels import autonomy_level_of


def test_reads_the_level_out_of_the_rule():
    assert autonomy_level_of("settlement-agent.assisted.execute_limit") == "assisted"
    assert autonomy_level_of("settlement-agent.autonomous.execute_limit") == "autonomous"


def test_anything_else_is_unknown_not_a_guess():
    for rule in (None, "", "human-approved.execute", "settlement-agent.assisted.execute_limit.x"):
        assert autonomy_level_of(rule) == "unknown"
