import {useDerivedDirectory} from './useDerivedDirectory';
import {useEffect,useRef,useState} from 'react';
import {ApiRequestError} from '../../services/api';
import {knowledgeApi} from '../../services/knowledge';
import {useAuthStore} from '../../stores/useAuthStore';

const statusText:Record<string,string>={queued:'资料整理中…',processing:'资料整理中…',failed:'资料整理失败，原文件仍可查看',blocked:'资料整理未完成',blocked_permission:'资料权限已变化',partial:'部分资料尚未完成解析',disabled:'自动整理尚未启用'};

function ManualSection({objectId,sectionId,text,revision,onDone}:{objectId:string;sectionId:string;text:string;revision:number;onDone:()=>void}){
 const uid=useAuthStore(s=>s.user?.id),org=useAuthStore(s=>s.currentOrgId);const key='kc-section:'+uid+':'+org+':'+objectId+':'+sectionId;
 const [editing,setEditing]=useState(false),[value,setValue]=useState(text),[error,setError]=useState(''),[pending,setPending]=useState(false);
 const attempt=useRef<{id:string;text:string;revision:number}|null>(null);
 useEffect(()=>{try{const raw=localStorage.getItem(key);if(raw){const saved=JSON.parse(raw);if(!saved.savedAt||Date.now()-saved.savedAt>7*86400000){localStorage.removeItem(key);return;}if(typeof saved.text!=='string'||saved.text.length>12000)throw new Error('invalid draft');setValue(saved.text);attempt.current=saved.attempt;setEditing(true);}}catch{setError('无法恢复人工更正草稿');}},[key]);
 const save=async()=>{const op=attempt.current??{id:crypto.randomUUID(),text:value,revision};attempt.current=op;setPending(true);setError('');
  try{localStorage.setItem(key,JSON.stringify({text:value,attempt:op,savedAt:Date.now()}));await knowledgeApi.override(objectId,sectionId,{text:op.text},op.revision,op.id);attempt.current=null;localStorage.removeItem(key);setEditing(false);onDone();}catch(e){if(e instanceof ApiRequestError && e.transport==='http' && e.status===409){attempt.current=null;localStorage.setItem(key,JSON.stringify({text:value,attempt:null,savedAt:Date.now()}));onDone();setError('资料版本已变化，已保留你的更正。请核对最新资料后再次保存。');}else{setError(e instanceof Error?e.message:'保存失败，请重试核对');}}finally{setPending(false);}};
 return editing?<div className="kc-derived-edit"><textarea aria-label="人工更正" value={value} readOnly={pending||!!attempt.current} onChange={e=>{setValue(e.target.value);try{localStorage.setItem(key,JSON.stringify({text:e.target.value,attempt:null,savedAt:Date.now()}));}catch{setError('草稿无法持久保存，请先完成保存');}}}/>{error&&<p role="alert">{error}</p>}<button type="button" disabled={pending} onClick={()=>void save()}>{attempt.current?'重试核对':'保存更正'}</button>{!attempt.current&&<button type="button" onClick={()=>{localStorage.removeItem(key);setEditing(false);}}>取消</button>}</div>:<button type="button" onClick={()=>setEditing(true)}>修改此段</button>;
}
export function DerivedDirectory({objectId,writable}:{objectId:string;writable:boolean}){
 const {data,error,refresh}=useDerivedDirectory(objectId);
 if(!data&&!error)return null;
 return <section className="kc-derived-directory" aria-label="自动目录资料">{error&&<p role="alert">{error}</p>}{data&&statusText[data.status]&&<small role="status">{statusText[data.status]}</small>}{data?.sections.map(section=><section key={section.id}><h4>{section.title}{section.manual?' · 人工更正':''}</h4><p>{section.text}</p>{section.source_refs.map(ref=>data.source_links?.[ref]&&<a key={ref} href={data.source_links[ref]}>查看来源</a>)}{writable&&<ManualSection objectId={objectId} sectionId={section.id} text={section.text} revision={data.override_revisions?.[section.id]??0} onDone={refresh}/>}</section>)}</section>;
}
export function DerivedTags({objectId,writable}:{objectId:string;writable:boolean}){
 const {data,error,refresh}=useDerivedDirectory(objectId);const [failure,setFailure]=useState(''),[busy,setBusy]=useState(false);const attempt=useRef<{tag:string;id:string;revision:number}|null>(null);
 const hide=async(tag:string)=>{const op=attempt.current??{tag,id:crypto.randomUUID(),revision:data?.override_revisions?.['tag:'+tag]??0};attempt.current=op;setBusy(true);try{await knowledgeApi.override(objectId,'tag:'+op.tag,{hidden:true},op.revision,op.id);attempt.current=null;setFailure('');refresh();}catch(e){setFailure(e instanceof Error?e.message:'标签更新失败');}finally{setBusy(false);}};
 return <div className="kc-derived-tags" aria-label="自动标签">{(error||failure)&&<p role="alert">{error||failure}</p>}{data?.auto_tags?.map(tag=><span key={tag}>{tag}{writable&&<button type="button" aria-label={'隐藏自动标签 '+tag} disabled={busy||!!attempt.current} onClick={()=>void hide(tag)}>×</button>}</span>)}{failure&&attempt.current&&<button type="button" disabled={busy} onClick={()=>void hide(attempt.current!.tag)}>重试核对</button>}</div>;
}
