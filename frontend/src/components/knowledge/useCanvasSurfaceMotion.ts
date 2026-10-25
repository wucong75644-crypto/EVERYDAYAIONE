import { createContext, useContext, useLayoutEffect, useRef, useState, useCallback } from 'react';
import type { RefObject } from 'react';
import { useCanvasReducedMotion } from './useCanvasMotion';
type Kind = 'menu' | 'panel' | 'dialog' | 'backdrop';
export class SurfaceOwner {
  alive = true; reduced = false; exits = new Set<() => void>();
  constructor(readonly identity: string) {}
  start() { this.alive = true; }
  clear() { this.exits.forEach(cancel => cancel()); }
  dispose() { this.alive = false; this.clear(); }
  preference(reduced: boolean) { this.reduced = reduced; if (reduced) this.clear(); }
}
export const SurfaceMotionContext = createContext<SurfaceOwner | null>(null);
export function useCanvasSurfaceScope() { return useContext(SurfaceMotionContext) !== null; }
export function surfaceFrames(kind: Kind, exit = false): Keyframe[] {
  const offset = kind === 'panel' ? 'translateX(16px)' : kind === 'menu' ? 'translateY(-4px)' : kind === 'dialog' ? 'translateY(6px)' : 'none';
  return exit ? [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: offset }] : [{ opacity: 0, transform: offset }, { opacity: 1, transform: 'none' }];
}
/** React and focus scopes unmount immediately; only an inert static snapshot survives close. */
export function useCanvasSurfaceMotion(ref: RefObject<HTMLElement | null>, kind: Kind, enabled = true) {
  const owner = useContext(SurfaceMotionContext), reduced = useCanvasReducedMotion();
  const allowed = useRef(false);
  const [element, setElement] = useState<HTMLElement | null>(null);
  const attach = useCallback((node: HTMLElement | null) => { ref.current = node; setElement(node); }, [ref]);
  useLayoutEffect(() => { allowed.current = enabled && !reduced; }, [enabled, reduced]);
  useLayoutEffect(() => {
    if (!enabled || !element) return;
    return () => {
      if (!allowed.current || (owner && (!owner.alive || owner.reduced)) || !element.isConnected || typeof element.animate !== 'function') return;
      const rect = element.getBoundingClientRect(); if (!rect.width || !rect.height) return;
      const ghost = element.cloneNode(true) as HTMLElement, style = getComputedStyle(element);
      ghost.setAttribute('inert', ''); ghost.setAttribute('aria-hidden', 'true'); ghost.dataset.canvasSurfaceExit = kind;
      [ghost, ...ghost.querySelectorAll<HTMLElement>('*')].forEach(node => { node.removeAttribute('id'); node.removeAttribute('autofocus'); node.removeAttribute('tabindex'); });

      const originals = element.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('input,textarea,select');
      ghost.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('input,textarea,select').forEach((node, index) => { if (node.type !== 'file') node.value = originals[index]?.value ?? ''; });
      Object.assign(ghost.style, { position: 'fixed', left: rect.left + 'px', top: rect.top + 'px', width: rect.width + 'px', height: rect.height + 'px', right: 'auto', bottom: 'auto', margin: '0', transform: 'none', translate: 'none', animation: 'none', boxSizing: 'border-box', pointerEvents: 'none', color: style.color, font: style.font, opacity: style.opacity });
      const host = document.createElement('div'); host.className = 'kc-page'; host.setAttribute('inert', ''); host.setAttribute('aria-hidden', 'true');
      Object.assign(host.style, { position: 'fixed', inset: '0', height: '0', width: '0', pointerEvents: 'none', zIndex: style.zIndex === 'auto' ? '20' : style.zIndex });
      host.append(ghost); document.body.append(host);
      const originalElements = [element, ...element.querySelectorAll<HTMLElement>('*')];
      // Preserve the viewport of long panels and text areas in their final visual frame.
      [ghost, ...ghost.querySelectorAll<HTMLElement>('*')].forEach((node, index) => { const source = originalElements[index]; if (source) { node.scrollTop = source.scrollTop; node.scrollLeft = source.scrollLeft; } });
      ghost.querySelectorAll('iframe,video,audio,object,embed').forEach(node => node.remove());
      const animation = ghost.animate(surfaceFrames(kind, true).map((frame, index) => index === 0 ? { ...frame, opacity: Number(style.opacity || 1) } : frame), { duration: 120, easing: 'ease-in', fill: 'forwards' });
      const remove = () => { animation.cancel(); host.remove(); owner?.exits.delete(remove); clearTimeout(timer); };
      const timer = setTimeout(remove, 150); owner?.exits.add(remove); animation.onfinish = remove;
    };
  }, [element, kind, enabled, owner]);
  useLayoutEffect(() => {
    if (!enabled || !element || reduced || typeof element.animate !== 'function') return;
    owner?.exits.forEach(cancel => cancel());
    const animation = element.animate(surfaceFrames(kind), { duration: 180, easing: 'cubic-bezier(.2,.7,.2,1)' });
    return () => animation.cancel();
  }, [element, kind, enabled, reduced, owner]);
  return attach;
}
