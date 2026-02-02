/**
 * 任务状态管理 - 聊天/媒体任务追踪、轮询、通知队列
 */

import { create } from 'zustand';
import type { PollingCallbacks, PollingConfig } from '../utils/polling';
import { taskCoordinator } from '../utils/taskCoordinator';
import { notifyTaskComplete } from '../utils/taskNotification';
import type { StoreTaskStatus, StoreTaskType, CompletedNotification } from '../types/task';

const GLOBAL_TASK_LIMIT = 15;

// 重新导出类型供外部使用（保持向后兼容）
export type TaskStatus = StoreTaskStatus;
export type TaskType = StoreTaskType;
export type { CompletedNotification };

export interface ChatTask {
  conversationId: string;
  conversationTitle: string;
  status: StoreTaskStatus;
  startTime: number;
  content?: string;
}

export interface MediaTask {
  taskId: string;
  conversationId: string;
  conversationTitle: string;
  type: 'image' | 'video';
  status: StoreTaskStatus;
  startTime: number;
  placeholderId: string;
}

export type { PollingCallbacks };

interface TaskState {
  chatTasks: Map<string, ChatTask>;
  mediaTasks: Map<string, MediaTask>;
  pollingConfigs: Map<string, PollingConfig>;
  pendingNotifications: CompletedNotification[];
  recentlyCompleted: Set<string>;

  // 聊天任务操作
  startTask: (conversationId: string, conversationTitle: string) => void;
  updateTaskContent: (conversationId: string, content: string) => void;
  completeTask: (conversationId: string) => void;
  failTask: (conversationId: string) => void;
  removeTask: (conversationId: string) => void;

  // 媒体任务操作
  startMediaTask: (params: { taskId: string; conversationId: string; conversationTitle: string; type: 'image' | 'video'; placeholderId: string }) => void;
  completeMediaTask: (taskId: string) => void;
  failMediaTask: (taskId: string) => void;
  removeMediaTask: (taskId: string) => void;
  getMediaTask: (taskId: string) => MediaTask | undefined;
  getMediaTasksByConversation: (conversationId: string) => MediaTask[];

  // 轮询操作
  startPolling: (taskId: string, pollFn: () => Promise<{ done: boolean; result?: unknown; error?: Error }>, callbacks: PollingCallbacks, options?: { interval?: number; maxDuration?: number }) => void;
  stopPolling: (taskId: string) => void;

  // 通知操作
  markNotificationRead: (id: string) => void;
  clearRecentlyCompleted: (conversationId: string) => void;
  clearAllNotifications: () => void;

  // 查询方法
  hasActiveTask: (conversationId: string) => boolean;
  getTask: (conversationId: string) => ChatTask | undefined;
  getActiveConversationIds: () => string[];
  getUnreadNotificationCount: () => number;
  isRecentlyCompleted: (conversationId: string) => boolean;
  canStartTask: () => { allowed: boolean; reason?: string };
}

