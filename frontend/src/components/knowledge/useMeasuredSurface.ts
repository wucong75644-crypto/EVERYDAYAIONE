import {useCallback,useRef,useState} from 'react';
/** Observe CSS pixels, independent of canvas zoom and OS display scaling. */
export function useMeasuredSurface(){
 const [size,setSize]=useState({width:250,height:44,areaWidth:0,areaHeight:0});
 const observer=useRef<ResizeObserver|null>(null);
 const ref=useCallback((element:HTMLDivElement|null)=>{
  observer.current?.disconnect();observer.current=null;
  if(!element)return;
  const measure=()=>{const width=element.offsetWidth,height=element.offsetHeight,area=element.closest('.kc-workspace'),areaWidth=area?.clientWidth??0,areaHeight=area?.clientHeight??0;if(width&&height)setSize(previous=>previous.width===width&&previous.height===height&&previous.areaWidth===areaWidth&&previous.areaHeight===areaHeight?previous:{width,height,areaWidth,areaHeight});};
  measure();if(typeof ResizeObserver!=='undefined'){observer.current=new ResizeObserver(measure);observer.current.observe(element);const area=element.closest('.kc-workspace');if(area)observer.current.observe(area);}
 },[]);
 return {ref,size};
}
