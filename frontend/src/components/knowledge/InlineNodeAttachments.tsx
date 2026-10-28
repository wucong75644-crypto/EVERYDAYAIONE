import {Suspense,useEffect,useLayoutEffect,useMemo,useRef,useState} from 'react';
import {knowledgeFilesApi} from '../../services/knowledge';
import type {KnowledgeNode,KnowledgeFileVersion,KnowledgeFile} from '../../services/knowledge';
import {resolveAdapter} from '../../preview/registry';
import {InlinePreviewContext} from '../../preview/InlinePreviewContext';

function InlineFile({objectId,version,onOpen,onSize}:{objectId:string;version:KnowledgeFileVersion;onOpen:()=>void;onSize:()=>void}){
 const [resource,setResource]=useState<{key:string;url:string;error:string}>({key:'',url:'',error:''}),[retry,setRetry]=useState(0);
 const open=useRef(onOpen);useLayoutEffect(()=>{open.current=onOpen;},[onOpen]);
 const context=useMemo(()=>({onOpen:()=>open.current()}),[]);
 const key=objectId+':'+version.binding_id+':'+version.revision;
 useEffect(()=>{const c=new AbortController();let url='';void knowledgeFilesApi.content(objectId,version.binding_id,version.revision,c.signal).then(blob=>{if(c.signal.aborted)return;url=URL.createObjectURL(blob);setResource({key,url,error:''});}).catch(e=>{if(!c.signal.aborted)setResource({key,url:'',error:e instanceof Error?e.message:'文件读取失败'});});return()=>{c.abort();if(url)URL.revokeObjectURL(url);};},[objectId,version.binding_id,version.revision,key,retry]);
 const url=resource.key===key?resource.url:'',error=resource.key===key?resource.error:'';
 const item=useMemo(()=>({url,filename:version.name,mimeType:version.mime_type,size:version.size}),[url,version.name,version.mime_type,version.size]),adapter=resolveAdapter(item);
 if(error)return <div className="kc-inline-file-error"><span>{version.name}</span><p role="alert">{error}</p><button onClick={()=>setRetry(n=>n+1)}>重试读取</button></div>;
 if(!url)return <p className="kc-inline-file-loading" role="status">正在加载 {version.name}…</p>;
 if(version.mime_type.startsWith('image/'))return <button className="kc-inline-image" aria-label={'查看图片 '+version.name} onClick={onOpen}><img src={url} alt={version.name} onLoad={onSize}/></button>;
 if(version.mime_type.startsWith('video/'))return <figure className="kc-inline-video"><video controls preload="metadata" src={url} aria-label={version.name} onLoadedMetadata={onSize}/><figcaption>{version.name}<button onClick={onOpen} aria-label={'放大预览 '+version.name}>↗</button></figcaption></figure>;
 if(version.mime_type.startsWith('audio/'))return <audio controls preload="metadata" src={url} aria-label={version.name}/>;
 if(!adapter)return <p>{version.name} <button onClick={onOpen}>预览文件</button></p>;
 const Component=adapter.Component;
 return <InlinePreviewContext.Provider value={context}><Suspense fallback={<p role="status">正在加载预览…</p>}><Component item={item} siblings={[item]} index={0} onNavigate={()=>{}} onClose={()=>{}}/></Suspense></InlinePreviewContext.Provider>;
}

export function InlineNodeAttachments({node,onPreview,onSize}:{node:KnowledgeNode;onPreview:(version:KnowledgeFileVersion)=>void;onSize:()=>void}){
 const host=useRef<HTMLDivElement>(null),[visible,setVisible]=useState(typeof IntersectionObserver==='undefined');
 useEffect(()=>{if(!host.current||typeof ResizeObserver==='undefined')return;const observer=new ResizeObserver(onSize);observer.observe(host.current);return()=>observer.disconnect();},[onSize]);
 const signature=(node.attachments?.items??[]).map(f=>f.id+':'+f.current_revision).join(',')+':'+node.attachments?.total+':'+node.canvas_data?.video?.revision;
 const [listing,setListing]=useState<{key:string;items:KnowledgeFile[];error:string}>({key:'',items:[],error:''});
 const key=node.id+':'+signature;
 useEffect(()=>{if(!host.current||typeof IntersectionObserver==='undefined')return;const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){setVisible(true);observer.disconnect();}},{rootMargin:'300px'});observer.observe(host.current);return()=>observer.disconnect();},[]);
 useEffect(()=>{if(!visible||!node.attachments?.total)return;const c=new AbortController();if(node.attachments.total<=(node.attachments.items?.length??0)){return;}void knowledgeFilesApi.list(node.id,c.signal).then(items=>{if(!c.signal.aborted)setListing({key,items,error:''});}).catch(e=>{if(!c.signal.aborted)setListing({key,items:[],error:e instanceof Error?e.message:'附件无法读取'});});return()=>c.abort();},[node.id,key,visible,node.attachments]); // The signature fences the immutable versions in the captured scene.
 const items=listing.key===key?listing.items:node.attachments?.items??[];
 const files=items.filter(file=>file.id!==node.canvas_data?.image?.binding_id&&!file.version.name.toLowerCase().endsWith('.xmind'));
 return <div ref={host} className="kc-inline-attachments" onPointerDown={e=>e.stopPropagation()} onDoubleClick={e=>e.stopPropagation()} onKeyDown={e=>e.stopPropagation()} onWheel={e=>e.stopPropagation()}>{listing.key===key&&listing.error&&<p role="alert">{listing.error}</p>}{!visible&&files.length>0&&<p className="kc-inline-file-loading">附件待预览</p>}{visible&&files.map(file=>{const version=node.canvas_data?.video?.binding_id===file.id?{...file.version,revision:node.canvas_data.video.revision}:file.version;return <InlineFile key={file.id} objectId={node.id} version={version} onOpen={()=>onPreview(version)} onSize={onSize}/>;})}</div>;
}
