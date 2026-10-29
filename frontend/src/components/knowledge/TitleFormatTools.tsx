import type {CSSProperties,Ref} from 'react';
import type {TitleFormat,TitleRun} from '../../services/knowledge';
import {selectionValue} from './titleEditing';
import type {TextSelection,TextMark} from './titleEditing';
export function TitleFormatTools({hidden=false,surfaceRef,surfaceStyle,text,format,selection,baseStyle={},pendingMarks={},onFormat,onAlign,onClear,onUndo,onRedo,canUndo,canRedo}:{hidden?:boolean;surfaceRef?:Ref<HTMLDivElement>;surfaceStyle?:CSSProperties;text:string;format?:TitleFormat;selection:TextSelection;baseStyle?:CSSProperties;pendingMarks?:Omit<Partial<TitleRun>,'text'>;onFormat:(patch:Omit<Partial<TitleRun>,'text'>)=>void;onAlign:(align:TitleFormat['align'])=>void;onClear:()=>void;onUndo:()=>void;onRedo:()=>void;canUndo:boolean;canRedo:boolean}) {
 const value=<K extends TextMark>(key:K)=>(selection.start===selection.end?pendingMarks[key]:undefined)??selectionValue(text,format,selection,baseStyle,key);
 const bold=value('bold'),italic=value('italic'),color=value('color'),size=value('font_size');
 return <div hidden={hidden} ref={surfaceRef} style={surfaceStyle} className="kc-title-format" role="toolbar" aria-label="标题文字格式" onPointerDown={e=>{if((e.target as HTMLElement).closest('button'))e.preventDefault();}}>
 <button type="button" aria-label="选中文字加粗" aria-pressed={bold==='mixed'?'mixed':!!bold} onClick={()=>onFormat({bold:bold!==true})}><b>B</b></button><button type="button" aria-label="选中文字斜体" aria-pressed={italic==='mixed'?'mixed':!!italic} onClick={()=>onFormat({italic:italic!==true})}><i>I</i></button>
 <label>文字颜色{color==='mixed'&&<span>混合</span>}<input aria-label="选中文字颜色" type="color" value={color==='mixed'?'#202938':String(color)} onChange={e=>onFormat({color:e.target.value})} onInput={e=>onFormat({color:e.currentTarget.value})}/></label>
 <label>字号<select aria-label="选中文字字号" value={size==='mixed'?'':size} onChange={e=>onFormat({font_size:Number(e.target.value)})}><option value="" disabled>混合</option>{Array.from({length:39},(_,i)=>i+10).map(n=><option key={n}>{n}</option>)}</select></label>
 {(['left','center','right'] as const).map((align,i)=><button type="button" key={align} aria-label={['标题左对齐','标题居中','标题右对齐'][i]} aria-pressed={(format?.align??'left')===align} onClick={()=>onAlign(align)}>{['左','中','右'][i]}</button>)}
 <button type="button" aria-label="清除文字格式" title={selection.start!==selection.end?'清除选中文字格式，恢复继承':'恢复整个标题的默认格式和对齐'} onClick={onClear}>清除格式</button>
 <button type="button" aria-label="撤销标题编辑" disabled={!canUndo} onClick={onUndo}>↶</button><button type="button" aria-label="重做标题编辑" disabled={!canRedo} onClick={onRedo}>↷</button>
 </div>;
}
