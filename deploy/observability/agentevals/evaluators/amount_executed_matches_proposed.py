"""amount_executed_matches_proposed, as an AgentEvals code evaluator.

AgentEvals runs this file as a subprocess: one EvalInput JSON document on stdin,
one result on stdout (protocol 1.1, docs/custom-evaluators.md upstream).

Both payment tools take an `amount` (agents/settlement-agent/settlement.py): the
model proposes one, then executes the payment stating the amount it is paying.
This checks that every execute_payment states the amount of the propose_payment
before it, in the same session (one claim), read off the agent's tool calls.

  pass             every execute's amount equals the latest proposed amount
  not evaluated    no execute_payment in the claim
  fail             an execute with a different amount, no amount, or nothing proposed

A refused execute is still compared: what the agent asked for is the point, not
whether the platform let it through. claims-agent's intake extraction is skipped
(see is_intake).
"""
import json
import sys

# claims-agent's intake extraction runs under the same gen_ai.conversation.id
# (the claim id) as the settlement, so AgentEvals files it in the claim's session.
# Its prompt opens with this prefix (agents/claims-agent/extraction.py); such an
# invocation is not a settlement and is not judged here.
INTAKE_PROMPT_PREFIX = "First notice of loss"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return " ".join(_text(p) for p in content.get("parts") or []) or str(content.get("text") or "")
    if isinstance(content, list):
        return " ".join(_text(p) for p in content)
    return ""


def is_intake(inv: dict) -> bool:
    return _text(inv.get("user_content")).lstrip().startswith(INTAKE_PROMPT_PREFIX)


def _amount(call: dict):
    """The call's `amount` argument as a number, or None."""
    args = call.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    if not isinstance(args, dict):
        return None
    try:
        return float(args["amount"])
    except (KeyError, TypeError, ValueError):
        return None


def main() -> None:
    inp = json.load(sys.stdin)
    invocations = inp.get("invocations") or []
    per = [1.0] * len(invocations)
    proposed = None  # (amount or None) of the latest propose_payment, once there is one
    seen_propose, executes, wrong = False, 0, []
    for i, inv in enumerate(invocations):
        if is_intake(inv):
            continue
        for call in (inv.get("intermediate_steps") or {}).get("tool_calls") or []:
            name = call.get("name")
            if name == "propose_payment":
                seen_propose, proposed = True, _amount(call)
            elif name == "execute_payment":
                executes += 1
                executed = _amount(call)
                if not seen_propose:
                    wrong.append(f"executed {executed} with nothing proposed")
                elif proposed is None or executed is None:
                    wrong.append(f"executed {executed} against proposed {proposed}")
                elif executed != proposed:
                    wrong.append(f"executed {executed:g}, proposed {proposed:g}")
                else:
                    continue
                per[i] = 0.0
    if not executes:
        print(json.dumps({"score": 1.0, "status": "NOT_EVALUATED", "per_invocation_scores": per,
                          "details": {"explanation": "no execute_payment in this claim"}}))
        return
    if wrong:
        explanation = "; ".join(wrong)
    else:
        explanation = f"{executes} execute(s) at the proposed amount, {proposed:g}"
    print(json.dumps({"score": 0.0 if wrong else 1.0, "per_invocation_scores": per,
                      "details": {"explanation": explanation}}))


if __name__ == "__main__":
    main()
