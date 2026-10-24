import {summaryParent} from './summaryScope';
import {boundaryEntries} from './canvasGroups';
import type { KnowledgeNode } from "../../services/knowledge";
import type { TopicStructure } from "./topicStructure";
import { siblingCompare } from "./nodeOrder";
export type Direction = "right" | "left" | "down" | "up";
export const vectors: Record<Direction, { x: number; y: number }> = {
  right: { x: 1, y: 0 },
  left: { x: -1, y: 0 },
  down: { x: 0, y: 1 },
  up: { x: 0, y: -1 },
};
export const opposite: Record<Direction, Direction> = {
  right: "left",
  left: "right",
  down: "up",
  up: "down",
};
export const arrowDirection: Record<string, Direction> = {
  ArrowRight: "right",
  ArrowLeft: "left",
  ArrowDown: "down",
  ArrowUp: "up",
};
export const vertical = (d: Direction) => d === "down" || d === "up";
/** A summary/boundary is indivisible when a right tree becomes a balanced map. */
export function siblingGroups(nodes: KnowledgeNode[], parent: string) {
  const peers = nodes
      .filter((n) => n.parent_id === parent && !n.canvas_data?.summary)
      .sort(siblingCompare),
    leaders = new Map(peers.map((n, i) => [n.id, i]));
  const find = (i: number): number => {
    const id = peers[i]?.id;
    if (!id) return i;
    const next = leaders.get(id)!;
    return next === i ? i : find(next);
  };
  for (const n of nodes)
    for (const g of [n.canvas_data?.summary, ...boundaryEntries(n.canvas_data).map(b=>b.group)]) {
      const indices = (g?.members ?? [])
        .map((id) => peers.findIndex((n) => n.id === id))
        .filter((i) => i >= 0);
      if (!indices.length) continue;
      const range=Array.from({length:Math.max(...indices)-Math.min(...indices)+1},(_,i)=>Math.min(...indices)+i);
      const head = Math.min(...range.map(find));
      for (const i of range) leaders.set(peers[find(i)].id, head);
    }
  const groups=new Map<number,KnowledgeNode[]>();for(let i=0;i<peers.length;i++){const key=find(i);groups.set(key,[...(groups.get(key)??[]),peers[i]]);}return [...groups.values()];
}
export function balancedSides(nodes:KnowledgeNode[],parent:string,weights?:Map<string,number>){
 const groups=siblingGroups(nodes,parent),result=new Map<string,'left'|'right'>();
 const root=nodes.find(n=>n.id===parent),placed=!!root&&groups.some(g=>g[0].x<root.x)&&groups.some(g=>g[0].x>root.x);
 let left=0,right=0,index=0;
 for(const group of groups){
  const height=group.reduce((sum,n)=>sum+(weights?.get(n.id)??0),0);
  const side=weights?(right<=left?'right':'left'):placed?(group[0].x<root!.x?'left':'right'):index%2===0?'right':'left';
  for(const n of group)result.set(n.id,side);if(side==='right')right+=height;else left+=height;index+=group.length;
 }
 return result;
}

