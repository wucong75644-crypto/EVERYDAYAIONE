import {nodeHidden} from './summaryScope';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { KnowledgeNode, KnowledgeRelation } from '../../services/knowledge';
export type CanvasSnapshot = { nodes: KnowledgeNode[]; relations: KnowledgeRelation[]; sizes?: Record<string, { width: number; height: number }>; visuals?: Record<string, { opacity: number; scale: number }> };
const empty = { entering: new Set<string>(), emphasis: new Set<string>(), starts: {} as Record<string, { opacity: number; scale: number }>, leaving: [] as KnowledgeNode[], snapshot: { nodes: [], relations: [] } as CanvasSnapshot };
/** Visual departures never participate in the authoritative scene, selection or commands. */
export function useCanvasPresence(identity: string, reduced: boolean) {
  const [state, setState] = useState({ ...empty, identity });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const context = useRef({ identity, reduced });
  useLayoutEffect(() => { context.current = { identity, reduced }; }, [identity, reduced]);
  const cancel = useCallback(() => { if (timer.current !== null) clearTimeout(timer.current); timer.current = null; setState({ ...empty, identity: context.current.identity }); }, []);
  const transition = useCallback((before: CanvasSnapshot, after: CanvasSnapshot, emphasize = false, allowExit = true) => {
    cancel(); if (context.current.reduced) return;
    const old = new Map(before.nodes.map(n => [n.id, n])), next = new Set(after.nodes.map(n => n.id));
    const entering = new Set(after.nodes.filter(n => !old.has(n.id)).map(n => n.id));
    const leaving = allowExit ? before.nodes.filter(n => !next.has(n.id)) : [];
    const remainingRelations = new Set(after.relations.map(r => r.id)), leavingIds = new Set(leaving.map(n => n.id));
    const relations = allowExit ? before.relations.filter(r => !remainingRelations.has(r.id) || (leavingIds.has(r.source_object_id) || leavingIds.has(r.target_object_id))) : [];
    const emphasis = new Set(emphasize ? after.nodes.filter(n => { const p = old.get(n.id); return !p || p.x !== n.x || p.y !== n.y || p.title !== n.title || p.parent_id !== n.parent_id || p.revision !== n.revision; }).map(n => n.id) : []);
    if (!entering.size && !leaving.length && !emphasis.size && !relations.length) return;
    setState({ identity: context.current.identity, entering, emphasis, leaving, starts: before.visuals ?? {}, snapshot: { nodes: before.nodes, relations, sizes: before.sizes } });
    timer.current = setTimeout(cancel, 180);
  }, [cancel]);
  useEffect(() => { cancel(); return () => { if (timer.current !== null) clearTimeout(timer.current); }; }, [identity, reduced, cancel]);
  return { ...(state.identity === identity && !reduced ? state : empty), transition, cancel };
}
export function canvasSnapshot(nodes: KnowledgeNode[], relations: KnowledgeRelation[], scope: string | undefined, folds: Set<string>, viewport?: HTMLElement | null): CanvasSnapshot {
  const visible=nodes.filter(n=>n.container_id===scope&&!nodeHidden(nodes,folds,n));
  const ids = new Set(visible.map(n => n.id));
  const sizes = viewport ? Object.fromEntries([...viewport.querySelectorAll<HTMLElement>('[data-node-id]')].map(el => [el.dataset.nodeId!, { width: el.offsetWidth || 272, height: el.offsetHeight || 170 }])) : undefined;
  const visuals = viewport ? Object.fromEntries([...viewport.querySelectorAll<HTMLElement>('[data-node-id],[data-departure-id]')].map(el => {
    const style = getComputedStyle(el), transform = style.transform;
    const scale = transform && transform !== 'none' && typeof DOMMatrixReadOnly !== 'undefined' ? new DOMMatrixReadOnly(transform).a : 1;
    return [el.dataset.nodeId ?? el.dataset.departureId!, { opacity: Number(style.opacity || 1), scale }];
  })) : undefined;
  return { nodes: visible, ...(sizes ? { sizes, visuals } : {}), relations: relations.filter(r => r.container_id === scope && ids.has(r.source_object_id) && ids.has(r.target_object_id)) };
}
