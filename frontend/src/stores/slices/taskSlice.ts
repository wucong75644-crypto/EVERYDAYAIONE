/**
 * 任务管理 Slice
 *
 * 管理统一任务、聊天任务、媒体任务
 */

import type { StateCreator } from 'zustand';
import type { Message, TaskState, ChatTask, CompletedNotification } from '../../types/message';

// ============================================================
// 类型定义
// ============================================================

// Store 依赖类型（用于跨 slice 访问）
export interface TaskSliceDeps {
  messages: Record<string, Message[]>;
  currentConversationId: string | null;
}

export interface TaskSlice {
  /** 进行中的任务: taskId -> TaskState */
  tasks: Map<string, TaskState>;

  /** 聊天任务: conversationId -> ChatTask */
  chatTasks: Map<string, ChatTask>;

  /** 最近完成的对话（用于 UI 闪烁效果） */
  recentlyCompleted: Set<string>;

  /** 待处理通知 */
  pendingNotifications: CompletedNotification[];

  // 统一任务操作
  createTask: (task: TaskState) => void;
  updateTaskProgress: (taskId: string, progress: number) => void;
  completeTask: (taskId: string) => void;
  failTask: (taskId: string, error: string) => void;
  hasActiveTask: (conversationId: string) => boolean;
  canStartTask: () => { allowed: boolean; reason?: string };

  // 聊天任务操作
  startChatTask: (conversationId: string, conversationTitle: string) => void;
  updateChatTaskContent: (conversationId: string, content: string) => void;
  completeChatTask: (conversationId: string) => void;
  failChatTask: (conversationId: string) => void;
  removeChatTask: (conversationId: string) => void;
  getChatTask: (conversationId: string) => ChatTask | undefined;
  getActiveConversationIds: () => string[];

  // 通知操作
  markNotificationRead: (id: string) => void;
  /** 直接标记对话已完成（无依赖，供 WebSocket 调用） */
  markConversationCompleted: (conversationId: string) => void;
  clearRecentlyCompleted: (conversationId: string) => void;
  isRecentlyCompleted: (conversationId: string) => boolean;
}

// ============================================================
// 配置
// ============================================================

const GLOBAL_TASK_LIMIT = 15;

// ============================================================
// Slice 创建器
// ============================================================

