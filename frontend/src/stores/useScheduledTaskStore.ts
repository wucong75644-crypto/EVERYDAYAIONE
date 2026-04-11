/**
 * 定时任务状态管理（Zustand）
 *
 * 设计文档: docs/document/UI_定时任务面板设计.md §七
 */
import { create } from 'zustand';
import type {
  ScheduledTask,
  TaskRun,
  CreateTaskDto,
  UpdateTaskDto,
} from '../types/scheduledTask';
import { scheduledTaskService } from '../services/scheduledTask';
import { logger } from '../utils/logger';

type ViewMode = 'default' | 'mine' | 'dept';

interface ScheduledTaskState {
  tasks: ScheduledTask[];
  loading: boolean;
  error: string | null;

  /** 当前视图模式 */
  viewMode: ViewMode;
  /** 视图模式为 dept 时的部门 ID */
  viewDeptId: string | null;

  /** 当前展开/编辑的任务 ID */
  expandedTaskId: string | null;

  /** 执行历史缓存 (task_id → runs) */
  runs: Record<string, TaskRun[]>;

  // ── Actions ──
  fetchTasks: () => Promise<void>;
  setViewMode: (mode: ViewMode, deptId?: string) => void;
  setExpandedTaskId: (id: string | null) => void;

  createTask: (dto: CreateTaskDto) => Promise<ScheduledTask | null>;
  updateTask: (id: string, dto: UpdateTaskDto) => Promise<boolean>;
  deleteTask: (id: string) => Promise<boolean>;

  pauseTask: (id: string) => Promise<boolean>;
  resumeTask: (id: string) => Promise<boolean>;
  runTaskNow: (id: string) => Promise<boolean>;

  fetchRuns: (taskId: string) => Promise<void>;

  // ── 乐观更新 ──
  optimisticAdd: (task: ScheduledTask) => void;
  optimisticRemove: (id: string) => void;
  optimisticUpdate: (id: string, partial: Partial<ScheduledTask>) => void;

  /** 清除（退出登录时） */
  clear: () => void;
}

export const useScheduledTaskStore = create<ScheduledTaskState>((set, get) => ({
  tasks: [],
  loading: false,
  error: null,
  viewMode: 'default',
  viewDeptId: null,
  expandedTaskId: null,
  runs: {},

  fetchTasks: async () => {
    set({ loading: true, error: null });
    try {
      const { viewMode, viewDeptId } = get();
      const tasks = await scheduledTaskService.list(viewMode, viewDeptId || undefined);
      set({ tasks, loading: false });
    } catch (error) {
      logger.error('scheduled-task', 'fetchTasks failed', error);
      set({ error: '加载定时任务失败', loading: false });
    }
  },

  setViewMode: (mode, deptId) => {
    set({ viewMode: mode, viewDeptId: deptId || null });
    get().fetchTasks();
  },

  setExpandedTaskId: (id) => set({ expandedTaskId: id }),

  createTask: async (dto) => {
    try {
      const task = await scheduledTaskService.create(dto);
      get().optimisticAdd(task);
      return task;
    } catch (error) {
      logger.error('scheduled-task', 'createTask failed', error);
      return null;
    }
  },

  updateTask: async (id, dto) => {
    try {
      await scheduledTaskService.update(id, dto);
      // 重新拉一次该任务（同步 next_run_at 等字段）
      const task = await scheduledTaskService.get(id);
      get().optimisticUpdate(id, task);
      return true;
    } catch (error) {
      logger.error('scheduled-task', 'updateTask failed', error);
      return false;
    }
  },

  deleteTask: async (id) => {
    try {
      await scheduledTaskService.delete(id);
      get().optimisticRemove(id);
      return true;
    } catch (error) {
      logger.error('scheduled-task', 'deleteTask failed', error);
      return false;
    }
  },

  pauseTask: async (id) => {
    get().optimisticUpdate(id, { status: 'paused' });
    try {
      await scheduledTaskService.pause(id);
      return true;
    } catch (error) {
      logger.error('scheduled-task', 'pauseTask failed', error);
      // 回滚
      get().fetchTasks();
      return false;
    }
  },

  resumeTask: async (id) => {
    get().optimisticUpdate(id, { status: 'active' });
    try {
      await scheduledTaskService.resume(id);
      // 重拉以同步 next_run_at
      const task = await scheduledTaskService.get(id);
      get().optimisticUpdate(id, task);
      return true;
    } catch (error) {
      logger.error('scheduled-task', 'resumeTask failed', error);
      get().fetchTasks();
      return false;
    }
  },

  runTaskNow: async (id) => {
    try {
      await scheduledTaskService.runNow(id);
      // 标记为执行中
      get().optimisticUpdate(id, { status: 'running' });
      return true;
    } catch (error) {
      logger.error('scheduled-task', 'runTaskNow failed', error);
      return false;
    }
  },

  fetchRuns: async (taskId) => {
    try {
      const runs = await scheduledTaskService.listRuns(taskId);
      set((state) => ({
        runs: { ...state.runs, [taskId]: runs },
      }));
    } catch (error) {
      logger.error('scheduled-task', 'fetchRuns failed', error);
    }
  },

  optimisticAdd: (task) => {
    set((state) => ({ tasks: [task, ...state.tasks] }));
  },

  optimisticRemove: (id) => {
    set((state) => ({
      tasks: state.tasks.filter((t) => t.id !== id),
      expandedTaskId: state.expandedTaskId === id ? null : state.expandedTaskId,
    }));
  },

  optimisticUpdate: (id, partial) => {
    set((state) => ({
      tasks: state.tasks.map((t) => (t.id === id ? { ...t, ...partial } : t)),
    }));
  },

  clear: () => {
    set({
      tasks: [],
      loading: false,
      error: null,
      viewMode: 'default',
      viewDeptId: null,
      expandedTaskId: null,
      runs: {},
    });
  },
}));
