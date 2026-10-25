import type {CanvasData,CanvasStyle} from '../../services/knowledge';
import {clearTextMarks} from './titleEditing';
import type {TextMark} from './titleEditing';
export type StyleSnapshot={style:CanvasStyle|null;align:'left'|'center'|'right'};
export function styleSnapshot(data?:CanvasData):StyleSnapshot{return {style:structuredClone(data?.style??null),align:data?.title_format?.align??'left'};}
export function pasteTopicStyle(text:string,data:CanvasData|undefined,sample:StyleSnapshot):CanvasData{return {...data,style:sample.style,title_format:{...clearTextMarks(text,data?.title_format,{start:0,end:text.length}),align:sample.align}};}
export function editTopicStyle(text:string,data:CanvasData|undefined,style:CanvasStyle|null|undefined,before:CanvasStyle,align?:'left'|'center'|'right',touched:TextMark[]=[]):CanvasData {
 if(style===null){const next={...data,style:null};delete next.title_format;return next;}
 const keys=[...new Set([...(['bold','italic','color','font_size'] as TextMark[]).filter(k=>style?.[k]!==before[k]),...touched])];
 return {...data,style,title_format:{...clearTextMarks(text,data?.title_format,{start:0,end:text.length},keys),align:align??data?.title_format?.align??'left'}};
}
