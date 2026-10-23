import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useCanvasNodeMotion, useCanvasReducedMotion, useCanvasViewMotion } from '../useCanvasMotion';
import type { KnowledgeNode } from '../../../services/knowledge';
let callbacks: Map<number, FrameRequestCallback>, sequence: number;
function frame(time: number) { act(() => { const pending = [...callbacks.values()]; callbacks.clear(); pending.forEach(fn => fn(time)); }); }
beforeEach(() => { callbacks = new Map(); sequence = 0;
  vi.stubGlobal('requestAnimationFrame', (fn: FrameRequestCallback) => { callbacks.set(++sequence, fn); return sequence; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => callbacks.delete(id));
});
afterEach(() => vi.unstubAllGlobals());
describe('canvas motion', () => {
  it('interpolates translation and scale continuously and reaches the exact target', () => {
    const { result } = renderHook(() => useCanvasViewMotion('a', false));
    act(() => result.current.animate({ x: 500, y: -200, z: 2 }));
    expect(result.current.view).toEqual({ x: 60, y: 95, z: 1 }); frame(0); frame(110);
    expect(result.current.view.x).toBeGreaterThan(60); expect(result.current.view.x).toBeLessThan(500);
    expect(result.current.view.z).toBeGreaterThan(1); expect(result.current.view.z).toBeLessThan(2);
    frame(220); expect(result.current.view).toEqual({ x: 500, y: -200, z: 2 }); expect(callbacks.size).toBe(0);
  });
  it('retargets from the visible frame and accumulates consecutive zoom commands', () => {
    const { result } = renderHook(() => useCanvasViewMotion('a', false));
    act(() => { result.current.animate(v => ({ ...v, z: v.z * 1.2 })); result.current.animate(v => ({ ...v, z: v.z * 1.2 })); });
    frame(0); frame(100); const visible = result.current.view;
    act(() => result.current.animate({ x: 900, y: 300, z: 1 })); expect(result.current.view).toEqual(visible);
    frame(110); expect(result.current.view).toEqual(visible); frame(330); expect(result.current.view.x).toBe(900);
    act(() => { result.current.animate(v => ({ ...v, z: v.z * 1.2 })); result.current.animate(v => ({ ...v, z: v.z * 1.2 })); });
    frame(400); frame(620); expect(result.current.view.z).toBeCloseTo(1.44);
  });
  it('direct input interrupts an animation without a stale frame restoring it', () => {
    const { result } = renderHook(() => useCanvasViewMotion('a', false));
    act(() => result.current.animate({ x: 500, y: 0, z: 2 })); frame(0); frame(80);
    const next = { ...result.current.view, x: 42 }; act(() => result.current.immediate(next)); frame(400);
    expect(result.current.view).toEqual(next); expect(callbacks.size).toBe(0);
  });
  it('respects initial and changed reduced motion and cancels across identity/unmount', () => {
    const { result, rerender, unmount } = renderHook(({ identity, reduced }) => useCanvasViewMotion(identity, reduced), { initialProps: { identity: 'a', reduced: true } });
    act(() => result.current.animate({ x: 200, y: 0, z: 2 })); expect(result.current.view.x).toBe(200); expect(callbacks.size).toBe(0);
    rerender({ identity: 'a', reduced: false }); act(() => result.current.animate({ x: 400, y: 10, z: 1 })); frame(0); frame(50);
    rerender({ identity: 'a', reduced: true }); expect(result.current.view.x).toBe(400); expect(callbacks.size).toBe(0);
    rerender({ identity: 'a', reduced: false }); act(() => result.current.animate({ x: 900, y: 0, z: 1 }));
    rerender({ identity: 'b', reduced: false }); expect(callbacks.size).toBe(0);
    act(() => result.current.animate({ x: 0, y: 0, z: 1 })); unmount(); expect(callbacks.size).toBe(0);
  });
  it('listens to operating system motion preferences and removes its listener', () => {
    let change: () => void = () => {}; const remove = vi.fn(); const media = { matches: false, addEventListener: vi.fn((_: string, fn: () => void) => { change = fn; }), removeEventListener: remove };
    vi.stubGlobal('matchMedia', vi.fn(() => media)); const { result, unmount } = renderHook(useCanvasReducedMotion);
    expect(result.current).toBe(false); act(() => { media.matches = true; change(); }); expect(result.current).toBe(true);
    unmount(); expect(remove).toHaveBeenCalledWith('change', change);
  });
  it('detaches grabbed nodes from an in-flight animation without stale frames overriding the drag', () => {
    const source = [{ id: 'a', x: 0, y: 20 }, { id: 'b', x: 200, y: 20 }] as KnowledgeNode[];
    const target = source.map(n => ({ ...n, x: n.x + 100 })); const { result } = renderHook(() => useCanvasNodeMotion('a', false));
    act(() => result.current.animate(source, target)); frame(0); frame(80); const visible = result.current.project(target);
    act(() => result.current.detach(new Set(['a']))); frame(120);
    expect(result.current.project(visible)[0].x).toBe(visible[0].x); expect(result.current.coordinates.current?.a).toBeUndefined();
    expect(result.current.project(target)[1].x).toBeGreaterThan(visible[1].x);
    act(() => result.current.animate(result.current.project(visible), target)); frame(200); frame(360); expect(result.current.project(target)).toEqual(target);
  });
  it('projects every moving node and its shared line geometry from the same landing coordinates', () => {
    const source = [{ id: 'a', x: 0, y: 20 }, { id: 'b', x: 200, y: 20 }] as KnowledgeNode[];
    const target = source.map(n => ({ ...n, x: n.x + 100, y: n.y + 80 }));
    const { result, rerender } = renderHook(({ reduced }) => useCanvasNodeMotion('a', reduced), { initialProps: { reduced: false } });
    act(() => result.current.animate(source, target)); expect(result.current.project(target)).toEqual(source);
    frame(0); frame(80); const projected = result.current.project(target);
    expect(projected[0].x).toBeGreaterThan(0); expect(projected[0].x).toBeLessThan(100); expect(projected[1].x - projected[0].x).toBe(200);
    frame(160); expect(result.current.project(target)).toEqual(target); expect(result.current.active).toBe(false);
    act(() => result.current.animate(source, target)); rerender({ reduced: true }); expect(result.current.project(target)).toEqual(target); expect(callbacks.size).toBe(0);
  });
});
