import type {CSSProperties} from "react";
import type {
  CanvasStyle,
  CanvasData,
  KnowledgeNode,
  MapTheme,
} from "../../services/knowledge";
import {topicWidth} from "./topicEditing";
import { defaultStyle, nodeStyle } from "./canvasStyle";
import { siblingCompare } from "./nodeOrder";
export const mapPalettes = {
  none: [],
  ocean: ["#2563eb", "#0891b2", "#7c3aed", "#0d9488"],
  forest: ["#15803d", "#0f766e", "#65a30d", "#a16207"],
  sunset: ["#c2410c", "#be123c", "#a21caf", "#b45309"],
  mono: ["#475569", "#64748b", "#334155", "#78716c"],
};
export const paletteNames = {
  none: "保留默认配色",
  ocean: "海洋",
  forest: "森林",
  sunset: "日落",
  mono: "简约灰",
};
export const defaultMapTheme: MapTheme = {
  palette: "none",
  background: "auto",
  grid: true,
  font: "system",
  font_size: 15,
  levels: [],
};
export function normalizeMapTheme(theme?: Partial<MapTheme>): MapTheme {
  const t = theme ?? {},
    fonts = ["system", "sans", "serif", "mono"],
    seen = new Set<number>();
  const levels = (Array.isArray(t.levels) ? t.levels : [])
    .filter((l) => {
      if (
        !l ||
        !Number.isInteger(l.depth) ||
        l.depth < 0 ||
        l.depth > 31 ||
        seen.has(l.depth) ||
        !l.style ||
        !["color", "background", "border"].every((k) =>
          /^#[0-9a-fA-F]{6}$/.test(String(l.style[k as "color"])),
        )
      )
        return false;
      seen.add(l.depth);
      return true;
    })
    .slice(0, 32);
  return {
    palette:
      t.palette && Object.prototype.hasOwnProperty.call(mapPalettes, t.palette)
        ? t.palette
        : "none",
    background:
      typeof t.background === "string" &&
      /^(auto|#[0-9a-fA-F]{6})$/.test(t.background)
        ? t.background
        : "auto",
    grid: typeof t.grid === "boolean" ? t.grid : true,
    font: t.font && fonts.includes(t.font) ? t.font : "system",
    font_size:
      Number.isInteger(t.font_size) && t.font_size! >= 10 && t.font_size! <= 48
        ? t.font_size!
        : 15,
    levels,
    ...(t.layout?{layout:{auto_balance:t.layout.auto_balance===true,compact:t.layout.compact===true,equal_width:t.layout.equal_width===true}}:{}),
  };
}
const graphs = new WeakMap<
  KnowledgeNode[],
  { index: Map<string, KnowledgeNode>; order: Map<string, number> }
>();
function graph(nodes: KnowledgeNode[]) {
  let value = graphs.get(nodes);
  if (value) return value;
  const groups = new Map<string, KnowledgeNode[]>(),
    order = new Map<string, number>();
  for (const n of nodes) {
    if (n.canvas_data?.summary) continue;
    const key = n.container_id + ":" + (n.parent_id ?? "");
    const group = groups.get(key) ?? [];
    group.push(n);
    groups.set(key, group);
  }
  for (const group of groups.values())
    group.sort(siblingCompare).forEach((n, i) => order.set(n.id, i));
  value = { index: new Map(nodes.map((n) => [n.id, n])), order };
  graphs.set(nodes, value);
  return value;
}
export function topicDepth(nodes: KnowledgeNode[], node: KnowledgeNode) {
  const index = graph(nodes).index,
    seen = new Set<string>();
  let depth = 0,
    current = node;
  // Summaries are independent roots in storage but follow their covered branch's visual depth.
  if (current.canvas_data?.summary) {
    const member = index.get(current.canvas_data.summary.members[0]);
    if (member) current = member;
  }
  while (current.parent_id && !seen.has(current.id)) {
    seen.add(current.id);
    const parent = index.get(current.parent_id);
    if (!parent || parent.container_id !== node.container_id) break;
    depth++;
    current = parent;
  }
  return depth;
}
export function mapBranchColor(
  nodes: KnowledgeNode[],
  node: KnowledgeNode,
  theme?: Partial<MapTheme>,
) {
  const colors = mapPalettes[theme?.palette ?? "none"];
  if (!colors?.length) return undefined;
  const index = graph(nodes).index,
    seen = new Set<string>();
  let branch = node;
  if (branch.canvas_data?.summary) {
    const member = index.get(branch.canvas_data.summary.members[0]);
    if (member) branch = member;
  }
  while (branch.parent_id && !seen.has(branch.id)) {
    seen.add(branch.id);
    const parent = index.get(branch.parent_id);
    if (!parent || !parent.parent_id) break;
    branch = parent;
  }
  return colors[(graph(nodes).order.get(branch.id) ?? 0) % colors.length];
}
function baseAppearance(
  node: KnowledgeNode,
  nodes: KnowledgeNode[],
  theme?: Partial<MapTheme>,
): CanvasData | undefined {
  if (!theme || !Object.keys(theme).length) return node.canvas_data;
  const t = normalizeMapTheme(theme),
    depth = topicDepth(nodes, node),
    color = mapBranchColor(nodes, node, t),
    level = t.levels.find((l) => l.depth === depth)?.style;
  const base: CanvasStyle = {
    ...defaultStyle,
    bold: true,
    font: t.font,
    font_size: t.font_size,
    ...(color
      ? {
          color: depth === 0 ? "#ffffff" : "#202938",
          background:
            depth === 0
              ? color
              : "#" +
                [1, 3, 5]
                  .map((i) =>
                    Math.round(
                      parseInt(color.slice(i, i + 2), 16) * 0.1 + 255 * 0.9,
                    )
                      .toString(16)
                      .padStart(2, "0"),
                  )
                  .join(""),
          border: color,
        }
      : {}),
    ...level,
    ...node.canvas_data?.style,
  };
  return { ...node.canvas_data, style: base };
}
const equalWidths=new WeakMap<KnowledgeNode[],WeakMap<object,Map<string,number>>>();
export function mapAppearance(node:KnowledgeNode,nodes:KnowledgeNode[],theme?:Partial<MapTheme>):CanvasData|undefined{
 const data=baseAppearance(node,nodes,theme);if(!theme?.layout?.equal_width)return data;
 let themes=equalWidths.get(nodes);if(!themes){themes=new WeakMap();equalWidths.set(nodes,themes);}let widths=themes.get(theme);
 if(!widths){widths=new Map();for(const n of nodes){const appearance=baseAppearance(n,nodes,theme),key=n.container_id+':'+topicDepth(nodes,n),width=topicWidth({...n,canvas_data:appearance});widths.set(key,Math.max(widths.get(key)??0,width));}themes.set(theme,widths);}
 return {...data,width:Math.max(widths.get(node.container_id+':'+topicDepth(nodes,node))??80,topicWidth({...node,canvas_data:data}))};
}
export function mapNodeStyle(
  node: KnowledgeNode,
  nodes: KnowledgeNode[],
  theme?: Partial<MapTheme>,
):CSSProperties {
  const data = mapAppearance(node, nodes, theme),
    style = {...nodeStyle(data),...(data?.height?{minHeight:Math.max(48,data.height)}:{}),width:topicWidth({...node,canvas_data:data})};
  if (
    theme &&
    Object.keys(theme).length &&
    (!theme.palette || theme.palette === "none") &&
    !node.canvas_data?.style &&
    !theme.levels?.some((l) => l.depth === topicDepth(nodes, node))
  )
    return {
      ...style,
      color: undefined,
      background: undefined,
      borderColor: undefined,
    };
  return style;
}
export function updateMapLevel(
  theme: MapTheme,
  depth: number,
  style: CanvasStyle,
): MapTheme {
  return {
    ...theme,
    levels: [
      ...theme.levels.filter((l) => l.depth !== depth),
      { depth, style: { ...style } },
    ].sort((a, b) => a.depth - b.depth),
  };
}
