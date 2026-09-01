/**
 * ScheduledTaskPanel — 定时任务主面板（右侧抽屉）
 *
 * 设计参考 SearchPanel：
 * - 右侧 Drawer 覆盖式
 * - AnimatePresence + FLUID_SPRING
 * - z-30 backdrop / z-40 panel
 * - Esc 关闭
 *
 * 设计文档: docs/document/UI_定时任务面板设计.md §四
 */
import { useEffect, useState, useCallback, useMemo } from 'react';
import { AnimatePresence, m } from 'framer-motion';
import { Clock, X, Plus } from 'lucide-react';
import { Button } from '../ui/Button';
import { ViewSwitcher } from './ViewSwitcher';
import { TaskList } from './TaskList';
import { TaskForm } from './TaskForm';
import ChangeSetCard from '../chat/message/ChangeSetCard';
import { useScheduledTaskStore } from '../../stores/useScheduledTaskStore';
import { scheduledTaskService } from '../../services/scheduledTask';
import { changeSetService } from '../../services/changeSet';
import type { ChangeSet } from '../../types/changeset';
import { FLUID_SPRING } from '../../utils/motion';
import { cn } from '../../utils/cn';
import type { ScheduledTask } from '../../types/scheduledTask';

export interface ScheduledTaskPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function ScheduledTaskPanel({ isOpen, onClose }: ScheduledTaskPanelProps) {
  const tasks = useScheduledTaskStore((s) => s.tasks);
  const loading = useScheduledTaskStore((s) => s.loading);
  const fetchTasks = useScheduledTaskStore((s) => s.fetchTasks);

  const [showForm, setShowForm] = useState(false);
  const [editingTask, setEditingTask] = useState<ScheduledTask | null>(null);
  const [changeSetId, setChangeSetId] = useState<string | null>(null);

  // 打开或刷新后同时从任务和 ChangeSet 真实状态恢复，绝不把本地表单当流程事实。
  useEffect(() => {
    if (!isOpen) return;
    let active = true;
    void fetchTasks();
    void changeSetService.listActive('scheduled_task')
      .then((changeSets) => {
        if (active && changeSets[0]) {
          setChangeSetId((current) => current ?? changeSets[0].id);
        }
      })
      .catch(() => {
        // 任务列表仍可用；ChangeSetCard 会在用户新建/操作后按 ID 独立加载。
      });
    return () => { active = false; };
  }, [isOpen, fetchTasks]);

  // ESC 全局关闭
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        if (showForm) {
          setShowForm(false);
          setEditingTask(null);
        } else if (changeSetId) {
          setChangeSetId(null);
        } else {
          onClose();
        }
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [changeSetId, isOpen, onClose, showForm]);

  const handleNew = useCallback(() => {
    setEditingTask(null);
    setShowForm(true);
  }, []);

  const handleEdit = useCallback((task: ScheduledTask) => {
    setEditingTask(task);
    setShowForm(true);
  }, []);

  const handleFormClose = useCallback(() => {
    setShowForm(false);
    setEditingTask(null);
  }, []);

  const handleChangeRequested = useCallback(async (
    operation: 'pause' | 'resume' | 'delete', task: ScheduledTask,
  ) => {
    const changeSet = await scheduledTaskService.proposeChange({
      operation,
      task_id: task.id,
    });
    setChangeSetId(changeSet.id);
  }, []);

  const handleFormProposed = useCallback((nextChangeSetId: string) => {
    setShowForm(false);
    setEditingTask(null);
    setChangeSetId(nextChangeSetId);
  }, []);

  const handleChangeSetUpdated = useCallback((changeSet: ChangeSet) => {
    if (changeSet.status === 'applied') void fetchTasks();
  }, [fetchTasks]);

  const replanChangeSet = useCallback(async (changeSet: ChangeSet) => {
    const operation = changeSet.operation as 'create' | 'update' | 'pause' | 'resume' | 'delete';
    const next = await scheduledTaskService.proposeChange({
      operation,
      ...(operation === 'create' ? {} : { task_id: changeSet.resource_id }),
      definition: changeSet.proposed_snapshot,
      idempotency_key: `scheduled-task-panel-replan:${changeSet.id}:${changeSet.revision}`,
    });
    return next;
  }, []);

  const changeSetActionHandlers = useMemo(() => ({
    replan: replanChangeSet,
    resolve_conflict: replanChangeSet,
  }), [replanChangeSet]);

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* 半透明 backdrop */}
          <m.div
            className="fixed inset-0 z-30 bg-black/20"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={onClose}
          />

          {/* 右侧抽屉 */}
          <m.aside
            className={cn(
              'fixed right-0 top-0 bottom-0 z-40',
              'w-full sm:w-[440px]',
              'bg-[var(--s-surface-overlay)]',
              'border-l border-[var(--s-border-default)]',
              'shadow-[var(--s-shadow-drop-xl)]',
              'flex flex-col',
            )}
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={FLUID_SPRING}
            role="dialog"
            aria-label="定时任务面板"
          >
            {changeSetId ? (
              <>
                <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--s-border-default)]">
                  <button type="button" onClick={() => setChangeSetId(null)} aria-label="返回任务列表" className="p-1 rounded text-[var(--s-text-tertiary)] hover:bg-[var(--s-hover)]">
                    <X className="w-4 h-4" />
                  </button>
                  <h2 className="text-sm font-medium text-[var(--s-text-primary)]">确认定时任务变更</h2>
                </div>
                <div className="flex-1 overflow-y-auto px-4 py-2">
                  <ChangeSetCard
                    changeSetId={changeSetId}
                    actionHandlers={changeSetActionHandlers}
                    onChangeSetUpdated={handleChangeSetUpdated}
                  />
                </div>
              </>
            ) : showForm ? (
              <TaskForm
                task={editingTask}
                onClose={handleFormClose}
                onProposed={handleFormProposed}
              />
            ) : (
              <>
                {/* 头部 */}
                <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--s-border-default)]">
                  <div className="flex items-center gap-2">
                    <Clock className="w-4 h-4 text-[var(--s-text-secondary)]" />
                    <h2 className="text-sm font-medium text-[var(--s-text-primary)]">定时任务</h2>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="accent"
                      size="sm"
                      icon={<Plus className="w-3.5 h-3.5" />}
                      onClick={handleNew}
                    >
                      新建
                    </Button>
                    <button
                      type="button"
                      onClick={onClose}
                      aria-label="关闭"
                      className={cn(
                        'p-1 rounded',
                        'text-[var(--s-text-tertiary)]',
                        'hover:bg-[var(--s-hover)] hover:text-[var(--s-text-primary)]',
                        'transition-colors',
                      )}
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* 视图切换器 */}
                <ViewSwitcher />

                {/* 任务列表 */}
                <TaskList tasks={tasks} loading={loading} onEdit={handleEdit} onChangeRequested={handleChangeRequested} />
              </>
            )}
          </m.aside>
        </>
      )}
    </AnimatePresence>
  );
}
