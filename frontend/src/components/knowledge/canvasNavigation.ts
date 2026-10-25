import {summaryParent} from './summaryScope';
import {
  effectiveStructure,
  branchDirection,
  childDirection,
  arrowDirection,
  opposite,
  vertical,
} from "./structureDirection";
import type { KnowledgeNode } from "../../services/knowledge";
import { siblingCompare } from "./branchLayout";
export function navigateNode(
  nodes: KnowledgeNode[],
  id: string | null,
  key: string,
  folded: Set<string>,
  container?: string,
): { id: string | null; expand?: string; collapse?: string } {
  const node = nodes.find((n) => n.id === id);
  if (!node)
    return {
      id:
        nodes
          .filter(
            (n) => !summaryParent(nodes,n) && (!container || n.container_id === container),
          )
          .sort(siblingCompare)[0]?.id ?? null,
    };
  const parentId=summaryParent(nodes,node);
  const children = nodes
      .filter((n) => n.parent_id === id && !n.canvas_data?.summary)
      .sort(siblingCompare),
    direction = arrowDirection[key];
  const out = childDirection(nodes, node.id),
    kind = effectiveStructure(nodes, node.id),
    incoming = branchDirection(nodes, node.id);
  const chosen =
    kind === "balanced"
      ? children.filter((n) => branchDirection(nodes, n.id) === direction)
      : direction === out
        ? children
        : [];
  if (
    (kind === "balanced" && ["left", "right"].includes(direction)) ||
    direction === out
  ) {
    if (folded.has(node.id) && chosen.length)
      return { id: node.id, expand: node.id };
    return { id: chosen[0]?.id ?? node.id };
  }
  if (
    direction === opposite[parentId ? incoming : out] &&
    kind !== "balanced" &&
    children.length &&
    !folded.has(node.id)
  )
    return { id: node.id, collapse: node.id };
  if (parentId && direction === opposite[incoming])
    return { id: parentId };
  const peers = nodes
    .filter(
      (n) =>
        n.container_id === node.container_id &&
        summaryParent(nodes,n) === parentId &&
        (!parentId ||
          effectiveStructure(nodes, parentId) !== "balanced" ||
          branchDirection(nodes, n.id) === incoming),
    )
    .sort(siblingCompare);
  const along = vertical(incoming) ? ["left", "right"] : ["up", "down"];
  if (!along.includes(direction)) return { id: node.id };
  const at = peers.findIndex((n) => n.id === node.id);
  return {
    id:
      peers[at + (["up", "left"].includes(direction) ? -1 : 1)]?.id ?? node.id,
  };
}
export function revealNode(
  view: { x: number; y: number; z: number },
  node: { x: number; y: number },
  card: { width: number; height: number },
  viewport: { width: number; height: number },
) {
  const axis = (
    offset: number,
    start: number,
    size: number,
    length: number,
    leading: number,
    trailing: number,
  ) => {
    const a = start * view.z + offset,
      b = a + size * view.z;
    if (size * view.z > length - leading - trailing)
      return leading - start * view.z;
    return a < leading
      ? offset + leading - a
      : b > length - trailing
        ? offset + length - trailing - b
        : offset;
  };
  return {
    ...view,
    x: axis(view.x, node.x, card.width, viewport.width, 32, 40),
    y: axis(view.y, node.y, card.height, viewport.height, 64, 64),
  };
}
