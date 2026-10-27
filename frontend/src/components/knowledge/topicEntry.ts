import {richFont} from './richTitle';
import {mapAppearance} from './mapTheme';
import type {MapTheme,TitleFormat,MapLayout} from '../../services/knowledge';
import {topicWidth,titleHeight} from './topicEditing';
import type { KnowledgeNode } from "../../services/knowledge";
import type { CardSize } from "./branchLayout";
import { branchIds } from "./branchLayout";
import {
  arrangeStructure,
  structureMoves,
  structureRoot,
} from "./topicStructure";
import { effectiveStructure } from "./structureDirection";
export type OutlineTopic = { title: string; parent_index: number; structure?: import("./topicStructure").TopicStructure };
function entryRoot(nodes: KnowledgeNode[], id: string,options:Partial<MapLayout>={}) {
  const structured = structureRoot(nodes, id,options);
  if (structured) return structured.id;
  const index = new Map(nodes.map((n) => [n.id, n])),
    seen = new Set<string>();
  let root = id;
  while (index.get(root)?.parent_id && !seen.has(root)) {
    seen.add(root);
    root = index.get(root)!.parent_id!;
  }
  return root;
}
/** Tabs or a consistent space indent express hierarchy; never silently truncate a paste. */
export function parseTopicOutline(text: string): OutlineTopic[] {
  if (text.length > 100000) throw new Error("文字过长，请分批粘贴");
  const lines = text
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .filter((line) => line.trim());
  if (lines.length > 200)
    throw new Error("单次最多创建 200 个主题，请分批粘贴");
  if (!lines.length) return [];
  const rows = lines.map((line) => ({
    title: line.trim(),
    indent: (line.match(/^[\t ]*/)?.[0] ?? "").replace(/\t/g, "  ").length,
  }));
  if (rows.some((r) => r.title.length > 200))
    throw new Error("每个主题最多 200 字，请调整后再粘贴");
  const baseline = Math.min(...rows.map((r) => r.indent)),
    steps = rows.map((r) => r.indent - baseline).filter((n) => n > 0),
    step = Math.min(...steps),
    stack: number[] = [],
    out: OutlineTopic[] = [];
  for (const r of rows) {
    const depth = steps.length ? (r.indent - baseline) / step : 0;
    if (!Number.isInteger(depth) || depth > stack.length)
      throw new Error("缩进层级不连续，请逐级缩进后再粘贴");
    out.push({ title: r.title, parent_index: depth ? stack[depth - 1] : -1 });
    stack[depth] = out.length - 1;
    stack.length = depth + 1;
  }
  return out;
}
export function planTopicOutline(
  topics: OutlineTopic[],
  nodes: KnowledgeNode[],
  container: string,
  target: KnowledgeNode | undefined,
  point: { x: number; y: number },
  sizes: Map<string, CardSize>,
  fontSize = 15,
  theme?:Partial<MapTheme>,
) {
  const last = Math.max(
    0,
    ...nodes
      .filter((n) => n.parent_id === (target?.id ?? null))
      .map((n) => n.sort_order ?? n.y),
  );
  let ordinal = 0;
  const virtual = topics.map((t, i): KnowledgeNode => ({
    id: "entry:" + i,
    node_id: "entry:" + i,
    container_id: container,
    parent_id:
      t.parent_index < 0 ? (target?.id ?? null) : "entry:" + t.parent_index,
    kind: "topic",
    canvas_data: t.structure?{structure:t.structure}:undefined,
    title: t.title,
    revision: 1,
    layout_revision: 1,
    x: point.x,
    y: point.y,
    sort_order: t.parent_index < 0 ? last + ++ordinal * 1024 : (i + 1) * 1024,
  }));
  const measured = new Map(sizes);
  for (const n of virtual)
    measured.set(n.id, {
      width: topicWidth(n,mapAppearance(n,[...nodes,...virtual],theme)?.style?.font_size??fontSize),
      height: Math.max(146,78+titleHeight(n.title,topicWidth(n,mapAppearance(n,[...nodes,...virtual],theme)?.style?.font_size??fontSize),mapAppearance(n,[...nodes,...virtual],theme)?.style?.font_size??fontSize)),
    });
  let arranged = [...nodes, ...virtual];
  if (target)
    arranged = arrangeStructure(
      arranged,
      entryRoot(arranged, target.id,theme?.layout),
      measured,theme?.layout,theme,
    );
  else {
    let cursor = point.y;
    for (const root of virtual.filter((n) => !n.parent_id)) {
      arranged = arrangeStructure(arranged, root.id, measured,theme?.layout,theme);
      const ids = branchIds(arranged, root.id),
        group = arranged.filter((n) => ids.has(n.id)),
        top = Math.min(...group.map((n) => n.y)),
        bottom = Math.max(
          ...group.map((n) => n.y + measured.get(n.id)!.height),
        );
      arranged = arranged.map((n) =>
        ids.has(n.id) ? { ...n, y: n.y + cursor - top } : n,
      );
      cursor += bottom - top + 48;
    }
  }
  const index = new Map(arranged.map((n) => [n.id, n]));
  return {
    topics: topics.map((t, i) => ({
      ...t,
      position: {
        x: index.get("entry:" + i)!.x,
        y: index.get("entry:" + i)!.y,
      },
    })),
    nodes: structureMoves(nodes, arranged),
    virtual: arranged.filter((n) => n.id.startsWith("entry:")),
    sizes: measured,
  };
}
/** Replace the selected branch's place in every group without changing its content identity. */
export function planParentTopic(
  nodes: KnowledgeNode[],
  target: KnowledgeNode,
  title: string,
  sizes: Map<string, CardSize>,
  fontSize = 15,
  theme?:Partial<MapTheme>,
  titleFormat?:TitleFormat,
) {
  const id = "parent-draft",
    data: NonNullable<KnowledgeNode["canvas_data"]> = {
      ...(titleFormat?{title_format:titleFormat}:{}),
      ...(target.canvas_data?.boundaries?{boundaries:target.canvas_data.boundaries}:{}),
      ...(target.parent_id
        ? {}
        : {
            structure:
              target.canvas_data?.structure ??
              effectiveStructure(nodes, target.id),
          }),
      ...(target.canvas_data?.summary
        ? { summary: target.canvas_data.summary }
        : {}),
      ...(target.canvas_data?.boundary
        ? { boundary: target.canvas_data.boundary }
        : {}),
    };
  let next: KnowledgeNode[] = nodes.map((n) => {
    const d = { ...n.canvas_data };
    for (const key of ["summary", "boundary"] as const) {
      const g = d[key];
      if (g)
        d[key] = {
          ...g,
          members: g.members.map((member) =>
            member === target.id ? id : member,
          ),
        };
    }
    if (n.id === target.id) {
      delete d.summary;
      delete d.boundary;
      delete d.boundaries;
    }
    if(d.boundaries)d.boundaries=d.boundaries.map(g=>({...g,members:g.members.map(member=>member===target.id?id:member)}));
    return {
      ...n,
      parent_id: n.id === target.id ? id : n.parent_id,
      canvas_data: d,
    };
  });
  const parent: KnowledgeNode = {
    ...target,
    id,
    node_id: id,
    title,
    kind: "topic",
    revision: 1,
    layout_revision: 1,
    canvas_data: data,
  };
  for (const key of ["summary", "boundary"] as const) {
    const g = parent.canvas_data?.[key];
    if (g)
      parent.canvas_data![key] = {
        ...g,
        members: g.members.map((member) =>
          member === target.id ? id : member,
        ),
      };
  }
  if(parent.canvas_data?.boundaries)parent.canvas_data.boundaries=parent.canvas_data.boundaries.map(g=>({...g,members:g.members.map(member=>member===target.id?id:member)}));
  next = [...next, parent];
  const measured = new Map(sizes);
  measured.set(id, {
    width: topicWidth(parent,mapAppearance(parent,next,theme)?.style?.font_size??fontSize),
    height: Math.max(146,78+titleHeight(title,topicWidth(parent,mapAppearance(parent,next,theme)?.style?.font_size??fontSize),richFont(titleFormat,mapAppearance(parent,next,theme)?.style?.font_size??fontSize))),
  });
  let root = structureRoot(next, id,theme?.layout)?.id ?? id;
  if (!structureRoot(next, id,theme?.layout)) {
    const index = new Map(next.map((n) => [n.id, n])),
      seen = new Set<string>();
    while (index.get(root)?.parent_id && !seen.has(root)) {
      seen.add(root);
      root = index.get(root)!.parent_id!;
    }
  }
  const arranged = arrangeStructure(next, root, measured,theme?.layout,theme),
    n = arranged.find((n) => n.id === id)!;
  return {
    position: { x: n.x, y: n.y },
    structure: parent.canvas_data?.structure,
    nodes: structureMoves(nodes, arranged),
  };
}
