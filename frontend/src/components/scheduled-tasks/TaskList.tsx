/**
 * TaskList — 任务列表（按状态分组）
 *
 * 分组：运行中 / 已暂停 / 失败
 */
import { AnimatePresence } from 'framer-motion';
import { TaskCard } from './TaskCard';
import { EmptyState } from './EmptyState';
import type { ScheduledTask } from '../../types/scheduledTask';

interface Props {
  tasks: ScheduledTask[];
  loading: boolean;
  onEdit?: (task: ScheduledTask) => void;
}

interface SectionProps {
  title: string;
  count: number;
  children: React.ReactNode;
}

function Section({ title, count, children }: SectionProps) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-2 px-1">
        <span className="text-xs font-medium text-[var(--s-text-tertiary)] uppercase tracking-wider">
          {title}
        </span>
        <span className="text-xs text-[var(--s-text-tertiary)]">({count})</span>
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

export function TaskList({ tasks, loading, onEdit }: Props) {
  if (loading && tasks.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--s-text-tertiary)] text-sm">
        加载中...
      </div>
    );
  }

  if (tasks.length === 0) {
    return <EmptyState />;
  }

  const grouped = {
    active: tasks.filter((t) => t.status === 'active' || t.status === 'running'),
    paused: tasks.filter((t) => t.status === 'paused'),
    error: tasks.filter((t) => t.status === 'error'),
  };

  return (
    <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
      {grouped.active.length > 0 && (
        <Section title="运行中" count={grouped.active.length}>
          <AnimatePresence>
            {grouped.active.map((t) => (
              <TaskCard key={t.id} task={t} onEdit={onEdit} />
            ))}
          </AnimatePresence>
        </Section>
      )}
      {grouped.paused.length > 0 && (
        <Section title="已暂停" count={grouped.paused.length}>
          <AnimatePresence>
            {grouped.paused.map((t) => (
              <TaskCard key={t.id} task={t} onEdit={onEdit} />
            ))}
          </AnimatePresence>
        </Section>
      )}
      {grouped.error.length > 0 && (
        <Section title="失败" count={grouped.error.length}>
          <AnimatePresence>
            {grouped.error.map((t) => (
              <TaskCard key={t.id} task={t} onEdit={onEdit} />
            ))}
          </AnimatePresence>
        </Section>
      )}
    </div>
  );
}
