/**
 * 任务状态管理
 *
 * 管理并发任务状态，支持：
 * - 聊天任务追踪（以 conversationId 为 key，单对话单任务）
 * - 图片/视频任务追踪（以 taskId 为 key，支持并发）
 * - 后台轮询生命周期管理（仅图片/视频任务）
 * - 任务完成通知队列
 * - 侧边栏任务徽章状态
 */

import { create } from 'zustand';

/** 任务限制常量 */
const GLOBAL_TASK_LIMIT = 15;
const MAX_NOTIFICATIONS = 50;

/** 任务状态 */
export type TaskStatus = 'pending' | 'streaming' | 'polling' | 'completed' | 'error';

/** 任务类型 */
export type TaskType = 'chat' | 'image' | 'video';

/** 聊天任务信息（以 conversationId 为 key） */
export interface ChatTask {
  conversationId: string;
  conversationTitle: string;
  status: TaskStatus;
  startTime: number;
  content?: string; // 流式内容累积
}

/** 媒体任务信息（以 taskId 为 key） */
export interface MediaTask {
  taskId: string;
  conversationId: string;
  conversationTitle: string;
  type: 'image' | 'video';
  status: TaskStatus;
  startTime: number;
  placeholderId: string; // 占位符消息 ID，用于任务完成后替换
}

/** 完成通知 */
export interface CompletedNotification {
  id: string; // conversationId 或 taskId
  conversationId: string;
  conversationTitle: string;
  type: TaskType;
  completedAt: number;
  isRead: boolean;
}

/** 轮询回调 */
export interface PollingCallbacks {
  onSuccess: (result: unknown) => void;
  onError: (error: Error) => void;
  onProgress?: (progress: number) => void;
}

/** 轮询配置 */
interface PollingConfig {
  intervalId: ReturnType<typeof setInterval>;
  pollFn: () => Promise<{ done: boolean; result?: unknown; error?: Error }>;
  callbacks: PollingCallbacks;
}

interface TaskState {
  // 聊天任务 Map<conversationId, ChatTask>
  chatTasks: Map<string, ChatTask>;
  // 媒体任务 Map<taskId, MediaTask>
  mediaTasks: Map<string, MediaTask>;
  // 轮询管理 Map<taskId, PollingConfig>
  pollingConfigs: Map<string, PollingConfig>;
  // 完成通知队列（未读）
  pendingNotifications: CompletedNotification[];
  // 刚完成的任务对话（用于闪烁动画）
  recentlyCompleted: Set<string>;

  // === 聊天任务操作（保持旧 API 兼容） ===
  startTask: (conversationId: string, conversationTitle: string) => void;
  updateTaskContent: (conversationId: string, content: string) => void;
  completeTask: (conversationId: string) => void;
  failTask: (conversationId: string) => void;
  removeTask: (conversationId: string) => void;

  // === 媒体任务操作（新 API） ===
  startMediaTask: (params: {
    taskId: string;
    conversationId: string;
    conversationTitle: string;
    type: 'image' | 'video';
    placeholderId: string;
  }) => void;
  completeMediaTask: (taskId: string) => void;
  failMediaTask: (taskId: string) => void;
  removeMediaTask: (taskId: string) => void;
  getMediaTask: (taskId: string) => MediaTask | undefined;
  getMediaTasksByConversation: (conversationId: string) => MediaTask[];

  // === 轮询操作 ===
  startPolling: (
    taskId: string,
    pollFn: () => Promise<{ done: boolean; result?: unknown; error?: Error }>,
    callbacks: PollingCallbacks,
    interval?: number
  ) => void;
  stopPolling: (taskId: string) => void;

  // === 通知操作 ===
  markNotificationRead: (id: string) => void;
  clearRecentlyCompleted: (conversationId: string) => void;
  clearAllNotifications: () => void;

  // === 查询方法 ===
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

  // =============================================
  // 聊天任务操作（保持旧 API 兼容）
  // =============================================

  // 开始聊天任务
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

  // 更新聊天任务流式内容
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

  // 完成聊天任务
  completeTask: (conversationId: string) => {
    set((state) => {
      const task = state.chatTasks.get(conversationId);
      if (!task) return state;

      const newTasks = new Map(state.chatTasks);
      newTasks.delete(conversationId);

      // 添加到完成通知队列
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

      // 限制通知队列长度
      if (newNotifications.length > MAX_NOTIFICATIONS) {
        newNotifications = newNotifications.slice(-MAX_NOTIFICATIONS);
      }

      // 添加到刚完成集合
      const newRecentlyCompleted = new Set(state.recentlyCompleted);
      newRecentlyCompleted.add(conversationId);

      return {
        chatTasks: newTasks,
        pendingNotifications: newNotifications,
        recentlyCompleted: newRecentlyCompleted,
      };
    });
  },

  // 聊天任务失败
  failTask: (conversationId: string) => {
    set((state) => {
      const task = state.chatTasks.get(conversationId);
      if (!task) return state;

      const newTasks = new Map(state.chatTasks);
      newTasks.set(conversationId, { ...task, status: 'error' });
      return { chatTasks: newTasks };
    });

    // 3秒后自动移除
    setTimeout(() => {
      get().removeTask(conversationId);
    }, 3000);
  },

  // 移除聊天任务
  removeTask: (conversationId: string) => {
    set((state) => {
      const newTasks = new Map(state.chatTasks);
      newTasks.delete(conversationId);
      return { chatTasks: newTasks };
    });
  },

