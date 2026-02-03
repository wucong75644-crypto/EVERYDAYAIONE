/**
 * 聊天状态管理
 *
 * 管理对话列表、消息、发送状态、消息缓存等
 *
 * 重构说明（方案B）：
 * - 删除了旧的 CacheMessage 类型（驼峰命名），统一使用 API 的 Message 格式（下划线命名）
 * - messageCache 直接存储 Message 格式，无需格式转换
 * - 新增统一操作方法：appendMessage, replaceMessage, removeMessage, setMessagesForConversation
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { type Message } from '../services/message';

// 重新导出 Message 类型，保持向后兼容
export type { Message } from '../services/message';

export interface Conversation {
  id: string;
  title: string;
  lastMessage: string;
  updatedAt: string;
}

/** 消息缓存条目 */
export interface MessageCacheEntry {
  messages: Message[];
  hasMore: boolean;
  lastFetchedAt: number;
  isSending?: boolean;
}

/** 缓存配置 */
const CACHE_CONFIG = {
  MAX_CACHED_CONVERSATIONS: 10,
  CACHE_EXPIRY_MS: 5 * 60 * 1000, // 5分钟
};

/** 持久化状态类型（只包含需要缓存的字段） */
type PersistedState = Pick<ChatState, 'messageCache' | 'cacheAccessOrder'>;

/** 自定义序列化函数 - 将 Map 转换为 Object */
const customStorage = createJSONStorage<PersistedState>(() => ({
  getItem: (name) => {
    const str = localStorage.getItem(name);
    if (!str) return null;

    try {
      const data = JSON.parse(str);
      // 将缓存的对象转回 Map
      if (data.state?.messageCacheObj) {
        const cache = new Map<string, MessageCacheEntry>();
        Object.entries(data.state.messageCacheObj as Record<string, MessageCacheEntry>).forEach(([key, value]) => {
          cache.set(key, value);
        });
        data.state.messageCache = cache;
        delete data.state.messageCacheObj;
      }
      return data;
    } catch {
      return null;
    }
  },
  setItem: (name, value) => {
    try {
      const data = JSON.parse(value);
      // 将 Map 转换为普通对象以便序列化
      if (data.state?.messageCache instanceof Map) {
        const obj: Record<string, MessageCacheEntry> = {};
        data.state.messageCache.forEach((value: MessageCacheEntry, key: string) => {
          obj[key] = value;
        });
        data.state.messageCacheObj = obj;
        delete data.state.messageCache;
      }
      localStorage.setItem(name, JSON.stringify(data));
    } catch {
      // 缓存保存失败，不影响功能
    }
  },
  removeItem: (name) => {
    localStorage.removeItem(name);
  },
}));

interface ChatState {
  // 对话列表
  conversations: Conversation[];
  conversationsLoading: boolean;

  // 当前对话
  currentConversationId: string | null;
  currentConversationTitle: string;

  // 发送状态
  isSending: boolean;

  // 消息缓存 Map<conversationId, MessageCacheEntry>
  messageCache: Map<string, MessageCacheEntry>;
  // LRU 访问顺序记录
  cacheAccessOrder: string[];

  // 有新消息的对话（用于切换对话时决定滚动行为）
  unreadConversations: Set<string>;

  // Actions
  setConversations: (conversations: Conversation[]) => void;
  setConversationsLoading: (loading: boolean) => void;
  setCurrentConversation: (id: string | null, title: string) => void;
  setIsSending: (sending: boolean) => void;
  deleteConversation: (id: string) => void;
  renameConversation: (id: string, title: string) => void;
  createConversation: (title: string) => string;
  reset: () => void;

  // ========================================
  // 统一操作入口（方案B 新增）
  // ========================================

  /** 追加消息到缓存末尾 */
  appendMessage: (conversationId: string, message: Message) => void;
  /** 替换缓存中的消息（通过 messageId 查找） */
  replaceMessage: (conversationId: string, messageId: string, newMessage: Message) => void;
  /** 删除缓存中的消息 */
  removeMessage: (conversationId: string, messageId: string) => void;
  /** 批量设置对话的所有消息（从后端加载时使用） */
  setMessagesForConversation: (conversationId: string, messages: Message[], hasMore?: boolean) => void;
  /** 向缓存顶部追加消息（加载历史消息时使用） */
  prependMessages: (conversationId: string, messages: Message[], hasMore: boolean) => void;
  /** 清空指定对话的缓存 */
  clearConversationCache: (conversationId: string) => void;

  // ========================================
  // 缓存操作
  // ========================================
  getCachedMessages: (conversationId: string) => MessageCacheEntry | null;
  touchCache: (conversationId: string) => void;
  updateMessageId: (conversationId: string, clientRequestId: string, newId: string) => void;
  setCacheSendingState: (conversationId: string, isSending: boolean) => void;
  deleteCachedMessages: (conversationId: string) => void;
  isCacheExpired: (conversationId: string) => boolean;
  clearAllCache: () => void;

