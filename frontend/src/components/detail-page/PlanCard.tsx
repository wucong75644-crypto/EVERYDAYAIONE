import { useState } from 'react';
import { ChevronDown, ChevronUp, Trash2 } from 'lucide-react';
import type { DetailPlanItem } from '../../types/detailPage';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';

interface PlanCardProps { item: DetailPlanItem; canRemove: boolean; onChange: (patch: Partial<DetailPlanItem>) => void; onRemove: () => void; }

export function PlanCard({ item, canRemove, onChange, onRemove }: PlanCardProps) {
  const [expanded, setExpanded] = useState(false);
  return (
    <article className="rounded-[var(--s-radius-card)] border border-[var(--s-border-default)] p-4">
      <div className="flex items-start justify-between gap-3"><div><span className="text-xs text-[var(--s-text-tertiary)]">{item.role}</span><h3 className="font-medium mt-1">{item.purpose}</h3><p className="text-sm text-[var(--s-text-secondary)] mt-1">{item.composition}</p></div><Button aria-label={`删除${item.role}`} variant="danger" size="sm" disabled={!canRemove} icon={<Trash2 className="w-4 h-4" />} onClick={onRemove} /></div>
      <div className="grid sm:grid-cols-2 gap-3 mt-4"><Input label="标题" value={item.title} onChange={(event) => onChange({ title: event.target.value })} /><Input label="副标题" value={item.subtitle} onChange={(event) => onChange({ subtitle: event.target.value })} /></div>
      <Button className="mt-3" variant="ghost" size="sm" icon={expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />} onClick={() => setExpanded((value) => !value)}>高级提示词</Button>
      {expanded && <textarea aria-label="高级提示词" className="mt-2 w-full min-h-24 p-3 text-sm rounded-[var(--c-input-radius)] border border-[var(--c-input-border)] bg-[var(--c-input-bg)]" value={item.prompt} onChange={(event) => onChange({ prompt: event.target.value })} />}
    </article>
  );
}
