import type { KnowledgeNode } from '../../services/knowledge';
import { siblingCompare } from './branchLayout';
export function navigateNode(nodes: KnowledgeNode[], id: string | null, key: string, folded: Set<string>, container?: string): { id: string | null; expand?: string; collapse?: string } {
  const node = nodes.find(n => n.id === id);
  if (!node) return { id: nodes.filter(n => !n.parent_id && (!container || n.container_id === container)).sort(siblingCompare)[0]?.id ?? null };
  const children = nodes.filter(n => n.parent_id === id).sort(siblingCompare);
  if (key === 'ArrowRight') return folded.has(node.id) && children.length ? { id: node.id, expand: node.id } : { id: children[0]?.id ?? node.id };
  if (key === 'ArrowLeft') return children.length && !folded.has(node.id) ? { id: node.id, collapse: node.id } : { id: node.parent_id ?? node.id };
  const siblings = nodes.filter(n => n.container_id === node.container_id && n.parent_id === node.parent_id).sort(siblingCompare);
  const at = siblings.findIndex(n => n.id === id); return { id: siblings[at + (key === 'ArrowUp' ? -1 : 1)]?.id ?? node.id };
}
export function revealNode(view: { x: number; y: number; z: number }, node: { x: number; y: number }, card: { width: number; height: number }, viewport: { width: number; height: number }) {
  const axis = (offset: number, start: number, size: number, length: number, leading: number, trailing: number) => {
    const a = start * view.z + offset, b = a + size * view.z;
    if (size * view.z > length - leading - trailing) return leading - start * view.z;
    return a < leading ? offset + leading - a : b > length - trailing ? offset + length - trailing - b : offset;
  };
  return { ...view, x: axis(view.x, node.x, card.width, viewport.width, 32, 40), y: axis(view.y, node.y, card.height, viewport.height, 64, 64) };
}
