import { Check } from 'lucide-react';
import { DETAIL_STEP_LABELS } from '../../mocks/detailPageMocks';
import type { DetailPageStep } from '../../types/detailPage';
import { cn } from '../../utils/cn';

interface StepBarProps {
  step: DetailPageStep;
}

export function StepBar({ step }: StepBarProps) {
  return (
    <nav aria-label="制作进度" className="overflow-x-auto">
      <ol className="min-w-[680px] max-w-4xl mx-auto grid grid-cols-5 gap-3 px-4">
        {DETAIL_STEP_LABELS.map((label, index) => {
          const stepNumber = (index + 1) as DetailPageStep;
          const isCurrent = stepNumber === step;
          const isCompleted = stepNumber < step;
          return (
            <li
              key={label}
              aria-current={isCurrent ? 'step' : undefined}
              className={cn(
                'flex items-center justify-center gap-2 px-3 py-2 rounded-[var(--s-radius-control)] border text-sm whitespace-nowrap',
                isCurrent && 'bg-[var(--c-button-primary-bg)] text-[var(--c-button-primary-fg)] border-transparent',
                isCompleted && 'bg-[var(--s-surface-secondary)] text-[var(--s-text-primary)] border-[var(--s-border-subtle)]',
                !isCurrent && !isCompleted && 'text-[var(--s-text-tertiary)] border-[var(--s-border-default)]',
              )}
            >
              <span className="w-6 h-6 rounded-full bg-current/10 flex items-center justify-center" aria-hidden="true">
                {isCompleted ? <Check className="w-4 h-4" /> : stepNumber}
              </span>
              {label}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
