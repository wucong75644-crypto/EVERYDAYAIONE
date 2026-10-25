import type { CSSProperties } from 'react';
import type { TitleFormat, TitleRun } from '../../services/knowledge';
export const plainFormat = ():TitleFormat => ({runs:[],align:'left'});
export function runsFor(text:string, format?:TitleFormat):TitleRun[] {
  return format?.runs.length && format.runs.map(r=>r.text).join('')===text ? format.runs : text?[{text}]:[];
}
export function runStyle(run:TitleRun):CSSProperties {return {fontWeight:run.bold===undefined?undefined:run.bold?700:400,fontStyle:run.italic===undefined?undefined:run.italic?'italic':'normal',color:run.color,fontSize:run.font_size};}
function chars(text:string,format?:TitleFormat){return runsFor(text,format).flatMap(r=>Array.from(r.text).map(text=>({...r,text})));}
function pack(items:TitleRun[],align:TitleFormat['align']):TitleFormat {
 const runs:TitleRun[]=[];for(const item of items){const last=runs.at(-1);const style=({...item,text:''});if(last&&JSON.stringify({...last,text:''})===JSON.stringify(style))last.text+=item.text;else runs.push({...item});}return {runs,align};
}
export function formatRange(text:string,format:TitleFormat|undefined,start:number,end:number,patch:Omit<Partial<TitleRun>,'text'>):TitleFormat {
 let offset=0;return pack(chars(text,format).map(r=>{const at=offset;offset+=r.text.length;return at<end&&offset>start?{...r,...patch}:r;}),format?.align??'left');
}
export function editRuns(before:string,after:string,format?:TitleFormat):TitleFormat {
 const a=Array.from(before),b=Array.from(after);let head=0,tail=0;
 while(head<a.length&&head<b.length&&a[head]===b[head])head++;
 while(tail<a.length-head&&tail<b.length-head&&a[a.length-1-tail]===b[b.length-1-tail])tail++;
 const old=chars(before,format),inherit=old[Math.max(0,head-1)]??{text:''};
 return pack([...old.slice(0,head),...b.slice(head,b.length-tail).map(text=>({...inherit,text})),...old.slice(a.length-tail)],format?.align??'left');
}
export function paintTitle(el:HTMLElement,text:string,format?:TitleFormat){el.replaceChildren(...runsFor(text,format).map(r=>{const span=document.createElement('span');span.textContent=r.text;Object.assign(span.style,runStyle(r),{fontSize:r.font_size?`${r.font_size}px`:''});return span;}));el.style.textAlign=format?.align??'left';}

export function rangeActive(text:string,format:TitleFormat|undefined,start:number,end:number,key:'bold'|'italic') {let offset=0;const selected=runsFor(text,format).filter(r=>{const at=offset;offset+=r.text.length;return at<end&&offset>start;});return selected.length>0&&selected.every(r=>r[key]===true);}

export const richFont=(format:TitleFormat|undefined,base=15)=>Math.max(base,...(format?.runs.map(r=>r.font_size??base)??[]));

export function rangeValue<K extends 'color'|'font_size'>(text:string,format:TitleFormat|undefined,start:number,end:number,key:K):TitleRun[K]|undefined {let offset=0;const selected=runsFor(text,format).filter(r=>{const at=offset;offset+=r.text.length;return at<end&&offset>start;});return selected.length&&selected.every(r=>r[key]===selected[0][key])?selected[0][key]:undefined;}
