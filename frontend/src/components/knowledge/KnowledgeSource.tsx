import { useCanvasSurfaceMotion } from './useCanvasSurfaceMotion';
import { useEffect, useRef, useState } from 'react';
import { knowledgeApi, knowledgeFilesApi } from '../../services/knowledge';
import type { KnowledgeVersionDetail, KnowledgeFileVersion } from '../../services/knowledge';
import PreviewHost from '../../preview/PreviewHost';
import type { PreviewItem } from '../../preview/types';

/** Historical sources are read-only; opening them never replaces editor drafts. */
export function KnowledgeSource({objectId,revision,bindingId,onClose}: {objectId:string;revision:number;bindingId:string|null;onClose?:()=>void}) {
  const [body,setBody] = useState<KnowledgeVersionDetail>();
  const [file,setFile] = useState<KnowledgeFileVersion>();
  const [error,setError] = useState('');
  const [preview,setPreview] = useState<PreviewItem>();
  const [open,setOpen] = useState(true);
  const panel = useRef<HTMLElement>(null);
  const closable = !!onClose;
  useEffect(() => { if (closable) panel.current?.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true }); }, [objectId, revision, closable]);
  useEffect(() => {
    const c=new AbortController();let blobUrl:string|undefined;
    void (async () => {
      if (!Number.isSafeInteger(revision) || revision<1) throw new Error('来源版本无效');
      if (!bindingId) {
        const detail=await knowledgeApi.version(objectId,revision,c.signal);
        if (!c.signal.aborted)setBody(detail);
      } else {
        let before:number|undefined;let found:KnowledgeFileVersion|undefined;
        do {
          const page=await knowledgeFilesApi.versions(objectId,bindingId,before,c.signal);
          found=page.items.find(v=>v.revision===revision);
          before=page.next_cursor ?? undefined;
        } while (!found && before && !c.signal.aborted);
        if (!found)throw new Error('引用的文件版本不可用');
        const blob=await knowledgeFilesApi.content(objectId,bindingId,revision,c.signal);
        if (c.signal.aborted)return;
        blobUrl=URL.createObjectURL(blob);setFile(found);
        setPreview({url:blobUrl,filename:found.name,mimeType:found.mime_type,size:found.size});
      }
    })().catch(() => {if (!c.signal.aborted)setError('来源已移除、无权访问或版本不可用，请重新选择知识引用。');});
    return () => {c.abort();if (blobUrl)URL.revokeObjectURL(blobUrl);};
  },[objectId,revision,bindingId]);
  const attachPanel = useCanvasSurfaceMotion(panel, 'panel');
  return <aside ref={attachPanel} aria-label="回答来源版本" className="kc-source" onKeyDown={event => { if (event.key !== 'Escape' || event.defaultPrevented || (open && preview) || !event.currentTarget.contains(event.target as Node)) return; event.preventDefault(); event.stopPropagation(); onClose?.(); }}>
    {onClose && <button aria-label="关闭回答来源" onClick={onClose}>关闭</button>}
    <h2>回答依据 · 版本 {revision}</h2><p>这是本次回答使用的历史版本，当前内容更新不会改变此依据。</p>
    {error && <p role="alert">{error}</p>}
    {body && <><h3>{body.title}</h3><textarea aria-label="回答来源正文" value={body.body} readOnly /></>}
    {file && <><p>{file.name}</p><button onClick={()=>setOpen(true)}>预览引用版本</button></>}
    {!body && !file && !error && <p role="status">正在读取来源…</p>}
    <PreviewHost state={open && preview ? {kind:'open',items:[preview],index:0} : {kind:'closed'}} onClose={()=>setOpen(false)} onIndexChange={()=>{}} />
  </aside>;
}
