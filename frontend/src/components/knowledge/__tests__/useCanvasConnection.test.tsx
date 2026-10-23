import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { useCanvasConnection } from '../useCanvasConnection';
import type { KnowledgeNode } from '../../../services/knowledge';
const root: KnowledgeNode = { id: 'a', node_id: 'a', container_id: 'space', parent_id: null, kind: 'topic', title: 'A', revision: 1, layout_revision: 1, x: 40, y: 80 };
let host: HTMLDivElement;
let frames: Map<number, FrameRequestCallback>;
let counter: number;
function rect(x: number, y: number, width: number, height: number) { return { x, y, left: x, top: y, right: x + width, bottom: y + height, width, height, toJSON: () => ({}) } as DOMRect; }
function setup(container = 'space', onTap = vi.fn()) {
  return renderHook(({ identity, enabled }) => {
    const [view, setView] = useState({ x: 0, y: 0, z: 1 });
    return { ...useCanvasConnection({ viewport: { current: host }, view, setView, nodes: [root, { ...root, id: 'b', container_id: container }], identity, enabled, onTap }), view };
  }, { initialProps: { identity: 'first', enabled: true } });
}
beforeEach(() => {
  frames = new Map(); counter = 0;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++counter, callback); return counter; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  host = document.createElement('div'); host.tabIndex = 0;
  host.getBoundingClientRect = () => rect(10, 20, 800, 600);
  host.setPointerCapture = vi.fn(); host.hasPointerCapture = vi.fn(() => true); host.releasePointerCapture = vi.fn();
  for (const [id, x] of [['a', 50], ['b', 500]] as const) {
    const el = document.createElement('article'); el.dataset.nodeId = id; el.getBoundingClientRect = () => rect(x, 100, 240, 120); host.append(el);
  }
  document.body.append(host);
  Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: () => host });
});
afterEach(() => { cleanup(); host.remove(); vi.unstubAllGlobals(); });
function frame(time: number) { const callbacks = [...frames.values()]; frames.clear(); act(() => callbacks.forEach(callback => callback(time))); }
describe('canvas connection gesture', () => {
  it('treats a small pointer release as one tap, but never taps after dragging or cancellation', () => {
    const onTap = vi.fn(); const { result } = setup('space', onTap);
    act(() => { result.current.begin('a', 1, { x: 290, y: 155 }); result.current.finish(1, { x: 292, y: 156 }); });
    expect(onTap).toHaveBeenCalledExactlyOnceWith('a');
    act(() => { result.current.begin('a', 2, { x: 290, y: 155 }); result.current.move(2, { x: 400, y: 350 }); result.current.finish(2, { x: 290, y: 155 }); });
    act(() => { result.current.begin('a', 3, { x: 290, y: 155 }); result.current.cancel(); result.current.finish(3, { x: 290, y: 155 }); });
    expect(onTap).toHaveBeenCalledTimes(1);
  });
  it('follows the pointer then returns a blank drop in world coordinates', () => {
    const { result } = setup(); act(() => result.current.begin('a', 1, { x: 290, y: 155 }));
    act(() => result.current.move(1, { x: 400, y: 350 }));
    expect(result.current.preview).toMatchObject({ source: 'a', target: null, end: { x: 390, y: 330 }, valid: true });
    let drop; act(() => { drop = result.current.finish(1, { x: 400, y: 350 }); });
    expect(drop).toMatchObject({ source: 'a', target: null, position: { x: 390, y: 330 } });
    expect(result.current.preview).toBeNull(); expect(frames.size).toBe(0);
  });
  it('snaps within 20 screen pixels to the target edge', () => {
    const { result } = setup(); act(() => result.current.begin('a', 1, { x: 290, y: 155 }));
    act(() => result.current.move(1, { x: 485, y: 155 }));
    expect(result.current.preview).toMatchObject({ target: 'b', valid: true, end: { x: 490, y: 140 } });
    let drop; act(() => { drop = result.current.finish(1, { x: 485, y: 155 }); });
    expect(drop).toMatchObject({ target: 'b' });
  });
  it('rejects self, cross-space targets and drops outside the canvas', () => {
    const { result } = setup('other');
    for (const cursor of [{ x: 200, y: 150 }, { x: 550, y: 150 }, { x: 850, y: 400 }]) {
      act(() => result.current.begin('a', 1, { x: 290, y: 155 }));
      act(() => result.current.move(1, cursor)); expect(result.current.preview?.valid).toBe(false);
      let drop; act(() => { drop = result.current.finish(1, cursor); }); expect(drop).toBeNull();
    }
  });
  it('cancels with Escape and ignores the later pointer release', () => {
    const { result } = setup(); act(() => result.current.begin('a', 1, { x: 290, y: 155 }));
    act(() => result.current.move(1, { x: 400, y: 350 }));
    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })));
    expect(result.current.preview).toBeNull(); expect(frames.size).toBe(0);
    expect(result.current.finish(1, { x: 400, y: 350 })).toBeNull();
  });
  it('continues panning while held at the edge, updates drop coordinates and stops on cancel', () => {
    const { result } = setup(); act(() => result.current.begin('a', 1, { x: 290, y: 155 }));
    act(() => result.current.move(1, { x: 805, y: 500 })); frame(0); frame(16); frame(32);
    expect(result.current.view.x).toBeLessThan(-10);
    const x = result.current.view.x;
    let drop; act(() => { drop = result.current.finish(1, { x: 805, y: 500 }); });
    expect(drop).toMatchObject({ position: { x: 795 - x, y: 480 } }); expect(frames.size).toBe(0);
  });
  it('does not create from a click, a secondary pointer or a drop on controls', () => {
    const { result } = setup(); act(() => result.current.begin('a', 1, { x: 290, y: 155 }));
    expect(result.current.move(2, { x: 400, y: 350 })).toBe(false);
    let drop; act(() => { drop = result.current.finish(1, { x: 292, y: 155 }); }); expect(drop).toBeNull();
    const button = document.createElement('button'); host.append(button);
    Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: () => button });
    act(() => result.current.begin('a', 1, { x: 290, y: 155 }));
    act(() => { drop = result.current.finish(1, { x: 400, y: 350 }); }); expect(drop).toBeNull();
  });
  it('cleans up on lost window focus, identity change, disable and unmount', () => {
    const { result, rerender, unmount } = setup();
    act(() => result.current.begin('a', 1, { x: 290, y: 155 })); act(() => window.dispatchEvent(new Event('blur'))); expect(frames.size).toBe(0);
    act(() => result.current.begin('a', 1, { x: 290, y: 155 })); rerender({ identity: 'second', enabled: true }); expect(frames.size).toBe(0);
    act(() => result.current.begin('a', 1, { x: 290, y: 155 })); rerender({ identity: 'second', enabled: false }); frame(16); expect(frames.size).toBe(0);
    rerender({ identity: 'second', enabled: true }); act(() => result.current.begin('a', 1, { x: 290, y: 155 })); unmount(); expect(frames.size).toBe(0);
  });
});

