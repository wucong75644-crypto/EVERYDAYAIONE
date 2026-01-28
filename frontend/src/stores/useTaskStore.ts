/**
 * 任务状态管理 - 聊天/媒体任务追踪、轮询、通知队列
 */

import { create } from 'zustand';
import { useChatStore } from './useChatStore';
import type { PollingCallbacks, PollingConfig } from '../utils/polling';

const GLOBAL_TASK_LIMIT = 15;
const MAX_NOTIFICATIONS = 50;

export type TaskStatus = 'pending' | 'streaming' | 'polling' | 'completed' | 'error';
export type TaskType = 'chat' | 'image' | 'video';

export interface ChatTask {
  conversationId: string;
  conversationTitle: string;
  status: TaskStatus;
  startTime: number;
  content?: string;
}

export interface MediaTask {
  taskId: string;
  conversationId: string;
  conversationTitle: string;
  type: 'image' | 'video';
  status: TaskStatus;
  startTime: number;
  placeholderId: string;
}

export interface CompletedNotification {
  id: string;
  conversationId: string;
  conversationTitle: string;
  type: TaskType;
  completedAt: number;
  isRead: boolean;
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
    useChatStore.getState().markConversationUnread(conversationId);

    set((state) => {
      const task = state.chatTasks.get(conversationId);
      if (!task) return state;

      const newTasks = new Map(state.chatTasks);
      newTasks.delete(conversationId);

      let newNotifications = [
        ...state.pendingNotifications,
        {
          id: conversationId,
          conversationId,
          conversationTitle: task.conversationTitle,
          type: 'chat' as TaskType,
          completedAt: Date.now(),
          isRead: false,
        },
      ];

      if (newNotifications.length > MAX_NOTIFICATIONS) {
        newNotifications = newNotifications.slice(-MAX_NOTIFICATIONS);
      }

      const newRecentlyCompleted = new Set(state.recentlyCompleted);
      newRecentlyCompleted.add(conversationId);

      return {
        chatTasks: newTasks,
        pendingNotifications: newNotifications,
        recentlyCompleted: newRecentlyCompleted,
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
    useChatStore.getState().markConversationUnread(task.conversationId);

    set((state) => {
      const newTasks = new Map(state.mediaTasks);
      newTasks.delete(taskId);

      let newNotifications = [
        ...state.pendingNotifications,
        {
          id: taskId,
          conversationId: task.conversationId,
          conversationTitle: task.conversationTitle,
          type: task.type,
          completedAt: Date.now(),
          isRead: false,
        },
      ];

      if (newNotifications.length > MAX_NOTIFICATIONS) {
        newNotifications = newNotifications.slice(-MAX_NOTIFICATIONS);
      }

      const newRecentlyCompleted = new Set(state.recentlyCompleted);
      newRecentlyCompleted.add(task.conversationId);

      return {
        mediaTasks: newTasks,
        pendingNotifications: newNotifications,
        recentlyCompleted: newRecentlyCompleted,
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
    const { interval = 2000, maxDuration } = options;
    const startTime = Date.now();

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
        if (result.done) {
          if (!get().pollingConfigs.has(taskId)) return;
          get().stopPolling(taskId);
          result.error ? callbacks.onError(result.error) : callbacks.onSuccess(result.result);
        }
      } catch (error) {
        console.warn(`轮询任务 ${taskId} 请求失败，将在下次间隔后重试:`, error);
      }
    };

    const intervalId = setInterval(executePoll, interval);

    set((state) => {
      const newConfigs = new Map(state.pollingConfigs);
      newConfigs.set(taskId, { intervalId, pollFn, callbacks });
      return { pollingConfigs: newConfigs };
    });

    executePoll();
  },

  stopPolling: (taskId: string) => {
    const state = get();
    const config = state.pollingConfigs.get(taskId);
    if (config) {
      clearInterval(config.intervalId);
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
