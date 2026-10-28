import * as Menu from '@radix-ui/react-dropdown-menu';
import {fileMeta} from './filePresentation';
import {Plus,X,MoreHorizontal,RefreshCw} from 'lucide-react';
import {Dialog} from '../primitives/Dialog';
import {FileThumbnail,WorkspaceFilePicker} from './KnowledgeAttachmentUI';
import { useCanvasSurfaceMotion } from './useCanvasSurfaceMotion';
import { useEffect,useLayoutEffect, useRef, useState } from 'react';
import PreviewHost from '../../preview/PreviewHost';
import { resolveAdapter } from '../../preview/registry';
import type { PreviewItem } from '../../preview/types';
import { knowledgeFilesApi } from '../../services/knowledge';
import type { KnowledgeFile, KnowledgeFileVersion, KnowledgeNode, KnowledgeObject } from '../../services/knowledge';

export type FileEntryAction={kind:'upload';file:File}|{kind:'workspace'|'reference'}|{kind:'remove';binding:KnowledgeFile};
type Attempt = { id: string } & (
  { kind: 'upload'; file: File; binding?: KnowledgeFile } |
  { kind: 'snapshot'; path: string } |
  { kind: 'reference'; source: string; binding: string; revision: number } |
  { kind: 'remove'; binding: KnowledgeFile }
);
const parseStatus:Record<string,string>={disabled:'媒体解析未启用',queued:'等待解析',processing:'正在解析',failed:'解析失败',blocked:'解析失败，可重试',blocked_permission:'权限已变化',unsupported:'暂不支持解析',partial:'部分解析',ready:'正文已解析'};
const textStatus = { ready: '正文可搜索', truncated: '正文部分可搜索', failed: '正文提取失败，可下载原件', unsupported: '无可搜索正文' };
function parseLabel(version:KnowledgeFileVersion){
 if(version.ai_processing_enabled===false)return version.text_status==='ready'?parseStatus.ready:version.text_status==='truncated'?parseStatus.partial:'AI 解析未启用，原件可查看';
 return parseStatus[version.parse_state??'']??textStatus[version.text_status];
}
function errorMessage(error: unknown) { return error instanceof Error ? error.message : '操作失败，请重试'; }

