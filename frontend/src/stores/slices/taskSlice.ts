/**
 * 任务管理 Slice
 *
 * 路径协议:任务限流由后端 task_limit_service(Redis SET)做单一事实来源,
 * 前端不再维护任务计数和预检。前端只追踪媒体任务(state.tasks)用于 UI 状态
 * 和导航跳转,聊天任务的"进行中"状态从 messages/streamingMessages 直接派生。
 *
 * 删除项(2026-06 Phase 1 后续修复):
 *   - chatTasks Map / startChatTask / failChatTask / completeChatTask /
 *     updateChatTaskContent / removeChatTask / getChatTask
 *     原因:零读消费者(死代码) + 异常路径不清理(假约束)。
 *   - canStartTask / hasActiveTask
 *     原因:零调用方,且限流是后端职责。后端超限返回 429,前端 api.ts 拦截弹 toast。
 */

import type { StateCreator } from 'zustand';
import type { Message, TaskState, CompletedNotification } from '../../types/message';

// ============================================================
// 类型定义
// ============================================================

// Store 依赖类型（用于跨 slice 访问）
export interface TaskSliceDeps {
  messages: Record<string, Message[]>;
  currentConversationId: string | null;
  streamingMessages: Map<string, string>;
}

export interface TaskSlice {
  /** 进行中的任务: taskId -> TaskState(媒体任务) */
  tasks: Map<string, TaskState>;

  /** 最近完成的对话（用于 UI 闪烁效果） */
  recentlyCompleted: Set<string>;

  /** 待处理通知 */
  pendingNotifications: CompletedNotification[];

  // 统一任务操作
  createTask: (task: TaskState) => void;
  updateTaskProgress: (taskId: string, progress: number) => void;
  completeTask: (taskId: string) => void;
  failTask: (taskId: string, error: string) => void;

  /** 计算所有有"进行中状态"的对话 ID(用于 LRU 清理保留 state) */
  getActiveConversationIds: () => string[];

  // 通知操作
  markNotificationRead: (id: string) => void;
  /** 直接标记对话已完成（无依赖，供 WebSocket 调用） */
  markConversationCompleted: (conversationId: string) => void;
  clearRecentlyCompleted: (conversationId: string) => void;
  isRecentlyCompleted: (conversationId: string) => boolean;
}

// ============================================================
// Slice 创建器
// ============================================================

export const createTaskSlice: StateCreator<TaskSlice & TaskSliceDeps, [], [], TaskSlice> = (set, get) => ({
  // 初始状态
  tasks: new Map<string, TaskState>(),
  recentlyCompleted: new Set<string>(),
  pendingNotifications: [] as CompletedNotification[],

  // ========================================
  // 统一任务操作（媒体任务）
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

  getActiveConversationIds: () => {
    const state = get();
    const ids = new Set<string>();

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

    // 流式聊天对话(streamingMessages 是事实来源)
    state.streamingMessages.forEach((_, conversationId) => {
      ids.add(conversationId);
    });

    // 统一任务（媒体任务追踪）
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
