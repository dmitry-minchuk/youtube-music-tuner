import { describe, expect, it } from "vitest";
import { relaxationMessage } from "@/features/wave/WavePage";

describe("relaxationMessage", () => {
  it("stays silent when nothing was relaxed", () => {
    expect(relaxationMessage([])).toBeNull();
  });

  it("explains a discovery shortage before anything else", () => {
    expect(
      relaxationMessage(["CONTEXT_WIDENED", "DISCOVERY_POOL_WIDENED", "FAMILIAR_POOL_WIDENED"]),
    ).toMatch(/discovery candidates/);
  });

  it("explains a familiar shortage ahead of context widening", () => {
    expect(relaxationMessage(["CONTEXT_WIDENED", "FAMILIAR_POOL_WIDENED"])).toMatch(
      /familiar tracks/,
    );
  });

  it("reports an ineffective context honestly", () => {
    expect(relaxationMessage(["CONTEXT_WIDENED"])).toMatch(/Context widened/);
  });

  it("explains the rotation reserve instead of calling it diversity", () => {
    expect(relaxationMessage(["FAMILIAR_ROTATION_CAP", "CONTEXT_WIDENED"])).toMatch(/reserve/);
  });

  it("falls back to the diversity message for ladder codes", () => {
    expect(relaxationMessage(["ARTIST_WINDOW_15_RELAXED"])).toMatch(/Diversity widened/);
  });
});
