/**
 * 消息操作 Slice
 *
 * 管理消息的增删改查操作
 */

import type { StateCreator } from 'zustand';
import type { Message, ContentPart, TextPart, MessageStatus } from '../../types/message';
import { normalizeMessage } from '../../utils/messageUtils';

// ============================================================
// 类型定义
// ============================================================

export interface MessageSlice {
  /** 消息缓存: conversationId -> messages */
  messages: Map<string, Message[]>;

  /** 缓存元数据: conversationId -> { hasMore, lastFetchedAt } */
  cacheMetadata: Map<string, { hasMore: boolean; lastFetchedAt: number }>;

  /** LRU 访问顺序 */
  cacheAccessOrder: string[];

  // 消息操作
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

  // 辅助方法
  getMessages: (conversationId: string) => Message[];
  getMessage: (messageId: string) => Message | undefined;
  clearConversation: (conversationId: string) => void;
}

// Store 依赖类型（用于跨 slice 访问）
export interface MessageSliceDeps {
  optimisticMessages: Map<string, Message[]>;
}

// ============================================================
// 缓存配置
// ============================================================

export const CACHE_CONFIG = {
  MAX_CACHED_CONVERSATIONS: 10,
  CACHE_EXPIRY_MS: 5 * 60 * 1000, // 5分钟
};

// ============================================================
// Slice 创建器
// ============================================================

export const createMessageSlice: StateCreator<
  MessageSlice & MessageSliceDeps,
  [],
  [],
  MessageSlice
> = (set, get) => ({
  // 初始状态
  messages: new Map<string, Message[]>(),
  cacheMetadata: new Map<string, { hasMore: boolean; lastFetchedAt: number }>(),
  cacheAccessOrder: [] as string[],

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
    // 🔥 DEBUG: 记录 updateMessage 调用
    console.log('🔥 [DEBUG] updateMessage called:', { messageId, updates });

    set((state) => {
      const messages = new Map(state.messages);

      // 🔥 DEBUG: 显示当前 messages Map 状态
      console.log('🔥 [DEBUG] updateMessage - current messages Map size:', messages.size);
      for (const [convId, list] of messages) {
        console.log(`🔥 [DEBUG] updateMessage - convId: ${convId}, messages:`, list.map(m => ({ id: m.id, status: m.status, content: m.content })));
      }

      for (const [convId, list] of messages) {
        const index = list.findIndex((m) => m.id === messageId);
        if (index !== -1) {
          console.log('🔥 [DEBUG] updateMessage - FOUND in messages:', { convId, index, oldMessage: list[index] });

          const updated = {
            ...list[index],
            ...updates,
            updated_at: new Date().toISOString(),
          };
          const newList = [...list];
          newList[index] = updated;
          messages.set(convId, newList);

          console.log('🔥 [DEBUG] updateMessage - UPDATED message:', updated);
          console.log('🔥 [DEBUG] updateMessage - new messages Map:', messages.get(convId));

          return { messages };
        }
      }

      console.log('🔥 [DEBUG] updateMessage - NOT FOUND in messages, checking optimisticMessages');

      // 也检查乐观消息
      const optimisticMessages = new Map(state.optimisticMessages);
      console.log('🔥 [DEBUG] updateMessage - optimisticMessages Map size:', optimisticMessages.size);

      for (const [convId, list] of optimisticMessages) {
        const index = list.findIndex((m) => m.id === messageId);
        if (index !== -1) {
          console.log('🔥 [DEBUG] updateMessage - FOUND in optimisticMessages:', { convId, index, oldMessage: list[index] });

          const updated = {
            ...list[index],
            ...updates,
            updated_at: new Date().toISOString(),
          };
          const newList = [...list];
          newList[index] = updated;
          optimisticMessages.set(convId, newList);

          console.log('🔥 [DEBUG] updateMessage - UPDATED optimistic message:', updated);

          return { optimisticMessages };
        }
      }

      console.warn('🔥 [DEBUG] updateMessage - MESSAGE NOT FOUND in either map!', { messageId });

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
});
