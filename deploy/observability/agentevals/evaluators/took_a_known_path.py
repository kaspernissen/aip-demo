"""took_a_known_path, as an AgentEvals code evaluator (M11).

The model has two payment tools, and a settlement takes one path through them,
read off its tool calls in order (get_payment, a read, is left out):

  settled   propose_payment -> execute_payment
  held      propose_payment -> execute_payment (refused by the platform)

The refusal itself is recorded by settlement-agent's own code after the run
(decide_settlement), not by the model, so it is not a tool call here (measured on
the live demo, 2026-10-21). Anything else, a skipped proposal, a second execute, is
a path nobody designed. This is the deterministic stand-in for AgentEvals' own
tool_trajectory_avg_score, which compares tool arguments too (claim ids, amounts)
and, at agentevals 0.11.0, exposes no switch to ignore them: against a golden run
of another claim it fails every claim.
"""
import json
import sys

PATH = ["propose_payment", "execute_payment"]


def refused(response: dict) -> bool:
    return response.get("status") == "error" or "[policy-denial" in str(response.get("output") or "")


def main() -> None:
    inp = json.load(sys.stdin)
    invocations = inp.get("invocations") or []
    path, execute_refused = [], False
    for inv in invocations:
        inter = inv.get("intermediate_steps") or {}
        responses = {r.get("id"): r for r in inter.get("tool_responses") or []}
        for call in inter.get("tool_calls") or []:
            name = call.get("name")
            if name == "get_payment":
                continue
            path.append(name)
            if name == "execute_payment" and refused(responses.get(call.get("id")) or {}):
                execute_refused = True
    if not path:
        print(json.dumps({"score": 1.0, "status": "NOT_EVALUATED", "per_invocation_scores": [],
                          "details": {"explanation": "no tool calls in this claim"}}))
        return
    known = path == PATH
    which = ("held" if execute_refused else "settled") if known else "unknown"
    print(json.dumps({"score": 1.0 if known else 0.0, "details": {
        "explanation": f"{which} path: " + " -> ".join(path) + ("" if known else "  (not a designed path)")}}))


if __name__ == "__main__":
    main()
