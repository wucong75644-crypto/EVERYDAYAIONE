/**
 * 聊天状态管理
 *
 * 管理对话列表、消息、发送状态、消息缓存等
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  imageUrl?: string;
  videoUrl?: string;
  createdAt: string;
}

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

  // 消息
  messages: Message[];
  messagesLoading: boolean;

  // 发送状态
  isSending: boolean;

  // 消息缓存 Map<conversationId, MessageCacheEntry>
  messageCache: Map<string, MessageCacheEntry>;
  // LRU 访问顺序记录
  cacheAccessOrder: string[];

  // 滚动位置缓存 Map<conversationId, scrollTop>（仅当用户在历史记录时保存）
  scrollPositions: Map<string, number>;

  // 有新消息的对话（用于切换对话时决定滚动行为）
  unreadConversations: Set<string>;

  // Actions
  setConversations: (conversations: Conversation[]) => void;
  setConversationsLoading: (loading: boolean) => void;
  setCurrentConversation: (id: string | null, title: string) => void;
  setMessages: (messages: Message[]) => void;
  addMessage: (message: Message) => void;
  setMessagesLoading: (loading: boolean) => void;
  setIsSending: (sending: boolean) => void;
  deleteConversation: (id: string) => void;
  renameConversation: (id: string, title: string) => void;
  createConversation: (title: string) => string;
  reset: () => void;

  // 缓存操作
  getCachedMessages: (conversationId: string) => MessageCacheEntry | null;
  touchCache: (conversationId: string) => void;
  setCachedMessages: (conversationId: string, entry: Omit<MessageCacheEntry, 'lastFetchedAt'>) => void;
  updateCachedMessages: (conversationId: string, messages: Message[], hasMore?: boolean) => void;
  addMessageToCache: (conversationId: string, message: Message) => void;
  replaceMessageInCache: (conversationId: string, messageId: string, newMessage: Message) => void;
  setCacheSendingState: (conversationId: string, isSending: boolean) => void;
  deleteCachedMessages: (conversationId: string) => void;
  isCacheExpired: (conversationId: string) => boolean;
  clearAllCache: () => void;

  // 滚动位置操作
  setScrollPosition: (conversationId: string, position: number) => void;
  getScrollPosition: (conversationId: string) => number | null;
  clearScrollPosition: (conversationId: string) => void;

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
  messages: [] as Message[],
  messagesLoading: false,
  isSending: false,
  messageCache: new Map<string, MessageCacheEntry>(),
  cacheAccessOrder: [] as string[],
  scrollPositions: new Map<string, number>(),
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
      messages: [],
    }),

  setMessages: (messages) => set({ messages }),

  addMessage: (message) =>
    set((state) => ({
      messages: [...state.messages, message],
    })),

  setMessagesLoading: (loading) => set({ messagesLoading: loading }),

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
            messages: [],
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
      messages: [],
    }));

    return id;
  },

  reset: () => set({
    ...initialState,
    messageCache: new Map(),
    cacheAccessOrder: [],
  }),

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

  // 设置缓存的消息（带 LRU 淘汰）
  setCachedMessages: (conversationId: string, entry: Omit<MessageCacheEntry, 'lastFetchedAt'>) => {
    const state = get();
    const newCache = new Map(state.messageCache);
    let newOrder = [...state.cacheAccessOrder];

    // 如果已存在，先移除旧的顺序记录
    newOrder = newOrder.filter((id) => id !== conversationId);

    // LRU 淘汰：超出上限时移除最久未访问的
    while (newOrder.length >= CACHE_CONFIG.MAX_CACHED_CONVERSATIONS) {
      const oldestId = newOrder.shift();
      if (oldestId) {
        newCache.delete(oldestId);
      }
    }

    // 添加新缓存
    newCache.set(conversationId, {
      ...entry,
      lastFetchedAt: Date.now(),
    });
    newOrder.push(conversationId);

    set({ messageCache: newCache, cacheAccessOrder: newOrder });
  },

  // 更新已缓存的消息（静默刷新后调用）- 使用函数式 set 避免并发覆盖
  updateCachedMessages: (conversationId: string, messages: Message[], hasMore?: boolean) => {
    set((state) => {
      const cached = state.messageCache.get(conversationId);
      if (!cached) return state;

      const newCache = new Map(state.messageCache);
      newCache.set(conversationId, {
        ...cached,
        messages,
        hasMore: hasMore ?? cached.hasMore,
        lastFetchedAt: Date.now(),
      });

      // 如果是当前对话，同步更新 messages 状态
      const updates: Partial<ChatState> = { messageCache: newCache };
      if (state.currentConversationId === conversationId) {
        updates.messages = messages;
      }

      return updates;
    });
  },

  // 追加消息到缓存（发送消息后调用）- 使用函数式 set 避免并发覆盖
  addMessageToCache: (conversationId: string, message: Message) => {
    set((state) => {
      const cached = state.messageCache.get(conversationId);
      if (!cached) return state;

      const newCache = new Map(state.messageCache);
      newCache.set(conversationId, {
        ...cached,
        messages: [...cached.messages, message],
      });
      return { messageCache: newCache };
    });
  },

  // 原子替换缓存中的单条消息（通过 messageId 查找并替换）
  replaceMessageInCache: (conversationId: string, messageId: string, newMessage: Message) => {
    set((state) => {
      const cached = state.messageCache.get(conversationId);
      if (!cached) return state;

      const messageIndex = cached.messages.findIndex((m) => m.id === messageId);
      if (messageIndex === -1) return state;

      const newMessages = [...cached.messages];
      newMessages[messageIndex] = newMessage;

      const newCache = new Map(state.messageCache);
      newCache.set(conversationId, {
        ...cached,
        messages: newMessages,
      });

      // 如果是当前对话，同步更新 messages 状态
      const updates: Partial<ChatState> = { messageCache: newCache };
      if (state.currentConversationId === conversationId) {
        updates.messages = newMessages;
      }

      return updates;
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

  // 保存滚动位置（仅当用户在历史记录时调用）
  setScrollPosition: (conversationId: string, position: number) => {
    const state = get();
    const newPositions = new Map(state.scrollPositions);
    newPositions.set(conversationId, position);
    set({ scrollPositions: newPositions });
  },

  // 获取保存的滚动位置
  getScrollPosition: (conversationId: string) => {
    const state = get();
    return state.scrollPositions.get(conversationId) ?? null;
  },

  // 清除滚动位置（用户在底部时调用，下次直接显示底部）
  clearScrollPosition: (conversationId: string) => {
    const state = get();
    const newPositions = new Map(state.scrollPositions);
    newPositions.delete(conversationId);
    set({ scrollPositions: newPositions });
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
