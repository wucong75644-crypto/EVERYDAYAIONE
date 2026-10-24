import * as Menu from '@radix-ui/react-dropdown-menu';
import {Paperclip} from 'lucide-react';
import type {FileEntryAction} from './KnowledgeFiles';
import {useEffect,useRef,useState} from 'react';
import {FileText,FileImage,FileVideo,File,Folder,ArrowLeft,X} from 'lucide-react';
import {knowledgeFilesApi} from '../../services/knowledge';
import type {KnowledgeFile,KnowledgeFileVersion,KnowledgeNode} from '../../services/knowledge';
import {listWorkspace} from '../../services/workspace';
import type {WorkspaceFileItem} from '../../services/workspace';
import PreviewHost from '../../preview/PreviewHost';
import type {PreviewItem} from '../../preview/types';
import {Dialog} from '../primitives/Dialog';
export function FileIcon({mime}:{mime:string}){const Icon=mime.startsWith('image/')?FileImage:mime.startsWith('video/')?FileVideo:mime==='application/pdf'||mime.startsWith('text/')?FileText:File;return <Icon size={22} aria-hidden="true"/>;}

export function FileThumbnail({objectId,file}:{objectId:string;file:KnowledgeFile}){
 const host=useRef<HTMLSpanElement>(null),[visible,setVisible]=useState(typeof IntersectionObserver==='undefined'),[loaded,setLoaded]=useState({key:'',src:''});const key=objectId+':'+file.id+':'+file.current_revision,src=loaded.key===key?loaded.src:'';
 useEffect(()=>{if(!host.current)return;if(typeof IntersectionObserver==='undefined')return;const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){setVisible(true);observer.disconnect();}});observer.observe(host.current);return()=>observer.disconnect();},[]);
 useEffect(()=>{if(!visible||!file.version.mime_type.startsWith('image/')||file.version.size>2*1024*1024)return;const c=new AbortController();let url='';void(async()=>{try{const blob=await knowledgeFilesApi.content(objectId,file.id,file.current_revision,c.signal);if(!c.signal.aborted){url=URL.createObjectURL(blob);setLoaded({key,src:url});}}catch{/* Keep the file-type icon if thumbnail reading fails. */}})();return()=>{c.abort();if(url)URL.revokeObjectURL(url);};},[visible,objectId,file.id,file.current_revision,file.version.mime_type,file.version.size,key]);
 return <span ref={host} className="kc-file-thumb">{src?<img src={src} alt=""/>:<FileIcon mime={file.version.mime_type}/>}</span>;
}
export function NodeFileMenu({node,disabled,canWrite,onMore,onAction}:{node:KnowledgeNode;disabled:boolean;canWrite:boolean;onMore:()=>void;onAction:(action:FileEntryAction)=>void}){
 const upload=useRef<HTMLInputElement>(null),selectedAction=useRef(false);
 const choose=(action:()=>void)=>{selectedAction.current=true;action();};
 const content=<><Paperclip size={14} aria-hidden="true"/>文件资料{node.attachments?.total?<span aria-hidden="true" className="kc-file-count">{node.attachments.total}</span>:null}</>;
 if(!canWrite)return <button type="button" aria-label="文件资料" title="文件资料" disabled={disabled} onClick={onMore}>{content}</button>;
 return <span className="kc-node-file-menu"><input hidden type="file" ref={upload} aria-label={'上传文件到 '+node.title} disabled={disabled} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)onAction({kind:'upload',file});}}/><Menu.Root onOpenChange={open=>{if(open)selectedAction.current=false;}}><Menu.Trigger asChild><button type="button" aria-label="文件资料" title="文件资料" disabled={disabled}>{content}</button></Menu.Trigger><Menu.Portal><Menu.Content className="kc-tools-menu kc-node-files-dropdown" side="bottom" align="start" sideOffset={6} avoidCollisions={false} collisionPadding={10} onCloseAutoFocus={e=>{if(selectedAction.current)e.preventDefault();}}><Menu.Item onSelect={()=>choose(()=>upload.current?.click())}>上传文件</Menu.Item><Menu.Item onSelect={()=>choose(()=>onAction({kind:'workspace'}))}>从工作区选择</Menu.Item><Menu.Item onSelect={()=>choose(()=>onAction({kind:'reference'}))}>引用其他节点文件</Menu.Item><Menu.Separator/><Menu.Item onSelect={()=>choose(onMore)}>查看全部附件</Menu.Item></Menu.Content></Menu.Portal></Menu.Root></span>;
}
export function AttachmentSummary({node,onPreview,onMore,onRemove}:{node:KnowledgeNode;onPreview:(v:KnowledgeFileVersion)=>void;onMore:()=>void;onRemove?:(file:KnowledgeFile)=>void}){const data=node.attachments;if(!data?.total)return null;return <div className="kc-attachment-summary" onPointerDown={e=>e.stopPropagation()} onDoubleClick={e=>e.stopPropagation()}>{data.items.map(file=><div className="kc-summary-file" key={file.id}><button title={file.title} onClick={()=>onPreview(file.version)}><FileThumbnail objectId={node.id} file={file}/><span>{file.title}</span></button>{onRemove&&<button type="button" className="kc-remove-affordance" aria-label={'移除附件 '+file.title} title="移除附件" onClick={()=>onRemove(file)}><X size={14}/></button>}</div>)}<button className="kc-attachment-count" onClick={onMore}>共 {data.total} 个附件</button></div>;}

