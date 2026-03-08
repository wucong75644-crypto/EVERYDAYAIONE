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
import { useTaskRestorationStore } from './useTaskRestorationStore';

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
// Store 创建
// ============================================================

// 持久化策略：只持久化 conversations（会话列表）
// messages 不持久化：切换对话从内存读取（秒显），刷新从 API 加载（骨架屏）

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
      storage: createJSONStorage(() => localStorage),
      // 只持久化会话列表，messages 由 API 加载（刷新）或内存缓存（切换）
      partialize: (state) => ({
        conversations: state.conversations,
      }),
      onRehydrateStorage: () => {
        return () => {
          // hydrate 完成后通知 TaskRestorationStore
          useTaskRestorationStore.getState().setHydrateComplete();
        };
      },
    }
  )
);

