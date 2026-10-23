import type { KnowledgeRelation } from '../../services/knowledge';
export type RelationBox = { x: number; y: number; width: number; height: number };
type Port = { x: number; y: number; nx: number; ny: number };
export function relationCurve(a: RelationBox, b: RelationBox, lane = 0, scale = 1) {
  const ax = a.x + a.width / 2, ay = a.y + a.height / 2;
  const bx = b.x + b.width / 2, by = b.y + b.height / 2;
  const horizontal = Math.abs(bx - ax) / Math.max(1, a.width + b.width) >= Math.abs(by - ay) / Math.max(1, a.height + b.height);
  let start: Port, end: Port;
  if (horizontal) {
    const direction = bx >= ax ? 1 : -1;
    start = { x: direction > 0 ? a.x + a.width : a.x, y: ay, nx: direction, ny: 0 };
    end = { x: direction > 0 ? b.x : b.x + b.width, y: by, nx: -direction, ny: 0 };
  } else {
    const direction = by >= ay ? 1 : -1;
    start = { x: ax, y: direction > 0 ? a.y + a.height : a.y, nx: 0, ny: direction };
    end = { x: bx, y: direction > 0 ? b.y : b.y + b.height, nx: 0, ny: -direction };
  }
  const reach = Math.max(32 * scale, Math.min(160 * scale, Math.hypot(end.x - start.x, end.y - start.y) * .4));
  const length = Math.hypot(bx - ax, by - ay) || 1;
  const ox = -(by - ay) / length * lane, oy = (bx - ax) / length * lane;
  const path = `M ${start.x} ${start.y} C ${start.x + start.nx * reach + ox} ${start.y + start.ny * reach + oy}, ${end.x + end.nx * reach + ox} ${end.y + end.ny * reach + oy}, ${end.x} ${end.y}`;
  return { start, end, path };
}

export function relationLane(relations: KnowledgeRelation[], relation: KnowledgeRelation, zoom: number) {
  const a = relation.source_object_id, b = relation.target_object_id;
  const group = [...relations.filter(r => r.id !== relation.id), relation].filter(r => (r.source_object_id === a && r.target_object_id === b) || (r.source_object_id === b && r.target_object_id === a)).sort((x, y) => x.source_object_id.localeCompare(y.source_object_id) || x.target_object_id.localeCompare(y.target_object_id) || x.relation_type.localeCompare(y.relation_type) || x.id.localeCompare(y.id));
  const direction = a < b ? 1 : -1;
  return { count: group.length, offset: (group.findIndex(r => r.id === relation.id) - (group.length - 1) / 2) * 36 / zoom * direction };
}
export const relationTypes: Record<string, string> = { reference: '引用', related: '关联', dependency: '依赖' };
