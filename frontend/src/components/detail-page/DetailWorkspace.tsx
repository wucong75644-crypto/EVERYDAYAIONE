import { Fragment, useEffect, useRef, useState } from 'react';
import { Download, Sparkles } from 'lucide-react';
import toast from 'react-hot-toast';
import type { DetailGenerationRun, DetailGroup, DetailImageTask } from '../../types/detailPage';
import { AiGeneratedImage } from '../chat/message/MessageImageBlocks';
import ChatImageControls from '../chat/media/ChatImageControls';
import { FailedMediaPlaceholder } from '../chat/media/MediaPlaceholder';
import { usePreview } from '../../preview/usePreview';
import PreviewHost from '../../preview/PreviewHost';
import type { PreviewItem } from '../../preview/types';
import { chatImageService } from '../../services/chatImage';
import { resumeDetailPlan, stopDetailRecovery } from '../../services/detailProject';
import { getImagePlaceholderSize } from '../../utils/settingsStorage';
import { detailImageDownloads, detailTaskFor as taskFor } from '../../utils/detailPageImages';
import { downloadWorkspaceZip } from '../../services/workspace';
import { Button } from '../ui/Button';
import { DetailPlanProgress } from './DetailPlanProgress';
import progressStyles from './DetailPlanProgress.module.css';

