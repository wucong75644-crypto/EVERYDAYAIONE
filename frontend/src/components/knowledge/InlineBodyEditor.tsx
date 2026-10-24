import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import type {CSSProperties} from 'react';
import {X} from 'lucide-react';
import type {KnowledgeObject} from '../../services/knowledge';

/** The parent retains the durable draft and reconciles only acknowledged content. */
export function InlineBodyEditor({object,base,pending,error,unknown,style,onChange,onSave,onClose,onCheck,onReveal}:{
 object:KnowledgeObject;base:string;pending:boolean;error:string;unknown:boolean;style:CSSProperties;
 onReveal?:()=>void;onChange:(body:string)=>void;onSave:()=>Promise<void>;onClose:()=>void;onCheck:()=>void;
}) {
 const [closing,setClosing]=useState(false),[composing,setComposing]=useState(false);
 const panel=useRef<HTMLDivElement>(null),input=useRef<HTMLTextAreaElement>(null);
 const actions=useRef({onSave,onClose,onReveal});
 useLayoutEffect(()=>{actions.current={onSave,onClose,onReveal};});
 const dirty=object.can_write&&object.body!==base;
 useEffect(()=>{actions.current.onReveal?.();input.current?.focus({preventScroll:true});},[]);
 useEffect(()=>{
  if(pending||composing||error||unknown)return;
  if(closing&&!dirty){actions.current.onClose();return;}
  if(!dirty)return;
  const timer=window.setTimeout(()=>void actions.current.onSave(),closing?0:500);
  return ()=>window.clearTimeout(timer);
 },[object.body,object.revision,dirty,pending,composing,error,unknown,closing]);
 useEffect(()=>{
  const outside=(e:PointerEvent)=>{const target=e.target;if(!(target instanceof Element)||panel.current?.contains(target))return;
   if(target.matches('.kc-workspace,.kc-world,.kc-edges'))setClosing(true);
  };
  const escape=(e:KeyboardEvent)=>{if(e.key==='Escape'&&!e.isComposing&&(!(e.target instanceof Element)||!e.target.closest('[role=dialog]')||panel.current?.contains(e.target))){e.preventDefault();e.stopPropagation();setClosing(true);}};
  document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape,true);
  return ()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape,true);};
 },[]);
 return <div ref={panel} role="dialog" aria-label="编辑内容" aria-modal="false" data-state="open" className="kc-inline-body" style={{...style,...(closing&&!error?{visibility:'hidden',pointerEvents:'none'}:{})}} onPointerDown={e=>e.stopPropagation()} onDoubleClick={e=>e.stopPropagation()} onKeyDown={e=>e.stopPropagation()}>
  <button type="button" className="kc-inline-close" aria-label="关闭" title="关闭" onClick={()=>setClosing(true)}><X aria-hidden="true" size={16}/></button>
  <textarea ref={input} aria-label="正文" maxLength={200000} readOnly={!object.can_write} value={object.body} onCompositionStart={()=>setComposing(true)} onCompositionEnd={()=>setComposing(false)} onChange={e=>onChange(e.target.value)} onBlur={()=>{if(dirty&&!pending&&!composing&&!error&&!unknown)void onSave();}}/>
  {error&&<div className="kc-inline-save-error"><p role="alert">{error}</p>{object.can_write&&<button type="button" disabled={pending} onClick={()=>unknown?onCheck():void onSave()}>{unknown?'核对保存结果':'重试保存'}</button>}</div>}
 </div>;
}
