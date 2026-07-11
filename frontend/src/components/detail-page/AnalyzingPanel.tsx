import { Loader2, X } from 'lucide-react';
import { DETAIL_ANALYSIS_STAGES } from '../../mocks/detailPageMocks';
import { Button } from '../ui/Button';

interface AnalyzingPanelProps {
  stage: number;
  onCancel: () => void;
}

export function AnalyzingPanel({ stage, onCancel }: AnalyzingPanelProps) {
  const progress = Math.round(((stage + 1) / DETAIL_ANALYSIS_STAGES.length) * 100);
  return (
    <div className="w-full max-w-xl mx-auto text-left">
      <div className="flex items-center gap-3">
        <Loader2 className="w-7 h-7 animate-spin text-[var(--s-text-secondary)]" aria-hidden="true" />
        <div><h2 className="font-semibold text-lg">正在分析产品</h2><p className="text-sm text-[var(--s-text-tertiary)]">请稍候，AI 正在为你准备图片规划</p></div>
      </div>
      <div className="mt-6 h-2 rounded-full bg-[var(--s-surface-secondary)] overflow-hidden"><div className="h-full bg-[var(--s-text-primary)] transition-[width]" style={{ width: `${progress}%` }} /></div>
      <ul className="mt-5 space-y-3">
        {DETAIL_ANALYSIS_STAGES.map((label, index) => <li key={label} className={index <= stage ? 'text-[var(--s-text-primary)]' : 'text-[var(--s-text-tertiary)]'}>{index < stage ? '✓' : index === stage ? '●' : '○'} <span className="ml-2">{label}</span></li>)}
      </ul>
      <Button className="mt-7" variant="secondary" icon={<X className="w-4 h-4" />} onClick={onCancel}>取消分析</Button>
    </div>
  );
}
