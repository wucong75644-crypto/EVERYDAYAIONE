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
import { ArrowLeft, Clock, GitCompare, Plus, X } from 'lucide-react';
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

const changeSetStatusLabels: Record<ChangeSet['status'], string> = {
  draft: '草案', resolving: '规划中', proposed: '待校验', validating: '校验中',
  preflighting: '试跑中', awaiting_approval: '待确认', committing: '提交中',
  applied: '已提交', cancelled: '已取消', rejected: '审批拒绝', failed: '失败',
  expired: '已过期', conflicted: '发生冲突',
};

function changeSetTitle(changeSet: ChangeSet): string {
  const name = changeSet.proposed_snapshot.name;
  return typeof name === 'string' && name.trim() ? name : '定时任务变更';
}

export default function ScheduledTaskPanel({ isOpen, onClose }: ScheduledTaskPanelProps) {
  const tasks = useScheduledTaskStore((s) => s.tasks);
  const loading = useScheduledTaskStore((s) => s.loading);
  const fetchTasks = useScheduledTaskStore((s) => s.fetchTasks);

  const [showForm, setShowForm] = useState(false);
  const [editingTask, setEditingTask] = useState<ScheduledTask | null>(null);
  const [changeSetId, setChangeSetId] = useState<string | null>(null);
  const [showPendingChanges, setShowPendingChanges] = useState(false);
  const [pendingChangeSets, setPendingChangeSets] = useState<ChangeSet[]>([]);

  const loadPendingChangeSets = useCallback(async () => {
    try {
      setPendingChangeSets(await changeSetService.listActive('scheduled_task'));
    } catch {
      // 保留已读取的待办；任务列表不应因待办读取失败而不可用。
    }
  }, []);

  // 任务列表始终是面板默认入口。待处理 ChangeSet 从服务端恢复为可见待办，
  // 但不得劫持用户的列表导航。
  useEffect(() => {
    if (!isOpen) {
      setChangeSetId(null);
      setShowPendingChanges(false);
      setShowForm(false);
      setEditingTask(null);
      return;
    }
    void fetchTasks();
    void loadPendingChangeSets();
  }, [isOpen, fetchTasks, loadPendingChangeSets]);

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
    setShowPendingChanges(false);
    setEditingTask(null);
    setShowForm(true);
  }, []);

  const handleEdit = useCallback((task: ScheduledTask) => {
    setShowPendingChanges(false);
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
    setShowPendingChanges(false);
    setChangeSetId(changeSet.id);
    void loadPendingChangeSets();
  }, [loadPendingChangeSets]);

  const handleFormProposed = useCallback((nextChangeSetId: string) => {
    setShowForm(false);
    setEditingTask(null);
    setShowPendingChanges(false);
    setChangeSetId(nextChangeSetId);
    void loadPendingChangeSets();
  }, [loadPendingChangeSets]);

  const handleChangeSetUpdated = useCallback((changeSet: ChangeSet) => {
    if (changeSet.status === 'applied') void fetchTasks();
    void loadPendingChangeSets();
  }, [fetchTasks, loadPendingChangeSets]);

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

  const openPendingChange = useCallback((changeSetId: string) => {
    setShowPendingChanges(true);
    setChangeSetId(changeSetId);
  }, []);

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
                  <button type="button" onClick={() => setChangeSetId(null)} aria-label={showPendingChanges ? '返回待处理变更' : '返回任务列表'} className="p-1 rounded text-[var(--s-text-tertiary)] hover:bg-[var(--s-hover)]">
                    <ArrowLeft className="w-4 h-4" />
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
            ) : showPendingChanges ? (
              <>
                <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--s-border-default)]">
                  <button type="button" onClick={() => setShowPendingChanges(false)} aria-label="返回任务列表" className="p-1 rounded text-[var(--s-text-tertiary)] hover:bg-[var(--s-hover)]">
                    <ArrowLeft className="w-4 h-4" />
                  </button>
                  <h2 className="text-sm font-medium text-[var(--s-text-primary)]">待处理变更</h2>
                </div>
                <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2" aria-label="待处理变更列表">
                  {pendingChangeSets.length ? pendingChangeSets.map((changeSet) => (
                    <button
                      key={changeSet.id}
                      type="button"
                      className="w-full rounded-[var(--s-radius-card)] border border-[var(--s-border-default)] bg-[var(--s-surface)] p-3 text-left hover:bg-[var(--s-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--s-accent)]"
                      onClick={() => openPendingChange(changeSet.id)}
                    >
                      <span className="flex items-start gap-2">
                        <GitCompare className="mt-0.5 h-4 w-4 shrink-0 text-[var(--s-accent)]" aria-hidden="true" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium text-[var(--s-text-primary)]">{changeSetTitle(changeSet)}</span>
                          <span className="mt-1 block text-xs text-[var(--s-text-secondary)]">{changeSetStatusLabels[changeSet.status]} · {changeSet.operation}</span>
                        </span>
                      </span>
                    </button>
                  )) : (
                    <p className="py-8 text-center text-sm text-[var(--s-text-tertiary)]">暂无待处理变更</p>
                  )}
                </div>
              </>
            ) : (
              <>
                {/* 头部 */}
                <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--s-border-default)]">
                  <div className="flex items-center gap-2">
                    <Clock className="w-4 h-4 text-[var(--s-text-secondary)]" />
                    <h2 className="text-sm font-medium text-[var(--s-text-primary)]">定时任务</h2>
                  </div>
                  <div className="flex items-center gap-2">
                    {pendingChangeSets.length > 0 && (
                      <Button variant="secondary" size="sm" onClick={() => setShowPendingChanges(true)} icon={<GitCompare className="w-3.5 h-3.5" />}>
                        待处理变更 ({pendingChangeSets.length})
                      </Button>
                    )}
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
