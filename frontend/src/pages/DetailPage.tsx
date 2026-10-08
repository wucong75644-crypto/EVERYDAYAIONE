import { useEffect } from 'react';
import { Sparkles } from 'lucide-react';
import { DetailPageHeader } from '../components/detail-page/DetailPageHeader';
import { GenerationSettings } from '../components/detail-page/GenerationSettings';
import { AnalyzingPanel } from '../components/detail-page/AnalyzingPanel';
import { PlanReviewPanel } from '../components/detail-page/PlanReviewPanel';
import { GenerationProgress } from '../components/detail-page/GenerationProgress';
import { ResultGallery } from '../components/detail-page/ResultGallery';
import { RequirementAssistModal } from '../components/detail-page/RequirementAssistModal';
import { Card } from '../components/ui/Card';
import { PageTransition } from '../components/motion/PageTransition';
import { useDetailRequirementAssist } from '../hooks/useDetailRequirementAssist';
import { DETAIL_STEP_LABELS } from '../mocks/detailPageMocks';
import { useDetailPageStore } from '../stores/useDetailPageStore';

const STEP_PLACEHOLDERS = {
  1: '上传产品图并填写要求后，点击“开始生成”开始',
  2: 'AI 正在分析产品并提取核心卖点',
  3: '检查并编辑即将生成的图片规划',
  4: '图片将按规划逐张生成',
  5: '查看和下载本次生成结果',
} as const;

export default function DetailPage() {
  const step = useDetailPageStore((state) => state.step);
  const images = useDetailPageStore((state) => state.images);
  const form = useDetailPageStore((state) => state.form);
  const projectId = useDetailPageStore((state) => state.projectId);
  const isHydrating = useDetailPageStore((state) => state.isHydrating);
  const formError = useDetailPageStore((state) => state.formError);
  const analysisStage = useDetailPageStore((state) => state.analysisStage);
  const plan = useDetailPageStore((state) => state.plan);
  const generationItems = useDetailPageStore((state) => state.generationItems);
  const addImages = useDetailPageStore((state) => state.addImages);
  const attachWorkspaceImages = useDetailPageStore((state) => state.attachWorkspaceImages);
  const removeImage = useDetailPageStore((state) => state.removeImage);
  const updateForm = useDetailPageStore((state) => state.updateForm);
  const setStep = useDetailPageStore((state) => state.setStep);
  const startAnalysis = useDetailPageStore((state) => state.startAnalysis);
  const cancelAnalysis = useDetailPageStore((state) => state.cancelAnalysis);
  const updatePlanItem = useDetailPageStore((state) => state.updatePlanItem);
  const removePlanItem = useDetailPageStore((state) => state.removePlanItem);
  const replan = useDetailPageStore((state) => state.replan);
  const startGeneration = useDetailPageStore((state) => state.startGeneration);
  const retryGeneration = useDetailPageStore((state) => state.retryGeneration);
  const backToPlan = useDetailPageStore((state) => state.backToPlan);
  const restart = useDetailPageStore((state) => state.restart);
  const reset = useDetailPageStore((state) => state.reset);
  const hydrateDraft = useDetailPageStore((state) => state.hydrateDraft);
  const hasReadyProductImage = images.some((image) => image.category === 'product' && image.status === 'ready');
  const hasPendingImage = images.some((image) => ['local', 'uploading', 'attaching'].includes(image.status));
  const requirementAssist = useDetailRequirementAssist();
  const requirementAssistDisabled = isHydrating || !projectId || !hasReadyProductImage || hasPendingImage;

  const openRequirementAssist = () => {
    if (!projectId || requirementAssistDisabled) return;
    void requirementAssist.open(projectId, form);
  };

  const confirmRequirementAssist = (brief: string) => {
    updateForm({ requirement: brief });
    requirementAssist.close();
  };

  useEffect(() => {
    void hydrateDraft();
    return reset;
  }, [hydrateDraft, reset]);

  return (
    <PageTransition className="h-dvh overflow-hidden flex flex-col bg-[var(--s-surface-base)] text-[var(--s-text-primary)]">
      <div className="shrink-0"><DetailPageHeader /></div>
      <main className="min-h-0 flex-1 w-full max-w-[1600px] mx-auto p-3 sm:p-5">
        <section className="h-full min-h-0 grid grid-cols-[360px_minmax(0,1fr)] gap-4">
          <Card variant="elevated" padding="sm" className="h-full min-h-0 overflow-hidden">
            <GenerationSettings form={form} images={images} error={formError} hasProductImage={hasReadyProductImage && !hasPendingImage} disabled={step !== 1 || isHydrating} requirementAssistDisabled={requirementAssistDisabled} onChange={updateForm} onRequirementAssist={openRequirementAssist} onAnalyze={startAnalysis} onAdd={(files) => void addImages('product', files)} onWorkspaceAdd={(paths) => void attachWorkspaceImages('product', paths)} onRemove={(id) => void removeImage(id)} />
          </Card>
          <Card variant="elevated" padding="lg" className="h-full min-h-0 overflow-y-auto overscroll-contain text-left" aria-label="规划与生成结果" tabIndex={0}>
            {step === 2 ? <AnalyzingPanel stage={analysisStage} onCancel={cancelAnalysis} /> : step === 3 ? <PlanReviewPanel plan={plan} error={formError} onChange={updatePlanItem} onRemove={removePlanItem} onBack={() => setStep(1)} onReplan={replan} onConfirm={startGeneration} /> : step === 4 ? <GenerationProgress items={generationItems} onRetry={retryGeneration} /> : step === 5 ? <ResultGallery items={generationItems} onRetry={retryGeneration} onRestart={restart} onBack={backToPlan} /> : <div className="flex min-h-full flex-col items-center justify-center text-center">
              <div className="w-16 h-16 mx-auto rounded-full bg-[var(--s-surface-secondary)] flex items-center justify-center">
                <Sparkles className="w-7 h-7 text-[var(--s-text-secondary)]" aria-hidden="true" />
              </div>
              <h2 className="mt-4 font-semibold">{DETAIL_STEP_LABELS[step - 1]}</h2>
              <p className="mt-2 text-sm text-[var(--s-text-tertiary)]">{STEP_PLACEHOLDERS[step]}</p>
            </div>}
          </Card>
        </section>
      </main>
      <RequirementAssistModal
        isOpen={requirementAssist.isOpen}
        isLoading={requirementAssist.isLoading}
        result={requirementAssist.result}
        selectedId={requirementAssist.selectedId}
        selectedBrief={requirementAssist.selectedBrief}
        error={requirementAssist.error}
        onClose={requirementAssist.close}
        onSelect={requirementAssist.selectSuggestion}
        onDraftChange={requirementAssist.updateDraft}
        onRegenerate={() => void requirementAssist.regenerate()}
        onConfirm={confirmRequirementAssist}
      />
    </PageTransition>
  );
}