const previewWidth=getImagePlaceholderSize('1:1').width;
function Group({group,onRefresh,projectId,readOnly=false,onPreview}:{group:DetailGroup;onRefresh:()=>void;projectId:string|null;readOnly?:boolean;onPreview:(taskId:string)=>void}){
  const accepted=group.tasks.length>0;
  const [promptOpen,setPromptOpen]=useState<boolean|null>(null);
  const [retrying,setRetrying]=useState<string|null>(null);
  const requestIds=useRef(new Map<string,string>());
  useEffect(()=>{ if(accepted)setPromptOpen(false); },[accepted]);
  useEffect(()=>{
    if(group.resume_request_id&&requestIds.current.get(group.plan_id)===group.resume_request_id)
      requestIds.current.delete(group.plan_id);
  },[group.plan_id,group.resume_request_id]);
  const recovering=group.auto_recovery?.status==='waiting';
  const failed=!recovering&&(['failed','cancelled','needs_input','insufficient'].includes(group.status)||!!group.acceptance_error);
  async function retry(task:DetailImageTask){
    if(retrying)return;setRetrying(task.id);
    if(!requestIds.current.has(task.id))requestIds.current.set(task.id,crypto.randomUUID());
    try{await chatImageService.replay(task.id,requestIds.current.get(task.id)!);}
    catch(error){toast.error(error instanceof Error?error.message:'重试失败');}
    finally{setRetrying(null);onRefresh();}
  }
  return <section className="rounded-2xl border border-[var(--s-border-subtle)] p-4 sm:p-5" aria-label={group.kind==='main_images'?'主图工作区':'详情图工作区'}>
    <DetailPlanProgress group={group} readOnly={readOnly}/>
    {failed&&<div className="mb-4 rounded-xl bg-[var(--s-surface-secondary)] p-3 text-sm" role="alert">
      {group.status==='needs_input'||group.status==='insufficient'?'需要补充产品信息，请根据提示完善要求后重新开始。':group.acceptance_error?
        `图片任务尚未受理：${group.acceptance_error.code??'提交失败'}`:`策划未完成：${group.error?.message??group.error?.code??'服务调用失败'}`}
      {!!group.questions?.length&&<pre className="mt-2 whitespace-pre-wrap text-xs">{JSON.stringify(group.questions,null,2)}</pre>}
      {group.retry_may_have_provider_cost&&<p className="mt-2 text-xs">本地超时请求已结束，已完成的阶段会保留。重试会发起新的模型调用，旧调用的供应商费用仍待确认。</p>}
      {!readOnly&&(group.can_resume||group.acceptance_error)&&projectId&&<button type="button" className="mt-2 text-[var(--s-accent)]" disabled={!!retrying} onClick={()=>{
        if(!requestIds.current.has(group.plan_id))requestIds.current.set(group.plan_id,crypto.randomUUID());
        setRetrying(group.plan_id);void resumeDetailPlan(projectId,group.plan_id,requestIds.current.get(group.plan_id)!).then(()=>{requestIds.current.delete(group.plan_id);})
          .catch(error=>toast.error(error instanceof Error?error.message:'恢复失败')).finally(()=>{setRetrying(null);onRefresh();});
      }}>{group.acceptance_error?'重试提交生图':'从失败阶段继续'}</button>}
    </div>}
    {!!group.items.length&&<div className="mb-4">
      <button type="button" className="text-sm text-[var(--s-accent)]" aria-expanded={promptOpen??!accepted} onClick={()=>setPromptOpen(!(promptOpen??!accepted))}>
        {(promptOpen??!accepted)?'收起提示词':'展开提示词'} · {group.items.length}份</button>
      {(promptOpen??!accepted)&&<div className={`mt-3 max-h-[480px] space-y-3 overflow-y-auto ${progressStyles.promptContent}`}>
        {[...group.items].sort((a,b)=>a.position-b.position).map(item=><article key={item.item_id} className="rounded-xl bg-[var(--s-surface-secondary)] p-3">
          <div className="flex justify-between gap-2 text-sm"><h3>{item.position}. {item.name}</h3><button type="button" onClick={()=>void navigator.clipboard.writeText(item.request_text).then(()=>toast.success('提示词已复制'))}>复制</button></div>
          <pre className="mt-2 whitespace-pre-wrap break-words text-xs leading-6">{item.request_text}</pre>
        </article>)}
      </div>}
    </div>}
    {accepted&&<div className="grid items-start gap-5" style={{gridTemplateColumns:`repeat(auto-fill, minmax(0, min(100%, ${previewWidth}px)))`}}>
      {[...group.items].sort((a,b)=>a.position-b.position).map(item=>{
        const task=taskFor(group,item.item_id);if(!task)return null;
        const [w,h]=item.aspect_ratio.split(':').map(Number);const size={width:previewWidth,height:previewWidth*h/w};
        const result=task.result_data;const url=result?.url??result?.original_url;
        const retryPending=group.auto_recovery?.items[item.item_id]?.status==='waiting';
        const blocked=group.auto_recovery?.items[item.item_id]?.status==='blocked';
        const isFailed=['failed','cancelled'].includes(task.status)&&!retryPending;
        return <article key={item.item_id} className={`min-w-0 overflow-hidden ${task.submission_state==='uncertain'?progressStyles.staticPreview:''}`}>
          <h3 className="mb-1 truncate text-sm">{item.position}. {item.name}</h3>
          <ChatImageControls taskId={task.id}/>
          {isFailed?<div className="mt-3"><FailedMediaPlaceholder type="image" aspectRatio={w/h} errorMessage={group.auto_recovery?.items[item.item_id]?.message||task.error_message||'生成失败'} onRetry={blocked||readOnly?undefined:()=>void retry(task)} retryLabel={retrying===task.id?'正在受理…':'重新生成'}/></div>:
          <AiGeneratedImage fitContainer renderId={task.id} imageAsset={url?{originalUrl:url,thumbnailUrl:result?.thumbnail_url}:null}
            placeholderSize={size} isGenerating={task.status!=='completed'} onImageClick={()=>{if(url)onPreview(task.id);}}/>}
          <p className="mt-2 text-xs text-[var(--s-text-tertiary)]">{retryPending?'自动恢复中，正在补齐此图片':isFailed?'生成失败':task.status==='completed'?'已完成':task.submission_state==='queued'?'排队中':task.submission_state==='uncertain'?'正在核实供应商受理结果':'生成中'}</p>
        </article>;
      })}
    </div>}
  </section>;
}
export function DetailWorkspace({groups,runs=[],currentRunId,onRefresh,projectId}:{groups:DetailGroup[];runs?:DetailGenerationRun[];currentRunId?:string|null;onRefresh:()=>void;projectId:string|null}){
  const [downloading,setDownloading]=useState(false);
  const [stopping,setStopping]=useState(false);
  const downloadInFlight=useRef(false);
  const latestRef=useRef<HTMLDivElement>(null);
  const preview=usePreview();
  const orderedRuns=runs.length?runs:[{run_id:'current',created_at:'',requirement:'',groups}];
  const latestId=currentRunId??orderedRuns.at(-1)?.run_id;
  useEffect(()=>{latestRef.current?.scrollIntoView?.({block:'start'});},[latestId]);
  const downloads=orderedRuns.flatMap((run,index)=>detailImageDownloads(run.groups).map(file=>({...file,
    archivePath:orderedRuns.length>1?file.archivePath.replace('/',`/第${String(index+1).padStart(2,'0')}轮/`):file.archivePath})));
  const previewImages=orderedRuns.flatMap(run=>run.groups.flatMap(group=>[...group.items].sort((a,b)=>a.position-b.position).flatMap(item=>{
    const task=taskFor(group,item.item_id);const result=task?.result_data;const url=result?.original_url??result?.url;
    if(task?.status!=='completed'||!url)return [];
    const filename=result?.workspace_path?.split('/').pop()??`${item.name}.png`;
    return [{taskId:task.id,item:{url,thumbnailUrl:result?.thumbnail_url,workspacePath:result?.workspace_path,filename,mimeType:'image/png'} as PreviewItem}];
  })));
  const openPreview=(taskId:string)=>{
    const index=previewImages.findIndex(image=>image.taskId===taskId);
    if(index>=0)preview.open(previewImages.map(image=>image.item),index);
  };
  async function downloadAll(){
    if(downloadInFlight.current||!downloads.length)return;
    downloadInFlight.current=true;setDownloading(true);
    try{
      await downloadWorkspaceZip(downloads.map(file=>file.path),{
        archivePaths:downloads.map(file=>file.archivePath),
        archiveName:`主图详情-${new Date().toISOString().replace(/[:.]/g,'-')}.zip`,
      });
    }catch(error){toast.error(error instanceof Error?error.message:'批量下载失败，请重试');}
    finally{downloadInFlight.current=false;setDownloading(false);}
  }
  if(!orderedRuns.some(run=>run.groups.length))return <div className="flex h-full items-center justify-center text-center">
    <div><Sparkles className="mx-auto mb-5 h-8 w-8 text-[var(--s-text-tertiary)]"/><h2 className="font-semibold">输入</h2>
      <p className="mt-3 text-sm text-[var(--s-text-tertiary)]">上传产品图并填写要求后，点击“开始生成”开始</p></div></div>;
  return <div className="space-y-5">
    <div className="flex justify-end gap-3">
    {projectId&&groups.some(group=>group.auto_recovery?.enabled&&(group.status!=='ready'||
      group.items.some(item=>taskFor(group,item.item_id)?.status!=='completed')))&&
      <Button variant="secondary" size="sm" loading={stopping} disabled={stopping}
        title="不再自动补图；已提交给供应商的任务继续回收结果" onClick={()=>{
          setStopping(true);void stopDetailRecovery(projectId).then(onRefresh)
            .catch(error=>toast.error(error instanceof Error?error.message:'暂时无法停止，请重试')).finally(()=>setStopping(false));
        }}>停止自动重试</Button>}
    <Button variant="secondary" size="sm" icon={<Download className="h-4 w-4"/>}
      disabled={!downloads.length} loading={downloading} title="下载已完成的原图，按主图和详情页分类" onClick={()=>void downloadAll()}>
      {downloading?'打包中…':`批量下载（${downloads.length}张）`}
    </Button></div>
    {orderedRuns.map((run,runIndex)=><div key={run.run_id} ref={run.run_id===latestId?latestRef:undefined}
      className="scroll-mt-4 space-y-5" role="region" aria-label={`第${runIndex+1}轮生成`}>
      {orderedRuns.length>1&&<div className="border-t border-[var(--s-border-default)] pt-4">
        <h2 className="text-base font-semibold">第{runIndex+1}轮生成{run.run_id===latestId?' · 最新':''}</h2>
        {run.requirement&&<details className="mt-2 text-sm text-[var(--s-text-secondary)]"><summary className="cursor-pointer">本轮创作要求</summary><p className="mt-2 whitespace-pre-wrap break-words">{run.requirement}</p></details>}
      </div>}
    {[...run.groups].sort((a,b)=>Number(a.kind==='detail_page')-Number(b.kind==='detail_page')).map((group,index)=><Fragment key={group.plan_id}>
    {index>0&&<div role="separator" aria-label="主图与详情图分区" className="border-t border-[var(--s-border-default)]"/>}
    <Group group={group} projectId={projectId} onRefresh={onRefresh} readOnly={run.run_id!==latestId} onPreview={openPreview}/>
  </Fragment>)}</div>)}
    <PreviewHost state={preview.state} onClose={preview.close} onIndexChange={preview.setIndex}/>
  </div>;
}
