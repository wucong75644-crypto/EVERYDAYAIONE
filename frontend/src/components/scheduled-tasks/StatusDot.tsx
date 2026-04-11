/**
 * StatusDot — 任务状态指示点
 *
 * - active: 绿色 + 呼吸动画
 * - paused: 黄色静态
 * - error: 红色闪烁
 * - running: 蓝色旋转
 */
import { m } from 'framer-motion';
import { cn } from '../../utils/cn';
import type { TaskStatus } from '../../types/scheduledTask';

interface Props {
  status: TaskStatus;
  className?: string;
}

const STATUS_COLOR: Record<TaskStatus, string> = {
  active: 'bg-[var(--s-success)]',
  paused: 'bg-[var(--s-warning)]',
  error: 'bg-[var(--s-error)]',
  running: 'bg-[var(--s-accent)]',
};

export function StatusDot({ status, className }: Props) {
  return (
    <div className={cn('relative inline-flex items-center justify-center', className)}>
      <span className={cn('block w-2 h-2 rounded-full', STATUS_COLOR[status])} />
      {status === 'active' && (
        <m.span
          className={cn('absolute inset-0 rounded-full', STATUS_COLOR[status])}
          animate={{ scale: [1, 2.2, 1], opacity: [0.6, 0, 0.6] }}
          transition={{ duration: 1.8, ease: 'easeInOut', repeat: Infinity }}
        />
      )}
      {status === 'error' && (
        <m.span
          className={cn('absolute inset-0 rounded-full', STATUS_COLOR[status])}
          animate={{ opacity: [1, 0.3, 1] }}
          transition={{ duration: 1.5, ease: 'easeInOut', repeat: Infinity }}
        />
      )}
      {status === 'running' && (
        <m.span
          className={cn('absolute inset-0 rounded-full border-2', 'border-[var(--s-accent)]')}
          style={{ borderRightColor: 'transparent', borderTopColor: 'transparent' }}
          animate={{ rotate: 360 }}
          transition={{ duration: 1.2, ease: 'linear', repeat: Infinity }}
        />
      )}
    </div>
  );
}
