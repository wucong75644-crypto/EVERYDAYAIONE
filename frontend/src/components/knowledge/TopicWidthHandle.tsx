import { useRef } from "react";
import type { PointerEvent } from "react";
export type TopicResize = {width:number;height:number;dx:number;dy:number};
const edges = ['e','w','n','s','ne','nw','se','sw'] as const;
const names = {e:'调整节点宽度',w:'拖动左边调整大小',n:'拖动上边调整大小',s:'拖动下边调整大小',ne:'拖动右上角调整大小',nw:'拖动左上角调整大小',se:'拖动右下角调整大小',sw:'拖动左下角调整大小'};
export function TopicWidthHandle({width,height=146,zoom,onPreview,onCommit,onCancel,onResizePreview,onResizeCommit,onStart,onEnd,onEscapeIdle,minWidth=80,maxWidth=800,minHeight=146,maxHeight=1600,measureContent=true,scopeLabel=''}:{
 onStart?:()=>void;onEnd?:()=>void;onEscapeIdle?:()=>void;minWidth?:number;maxWidth?:number;minHeight?:number;maxHeight?:number;measureContent?:boolean;scopeLabel?:string;
 width:number;height?:number;zoom:number;onPreview:(width:number)=>void;onCommit:(width:number|null)=>void;onCancel:()=>void;
 onResizePreview?:(size:TopicResize)=>void;onResizeCommit?:(size:TopicResize|null)=>void;
}) {
 const drag=useRef<{x:number;y:number;width:number;height:number;edge:string;pointer:number;value:TopicResize;moved:boolean}|null>(null);
 const cancel=()=>{drag.current=null;onCancel();};
 const move=(e:PointerEvent<HTMLButtonElement>)=>{
  const d=drag.current;if(!d||e.pointerId!==d.pointer)return;
  const x=(e.clientX-d.x)/zoom,y=(e.clientY-d.y)/zoom;
  const horizontal=d.edge.includes('e')||d.edge.includes('w'),vertical=d.edge.includes('n')||d.edge.includes('s');
  const w=horizontal?Math.max(minWidth,Math.min(maxWidth,Math.round(d.width+(d.edge.includes('w')?-x:x)))):d.width;
  let minimum=minHeight;
  const card=e.currentTarget.parentElement,clone=(measureContent?card?.cloneNode(true):undefined) as HTMLElement|undefined;
  if(clone){clone.removeAttribute('data-node-id');clone.inert=true;Object.assign(clone.style,{width:w+'px',height:'auto',minHeight:minHeight+'px',position:'fixed',visibility:'hidden',pointerEvents:'none',transform:'none',left:'0',top:'0'});card?.parentElement?.append(clone);minimum=Math.max(minHeight,clone.offsetHeight);clone.remove();}
  const h=Math.min(maxHeight,vertical?Math.max(minimum,Math.round(d.height+(d.edge.includes('n')?-y:y))):Math.max(minimum,d.height));
  d.value={width:w,height:h,dx:d.edge.includes('w')?d.width-w:0,dy:d.edge.includes('n')?d.height-Math.max(minimum,h):0};
  d.moved=d.moved||Math.abs(x)+Math.abs(y)>0;
  if(onResizePreview)onResizePreview(d.value);else onPreview(w);
 };
 const commit=(size:TopicResize|null)=>{if(onResizeCommit)onResizeCommit(size);else onCommit(size?.width??null);};
 return <>{edges.map(edge=><button key={edge} className={'kc-width-handle kc-resize-'+edge} aria-label={scopeLabel?scopeLabel+names[edge].replace('节点',''):names[edge]} role={edge==='e'?'slider':undefined} aria-valuemin={edge==='e'?minWidth:undefined} aria-valuemax={edge==='e'?maxWidth:undefined} aria-valuenow={edge==='e'?width:undefined} title="拖动边缘或角调整大小；双击恢复适应内容；方向键微调；Esc 取消"
 onPointerDown={e=>{e.stopPropagation();if(e.button!==0)return;e.preventDefault();e.currentTarget.focus({preventScroll:true});onStart?.();drag.current={x:e.clientX,y:e.clientY,width,height,edge,pointer:e.pointerId,value:{width,height,dx:0,dy:0},moved:false};e.currentTarget.setPointerCapture(e.pointerId);}}
 onPointerMove={move} onPointerUp={e=>{const d=drag.current;if(!d||d.pointer!==e.pointerId)return;e.stopPropagation();move(e);drag.current=null;e.currentTarget.releasePointerCapture(e.pointerId);if(d.moved)commit(d.value);else onCancel();onEnd?.();}}
 onPointerCancel={cancel} onLostPointerCapture={()=>{if(drag.current)cancel();}}
 onClick={e=>e.stopPropagation()} onDoubleClick={e=>{e.stopPropagation();commit(null);}}
 onKeyDown={e=>{e.stopPropagation();if(e.key==='Escape'){e.preventDefault();const d=drag.current;if(d){drag.current=null;e.currentTarget.releasePointerCapture(d.pointer);onCancel();}else onEscapeIdle?.();}else if(e.key==='Home'){e.preventDefault();commit(null);}else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();if(!onResizeCommit){if(e.key==='ArrowLeft'||e.key==='ArrowRight')onCommit(Math.max(minWidth,Math.min(maxWidth,width+(e.key==='ArrowRight'?10:-10))));return;}onStart?.();const horizontal=e.key==='ArrowLeft'||e.key==='ArrowRight';commit({width:horizontal?Math.max(minWidth,Math.min(maxWidth,width+(e.key==='ArrowRight'?10:-10))):width,height:horizontal?height:Math.max(minHeight,Math.min(maxHeight,height+(e.key==='ArrowDown'?10:-10))),dx:0,dy:0});}}} />)}</>;
}
