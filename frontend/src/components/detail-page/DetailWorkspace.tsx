import { Fragment, useEffect, useRef, useState } from 'react';
import { Download, Sparkles } from 'lucide-react';
import toast from 'react-hot-toast';
import type { DetailGroup, DetailImageTask } from '../../types/detailPage';
import { AiGeneratedImage } from '../chat/message/MessageImageBlocks';
import ChatImageControls from '../chat/media/ChatImageControls';
import { FailedMediaPlaceholder } from '../chat/media/MediaPlaceholder';
import ImagePreviewModal from '../chat/media/ImagePreviewModal';
import { chatImageService } from '../../services/chatImage';
import { resumeDetailPlan } from '../../services/detailProject';
import { getImagePlaceholderSize } from '../../utils/settingsStorage';
import { detailImageDownloads, detailTaskFor as taskFor } from '../../utils/detailPageImages';
import { downloadWorkspaceZip } from '../../services/workspace';
import { Button } from '../ui/Button';

const stages=['卖点分析','视觉定位','逐图提示词'];
const previewWidth=getImagePlaceholderSize('1:1').width;
function Group({group,onRefresh,projectId}:{group:DetailGroup;onRefresh:()=>void;projectId:string|null}){
  const accepted=group.tasks.length>0;
  const [promptOpen,setPromptOpen]=useState<boolean|null>(null);
  const [preview,setPreview]=useState<string|null>(null);
  const [retrying,setRetrying]=useState<string|null>(null);
  const requestIds=useRef(new Map<string,string>());
  useEffect(()=>{ if(accepted)setPromptOpen(false); },[accepted]);
  const failed=['failed','cancelled','needs_input','insufficient'].includes(group.status)||!!group.acceptance_error;
  const completed=group.items.filter(item=>taskFor(group,item.item_id)?.status==='completed').length;
  async function retry(task:DetailImageTask){
    if(retrying)return;setRetrying(task.id);
    if(!requestIds.current.has(task.id))requestIds.current.set(task.id,crypto.randomUUID());
    try{await chatImageService.replay(task.id,requestIds.current.get(task.id)!);}
    catch(error){toast.error(error instanceof Error?error.message:'重试失败');}
    finally{setRetrying(null);onRefresh();}
  }
  return <section className="rounded-2xl border border-[var(--s-border-subtle)] p-4 sm:p-5" aria-label={group.kind==='main_images'?'主图工作区':'详情图工作区'}>
    <div className="mb-4 flex items-center justify-between gap-3"><h2 className="font-semibold">{group.kind==='main_images'?'主图':'详情图'} · {group.count}张</h2>
      <span className="text-xs text-[var(--s-text-tertiary)]" role="status">{accepted?`已完成 ${completed}/${group.count}`:failed?'需要处理':group.status==='ready'?'提示词已完成，准备生图':'正在策划'}</span></div>
    <ol className="mb-4 grid grid-cols-3 gap-2 text-xs" aria-label="策划阶段">
      {stages.map((label,index)=><li key={label} className={index+1<=group.stage?'text-[var(--s-accent)]':'text-[var(--s-text-tertiary)]'}>
        {index+1<group.stage||group.status==='ready'?'✓':index+1===group.stage?'●':'○'} {label}</li>)}
    </ol>
    {failed&&<div className="mb-4 rounded-xl bg-[var(--s-surface-secondary)] p-3 text-sm" role="alert">
      {group.status==='needs_input'||group.status==='insufficient'?'需要补充产品信息，请根据提示完善要求后重新开始。':group.acceptance_error?
        `图片任务尚未受理：${group.acceptance_error.code??'提交失败'}`:`策划未完成：${group.error?.message??group.error?.code??'服务调用失败'}`}
      {!!group.questions?.length&&<pre className="mt-2 whitespace-pre-wrap text-xs">{JSON.stringify(group.questions,null,2)}</pre>}
      {(group.can_resume||group.acceptance_error)&&projectId&&<button type="button" className="mt-2 text-[var(--s-accent)]" disabled={!!retrying} onClick={()=>{
        setRetrying(group.plan_id);void resumeDetailPlan(projectId,group.plan_id,crypto.randomUUID()).then(onRefresh)
          .catch(error=>toast.error(error instanceof Error?error.message:'恢复失败')).finally(()=>setRetrying(null));
      }}>{group.acceptance_error?'重试提交生图':'从失败阶段继续'}</button>}
    </div>}
    {!!group.items.length&&<div className="mb-4">
      <button type="button" className="text-sm text-[var(--s-accent)]" aria-expanded={promptOpen??!accepted} onClick={()=>setPromptOpen(!(promptOpen??!accepted))}>
        {(promptOpen??!accepted)?'收起提示词':'展开提示词'} · {group.items.length}份</button>
      {(promptOpen??!accepted)&&<div className="mt-3 max-h-[480px] space-y-3 overflow-y-auto">
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
        const isFailed=['failed','cancelled'].includes(task.status);
        return <article key={item.item_id} className="min-w-0 overflow-hidden">
          <h3 className="mb-1 truncate text-sm">{item.position}. {item.name}</h3>
          <ChatImageControls taskId={task.id}/>
          {isFailed?<div className="mt-3"><FailedMediaPlaceholder type="image" aspectRatio={w/h} errorMessage={task.error_message||'生成失败'} onRetry={()=>void retry(task)} retryLabel={retrying===task.id?'正在受理…':'重新生成'}/></div>:
          <AiGeneratedImage fitContainer renderId={task.id} imageAsset={url?{originalUrl:url,thumbnailUrl:result?.thumbnail_url}:null}
            placeholderSize={size} isGenerating={task.status!=='completed'} onImageClick={()=>setPreview(url??null)}/>}
          <p className="mt-2 text-xs text-[var(--s-text-tertiary)]">{isFailed?'生成失败':task.status==='completed'?'已完成':task.submission_state==='queued'?'排队中':task.submission_state==='uncertain'?'正在核实供应商受理结果':'生成中'}</p>
        </article>;
      })}
    </div>}
    <ImagePreviewModal imageUrl={preview} onClose={()=>setPreview(null)}/>
  </section>;
}
export function DetailWorkspace({groups,onRefresh,projectId}:{groups:DetailGroup[];onRefresh:()=>void;projectId:string|null}){
  const [downloading,setDownloading]=useState(false);
  const downloadInFlight=useRef(false);
  const downloads=detailImageDownloads(groups);
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
  if(!groups.length)return <div className="flex h-full items-center justify-center text-center">
    <div><Sparkles className="mx-auto mb-5 h-8 w-8 text-[var(--s-text-tertiary)]"/><h2 className="font-semibold">输入</h2>
      <p className="mt-3 text-sm text-[var(--s-text-tertiary)]">上传产品图并填写要求后，点击“开始生成”开始</p></div></div>;
  return <div className="space-y-5">
    <div className="flex justify-end"><Button variant="secondary" size="sm" icon={<Download className="h-4 w-4"/>}
      disabled={!downloads.length} loading={downloading} title="下载已完成的原图，按主图和详情页分类" onClick={()=>void downloadAll()}>
      {downloading?'打包中…':`批量下载（${downloads.length}张）`}
    </Button></div>
    {[...groups].sort((a,b)=>Number(a.kind==='detail_page')-Number(b.kind==='detail_page')).map((group,index)=><Fragment key={group.plan_id}>
    {index>0&&<div role="separator" aria-label="主图与详情图分区" className="border-t border-[var(--s-border-default)]"/>}
    <Group group={group} projectId={projectId} onRefresh={onRefresh}/>
  </Fragment>)}</div>;
}
