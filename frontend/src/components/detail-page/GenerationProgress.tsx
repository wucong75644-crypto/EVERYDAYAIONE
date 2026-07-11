import type { DetailGenerationItem } from '../../types/detailPage';
import { GenerationCard } from './GenerationCard';

interface GenerationProgressProps { items: DetailGenerationItem[]; onRetry: (id: string) => void; }

export function GenerationProgress({ items, onRetry }: GenerationProgressProps) {
  const finished = items.filter((item) => item.status === 'completed' || item.status === 'failed').length;
  return <div className="w-full text-left"><h2 className="text-lg font-semibold">正在生成 {finished}/{items.length}</h2><p className="text-sm text-[var(--s-text-tertiary)] mt-1">每张图片独立生成，已完成的结果会立即显示</p><div className="mt-4 h-2 rounded-full bg-[var(--s-surface-secondary)] overflow-hidden"><div className="h-full bg-[var(--s-text-primary)] transition-[width]" style={{ width: `${items.length ? finished / items.length * 100 : 0}%` }} /></div><div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4 mt-5">{items.map((item) => <GenerationCard key={item.id} item={item} onRetry={() => onRetry(item.id)} />)}</div></div>;
}
