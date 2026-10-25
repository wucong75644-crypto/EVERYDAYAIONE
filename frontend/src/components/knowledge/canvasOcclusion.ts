import {fitView} from './viewportGestures';
import type {CanvasView} from './viewportGestures';
export type SurfaceRect={x:number;y:number;width:number;height:number};
const surfaces='.kc-canvas-tools,.kc-zoom,.kc-location,.kc-feedback>*,.kc-group-tools,.kc-hint';
/** Local screen pixels, never world coordinates. Ignore empty/hidden surfaces. */
export function canvasObstacles(area:HTMLElement){
 const box=area.getBoundingClientRect();
 return [...area.querySelectorAll<HTMLElement>(surfaces)].flatMap(el=>{
  const r=el.getBoundingClientRect();if(!r.width||!r.height||getComputedStyle(el).display==='none')return [];
  return [{x:r.left-box.left,y:r.top-box.top,width:r.width,height:r.height}];
 });
}
export function usableCanvas(box:{width:number;height:number},obstacles:SurfaceRect[]):SurfaceRect{
 let top=0,bottom=box.height;
 for(const r of obstacles){if(r.y+r.height<=0||r.y>=box.height)continue;if(r.y<box.height/2)top=Math.max(top,r.y+r.height+12);else bottom=Math.min(bottom,r.y-12);}
 return {x:0,y:Math.min(top,box.height),width:box.width,height:Math.max(0,bottom-top)};
}
export function canvasSafeArea(area:HTMLElement){return usableCanvas(area.getBoundingClientRect(),canvasObstacles(area));}
export function fitSafeView(cards:SurfaceRect[],safe:SurfaceRect){const fit=fitView(cards,safe);return fit?{...fit,x:fit.x+safe.x,y:fit.y+safe.y}:null;}
export function revealScreenRect(view:CanvasView,rect:SurfaceRect,safe:SurfaceRect):CanvasView{
 if(safe.width<40||safe.height<40)return view;
 const move=(a:number,size:number,start:number,length:number)=>size>length?start-a:a<start?start-a:a+size>start+length?start+length-a-size:0;
 const x=move(rect.x,rect.width,safe.x+12,safe.width-24),y=move(rect.y,rect.height,safe.y+12,safe.height-24);
 return x||y?{...view,x:view.x+x,y:view.y+y}:view;
}
