import {useEffect,useRef,useState} from 'react';
import {Dialog} from '../primitives/Dialog';
import {knowledgeFilesApi} from '../../services/knowledge';
import type {XmindImportResult,ImportProgress} from '../../services/knowledge';

export function XmindImport({identity,disabled,onBusy,onOpenChange,onImported}:{identity:string;disabled:boolean;onBusy:(busy:boolean)=>void;onOpenChange?:(open:boolean)=>void;onImported:(result:XmindImportResult)=>Promise<void>}) {
  const [open,setOpen]=useState(false),[file,setFile]=useState<File|null>(null),[busy,setBusy]=useState(false);
  const [error,setError]=useState(''),[phase,setPhase]=useState(''),[result,setResult]=useState<XmindImportResult|null>(null);
  const [progress,setProgress]=useState<ImportProgress|null>(null);
  useEffect(()=>{if(!open||!result||result.hidden||typeof knowledgeFilesApi.progress!=='function')return;const c=new AbortController();let timer:ReturnType<typeof setTimeout>;const load=async()=>{try{const p=await knowledgeFilesApi.progress(result.container_id,c.signal);if(!c.signal.aborted){setProgress(p);if(p.files.pending)timer=setTimeout(()=>void load(),3000);}}catch{if(!c.signal.aborted)setError('框架与节点已导入，附件进度暂时无法刷新，请稍后查看资料面板。');}};void load();return()=>{c.abort();clearTimeout(timer);};},[open,result]);
  const mounted=useRef(true),running=useRef(false);
  const attempt=useRef<{digest:string;command:string}|null>(null);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  function changeOpen(value:boolean){setOpen(value);onOpenChange?.(value);}
  async function submit(){
    if(!file||running.current)return;
    running.current=true;setBusy(true);onBusy(true);setError('');setPhase('正在校验并上传…');
    try {
      if(!file.name.toLowerCase().endsWith('.xmind')||!file.size||file.size>20*1024*1024)throw new Error('请选择 20MB 以内的 .xmind 文件');
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer())),b=>b.toString(16).padStart(2,'0')).join('');
      const key='everydayai:xmind-import:'+encodeURIComponent(identity)+':'+digest;
      // Store the receipt key before sending. A lost response or page reload must
      // retry the same command, even when the original transaction committed.
      if(attempt.current?.digest!==digest){
        const saved=sessionStorage.getItem(key);
        attempt.current={digest,command:saved&&/^[0-9a-f-]{36}$/.test(saved)?saved:crypto.randomUUID()};
        sessionStorage.setItem(key,attempt.current.command);
      }
      if(!mounted.current)return;
      setPhase('正在校验框架并建立节点与附件绑定…');
      const response=await knowledgeFilesApi.importXmind(file,attempt.current.command);
      if(!mounted.current)return;
      setResult(response.data);setPhase('');
      try{await onImported(response.data);}catch{if(mounted.current)setError('导入已完成，画布同步失败；请关闭后刷新，或用同一文件重试核对。');}
    }catch(e){if(mounted.current){setPhase('');setError(e instanceof Error?e.message:'导入结果尚未确认，请使用同一文件重试核对。');}}
    finally{running.current=false;if(mounted.current){setBusy(false);onBusy(false);}}
  }
  return <>
    <button disabled={disabled||busy} onClick={()=>{changeOpen(true);setResult(null);setProgress(null);setError('');}}>导入 Xmind</button>
    {open&&<Dialog open title="导入 Xmind" className="kc-modal" showClose={!busy} closeOnEscape={!busy} closeOnOutsideClick={!busy} onOpenChange={value=>{if(!busy)changeOpen(value);}}>
      <div className="kc-form">
        <p>新建独立项目，保留主题层级、完整文字、图片和链接。默认仅你可访问，可通过项目成员权限共享。外部链接仅保留入口，不读取其中的视频或文档。</p>
        <label>选择导图<input aria-label="选择 Xmind 文件" type="file" accept=".xmind" disabled={busy} onChange={e=>{setFile(e.target.files?.[0]??null);setResult(null);setProgress(null);setError('');}}/></label>
        {!result&&<button disabled={!file||busy} onClick={()=>void submit()}>{busy?'正在导入…':error?'重试并核对导入':'导入为新项目'}</button>}
        {phase&&<p role="status">{phase}</p>}
        {error&&<p role="alert">{error}</p>}
        {result&&<div role="status"><p>{result.hidden?'这次导入已撤销或删除，本次重试没有重新创建；请核对画布历史记录。':result.status==='replayed'?'已找到这次导入的项目，没有重复创建。':'导入完成。'} 共 {result.report.topics} 个主题、{result.report.images} 张图片、{result.report.links} 个链接、{result.report.summaries} 个概要。</p><p>框架与节点已建立，可以立即查看。附件独立排队解析，失败可单独重试；不会默认逐层生成摘要或标签。可使用画布撤销/重做，或删除项目后撤销恢复。</p>{progress&&<p>附件：已解析 {progress.files.ready}，部分解析 {progress.files.partial}，待处理 {progress.files.pending}，失败 {progress.files.failed}，不支持 {progress.files.unsupported}，未启用 {progress.files.disabled??0}；另有 {progress.links_unread} 个链接仅保留入口。</p>}<ul>{result.report.warnings.map(w=><li key={w}>{w}</li>)}</ul></div>}
        <button disabled={busy} onClick={()=>changeOpen(false)}>{result?'完成':'关闭'}</button>
      </div>
    </Dialog>}
  </>;
}