  // 未读消息操作
  markConversationUnread: (conversationId: string) => void;
  clearConversationUnread: (conversationId: string) => void;
  hasUnreadMessages: (conversationId: string) => boolean;
}

const initialState = {
  conversations: [] as Conversation[],
  conversationsLoading: false,
  currentConversationId: null as string | null,
  currentConversationTitle: '新对话',
  isSending: false,
  messageCache: new Map<string, MessageCacheEntry>(),
  cacheAccessOrder: [] as string[],
  unreadConversations: new Set<string>(),
};

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      ...initialState,

      setConversations: (conversations) => set({ conversations }),

  setConversationsLoading: (loading) => set({ conversationsLoading: loading }),

  setCurrentConversation: (id, title) =>
    set({
      currentConversationId: id,
      currentConversationTitle: title,
    }),

  setIsSending: (sending) => set({ isSending: sending }),

  deleteConversation: (id) => {
    const state = get();
    // 同时删除缓存
    const newCache = new Map(state.messageCache);
    newCache.delete(id);
    const newOrder = state.cacheAccessOrder.filter((cid) => cid !== id);

    const newConversations = state.conversations.filter((c) => c.id !== id);
    const needReset = state.currentConversationId === id;
    set({
      conversations: newConversations,
      messageCache: newCache,
      cacheAccessOrder: newOrder,
      ...(needReset
        ? {
            currentConversationId: null,
            currentConversationTitle: '新对话',
          }
        : {}),
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

  reset: () => set({
    ...initialState,
    messageCache: new Map(),
    cacheAccessOrder: [],
  }),

  // ========================================
  // 统一操作入口（方案B 新增）
  // ========================================

  // 追加消息到缓存末尾（统一入口）
  appendMessage: (conversationId: string, message: Message) => {
    set((state) => {
      const cached = state.messageCache.get(conversationId);

      // 如果缓存不存在，创建新缓存
      if (!cached) {
        const newCache = new Map(state.messageCache);
        const newOrder = [...state.cacheAccessOrder];

        // LRU 淘汰
        while (newOrder.length >= CACHE_CONFIG.MAX_CACHED_CONVERSATIONS) {
          const oldestId = newOrder.shift();
          if (oldestId) newCache.delete(oldestId);
        }

        newCache.set(conversationId, {
          messages: [message],
          hasMore: false,
          lastFetchedAt: Date.now(),
        });
        newOrder.push(conversationId);

        return { messageCache: newCache, cacheAccessOrder: newOrder };
      }

      // 缓存存在，检查消息是否已存在（去重）
      if (cached.messages.some((m) => m.id === message.id)) {
        return state; // 消息已存在，不重复追加
      }

      const newCache = new Map(state.messageCache);
      newCache.set(conversationId, {
        ...cached,
        messages: [...cached.messages, message],
      });

      return { messageCache: newCache };
    });
  },

  // 替换缓存中的消息（统一入口）
  replaceMessage: (conversationId: string, messageId: string, newMessage: Message) => {
    set((state) => {
      const cached = state.messageCache.get(conversationId);
      if (!cached) return state;

      const messageIndex = cached.messages.findIndex((m) => m.id === messageId);
      if (messageIndex === -1) {
        // 消息不存在，追加到末尾
        const newCache = new Map(state.messageCache);
        newCache.set(conversationId, {
          ...cached,
          messages: [...cached.messages, newMessage],
        });
        return { messageCache: newCache };
      }

      // 替换消息
      const newMessages = [...cached.messages];
      newMessages[messageIndex] = newMessage;

      const newCache = new Map(state.messageCache);
      newCache.set(conversationId, {
        ...cached,
        messages: newMessages,
      });

      return { messageCache: newCache };
    });
  },

  // 删除缓存中的消息（统一入口）
  removeMessage: (conversationId: string, messageId: string) => {
    set((state) => {
      const cached = state.messageCache.get(conversationId);
      if (!cached) return state;

      const newMessages = cached.messages.filter((m) => m.id !== messageId);

      const newCache = new Map(state.messageCache);
      newCache.set(conversationId, {
        ...cached,
        messages: newMessages,
      });

      return { messageCache: newCache };
    });
  },

  // 批量设置对话的所有消息（统一入口，从后端加载时使用）
  setMessagesForConversation: (conversationId: string, messages: Message[], hasMore = false) => {
    const state = get();
    const newCache = new Map(state.messageCache);
    let newOrder = [...state.cacheAccessOrder];

    // 移除旧的顺序记录
    newOrder = newOrder.filter((id) => id !== conversationId);

    // LRU 淘汰
    while (newOrder.length >= CACHE_CONFIG.MAX_CACHED_CONVERSATIONS) {
      const oldestId = newOrder.shift();
      if (oldestId) newCache.delete(oldestId);
    }

    newCache.set(conversationId, {
      messages,
      hasMore,
      lastFetchedAt: Date.now(),
    });
    newOrder.push(conversationId);

    set({ messageCache: newCache, cacheAccessOrder: newOrder });
  },

  // 向缓存顶部追加消息（加载历史消息时使用）
  prependMessages: (conversationId: string, messages: Message[], hasMore: boolean) => {
    set((state) => {
      const cached = state.messageCache.get(conversationId);
      if (!cached) return state;

      // 去重：过滤掉已存在的消息
      const existingIds = new Set(cached.messages.map(m => m.id));
      const newMessages = messages.filter(m => !existingIds.has(m.id));

      if (newMessages.length === 0) {
        // 没有新消息，只更新 hasMore 状态
        const newCache = new Map(state.messageCache);
        newCache.set(conversationId, { ...cached, hasMore });
        return { messageCache: newCache };
      }

      const newCache = new Map(state.messageCache);
      newCache.set(conversationId, {
        ...cached,
        messages: [...newMessages, ...cached.messages], // 追加到顶部
        hasMore,
      });

      return { messageCache: newCache };
    });
  },

  // 清空指定对话的缓存（统一入口）
  clearConversationCache: (conversationId: string) => {
    const state = get();
    const newCache = new Map(state.messageCache);
    newCache.delete(conversationId);
    const newOrder = state.cacheAccessOrder.filter((id) => id !== conversationId);
    set({ messageCache: newCache, cacheAccessOrder: newOrder });
  },

  // ========================================
  // 缓存操作（旧方法，过渡期保留）
  // ========================================

  // 获取缓存的消息（只读，不更新LRU顺序）
  getCachedMessages: (conversationId: string) => {
    const state = get();
    return state.messageCache.get(conversationId) ?? null;
  },

  // 更新LRU访问顺序（在访问缓存时调用）
  touchCache: (conversationId: string) => {
    const state = get();
    const cached = state.messageCache.get(conversationId);
    if (!cached) return;

    // 更新 LRU 访问顺序
    const newOrder = state.cacheAccessOrder.filter((id) => id !== conversationId);
    newOrder.push(conversationId);
    set({ cacheAccessOrder: newOrder });
  },

  // 根据 client_request_id 只更新消息 ID 和状态（精简版乐观更新）
  updateMessageId: (conversationId: string, clientRequestId: string, newId: string) => {
    set((state) => {
      const cached = state.messageCache.get(conversationId);
      if (!cached) return state;

      const updatedMessages = cached.messages.map((msg) =>
        msg.client_request_id === clientRequestId
          ? { ...msg, id: newId, status: 'sent' as const }
          : msg
      );

      const newCache = new Map(state.messageCache);
      newCache.set(conversationId, { ...cached, messages: updatedMessages });

      return { messageCache: newCache };
    });
  },

  // 设置缓存的发送中状态
  setCacheSendingState: (conversationId: string, isSending: boolean) => {
    const state = get();
    const cached = state.messageCache.get(conversationId);
    if (!cached) return;

    const newCache = new Map(state.messageCache);
    newCache.set(conversationId, {
      ...cached,
      isSending,
    });
    set({ messageCache: newCache });
  },

  // 删除指定对话的缓存
  deleteCachedMessages: (conversationId: string) => {
    const state = get();
    const newCache = new Map(state.messageCache);
    newCache.delete(conversationId);
    const newOrder = state.cacheAccessOrder.filter((id) => id !== conversationId);
    set({ messageCache: newCache, cacheAccessOrder: newOrder });
  },

  // 检查缓存是否过期
  isCacheExpired: (conversationId: string) => {
    const state = get();
    const cached = state.messageCache.get(conversationId);
    if (!cached) return true;
    return Date.now() - cached.lastFetchedAt > CACHE_CONFIG.CACHE_EXPIRY_MS;
  },

  // 清空所有缓存
  clearAllCache: () => {
    set({ messageCache: new Map(), cacheAccessOrder: [] });
  },

  // 标记对话有新消息（任务完成时调用）
  markConversationUnread: (conversationId: string) => {
    const state = get();
    // 如果用户当前就在这个对话，不标记为未读
    if (state.currentConversationId === conversationId) return;
    const newUnread = new Set(state.unreadConversations);
    newUnread.add(conversationId);
    set({ unreadConversations: newUnread });
  },

  // 清除对话未读状态（切换到该对话时调用）
  clearConversationUnread: (conversationId: string) => {
    const state = get();
    if (!state.unreadConversations.has(conversationId)) return;
    const newUnread = new Set(state.unreadConversations);
    newUnread.delete(conversationId);
    set({ unreadConversations: newUnread });
  },

  // 检查对话是否有未读消息
  hasUnreadMessages: (conversationId: string) => {
    const state = get();
    return state.unreadConversations.has(conversationId);
  },
}),
{
  name: 'everydayai_message_cache',
  storage: customStorage,
  // 只持久化缓存相关的状态，不持久化当前对话和加载状态
  partialize: (state) => ({
    messageCache: state.messageCache,
    cacheAccessOrder: state.cacheAccessOrder,
  }),
}
  )
);
