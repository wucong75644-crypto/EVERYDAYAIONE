import {useEffect,useRef,useState} from 'react';
import type {CSSProperties} from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {PencilLine,X} from 'lucide-react';
import type {KnowledgeObject} from '../../services/knowledge';
import {useDerivedDirectory} from './useDerivedDirectory';
import {TopicWidthHandle} from './TopicWidthHandle';

const statuses:Record<string,string>={queued:'资料整理中',processing:'资料整理中',failed:'资料整理失败，可查看已保存正文',partial:'部分资料尚未完成解析',blocked:'资料整理未完成',blocked_permission:'资料权限已变化',disabled:'自动整理尚未启用'};
export function DirectoryPanel({object,path,style,zoom,inputMode,text,pending,error,unknown,onText,onInputMode,onSubmit,onCheck,onClose}:{
 object:KnowledgeObject;path:string[];style:CSSProperties;zoom:number;inputMode:boolean;text:string;pending:boolean;error:string;unknown:boolean;
 onText:(text:string)=>void;onInputMode:(mode:boolean)=>void;onSubmit:()=>void;onCheck:()=>void;onClose:()=>void;
}){
 const {data,error:loadError}=useDerivedDirectory(object.id);
 const initial={width:Math.max(720,Number(style.width)||860),height:640,dx:0,dy:0};
 const [box,setBox]=useState(initial),base=useRef(box),input=useRef<HTMLTextAreaElement>(null);
 const panel=useRef<HTMLDivElement>(null);
 useEffect(()=>{if(inputMode)input.current?.focus({preventScroll:true});else panel.current?.focus({preventScroll:true});},[inputMode]);
 const sections=data?.sections??[];
 const automatic=<div className="kc-directory-document">{sections.map(section=><section key={section.id}><h3>{section.title}</h3><Markdown remarkPlugins={[remarkGfm]}>{section.text}</Markdown>{section.source_refs.map(ref=>data?.source_links?.[ref]&&<a key={ref} href={data.source_links[ref]}>查看来源</a>)}</section>)}</div>;
 return <div ref={panel} role="dialog" aria-label="目录资料" aria-modal="false" data-state="open" tabIndex={-1} className="kc-inline-body kc-directory-panel" style={{...style,left:Number(style.left??0)+box.dx,top:Number(style.top??0)+box.dy,width:box.width,height:box.height}} onPointerDown={e=>e.stopPropagation()} onDoubleClick={e=>e.stopPropagation()} onWheel={e=>e.stopPropagation()} onKeyDown={e=>{e.stopPropagation();if(e.key==='Escape'&&!pending&&!e.nativeEvent.isComposing&&!(e.target as HTMLElement).closest('.kc-width-handle'))onClose();}}>
  <header className="kc-directory-header"><strong>目录资料</strong><div>{object.can_write&&<button disabled={pending||unknown} onClick={()=>onInputMode(!inputMode)}><PencilLine size={14}/>{inputMode?'返回资料':'补充 / 修改'}</button>}<button aria-label="关闭目录资料" disabled={pending} onClick={onClose}><X size={18}/></button></div></header>
  <div className="kc-directory-scroll">
   <h2>{object.title}</h2><nav aria-label="节点路径" className="kc-directory-path">{path.join(' › ')}</nav>
   {data&&statuses[data.status]&&<p role="status" className="kc-directory-status">{statuses[data.status]}</p>}
   {loadError&&<p role="alert">{loadError}</p>}
   {inputMode&&object.can_write?<form className="kc-content-form" onSubmit={e=>{e.preventDefault();if(unknown)onCheck();else onSubmit();}}><label htmlFor="kc-content-input">补充 / 修改内容</label><p>补充信息或说明要修改的内容，AI 将整理到目录资料中，保留已有标题。</p><textarea ref={input} id="kc-content-input" value={text} maxLength={12000} readOnly={pending||unknown} placeholder="例如：补充导出步骤，或把查询时间改为昨天……" onChange={e=>{if(!pending&&!unknown)onText(e.target.value);}}/><button type="submit" disabled={pending||!text.trim()}>{pending?'AI 正在整理并保存…':unknown?'核对保存结果':'提交给 AI 整理'}</button><small>输入会保留为草稿，提交后才更新资料。</small></form>:<>
    {object.body?<div className="kc-directory-document"><Markdown remarkPlugins={[remarkGfm]}>{object.body}</Markdown></div>:sections.length?automatic:<p className="kc-directory-empty">暂无目录资料。{object.can_write?'可通过“补充 / 修改”输入内容，或添加文件资料。':'等待资料整理后即可查看。'}</p>}
    {object.body&&sections.length>0&&<details className="kc-directory-original"><summary>文件与分支自动整理</summary>{automatic}</details>}
   </>}
   {error&&<div className="kc-inline-save-error"><p role="alert">{error}</p>{unknown&&!inputMode&&<button disabled={pending} onClick={onCheck}>核对保存结果</button>}</div>}
  </div>
  <TopicWidthHandle width={box.width} height={box.height} zoom={zoom} minWidth={480} maxWidth={1400} minHeight={320} maxHeight={1400} measureContent={false} scopeLabel="资料窗口" onPreview={()=>{}} onCommit={()=>{}} onStart={()=>{base.current=box;}} onResizePreview={size=>setBox({...size,dx:base.current.dx+size.dx,dy:base.current.dy+size.dy})} onResizeCommit={size=>setBox(size?{...size,dx:base.current.dx+size.dx,dy:base.current.dy+size.dy}:initial)} onCancel={()=>setBox(base.current)} onEscapeIdle={onClose}/>
 </div>;
}
