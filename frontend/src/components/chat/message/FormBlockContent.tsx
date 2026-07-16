/** Presentational shell for an active chat form. */

import type { ReactNode } from 'react';
import { m } from 'framer-motion';
import { Calendar, CheckCircle2, X } from 'lucide-react';
import type { FormPart } from '../../../types/message';
import { cn } from '../../../utils/cn';
import { SOFT_SPRING } from '../../../utils/motion';

interface FormBlockContentProps {
  form: FormPart;
  fields: ReactNode;
  submitting: boolean;
  onSubmit: () => void;
  onCancel: () => void;
}

export function FormBlockContent({
  form,
  fields,
  submitting,
  onSubmit,
  onCancel,
}: FormBlockContentProps) {
  return (
    <m.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={SOFT_SPRING}
      className={cn(
        'my-3 rounded-[var(--s-radius-card)] border overflow-hidden',
        'border-border-default bg-surface shadow-sm',
      )}
    >
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border-default bg-surface-elevated">
        <Calendar size={16} className="text-accent" />
        <span className="text-sm font-medium text-text-primary">{form.title}</span>
      </div>
      {form.description && (
        <p className="px-4 pt-3 text-xs text-text-tertiary">{form.description}</p>
      )}
      {fields}
      <div className="flex items-center gap-3 px-4 py-3 border-t border-border-default bg-surface-elevated">
        <button
          type="button"
          onClick={onSubmit}
          disabled={submitting}
          className={cn(
            'flex flex-1 items-center justify-center gap-1.5 rounded-[var(--s-radius-control)] px-4 py-2 text-sm font-medium',
            'bg-accent text-text-on-accent hover:opacity-90 active:opacity-80',
            'disabled:opacity-50 disabled:cursor-not-allowed transition-opacity duration-150',
          )}
        >
          {submitting ? (
            <><span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/30 border-t-white" />提交中…</>
          ) : (
            <><CheckCircle2 size={14} />{form.submit_text || '确认'}</>
          )}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className={cn(
            'flex items-center justify-center gap-1.5 rounded-[var(--s-radius-control)] px-4 py-2 text-sm',
            'border border-border-default text-text-secondary hover:text-text-primary',
            'hover:bg-hover transition-colors duration-150',
          )}
        >
          <X size={14} />{form.cancel_text || '取消'}
        </button>
      </div>
    </m.div>
  );
}
