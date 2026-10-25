import type { KnowledgeScene } from '../../services/knowledge';
import { siblingCompare } from './branchLayout';
export function historyTargets(before: KnowledgeScene, after: KnowledgeScene, container?: string) {
  const old = new Map(before.nodes.map(n => [n.id, n]));
  const changed = after.nodes.filter(n => { const p = old.get(n.id); return !p || p.title !== n.title || p.parent_id !== n.parent_id || p.sort_order !== n.sort_order || p.x !== n.x || p.y !== n.y || JSON.stringify(p.canvas_data ?? {}) !== JSON.stringify(n.canvas_data ?? {}); });
  for (const removed of before.nodes.filter(n => !after.nodes.some(p => p.id === n.id))) {
    const members = new Set(removed.canvas_data?.summary?.members ?? []);
    changed.push(...after.nodes.filter(n => members.has(n.id)));
    let p = removed.parent_id; const seen = new Set<string>();
    while (p && !seen.has(p)) { seen.add(p); const survivor = after.nodes.find(n => n.id === p); if (survivor) { changed.push(survivor); break; } p = old.get(p)?.parent_id ?? null; }
  }
  const endpoints = new Set([...before.relations, ...after.relations].filter(r => !before.relations.some(b => b.id === r.id && b.source_object_id === r.source_object_id && b.target_object_id === r.target_object_id && JSON.stringify(b.appearance??{})===JSON.stringify(r.appearance??{})) || !after.relations.some(a => a.id === r.id && a.source_object_id === r.source_object_id && a.target_object_id === r.target_object_id && JSON.stringify(a.appearance??{})===JSON.stringify(r.appearance??{}))).flatMap(r => [r.source_object_id, r.target_object_id]));
  changed.push(...after.nodes.filter(n => endpoints.has(n.id)));
  const related = [...new Map(changed.map(n => [n.id, n])).values()].filter(n => !container || n.container_id === container);
  if (!related.length) related.push(...after.nodes.filter(n => n.container_id === container && !n.parent_id).sort(siblingCompare).slice(0, 1));
  const ids = new Set(related.map(n => n.id));
  return related.filter(n => endpoints.has(n.id) || !n.parent_id || !ids.has(n.parent_id));
}