export function BoundFilePreview({objectId,version,onClose}:{objectId:string;version:KnowledgeFileVersion;onClose:()=>void}){
 const [item,setItem]=useState<PreviewItem|null>(null),[error,setError]=useState('');
 useEffect(()=>{const c=new AbortController();let url='';void(async()=>{try{const blob=await knowledgeFilesApi.content(objectId,version.binding_id,version.revision,c.signal);if(c.signal.aborted)return;url=URL.createObjectURL(blob);setItem({url,filename:version.name,mimeType:version.mime_type,size:version.size});}catch(e){if(!c.signal.aborted)setError(e instanceof Error?e.message:'文件无法读取');}})();return()=>{c.abort();if(url)URL.revokeObjectURL(url);};},[objectId,version]);
 return <>{!item&&<Dialog open title="文件预览" onOpenChange={open=>{if(!open)onClose();}}>{error?<p role="alert">{error}</p>:<p role="status">正在读取文件…</p>}</Dialog>}<PreviewHost state={item?{kind:'open',items:[item],index:0}:{kind:'closed'}} onClose={onClose} onIndexChange={()=>{}}/></>;
}
export function WorkspaceFilePicker({disabled,onSelect,onClose}:{disabled:boolean;onSelect:(path:string)=>void;onClose:()=>void}){
 const [path,setPath]=useState('.'),[listing,setListing]=useState<{path:string;items:WorkspaceFileItem[];error:string}>({path:'',items:[],error:''});const loading=listing.path!==path,items=listing.items,error=loading?'':listing.error;
 useEffect(()=>{const c=new AbortController();void listWorkspace(path,c.signal).then(page=>{if(!c.signal.aborted)setListing({path,items:page.items,error:''});}).catch(e=>{if(!c.signal.aborted)setListing({path,items:[],error:e instanceof Error?e.message:'无法读取工作区'});});return()=>c.abort();},[path]);
 const join=(name:string)=>path==='.'?name:path+'/'+name;
 return <section className="kc-workspace-picker" aria-label="从工作区选择"><header><button type="button" aria-label="上一级" disabled={disabled||loading||path==='.'} onClick={()=>setPath(path.includes('/')?path.slice(0,path.lastIndexOf('/')):'.')}><ArrowLeft size={16}/></button><span>{path==='.'?'工作区':path}</span><button type="button" aria-label="关闭文件选择" onClick={onClose}><X size={16}/></button></header>{loading&&<p role="status">正在加载…</p>}{error&&<p role="alert">{error}</p>}{!loading&&!error&&items.length===0&&<p>此文件夹为空</p>}{!loading&&items.map(item=><button type="button" key={item.name} title={item.name} disabled={disabled||(!item.is_dir&&(item.size===0||item.size>20*1024*1024))} onClick={()=>item.is_dir?setPath(join(item.name)):onSelect(join(item.name))}>{item.is_dir?<Folder size={20}/>:<FileIcon mime={item.mime_type??''}/>}<span>{item.name}</span></button>)}<small>选择文件即添加；单文件最多20MB</small></section>;
}
