import { planTopicOutline } from "../topicEntry";
import { describe, it, expect } from "vitest";
import {
  defaultMapTheme,
  normalizeMapTheme,
  mapAppearance,
  mapNodeStyle,
  mapBranchColor,
  topicDepth,
  updateMapLevel,
} from "../mapTheme";
import { defaultStyle } from "../canvasStyle";
import type { KnowledgeNode } from "../../../services/knowledge";
const n = (
  id: string,
  parent_id: string | null,
  sort_order = 1,
): KnowledgeNode => ({
  id,
  node_id: id,
  container_id: "p",
  parent_id,
  sort_order,
  title: id,
  kind: "topic",
  revision: 1,
  layout_revision: 1,
  x: 0,
  y: 0,
});
const nodes = [n("r", null), n("a", "r"), n("b", "r", 2), n("child", "a")];
describe("map theme inheritance", () => {
  it("preserves legacy appearance while applying content-sized width", () => {
    expect(mapAppearance(nodes[0], nodes, {})).toBeUndefined();
    expect(mapNodeStyle(nodes[0], nodes, {})).toEqual({width:80});
  });
  it("inherits branch colors and gives siblings different palette colors", () => {
    const t = { ...defaultMapTheme, palette: "ocean" as const };
    expect(mapBranchColor(nodes, nodes[1], t)).toBe(
      mapBranchColor(nodes, nodes[3], t),
    );
    expect(mapBranchColor(nodes, nodes[1], t)).not.toBe(
      mapBranchColor(nodes, nodes[2], t),
    );
    expect(mapAppearance(nodes[0], nodes, t)?.style?.color).toBe("#ffffff");
    expect(mapAppearance(nodes[1], nodes, t)?.style?.background).toMatch(
      /^#[0-9a-f]{6}$/,
    );
  });
  it("prioritizes explicit local styles over levels and global defaults", () => {
    const custom = {
        ...nodes[1],
        canvas_data: {
          style: { ...defaultStyle, font: "mono" as const, font_size: 30 },
        },
      },
      theme = updateMapLevel({ ...defaultMapTheme, font: "serif" }, 1, {
        ...defaultStyle,
        font_size: 20,
      });
    expect(mapAppearance(custom, nodes, theme)?.style?.font_size).toBe(30);
    expect(mapAppearance(custom, nodes, theme)?.style?.font).toBe("mono");
    expect(mapAppearance(nodes[2], nodes, theme)?.style?.font_size).toBe(20);
  });
  it("lets a newly created topic inherit its level without storing an override", () => {
    const t = updateMapLevel(defaultMapTheme, 2, {
        ...defaultStyle,
        font_size: 28,
      }),
      newNode = n("new", "a");
    expect(
      mapAppearance(newNode, [...nodes, newNode], t)?.style?.font_size,
    ).toBe(28);
    expect(newNode.canvas_data).toBeUndefined();
  });
  it("applies font-only changes without replacing default card fills", () => {
    const t = { ...defaultMapTheme, font: "serif" as const };
    expect(mapNodeStyle(nodes[0], nodes, t).background).toBeUndefined();
    expect(mapAppearance(nodes[0], nodes, t)?.style?.font).toBe("serif");
  });
  it("uses visual depth for summaries and terminates broken ancestry", () => {
    const summary = {
      ...n("s", null),
      canvas_data: { summary: { title: "概要", members: ["a", "b"] } },
    };
    expect(topicDepth([...nodes, summary], summary)).toBe(1);
    expect(
      topicDepth([n("a", "b"), n("b", "a")], n("a", "b")),
    ).toBeLessThanOrEqual(2);
  });
  it("replaces a level rule without mutating the original rules", () => {
    const old = updateMapLevel(defaultMapTheme, 1, {
        ...defaultStyle,
        font_size: 20,
      }),
      next = updateMapLevel(old, 1, { ...defaultStyle, font_size: 30 });
    expect(next.levels).toHaveLength(1);
    expect(old.levels[0].style.font_size).toBe(20);
  });
  it("normalizes malformed cached values without interpreting CSS strings", () => {
    const t = normalizeMapTheme({
      palette: "bad",
      background: "url(example)",
      font: "bad",
      font_size: 100,
      levels: null,
    } as never);
    expect(t).toEqual(defaultMapTheme);expect(normalizeMapTheme({palette:"toString"}as never).palette).toBe("none");
  });
});

it("reserves the inherited level font in batch layout planning", () => {
  const t = updateMapLevel(defaultMapTheme, 1, {
    ...defaultStyle,
    font_size: 48,
  });
  const plan = planTopicOutline(
    [
      { title: "长标题".repeat(12), parent_index: -1 },
      { title: "另一主题", parent_index: -1 },
    ],
    nodes,
    "p",
    nodes[0],
    { x: 0, y: 0 },
    new Map(),
    15,
    t,
  );
  const a = plan.virtual[0],
    b = plan.virtual[1];
  expect(plan.sizes.get(a.id)!.height).toBeGreaterThan(300);
  expect(Math.abs(a.y - b.y)).toBeGreaterThan(170);
});
