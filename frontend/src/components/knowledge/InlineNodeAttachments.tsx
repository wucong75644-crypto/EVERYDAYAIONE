import {useEffect,useRef,useState} from 'react';
import {KnowledgeMediaPlayer} from './KnowledgeMediaPlayer';
import {useKnowledgeThumbnail,useNearViewport} from './useKnowledgeThumbnail';
import {FileText} from 'lucide-react';
import {knowledgeFilesApi} from '../../services/knowledge';
import type {KnowledgeNode,KnowledgeFileVersion,KnowledgeFile} from '../../services/knowledge';

function AttachmentImage({objectId,version,onOpen,onSize}:{objectId:string;version:KnowledgeFileVersion;onOpen:()=>void;onSize:()=>void}){
 const {ref,near}=useNearViewport(),image=useKnowledgeThumbnail(objectId,version.binding_id,version.revision,near);
 return <div ref={ref} className="kc-attachment-thumbnail"><button type="button" className="kc-inline-image" aria-label={'查看图片 '+version.name} onClick={onOpen}>{image.src?<img src={image.src} alt={version.name} onLoad={onSize}/>:<span>{image.error?'缩略图无法读取，点击查看原图':version.name}</span>}</button></div>;
}

function FileBadge({version,onOpen}:{version:KnowledgeFileVersion;onOpen:()=>void}){
 const dot=version.name.lastIndexOf('.'),hasExtension=dot>0&&dot<version.name.length-1;
 return <button type="button" className="kc-file-badge" title={version.name} aria-label={'预览文件 '+version.name} onClick={onOpen}><FileText size={16} aria-hidden="true"/><span className="kc-file-name">{hasExtension?version.name.slice(0,dot):version.name}</span>{hasExtension&&<span className="kc-file-extension">{version.name.slice(dot)}</span>}</button>;
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
 return <div ref={host} className="kc-inline-attachments" onPointerDown={e=>e.stopPropagation()} onDoubleClick={e=>e.stopPropagation()} onKeyDown={e=>e.stopPropagation()} onWheel={e=>e.stopPropagation()}>{listing.key===key&&listing.error&&<p role="alert">{listing.error}</p>}{!visible&&files.length>0&&<p className="kc-inline-file-loading">附件待预览</p>}{visible&&files.map(file=>{const version=node.canvas_data?.video?.binding_id===file.id?{...file.version,revision:node.canvas_data.video.revision}:file.version;const onOpen=()=>onPreview(version);return version.mime_type.startsWith('image/')?<AttachmentImage key={file.id} objectId={node.id} version={version} onOpen={onOpen} onSize={onSize}/>:version.mime_type==='video/mp4'?<KnowledgeMediaPlayer key={file.id} objectId={node.id} version={version}/>:<FileBadge key={file.id} version={version} onOpen={onOpen}/>;})}</div>;
}
