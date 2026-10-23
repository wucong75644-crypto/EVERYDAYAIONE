import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { relationCurve, relationLane } from './relationGeometry';
import type { KnowledgeNode, KnowledgeRelation } from '../../services/knowledge';

type Point = { x: number; y: number };
type View = Point & { z: number };
type Preview = { source: string; target: string | null; start: Point; end: Point; valid: boolean; inside: boolean; path: string; reconnect?: { relation: KnowledgeRelation; endpoint: 'source' | 'target'; revision: number }; reason?: string };
type Options = {
  viewport: RefObject<HTMLDivElement | null>; view: View; setView: (view: View) => void;
  nodes: KnowledgeNode[]; relations?: KnowledgeRelation[]; identity: string; enabled: boolean; onTap?: (source: string) => void;
};
type Session = { source: string; pointer: number; origin: Point; cursor: Point; moved: boolean; reconnect?: Preview['reconnect'] };
export function useCanvasConnection(options: Options) {
  const latest = useRef(options);
  const view = useRef(options.view);
  useLayoutEffect(() => { latest.current = options; view.current = options.view; });
  const session = useRef<Session | null>(null); const frame = useRef<number | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const inspect = (s: Session): Preview | null => {
    const { viewport, nodes } = latest.current; const host = viewport.current;
    const index = new Map(nodes.map(node => [node.id, node]));
    const source = index.get(s.source); if (!host || !source) return null;
    const bounds = host.getBoundingClientRect();
    const sourceElement = host.querySelector<HTMLElement>(`[data-node-id="${source.id}"]`);
    if (!sourceElement) return null;
    const a = sourceElement.getBoundingClientRect();
    const inside = s.cursor.x >= bounds.left && s.cursor.x <= bounds.right && s.cursor.y >= bounds.top && s.cursor.y <= bounds.bottom;
    let candidate: { node: KnowledgeNode; rect: DOMRect; distance: number } | null = null;
    for (const element of host.querySelectorAll<HTMLElement>('[data-node-id]')) {
      const node = index.get(element.dataset.nodeId ?? ''); if (!node) continue;
      const rect = element.getBoundingClientRect();
      const dx = Math.max(rect.left - s.cursor.x, 0, s.cursor.x - rect.right);
      const dy = Math.max(rect.top - s.cursor.y, 0, s.cursor.y - rect.bottom);
      const distance = Math.hypot(dx, dy);
      if (distance <= 20 && (!candidate || distance < candidate.distance)) candidate = { node, rect, distance };
    }
    const target = candidate?.node ?? null;
    const hit = document.elementFromPoint?.(s.cursor.x, s.cursor.y);
    const blocked = !!hit?.closest('button,input,textarea,.kc-location,.kc-zoom,.kc-quick');
    const fixed = { x: a.left - bounds.left, y: a.top - bounds.top, width: a.width, height: a.height };
    const destination = candidate ? { x: candidate.rect.left - bounds.left, y: candidate.rect.top - bounds.top, width: candidate.rect.width, height: candidate.rect.height } : { x: s.cursor.x - bounds.left, y: s.cursor.y - bounds.top, width: 0, height: 0 };
    const sourceId = s.reconnect?.endpoint === 'source' ? target?.id : source.id;
    const targetId = s.reconnect?.endpoint === 'source' ? source.id : target?.id;
    const proposed = s.reconnect ? { ...s.reconnect.relation, source_object_id: sourceId ?? '', target_object_id: targetId ?? '' } : { id: 'preview', container_id: source.container_id, source_object_id: sourceId ?? '', target_object_id: targetId ?? '', relation_type: 'related' };
    const lane = target ? relationLane(latest.current.relations ?? [], proposed, 1).offset : 0;
    const curve = s.reconnect?.endpoint === 'source' ? relationCurve(destination, fixed, lane, view.current.z) : relationCurve(fixed, destination, lane, view.current.z);
    const duplicate = target && (latest.current.relations ?? []).some(r => r.id !== s.reconnect?.relation.id && r.source_object_id === sourceId && r.target_object_id === targetId && r.relation_type === (s.reconnect?.relation.relation_type ?? 'related'));
    const reason = !inside ? '请在画布内选择节点' : blocked ? '请移到节点边缘' : target?.id === source.id ? '不能连接节点自身' : target && target.container_id !== source.container_id ? '不能跨空间连接' : duplicate ? '已有关联线，请选择其他节点' : s.reconnect && !target ? '请拖到节点上；空白处松手保留原线' : '';
    return { source: s.source, target: target?.id ?? null, start: curve.start, end: curve.end, path: curve.path, inside, valid: !reason, reconnect: s.reconnect, reason };

  };
  const cancel = () => {
    const s = session.current; session.current = null;
    if (frame.current !== null) cancelAnimationFrame(frame.current); frame.current = null;
    const host = latest.current.viewport.current;
    if (s && host?.hasPointerCapture(s.pointer)) host.releasePointerCapture(s.pointer);
    setPreview(null);
  };
  const tick = (time: number, previous = time) => {
    const s = session.current; if (!s) return;
    if (!latest.current.enabled) { cancel(); return; }
    const host = latest.current.viewport.current; if (!host) { cancel(); return; }
    const bounds = host.getBoundingClientRect();
    const speed = (cursor: number, low: number, high: number) => {
      if (cursor < low || cursor > high) return 0;
      if (cursor < low + 48) return 420 * (1 - (cursor - low) / 48);
      if (cursor > high - 48) return -420 * (1 - (high - cursor) / 48);
      return 0;
    };
    if (s.moved) {
      const dt = Math.min(32, time - previous) / 1000;
      const dx = speed(s.cursor.x, bounds.left, bounds.right) * dt;
      const dy = speed(s.cursor.y, bounds.top, bounds.bottom) * dt;
      if (dx || dy) {
        view.current = { ...view.current, x: view.current.x + dx, y: view.current.y + dy };
        latest.current.setView(view.current); setPreview(inspect(s));
      }
    }
    frame.current = requestAnimationFrame(next => tick(next, time));
  };
  const begin = (source: string, pointer: number, cursor: Point) => {
    if (!latest.current.enabled || session.current) return false;
    session.current = { source, pointer, cursor, origin: cursor, moved: false };
    latest.current.viewport.current?.focus({ preventScroll: true });
    latest.current.viewport.current?.setPointerCapture(pointer);
    frame.current = requestAnimationFrame(tick); return true;
  };
  const beginReconnect = (relation: KnowledgeRelation, endpoint: 'source' | 'target', revision: number, pointer: number, cursor: Point) => {
    const source = endpoint === 'source' ? relation.target_object_id : relation.source_object_id;
    if (!begin(source, pointer, cursor)) return;
    const s = session.current;
    if (s && s.pointer === pointer && !s.reconnect) { s.reconnect = { relation, endpoint, revision }; setPreview(inspect(s)); }
  };
  const move = (pointer: number, cursor: Point) => {
    const s = session.current; if (!s || s.pointer !== pointer) return false;
    s.cursor = cursor; if (Math.hypot(cursor.x - s.origin.x, cursor.y - s.origin.y) > 4) s.moved = true;
    if (s.moved) setPreview(inspect(s));
    return true;
  };
  const finish = (pointer: number, cursor: Point) => {
    const s = session.current; if (!s || s.pointer !== pointer) return null;
    move(pointer, cursor); const result = inspect(s);
    const bounds = latest.current.viewport.current?.getBoundingClientRect();
    const drop = bounds ? { x: (cursor.x - bounds.left - view.current.x) / view.current.z, y: (cursor.y - bounds.top - view.current.y) / view.current.z } : null;
    const element = document.elementFromPoint?.(cursor.x, cursor.y);
    const overControl = element?.closest('button,input,textarea,.kc-location,.kc-zoom,.kc-quick');
    const allowed = s.moved && result?.valid && drop && !overControl && latest.current.enabled;
    const tap = !s.moved && !s.reconnect && latest.current.enabled;
    cancel(); if (tap) latest.current.onTap?.(s.source);
    return allowed ? { ...result, position: drop } : null;
  };
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape' && session.current) { event.preventDefault(); cancel(); } };
    const blur = () => cancel();
    document.addEventListener('keydown', escape); window.addEventListener('blur', blur);
    return () => { document.removeEventListener('keydown', escape); window.removeEventListener('blur', blur); cancel(); };
    // The session is intentionally cancelled when the account changes or the component unmounts.
  }, [options.identity]);
  return { preview, begin, beginReconnect, move, finish, cancel };
}
