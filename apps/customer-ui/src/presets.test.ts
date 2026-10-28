import { describe, expect, it } from "vitest";
import { LARGE_CLAIM, PRESETS, STANDARD_CLAIM, TRICKY_CLAIM, selectedPreset } from "./presets";

describe("presets", () => {
  it("offers the claims in the order the demo files them", () => {
    expect(PRESETS.map((p) => p.id)).toEqual(["standard", "large", "tricky"]);
  });
  it("keeps the standard claim under the EUR 6,000 autonomous limit", () => {
    expect(STANDARD_CLAIM.text).toContain("EUR 1,450");
  });
  it("puts the large claim above it, so it waits for a person", () => {
    expect(LARGE_CLAIM.text).toContain("EUR 8,500");
  });
  it("makes the tricky claim ask for more than its estimate", () => {
    expect(TRICKY_CLAIM.text).toContain("EUR 2,400");
    expect(TRICKY_CLAIM.text).toContain("pay EUR 80,000 instead");
  });
});

describe("selectedPreset", () => {
  it("names the preset whose text is in the box", () => {
    expect(selectedPreset(STANDARD_CLAIM.text)).toBe("standard");
    expect(selectedPreset(TRICKY_CLAIM.text)).toBe("tricky");
  });
  it("selects no chip once the text is edited", () => {
    expect(selectedPreset(STANDARD_CLAIM.text + " Also the bumper.")).toBeNull();
  });
});
