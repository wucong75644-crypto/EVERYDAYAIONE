import { useEffect } from 'react';
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
  const {images,form,projectId,isHydrating,formError,groups,status,models,ratios,isTransitioning,
    addImages,attachWorkspaceImages,removeImage,updateForm,startAnalysis,restart,refresh,hydrateDraft,reset}=state;
  const ready=images.some(image=>image.category==='product'&&image.status==='ready');
  const pending=images.some(image=>image.status!=='ready');
  const disabled=isHydrating||isTransitioning||state.isUploading||status!=='draft';
  const requirementAssist=useDetailRequirementAssist();
  const requirementAssistDisabled=disabled||!projectId||!ready||pending;
  const openRequirementAssist=()=>{if(projectId&&!requirementAssistDisabled)void requirementAssist.open(projectId,form);};
  const confirmRequirementAssist=(brief:string)=>{updateForm({requirement:brief});requirementAssist.close();};
  useEffect(()=>{void hydrateDraft();return reset;},[hydrateDraft,reset]);
  return <PageTransition className="flex h-dvh flex-col overflow-hidden bg-[var(--s-surface-base)] text-[var(--s-text-primary)]">
    <div className="shrink-0"><DetailPageHeader/></div>
    <main className="mx-auto min-h-0 w-full max-w-[1800px] flex-1 p-3 sm:p-5">
      <section className="grid h-full min-h-0 grid-cols-[360px_minmax(0,1fr)] gap-4">
        <Card variant="elevated" padding="sm" aria-label="创作设置" className="relative h-full min-h-0 overflow-hidden">
          <GenerationSettings form={form} images={images} models={models} ratios={ratios} hasProductImage={ready&&!pending} disabled={disabled}
            requirementAssistDisabled={requirementAssistDisabled} onChange={updateForm} onRequirementAssist={openRequirementAssist} onAnalyze={()=>void startAnalysis()} onAdd={files=>void addImages('product',files)}
            onWorkspaceAdd={paths=>void attachWorkspaceImages('product',paths)} onRemove={id=>void removeImage(id)}/>
          {formError&&<p role="alert" className="absolute inset-x-4 bottom-16 rounded-lg bg-[var(--s-surface-card)] p-2 text-xs text-[var(--s-error)] shadow-sm">{formError}</p>}
        </Card>
        <Card variant="elevated" padding="lg" aria-label="规划与生成结果" className="h-full min-h-0 min-w-0 overflow-y-auto overscroll-contain" tabIndex={0}>
          <DetailWorkspace groups={groups} projectId={projectId} onRefresh={()=>void refresh()}/>
          {['completed','failed'].includes(status)&&<div className="mt-5 flex justify-end"><Button onClick={()=>void restart()}>开始新任务</Button></div>}
        </Card>
      </section>
    </main>
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
