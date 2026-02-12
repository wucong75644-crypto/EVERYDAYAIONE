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
 * - 使用 Slice 模式拆分代码
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

// Slice 导入
import {
  createMessageSlice,
  createTaskSlice,
  createStreamingSlice,
  createConversationSlice,
  type MessageSlice,
  type TaskSlice,
  type StreamingSlice,
  type ConversationSlice,
} from './slices';

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
// Store 类型
// ============================================================

export type MessageStore = MessageSlice & TaskSlice & StreamingSlice & ConversationSlice;

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
// Store 创建
// ============================================================

export const useMessageStore = create<MessageStore>()(
  persist(
    (...args) => ({
      // 组合所有 slice
      ...createMessageSlice(...args),
      ...createTaskSlice(...args),
      ...createStreamingSlice(...args),
      ...createConversationSlice(...args),
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

// ============================================================
// 便捷 Hooks
// ============================================================

export const useMessages = (conversationId: string) =>
  useMessageStore((state) => state.getMessages(conversationId));

export const useCurrentConversation = () =>
  useMessageStore((state) => ({
    id: state.currentConversationId,
    title: state.currentConversationTitle,
  }));

export const useIsSending = () => useMessageStore((state) => state.isSending);