export const useTaskStore = create<TaskState>((set, get) => ({
  chatTasks: new Map(),
  mediaTasks: new Map(),
  pollingConfigs: new Map(),
  pendingNotifications: [],
  recentlyCompleted: new Set(),

  // 聊天任务操作
  startTask: (conversationId: string, conversationTitle: string) => {
    set((state) => {
      const newTasks = new Map(state.chatTasks);
      newTasks.set(conversationId, {
        conversationId,
        conversationTitle,
        status: 'pending',
        startTime: Date.now(),
        content: '',
      });
      return { chatTasks: newTasks };
    });
  },

  updateTaskContent: (conversationId: string, content: string) => {
    set((state) => {
      const task = state.chatTasks.get(conversationId);
      if (!task) return state;

      const newTasks = new Map(state.chatTasks);
      newTasks.set(conversationId, {
        ...task,
        status: 'streaming',
        content: (task.content || '') + content,
      });
      return { chatTasks: newTasks };
    });
  },

  completeTask: (conversationId: string) => {
    // 注意：markConversationUnread 已移至调用方（useMessageCallbacks.tsx）
    // 解耦 TaskStore 对 ChatStore 的依赖

    set((state) => {
      const task = state.chatTasks.get(conversationId);
      if (!task) return state;

      const newTasks = new Map(state.chatTasks);
      newTasks.delete(conversationId);

      const { pendingNotifications, recentlyCompleted } = notifyTaskComplete(
        {
          id: conversationId,
          conversationId,
          conversationTitle: task.conversationTitle,
          type: 'chat',
        },
        state.pendingNotifications,
        state.recentlyCompleted
      );

      return {
        chatTasks: newTasks,
        pendingNotifications,
        recentlyCompleted,
      };
    });
  },

  failTask: (conversationId: string) => {
    set((state) => {
      const task = state.chatTasks.get(conversationId);
      if (!task) return state;

      const newTasks = new Map(state.chatTasks);
      newTasks.set(conversationId, { ...task, status: 'error' });
      return { chatTasks: newTasks };
    });

    setTimeout(() => get().removeTask(conversationId), 3000);
  },

  removeTask: (conversationId: string) => {
    set((state) => {
      const newTasks = new Map(state.chatTasks);
      newTasks.delete(conversationId);
      return { chatTasks: newTasks };
    });
  },

  // 媒体任务操作
  startMediaTask: ({ taskId, conversationId, conversationTitle, type, placeholderId }) => {
    set((state) => {
      const newTasks = new Map(state.mediaTasks);
      newTasks.set(taskId, {
        taskId,
        conversationId,
        conversationTitle,
        type,
        status: 'pending',
        startTime: Date.now(),
        placeholderId,
      });
      return { mediaTasks: newTasks };
    });
  },

  completeMediaTask: (taskId: string) => {
    const task = get().mediaTasks.get(taskId);
    if (!task) return;

    get().stopPolling(taskId);
    // 注意：markConversationUnread 已移至调用方（mediaGenerationCore.ts、taskRestoration.ts）
    // 解耦 TaskStore 对 ChatStore 的依赖

    set((state) => {
      const newTasks = new Map(state.mediaTasks);
      newTasks.delete(taskId);

      const { pendingNotifications, recentlyCompleted } = notifyTaskComplete(
        {
          id: taskId,
          conversationId: task.conversationId,
          conversationTitle: task.conversationTitle,
          type: task.type,
        },
        state.pendingNotifications,
        state.recentlyCompleted
      );

      return {
        mediaTasks: newTasks,
        pendingNotifications,
        recentlyCompleted,
      };
    });
  },

  failMediaTask: (taskId: string) => {
    get().stopPolling(taskId);

    set((state) => {
      const task = state.mediaTasks.get(taskId);
      if (!task) return state;

      const newTasks = new Map(state.mediaTasks);
      newTasks.set(taskId, { ...task, status: 'error' });
      return { mediaTasks: newTasks };
    });

    setTimeout(() => get().removeMediaTask(taskId), 3000);
  },

  removeMediaTask: (taskId: string) => {
    get().stopPolling(taskId);

    set((state) => {
      const newTasks = new Map(state.mediaTasks);
      newTasks.delete(taskId);
      return { mediaTasks: newTasks };
    });
  },

  getMediaTask: (taskId: string) => get().mediaTasks.get(taskId),

  getMediaTasksByConversation: (conversationId: string) => {
    const state = get();
    const tasks: MediaTask[] = [];
    for (const task of state.mediaTasks.values()) {
      if (task.conversationId === conversationId) {
        tasks.push(task);
      }
    }
    return tasks;
  },

  // 轮询操作
  startPolling: (taskId, pollFn, callbacks, options = {}) => {
    // 检查是否可以开始轮询（防止多标签页重复轮询）
    if (!taskCoordinator.canStartPolling(taskId)) {
      return;
    }

    const { interval = 2000, maxDuration } = options;
    const startTime = Date.now();
    let consecutiveFailures = 0;
    const MAX_CONSECUTIVE_FAILURES = 5; // 连续失败5次后停止轮询（考虑首次OSS上传可能超时）

    // 每15秒更新锁
    const lockRenewalId = setInterval(() => {
      taskCoordinator.renewLock(taskId);
    }, 15000);

    set((state) => {
      const task = state.mediaTasks.get(taskId);
      if (!task) return state;
      const newTasks = new Map(state.mediaTasks);
      newTasks.set(taskId, { ...task, status: 'polling' });
      return { mediaTasks: newTasks };
    });

    // 轮询执行函数
    // 注意：立即执行 + 定时器可能导致多个 executePoll 并发
    // 使用 pollingConfigs.has() 作为原子锁：只有第一个完成的能触发回调
    const executePoll = async () => {
      // 检查是否超过最大轮询时长
      if (maxDuration) {
        const elapsed = Date.now() - startTime;
        if (elapsed > maxDuration) {
          if (!get().pollingConfigs.has(taskId)) return;
          get().stopPolling(taskId);
          const minutes = Math.round(maxDuration / 60000);
          callbacks.onError(new Error(`任务轮询超时，已等待 ${minutes} 分钟`));
          return;
        }
      }

      try {
        const result = await pollFn();
        consecutiveFailures = 0; // 重置失败计数
        if (result.done) {
          if (!get().pollingConfigs.has(taskId)) return;
          get().stopPolling(taskId);
          if (result.error) {
            callbacks.onError(result.error);
          } else {
            callbacks.onSuccess(result.result);
          }
        }
      } catch (error) {
        consecutiveFailures++;
        console.warn(`轮询任务 ${taskId} 请求失败 (${consecutiveFailures}/${MAX_CONSECUTIVE_FAILURES})，将在下次间隔后重试:`, error);

        // 连续失败超过限制，停止轮询
        if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
          if (!get().pollingConfigs.has(taskId)) return;
          get().stopPolling(taskId);
          callbacks.onError(new Error('任务查询连续失败，可能任务已过期'));
        }
      }
    };

    const intervalId = setInterval(executePoll, interval);

    set((state) => {
      const newConfigs = new Map(state.pollingConfigs);
      newConfigs.set(taskId, { intervalId, pollFn, callbacks, lockRenewalId });
      return { pollingConfigs: newConfigs };
    });

    executePoll();
  },

  stopPolling: (taskId: string) => {
    const state = get();
    const config = state.pollingConfigs.get(taskId);
    if (config) {
      clearInterval(config.intervalId);
      if (config.lockRenewalId) {
        clearInterval(config.lockRenewalId);
      }
      taskCoordinator.releasePolling(taskId);
      set((state) => {
        const newConfigs = new Map(state.pollingConfigs);
        newConfigs.delete(taskId);
        return { pollingConfigs: newConfigs };
      });
    }
  },

  // 通知操作
  markNotificationRead: (id: string) => {
    set((state) => ({
      pendingNotifications: state.pendingNotifications.map((n) =>
        n.id === id ? { ...n, isRead: true } : n
      ),
    }));
  },

  clearRecentlyCompleted: (conversationId: string) => {
    set((state) => {
      const newRecentlyCompleted = new Set(state.recentlyCompleted);
      newRecentlyCompleted.delete(conversationId);
      return { recentlyCompleted: newRecentlyCompleted };
    });
  },

  clearAllNotifications: () => set({ pendingNotifications: [] }),

  // 查询方法
  hasActiveTask: (conversationId: string) => {
    const state = get();

    if (state.chatTasks.has(conversationId)) return true;

    for (const task of state.mediaTasks.values()) {
      if (task.conversationId === conversationId) {
        return true;
      }
    }

    return false;
  },

  getTask: (conversationId: string) => get().chatTasks.get(conversationId),

  getActiveConversationIds: () => {
    const state = get();
    const ids = new Set<string>();
    for (const conversationId of state.chatTasks.keys()) ids.add(conversationId);
    for (const task of state.mediaTasks.values()) ids.add(task.conversationId);
    return Array.from(ids);
  },

  getUnreadNotificationCount: () => get().pendingNotifications.filter((n) => !n.isRead).length,

  isRecentlyCompleted: (conversationId: string) => get().recentlyCompleted.has(conversationId),

  canStartTask: () => {
    const state = get();
    const totalCount = state.chatTasks.size + state.mediaTasks.size;

    if (totalCount >= GLOBAL_TASK_LIMIT) {
      return {
        allowed: false,
        reason: `任务队列已满，最多同时执行 ${GLOBAL_TASK_LIMIT} 个任务`,
      };
    }

    return { allowed: true };
  },
}));
