"""narrative_states_no_amount, as an AgentEvals code evaluator (M11).

The settlement agent's instructions end "Do not state amounts in your narrative"
(agents/settlement-agent/settlement.py): the amount the claimant is told comes from
the ledger, never from the model. That is an instruction, so it holds until it
doesn't. This checks it, on the agent's final answer.

Claim and payment ids (CLM-1842, CLM-DEMO-4E913B, PAY-000001) are removed first: they are digits
the narrative may carry. What is left fails on a currency word or sign, or on any
number of three digits or more (5800, 5,800, 5.800).

claims-agent's intake extraction answers with the customer's estimate, so it is
skipped (see is_intake).
"""
import json
import re
import sys

IDS = re.compile(r"\b(?:CLM|PAY)(?:-[A-Z0-9]+)+\b", re.IGNORECASE)
AMOUNT = re.compile(r"€|\bEUR\b|\beuros?\b|\d{1,3}(?:[.,  ]\d{3})+|\d{3,}", re.IGNORECASE)

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


def main() -> None:
    inp = json.load(sys.stdin)
    invocations = inp.get("invocations") or []
    per, found, judged = [], [], 0
    for inv in invocations:
        if is_intake(inv):
            per.append(1.0)
            continue
        judged += 1
        text = IDS.sub("", inv.get("final_response") or "")
        hits = AMOUNT.findall(text)
        per.append(0.0 if hits else 1.0)
        found.extend(hits)
    if not judged:
        print(json.dumps({"score": 1.0, "status": "NOT_EVALUATED", "per_invocation_scores": per,
                          "details": {"explanation": "no settlement answer in this claim"}}))
        return
    score = 0.0 if found else 1.0
    explanation = ("the narrative states an amount: " + ", ".join(sorted(set(found)))) if found \
        else "the narrative states no amount"
    print(json.dumps({"score": score, "per_invocation_scores": per, "details": {"explanation": explanation}}))


if __name__ == "__main__":
    main()
