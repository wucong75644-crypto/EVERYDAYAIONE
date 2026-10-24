import {useRef} from 'react';
import {render,screen,act} from '@testing-library/react';
import {it,expect,vi} from 'vitest';
import {useCanvasOcclusion} from '../useCanvasOcclusion';
it('attaches after loading and measures wrapped surfaces on resize, then cleans up',async()=>{
 let height=44;const callbacks:ResizeObserverCallback[]=[];const disconnect=vi.fn(),observe=vi.fn();
 vi.stubGlobal('ResizeObserver',class {constructor(callback:ResizeObserverCallback){callbacks.push(callback);}observe=observe;disconnect=disconnect;});
 const box=(y:number,width:number,height:number)=>({x:0,y,left:0,top:y,right:width,bottom:y+height,width,height,toJSON:()=>({})});
 function Harness({ready}:{ready:boolean}){const area=useRef<HTMLDivElement>(null),stamp=useCanvasOcclusion(area,'account:org',ready);return <><output data-testid="stamp">{stamp}</output>{ready&&<div ref={el=>{area.current=el;if(el)el.getBoundingClientRect=()=>box(0,800,700);}}><div className="kc-canvas-tools" ref={el=>{if(el)el.getBoundingClientRect=()=>box(56,760,height);}}>tools</div></div>}</>;}
 const flush=()=>act(async()=>{await Promise.resolve();});
 try{
  const view=render(<Harness ready={false}/>);expect(observe).not.toHaveBeenCalled();view.rerender(<Harness ready/>);await flush();expect(observe).toHaveBeenCalled();expect(screen.getByTestId('stamp')).toHaveTextContent('44');
  height=140;act(()=>callbacks.forEach(callback=>callback([],{} as ResizeObserver)));await flush();expect(screen.getByTestId('stamp')).toHaveTextContent('140');view.unmount();expect(disconnect).toHaveBeenCalled();await flush();
 }finally{vi.unstubAllGlobals();}
});
