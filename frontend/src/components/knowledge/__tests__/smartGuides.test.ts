import { describe, it, expect } from "vitest";
import { smartGuides } from "../smartGuides";
const r = (id: string, x: number, y: number, width = 100, height = 100) => ({
  id,
  x,
  y,
  width,
  height,
});
describe("free placement smart guides", () => {
  it("aligns edges and centers without moving the reference", () => {
    const ref = r("ref", 0, 0);
    expect(smartGuides([r("move", 4, 400)], [ref], 1)).toMatchObject({
      dx: -4,
      dy: 0,
      lines: [{ x1: 0, x2: 0 }],
    });
    expect(ref.x).toBe(0);
    expect(smartGuides([r("move", -50, 400, 200)], [ref], 1).lines[0].x1).toBe(
      50,
    );
  });
  it("uses screen distance at different zoom levels", () => {
    expect(smartGuides([r("move", 8, 400)], [r("ref", 0, 0)], 0.5).dx).toBe(-8);
    expect(smartGuides([r("move", 4, 400)], [r("ref", 0, 0)], 2).dx).toBe(0);
  });
  it("retains a snap slightly longer and releases it without drift", () => {
    const refs = [r("ref", 0, 0)],
      locks = smartGuides([r("move", 4, 400)], refs, 1).locks;
    expect(smartGuides([r("move", 8, 400)], refs, 1, locks).dx).toBe(-8);
    expect(smartGuides([r("move", 8, 400)], refs, 1).dx).toBe(0);
    expect(
      smartGuides([r("move", 11, 400)], refs, 1, locks).locks.x,
    ).toBeUndefined();
  });
  it("centers between two cards with equal gaps", () => {
    const result = smartGuides(
      [r("m", 154, 0)],
      [r("a", 0, 0), r("b", 300, 0)],
      1,
    );
    expect(result.dx).toBe(-4);
    expect(
      result.gaps.filter((g) => g.axis === "x").map((g) => g.end - g.start),
    ).toEqual([50, 50]);
  });
  it("matches repeated gaps after or before a row", () => {
    const refs = [r("a", 0, 0), r("b", 300, 0)];
    expect(
      smartGuides([r("m", 604, 0)], refs, 1)
        .gaps.filter((g) => g.axis === "x")
        .map((g) => g.end - g.start),
    ).toEqual([200, 200]);
    expect(smartGuides([r("m", -304, 0)], refs, 1).dx).toBe(4);
  });
  it("handles unequal widths and vertical equal spacing", () => {
    expect(
      smartGuides([r("m", 404, 0)], [r("a", 0, 0, 80), r("b", 180, 0, 120)], 1)
        .dx,
    ).toBe(-4);
    const result = smartGuides(
      [r("m", 0, 154)],
      [r("a", 0, 0), r("b", 0, 300)],
      1,
    );
    expect(result.dy).toBe(-4);
    expect(
      result.gaps.filter((g) => g.axis === "y").map((g) => g.end - g.start),
    ).toEqual([50, 50]);
  });
  it("uses the visible selection bounds and excludes moving cards as references", () => {
    const result = smartGuides(
      [r("a", 4, 300), r("b", 104, 500)],
      [r("a", 4, 300), r("ref", 0, 0)],
      1,
    );
    expect(result.dx).toBe(-4);
    expect(smartGuides([r("a", 4, 300)], [r("a", 4, 300)], 1).lines).toEqual(
      [],
    );
  });
  it("does not invent spacing from overlapping references or unrelated rows", () => {
    const result = smartGuides(
      [r("m", 400, 500)],
      [r("a", 0, 0), r("b", 80, 0)],
      1,
    );
    expect(result.gaps).toEqual([]);
  });
  it("resolves ties deterministically regardless of reference order", () => {
    const refs = [r("z", 0, 0), r("a", 0, 200)];
    expect(smartGuides([r("m", 4, 500)], refs, 1)).toEqual(
      smartGuides([r("m", 4, 500)], [...refs].reverse(), 1),
    );
  });
  it("handles empty input and invalid zoom safely", () => {
    expect(smartGuides([], [], 1)).toMatchObject({ dx: 0, dy: 0 });
    expect(smartGuides([r("m", 0, 0)], [r("ref", 0, 0)], 0).lines).toEqual([]);
  });
});
