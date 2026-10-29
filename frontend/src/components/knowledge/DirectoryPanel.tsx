import {useEffect,useRef,useState} from 'react';
import type {CSSProperties} from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {PencilLine,X} from 'lucide-react';
import type {KnowledgeObject} from '../../services/knowledge';
import {TopicWidthHandle} from './TopicWidthHandle';

export function DirectoryPanel({object,path,style,zoom,inputMode,text,pending,error,unknown,onText,onInputMode,onSubmit,onCheck,onClose}:{
 object:KnowledgeObject;path:string[];style:CSSProperties;zoom:number;inputMode:boolean;text:string;pending:boolean;error:string;unknown:boolean;
 onText:(text:string)=>void;onInputMode:(mode:boolean)=>void;onSubmit:()=>void;onCheck:()=>void;onClose:()=>void;
}){
 const initial={width:Math.max(720,Number(style.width)||860),height:640,dx:0,dy:0};
 const [box,setBox]=useState(initial),base=useRef(box),input=useRef<HTMLTextAreaElement>(null);
 const panel=useRef<HTMLDivElement>(null);
 useEffect(()=>{if(inputMode)input.current?.focus({preventScroll:true});else panel.current?.focus({preventScroll:true});},[inputMode]);
 return <div ref={panel} role="dialog" aria-label="目录资料" aria-modal="false" data-state="open" tabIndex={-1} className="kc-inline-body kc-directory-panel" style={{...style,left:Number(style.left??0)+box.dx,top:Number(style.top??0)+box.dy,width:box.width,height:box.height}} onPointerDown={e=>e.stopPropagation()} onDoubleClick={e=>e.stopPropagation()} onWheel={e=>e.stopPropagation()} onKeyDown={e=>{e.stopPropagation();if(e.key==='Escape'&&!pending&&!e.nativeEvent.isComposing&&!(e.target as HTMLElement).closest('.kc-width-handle'))onClose();}}>
  <header className="kc-directory-header"><strong>目录资料</strong><div>{object.can_write&&<button disabled={pending||unknown} onClick={()=>onInputMode(!inputMode)}><PencilLine size={14}/>{inputMode?'返回资料':'补充 / 修改'}</button>}<button aria-label="关闭目录资料" disabled={pending} onClick={onClose}><X size={18}/></button></div></header>
  <div className="kc-directory-scroll">
   <h2>{object.title}</h2><nav aria-label="节点路径" className="kc-directory-path">{path.join(' › ')}</nav>
   {inputMode&&object.can_write?<form className="kc-content-form" onSubmit={e=>{e.preventDefault();if(unknown)onCheck();else onSubmit();}}><label htmlFor="kc-content-input">补充 / 修改内容</label><p>直接编辑本节点的完整正文，保存后原样保留；不会自动改写或拆分。</p><textarea ref={input} id="kc-content-input" value={text} maxLength={200000} readOnly={pending||unknown} placeholder="输入本节点的正文……" onChange={e=>{if(!pending&&!unknown)onText(e.target.value);}}/><button type="submit" disabled={pending}>{pending?'正在保存…':unknown?'核对保存结果':'保存正文'}</button><small>输入会保留为草稿，提交后才更新资料。</small></form>:<>
    {object.body?<div className="kc-directory-document"><Markdown remarkPlugins={[remarkGfm]}>{object.body}</Markdown></div>:<p className="kc-directory-empty">暂无正文。{object.can_write?'可通过“补充 / 修改”编辑正文，或添加文件资料。':'可查看节点的原始附件。'}</p>}
   </>}
   {error&&<div className="kc-inline-save-error"><p role="alert">{error}</p>{unknown&&!inputMode&&<button disabled={pending} onClick={onCheck}>核对保存结果</button>}</div>}
  </div>
  <TopicWidthHandle width={box.width} height={box.height} zoom={zoom} minWidth={480} maxWidth={1400} minHeight={320} maxHeight={1400} measureContent={false} scopeLabel="资料窗口" onPreview={()=>{}} onCommit={()=>{}} onStart={()=>{base.current=box;}} onResizePreview={size=>setBox({...size,dx:base.current.dx+size.dx,dy:base.current.dy+size.dy})} onResizeCommit={size=>setBox(size?{...size,dx:base.current.dx+size.dx,dy:base.current.dy+size.dy}:initial)} onCancel={()=>setBox(base.current)} onEscapeIdle={onClose}/>
 </div>;
}
