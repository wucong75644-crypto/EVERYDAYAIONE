import {useLayoutEffect,useState} from 'react';
import type {RefObject} from 'react';
import {canvasObstacles} from './canvasOcclusion';
/** Rerender placement when wrapping, messages or workspace dimensions change. */
export function useCanvasOcclusion(viewport:RefObject<HTMLElement|null>,identity:string,ready:boolean){
 const [stamp,setStamp]=useState('');
 useLayoutEffect(()=>{
  const area=viewport.current;if(!area||!ready)return;let queued=false,alive=true;
  const measure=()=>{queued=false;if(!alive)return;const r=area.getBoundingClientRect(),next=JSON.stringify([r.width,r.height,canvasObstacles(area)]);setStamp(old=>old===next?old:next);};
  const schedule=()=>{if(!queued){queued=true;queueMicrotask(measure);}};
  const resize=typeof ResizeObserver==='undefined'?null:new ResizeObserver(schedule);
  const observe=()=>{resize?.disconnect();resize?.observe(area);area.querySelectorAll<HTMLElement>('.kc-canvas-tools,.kc-zoom,.kc-location,.kc-feedback,.kc-feedback>*,.kc-group-tools,.kc-hint').forEach(el=>resize?.observe(el));schedule();};
  const mutation=new MutationObserver(observe);mutation.observe(area,{childList:true,subtree:true});observe();window.addEventListener('resize',schedule);
  return()=>{resize?.disconnect();mutation.disconnect();window.removeEventListener('resize',schedule);alive=false;};
 },[viewport,identity,ready]);
 return stamp;
}