function FileVersions({ objectId, binding, disabled, onRead }: { objectId: string; binding: KnowledgeFile; disabled: boolean; onRead: (v: KnowledgeFileVersion, download: boolean) => void }) {
  const [rows, setRows] = useState<KnowledgeFileVersion[]>([binding.version]);
  const [selected, setSelected] = useState(binding.current_revision);
  const [loading, setLoading] = useState(false); const [cursor, setCursor] = useState<number | null>(null); const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const load = async (before?: number) => {
    controller.current?.abort(); const c = new AbortController(); controller.current = c;
    setLoading(true); setError('');
    try {
      const page = await knowledgeFilesApi.versions(objectId,binding.id,before,c.signal);
      if (!c.signal.aborted) { setRows(old => before ? [...old,...page.items] : page.items); setCursor(page.next_cursor); }
    } catch (e) { if (!c.signal.aborted) { setRows([]); setError(errorMessage(e)); } }
    finally { if (!c.signal.aborted) setLoading(false); }
  };
  const version = rows.find(v => v.revision === selected);
  const adapter = version ? resolveAdapter({ filename: version.name, mimeType: version.mime_type }) : null;
  const previewable = adapter && !['fallback','pptx'].includes(adapter.id);
  return <div className="kc-file-versions">
    <button type="button" disabled={loading || disabled} onClick={() => void load()}>查看文件版本</button>
    <select aria-label={'文件版本 ' + binding.title} disabled={loading || disabled || !rows.length} value={selected} onChange={e => setSelected(Number(e.target.value))}>
      {rows.map(v => <option key={v.revision} value={v.revision}>版本 {v.revision} · {v.name} · {v.sha256.slice(0,8)}</option>)}
    </select>
    {cursor && <button type="button" disabled={loading || disabled} onClick={() => void load(cursor)}>更早文件版本</button>}
    {version && <><small>{textStatus[version.text_status]} · {(version.size/1024).toFixed(1)} KB</small>
      <button type="button" disabled={disabled || !previewable} onClick={() => onRead(version,false)}>预览此版本</button>
      <button type="button" disabled={disabled} onClick={() => onRead(version,true)}>下载此版本</button>
      {!previewable && <small>此格式暂不支持在线预览，请下载查看。</small>}
    </>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

function ReferencePicker({ nodes, disabled, onReference }: { nodes: KnowledgeNode[]; disabled: boolean; onReference: (source: string, binding: string, revision: number) => void }) {
  const [source, setSource] = useState(''); const [bindings, setBindings] = useState<KnowledgeFile[]>([]);
  const [binding, setBinding] = useState(''); const [versions, setVersions] = useState<KnowledgeFileVersion[]>([]);
  const [revision, setRevision] = useState(0); const [cursor, setCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(false); const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const chooseSource = async (id: string) => {
    request.current?.abort(); const c = new AbortController(); request.current = c;
    setSource(id); setBinding(''); setBindings([]); setVersions([]); setRevision(0); setCursor(null); setError(''); setLoading(!!id);
    if (!id) return;
    try { const list = await knowledgeFilesApi.list(id,c.signal); if (!c.signal.aborted) setBindings(list); }
    catch (e) { if (!c.signal.aborted) setError(errorMessage(e)); }
    finally { if (!c.signal.aborted) setLoading(false); }
  };
  const chooseBinding = async (id: string, before?: number) => {
    request.current?.abort(); const c = new AbortController(); request.current = c;
    setBinding(id); setLoading(!!id); setError('');
    if (!before) { setVersions([]); setRevision(0); setCursor(null); }
    if (!id) return;
    try {
      const page = await knowledgeFilesApi.versions(source,id,before,c.signal);
      if (!c.signal.aborted) { setVersions(old => before ? [...old,...page.items] : page.items); setCursor(page.next_cursor); if (!before) setRevision(page.items[0]?.revision ?? 0); }
    } catch (e) { if (!c.signal.aborted) setError(errorMessage(e)); }
    finally { if (!c.signal.aborted) setLoading(false); }
  };
  return <details open className="kc-file-reference"><summary>引用已有文件的固定版本</summary>
    <label>来源节点<select aria-label="来源节点" disabled={disabled} value={source} onChange={e => void chooseSource(e.target.value)}><option value="">选择可读节点</option>{nodes.map(n => <option key={n.id} value={n.id}>{n.title}</option>)}</select></label>
    {source && <label>来源文件<select aria-label="来源文件" disabled={disabled || loading} value={binding} onChange={e => void chooseBinding(e.target.value)}><option value="">选择文件</option>{bindings.map(b => <option key={b.id} value={b.id}>{b.title}</option>)}</select></label>}
    {binding && <label>固定版本<select aria-label="引用固定版本" disabled={disabled || loading} value={revision} onChange={e => setRevision(Number(e.target.value))}>{versions.map(v => <option key={v.revision} value={v.revision}>版本 {v.revision} · {v.name} · {v.sha256.slice(0,8)}</option>)}</select></label>}
    {cursor && <button type="button" disabled={disabled || loading} onClick={() => void chooseBinding(binding,cursor)}>加载更早版本</button>}
    {loading && <p role="status">正在读取来源…</p>}{error && <p role="alert">{error}</p>}
    <button type="button" disabled={disabled || loading || !source || !binding || !revision} onClick={() => onReference(source,binding,revision)}>添加固定版本引用</button>
    <small>来源后续更新不会改变此引用；引用只包含该文件版本，不包含来源节点的其他资料。</small>
  </details>;
}

export function KnowledgeFiles({ object, nodes, onClose, onBusyChange,onFilesChanged,entry,position,onLabels }: { object: KnowledgeObject; nodes: KnowledgeNode[]; onClose: () => void; onBusyChange: (busy: boolean) => void;onFilesChanged?:(files:KnowledgeFile[])=>void;onLabels?:()=>void;entry?:FileEntryAction;position?:{left:number;top:number} }) {
  const [rows, setRows] = useState<KnowledgeFile[]>([]); const [loading, setLoading] = useState(true);
  const [reading, setReading] = useState(false); const listController = useRef<AbortController | null>(null);
  const [pending, setPending] = useState(false); const busy = useRef(false);
  const [error, setError] = useState(''); const [mode,setMode]=useState<'workspace'|'reference'|null>(entry?.kind==='workspace'||entry?.kind==='reference'?entry.kind:null);const [historyId,setHistoryId]=useState<string|null>(null),[removeTarget,setRemoveTarget]=useState<KnowledgeFile|null>(entry?.kind==='remove'?entry.binding:null);const upload=useRef<HTMLInputElement>(null); const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [preview, setPreview] = useState<PreviewItem | null>(null); const blobUrl = useRef<string | null>(null);
  const previewFocus=useRef<HTMLElement|null>(null);
  const alive = useRef(true); const readController = useRef<AbortController | null>(null);
  const closePreview = () => {const wasOpen=!!blobUrl.current;if(blobUrl.current)URL.revokeObjectURL(blobUrl.current);blobUrl.current=null;setPreview(null);if(wasOpen&&previewFocus.current?.isConnected)previewFocus.current.focus({preventScroll:true});};
  useEffect(() => {
    alive.current = true; const c = new AbortController(); listController.current = c;
    knowledgeFilesApi.list(object.id,c.signal).then(list => { if (!c.signal.aborted) setRows(list); })
      .catch(e => { if (!c.signal.aborted) setError(errorMessage(e)); })
      .finally(() => { if (!c.signal.aborted) setLoading(false); });
    return () => { alive.current = false; listController.current?.abort(); readController.current?.abort(); if (blobUrl.current) URL.revokeObjectURL(blobUrl.current); };
  }, [object.id]);
  const execute = async (next: Attempt) => {
    if (busy.current) return;
    listController.current?.abort(); setLoading(false);
    busy.current = true; setPending(true); onBusyChange(true); setError('');
    let uncertain = false;
    try {
      if (next.kind === 'upload') await knowledgeFilesApi.upload(object.id,next.file,next.id,next.binding ? { binding_id:next.binding.id, expected_revision:next.binding.current_revision } : {});
      if (next.kind === 'snapshot') await knowledgeFilesApi.snapshot(object.id,next.path,next.id);
      if (next.kind === 'reference') await knowledgeFilesApi.reference(object.id,next.source,next.binding,next.revision,next.id);
      if (next.kind === 'remove') await knowledgeFilesApi.remove(object.id,next.binding,next.id);
      const list = await knowledgeFilesApi.list(object.id);
      if (alive.current) { setRows(list); setAttempt(null);setMode(null);onFilesChanged?.(list);if(entry){onBusyChange(false);onClose();} }
    } catch (e) {
      if (alive.current) {
        const rejected = typeof e === 'object' && e !== null && 'status' in e && typeof e.status === 'number' && e.status>=400 && e.status<500;
        uncertain = !rejected; setAttempt(rejected ? null : next);
        setError(errorMessage(e) + (rejected ? '；请刷新文件列表后重试。' : '；结果尚未确认，点击“重试同一操作”核对，避免重复添加。'));
        if (rejected) {
          try { const list = await knowledgeFilesApi.list(object.id); if (alive.current) setRows(list); } catch { if (alive.current) setRows([]); }
        }
      }
    } finally { busy.current = false; if (alive.current) { setPending(false); onBusyChange(uncertain); } }
  };
  const start = (action: Omit<Extract<Attempt,{kind:'upload'}>,'id'> | Omit<Extract<Attempt,{kind:'snapshot'}>,'id'> | Omit<Extract<Attempt,{kind:'reference'}>,'id'> | Omit<Extract<Attempt,{kind:'remove'}>,'id'>) => {
    if(!object.can_write){setError('当前节点为只读，无法修改附件');return;}
    if (attempt) { setError('请先重试核对上次操作。'); return; }
    if (action.kind === 'upload' && (!action.file.size || action.file.size>20*1024*1024)) { setError('文件不能为空且不能超过 20MB'); return; }
    void execute({ ...action,id:crypto.randomUUID() });
  };
  const launched=useRef(false),latestStart=useRef(start);useLayoutEffect(()=>{latestStart.current=start;});
  useEffect(()=>{let active=true;queueMicrotask(()=>{if(active&&!launched.current&&entry?.kind==='upload'){launched.current=true;latestStart.current({kind:'upload',file:entry.file});}});return()=>{active=false;};},[entry]);
  const read = async (v: KnowledgeFileVersion, download: boolean) => {
    readController.current?.abort(); const c = new AbortController(); readController.current = c;
    closePreview();previewFocus.current=document.activeElement instanceof HTMLElement?document.activeElement:null; setReading(true); setError('');
    try {
      const blob = await knowledgeFilesApi.content(object.id,v.binding_id,v.revision,c.signal);
      if (!alive.current || c.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      if (download) { const a = document.createElement('a'); a.href=url; a.download=v.name; a.click(); window.setTimeout(() => URL.revokeObjectURL(url),1000); }
      else { blobUrl.current=url; setPreview({ filename:v.name,mimeType:v.mime_type,size:v.size,url }); }
    } catch (e) { if (alive.current && !c.signal.aborted) setError(errorMessage(e)); }
    finally { if (alive.current && !c.signal.aborted) setReading(false); }
  };
  const disabled = pending || !!attempt;
  const panel = useRef<HTMLElement>(null);
  useEffect(() => { panel.current?.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true }); }, [object.id]);

  const attachPanel = useCanvasSurfaceMotion(panel, entry?'menu':'panel');
  return <aside ref={attachPanel} className={entry?"kc-file-panel kc-file-popover":"kc-file-panel"} style={entry&&position?{...position,maxHeight:`max(80px, calc(100vh - ${position.top+8}px))`}:undefined} role={entry?"dialog":undefined} aria-label={entry?"添加文件":"文件资料"} onKeyDown={event => { if (event.key !== 'Escape' || event.defaultPrevented || preview || !event.currentTarget.contains(event.target as Node)) return; event.preventDefault(); event.stopPropagation(); if (!disabled) onClose(); }}>
    <header><h2>{entry?entry.kind==='remove'?'移除附件':'添加文件':<>文件资料 · {rows.length}</>}</h2><button type="button" aria-label="关闭文件资料" disabled={disabled} onClick={onClose}><X size={18}/></button>{onLabels&&<button type="button" disabled={disabled} onClick={onLabels}>标签</button>}</header>
    {!entry&&<div className="kc-files-subhead"><span title={object.title}>{object.title}</span><button type="button" aria-label="刷新文件列表" title="刷新文件列表" disabled={disabled} onClick={()=>{listController.current?.abort();const c=new AbortController();listController.current=c;setLoading(true);setError('');void knowledgeFilesApi.list(object.id,c.signal).then(list=>{if(!c.signal.aborted){setRows(list);onFilesChanged?.(list);}}).catch(e=>{if(!c.signal.aborted)setError(errorMessage(e));}).finally(()=>{if(!c.signal.aborted)setLoading(false);});}}><RefreshCw size={16}/></button>
    {object.can_write&&<Menu.Root><Menu.Trigger asChild><button type="button" disabled={disabled} aria-label="添加文件"><Plus size={16}/>添加</button></Menu.Trigger><Menu.Portal><Menu.Content className="kc-tools-menu" collisionPadding={10}><Menu.Item onSelect={()=>upload.current?.click()}>上传文件</Menu.Item><Menu.Item onSelect={()=>setMode('workspace')}>从工作区选择</Menu.Item><Menu.Item onSelect={()=>setMode('reference')}>引用其他节点文件</Menu.Item></Menu.Content></Menu.Portal></Menu.Root>}</div>}
    {error&&<p role="alert">{error}</p>}{pending&&<p role="status">正在保存文件…</p>}{reading&&<p role="status">正在读取文件…</p>}
    {attempt&&<button type="button" disabled={pending} onClick={()=>void execute(attempt)}>重试同一操作</button>}
    {object.can_write&&<input ref={upload} hidden aria-label="上传并绑定文件" type="file" disabled={disabled} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)start({kind:'upload',file});}}/>}
    {mode==='workspace'&&<WorkspaceFilePicker disabled={disabled} onClose={()=>setMode(null)} onSelect={path=>start({kind:'snapshot',path})}/>}
    {mode==='reference'&&<section className="kc-file-pick" aria-label="引用其他节点文件"><button type="button" aria-label="关闭文件引用" onClick={()=>setMode(null)}><X size={16}/></button><ReferencePicker nodes={nodes} disabled={disabled} onReference={(source,binding,revision)=>start({kind:'reference',source,binding,revision})}/></section>}
    {!entry&&loading&&<p role="status">正在加载文件…</p>}{!entry&&!loading&&!rows.length&&!error&&<p>暂无附件，点击添加上传文件</p>}
    {!entry&&rows.map(binding=><article key={binding.id} className="kc-file-list-item"><div className="kc-file-list-row"><button type="button" className="kc-file-open" title={binding.title} aria-label={'预览文件 '+binding.title} disabled={pending||reading} onClick={()=>void read(binding.version,false)}><FileThumbnail objectId={object.id} file={binding}/><span><h3>{binding.title}</h3><small>{fileMeta(binding.version)} · {parseLabel(binding.version)}</small></span></button>
      {object.can_write&&<button type="button" className="kc-remove-affordance" aria-label={'移除附件 '+binding.title} title="移除附件" disabled={disabled} onClick={()=>setRemoveTarget(binding)}><X size={14}/></button>}
      <Menu.Root><Menu.Trigger asChild><button type="button" aria-label={'更多操作 '+binding.title} disabled={disabled}><MoreHorizontal size={20}/></button></Menu.Trigger><Menu.Portal><Menu.Content className="kc-tools-menu" collisionPadding={10}><Menu.Item disabled={reading} onSelect={()=>void read(binding.version,true)}>下载</Menu.Item>{object.can_write&&<Menu.Item onSelect={()=>document.getElementById('kc-file-update-'+binding.id)?.click()}>更新版本</Menu.Item>}<Menu.Item onSelect={()=>setHistoryId(historyId===binding.id?null:binding.id)}>历史版本</Menu.Item>{object.can_write&&<Menu.Item className="kc-danger" onSelect={()=>setRemoveTarget(binding)}>移除附件</Menu.Item>}</Menu.Content></Menu.Portal></Menu.Root>
      {object.can_write&&<input id={'kc-file-update-'+binding.id} hidden aria-label={'上传新版 '+binding.title} type="file" disabled={disabled} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)start({kind:'upload',file,binding});}}/>}</div>
      {object.can_write&&binding.version.ai_processing_enabled!==false&&['failed','blocked','blocked_permission'].includes(binding.version.parse_state??'')&&<button type="button" disabled={pending||reading} onClick={()=>void knowledgeFilesApi.retry(object.id,binding.id,binding.current_revision).then(()=>knowledgeFilesApi.list(object.id)).then(list=>setRows(list)).catch(e=>setError(errorMessage(e)))}>重试解析</button>}{historyId===binding.id&&<FileVersions key={binding.id+':'+binding.current_revision} objectId={object.id} binding={binding} disabled={pending||reading} onRead={(v,download)=>void read(v,download)}/>}</article>)}
    {removeTarget&&<Dialog open title="移除附件" onOpenChange={open=>{if(!open){setRemoveTarget(null);if(entry?.kind==='remove')onClose();}}}><p>从此节点移除“{removeTarget.title}”？源文件和其他节点的引用不会删除。</p><button type="button" onClick={()=>{setRemoveTarget(null);if(entry?.kind==='remove')onClose();}}>取消</button><button type="button" disabled={disabled} onClick={()=>{start({kind:'remove',binding:removeTarget});setRemoveTarget(null);}}>移除附件</button></Dialog>}
    <PreviewHost state={preview ? {kind:'open',items:[preview],index:0} : {kind:'closed'}} onClose={closePreview} onIndexChange={() => {}} />
  </aside>;
}
