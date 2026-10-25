import {physicalScope} from './summaryScope';
import {branchDirection} from './structureDirection';
import type { KnowledgeNode } from '../../services/knowledge';
import { branchIds, siblingCompare } from './branchLayout';
export function selectedGroups(nodes: KnowledgeNode[], selected: Set<string>) {
  const roots = nodes.filter(n => !n.canvas_data?.summary && selected.has(n.id) && !nodes.some(p => selected.has(p.id) && branchIds(nodes, p.id).has(n.id) && p.id !== n.id));
  const groups = new Map<string, KnowledgeNode[]>();
  for (const n of roots) { if (!n.parent_id) continue; const key = n.parent_id+':'+branchDirection(nodes,n.id); groups.set(key, [...(groups.get(key) ?? []), n]); }
  return [...groups.values()].map(group => group.sort(siblingCompare));
}
export function groupBounds(nodes: KnowledgeNode[], members: string[], sizes: Map<string, { width: number; height: number }>) {
  const ids = new Set(physicalScope(nodes,members)), content = nodes.filter(n => ids.has(n.id));
  if (!content.length) return null;
  const x = Math.min(...content.map(n => n.x)), y = Math.min(...content.map(n => n.y));
  return { x, y, right: Math.max(...content.map(n => n.x + (sizes.get(n.id)?.width ?? 272))), bottom: Math.max(...content.map(n => n.y + (sizes.get(n.id)?.height ?? 146))) };
}
