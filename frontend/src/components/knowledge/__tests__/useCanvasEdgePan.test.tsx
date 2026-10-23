import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useCanvasEdgePan } from '../useCanvasEdgePan';
let frames:Map<number,FrameRequestCallback>,id:number;
const host=document.createElement('div');host.getBoundingClientRect=()=>({left:0,top:0,right:800,bottom:600,width:800,height:600,x:0,y:0,toJSON:()=>({})});
beforeEach(()=>{frames=new Map();id=0;vi.stubGlobal('requestAnimationFrame',(cb:FrameRequestCallback)=>{frames.set(++id,cb);return id;});vi.stubGlobal('cancelAnimationFrame',(n:number)=>frames.delete(n));});
afterEach(()=>vi.unstubAllGlobals());
function frame(time:number){const callbacks=[...frames.values()];frames.clear();act(()=>callbacks.forEach(cb=>cb(time)));}
it('continues edge pan for a stationary pointer and stops on completion',()=>{
 const tick=vi.fn();const {result,unmount}=renderHook(()=>useCanvasEdgePan({viewport:{current:host},identity:'one',enabled:true,active:()=>true,tick,cancel:vi.fn()}));
 act(()=>result.current.begin(1,{x:795,y:300}));frame(0);frame(16);frame(32);expect(tick.mock.calls[1][1]).toBeLessThan(0);
 act(()=>result.current.move(2,{x:400,y:300}));frame(48);expect(tick.mock.calls.at(-1)![1]).toBeLessThan(0);
 act(()=>result.current.stop());expect(frames.size).toBe(0);unmount();
});
it('cleans up on blur, disable, identity switch and unmount without phantom frames',()=>{
 const cancel=vi.fn();const {result,rerender,unmount}=renderHook(({identity,enabled})=>useCanvasEdgePan({viewport:{current:host},identity,enabled,active:()=>true,tick:vi.fn(),cancel}),{initialProps:{identity:'one',enabled:true}});
 act(()=>result.current.begin(1,{x:795,y:300}));act(()=>window.dispatchEvent(new Event('blur')));expect(cancel).toHaveBeenCalledTimes(1);expect(frames.size).toBe(0);
 act(()=>result.current.begin(1,{x:795,y:300}));rerender({identity:'one',enabled:false});frame(16);expect(frames.size).toBe(0);
 rerender({identity:'one',enabled:true});act(()=>result.current.begin(1,{x:795,y:300}));rerender({identity:'two',enabled:true});expect(frames.size).toBe(0);
 act(()=>result.current.begin(1,{x:795,y:300}));unmount();expect(frames.size).toBe(0);
});
