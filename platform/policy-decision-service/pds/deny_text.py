"""The deny message, in the one format the settlement-agent parses.

agentgateway v1.6.0 returns a `mcpGuardrails` deny on `tools/call` as an MCP
tool result, `{"isError": true, "content": [{"type": "text", "text": ...}]}`,
and drops `error.data` (upstream PR #3526, `tool_error_body` in
crates/agentgateway/src/mcp/mod.rs). The text is all that reaches the agent, so
the facts the agent needs ride in it, in a fixed shape at the very end:

    <reason> [policy-denial rule=<rule> limit=<amount|none> requested=<amount|none>]

`<amount>` has exactly two decimals. `none` means the PDS had no figure.
The parser is agents/settlement-agent/policy_denial_text.py::parse_deny, and
agents/settlement-agent/tests/test_policy_deny_contract.py feeds this function's
real output into it. Change one, and that test goes red.
"""
from __future__ import annotations

DENY_TAG = "policy-denial"


def _amount(value: float | None) -> str:
    return "none" if value is None else f"{value:.2f}"


def format_deny(reason: str, rule: str, limit: float | None, requested: float | None) -> str:
    return (
        f"{reason} [{DENY_TAG} rule={rule} limit={_amount(limit)} "
        f"requested={_amount(requested)}]"
    )
