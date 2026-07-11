import { Download, RefreshCw } from 'lucide-react';
import type { DetailGenerationItem } from '../../types/detailPage';
import { Button } from '../ui/Button';
import { buttonVariants } from '../ui/Button';

const STATUS_LABEL = { waiting: '等待中', generating: '生成中', completed: '已完成', failed: '生成失败' } as const;

interface GenerationCardProps { item: DetailGenerationItem; onRetry: () => void; }

export function GenerationCard({ item, onRetry }: GenerationCardProps) {
  return <article className="overflow-hidden rounded-[var(--s-radius-card)] border border-[var(--s-border-default)] text-left">
    <div className="aspect-square bg-[var(--s-surface-secondary)] flex items-center justify-center">
      {item.previewUrl ? <img src={item.previewUrl} alt={`${item.role}生成结果`} className="w-full h-full object-cover" /> : <span className="text-sm text-[var(--s-text-tertiary)]">{STATUS_LABEL[item.status]}</span>}
    </div>
    <div className="p-3"><div className="flex justify-between gap-2"><h3 className="font-medium">{item.role}</h3><span className="text-xs text-[var(--s-text-tertiary)]">{item.aspectRatio}</span></div>
      {item.error && <p role="alert" className="mt-2 text-sm text-[var(--s-error)]">{item.error}，已退还 {item.refundedCredits} 积分</p>}
      <div className="flex gap-2 mt-3">{item.status === 'failed' && <Button size="sm" variant="secondary" icon={<RefreshCw className="w-4 h-4" />} onClick={onRetry}>重试该张</Button>}{item.status === 'completed' && item.previewUrl && <a href={item.previewUrl} download={`${item.role}.svg`} className={buttonVariants({ size: 'sm', variant: 'secondary' })}><Download className="w-4 h-4" aria-hidden="true" />下载</a>}</div>
    </div>
  </article>;
}
