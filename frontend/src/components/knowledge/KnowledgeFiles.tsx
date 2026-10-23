import { useCanvasSurfaceMotion } from './useCanvasSurfaceMotion';
import { useEffect, useRef, useState } from 'react';
import PreviewHost from '../../preview/PreviewHost';
import { resolveAdapter } from '../../preview/registry';
import type { PreviewItem } from '../../preview/types';
import { knowledgeFilesApi } from '../../services/knowledge';
import type { KnowledgeFile, KnowledgeFileVersion, KnowledgeNode, KnowledgeObject } from '../../services/knowledge';

type Attempt = { id: string } & (
  { kind: 'upload'; file: File; binding?: KnowledgeFile } |
  { kind: 'snapshot'; path: string } |
  { kind: 'reference'; source: string; binding: string; revision: number } |
  { kind: 'remove'; binding: KnowledgeFile }
);
const textStatus = { ready: '正文可搜索', truncated: '正文部分可搜索', failed: '正文提取失败，可下载原件', unsupported: '无可搜索正文' };
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
  return <details className="kc-file-reference"><summary>引用已有文件的固定版本</summary>
    <label>来源节点<select aria-label="来源节点" disabled={disabled} value={source} onChange={e => void chooseSource(e.target.value)}><option value="">选择可读节点</option>{nodes.map(n => <option key={n.id} value={n.id}>{n.title}</option>)}</select></label>
    {source && <label>来源文件<select aria-label="来源文件" disabled={disabled || loading} value={binding} onChange={e => void chooseBinding(e.target.value)}><option value="">选择文件</option>{bindings.map(b => <option key={b.id} value={b.id}>{b.title}</option>)}</select></label>}
    {binding && <label>固定版本<select aria-label="引用固定版本" disabled={disabled || loading} value={revision} onChange={e => setRevision(Number(e.target.value))}>{versions.map(v => <option key={v.revision} value={v.revision}>版本 {v.revision} · {v.name} · {v.sha256.slice(0,8)}</option>)}</select></label>}
    {cursor && <button type="button" disabled={disabled || loading} onClick={() => void chooseBinding(binding,cursor)}>加载更早版本</button>}
    {loading && <p role="status">正在读取来源…</p>}{error && <p role="alert">{error}</p>}
    <button type="button" disabled={disabled || loading || !source || !binding || !revision} onClick={() => onReference(source,binding,revision)}>添加固定版本引用</button>
    <small>来源后续更新不会改变此引用；引用只包含该文件版本，不包含来源节点的其他资料。</small>
  </details>;
}

export function KnowledgeFiles({ object, nodes, onClose, onBusyChange }: { object: KnowledgeObject; nodes: KnowledgeNode[]; onClose: () => void; onBusyChange: (busy: boolean) => void }) {
  const [rows, setRows] = useState<KnowledgeFile[]>([]); const [loading, setLoading] = useState(true);
  const [reading, setReading] = useState(false); const listController = useRef<AbortController | null>(null);
  const [pending, setPending] = useState(false); const busy = useRef(false);
  const [error, setError] = useState(''); const [path, setPath] = useState(''); const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [preview, setPreview] = useState<PreviewItem | null>(null); const blobUrl = useRef<string | null>(null);
  const alive = useRef(true); const readController = useRef<AbortController | null>(null);
  const closePreview = () => { if (blobUrl.current) URL.revokeObjectURL(blobUrl.current); blobUrl.current = null; setPreview(null); };
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
      if (alive.current) { setRows(list); setAttempt(null); setPath(''); }
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
    if (attempt) { setError('请先重试核对上次操作。'); return; }
    if (action.kind === 'upload' && (!action.file.size || action.file.size>20*1024*1024)) { setError('文件不能为空且不能超过 20MB'); return; }
    void execute({ ...action,id:crypto.randomUUID() });
  };
  const read = async (v: KnowledgeFileVersion, download: boolean) => {
    readController.current?.abort(); const c = new AbortController(); readController.current = c;
    closePreview(); setReading(true); setError('');
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

  const attachPanel = useCanvasSurfaceMotion(panel, 'panel');
  return <aside ref={attachPanel} className="kc-file-panel" aria-label="文件资料" onKeyDown={event => { if (event.key !== 'Escape' || event.defaultPrevented || preview || !event.currentTarget.contains(event.target as Node)) return; event.preventDefault(); event.stopPropagation(); if (!disabled) onClose(); }}>
    <header><h2>{object.title} · 文件资料</h2><button type="button" disabled={pending || !!attempt} onClick={onClose}>关闭文件资料</button></header>
    <p>上传与引用均固定到内容版本。移除附件不会删除源文件或其他节点的引用。</p>
    <small>单文件最多 20MB；文本须为 UTF-8。支持图片、PDF、Word、表格和 MP4 的只读预览；旧版 Office、PPT 和 ZIP 仅下载。扫描件与音视频未提供 OCR/转写。</small>
    {error && <p role="alert">{error}</p>}{pending && <p role="status">正在保存文件…</p>}{reading && <p role="status">正在读取文件…</p>}
    {attempt && <button type="button" disabled={pending} onClick={() => void execute(attempt)}>重试同一操作</button>}
    <button type="button" disabled={pending || !!attempt} onClick={() => { listController.current?.abort(); const c = new AbortController(); listController.current = c; setLoading(true); setError(''); knowledgeFilesApi.list(object.id,c.signal).then(list => { if (alive.current && !c.signal.aborted) setRows(list); }).catch(e => { if (alive.current && !c.signal.aborted) { setRows([]); setError(errorMessage(e)); } }).finally(() => { if (alive.current && !c.signal.aborted) setLoading(false); }); }}>刷新文件列表</button>
    {object.can_write && <div className="kc-file-add">
      <label>上传并绑定文件<input aria-label="上传并绑定文件" type="file" disabled={disabled} onChange={e => { const file=e.target.files?.[0]; e.target.value=''; if (file) start({kind:'upload',file}); }} /></label>
      <details><summary>从我的 Workspace 固定文件</summary><label>Workspace 相对路径<input aria-label="Workspace 相对路径" maxLength={2048} value={path} disabled={disabled} onChange={e => setPath(e.target.value)} placeholder="例如：上传/2026-10/资料.pdf" /></label><button type="button" disabled={disabled || !path.trim()} onClick={() => start({kind:'snapshot',path:path.trim()})}>建立固定快照</button></details>
      <ReferencePicker nodes={nodes} disabled={disabled} onReference={(source,binding,revision) => start({kind:'reference',source,binding,revision})} />
    </div>}
    {loading && <p role="status">正在加载文件…</p>}{!loading && !rows.length && !error && <p>此节点暂无文件</p>}
    {rows.map(binding => <article key={binding.id} className="kc-file-card"><h3>{binding.title}</h3>
      <FileVersions key={binding.id+':'+binding.current_revision} objectId={object.id} binding={binding} disabled={pending || reading} onRead={(v,download) => void read(v,download)} />
      {object.can_write && <><label>上传新版<input aria-label={'上传新版 '+binding.title} type="file" disabled={disabled} onChange={e => { const file=e.target.files?.[0]; e.target.value=''; if (file) start({kind:'upload',file,binding}); }} /></label>
        <button type="button" disabled={disabled} onClick={() => start({kind:'remove',binding})}>移除此附件</button></>}
    </article>)}
    <PreviewHost state={preview ? {kind:'open',items:[preview],index:0} : {kind:'closed'}} onClose={closePreview} onIndexChange={() => {}} />
  </aside>;
}
