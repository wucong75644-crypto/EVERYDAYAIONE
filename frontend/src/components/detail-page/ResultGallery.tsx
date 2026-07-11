import { ArrowLeft, RefreshCw } from 'lucide-react';
import type { DetailGenerationItem } from '../../types/detailPage';
import { Button } from '../ui/Button';
import { GenerationCard } from './GenerationCard';

interface ResultGalleryProps { items: DetailGenerationItem[]; onRetry: (id: string) => void; onRestart: () => void; onBack: () => void; }

export function ResultGallery({ items, onRetry, onRestart, onBack }: ResultGalleryProps) {
  const successCount = items.filter((item) => item.status === 'completed').length;
  const failedCount = items.length - successCount;
  return <div className="w-full text-left"><div className="flex flex-wrap justify-between gap-3"><div><h2 className="text-lg font-semibold">本次制作完成</h2><p className="text-sm text-[var(--s-text-tertiary)] mt-1">成功 {successCount} 张{failedCount > 0 ? `，失败 ${failedCount} 张` : ''}</p></div><Button variant="secondary" icon={<RefreshCw className="w-4 h-4" />} onClick={onRestart}>再次制作</Button></div><div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4 mt-5">{items.map((item) => <GenerationCard key={item.id} item={item} onRetry={() => onRetry(item.id)} />)}</div><Button className="mt-6" variant="secondary" icon={<ArrowLeft className="w-4 h-4" />} onClick={onBack}>返回修改方案</Button></div>;
}
