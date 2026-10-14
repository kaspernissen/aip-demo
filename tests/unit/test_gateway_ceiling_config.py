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
    assert " ".join(text.split()) == RULE


def test_the_ceiling_is_above_every_published_agent_limit():
    rules_py = (CONFIG.parents[3] / "platform" / "policy-decision-service" / "pds" / "rules.py").read_text()
    limits = [float(x.replace("_", "")) for x in re.findall(r'"\):\s*([0-9_.]+),', rules_py)]
    assert limits and max(limits) < CEILING_EUR


def test_the_pds_hook_is_still_there():
    assert _route()["policies"]["mcpGuardrails"]["processors"][0]["failureMode"] == "failClosed"
