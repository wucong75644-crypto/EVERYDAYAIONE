/**
 * TaskCard — 单个定时任务卡片
 *
 * - 状态点 + 任务名 + cron 描述 + 推送目标
 * - 上次执行 / 下次执行
 * - hover 显示操作按钮（暂停 / 立即执行 / 编辑 / 删除）
 * - 老板/主管视角显示 CreatorBadge
 */
import { m, AnimatePresence } from 'framer-motion';
import { Pause, Play, Settings, Trash2, Paperclip, Clock, ChevronDown } from 'lucide-react';
import { Card } from '../ui/Card';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { StatusDot } from './StatusDot';
import { CreatorBadge } from './CreatorBadge';
import { TaskRunHistory } from './TaskRunHistory';
import { useScheduledTaskStore } from '../../stores/useScheduledTaskStore';
import { useAuthStore } from '../../stores/useAuthStore';
import { usePermission, useCanExecuteTask } from '../../hooks/usePermission';
import { SOFT_SPRING } from '../../utils/motion';
import { cn } from '../../utils/cn';
import type { ScheduledTask } from '../../types/scheduledTask';

interface Props {
  task: ScheduledTask;
  onEdit?: (task: ScheduledTask) => void;
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '';
  try {
    const date = new Date(iso);
    const diff = Date.now() - date.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return '刚刚';
    if (mins < 60) return `${mins} 分钟前`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours} 小时前`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days} 天前`;
    return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
  } catch {
    return '';
  }
}

function formatNextRun(iso: string | null | undefined): string {
  if (!iso) return '';
  try {
    const date = new Date(iso);
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '';
  }
}

export function TaskCard({ task, onEdit }: Props) {
  const pauseTask = useScheduledTaskStore((s) => s.pauseTask);
  const resumeTask = useScheduledTaskStore((s) => s.resumeTask);
  const deleteTask = useScheduledTaskStore((s) => s.deleteTask);
  const runTaskNow = useScheduledTaskStore((s) => s.runTaskNow);
  const expandedTaskId = useScheduledTaskStore((s) => s.expandedTaskId);
  const setExpandedTaskId = useScheduledTaskStore((s) => s.setExpandedTaskId);

  const currentUser = useAuthStore((s) => s.user);
  const showCreator = currentUser?.id !== task.user_id;
  const isExpanded = expandedTaskId === task.id;

  const canEdit = usePermission('task.edit', task);
  const canDelete = usePermission('task.delete', task);
  const canExecute = useCanExecuteTask(task);

  const isPaused = task.status === 'paused';
  const isError = task.status === 'error';

  const handleToggle = async () => {
    if (isPaused) {
      await resumeTask(task.id);
    } else {
      await pauseTask(task.id);
    }
  };

  const handleDelete = async () => {
    if (!confirm(`确认删除任务「${task.name}」？`)) return;
    await deleteTask(task.id);
  };

  const handleRunNow = async () => {
    await runTaskNow(task.id);
  };

  const handleToggleExpand = (e: React.MouseEvent) => {
    // 点击按钮区不触发展开
    if ((e.target as HTMLElement).closest('button')) return;
    setExpandedTaskId(isExpanded ? null : task.id);
  };

  return (
    <m.div
      layout
      initial={{ opacity: 0, y: -8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, x: 32, scale: 0.95 }}
      transition={SOFT_SPRING}
      className={cn(isPaused && 'opacity-65')}
    >
      <Card variant="default" padding="md" className="group cursor-pointer" onClick={handleToggleExpand}>
        <div className="flex items-start justify-between gap-3">
          {/* 状态点 + 任务信息 */}
          <div className="flex items-start gap-2 min-w-0 flex-1">
            <div className="mt-1 shrink-0">
              <StatusDot status={task.status} />
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h4 className="text-sm font-medium text-[var(--s-text-primary)] truncate">
                  {task.name}
                </h4>
                {task.template_file && (
                  <span title={task.template_file.name} className="text-[var(--s-text-tertiary)]">
                    <Paperclip className="w-3.5 h-3.5" />
                  </span>
                )}
              </div>

              <p className="text-xs text-[var(--s-text-tertiary)] mt-0.5 truncate">
                {task.cron_readable || task.cron_expr}
                {task.push_target.chat_name && ` · ${task.push_target.chat_name}`}
                {task.push_target.name && ` · ${task.push_target.name}`}
              </p>

              {/* 上次执行 + 状态 */}
              {task.last_run_at && (
                <p className="text-xs text-[var(--s-text-tertiary)] mt-1 flex items-center gap-1.5 flex-wrap">
                  <span>上次: {formatRelative(task.last_run_at)}</span>
                  {task.last_result?.status === 'success' && (
                    <Badge variant="success" size="sm">✓</Badge>
                  )}
                  {isError && (
                    <Badge variant="error" size="sm" pulse>失败</Badge>
                  )}
                </p>
              )}

              {/* 下次执行 */}
              {task.next_run_at && task.status === 'active' && (
                <p className="text-xs text-[var(--s-text-tertiary)] mt-1 flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  下次: {formatNextRun(task.next_run_at)}
                </p>
              )}

              {/* 创建者徽标 */}
              {showCreator && task.creator && (
                <div className="mt-2">
                  <CreatorBadge creator={task.creator} />
                </div>
              )}
            </div>
          </div>

          {/* 展开 chevron */}
          <m.div
            animate={{ rotate: isExpanded ? 180 : 0 }}
            transition={{ duration: 0.2 }}
            className="text-[var(--s-text-tertiary)] mt-1"
          >
            <ChevronDown className="w-3.5 h-3.5" />
          </m.div>

          {/* 操作按钮（hover 显示） */}
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            {canExecute && task.status === 'active' && (
              <Button
                variant="ghost"
                size="sm"
                icon={<Play className="w-3.5 h-3.5" />}
                onClick={handleRunNow}
                aria-label="立即执行"
                title="立即执行"
              />
            )}
            {canEdit && (
              <Button
                variant="ghost"
                size="sm"
                icon={isPaused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
                onClick={handleToggle}
                aria-label={isPaused ? '恢复' : '暂停'}
                title={isPaused ? '恢复' : '暂停'}
              />
            )}
            {canEdit && onEdit && (
              <Button
                variant="ghost"
                size="sm"
                icon={<Settings className="w-3.5 h-3.5" />}
                onClick={() => onEdit(task)}
                aria-label="编辑"
                title="编辑"
              />
            )}
            {canDelete && (
              <Button
                variant="danger"
                size="sm"
                icon={<Trash2 className="w-3.5 h-3.5" />}
                onClick={handleDelete}
                aria-label="删除"
                title="删除"
              />
            )}
          </div>
        </div>

        {/* 展开后显示执行历史 */}
        <AnimatePresence>
          {isExpanded && (
            <m.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              <TaskRunHistory taskId={task.id} />
            </m.div>
          )}
        </AnimatePresence>
      </Card>
    </m.div>
  );
}
