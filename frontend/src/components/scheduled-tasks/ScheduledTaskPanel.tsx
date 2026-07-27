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
import { useEffect, useState, useCallback } from 'react';
import { AnimatePresence, m } from 'framer-motion';
import { AlertCircle, Clock, X, Plus, RefreshCw } from 'lucide-react';
import { Button } from '../ui/Button';
import { ViewSwitcher } from './ViewSwitcher';
import { TaskList } from './TaskList';
import { TaskForm } from './TaskForm';
import { useScheduledTaskStore } from '../../stores/useScheduledTaskStore';
import { FLUID_SPRING } from '../../utils/motion';
import { cn } from '../../utils/cn';
import type { ScheduledTask } from '../../types/scheduledTask';

export interface ScheduledTaskPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

function PanelHeader({ onNew, onClose }: { onNew: () => void; onClose: () => void }) {
  return (
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
          onClick={onNew}
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
  );
}

function TaskLoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      className="flex-1 flex flex-col items-center justify-center px-8 text-center"
      role="alert"
    >
      <AlertCircle className="w-10 h-10 text-[var(--s-error)] mb-3" />
      <p className="text-sm font-medium text-[var(--s-text-primary)]">{message}</p>
      <p className="mt-1 text-xs text-[var(--s-text-tertiary)]">
        请检查网络或稍后重试
      </p>
      <Button
        variant="secondary"
        size="sm"
        icon={<RefreshCw className="w-3.5 h-3.5" />}
        onClick={onRetry}
        className="mt-4"
      >
        重新加载
      </Button>
    </div>
  );
}

export default function ScheduledTaskPanel({ isOpen, onClose }: ScheduledTaskPanelProps) {
  const tasks = useScheduledTaskStore((s) => s.tasks);
  const loading = useScheduledTaskStore((s) => s.loading);
  const error = useScheduledTaskStore((s) => s.error);
  const fetchTasks = useScheduledTaskStore((s) => s.fetchTasks);

  const [showForm, setShowForm] = useState(false);
  const [editingTask, setEditingTask] = useState<ScheduledTask | null>(null);

  // 打开面板时拉取数据
  useEffect(() => {
    if (isOpen) {
      fetchTasks();
    }
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
        } else {
          onClose();
        }
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose, showForm]);

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
            {showForm ? (
              <TaskForm
                task={editingTask}
                onClose={handleFormClose}
                onSaved={() => {
                  handleFormClose();
                  fetchTasks();
                }}
              />
            ) : (
              <>
                {/* 头部 */}
                <PanelHeader onNew={handleNew} onClose={onClose} />

                {/* 视图切换器 */}
                <ViewSwitcher />

                {/* 任务列表 */}
                {error && tasks.length === 0 ? (
                  <TaskLoadError message={error} onRetry={fetchTasks} />
                ) : (
                  <TaskList tasks={tasks} loading={loading} onEdit={handleEdit} />
                )}
              </>
            )}
          </m.aside>
        </>
      )}
    </AnimatePresence>
  );
}