  // =============================================
  // 媒体任务操作（新 API）
  // =============================================

  // 开始媒体任务
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

  // 完成媒体任务
  completeMediaTask: (taskId: string) => {
    const state = get();
    const task = state.mediaTasks.get(taskId);
    if (!task) return;

    // 停止轮询
    get().stopPolling(taskId);

    set((state) => {
      const newTasks = new Map(state.mediaTasks);
      newTasks.delete(taskId);

      // 添加到完成通知队列
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

      // 限制通知队列长度
      if (newNotifications.length > MAX_NOTIFICATIONS) {
        newNotifications = newNotifications.slice(-MAX_NOTIFICATIONS);
      }

      // 添加到刚完成集合
      const newRecentlyCompleted = new Set(state.recentlyCompleted);
      newRecentlyCompleted.add(task.conversationId);

      return {
        mediaTasks: newTasks,
        pendingNotifications: newNotifications,
        recentlyCompleted: newRecentlyCompleted,
      };
    });
  },

  // 媒体任务失败
  failMediaTask: (taskId: string) => {
    // 停止轮询
    get().stopPolling(taskId);

    set((state) => {
      const task = state.mediaTasks.get(taskId);
      if (!task) return state;

      const newTasks = new Map(state.mediaTasks);
      newTasks.set(taskId, { ...task, status: 'error' });
      return { mediaTasks: newTasks };
    });

    // 3秒后自动移除
    setTimeout(() => {
      get().removeMediaTask(taskId);
    }, 3000);
  },

  // 移除媒体任务
  removeMediaTask: (taskId: string) => {
    get().stopPolling(taskId);

    set((state) => {
      const newTasks = new Map(state.mediaTasks);
      newTasks.delete(taskId);
      return { mediaTasks: newTasks };
    });
  },

  // 获取媒体任务
  getMediaTask: (taskId: string) => {
    return get().mediaTasks.get(taskId);
  },

  // 获取某对话的所有媒体任务
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

  // =============================================
  // 轮询操作
  // =============================================

  // 开始轮询
  startPolling: (taskId, pollFn, callbacks, interval = 2000) => {
    // 更新状态为轮询中
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
      try {
        const result = await pollFn();
        if (result.done) {
          // 原子性检查：防止竞态时多个 executePoll 重复触发回调
          if (!get().pollingConfigs.has(taskId)) return;
          get().stopPolling(taskId);
          result.error ? callbacks.onError(result.error) : callbacks.onSuccess(result.result);
        }
      } catch (error) {
        // 原子性检查：防止竞态时重复触发 onError
        if (!get().pollingConfigs.has(taskId)) return;
        get().stopPolling(taskId);
        callbacks.onError(error instanceof Error ? error : new Error(String(error)));
      }
    };

    const intervalId = setInterval(executePoll, interval);

    // 先注册 pollingConfig，再执行立即轮询（避免竞态：轮询完成时 config 还未注册）
    set((state) => {
      const newConfigs = new Map(state.pollingConfigs);
      newConfigs.set(taskId, { intervalId, pollFn, callbacks });
      return { pollingConfigs: newConfigs };
    });

    // 立即执行一次（此时 config 已注册，stopPolling 可正常工作）
    executePoll();
  },

  // 停止轮询
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

  // =============================================
  // 通知操作
  // =============================================

  // 标记通知已读
  markNotificationRead: (id: string) => {
    set((state) => ({
      pendingNotifications: state.pendingNotifications.map((n) =>
        n.id === id ? { ...n, isRead: true } : n
      ),
    }));
  },

  // 清除完成闪烁状态
  clearRecentlyCompleted: (conversationId: string) => {
    set((state) => {
      const newRecentlyCompleted = new Set(state.recentlyCompleted);
      newRecentlyCompleted.delete(conversationId);
      return { recentlyCompleted: newRecentlyCompleted };
    });
  },

  // 清除所有通知
  clearAllNotifications: () => {
    set({ pendingNotifications: [] });
  },

  // =============================================
  // 查询方法
  // =============================================

  // 某对话是否有活跃任务（聊天或媒体）
  hasActiveTask: (conversationId: string) => {
    const state = get();

    // 检查聊天任务
    if (state.chatTasks.has(conversationId)) {
      return true;
    }

    // 检查媒体任务
    for (const task of state.mediaTasks.values()) {
      if (task.conversationId === conversationId) {
        return true;
      }
    }

    return false;
  },

  // 获取聊天任务
  getTask: (conversationId: string) => {
    return get().chatTasks.get(conversationId);
  },

  // 获取所有活跃任务的对话ID（去重）
  getActiveConversationIds: () => {
    const state = get();
    const ids = new Set<string>();

    // 聊天任务
    for (const conversationId of state.chatTasks.keys()) {
      ids.add(conversationId);
    }

    // 媒体任务
    for (const task of state.mediaTasks.values()) {
      ids.add(task.conversationId);
    }

    return Array.from(ids);
  },

  // 获取未读通知数量
  getUnreadNotificationCount: () => {
    return get().pendingNotifications.filter((n) => !n.isRead).length;
  },

  // 是否刚完成（用于绿色闪烁动画）
  isRecentlyCompleted: (conversationId: string) => {
    return get().recentlyCompleted.has(conversationId);
  },

  // 是否可以开始新任务（全局任务限制检查）
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
