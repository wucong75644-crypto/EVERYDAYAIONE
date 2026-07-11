import { useState } from 'react';
import { ArrowLeft, RefreshCw, Sparkles } from 'lucide-react';
import type { DetailPlanItem } from '../../types/detailPage';
import { Button } from '../ui/Button';
import { PlanCard } from './PlanCard';

interface PlanReviewPanelProps { plan: DetailPlanItem[]; error?: string | null; onChange: (id: string, patch: Partial<DetailPlanItem>) => void; onRemove: (id: string) => void; onBack: () => void; onReplan: () => void; onConfirm: () => void; }

export function PlanReviewPanel({ plan, error, onChange, onRemove, onBack, onReplan, onConfirm }: PlanReviewPanelProps) {
  const [confirming, setConfirming] = useState(false);
  return (
    <div className="w-full">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-5"><div><h2 className="text-lg font-semibold">确认图片规划</h2><p className="text-sm text-[var(--s-text-tertiary)] mt-1">共 {plan.length} 张，可在生成前调整文案和提示词</p></div><Button variant="secondary" icon={<RefreshCw className="w-4 h-4" />} onClick={() => setConfirming(true)}>重新规划</Button></div>
      <div className="space-y-4">{plan.map((item) => <PlanCard key={item.id} item={item} canRemove={plan.length > 1} onChange={(patch) => onChange(item.id, patch)} onRemove={() => onRemove(item.id)} />)}</div>
      {confirming && <div role="alertdialog" aria-label="确认重新规划" className="mt-4 rounded-[var(--s-radius-card)] border border-[var(--s-border-default)] bg-[var(--s-surface-secondary)] p-4"><p className="font-medium">重新规划会覆盖当前编辑内容，是否继续？</p><div className="flex gap-2 mt-3"><Button variant="secondary" onClick={() => setConfirming(false)}>取消</Button><Button onClick={() => { onReplan(); setConfirming(false); }}>确认重新规划</Button></div></div>}
      {error && <p role="alert" className="mt-4 text-sm text-[var(--s-error)]">{error}</p>}
      <div className="flex flex-wrap justify-between gap-3 mt-6"><Button variant="secondary" icon={<ArrowLeft className="w-4 h-4" />} onClick={onBack}>返回修改需求</Button><Button icon={<Sparkles className="w-4 h-4" />} onClick={onConfirm}>确认生成</Button></div>
    </div>
  );
}
