import {useEffect,useRef,useState} from 'react';
import {Dialog} from '../primitives/Dialog';
import {ApiRequestError} from '../../services/api';
import {knowledgeFilesApi} from '../../services/knowledge';
import type {XmindImportResult,ImportProgress,KnowledgeContainer} from '../../services/knowledge';

export function XmindImport({identity,target,disabled,onBusy,onOpenChange,onImported}:{identity:string;target:Pick<KnowledgeContainer,'id'|'title'|'revision'>;disabled:boolean;onBusy:(busy:boolean)=>void;onOpenChange?:(open:boolean)=>void;onImported:(result:XmindImportResult)=>Promise<void>}) {
  const [open,setOpen]=useState(false),[file,setFile]=useState<File|null>(null),[busy,setBusy]=useState(false);
  const [error,setError]=useState(''),[phase,setPhase]=useState(''),[result,setResult]=useState<XmindImportResult|null>(null);
  const [synced,setSynced]=useState(false);
  const [progress,setProgress]=useState<ImportProgress|null>(null);
  useEffect(()=>{if(!open||!result||result.hidden||!synced||typeof knowledgeFilesApi.progress!=='function')return;const c=new AbortController();let timer:ReturnType<typeof setTimeout>;const load=async()=>{try{const p=await knowledgeFilesApi.progress(result.container_id,c.signal);if(!c.signal.aborted){setProgress(p);if(p.ai_processing_enabled!==false&&p.files.pending)timer=setTimeout(()=>void load(),3000);}}catch{if(!c.signal.aborted)setError('导图已导入，附件状态暂时无法刷新，可稍后查看原始文件。');}};void load();return()=>{c.abort();clearTimeout(timer);};},[open,result,synced]);
  const mounted=useRef(true),running=useRef(false);
  const attempt=useRef<{digest:string;command:string;revision:number}|null>(null);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  function changeOpen(value:boolean){setOpen(value);onOpenChange?.(value);}
  async function submit(fresh=false){
    if(!file||running.current)return;
    running.current=true;setBusy(true);onBusy(true);setError('');setResult(null);setSynced(false);setProgress(null);setPhase('正在校验并上传…');
    try {
      if(!file.name.toLowerCase().endsWith('.xmind')||!file.size||file.size>20*1024*1024)throw new Error('请选择 20MB 以内的 .xmind 文件');
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer())),b=>b.toString(16).padStart(2,'0')).join('');
      const key='everydayai:xmind-import:'+encodeURIComponent(identity)+':'+target.id+':'+digest;
      // Store the receipt key before sending. A lost response or page reload must
      // retry the same command, even when the original transaction committed.
      if(fresh||attempt.current?.digest!==digest){
        const stored=fresh?null:sessionStorage.getItem(key);
        const saved=stored?JSON.parse(stored):null;
        const valid=saved&&/^[0-9a-f-]{36}$/.test(saved.command)&&Number.isSafeInteger(saved.revision)&&saved.revision>=1;
        attempt.current={digest,command:valid?saved.command:crypto.randomUUID(),revision:valid?saved.revision:target.revision};
      }
      // Every send must have a durable pending receipt, including retries after a storage failure.
      sessionStorage.setItem(key,JSON.stringify({command:attempt.current.command,revision:attempt.current.revision}));
      if(!mounted.current)return;
      setPhase('正在校验框架并建立节点与附件绑定…');
      const response=await knowledgeFilesApi.importXmind(file,attempt.current.command,target.id,attempt.current.revision);
      if(!mounted.current)return;
      setResult(response.data);setPhase('');
      try{
        await onImported(response.data);
        if(!mounted.current)return;
        setSynced(!response.data.hidden);
      }catch{if(mounted.current)setError(response.data.hidden?'原导入已撤销或删除，当前画布暂未刷新；请核对历史记录或重新导入。':'内容已保存，但画布尚未同步确认；请点击“重新核对导入结果”，不会重复添加。');return;}
      // A confirmed result completes this operation. A later import is a new user action.
      try{sessionStorage.removeItem(key);attempt.current=null;}catch{if(mounted.current)setError('结果已核对，但浏览器未能清除旧导入记录；再次导入时请核对结果。');}
    }catch(e){if(e instanceof ApiRequestError&&e.code==='VERSION_CONFLICT'&&attempt.current){try{sessionStorage.removeItem('everydayai:xmind-import:'+encodeURIComponent(identity)+':'+target.id+':'+attempt.current.digest);}catch{/* Keep the original failure visible. */}attempt.current=null;}if(mounted.current){setPhase('');setError(e instanceof Error?e.message:'导入结果尚未确认，请使用同一文件重试核对。');}}
    finally{running.current=false;if(mounted.current){setBusy(false);onBusy(false);}}
  }
  return <>
    <button aria-label={"导入 Xmind 到 "+target.title} disabled={disabled||busy} onClick={()=>{changeOpen(true);setResult(null);setSynced(false);setProgress(null);setError('');}}>导入 Xmind</button>
    {open&&<Dialog open title="导入 Xmind" className="kc-modal" showClose={!busy} closeOnEscape={!busy} closeOnOutsideClick={!busy} onOpenChange={value=>{if(!busy)changeOpen(value);}}>
      <div className="kc-form">
        <p>导入到“{target.title}”，保留主题层级、完整文字、图片和链接；新增一棵导图，访问权限继承当前空间。外部链接仅保留入口，不读取其中的视频或文档。</p>
        <label>选择导图<input aria-label="选择 Xmind 文件" type="file" accept=".xmind" disabled={busy} onChange={e=>{setFile(e.target.files?.[0]??null);setResult(null);setSynced(false);setProgress(null);setError('');}}/></label>
        {!result&&<button disabled={!file||busy} onClick={()=>void submit()}>{busy?'正在导入…':error?'重试并核对导入':'导入到当前空间'}</button>}
        {phase&&<p role="status">{phase}</p>}
        {error&&<p role="alert">{error}</p>}
        {result&&<div role="status"><p>{result.hidden?'这次导入已撤销或删除，本次没有新增内容；可以重新导入为新导图，或在画布历史中恢复原导入。':synced?(result.status==='replayed'?'已找到这次导入的内容，没有重复添加。':'导入完成。'):busy?'文件已保存，正在核对画布显示。':'文件已保存，画布显示尚未确认。'} 共 {result.report.topics} 个主题、{result.report.images} 张图片、{result.report.links} 个链接、{result.report.summaries} 个概要。</p>{!result.hidden&&synced&&<p>框架与节点已建立，可以立即查看。原始图片、附件与链接已保留，可直接查看；用户导图不会自动改写或拆分。可使用画布撤销/重做，或删除导入分支后撤销恢复。</p>}{!result.hidden&&progress&&(progress.ai_processing_enabled===false?<p>原始附件已保存，AI 解析未启用；{progress.links_unread} 个链接仅保留入口。</p>:<p>当前空间附件：已解析 {progress.files.ready}，部分解析 {progress.files.partial}，待处理 {progress.files.pending}，失败 {progress.files.failed}，不支持 {progress.files.unsupported}，未启用 {progress.files.disabled??0}；另有 {progress.links_unread} 个链接仅保留入口。</p>)}{!result.hidden&&<ul>{result.report.warnings.map(w=><li key={w}>{w}</li>)}</ul>}</div>}
        {result?.hidden&&<button disabled={busy} onClick={()=>void submit(true)}>重新导入为新导图</button>}
        {result&&!result.hidden&&!synced&&!busy&&<button onClick={()=>void submit()}>重新核对导入结果</button>}
        <button disabled={busy} onClick={()=>changeOpen(false)}>{result&&!result.hidden&&synced?'完成':'关闭'}</button>
      </div>
    </Dialog>}
  </>;
}