describe('association endpoint reconnect', () => {
  const edge = { id: 'edge', container_id: 'space', source_object_id: 'a', target_object_id: 'b', relation_type: 'related' };
  function reconnectSetup(duplicate = false, otherSpace = false) {
    const el = document.createElement('article'); el.dataset.nodeId = 'c'; el.getBoundingClientRect = () => rect(500,350,240,120); host.append(el);
    return renderHook(({identity,enabled}) => {
      const [view,setView]=useState({x:0,y:0,z:1});
      return useCanvasConnection({viewport:{current:host},view,setView,nodes:[root,{...root,id:'b'},{...root,id:'c',container_id:otherSpace?'other':'space'}],relations:[edge,...(duplicate?[{...edge,id:'duplicate',target_object_id:'c'}]:[])],identity,enabled});
    },{initialProps:{identity:'one',enabled:true}});
  }
  it.each(['source','target'] as const)('previews and returns the dragged %s endpoint with the original revision', endpoint => {
    const {result}=reconnectSetup();
    act(()=>result.current.beginReconnect(edge,endpoint,12,1,{x:300,y:150}));
    act(()=>result.current.move(1,{x:550,y:380}));
    expect(result.current.preview).toMatchObject({target:'c',valid:true,reconnect:{endpoint,revision:12}});
    let drop;act(()=>{drop=result.current.finish(1,{x:550,y:380});});
    expect(drop).toMatchObject({target:'c',source:endpoint==='source'?'b':'a',reconnect:{relation:edge,endpoint,revision:12}});
  });
  it.each(['blank','self','duplicate','other-space','outside'])('rejects %s without returning a creation drop', kind => {
    const {result}=reconnectSetup(kind==='duplicate',kind==='other-space');
    act(()=>result.current.beginReconnect(edge,'target',12,1,{x:500,y:150}));
    const cursor=kind==='blank'?{x:380,y:500}:kind==='self'?{x:150,y:150}:kind==='outside'?{x:900,y:400}:{x:550,y:380};
    act(()=>result.current.move(1,cursor));expect(result.current.preview?.valid).toBe(false);
    let drop;act(()=>{drop=result.current.finish(1,cursor);});expect(drop).toBeNull();
  });
  it('keeps the original gesture when a second pointer tries to start another reconnect', () => {
    const {result}=reconnectSetup();
    act(()=>result.current.beginReconnect(edge,'target',12,1,{x:500,y:150}));
    act(()=>result.current.beginReconnect(edge,'source',99,2,{x:300,y:150}));
    expect(result.current.preview?.reconnect).toMatchObject({endpoint:'target',revision:12});
    expect(result.current.finish(2,{x:550,y:380})).toBeNull();
    act(()=>result.current.cancel());
  });
  it('cancels on Escape and identity change without returning a later drop', () => {
    const {result,rerender}=reconnectSetup();
    act(()=>result.current.beginReconnect(edge,'target',12,1,{x:500,y:150}));
    act(()=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'})));
    expect(result.current.finish(1,{x:550,y:380})).toBeNull();
    act(()=>result.current.beginReconnect(edge,'target',12,1,{x:500,y:150}));
    rerender({identity:'two',enabled:true});
    expect(result.current.preview).toBeNull();expect(frames.size).toBe(0);
  });
});
