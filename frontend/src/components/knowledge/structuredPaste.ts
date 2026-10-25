import type { KnowledgeNode,MapLayout,MapTheme } from "../../services/knowledge";
import type { CardSize } from "./branchLayout";
import { siblingCompare } from "./nodeOrder";
import {
  arrangeStructure,
  structureMoves,
  structureRoot,
} from "./topicStructure";
export function planStructuredPaste(
  source: KnowledgeNode[],
  occupied: KnowledgeNode[],
  target: KnowledgeNode,
  sizes: Map<string, CardSize>,
  cut = false,options:Partial<MapLayout>={},theme?:Partial<MapTheme>,
) {
  const aliases = new Map(
      source.map((n) => [n.id, cut ? n.id : "paste:" + n.id]),
    ),
    roots = source
      .filter((n) => !aliases.has(n.parent_id ?? ""))
      .sort(siblingCompare);
  const last = Math.max(
    0,
    ...occupied
      .filter((n) => n.parent_id === target.id)
      .map((n) => n.sort_order ?? n.y),
  );
  const virtual = source.map((n) => {
    const data = structuredClone(n.canvas_data ?? {});
    // Pasting is an explicit new placement; source coordinates must not pin the copy.
    delete data.manual_layout;
    for (const key of ["summary", "boundary"] as const) {
      const g = data[key];
      if (g) {
        if (g.members.every((id) => aliases.has(id)))
          g.members = g.members.map((id) => aliases.get(id)!);
        else delete data[key];
      }
    }
    if(data.boundaries)data.boundaries=data.boundaries.filter(g=>g.members.every(id=>aliases.has(id))).map(g=>({...g,members:g.members.map(id=>aliases.get(id)!)}));
    return {
      ...n,
      id: aliases.get(n.id)!,
      parent_id: aliases.get(n.parent_id ?? "") ?? target.id,
      canvas_data: data,
      ...(roots.some((r) => r.id === n.id)
        ? {
            sort_order:
              last + (roots.findIndex((r) => r.id === n.id) + 1) * 1024,
          }
        : {}),
    };
  });
  const measured = new Map(sizes);
  for (const n of source)
    measured.set(
      aliases.get(n.id)!,
      sizes.get(n.id) ?? { width: 272, height: 170 },
    );
  let arranged = [...occupied, ...virtual];
  if (cut)
    for (const n of roots) {
      const old = structureRoot([...occupied, ...source], n.parent_id ?? "",options);
      if (old && occupied.some((n) => n.id === old.id))
        arranged = arrangeStructure(arranged, old.id, measured,options,theme);
    }
  arranged = arrangeStructure(
    arranged,
    structureRoot(arranged, target.id,options)?.id ?? target.id,
    measured,options,theme,
  );
  const index = new Map(arranged.map((n) => [n.id, n]));
  return {
    positions: source.map((n) => ({
      source_object_id: n.id,
      position: {
        x: index.get(aliases.get(n.id)!)!.x,
        y: index.get(aliases.get(n.id)!)!.y,
      },
    })),
    nodes: structureMoves(occupied, arranged),
    sourceNodes: source.map((n) => ({
      node_id: n.node_id,
      expected_revision: n.layout_revision,
      position: {
        x: index.get(aliases.get(n.id)!)!.x,
        y: index.get(aliases.get(n.id)!)!.y,
      },
    })),
  };
}
