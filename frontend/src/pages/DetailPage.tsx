import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import * as Dialog from '@radix-ui/react-dialog';
import { List, X } from 'lucide-react';
import { useAuthStore } from '../stores/useAuthStore';
import { DetailTaskSidebar } from '../components/detail-page/DetailTaskSidebar';
import { DetailPageHeader } from '../components/detail-page/DetailPageHeader';
import { GenerationSettings } from '../components/detail-page/GenerationSettings';
import { DetailWorkspace } from '../components/detail-page/DetailWorkspace';
import { RequirementAssistModal } from '../components/detail-page/RequirementAssistModal';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { PageTransition } from '../components/motion/PageTransition';
import { useDetailRequirementAssist } from '../hooks/useDetailRequirementAssist';
import { useDetailPageStore } from '../stores/useDetailPageStore';

export default function DetailPage(){
  const state=useDetailPageStore();
  const [searchParams,setSearchParams]=useSearchParams();
  const userId=useAuthStore(s=>s.user?.id);
  const orgId=useAuthStore(s=>s.currentOrgId);
  const scopeKey=`${userId??''}:${orgId??'personal'}`;
  const [taskDrawer,setTaskDrawer]=useState(false);
  const {images,form,projectId,isHydrating,formError,groups,status,models,ratios,isTransitioning,
    addImages,attachWorkspaceImages,removeImage,updateForm,startAnalysis,restart,refresh,hydrateDraft,reset}=state;
  const ready=images.some(image=>image.category==='product'&&image.status==='ready');
  const pending=images.some(image=>image.status!=='ready');
  const requirementDisabled=isHydrating||isTransitioning||!['draft','completed','failed'].includes(status);
  const disabled=requirementDisabled||state.isUploading||state.isMutating;
  const analyzeDisabled=requirementDisabled||state.isUploading||state.isMutating;
  const requirementAssist=useDetailRequirementAssist();
  const closeAssist=requirementAssist.close;
  const requirementAssistDisabled=disabled||!projectId||!ready||pending;
  const openRequirementAssist=()=>{if(projectId&&!requirementAssistDisabled)void requirementAssist.open(projectId,form);};
  const confirmRequirementAssist=(brief:string)=>{
    if(requirementAssist.sourceProjectId===useDetailPageStore.getState().projectId&&!requirementAssistDisabled)updateForm({requirement:brief});
    requirementAssist.close();
  };
  const preferredId=searchParams.get('projectId');
  useEffect(()=>{
    let live=true;
    void hydrateDraft(scopeKey,preferredId).then(()=>{const id=useDetailPageStore.getState().projectId;
      if(live&&id)setSearchParams({projectId:id},{replace:true});});
    return ()=>{live=false;reset();};
    // Task selection has its own save/read boundary; only account changes rehydrate the page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[scopeKey,hydrateDraft,reset]);
  useEffect(()=>{closeAssist();},[projectId,scopeKey,closeAssist]);
  useEffect(()=>{
    const current=useDetailPageStore.getState();
    if(preferredId&&preferredId!==current.projectId&&!current.isHydrating&&!current.isTransitioning&&!current.isUploading&&!current.isMutating){
      void current.selectTask(preferredId).then(()=>{const latest=useDetailPageStore.getState();
        if(latest.scopeKey===scopeKey&&latest.projectId)setSearchParams({projectId:latest.projectId},{replace:true});});
    }
  },[preferredId,scopeKey,setSearchParams]);
  useEffect(()=>{
    const refreshVisible=()=>{if(!document.hidden){void useDetailPageStore.getState().refreshTasks();void useDetailPageStore.getState().refresh();}};
    window.addEventListener('focus',refreshVisible);document.addEventListener('visibilitychange',refreshVisible);
    return ()=>{window.removeEventListener('focus',refreshVisible);document.removeEventListener('visibilitychange',refreshVisible);};
  },[]);
  const switchDisabled=isHydrating||isTransitioning||state.isUploading||state.isMutating;
  const changeTask=async(id?:string)=>{
    if(switchDisabled)return;
    requirementAssist.close();
    if(id)await state.selectTask(id);else await restart();
    const selected=useDetailPageStore.getState().projectId;
    if(selected&&useDetailPageStore.getState().scopeKey===scopeKey){setSearchParams({projectId:selected},{replace:true});setTaskDrawer(false);}
  };
  const sidebar=<DetailTaskSidebar key={scopeKey} tasks={state.tasks} selectedId={projectId} disabled={switchDisabled}
    loading={state.isLoadingTasks} hasMore={!!state.taskCursor} error={state.taskError}
    onCreate={()=>void changeTask()} onSelect={id=>void changeTask(id)} onMore={()=>void state.loadMoreTasks()} onRefresh={()=>void state.refreshTasks()}
    onDelete={async id=>{await state.deleteTask(id);const current=useDetailPageStore.getState();
      if(current.scopeKey===scopeKey)setSearchParams(current.projectId?{projectId:current.projectId}:{},{replace:true});}}/>;
  return <PageTransition className="flex h-dvh flex-col overflow-hidden bg-[var(--s-surface-base)] text-[var(--s-text-primary)]">
    <div className="shrink-0"><DetailPageHeader/></div>
    <main className="mx-auto min-h-0 w-full max-w-[2200px] flex-1 p-3 sm:p-5">
      <div className="mb-2 flex justify-end xl:hidden"><Button variant="ghost" size="sm" icon={<List className="h-4 w-4"/>} onClick={()=>setTaskDrawer(true)}>任务列表</Button></div>
      <section className="grid h-full min-h-0 grid-cols-1 gap-4 md:grid-cols-[360px_minmax(0,1fr)] xl:grid-cols-[360px_minmax(0,1fr)_256px] max-xl:h-[calc(100%-40px)] max-md:overflow-y-auto">
        <Card variant="elevated" padding="sm" aria-label="创作设置" className="relative h-full min-h-0 overflow-hidden max-md:min-h-[600px]">
          <GenerationSettings form={form} images={images} models={models} ratios={ratios} hasProductImage={ready&&!pending} disabled={disabled} requirementDisabled={requirementDisabled}
            analyzeDisabled={analyzeDisabled} analyzeLabel={status==='draft'?'开始生成':'再次生成'}
            requirementAssistDisabled={requirementAssistDisabled} onChange={updateForm} onRequirementAssist={openRequirementAssist} onAnalyze={()=>void startAnalysis()} onAdd={files=>void addImages('product',files)}
            onWorkspaceAdd={paths=>void attachWorkspaceImages('product',paths)} onRemove={id=>void removeImage(id)}/>
          {formError&&<p role="alert" className="absolute inset-x-4 bottom-16 rounded-lg bg-[var(--s-surface-raised)] p-2 text-xs text-[var(--s-error)] shadow-sm">{formError}{!projectId&&<button type="button" className="ml-2 underline" onClick={()=>void hydrateDraft(scopeKey,preferredId)}>重新加载</button>}</p>}
        </Card>
        <Card variant="elevated" padding="lg" aria-label="规划与生成结果" className="h-full min-h-0 min-w-0 overflow-y-auto overscroll-contain" tabIndex={0}>
          <DetailWorkspace key={projectId} groups={groups} runs={state.runs} currentRunId={state.currentRunId} projectId={projectId} onRefresh={()=>void refresh()}/>
          {['completed','failed'].includes(status)&&<div className="mt-5 flex justify-end"><Button onClick={()=>void changeTask()} disabled={switchDisabled}>开始新任务</Button></div>}
        </Card>
        <Card variant="elevated" padding="sm" className="hidden h-full min-h-0 overflow-hidden xl:block">{sidebar}</Card>
      </section>
    </main>
      <Dialog.Root open={taskDrawer} onOpenChange={setTaskDrawer}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30"/>
          <Dialog.Content aria-describedby={undefined} className="fixed inset-y-0 right-0 z-50 flex w-[300px] max-w-[90vw] flex-col bg-[var(--s-surface-raised)] p-4 shadow-xl focus:outline-none">
            <div className="mb-3 flex items-center justify-between"><Dialog.Title className="font-semibold">任务列表</Dialog.Title><Dialog.Close asChild><Button variant="ghost" size="sm" aria-label="关闭任务列表"><X className="h-4 w-4"/></Button></Dialog.Close></div>
            <div className="min-h-0 flex-1">{sidebar}</div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <RequirementAssistModal
        isOpen={requirementAssist.isOpen}
        isLoading={requirementAssist.isLoading}
        draft={requirementAssist.draft}
        brief={requirementAssist.brief}
        error={requirementAssist.error}
        validationError={requirementAssist.validationError}
        supplement={requirementAssist.supplement}
        answers={requirementAssist.answers}
        skippedQuestions={requirementAssist.skippedQuestions}
        onClose={requirementAssist.close}
        onDraftChange={requirementAssist.updateDraft}
        onSupplementChange={requirementAssist.setSupplement}
        onAnswer={requirementAssist.answerQuestion}
        onToggleSkip={requirementAssist.toggleSkip}
        onUpdate={() => void requirementAssist.update()}
        onConfirm={confirmRequirementAssist}
      />
  </PageTransition>;
}
