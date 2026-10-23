import type { KnowledgeNode } from '../../services/knowledge';
export type Placement = 'free' | 'child' | 'before' | 'after';
export type CardSize = { width: number; height: number };
export type BranchPlan = { nodes: KnowledgeNode[]; moving: Set<string>; changed: KnowledgeNode[]; target: string | null; placement: Placement; valid: boolean; message: string; marker?: { x: number; y: number; width: number } };
function childrenIndex(nodes: KnowledgeNode[]) {
  const children = new Map<string, string[]>();
  for (const n of nodes) if (n.parent_id) { const list = children.get(n.parent_id) ?? []; list.push(n.id); children.set(n.parent_id, list); }
  return children;
}
function collect(children: Map<string, string[]>, root: string) {
  const ids = new Set([root]); const queue = [root];
  for (let i = 0; i < queue.length; i++) for (const id of children.get(queue[i]) ?? []) if (!ids.has(id)) { ids.add(id); queue.push(id); }
  return ids;
}
export function branchIds(nodes: KnowledgeNode[], root: string) { return collect(childrenIndex(nodes), root); }
export function siblingCompare(a: KnowledgeNode, b: KnowledgeNode) { return (a.sort_order ?? a.y) - (b.sort_order ?? b.y) || a.id.localeCompare(b.id); }
export function planBranch(nodes: KnowledgeNode[], source: string, delta: { x: number; y: number }, cursor: { x: number; y: number }, sizes: Map<string, CardSize>, visible: Set<string>): BranchPlan {
  return planBranches(nodes, [source], delta, cursor, sizes, visible);
}
export function planBranches(nodes: KnowledgeNode[], sources: string[], delta: { x: number; y: number }, cursor: { x: number; y: number }, sizes: Map<string, CardSize>, visible: Set<string>): BranchPlan {
  const selected = new Set(sources); const children = childrenIndex(nodes);
  const moving = new Set(sources.flatMap(id => [...collect(children, id)]));
  const roots = nodes.filter(n => selected.has(n.id) && (!n.parent_id || !moving.has(n.parent_id))).sort(siblingCompare);
  if (!roots.length) return { nodes, moving, changed: [], target: null, placement: 'free', valid: false, message: '无法移动此分支' };
  const original = new Map(nodes.map(n => [n.id, n]));
  const root = roots[0];
  const size = (n: KnowledgeNode) => sizes.get(n.id) ?? { width: 272, height: 170 };
  const next = nodes.map(n => moving.has(n.id) ? { ...n, x: n.x + delta.x, y: n.y + delta.y } : { ...n });
  const index = new Map(next.map(n => [n.id, n]));
  let target: KnowledgeNode | undefined;
  for (const n of nodes) {
    if (sources.includes(n.id) || !visible.has(n.id)) continue;
    const r = size(n);
    if (cursor.x >= n.x - 16 && cursor.x <= n.x + r.width + 16 && cursor.y >= n.y - 16 && cursor.y <= n.y + r.height + 16) { target = n; break; }
  }
  let placement: Placement = 'free'; let valid = roots.every(n => n.container_id === root.container_id); let message = roots.length > 1 ? `松手移动 ${roots.length} 条分支` : '松手移动整条分支';
  let marker: BranchPlan['marker'];
  if (target) {
    const r = size(target); const offset = cursor.y - target.y;
    placement = offset < Math.min(28, r.height * .25) ? 'before' : offset > r.height - Math.min(28, r.height * .25) ? 'after' : 'child';
    valid = !moving.has(target.id) && roots.every(n => n.container_id === root.container_id) && target.container_id === root.container_id;
    message = !valid ? '不能移入自身后代或其他空间' : placement === 'child' ? `移入「${target.title}」` : `插入「${target.title}」${placement === 'before' ? '之前' : '之后'}`;
    if (placement !== 'child') marker = { x: target.x - 8, y: placement === 'before' ? target.y - 12 : target.y + r.height + 12, width: r.width + 16 };
    if (valid) {
      const parent = placement === 'child' ? target.id : target.parent_id;
      const siblings = nodes.filter(n => n.container_id === root.container_id && n.parent_id === parent && !moving.has(n.id)).sort(siblingCompare);
      const at = placement === 'child' ? siblings.length : siblings.findIndex(n => n.id === target!.id) + (placement === 'after' ? 1 : 0);
      siblings.splice(at, 0, ...roots);
      for (const root of roots) index.get(root.id)!.parent_id = parent;
      const shift = (ids: Set<string>, dx: number, dy: number) => { for (const id of ids) { const n = index.get(id)!; n.x += dx; n.y += dy; } };
      const bounds = (ids: Set<string>) => { const group = [...ids].map(id => index.get(id)!); return { top: Math.min(...group.map(n => n.y)), bottom: Math.max(...group.map(n => n.y + size(n).height)) }; };
      const current = index.get(root.id)!;
      shift(moving, (placement === 'child' ? target.x + 330 : target.x) - current.x, target.y - current.y);
      const children = childrenIndex(next);
      let floor = Math.min(...siblings.map(n => index.get(n.id)!.y));
      for (const [i, sibling] of siblings.entries()) {
        const ids = collect(children, sibling.id); const box = bounds(ids);
        if (box.top < floor) shift(ids, 0, floor - box.top);
        index.get(sibling.id)!.sort_order = (i + 1) * 1024;
        floor = bounds(ids).bottom + 32;
      }
      // Propagate occupied height through ancestors, shifting complete peer trees.
      let ancestor = parent; const visited = new Set<string>();
      while (ancestor && !visited.has(ancestor)) {
        visited.add(ancestor);
        const owner = index.get(ancestor); if (!owner) break;
        const peers = next.filter(n => n.container_id === root.container_id && n.parent_id === owner.parent_id).sort(siblingCompare);
        let bottom = -Infinity;
        for (const peer of peers) {
          const ids = collect(children, peer.id); const box = bounds(ids);
          if (box.top < bottom) shift(ids, 0, bottom - box.top);
          bottom = bounds(ids).bottom + 32;
        }
        ancestor = owner.parent_id;
      }
    }
  }
  return { nodes: next, moving, target: target?.id ?? null, placement, valid, message, marker,
    changed: next.filter(n => moving.has(n.id) || n.x !== original.get(n.id)!.x || n.y !== original.get(n.id)!.y) };
}
