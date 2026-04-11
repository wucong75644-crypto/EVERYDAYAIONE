/**
 * EmptyState — 无任务时的空状态
 */
import { m } from 'framer-motion';
import { Clock } from 'lucide-react';

export function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-8 text-center text-[var(--s-text-secondary)]">
      <m.div
        animate={{ y: [0, -8, 0] }}
        transition={{ duration: 3, ease: 'easeInOut', repeat: Infinity }}
      >
        <Clock className="w-12 h-12 text-[var(--s-text-tertiary)] mb-4" />
      </m.div>
      <h3 className="text-sm font-medium text-[var(--s-text-primary)] mb-2">
        还没有定时任务
      </h3>
      <p className="text-xs text-[var(--s-text-tertiary)] leading-relaxed">
        让 AI 帮你自动推送日报、预警、周报到企微群
      </p>
      <p className="text-xs text-[var(--s-text-tertiary)] mt-3">
        例如: 每天9点推销售日报到运营群
      </p>
    </div>
  );
}
