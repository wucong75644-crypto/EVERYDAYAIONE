import {summaryParent} from './summaryScope';
import type { KnowledgeNode } from "../../services/knowledge";
import { siblingCompare } from "./nodeOrder";
/** Restrict graph walks to the current space, and stop on malformed cycles. */
export function branchFoldState(
  nodes: KnowledgeNode[],
  folded: Set<string>,
  targets: string[],
  expand: boolean,
) {
  const index = new Map(nodes.map((n) => [n.id, n])),
    children = new Map<string, string[]>();
  for(const n of nodes){const parent=summaryParent(nodes,n);if(parent&&index.get(parent)?.container_id===n.container_id){const list=children.get(parent)??[];list.push(n.id);children.set(parent,list);}}
  const next = new Set(folded),
    visited = new Set<string>(),
    stack = [...targets];
  while (stack.length) {
    const id = stack.pop()!;
    if (visited.has(id) || !index.has(id)) continue;
    visited.add(id);
    const kids = children.get(id) ?? [];
    if (expand) next.delete(id);
    else if (kids.length) next.add(id);
    stack.push(...kids);
  }
  return next;
}
export function visibleSelection(
  nodes: KnowledgeNode[],
  folded: Set<string>,
  id: string,
) {
  const index = new Map(nodes.map((n) => [n.id, n]));
  let node = index.get(id),
    visible = node?.id;
  const seen = new Set<string>();
  while (node && summaryParent(nodes,node) && !seen.has(node.id)) {
    seen.add(node.id);
    const parent = index.get(summaryParent(nodes,node)!);
    if (!parent || parent.container_id !== node.container_id) break;
    if (folded.has(parent.id)) visible = parent.id;
    node = parent;
  }
  return visible ?? null;
}
export function centerTopic(
  nodes: KnowledgeNode[],
  selected: string | null,
  container?: string,
) {
  const local = nodes.filter((n) => !container || n.container_id === container),
    index = new Map(local.map((n) => [n.id, n]));
  let node = selected ? index.get(selected) : undefined;
  if (node?.canvas_data?.summary) {
    const member = index.get(node.canvas_data.summary.members[0]);
    if (member) node = member;
  }
  const seen = new Set<string>();
  while (node && summaryParent(nodes,node) && !seen.has(node.id)) {
    seen.add(node.id);
    const parent = index.get(summaryParent(nodes,node)!);
    if (!parent || parent.container_id !== node.container_id) {
      node = undefined;
      break;
    }
    node = parent;
  }
  if (node && !node.parent_id) return node;
  return (
    local
      .filter((n) => !n.parent_id && !n.canvas_data?.summary)
      .sort(siblingCompare)[0] ??
    local.filter((n) => !n.parent_id).sort(siblingCompare)[0]
  );
}
export function centerTopicView(
  view: { x: number; y: number; z: number },
  node: { x: number; y: number },
  size: { width: number; height: number },
  area: { width: number; height: number },
) {
  return {
    ...view,
    x: area.width / 2 - (node.x + size.width / 2) * view.z,
    y: area.height / 2 - (node.y + size.height / 2) * view.z,
  };
}
