import type { Direction } from "./structureDirection";
import { vertical, vectors } from "./structureDirection";
export type GroupBounds = {
  x: number;
  y: number;
  right: number;
  bottom: number;
};
export function summaryPosition(
  b: GroupBounds,
  d: Direction,
  size = { width: 272, height: 170 },
) {
  return {
    x:
      d === "right"
        ? b.right + 120
        : d === "left"
          ? b.x - 120 - size.width
          : (b.x + b.right - size.width) / 2,
    y:
      d === "down"
        ? b.bottom + 120
        : d === "up"
          ? b.y - 120 - size.height
          : (b.y + b.bottom - size.height) / 2,
  };
}
export function rangePoint(
  b: GroupBounds,
  d: Direction,
  edge: number,
  kind: "summary" | "boundary",
) {
  const verticalFlow = vertical(d),
    v = vectors[d],
    gap = kind === "summary" ? 48 : 20;
  return verticalFlow
    ? {
        x: edge
          ? b.right + (kind === "boundary" ? 20 : 0)
          : b.x - (kind === "boundary" ? 20 : 0),
        y: (d === "down" ? b.bottom : b.y) + v.y * gap,
      }
    : {
        x: (d === "right" ? b.right : b.x) + v.x * gap,
        y: edge
          ? b.bottom + (kind === "boundary" ? 20 : 0)
          : b.y - (kind === "boundary" ? 30 : 0),
      };
}
export function summaryPath(
  b: GroupBounds,
  d: Direction,
  n: { x: number; y: number; width: number; height: number },
) {
  const isVertical = vertical(d),
    span = isVertical ? b.right - b.x : b.bottom - b.y,
    v = vectors[d],
    bend = Math.min(24, span / 4),
    mid = span / 2;
  const point = (u: number, w: number) =>
    isVertical
      ? `${b.x + w} ${(d === "down" ? b.bottom : b.y) + v.y * (24 + u)}`
      : `${(d === "right" ? b.right : b.x) + v.x * (24 + u)} ${b.y + w}`;
  const end = isVertical
    ? `${n.x + n.width / 2} ${d === "down" ? n.y : n.y + n.height}`
    : `${d === "right" ? n.x : n.x + n.width} ${n.y + n.height / 2}`;
  return `M ${point(0, 0)} Q ${point(24, 0)} ${point(24, bend)} L ${point(24, mid - bend)} Q ${point(24, mid)} ${point(40, mid)} Q ${point(24, mid)} ${point(24, mid + bend)} L ${point(24, span - bend)} Q ${point(24, span)} ${point(0, span)} M ${point(40, mid)} L ${end}`;
}

export function placeSummary(
  b: GroupBounds,
  d: Direction,
  occupied: { x: number; y: number; width: number; height: number }[],
  size = { width: 272, height: 170 },
) {
  const p = summaryPosition(b, d, size);
  for (let attempt = 0; attempt <= occupied.length; attempt++) {
    const hit = occupied.find(
      (o) =>
        p.x < o.x + o.width + 24 &&
        p.x + size.width + 24 > o.x &&
        p.y < o.y + o.height + 24 &&
        p.y + size.height + 24 > o.y,
    );
    if (!hit) break;
    if (d === "right") p.x = hit.x + hit.width + 24;
    else if (d === "left") p.x = hit.x - size.width - 24;
    else if (d === "down") p.y = hit.y + hit.height + 24;
    else p.y = hit.y - size.height - 24;
  }
  return p;
}
