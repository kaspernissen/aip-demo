"""The gateway's own guardrail: a hard ceiling on execute_payment, as CEL."""
import re
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[2] / "deploy" / "platform" / "agentgateway" / "config.yaml"
# CEILING_EUR is read from the config, never retyped: the rule text below is the
# spec's shape with the one number taken from the file under test.
CEILING_EUR = int(re.search(r"arguments\.amount <= (\d+)", CONFIG.read_text()).group(1))
RULE = ('request.method != "POST" || mcp.methodName != "tools/call" || mcp.tool.name != "execute_payment" '
        f'|| (mcp.tool.arguments.amount > 0 && mcp.tool.arguments.amount <= {CEILING_EUR})')


CALLER_CLAUSE = ('jwt.sub in ["spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent", '
                 '"spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"]')


def _norm(text):
    return " ".join(text.split())


def _route():
    config = yaml.safe_load(CONFIG.read_text())
    (bind,) = config["binds"]
    (listener,) = bind["listeners"]
    (route,) = listener["routes"]
    return route


def test_the_ceiling_is_one_allow_rule_on_the_route():
    rules = _route()["policies"]["authorization"]["rules"]
    assert len(rules) == 1
    rule = rules[0]
    text = rule if isinstance(rule, str) else rule.get("allow")
    assert text is not None, "the ceiling must be an allow rule: a deny rule fails open on a string amount"
    # The ceiling clause is unchanged and is still the only amount check; the
    # caller clause in front of it is pinned by the next test.
    assert _norm(text) == f"{CALLER_CLAUSE} && ({RULE})"
    assert _norm(text).count("amount <=") == 1


def test_the_caller_clause_names_exactly_the_two_callers_and_is_anded_with_the_ceiling():
    """Only settlement-agent and reviewer-ui may use the MCP route at all (claims-agent
    holds a gateway-audience SVID for the llm listener and reaches port 3000, so
    jwtAuth alone would admit it). `&&`, not `||`: an OR would let either clause
    alone admit a call, i.e. any listed caller over the ceiling, or any caller
    under it."""
    text = _norm(_route()["policies"]["authorization"]["rules"][0]["allow"])
    prefix = f"{CALLER_CLAUSE} && ("
    assert text.startswith(prefix) and text.endswith(")"), text
    assert text[len(prefix):-1] == RULE
    # Nothing sits outside the ANDed pair: no top-level `||` after the caller clause.
    assert "||" not in text[: len(prefix)]
    assert "claims-agent" not in text


def test_the_ceiling_is_above_every_published_agent_limit():
    rules_py = (CONFIG.parents[3] / "platform" / "policy-decision-service" / "pds" / "rules.py").read_text()
    limits = [float(x.replace("_", "")) for x in re.findall(r'"\):\s*([0-9_.]+),', rules_py)]
    assert limits and max(limits) < CEILING_EUR


def test_the_pds_hook_is_still_there():
    assert _route()["policies"]["mcpGuardrails"]["processors"][0]["failureMode"] == "failClosed"
