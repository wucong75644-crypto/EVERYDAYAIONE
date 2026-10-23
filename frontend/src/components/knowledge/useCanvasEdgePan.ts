import { useEffect, useLayoutEffect, useRef } from 'react';
import type { RefObject } from 'react';
import { edgeVelocity } from './viewportGestures';
import type { CanvasPoint } from './viewportGestures';
type Options = { viewport: RefObject<HTMLDivElement | null>; identity: string; enabled: boolean; active: () => boolean; tick: (cursor: CanvasPoint, dx: number, dy: number, time: number) => void; cancel: (restore?: boolean) => void };
export function useCanvasEdgePan(options: Options) {
  const latest = useRef(options); useLayoutEffect(() => { latest.current = options; });
  const session = useRef<{ pointer: number; cursor: CanvasPoint } | null>(null); const frame = useRef<number | null>(null);
  const stop = () => { session.current = null; if (frame.current !== null) cancelAnimationFrame(frame.current); frame.current = null; };
  const tick = (time: number, before = time) => {
    const s = session.current; if (!s) return;
    if (!latest.current.enabled) { stop(); latest.current.cancel(); return; }
    const bounds = latest.current.viewport.current?.getBoundingClientRect();
    if (!bounds) { stop(); latest.current.cancel(); return; }
    if (latest.current.active()) {
      const dt = Math.min(32, time - before) / 1000;
      latest.current.tick(s.cursor, edgeVelocity(s.cursor.x, bounds.left, bounds.right) * dt, edgeVelocity(s.cursor.y, bounds.top, bounds.bottom) * dt, time);
    }
    frame.current = requestAnimationFrame(next => tick(next, time));
  };
  const previousIdentity = useRef(options.identity);
  useLayoutEffect(() => { if (previousIdentity.current !== options.identity) { stop(); latest.current.cancel(false); previousIdentity.current = options.identity; } }, [options.identity]);
  useEffect(() => {
    const blur = () => { stop(); latest.current.cancel(); };
    window.addEventListener('blur', blur);
    return () => { window.removeEventListener('blur', blur); stop(); };
  }, [options.identity]);
  return { begin: (pointer: number, cursor: CanvasPoint) => { stop(); session.current = { pointer, cursor }; frame.current = requestAnimationFrame(tick); },
    move: (pointer: number, cursor: CanvasPoint) => { if (session.current?.pointer === pointer) session.current.cursor = cursor; }, stop };
}
