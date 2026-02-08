/**
 * 统一消息状态管理
 *
 * 合并了原有的 useChatStore、useTaskStore、useConversationRuntimeStore，
 * 提供统一的消息和任务状态管理接口。
 *
 * 设计原则：
 * - 一个 Store 管理所有消息状态
 * - 支持乐观更新和流式消息
 * - 任务状态与消息状态统一管理
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

// 类型从独立文件导入
import type {
  ContentPart,
  TextPart,
  Message,
  MessageStatus,
  GenerationParams,
  TaskState,
  Conversation,
  MessageCacheEntry,
  ChatTask,
  MediaTask,
  CompletedNotification,
} from '../types/message';

// 辅助函数从独立文件导入
import { getTextContent, normalizeMessage } from '../utils/messageUtils';

// 重新导出类型，保持向后兼容
export type {
  ContentPart,
  TextPart,
  ImagePart,
  VideoPart,
  AudioPart,
  FilePart,
  Message,
  MessageStatus,
  MessageError,
  GenerationParams,
  TaskState,
  Conversation,
  MessageCacheEntry,
  ChatTask,
  MediaTask,
  CompletedNotification,
} from '../types/message';

// 重新导出辅助函数，保持向后兼容
export { getTextContent, getImageUrls, getVideoUrls, normalizeMessage } from '../utils/messageUtils';

// ============================================================
// 缓存配置
// ============================================================

const CACHE_CONFIG = {
  MAX_CACHED_CONVERSATIONS: 10,
  CACHE_EXPIRY_MS: 5 * 60 * 1000, // 5分钟
};

const GLOBAL_TASK_LIMIT = 15;

// ============================================================
// Store 接口定义
// ============================================================

interface MessageStore {
  // ========================================
  // 状态
  // ========================================

  /** 消息缓存: conversationId -> messages */
  messages: Map<string, Message[]>;

  /** 缓存元数据: conversationId -> { hasMore, lastFetchedAt } */
  cacheMetadata: Map<string, { hasMore: boolean; lastFetchedAt: number }>;

  /** LRU 访问顺序 */
  cacheAccessOrder: string[];

  /** 进行中的任务: taskId -> TaskState */
  tasks: Map<string, TaskState>;

  /** 聊天任务: conversationId -> ChatTask */
  chatTasks: Map<string, ChatTask>;

  /** 媒体任务: taskId -> MediaTask */
  mediaTasks: Map<string, MediaTask>;

  /** 对话列表 */
  conversations: Conversation[];
  conversationsLoading: boolean;

  /** 当前对话 */
  currentConversationId: string | null;
  currentConversationTitle: string;

  /** 发送状态 */
  isSending: boolean;

  /** 流式消息状态: conversationId -> messageId */
  streamingMessages: Map<string, string>;

  /** 乐观消息: conversationId -> messages */
  optimisticMessages: Map<string, Message[]>;

  /** 未读对话 */
  unreadConversations: Set<string>;

  /** 需要强制刷新的对话 */
  forceRefreshConversations: Set<string>;

  /** 最近完成的对话（用于 UI 闪烁效果） */
  recentlyCompleted: Set<string>;

  /** 待处理通知 */
  pendingNotifications: CompletedNotification[];

  // ========================================
  // 消息操作
  // ========================================

  addMessage: (conversationId: string, message: Message) => void;
  updateMessage: (messageId: string, updates: Partial<Message>) => void;
  appendContent: (messageId: string, chunk: string) => void;
  setContent: (messageId: string, content: ContentPart[]) => void;
  setStatus: (messageId: string, status: MessageStatus) => void;
  removeMessage: (messageId: string) => void;
  setMessages: (conversationId: string, messages: Message[], hasMore?: boolean) => void;
  prependMessages: (conversationId: string, messages: Message[], hasMore: boolean) => void;
  appendMessage: (conversationId: string, message: Message) => void;
  replaceMessage: (conversationId: string, messageId: string, newMessage: Message) => void;

  // ========================================
  // 任务操作（新统一 API）
  // ========================================

  createTask: (task: TaskState) => void;
  updateTaskProgress: (taskId: string, progress: number) => void;
  completeTask: (taskId: string) => void;
  failTask: (taskId: string, error: string) => void;
  getTask: (taskId: string) => TaskState | undefined;
  hasActiveTask: (conversationId: string) => boolean;
  canStartTask: () => { allowed: boolean; reason?: string };

  // ========================================
  // 聊天任务操作（兼容旧 TaskStore）
  // ========================================

  startChatTask: (conversationId: string, conversationTitle: string) => void;
  updateChatTaskContent: (conversationId: string, content: string) => void;
  completeChatTask: (conversationId: string) => void;
  failChatTask: (conversationId: string) => void;
  removeChatTask: (conversationId: string) => void;
  getChatTask: (conversationId: string) => ChatTask | undefined;

  // ========================================
  // 媒体任务操作（兼容旧 TaskStore）
  // ========================================

  startMediaTask: (params: {
    taskId: string;
    conversationId: string;
    conversationTitle: string;
    type: 'image' | 'video';
    placeholderId: string;
  }) => void;
  completeMediaTask: (taskId: string) => void;
  failMediaTask: (taskId: string) => void;
  getMediaTask: (taskId: string) => MediaTask | undefined;
  getActiveConversationIds: () => string[];

  // ========================================
  // 流式消息操作
  // ========================================

  startStreaming: (conversationId: string, messageId: string, options?: {
    initialContent?: string;
    createdAt?: string;
    generationParams?: GenerationParams;
  }) => void;
  registerStreamingId: (conversationId: string, messageId: string) => void;
  appendStreamingContent: (conversationId: string, chunk: string) => void;
  setStreamingContent: (conversationId: string, content: string) => void;
  completeStreaming: (conversationId: string) => void;
  completeStreamingWithMessage: (conversationId: string, message: Message) => void;
  getStreamingMessageId: (conversationId: string) => string | null;

  // ========================================
  // 乐观消息操作（兼容旧 RuntimeStore）
  // ========================================

  addOptimisticMessage: (conversationId: string, message: Message) => void;
  addOptimisticUserMessage: (conversationId: string, message: Message) => void;
  updateOptimisticMessageId: (conversationId: string, clientRequestId: string, newId: string) => void;
  addErrorMessage: (conversationId: string, errorMessage: Message) => void;
  removeOptimisticMessage: (conversationId: string, messageId: string) => void;
  replaceMediaPlaceholder: (conversationId: string, placeholderId: string, realMessage: Message) => void;
  getOptimisticMessages: (conversationId: string) => Message[];

  // ========================================
  // 缓存操作
  // ========================================

  getCachedMessages: (conversationId: string) => MessageCacheEntry | null;
  touchCache: (conversationId: string) => void;
  isCacheExpired: (conversationId: string) => boolean;
  clearConversationCache: (conversationId: string) => void;
  setMessagesForConversation: (conversationId: string, messages: Message[], hasMore?: boolean) => void;

  // ========================================
  // 强制刷新标记
  // ========================================

  markForceRefresh: (conversationId: string) => void;
  clearForceRefresh: (conversationId: string) => void;
  needsForceRefresh: (conversationId: string) => boolean;

  // ========================================
  // 对话操作
  // ========================================

  setConversations: (conversations: Conversation[]) => void;
  setConversationsLoading: (loading: boolean) => void;
  setCurrentConversation: (id: string | null, title: string) => void;
  setIsSending: (sending: boolean) => void;
  deleteConversation: (id: string) => void;
  renameConversation: (id: string, title: string) => void;
  createConversation: (title: string) => string;

  // ========================================
  // 未读状态
  // ========================================

  markConversationUnread: (conversationId: string) => void;
  clearConversationUnread: (conversationId: string) => void;
  hasUnreadMessages: (conversationId: string) => boolean;

  // ========================================
  // 通知操作
  // ========================================

  markNotificationRead: (id: string) => void;
  clearRecentlyCompleted: (conversationId: string) => void;
  isRecentlyCompleted: (conversationId: string) => boolean;

  // ========================================
  // 辅助方法
  // ========================================

  getMessages: (conversationId: string) => Message[];
  getMessage: (messageId: string) => Message | undefined;
  clearConversation: (conversationId: string) => void;
  cleanup: (keepConversationIds: string[]) => void;
  reset: () => void;
}