export function effectiveStructure(
  nodes: KnowledgeNode[],
  id: string,
): TopicStructure {
  const index = new Map(nodes.map((n) => [n.id, n])),
    lineage: KnowledgeNode[] = [];
  let n = index.get(id);
  const seen = new Set<string>();
  while (n && !seen.has(n.id)) {
    seen.add(n.id);
    lineage.unshift(n);
    const parent=summaryParent(nodes,n);n=parent?index.get(parent):undefined;
  }
  let kind: TopicStructure = "right";
  for (const n of lineage) {
    if (n.canvas_data?.structure) kind = n.canvas_data.structure;
    else if (kind === "balanced")
      kind =
        balancedSides(nodes, n.parent_id ?? "").get(
          n.canvas_data?.summary?.members[0] ?? n.id,
        ) ?? "right";
  }
  return kind;
}
/** Direction of the edge INTO this branch; its own override only affects its children. */
export function branchDirection(nodes: KnowledgeNode[], id: string): Direction {
  const n = nodes.find((n) => n.id === id);
  const kind = effectiveStructure(nodes, n?summaryParent(nodes,n)??id:id);
  if (kind !== "balanced") return kind;
  return (
    balancedSides(nodes, n?summaryParent(nodes,n)??id:id).get(
      n?.canvas_data?.summary?.members[0] ?? id,
    ) ?? "right"
  );
}
export function childDirection(nodes: KnowledgeNode[], id: string): Direction {
  const kind = effectiveStructure(nodes, id);
  return kind === "balanced"
    ? nodes.filter((n) => n.parent_id === id && !n.canvas_data?.summary)
        .length %
        2 ===
      0
      ? "right"
      : "left"
    : kind;
}
export function portDirection(
  nodes: KnowledgeNode[],
  id: string,
  port: "child" | "sibling",
): Direction {
  return port === "child"
    ? childDirection(nodes, id)
    : vertical(branchDirection(nodes, id))
      ? "right"
      : "down";
}
export function edgePoint(
  size: { width: number; height: number },
  direction: Direction,
  gap = 0,
) {
  const v = vectors[direction];
  return {
    x: size.width / 2 + v.x * (size.width / 2 + gap),
    y: size.height / 2 + v.y * (size.height / 2 + gap),
  };
}
export function portStyle(
  nodes: KnowledgeNode[],
  id: string,
  port: "child" | "sibling",
  radius: number,
  gap: number,
) {
  const direction = portDirection(nodes, id, port),
    v = vectors[direction];
  return {
    left: `calc(${50 + v.x * 50}% + ${v.x * gap - radius}px)`,
    top: `calc(${50 + v.y * 50}% + ${v.y * gap - radius}px)`,
    right: "auto",
    bottom: "auto",
  };
}
export function foldStyle(nodes: KnowledgeNode[], id: string) {
  const direction = childDirection(nodes, id),
    v = vectors[direction];
  return {
    left: `calc(${50 + v.x * 50}% - 8px)`,
    top: `calc(${50 + v.y * 50}% - 8px)`,
    right: "auto",
    bottom: "auto",
  };
}

/** Keep the floating actions off the directional creation/folding ports. */
export function toolsPosition(
  card: { x: number; y: number; width: number; height: number },
  area: { width: number; height: number; top?:number },
  ports: { x: number; y: number; radius: number }[],
  measured = {width:250,height:44},
  obstacles: {x:number;y:number;width:number;height:number}[] = [],
) {
  const width = measured.width,
    height = measured.height,
    mid = card.y + (card.height - height) / 2;
  const candidates = [
    { left: card.x, top: card.y - height - 14 },
    { left: card.x, top: card.y + card.height + 14 },
    { left: card.x - width - 14, top: mid },
    { left: card.x + card.width + 14, top: mid },
    { left: card.x, top: card.y - height - 52 },
    { left: card.x, top: card.y + card.height + 52 },
  ];
  // Include obstacle edges so a nearby branch does not force an overlapping fallback.
  for(const obstacle of obstacles){
    candidates.push({left:obstacle.x,top:obstacle.y-height-14},{left:obstacle.x,top:obstacle.y+obstacle.height+14});
  }
  const fits = (p: { left: number; top: number }) =>
    p.left >= 12 &&
    p.left + width <= area.width - 12 &&
    p.top >= Math.max(12,area.top??100) &&
    p.top + height <= area.height - 16 &&
    ![card,...obstacles].some(r=>p.left<r.x+r.width+4&&p.left+width>r.x-4&&p.top<r.y+r.height+4&&p.top+height>r.y-4) &&
    !ports.some(
      (h) =>
        p.left < h.x + h.radius + 4 &&
        p.left + width > h.x - h.radius - 4 &&
        p.top < h.y + h.radius + 4 &&
        p.top + height > h.y - h.radius - 4,
    );
  return (
    candidates.find(fits) ?? {
      left: Math.max(12, Math.min(area.width - width - 12, card.x)),
      top: Math.max(
        Math.max(12,area.top??100),
        Math.min(area.height - height - 16, card.y - height - 52),
      ),
    }
  );
}
