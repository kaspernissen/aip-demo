import { describe, expect, it } from "vitest";
import { determinationView, formatElapsed, outcomeWord } from "./view";

describe("determinationView", () => {
  it("says awaiting human approval while the task waits", () => {
    expect(determinationView({ claimId: "C", status: "awaiting_approval" })).toEqual({
      tone: "is-waiting",
      heading: "Awaiting human approval",
    });
  });
  it("says settled when executed", () => {
    expect(determinationView({ claimId: "C", status: "executed" }).heading).toBe("Settled");
  });
  it("says declined by a reviewer when rejected", () => {
    expect(determinationView({ claimId: "C", status: "rejected" }).heading).toBe(
      "Declined by a reviewer",
    );
  });
  it("says plainly when the claim could not be processed", () => {
    expect(determinationView({ claimId: "C", status: "failed" }).heading).toBe("Could not be processed");
  });
  it("says plainly when the claim could not be routed for review", () => {
    expect(determinationView({ claimId: "C", status: "refusal_unrecorded" }).heading).toBe(
      "Not routed for review",
    );
  });
});

describe("formatElapsed", () => {
  it("is minutes and seconds", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(65_000)).toBe("1:05");
    expect(formatElapsed(600_000)).toBe("10:00");
  });
});

describe("outcomeWord", () => {
  it("names the outcome in the words the log should use", () => {
    expect(outcomeWord("executed")).toBe("settled");
    expect(outcomeWord("rejected")).toBe("declined");
    expect(outcomeWord("refusal_unrecorded")).toBe("not routed");
    expect(outcomeWord("failed")).toBe("failed");
    expect(outcomeWord("awaiting_approval")).toBe("awaiting human approval");
  });
  it("falls back to the state for a status it does not know", () => {
    expect(outcomeWord(undefined, "completed")).toBe("completed");
    expect(outcomeWord("surprise")).toBe("surprise");
  });
});