// ============================================================
// 自定义序列化
// ============================================================

const customStorage = createJSONStorage<Partial<MessageStore>>(() => ({
  getItem: (name) => {
    const str = localStorage.getItem(name);
    if (!str) return null;

    try {
      const data = JSON.parse(str);
      // 恢复 Map 类型
      if (data.state?.messagesObj) {
        data.state.messages = new Map(Object.entries(data.state.messagesObj));
        delete data.state.messagesObj;
      }
      if (data.state?.cacheMetadataObj) {
        data.state.cacheMetadata = new Map(Object.entries(data.state.cacheMetadataObj));
        delete data.state.cacheMetadataObj;
      }
      return data;
    } catch {
      return null;
    }
  },
  setItem: (name, value) => {
    try {
      const data = JSON.parse(value);
      // 转换 Map 为 Object
      if (data.state?.messages instanceof Map) {
        data.state.messagesObj = Object.fromEntries(data.state.messages);
        delete data.state.messages;
      }
      if (data.state?.cacheMetadata instanceof Map) {
        data.state.cacheMetadataObj = Object.fromEntries(data.state.cacheMetadata);
        delete data.state.cacheMetadata;
      }
      localStorage.setItem(name, JSON.stringify(data));
    } catch {
      // 忽略序列化错误
    }
  },
  removeItem: (name) => localStorage.removeItem(name),
}));

