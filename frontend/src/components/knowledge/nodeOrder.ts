import type { KnowledgeNode } from "../../services/knowledge";
export function siblingCompare(a: KnowledgeNode, b: KnowledgeNode) {
  return (
    (a.sort_order ?? a.y) - (b.sort_order ?? b.y) || a.id.localeCompare(b.id)
  );
}
