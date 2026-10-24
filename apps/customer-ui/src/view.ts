import type { ClaimResult } from "./a2a";

export function determinationView(result: ClaimResult): { tone: string; heading: string } {
  switch ((result.status ?? "").toLowerCase()) {
    case "awaiting_approval":
      return { tone: "is-waiting", heading: "Awaiting human approval" };
    case "needs_information":
      return { tone: "is-waiting", heading: "We need a little more information" };
    case "executed":
      return { tone: "is-executed", heading: "Settled" };
    case "rejected":
      return { tone: "is-refused", heading: "Declined by a reviewer" };
    case "failed":
      return { tone: "is-refused", heading: "Could not be processed" };
    case "refusal_unrecorded":
      return { tone: "is-refused", heading: "Not routed for review" };
    default:
      return { tone: "is-other", heading: "Recorded" };
  }
}

export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** The one word the log line uses for a terminal state, matching the outcome. */
export function outcomeWord(status?: string, state?: string): string {
  switch ((status ?? "").toLowerCase()) {
    case "executed":
      return "settled";
    case "rejected":
      return "declined";
    case "refusal_unrecorded":
      return "not routed";
    case "failed":
      return "failed";
    case "awaiting_approval":
      return "awaiting human approval";
    case "needs_information":
      return "waiting for your answer";
    default:
      return status || state || "recorded";
  }
}

/**
 * The agent's question, when the claim waits for the customer: the task is
 * input-required AND its reply says needs_information. The other wait
 * (awaiting_approval) is for a reviewer, and has no question.
 */
export function questionOf(state: string | undefined, result: ClaimResult | null): string | null {
  if (state !== "input-required" || result?.status !== "needs_information") return null;
  return result.question || result.narrative || null;
}
