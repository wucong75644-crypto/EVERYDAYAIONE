import { useEffect } from 'react';
import { Sparkles } from 'lucide-react';
import { DetailPageHeader } from '../components/detail-page/DetailPageHeader';
import { GenerationSettings } from '../components/detail-page/GenerationSettings';
import { ProductImageSection } from '../components/detail-page/ProductImageSection';
import { StepBar } from '../components/detail-page/StepBar';
import { AnalyzingPanel } from '../components/detail-page/AnalyzingPanel';
import { PlanReviewPanel } from '../components/detail-page/PlanReviewPanel';
import { GenerationProgress } from '../components/detail-page/GenerationProgress';
import { ResultGallery } from '../components/detail-page/ResultGallery';
import { Card } from '../components/ui/Card';
import { PageTransition } from '../components/motion/PageTransition';
import { DETAIL_STEP_LABELS } from '../mocks/detailPageMocks';
import { useDetailPageStore } from '../stores/useDetailPageStore';

const STEP_PLACEHOLDERS = {
  1: '上传产品图并填写要求后，点击“分析产品”开始',
  2: 'AI 正在分析产品并提取核心卖点',
  3: '检查并编辑即将生成的图片规划',
  4: '图片将按规划逐张生成',
  5: '查看和下载本次生成结果',
} as const;

export default function DetailPage() {
  const step = useDetailPageStore((state) => state.step);
  const images = useDetailPageStore((state) => state.images);
  const form = useDetailPageStore((state) => state.form);
  const formError = useDetailPageStore((state) => state.formError);
  const analysisStage = useDetailPageStore((state) => state.analysisStage);
  const plan = useDetailPageStore((state) => state.plan);
  const generationItems = useDetailPageStore((state) => state.generationItems);
  const addImages = useDetailPageStore((state) => state.addImages);
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
  const hasProductImage = images.some((image) => image.category === 'product');

  useEffect(() => reset, [reset]);

  return (
    <PageTransition className="min-h-screen bg-[var(--s-surface-base)] text-[var(--s-text-primary)]">
      <DetailPageHeader />
      <main className="max-w-[1600px] mx-auto px-4 sm:px-6 py-8">
        <section className="text-center mb-7">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-[var(--s-border-default)] bg-[var(--s-surface-card)] text-sm">
            <Sparkles className="w-4 h-4" aria-hidden="true" />
            AI 主图&详情页
          </div>
          <h1 className="mt-4 text-2xl sm:text-3xl font-semibold">AI 帮写需求，一键生成详情图组</h1>
          <p className="mt-2 text-[var(--s-text-secondary)]">上传产品图，AI 智能分析并规划多角度、多场景的电商图片</p>
        </section>
        <StepBar step={step} />
        <section className="mt-6 grid lg:grid-cols-[360px_minmax(0,1fr)] gap-5">
          <Card variant="elevated" padding="lg" className="min-h-[520px]">
            <ProductImageSection images={images} error={formError} disabled={step !== 1} onAdd={addImages} onRemove={removeImage} />
            <GenerationSettings form={form} hasProductImage={hasProductImage} disabled={step !== 1} onChange={updateForm} onAnalyze={startAnalysis} />
          </Card>
          <Card variant="elevated" padding="lg" className="min-h-[520px] flex items-center justify-center text-center">
            {step === 2 ? <AnalyzingPanel stage={analysisStage} onCancel={cancelAnalysis} /> : step === 3 ? <PlanReviewPanel plan={plan} error={formError} onChange={updatePlanItem} onRemove={removePlanItem} onBack={() => setStep(1)} onReplan={replan} onConfirm={startGeneration} /> : step === 4 ? <GenerationProgress items={generationItems} onRetry={retryGeneration} /> : step === 5 ? <ResultGallery items={generationItems} onRetry={retryGeneration} onRestart={restart} onBack={backToPlan} /> : <div>
              <div className="w-16 h-16 mx-auto rounded-full bg-[var(--s-surface-secondary)] flex items-center justify-center">
                <Sparkles className="w-7 h-7 text-[var(--s-text-secondary)]" aria-hidden="true" />
              </div>
              <h2 className="mt-4 font-semibold">{DETAIL_STEP_LABELS[step - 1]}</h2>
              <p className="mt-2 text-sm text-[var(--s-text-tertiary)]">{STEP_PLACEHOLDERS[step]}</p>
            </div>}
          </Card>
        </section>
      </main>
    </PageTransition>
  );
}
