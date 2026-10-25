export type GuideCard = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
};
export type GuideLine = { x1: number; y1: number; x2: number; y2: number };
export type GapHint = {
  axis: "x" | "y";
  start: number;
  end: number;
  cross: number;
};
export type SnapFeedback = {
  lines: GuideLine[];
  gaps: GapHint[];
  locks: { x?: string; y?: string };
  dx: number;
  dy: number;
};
type Axis = "x" | "y";
type Candidate = {
  key: string;
  delta: number;
  line?: number;
  peer?: GuideCard;
  segments?: [number, number][];
};
const extent = (r: GuideCard, a: Axis) => (a === "x" ? r.width : r.height);
function union(cards: GuideCard[]): GuideCard {
  const x = Math.min(...cards.map((c) => c.x)),
    y = Math.min(...cards.map((c) => c.y));
  return {
    id: "selection",
    x,
    y,
    width: Math.max(...cards.map((c) => c.x + c.width)) - x,
    height: Math.max(...cards.map((c) => c.y + c.height)) - y,
  };
}
/** World coordinates in/out; acquisition and hysteresis use screen pixels. */
export function smartGuides(
  moving: GuideCard[],
  references: GuideCard[],
  zoom: number,
  previous: { x?: string; y?: string } = {},
): SnapFeedback {
  const out: SnapFeedback = { dx: 0, dy: 0, lines: [], gaps: [], locks: {} };
  if (!moving.length || !references.length || zoom <= 0) return out;
  const box = union(moving),
    ids = new Set(moving.map((c) => c.id)),
    refs = references
      .filter((c) => !ids.has(c.id))
      .sort((a, b) => a.id.localeCompare(b.id)),
    chosen: Partial<Record<Axis, Candidate>> = {};
  for (const axis of ["x", "y"] as const) {
    const other: Axis = axis === "x" ? "y" : "x",
      candidates: Candidate[] = [],
      size = extent(box, axis),
      anchors = [box[axis], box[axis] + size / 2, box[axis] + size];
    for (const r of refs) {
      const targets = [
        r[axis],
        r[axis] + extent(r, axis) / 2,
        r[axis] + extent(r, axis),
      ];
      for (let a = 0; a < 3; a++)
        for (let b = 0; b < 3; b++)
          candidates.push({
            key: `align:${r.id}:${a}:${b}`,
            delta: targets[b] - anchors[a],
            line: targets[b],
            peer: r,
          });
    }
    // Equal spacing uses neighbouring cards in the same visual row/column.
    const lane = refs
      .filter(
        (r) =>
          r[other] < box[other] + extent(box, other) &&
          r[other] + extent(r, other) > box[other],
      )
      .sort((a, b) => a[axis] - b[axis] || a.id.localeCompare(b.id));
    for (let i = 0; i < lane.length - 1; i++) {
      const a = lane[i],
        b = lane[i + 1],
        end = a[axis] + extent(a, axis),
        gap = b[axis] - end;
      if (
        gap < 0 ||
        a[other] >= b[other] + extent(b, other) ||
        a[other] + extent(a, other) <= b[other]
      )
        continue;
      const base = `gap:${a.id}:${b.id}`,
        after = b[axis] + extent(b, axis) + gap,
        before = a[axis] - gap - size;
      candidates.push({
        key: base + ":after",
        delta: after - box[axis],
        segments: [
          [end, b[axis]],
          [b[axis] + extent(b, axis), after],
        ],
      });
      candidates.push({
        key: base + ":before",
        delta: before - box[axis],
        segments: [
          [before + size, a[axis]],
          [end, b[axis]],
        ],
      });
      if (gap >= size) {
        const start = end + (gap - size) / 2;
        candidates.push({
          key: base + ":between",
          delta: start - box[axis],
          segments: [
            [end, start],
            [start + size, b[axis]],
          ],
        });
      }
    }
    const retained = candidates.find(
      (c) => c.key === previous[axis] && Math.abs(c.delta) * zoom <= 10,
    );
    const best =
      retained ??
      candidates
        .filter((c) => Math.abs(c.delta) * zoom <= 6)
        .sort(
          (a, b) =>
            Math.abs(a.delta) - Math.abs(b.delta) || a.key.localeCompare(b.key),
        )[0];
    if (best) {
      chosen[axis] = best;
      out[axis === "x" ? "dx" : "dy"] = best.delta;
      out.locks[axis] = best.key;
    }
  }
  const final = { ...box, x: box.x + out.dx, y: box.y + out.dy };
  for (const axis of ["x", "y"] as const) {
    const c = chosen[axis];
    if (!c) continue;
    const other: Axis = axis === "x" ? "y" : "x";
    if (c.line !== undefined && c.peer) {
      const start = Math.min(final[other], c.peer[other]) - 12 / zoom,
        end =
          Math.max(
            final[other] + extent(final, other),
            c.peer[other] + extent(c.peer, other),
          ) +
          12 / zoom;
      out.lines.push(
        axis === "x"
          ? { x1: c.line, y1: start, x2: c.line, y2: end }
          : { x1: start, y1: c.line, x2: end, y2: c.line },
      );
    }
    if (c.segments) {
      const cross = final[other] + extent(final, other) / 2;
      out.gaps.push(
        ...c.segments.map(([start, end]) => ({ axis, start, end, cross })),
      );
    }
  }
  return out;
}
