import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {knowledgeApi} from '../../services/knowledge';
import type {KnowledgeNode,KnowledgeObject} from '../../services/knowledge';

export function InlineNodeBody({node,editing,pending,unknown,error,onEdit,onChange,onSave,onCheck,onCancel,onSize}:{node:KnowledgeNode;editing:KnowledgeObject|null;pending:boolean;unknown:boolean;error:string;onEdit:()=>void;onChange:(body:string)=>void;onSave:()=>void;onCheck:()=>void;onCancel:()=>void;onSize:()=>void}){
 const host=useRef<HTMLDivElement>(null),input=useRef<HTMLTextAreaElement>(null),[source,setSource]=useState({key:'',body:'',error:''});
 const key=node.id+':'+node.revision;
 useEffect(()=>{if(!node.has_body)return;let alive=true;void knowledgeApi.object(node.id).then(object=>{if(alive)setSource({key,body:object.body,error:''});}).catch(e=>{if(alive)setSource({key,body:'',error:e instanceof Error?e.message:'内容无法读取'});});return()=>{alive=false;};},[node.id,node.has_body,key]);
 const editingId=editing?.id;
 useLayoutEffect(()=>{const element=input.current;if(editingId&&element){element.focus({preventScroll:true});element.setSelectionRange(element.value.length,element.value.length);}},[editingId]);
 useEffect(()=>{if(!host.current||typeof ResizeObserver==='undefined')return;const observer=new ResizeObserver(onSize);observer.observe(host.current);return()=>observer.disconnect();},[onSize]);
 let text=source.key===key?source.body:'';
 if(text===node.title)text='';else if(text.startsWith(node.title+'\n\n'))text=text.slice(node.title.length+2);
 if(node.canvas_data?.link&&text.trim()==='外部链接（仅保留入口，未读取链接内容）：\n'+node.canvas_data.link.url)text='';
 const prefix=editing?.body.startsWith(node.title+'\n\n')?node.title+'\n\n':'';
 const draft=editing?.body.slice(prefix.length)??'';
 return <div ref={host} className="kc-inline-node-body" onPointerDown={e=>{if(editing)e.stopPropagation();}} onDoubleClick={e=>{e.stopPropagation();if(!editing)onEdit();}} onKeyDown={e=>{if(!editing)return;e.stopPropagation();if(e.nativeEvent.isComposing||e.repeat)return;if(e.key==='Enter'&&!e.shiftKey&&!e.ctrlKey&&!e.metaKey&&!e.altKey){e.preventDefault();if(unknown)onCheck();else onSave();}else if(e.key==='Escape'){e.preventDefault();onCancel();}}}>
  {editing?<>{pending&&<span className="kc-save-feedback" role="status">正在保存…</span>}<div className="kc-body-edit-surface"><div className="kc-node-source-text kc-body-measure" aria-hidden="true">{draft||'\u200b'}</div><textarea ref={input} aria-label={'编辑节点正文 '+node.title} maxLength={200000} value={draft} readOnly={!editing.can_write||pending||unknown} onChange={e=>{if(editing.can_write&&!pending&&!unknown)onChange(prefix+e.target.value);}} onBlur={()=>{if(!pending&&!unknown&&!error)onSave();}}/></div>{error&&<p role="alert">{error}</p>}{unknown&&<button onClick={onCheck}>核对保存结果</button>}</>:<>{text&&<div className="kc-node-source-text">{text}</div>}{source.key===key&&source.error&&<p role="alert">{source.error}</p>}</>}
 </div>;
}
