/**
 * 对话管理 Slice
 *
 * 管理对话列表和当前对话状态
 */

import type { StateCreator } from 'zustand';
import type { Conversation, Message, MessageCacheEntry } from '../../types/message';
import { CACHE_CONFIG } from './messageSlice';
import { normalizeMessage } from '../../utils/messageUtils';

// ============================================================
// 类型定义
// ============================================================

export interface ConversationSlice {
  /** 对话列表 */
  conversations: Conversation[];
  conversationsLoading: boolean;

  /** 当前对话 */
  currentConversationId: string | null;
  currentConversationTitle: string;

  /** 未读对话 */
  unreadConversations: Set<string>;

  /** 需要强制刷新的对话 */
  forceRefreshConversations: Set<string>;

  // 对话操作
  setConversations: (conversations: Conversation[]) => void;
  setConversationsLoading: (loading: boolean) => void;
  setCurrentConversation: (id: string | null, title: string) => void;
  deleteConversation: (id: string) => void;
  renameConversation: (id: string, title: string) => void;
  createConversation: (title: string) => string;

  // 缓存操作
  getCachedMessages: (conversationId: string) => MessageCacheEntry | null;
  touchCache: (conversationId: string) => void;
  isCacheExpired: (conversationId: string) => boolean;
  clearConversationCache: (conversationId: string) => void;
  setMessagesForConversation: (conversationId: string, messages: Message[], hasMore?: boolean) => void;

  // 强制刷新标记
  markForceRefresh: (conversationId: string) => void;
  clearForceRefresh: (conversationId: string) => void;
  needsForceRefresh: (conversationId: string) => boolean;

  // 未读状态
  markConversationUnread: (conversationId: string) => void;
  clearConversationUnread: (conversationId: string) => void;
  hasUnreadMessages: (conversationId: string) => boolean;

  // 清理
  cleanup: (keepConversationIds: string[]) => void;
  reset: () => void;
}

// Store 依赖类型
export interface ConversationSliceDeps {
  messages: Record<string, Message[]>;
  cacheMetadata: Record<string, { hasMore: boolean; lastFetchedAt: number }>;
  cacheAccessOrder: string[];
  optimisticMessages: Map<string, Message[]>;
  streamingMessages: Map<string, string>;
  tasks: Map<string, unknown>;
  chatTasks: Map<string, unknown>;
  recentlyCompleted: Set<string>;
  forceRefreshConversations: Set<string>;
  unreadConversations: Set<string>;
  isSending: boolean;
  pendingNotifications: unknown[];
}

// ============================================================
// Slice 创建器
// ============================================================

export const createConversationSlice: StateCreator<
  ConversationSlice & ConversationSliceDeps,
  [],
  [],
  ConversationSlice
> = (set, get) => ({
  // 初始状态
  conversations: [] as Conversation[],
  conversationsLoading: false,
  currentConversationId: null as string | null,
  currentConversationTitle: '新对话',
  unreadConversations: new Set<string>(),
  forceRefreshConversations: new Set<string>(),

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

  deleteConversation: (id) => {
    set((state) => {
      const messages = { ...state.messages };
      const cacheMetadata = { ...state.cacheMetadata };
      const optimisticMessages = new Map(state.optimisticMessages);

      delete messages[id];
      delete cacheMetadata[id];
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
  // 缓存操作
  // ========================================

  getCachedMessages: (conversationId) => {
    const state = get();
    const messages = state.messages[conversationId];
    const meta = state.cacheMetadata[conversationId];

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
    const meta = get().cacheMetadata[conversationId];
    if (!meta) return true;
    return Date.now() - meta.lastFetchedAt > CACHE_CONFIG.CACHE_EXPIRY_MS;
  },

  clearConversationCache: (conversationId) => {
    set((state) => {
      const messages = { ...state.messages };
      const cacheMetadata = { ...state.cacheMetadata };

      delete messages[conversationId];
      delete cacheMetadata[conversationId];

      const cacheAccessOrder = state.cacheAccessOrder.filter((id) => id !== conversationId);
      return { messages, cacheMetadata, cacheAccessOrder };
    });
  },

  setMessagesForConversation: (conversationId, msgs, hasMore = false) => {
    set((state) => {
      const messages = { ...state.messages };
      const cacheMetadata = { ...state.cacheMetadata };
      let cacheAccessOrder = [...state.cacheAccessOrder];

      // LRU 淘汰
      cacheAccessOrder = cacheAccessOrder.filter((id) => id !== conversationId);
      while (cacheAccessOrder.length >= CACHE_CONFIG.MAX_CACHED_CONVERSATIONS) {
        const oldestId = cacheAccessOrder.shift();
        if (oldestId) {
          delete messages[oldestId];
          delete cacheMetadata[oldestId];
        }
      }

      const normalizedMsgs = msgs.map(normalizeMessage);
      messages[conversationId] = normalizedMsgs;
      cacheMetadata[conversationId] = { hasMore, lastFetchedAt: Date.now() };

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
  // 清理
  // ========================================

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
      messages: {},
      cacheMetadata: {},
      cacheAccessOrder: [],
      tasks: new Map(),
      chatTasks: new Map(),
      streamingMessages: new Map(),
      optimisticMessages: new Map(),
      unreadConversations: new Set(),
      forceRefreshConversations: new Set(),
      recentlyCompleted: new Set(),
      conversations: [],
      conversationsLoading: false,
      currentConversationId: null,
      currentConversationTitle: '新对话',
      isSending: false,
      pendingNotifications: [],
    }),
});
