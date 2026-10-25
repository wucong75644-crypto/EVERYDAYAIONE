import {keepManualPositions} from './manualLayout';
import {followSummaryPositions} from './summaryLayout';
import type { KnowledgeNode } from '../../services/knowledge';
import { branchIds, siblingCompare } from './branchLayout';
import type { CardSize } from './branchLayout';
/** Only move later peer branches downward; preserve hierarchy, ordering and existing free spacing. */
export function reflowContent(nodes: KnowledgeNode[], sizes: Map<string, CardSize>) {
  const next = nodes.map(n => ({ ...n })), index = new Map(next.map(n => [n.id, n]));
  const height = (n: KnowledgeNode) => sizes.get(n.id)?.height ?? 170;
  const children = new Map<string | null, KnowledgeNode[]>();
  for (const n of next.filter(n=>!n.canvas_data?.summary)) { const peers = children.get(n.parent_id) ?? []; peers.push(n); children.set(n.parent_id, peers); }
  const order: (string | null)[] = [null], visited = new Set<string>();
  for (let i = 0; i < order.length; i++) for (const n of children.get(order[i]) ?? []) if (!visited.has(n.id)) { visited.add(n.id); order.push(n.id); }
  for (const parent of order.reverse()) {
    const peers = (children.get(parent) ?? []).sort(siblingCompare);
    let floor = -Infinity;
    for (const n of peers) {
      const group = [...branchIds(next, n.id)].map(id => index.get(id)!);
      const top = Math.min(...group.map(n => n.y));
      const dy = Math.max(0, floor - top); if (dy) group.forEach(n => { n.y += dy; });
      floor = Math.max(...group.map(n => n.y + height(n))) + 32;
    }
  }
  return keepManualPositions(nodes,followSummaryPositions(nodes,next,sizes));
}
/** Reserve the complete translated branch, rather than only its first card. */
export function placeContent(source: KnowledgeNode[], occupied: KnowledgeNode[], sizes: Map<string, CardSize>, wanted: { x: number; y: number }) {
  const left = Math.min(...source.map(n => n.x)), top = Math.min(...source.map(n => n.y)); let y = wanted.y;
  const size = (n: KnowledgeNode) => sizes.get(n.id) ?? { width: 272, height: 170 };
  for (let attempt = 0; attempt <= occupied.length * source.length; attempt++) {
    let advance = y;
    for (const n of source) for (const other of occupied) {
      const x = n.x - left + wanted.x, ny = n.y - top + y, a = size(n), b = size(other);
      if (x < other.x + b.width + 24 && x + a.width + 24 > other.x && ny < other.y + b.height + 24 && ny + a.height + 24 > other.y) advance = Math.max(advance, other.y + b.height + 24 - (n.y - top));
    }
    if (advance === y) break; y = advance;
  }
  return { x: wanted.x, y };
}
