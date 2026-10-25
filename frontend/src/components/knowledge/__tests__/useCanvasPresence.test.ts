import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { canvasSnapshot, useCanvasPresence } from '../useCanvasPresence';
import type { KnowledgeNode, KnowledgeRelation } from '../../../services/knowledge';
const root = { id: 'root', container_id: 'p', parent_id: null, title: 'Root', x: 20, y: 40, revision: 1 } as KnowledgeNode;
const child = { ...root, id: 'child', parent_id: 'root', title: 'Child', x: 350 };
const grand = { ...child, id: 'grand', parent_id: 'child', title: 'Grand', x: 680 };
const relation = { id: 'r', container_id: 'p', source_object_id: 'child', target_object_id: 'grand', relation_type: 'related' } as KnowledgeRelation;
const full = { nodes: [root, child, grand], relations: [relation] };
const folded = { nodes: [root], relations: [] };
beforeEach(() => vi.useFakeTimers()); afterEach(() => vi.useRealTimers());
describe('canvas presence feedback', () => {
  it('departs complete branches and connections and cleans up at the shared deadline', () => {
    const { result } = renderHook(() => useCanvasPresence('p', false)); act(() => result.current.transition(full, folded));
    expect(result.current.leaving.map(n => n.id)).toEqual(['child', 'grand']); expect(result.current.snapshot.relations).toEqual([relation]);
    act(() => vi.advanceTimersByTime(179)); expect(result.current.leaving).toHaveLength(2);
    act(() => vi.advanceTimersByTime(1)); expect(result.current.leaving).toHaveLength(0); expect(vi.getTimerCount()).toBe(0);
  });
  it('enters restored descendants and emphasizes only history changes', () => {
    const { result } = renderHook(() => useCanvasPresence('p', false)); act(() => result.current.transition(folded, full, true));
    expect([...result.current.entering]).toEqual(['child', 'grand']); expect([...result.current.emphasis]).toEqual(['child', 'grand']);
    act(() => result.current.transition(full, { ...full, nodes: [root, { ...child, title: 'New', x: 400 }, grand] }, true));
    expect([...result.current.emphasis]).toEqual(['child']); expect(result.current.entering.size).toBe(0);
  });
  it('reverses from measured opacity and scale rather than restarting from an invisible frame', () => {
    const { result } = renderHook(() => useCanvasPresence('p', false));
    act(() => result.current.transition({ ...folded, visuals: { child: { opacity: .45, scale: .985 } } }, full));
    expect(result.current.starts.child).toEqual({ opacity: .45, scale: .985 });
    act(() => result.current.transition({ ...full, visuals: { child: { opacity: .7, scale: .991 } } }, folded));
    expect(result.current.starts.child).toEqual({ opacity: .7, scale: .991 });
  });
  it('replaces feedback on rapid reversal without a stale cleanup erasing it', () => {
    const { result } = renderHook(() => useCanvasPresence('p', false)); act(() => result.current.transition(full, folded)); act(() => vi.advanceTimersByTime(100));
    act(() => result.current.transition(folded, full)); act(() => vi.advanceTimersByTime(80));
    expect(result.current.leaving).toHaveLength(0); expect(result.current.entering.has('child')).toBe(true);
    act(() => vi.advanceTimersByTime(100)); expect(result.current.entering.size).toBe(0);
  });
  it('clears snapshots for reduced motion, changed identity and unmount', () => {
    const { result, rerender, unmount } = renderHook(({ identity, reduced }) => useCanvasPresence(identity, reduced), { initialProps: { identity: 'u:org:p', reduced: false } });
    act(() => result.current.transition(full, folded)); rerender({ identity: 'u:org:p', reduced: true });
    expect(result.current.leaving).toHaveLength(0); expect(vi.getTimerCount()).toBe(0);
    act(() => result.current.transition(folded, full)); expect(result.current.entering.size).toBe(0);
    rerender({ identity: 'u:org:p', reduced: false }); act(() => result.current.transition(full, folded));
    rerender({ identity: 'other:org:p', reduced: false }); expect(result.current.snapshot.nodes).toHaveLength(0); expect(vi.getTimerCount()).toBe(0);
    act(() => result.current.transition(full, folded)); unmount(); expect(vi.getTimerCount()).toBe(0);
  });
  it('does not retain removed content for disallowed departures or cancellation', () => {
    const { result } = renderHook(() => useCanvasPresence('p', false)); act(() => result.current.transition(full, folded, false, false)); expect(result.current.snapshot.nodes).toHaveLength(0);
    act(() => result.current.transition(full, folded)); act(() => result.current.cancel()); expect(result.current.leaving).toHaveLength(0);
  });
  it('retains measured card dimensions for departing association endpoints', () => {
    const viewport = document.createElement('div'), card = document.createElement('article'); card.dataset.nodeId = 'child';
    Object.defineProperties(card, { offsetWidth: { value: 240 }, offsetHeight: { value: 116 } }); viewport.append(card);
    const before = canvasSnapshot(full.nodes, full.relations, 'p', new Set(), viewport);
    const { result } = renderHook(() => useCanvasPresence('p', false)); act(() => result.current.transition(before, folded));
    expect(result.current.snapshot.sizes?.child).toEqual({ width: 240, height: 116 });
  });
  it('filters other spaces, folds and missing connection endpoints', () => {
    expect(canvasSnapshot([...full.nodes, { ...root, id: 'private', container_id: 'other' }], [relation], 'p', new Set(['root']))).toEqual(folded);
    expect(canvasSnapshot(full.nodes, full.relations, 'p', new Set(['child'])).nodes.map(n => n.id)).toEqual(['root', 'child']);
    expect(canvasSnapshot(full.nodes, full.relations, 'p', new Set()).relations).toHaveLength(1);
  });
});
