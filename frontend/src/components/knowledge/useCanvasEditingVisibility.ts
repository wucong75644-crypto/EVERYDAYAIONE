import {useLayoutEffect} from 'react';
import type {RefObject} from 'react';
import type {CanvasView} from './viewportGestures';
import {canvasSafeArea,revealScreenRect} from './canvasOcclusion';
export function useCanvasEditingVisibility(viewport:RefObject<HTMLElement|null>,enabled:boolean,identity:string,view:RefObject<CanvasView>,update:(v:CanvasView)=>void,composing:RefObject<boolean>,change:unknown){
 useLayoutEffect(()=>{
  const area=viewport.current,editor=area?.querySelector<HTMLElement>('#kc-quick-title');if(!area||!editor||!enabled)return;
  let frame:number|null=null;
  const measure=()=>{
   frame=null;if(composing.current||!editor.isConnected)return;
   const selection=window.getSelection();if(!selection?.rangeCount||!editor.contains(selection.focusNode))return;
   const box=area.getBoundingClientRect();if(box.width<100||box.height<150)return;
   const range=selection.getRangeAt(0).cloneRange();range.setStart(selection.focusNode!,selection.focusOffset);range.collapse(true);
   let caret=range.getBoundingClientRect?.();
   if(!caret?.height&&range.startContainer.nodeType===Node.TEXT_NODE&&range.startOffset){range.setStart(range.startContainer,range.startOffset-1);caret=range.getBoundingClientRect?.();}
   const safe=canvasSafeArea(area),full=editor.getBoundingClientRect();if(!full.width&&!caret?.height)return;
   const target=full.width<=safe.width-24&&full.height<=safe.height-24?full:caret?.height?caret:full;
   let rect={x:target.left-box.left,y:target.top-box.top,width:Math.max(2,target.width),height:Math.max(18,target.height)};
   // Keep the caret out of the floating format surface too, including during color/font changes.
   const tools=area.querySelector<HTMLElement>('.kc-inline-title .kc-title-format')?.getBoundingClientRect();
   if(tools&&rect.x<tools.right-box.left&&rect.x+rect.width>tools.left-box.left&&rect.y<tools.bottom-box.top&&rect.y+rect.height>tools.top-box.top){
    const above=tools.top-box.top-safe.y,below=safe.y+safe.height-(tools.bottom-box.top);
    const room=above>=below?{...safe,height:Math.max(0,above-8)}:{...safe,y:tools.bottom-box.top+8,height:Math.max(0,below-8)};
    rect={...rect,width:Math.min(rect.width,room.width-24)};const next=revealScreenRect(view.current,rect,room);if(next!==view.current)update(next);return;
   }
   const next=revealScreenRect(view.current,rect,safe);if(next!==view.current)update(next);
  };
  const schedule=()=>{if(frame===null)frame=requestAnimationFrame(measure);};
  const resize=typeof ResizeObserver==='undefined'?null:new ResizeObserver(schedule);resize?.observe(editor);resize?.observe(area);
  document.addEventListener('selectionchange',schedule);editor.addEventListener('input',schedule);editor.addEventListener('compositionend',schedule);schedule();
  return()=>{resize?.disconnect();document.removeEventListener('selectionchange',schedule);editor.removeEventListener('input',schedule);editor.removeEventListener('compositionend',schedule);if(frame!==null)cancelAnimationFrame(frame);};
 },[viewport,enabled,identity,view,update,composing,change]);
}