// ============================================================
// Store 实现
// ============================================================

const initialState = {
  messages: new Map<string, Message[]>(),
  cacheMetadata: new Map<string, { hasMore: boolean; lastFetchedAt: number }>(),
  cacheAccessOrder: [] as string[],
  tasks: new Map<string, TaskState>(),
  chatTasks: new Map<string, ChatTask>(),
  mediaTasks: new Map<string, MediaTask>(),
  conversations: [] as Conversation[],
  conversationsLoading: false,
  currentConversationId: null as string | null,
  currentConversationTitle: '新对话',
  isSending: false,
  streamingMessages: new Map<string, string>(),
  optimisticMessages: new Map<string, Message[]>(),
  unreadConversations: new Set<string>(),
  forceRefreshConversations: new Set<string>(),
  recentlyCompleted: new Set<string>(),
  pendingNotifications: [] as CompletedNotification[],
};

export const useMessageStore = create<MessageStore>()(
  persist(
    (set, get) => ({
      ...initialState,

      // ========================================
      // 消息操作
      // ========================================

      addMessage: (conversationId, message) => {
        set((state) => {
          const messages = new Map(state.messages);
          const list = messages.get(conversationId) || [];

          if (list.some((m) => m.id === message.id)) {
            return state;
          }

          const normalizedMsg = normalizeMessage(message);
          messages.set(conversationId, [...list, normalizedMsg]);
          return { messages };
        });
      },

      updateMessage: (messageId, updates) => {
        set((state) => {
          const messages = new Map(state.messages);

          for (const [convId, list] of messages) {
            const index = list.findIndex((m) => m.id === messageId);
            if (index !== -1) {
              const updated = {
                ...list[index],
                ...updates,
                updated_at: new Date().toISOString(),
              };
              const newList = [...list];
              newList[index] = updated;
              messages.set(convId, newList);
              return { messages };
            }
          }

          // 也检查乐观消息
          const optimisticMessages = new Map(state.optimisticMessages);
          for (const [convId, list] of optimisticMessages) {
            const index = list.findIndex((m) => m.id === messageId);
            if (index !== -1) {
              const updated = {
                ...list[index],
                ...updates,
                updated_at: new Date().toISOString(),
              };
              const newList = [...list];
              newList[index] = updated;
              optimisticMessages.set(convId, newList);
              return { optimisticMessages };
            }
          }

          return state;
        });
      },

      appendContent: (messageId, chunk) => {
        const message = get().getMessage(messageId);
        if (!message) return;

        const content = [...(Array.isArray(message.content) ? message.content : [])];
        const textIndex = content.findIndex((p) => p.type === 'text');

        if (textIndex >= 0) {
          content[textIndex] = {
            type: 'text',
            text: (content[textIndex] as TextPart).text + chunk,
          };
        } else {
          content.push({ type: 'text', text: chunk });
        }

        get().updateMessage(messageId, { content });
      },

      setContent: (messageId, content) => {
        get().updateMessage(messageId, { content, status: 'completed' });
      },

      setStatus: (messageId, status) => {
        get().updateMessage(messageId, { status });
      },

      removeMessage: (messageId) => {
        set((state) => {
          // 先检查 messages
          const messages = new Map(state.messages);
          for (const [convId, list] of messages) {
            const filtered = list.filter((m) => m.id !== messageId);
            if (filtered.length !== list.length) {
              messages.set(convId, filtered);
              return { messages };
            }
          }

          // 再检查 optimisticMessages
          const optimisticMessages = new Map(state.optimisticMessages);
          for (const [convId, list] of optimisticMessages) {
            const filtered = list.filter((m) => m.id !== messageId);
            if (filtered.length !== list.length) {
              optimisticMessages.set(convId, filtered);
              return { optimisticMessages };
            }
          }

          return state;
        });
      },

      setMessages: (conversationId, msgs, hasMore = false) => {
        set((state) => {
          const messages = new Map(state.messages);
          const cacheMetadata = new Map(state.cacheMetadata);

          const normalizedMsgs = msgs.map(normalizeMessage);
          messages.set(conversationId, normalizedMsgs);
          cacheMetadata.set(conversationId, {
            hasMore,
            lastFetchedAt: Date.now(),
          });

          return { messages, cacheMetadata };
        });
      },

      prependMessages: (conversationId, msgs, hasMore) => {
        set((state) => {
          const messages = new Map(state.messages);
          const cacheMetadata = new Map(state.cacheMetadata);

          const existing = messages.get(conversationId) || [];
          const existingIds = new Set(existing.map((m) => m.id));

          const newMsgs = msgs
            .map(normalizeMessage)
            .filter((m) => !existingIds.has(m.id));

          messages.set(conversationId, [...newMsgs, ...existing]);

          const meta = cacheMetadata.get(conversationId) || { hasMore: false, lastFetchedAt: Date.now() };
          cacheMetadata.set(conversationId, { ...meta, hasMore });

          return { messages, cacheMetadata };
        });
      },

      appendMessage: (conversationId, message) => {
        get().addMessage(conversationId, message);
      },

      replaceMessage: (conversationId, messageId, newMessage) => {
        set((state) => {
          const messages = new Map(state.messages);
          const list = messages.get(conversationId);
          if (!list) return state;

          const index = list.findIndex((m) => m.id === messageId);
          if (index === -1) {
            messages.set(conversationId, [...list, normalizeMessage(newMessage)]);
          } else {
            const newList = [...list];
            newList[index] = normalizeMessage(newMessage);
            messages.set(conversationId, newList);
          }

          return { messages };
        });
      },

      // ========================================
      // 任务操作（新统一 API）
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

      getTask: (taskId) => get().tasks.get(taskId),

      hasActiveTask: (conversationId) => {
        const state = get();

        for (const task of state.tasks.values()) {
          if (task.conversationId === conversationId) return true;
        }

        if (state.chatTasks.has(conversationId)) return true;

        for (const task of state.mediaTasks.values()) {
          if (task.conversationId === conversationId) return true;
        }

        return false;
      },

      canStartTask: () => {
        const state = get();
        const totalCount = state.chatTasks.size + state.mediaTasks.size + state.tasks.size;

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

      // ========================================
      // 媒体任务操作
      // ========================================

      startMediaTask: ({ taskId, conversationId, conversationTitle, type, placeholderId }) => {
        set((state) => {
          const mediaTasks = new Map(state.mediaTasks);
          mediaTasks.set(taskId, {
            taskId,
            conversationId,
            conversationTitle,
            type,
            status: 'pending',
            startTime: Date.now(),
            placeholderId,
          });
          return { mediaTasks };
        });
      },

      completeMediaTask: (taskId) => {
        const task = get().mediaTasks.get(taskId);
        if (!task) return;

        set((state) => {
          const mediaTasks = new Map(state.mediaTasks);
          mediaTasks.delete(taskId);

          const recentlyCompleted = new Set(state.recentlyCompleted);
          recentlyCompleted.add(task.conversationId);

          const notification: CompletedNotification = {
            id: taskId,
            conversationId: task.conversationId,
            conversationTitle: task.conversationTitle,
            type: task.type,
            isRead: false,
            timestamp: Date.now(),
          };

          return {
            mediaTasks,
            recentlyCompleted,
            pendingNotifications: [...state.pendingNotifications, notification],
          };
        });
      },

      failMediaTask: (taskId) => {
        set((state) => {
          const task = state.mediaTasks.get(taskId);
          if (!task) return state;

          const mediaTasks = new Map(state.mediaTasks);
          mediaTasks.set(taskId, { ...task, status: 'error' });
          return { mediaTasks };
        });

        setTimeout(() => {
          set((state) => {
            const mediaTasks = new Map(state.mediaTasks);
            mediaTasks.delete(taskId);
            return { mediaTasks };
          });
        }, 3000);
      },

      getMediaTask: (taskId) => get().mediaTasks.get(taskId),

      getActiveConversationIds: () => {
        const state = get();
        const ids = new Set<string>();

        for (const conversationId of state.chatTasks.keys()) {
          ids.add(conversationId);
        }
        for (const task of state.mediaTasks.values()) {
          ids.add(task.conversationId);
        }
        for (const task of state.tasks.values()) {
          ids.add(task.conversationId);
        }

        return Array.from(ids);
      },

      // ========================================
      // 流式消息操作
      // ========================================

      startStreaming: (conversationId, messageId, options) => {
        set((state) => {
          const streamingMessages = new Map(state.streamingMessages);
          const targetId = messageId.startsWith('streaming-') ? messageId : `streaming-${messageId}`;
          streamingMessages.set(conversationId, targetId);

          const optimisticMessages = new Map(state.optimisticMessages);
          const list = optimisticMessages.get(conversationId) || [];

          // 幂等性检查
          if (!list.some((m) => m.id === targetId)) {
            const streamingMessage: Message = {
              id: targetId,
              conversation_id: conversationId,
              role: 'assistant',
              content: [{ type: 'text', text: options?.initialContent ?? '' }],
              status: 'streaming',
              created_at: options?.createdAt || new Date().toISOString(),
              generation_params: options?.generationParams,
            };
            optimisticMessages.set(conversationId, [...list, streamingMessage]);
          }

          return { streamingMessages, optimisticMessages, isSending: true };
        });
      },

      registerStreamingId: (conversationId, messageId) => {
        set((state) => {
          const streamingMessages = new Map(state.streamingMessages);
          streamingMessages.set(conversationId, messageId);
          return { streamingMessages, isSending: true };
        });
      },

      appendStreamingContent: (conversationId, chunk) => {
        set((state) => {
          const streamingId = state.streamingMessages.get(conversationId);
          if (!streamingId) return state;

          const optimisticMessages = new Map(state.optimisticMessages);
          const list = optimisticMessages.get(conversationId);
          if (!list) return state;

          const updatedList = list.map((m) =>
            m.id === streamingId
              ? { ...m, content: [{ type: 'text' as const, text: getTextContent(m) + chunk }] }
              : m
          );

          optimisticMessages.set(conversationId, updatedList);
          return { optimisticMessages };
        });
      },

      setStreamingContent: (conversationId, content) => {
        set((state) => {
          const streamingId = state.streamingMessages.get(conversationId);
          if (!streamingId) return state;

          const optimisticMessages = new Map(state.optimisticMessages);
          const list = optimisticMessages.get(conversationId);
          if (!list) return state;

          const updatedList = list.map((m) =>
            m.id === streamingId
              ? { ...m, content: [{ type: 'text' as const, text: content }] }
              : m
          );

          optimisticMessages.set(conversationId, updatedList);
          return { optimisticMessages };
        });
      },

      completeStreaming: (conversationId) => {
        set((state) => {
          const streamingMessages = new Map(state.streamingMessages);
          streamingMessages.delete(conversationId);
          return { streamingMessages, isSending: false };
        });
      },

      completeStreamingWithMessage: (conversationId, message) => {
        set((state) => {
          const streamingMessages = new Map(state.streamingMessages);
          streamingMessages.delete(conversationId);

          const optimisticMessages = new Map(state.optimisticMessages);
          const list = optimisticMessages.get(conversationId) || [];
          const filteredList = list.filter((m) => !m.id.startsWith('streaming-'));
          optimisticMessages.set(conversationId, [...filteredList, normalizeMessage(message)]);

          return { streamingMessages, optimisticMessages, isSending: false };
        });
      },

      getStreamingMessageId: (conversationId) => {
        return get().streamingMessages.get(conversationId) || null;
      },

      // ========================================
      // 乐观消息操作
      // ========================================

      addOptimisticMessage: (conversationId, message) => {
        set((state) => {
          const optimisticMessages = new Map(state.optimisticMessages);
          const list = optimisticMessages.get(conversationId) || [];

          // 幂等性检查：已存在则不重复添加
          if (list.some((m) => m.id === message.id)) {
            return state;
          }

          optimisticMessages.set(conversationId, [...list, normalizeMessage(message)]);
          return { optimisticMessages };
        });
      },

      addOptimisticUserMessage: (conversationId, message) => {
        set((state) => {
          const optimisticMessages = new Map(state.optimisticMessages);
          const list = optimisticMessages.get(conversationId) || [];

          // 幂等性检查：已存在则不重复添加
          if (list.some((m) => m.id === message.id)) {
            return state;
          }

          optimisticMessages.set(conversationId, [...list, normalizeMessage(message)]);
          return { optimisticMessages, isSending: true };
        });
      },

      updateOptimisticMessageId: (conversationId, clientRequestId, newId) => {
        set((state) => {
          const optimisticMessages = new Map(state.optimisticMessages);
          const list = optimisticMessages.get(conversationId);
          if (!list) return state;

          const updatedList = list.map((msg) =>
            msg.client_request_id === clientRequestId
              ? { ...msg, id: newId, status: 'completed' as const }
              : msg
          );

          optimisticMessages.set(conversationId, updatedList);
          return { optimisticMessages };
        });
      },

      addErrorMessage: (conversationId, errorMessage) => {
        set((state) => {
          const optimisticMessages = new Map(state.optimisticMessages);
          const list = optimisticMessages.get(conversationId) || [];

          if (list.some((m) => m.id === errorMessage.id)) {
            return state;
          }

          const filteredList = list.filter((m) => !m.id.startsWith('streaming-'));
          optimisticMessages.set(conversationId, [...filteredList, normalizeMessage(errorMessage)]);

          const streamingMessages = new Map(state.streamingMessages);
          streamingMessages.delete(conversationId);

          return { optimisticMessages, streamingMessages, isSending: false };
        });
      },

      removeOptimisticMessage: (conversationId, messageId) => {
        set((state) => {
          const optimisticMessages = new Map(state.optimisticMessages);
          const list = optimisticMessages.get(conversationId);
          if (!list) return state;

          optimisticMessages.set(
            conversationId,
            list.filter((m) => m.id !== messageId)
          );
          return { optimisticMessages };
        });
      },

      replaceMediaPlaceholder: (conversationId, placeholderId, realMessage) => {
        set((state) => {
          // 先检查 optimisticMessages
          const optimisticMessages = new Map(state.optimisticMessages);
          const optimisticList = optimisticMessages.get(conversationId);

          if (optimisticList) {
            const found = optimisticList.some((m) => m.id === placeholderId);
            if (found) {
              const updatedList = optimisticList.map((m) =>
                m.id === placeholderId ? normalizeMessage(realMessage) : m
              );
              const hasOtherStreaming = updatedList.some(
                (m) => m.id.startsWith('streaming-') && m.id !== realMessage.id
              );
              optimisticMessages.set(conversationId, updatedList);
              return { optimisticMessages, isSending: hasOtherStreaming };
            }
          }

          // 再检查 messages（用于 retry 场景）
          const messages = new Map(state.messages);
          const messageList = messages.get(conversationId);

          if (messageList) {
            const found = messageList.some((m) => m.id === placeholderId);
            if (found) {
              const updatedList = messageList.map((m) =>
                m.id === placeholderId ? normalizeMessage(realMessage) : m
              );
              messages.set(conversationId, updatedList);
              return { messages, isSending: false };
            }
          }

          return state;
        });
      },

      getOptimisticMessages: (conversationId) => {
        return get().optimisticMessages.get(conversationId) || [];
      },

      // ========================================
      // 缓存操作
      // ========================================

      getCachedMessages: (conversationId) => {
        const state = get();
        const messages = state.messages.get(conversationId);
        const meta = state.cacheMetadata.get(conversationId);

        if (!messages) return null;

        return {
          messages,
          hasMore: meta?.hasMore ?? false,
          lastFetchedAt: meta?.lastFetchedAt ?? 0,
        };
      },

      touchCache: (conversationId) => {
        set((state) => {
          const newOrder = state.cacheAccessOrder.filter((id) => id !== conversationId);
          newOrder.push(conversationId);
          return { cacheAccessOrder: newOrder };
        });
      },

      isCacheExpired: (conversationId) => {
        const meta = get().cacheMetadata.get(conversationId);
        if (!meta) return true;
        return Date.now() - meta.lastFetchedAt > CACHE_CONFIG.CACHE_EXPIRY_MS;
      },

      clearConversationCache: (conversationId) => {
        set((state) => {
          const messages = new Map(state.messages);
          const cacheMetadata = new Map(state.cacheMetadata);

          messages.delete(conversationId);
          cacheMetadata.delete(conversationId);

          const cacheAccessOrder = state.cacheAccessOrder.filter((id) => id !== conversationId);
          return { messages, cacheMetadata, cacheAccessOrder };
        });
      },

      setMessagesForConversation: (conversationId, msgs, hasMore = false) => {
        set((state) => {
          const messages = new Map(state.messages);
          const cacheMetadata = new Map(state.cacheMetadata);
          let cacheAccessOrder = [...state.cacheAccessOrder];

          // LRU 淘汰
          cacheAccessOrder = cacheAccessOrder.filter((id) => id !== conversationId);
          while (cacheAccessOrder.length >= CACHE_CONFIG.MAX_CACHED_CONVERSATIONS) {
            const oldestId = cacheAccessOrder.shift();
            if (oldestId) {
              messages.delete(oldestId);
              cacheMetadata.delete(oldestId);
            }
          }

          const normalizedMsgs = msgs.map(normalizeMessage);
          messages.set(conversationId, normalizedMsgs);
          cacheMetadata.set(conversationId, {
            hasMore,
            lastFetchedAt: Date.now(),
          });
          cacheAccessOrder.push(conversationId);

          return { messages, cacheMetadata, cacheAccessOrder };
        });
      },

      // ========================================
      // 强制刷新标记
      // ========================================

      markForceRefresh: (conversationId) => {
        set((state) => {
          const forceRefreshConversations = new Set(state.forceRefreshConversations);
          forceRefreshConversations.add(conversationId);
          return { forceRefreshConversations };
        });
      },

      clearForceRefresh: (conversationId) => {
        set((state) => {
          if (!state.forceRefreshConversations.has(conversationId)) return state;
          const forceRefreshConversations = new Set(state.forceRefreshConversations);
          forceRefreshConversations.delete(conversationId);
          return { forceRefreshConversations };
        });
      },

      needsForceRefresh: (conversationId) => {
        return get().forceRefreshConversations.has(conversationId);
      },

      // ========================================
      // 对话操作
      // ========================================

      setConversations: (conversations) => set({ conversations }),

      setConversationsLoading: (loading) => set({ conversationsLoading: loading }),

      setCurrentConversation: (id, title) =>
        set({
          currentConversationId: id,
          currentConversationTitle: title,
        }),

      setIsSending: (sending) => set({ isSending: sending }),

      deleteConversation: (id) => {
        set((state) => {
          const messages = new Map(state.messages);
          const cacheMetadata = new Map(state.cacheMetadata);
          const optimisticMessages = new Map(state.optimisticMessages);

          messages.delete(id);
          cacheMetadata.delete(id);
          optimisticMessages.delete(id);

          const conversations = state.conversations.filter((c) => c.id !== id);
          const cacheAccessOrder = state.cacheAccessOrder.filter((cid) => cid !== id);
          const needReset = state.currentConversationId === id;

          return {
            messages,
            cacheMetadata,
            optimisticMessages,
            conversations,
            cacheAccessOrder,
            ...(needReset
              ? {
                  currentConversationId: null,
                  currentConversationTitle: '新对话',
                }
              : {}),
          };
        });
      },

      renameConversation: (id, title) =>
        set((state) => ({
          conversations: state.conversations.map((c) =>
            c.id === id ? { ...c, title } : c
          ),
          ...(state.currentConversationId === id
            ? { currentConversationTitle: title }
            : {}),
        })),

      createConversation: (title) => {
        const id = Date.now().toString();
        const now = new Date().toISOString();
        const newConversation: Conversation = {
          id,
          title,
          lastMessage: '',
          updatedAt: now,
        };

        set((state) => ({
          conversations: [newConversation, ...state.conversations],
          currentConversationId: id,
          currentConversationTitle: title,
        }));

        return id;
      },

      // ========================================
      // 未读状态
      // ========================================

      markConversationUnread: (conversationId) => {
        set((state) => {
          if (state.currentConversationId === conversationId) return state;
          const unreadConversations = new Set(state.unreadConversations);
          unreadConversations.add(conversationId);
          return { unreadConversations };
        });
      },

      clearConversationUnread: (conversationId) => {
        set((state) => {
          if (!state.unreadConversations.has(conversationId)) return state;
          const unreadConversations = new Set(state.unreadConversations);
          unreadConversations.delete(conversationId);
          return { unreadConversations };
        });
      },

      hasUnreadMessages: (conversationId) => {
        return get().unreadConversations.has(conversationId);
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

      // ========================================
      // 辅助方法
      // ========================================

      getMessages: (conversationId) => get().messages.get(conversationId) || [],

      getMessage: (messageId) => {
        const state = get();

        // 检查持久化消息
        for (const list of state.messages.values()) {
          const found = list.find((m) => m.id === messageId);
          if (found) return found;
        }

        // 检查乐观消息
        for (const list of state.optimisticMessages.values()) {
          const found = list.find((m) => m.id === messageId);
          if (found) return found;
        }

        return undefined;
      },

      clearConversation: (conversationId) => {
        set((state) => {
          const messages = new Map(state.messages);
          const optimisticMessages = new Map(state.optimisticMessages);

          messages.delete(conversationId);
          optimisticMessages.delete(conversationId);

          return { messages, optimisticMessages };
        });
      },

      cleanup: (keepConversationIds) => {
        set((state) => {
          const keepSet = new Set(keepConversationIds);
          const optimisticMessages = new Map<string, Message[]>();

          for (const [id, msgs] of state.optimisticMessages.entries()) {
            if (keepSet.has(id)) {
              optimisticMessages.set(id, msgs);
            }
          }

          return { optimisticMessages };
        });
      },

      reset: () =>
        set({
          ...initialState,
          messages: new Map(),
          cacheMetadata: new Map(),
          tasks: new Map(),
          chatTasks: new Map(),
          mediaTasks: new Map(),
          streamingMessages: new Map(),
          optimisticMessages: new Map(),
          unreadConversations: new Set(),
          forceRefreshConversations: new Set(),
          recentlyCompleted: new Set(),
        }),
    }),
    {
      name: 'everydayai_message_store',
      storage: customStorage,
      partialize: (state) => ({
        messages: state.messages,
        cacheMetadata: state.cacheMetadata,
        cacheAccessOrder: state.cacheAccessOrder,
        conversations: state.conversations,
      }),
      onRehydrateStorage: () => {
        return () => {
          // hydrate 完成后通知 TaskRestorationStore
          import('./useTaskRestorationStore').then(({ useTaskRestorationStore }) => {
            useTaskRestorationStore.getState().setHydrateComplete();
          });
        };
      },
    }
  )
);

// 导出便捷 hooks
export const useMessages = (conversationId: string) =>
  useMessageStore((state) => state.getMessages(conversationId));

export const useCurrentConversation = () =>
  useMessageStore((state) => ({
    id: state.currentConversationId,
    title: state.currentConversationTitle,
  }));

export const useIsSending = () => useMessageStore((state) => state.isSending);
