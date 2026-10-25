import type {CSSProperties} from 'react';
import type {TitleFormat,TitleRun} from '../../services/knowledge';
import {runsFor,runStyle,editRuns,formatRange} from './richTitle';
export type TextSelection={start:number;end:number;backward?:boolean};
export type TitleDocument={text:string;format?:TitleFormat;selection:TextSelection};
export type TextMark='bold'|'italic'|'color'|'font_size';
const marks:TextMark[]=['bold','italic','color','font_size'];
export function effectiveTextStyle(base:CSSProperties={}):Required<Omit<TitleRun,'text'>> {return {bold:base.fontWeight==='bold'||base.fontWeight==='bolder'||Number(base.fontWeight??400)>=600,italic:base.fontStyle==='italic',color:cssTextColor(String(base.color??'#202938')),font_size:Number.parseFloat(String(base.fontSize??15))||15};}
export function selectionValue<K extends TextMark>(text:string,format:TitleFormat|undefined,range:TextSelection,base:CSSProperties,key:K):TitleRun[K]|'mixed' {
 const inherited=effectiveTextStyle(base);let offset=0;
 const at=range.start===range.end?Math.max(0,range.start-1):range.start,end=range.start===range.end?Math.max(1,range.start):range.end;
 const selected=runsFor(text,format).filter(r=>{const start=offset;offset+=r.text.length;return start<end&&offset>at;});
 const values=selected.map(r=>r[key]??inherited[key]);return values.length&&values.some(v=>v!==values[0])?'mixed':(values[0]??inherited[key]) as TitleRun[K];
}
export function clearTextMarks(text:string,format:TitleFormat|undefined,range:TextSelection,keys:TextMark[]=marks):TitleFormat {
 let offset=0;const runs=runsFor(text,format).flatMap(r=>Array.from(r.text).map(text=>{const start=offset;offset+=text.length;const item={...r,text};if(start<range.end&&offset>range.start)for(const key of keys)delete item[key];return item;}));
 return editRuns(text,text,{runs,align:format?.align??'left'});
}
export function formatSelection(doc:TitleDocument,patch:Omit<Partial<TitleRun>,'text'>):TitleDocument {return {...doc,format:formatRange(doc.text,doc.format,doc.selection.start,doc.selection.end,patch)};}
export function sameDocument(a:Pick<TitleDocument,'text'|'format'>,b:Pick<TitleDocument,'text'|'format'>){return a.text===b.text&&JSON.stringify(a.format)===JSON.stringify(b.format);}
/** One composition, paste or format action is one step; adjacent typing/deletion coalesces. */
export class TitleEditHistory {
 past:TitleDocument[]=[];future:TitleDocument[]=[];last:{kind:string;at:number;after:TitleDocument}|null=null;
 push(before:TitleDocument,after:TitleDocument,kind:string,at=Date.now()) {
  if(sameDocument(before,after))return;
  const merge=['insertText','deleteContentBackward','deleteContentForward'].includes(kind)&&this.last?.kind===kind&&at-this.last.at<1000&&sameDocument(this.last.after,before)&&before.selection.start===before.selection.end&&this.last.after.selection.start===before.selection.start&&this.last.after.selection.end===before.selection.end;
  if(!merge)this.past=[...this.past,structuredClone(before)].slice(-100);
  this.future=[];this.last={kind,at,after:structuredClone(after)};
 }
 break(){this.last=null;}
 undo(current:TitleDocument){const previous=this.past.pop();if(!previous)return;this.future.push(structuredClone(current));this.break();return previous;}
 redo(current:TitleDocument){const next=this.future.pop();if(!next)return;this.past.push(structuredClone(current));this.break();return next;}
}
export function readTitleDOM(root:HTMLElement):string {
 const visit=(node:Node):string=>{if(node.nodeType===Node.TEXT_NODE)return node.textContent??'';if(!(node instanceof HTMLElement)||node.dataset.titlePlaceholder)return '';if(node.tagName==='BR')return '\n';let text='';for(const child of node.childNodes){if(child instanceof HTMLElement&&['DIV','P'].includes(child.tagName)&&text&&!text.endsWith('\n'))text+='\n';text+=visit(child);}return text;};return visit(root).replace(/\u00a0/g,' ');
}
export function titleSelection(root:HTMLElement,fallback:TextSelection={start:0,end:0}):TextSelection {
 const s=window.getSelection();if(!s?.anchorNode||!s.focusNode||!root.contains(s.anchorNode)||!root.contains(s.focusNode))return fallback;
 const offset=(node:Node,n:number)=>{const r=document.createRange();r.selectNodeContents(root);r.setEnd(node,n);const host=document.createElement('div');host.append(r.cloneContents());return readTitleDOM(host).length;};
 const a=offset(s.anchorNode,s.anchorOffset),b=offset(s.focusNode,s.focusOffset);return {start:Math.min(a,b),end:Math.max(a,b),backward:a>b};
}
export function restoreTitleSelection(root:HTMLElement,selection:TextSelection) {
 const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);const texts:Text[]=[];let node;while((node=walker.nextNode()))texts.push(node as Text);
 const point=(wanted:number):[Node,number]=>{let offset=0;for(const text of texts){const length=text.data.length;if(offset+length>=wanted)return [text,Math.max(0,wanted-offset)];offset+=length;}return [root,root.childNodes.length];};
 const [a,ao]=point(selection.start),[b,bo]=point(selection.end);const s=window.getSelection();if(s?.setBaseAndExtent){if(selection.backward)s.setBaseAndExtent(b,bo,a,ao);else s.setBaseAndExtent(a,ao,b,bo);}else{const range=document.createRange();range.setStart(a,ao);range.setEnd(b,bo);s?.removeAllRanges();s?.addRange(range);}
}
export function paintTitleEditor(root:HTMLElement,text:string,format?:TitleFormat){root.replaceChildren(...runsFor(text,format).map(r=>{const span=document.createElement('span');span.textContent=r.text;Object.assign(span.style,runStyle(r),{fontSize:r.font_size?`${r.font_size}px`:''});return span;}));if(!text||text.endsWith('\n')){const br=document.createElement('br');br.dataset.titlePlaceholder='true';root.append(br);}}
export function limitTitle(text:string){let result='';for(const char of text){if(result.length+char.length>200)break;result+=char;}return result;}
export function editedTitle(before:TitleDocument,text:string,selection:TextSelection):TitleDocument {const limited=limitTitle(text);return {text:limited,format:before.format?editRuns(before.text,limited,before.format):undefined,selection:{...selection,start:Math.min(selection.start,limited.length),end:Math.min(selection.end,limited.length)}};}

export function cssTextColor(color:string|undefined){if(/^#[0-9a-f]{6}$/i.test(color??''))return color!;const rgb=color?.match(/^rgba?\(\s*(\d+)\D+(\d+)\D+(\d+)/i);return rgb?'#'+rgb.slice(1,4).map(v=>Math.max(0,Math.min(255,Number(v))).toString(16).padStart(2,'0')).join(''):'#202938';}
export function displayedTitleStyle(element:HTMLElement|null):CSSProperties {if(!element)return {fontSize:15,fontWeight:400,color:'#202938'};const s=getComputedStyle(element);return {fontSize:Number.parseFloat(s.fontSize)||15,fontWeight:s.fontWeight==='bold'?700:Number.parseInt(s.fontWeight)||400,fontStyle:s.fontStyle||'normal',fontFamily:s.fontFamily||'inherit',color:cssTextColor(s.color),textDecoration:s.textDecorationLine||'none'};}
