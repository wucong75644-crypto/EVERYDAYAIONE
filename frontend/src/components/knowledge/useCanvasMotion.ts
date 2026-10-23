import { useCallback, useEffect, useRef, useState } from 'react';
import type { KnowledgeNode } from '../../services/knowledge';
import type { CanvasView } from './viewportGestures';
const ease = (t: number) => 1 - (1 - t) ** 3;
export function useCanvasReducedMotion() {
  const [reduced, setReduced] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ?? false);
  useEffect(() => {
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)'); if (!media) return;
    const change = () => setReduced(media.matches); change();
    media.addEventListener('change', change); return () => media.removeEventListener('change', change);
  }, []);
  return reduced;
}
export function useCanvasViewMotion(identity: string, reduced: boolean) {
  const [view, setView] = useState<CanvasView>({ x: 60, y: 95, z: 1 });
  const ref = useRef(view), frame = useRef<number | null>(null);
  const animation = useRef<{ to: CanvasView; from: CanvasView; start: number | null } | null>(null);
  const reducedRef = useRef(reduced); reducedRef.current = reduced;
  const cancel = useCallback(() => { if (frame.current !== null) cancelAnimationFrame(frame.current); frame.current = null; animation.current = null; }, []);
  const publish = useCallback((next: CanvasView) => { ref.current = next; setView(next); }, []);
  const immediate = useCallback((next: CanvasView) => { cancel(); publish(next); }, [cancel, publish]);
  const animate = useCallback((next: CanvasView | ((current: CanvasView) => CanvasView)) => {
    const prior = animation.current?.to ?? ref.current;
    const to = typeof next === 'function' ? next(prior) : next;
    if (to.x === prior.x && to.y === prior.y && to.z === prior.z) return;
    cancel(); if (reducedRef.current) { publish(to); return; }
    const current = { to, from: ref.current, start: null as number | null }; animation.current = current;
    const tick = (time: number) => {
      if (animation.current !== current) return;
      current.start ??= time; const progress = Math.min(1, (time - current.start) / 220), t = ease(progress);
      publish({ x: current.from.x + (to.x - current.from.x) * t, y: current.from.y + (to.y - current.from.y) * t, z: current.from.z + (to.z - current.from.z) * t });
      if (progress < 1) frame.current = requestAnimationFrame(tick); else { frame.current = null; animation.current = null; }
    };
    frame.current = requestAnimationFrame(tick);
  }, [cancel, publish]);
  useEffect(() => { if (reduced && animation.current) immediate(animation.current.to); }, [reduced, immediate]);
  useEffect(() => cancel, [identity, cancel]);
  return { view, ref, animate, immediate, cancel };
}
type Coordinates = Record<string, { x: number; y: number }>;
export function useCanvasNodeMotion(identity: string, reduced: boolean) {
  const [positions, setPositions] = useState<Coordinates | null>(null);
  const ref = useRef<Coordinates | null>(null), frame = useRef<number | null>(null);
  const detached = useRef(new Set<string>()), generation = useRef(0);
  const reducedRef = useRef(reduced); reducedRef.current = reduced;
  const publish = (next: Coordinates | null) => { ref.current = next; setPositions(next); };
  const cancel = useCallback(() => { generation.current++; detached.current.clear(); if (frame.current !== null) cancelAnimationFrame(frame.current); frame.current = null; ref.current = null; setPositions(null); }, []);
  const animate = (from: KnowledgeNode[], to: KnowledgeNode[]) => {
    cancel(); if (reducedRef.current) return;
    const source = new Map(from.map(n => [n.id, n]));
    const changed = to.filter(n => { const p = source.get(n.id); return p && (p.x !== n.x || p.y !== n.y); });
    if (!changed.length) return;
    publish(Object.fromEntries(changed.map(n => [n.id, { x: source.get(n.id)!.x, y: source.get(n.id)!.y }])));
    const token = generation.current;
    let start: number | null = null;
    const tick = (time: number) => {
      if (token !== generation.current) return;
      start ??= time; const progress = Math.min(1, (time - start) / 160), t = ease(progress);
      if (progress === 1) { frame.current = null; publish(null); return; }
      publish(Object.fromEntries(changed.filter(n => !detached.current.has(n.id)).map(n => { const p = source.get(n.id)!; return [n.id, { x: p.x + (n.x - p.x) * t, y: p.y + (n.y - p.y) * t }]; })));
      frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
  };
  useEffect(() => { if (reduced) cancel(); }, [reduced, cancel]);
  useEffect(() => cancel, [identity, cancel]);
  const project = (nodes: KnowledgeNode[]) => nodes.map(n => ref.current?.[n.id] ? { ...n, ...ref.current[n.id] } : n);
  const detach = (ids: Set<string>) => {
    ids.forEach(id => detached.current.add(id));
    const next = Object.fromEntries(Object.entries(ref.current ?? {}).filter(([id]) => !ids.has(id)));
    const remaining = Object.keys(next).length;
    publish(remaining ? next : null);
    if (!remaining) { if (frame.current !== null) cancelAnimationFrame(frame.current); frame.current = null; generation.current++; }
  };
  return { animate, cancel, detach, project, active: !!positions, coordinates: ref };
}