export const createTaskSlice: StateCreator<TaskSlice & TaskSliceDeps, [], [], TaskSlice> = (set, get) => ({
  // 初始状态
  tasks: new Map<string, TaskState>(),
  chatTasks: new Map<string, ChatTask>(),
  recentlyCompleted: new Set<string>(),
  pendingNotifications: [] as CompletedNotification[],

  // ========================================
  // 统一任务操作
  // ========================================

  createTask: (task) => {
    set((state) => {
      const tasks = new Map(state.tasks);
      tasks.set(task.taskId, task);
      return { tasks };
    });
  },

  updateTaskProgress: (taskId, progress) => {
    set((state) => {
      const tasks = new Map(state.tasks);
      const task = tasks.get(taskId);
      if (task) {
        tasks.set(taskId, { ...task, progress, status: 'processing' });
      }
      return { tasks };
    });
  },

  completeTask: (taskId) => {
    set((state) => {
      const tasks = new Map(state.tasks);
      tasks.delete(taskId);
      return { tasks };
    });
  },

  failTask: (taskId, error) => {
    set((state) => {
      const tasks = new Map(state.tasks);
      const task = tasks.get(taskId);
      if (task) {
        tasks.set(taskId, { ...task, status: 'failed', error });
      }
      return { tasks };
    });
  },

  hasActiveTask: (conversationId) => {
    const state = get();

    // 检查统一任务
    for (const task of state.tasks.values()) {
      if (task.conversationId === conversationId) return true;
    }

    // 检查聊天任务
    if (state.chatTasks.has(conversationId)) return true;

    // 检查媒体任务：从 messages 中查找 pending 状态的媒体消息
    const messages = state.messages[conversationId];
    if (messages) {
      const hasPendingMedia = messages.some(
        (m: Message) => m.role === 'assistant' &&
             m.status === 'pending' &&
             m.generation_params?.type &&
             ['image', 'video'].includes(m.generation_params.type)
      );
      if (hasPendingMedia) return true;
    }

    return false;
  },

  canStartTask: () => {
    const state = get();

    // 计算进行中的媒体任务数（从所有 messages 中统计）
    let mediaTaskCount = 0;
    for (const messages of Object.values(state.messages)) {
      mediaTaskCount += messages.filter(
        (m: Message) => m.role === 'assistant' &&
             m.status === 'pending' &&
             m.generation_params?.type &&
             ['image', 'video'].includes(m.generation_params.type)
      ).length;
    }

    const totalCount = state.chatTasks.size + mediaTaskCount + state.tasks.size;

    if (totalCount >= GLOBAL_TASK_LIMIT) {
      return {
        allowed: false,
        reason: `任务队列已满，最多同时执行 ${GLOBAL_TASK_LIMIT} 个任务`,
      };
    }

    return { allowed: true };
  },

  // ========================================
  // 聊天任务操作
  // ========================================

  startChatTask: (conversationId, conversationTitle) => {
    set((state) => {
      const chatTasks = new Map(state.chatTasks);
      chatTasks.set(conversationId, {
        conversationId,
        conversationTitle,
        status: 'pending',
        startTime: Date.now(),
        content: '',
      });
      return { chatTasks };
    });
  },

  updateChatTaskContent: (conversationId, content) => {
    set((state) => {
      const task = state.chatTasks.get(conversationId);
      if (!task) return state;

      const chatTasks = new Map(state.chatTasks);
      chatTasks.set(conversationId, {
        ...task,
        status: 'streaming',
        content: (task.content || '') + content,
      });
      return { chatTasks };
    });
  },

  completeChatTask: (conversationId) => {
    set((state) => {
      const task = state.chatTasks.get(conversationId);
      if (!task) return state;

      const chatTasks = new Map(state.chatTasks);
      chatTasks.delete(conversationId);

      const recentlyCompleted = new Set(state.recentlyCompleted);
      recentlyCompleted.add(conversationId);

      const notification: CompletedNotification = {
        id: conversationId,
        conversationId,
        conversationTitle: task.conversationTitle,
        type: 'chat',
        isRead: false,
        timestamp: Date.now(),
      };

      return {
        chatTasks,
        recentlyCompleted,
        pendingNotifications: [...state.pendingNotifications, notification],
      };
    });
  },

  failChatTask: (conversationId) => {
    set((state) => {
      const task = state.chatTasks.get(conversationId);
      if (!task) return state;

      const chatTasks = new Map(state.chatTasks);
      chatTasks.set(conversationId, { ...task, status: 'error' });
      return { chatTasks };
    });

    setTimeout(() => get().removeChatTask(conversationId), 3000);
  },

  removeChatTask: (conversationId) => {
    set((state) => {
      const chatTasks = new Map(state.chatTasks);
      chatTasks.delete(conversationId);
      return { chatTasks };
    });
  },

  getChatTask: (conversationId) => get().chatTasks.get(conversationId),

  getActiveConversationIds: () => {
    const state = get();
    const ids = new Set<string>();

    // 聊天任务
    for (const conversationId of state.chatTasks.keys()) {
      ids.add(conversationId);
    }

    // 媒体任务：从 messages 中查找 pending 状态的媒体消息
    for (const [conversationId, messages] of Object.entries(state.messages)) {
      const hasPendingMedia = messages.some(
        (m: Message) => m.role === 'assistant' &&
             m.status === 'pending' &&
             m.generation_params?.type &&
             ['image', 'video'].includes(m.generation_params.type)
      );
      if (hasPendingMedia) {
        ids.add(conversationId);
      }
    }

    // 统一任务
    for (const task of state.tasks.values()) {
      ids.add(task.conversationId);
    }

    return Array.from(ids);
  },

  // ========================================
  // 通知操作
  // ========================================

  markNotificationRead: (id) => {
    set((state) => ({
      pendingNotifications: state.pendingNotifications.map((n) =>
        n.id === id ? { ...n, isRead: true } : n
      ),
    }));
  },

  markConversationCompleted: (conversationId) => {
    // 用户正在查看该对话时无需提醒，跳过
    if (get().currentConversationId === conversationId) return;

    set((state) => {
      const recentlyCompleted = new Set(state.recentlyCompleted);
      recentlyCompleted.add(conversationId);
      return { recentlyCompleted };
    });
  },

  clearRecentlyCompleted: (conversationId) => {
    set((state) => {
      const recentlyCompleted = new Set(state.recentlyCompleted);
      recentlyCompleted.delete(conversationId);
      return { recentlyCompleted };
    });
  },

  isRecentlyCompleted: (conversationId) => {
    return get().recentlyCompleted.has(conversationId);
  },
});
