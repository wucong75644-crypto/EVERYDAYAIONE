/**
 * 流式消息 Slice
 *
 * 管理流式消息状态和乐观更新
 */

import type { StateCreator } from 'zustand';
import type { ContentPart, Message, GenerationParams, TextPart, ToolStepPart } from '../../types/message';
import { createOptimisticMessageActions } from './optimisticMessageActions';
import { createStreamingLifecycleActions } from './streamingLifecycleActions';
import { createStreamingUiActions } from './streamingUiActions';

// ============================================================
// 类型定义
// ============================================================

export interface StreamingSlice {
  /** 发送状态 */
  isSending: boolean;

  /** 流式消息状态: conversationId -> messageId */
  streamingMessages: Map<string, string>;

  /** 乐观消息: conversationId -> messages */
  optimisticMessages: Map<string, Message[]>;

  // 流式消息操作
  startStreaming: (conversationId: string, messageId: string, options?: {
    initialContent?: string;
    createdAt?: string;
    generationParams?: GenerationParams;
  }) => void;
  registerStreamingId: (conversationId: string, messageId: string) => void;
  appendStreamingContent: (conversationId: string, chunk: string) => void;
  appendContentBlock: (conversationId: string, block: ContentPart) => void;
  /** text block 去重：替换最后一个 text block（message_chunk 已累积），不追加重复 */
  replaceLastTextBlock: (conversationId: string, block: TextPart) => void;
  setStreamingContent: (conversationId: string, content: string) => void;
  /** 刷新恢复：设置结构化 content blocks + 剩余流式文字 */
  restoreStreamingBlocks: (conversationId: string, blocks: ContentPart[], remainingText: string) => void;
  completeStreaming: (conversationId: string) => void;
  completeStreamingWithMessage: (conversationId: string, message: Message) => void;
  getStreamingMessageId: (conversationId: string) => string | null;

  // 乐观消息操作
  addOptimisticMessage: (conversationId: string, message: Message) => void;
  addOptimisticUserMessage: (conversationId: string, message: Message) => void;
  updateOptimisticMessageId: (conversationId: string, clientRequestId: string, newId: string) => void;
  addErrorMessage: (conversationId: string, errorMessage: Message) => void;
  removeOptimisticMessage: (conversationId: string, messageId: string) => void;
  getOptimisticMessages: (conversationId: string) => Message[];

  /** 按 tool_call_id 更新已有 content block（tool_step 状态更新） */
  updateContentBlock: (conversationId: string, toolCallId: string, updates: Partial<ToolStepPart>) => void;

  // 思考内容流式状态
  /** 流式思考内容: conversationId -> accumulated thinking text */
  streamingThinking: Map<string, string>;
  appendStreamingThinking: (conversationId: string, chunk: string) => void;
  getStreamingThinking: (conversationId: string) => string;

  // Agent Loop 步骤提示
  /** Agent Loop 步骤提示: conversationId -> "正在搜索..." */
  agentStepHint: Map<string, string>;
  setAgentStepHint: (conversationId: string, hint: string) => void;
  clearAgentStepHint: (conversationId: string) => void;

  // 建议问题: conversationId -> suggestions（不持久化，刷新消失）
  suggestions: Map<string, string[]>;
  setSuggestions: (conversationId: string, suggestions: string[]) => void;
  clearSuggestions: (conversationId: string) => void;

  // 工具写操作确认请求（Phase 3 B5）
  toolConfirmRequest: {
    toolCallId: string;
    toolName: string;
    arguments: Record<string, unknown>;
    description: string;
    timeout: number;
  } | null;
  setToolConfirmRequest: (request: StreamingSlice['toolConfirmRequest']) => void;

  // 发送状态
  setIsSending: (sending: boolean) => void;
}

// Store 依赖类型（用于跨 slice 访问）
export interface StreamingSliceDeps {
  messages: Record<string, Message[]>;
}

// ============================================================
// Slice 创建器
// ============================================================

export const createStreamingSlice: StateCreator<
  StreamingSlice & StreamingSliceDeps,
  [],
  [],
  StreamingSlice
> = (set, get) => ({
  // 初始状态
  isSending: false,
  streamingMessages: new Map<string, string>(),
  optimisticMessages: new Map<string, Message[]>(),
  streamingThinking: new Map<string, string>(),
  agentStepHint: new Map<string, string>(),
  suggestions: new Map<string, string[]>(),
  toolConfirmRequest: null,
  ...createStreamingLifecycleActions(set, get),
  ...createOptimisticMessageActions(set, get),
  ...createStreamingUiActions(set, get),

  // ========================================
  // 流式消息操作
  // ========================================

  appendStreamingContent: (conversationId, chunk) => {
    set((state) => {
      const streamingId = state.streamingMessages.get(conversationId);
      if (!streamingId) return state;

      const list = state.optimisticMessages.get(conversationId);
      if (!list) return state;

      const targetIndex = list.findIndex((m) => m.id === streamingId);
      if (targetIndex === -1) return state;

      const target = list[targetIndex];
      const content = [...target.content];

      // 多块模式：追加到最后一个 text block
      // 如果最后一个 block 不是 text（如 tool_result），创建新 text block
      const lastIndex = content.length - 1;
      if (lastIndex >= 0 && content[lastIndex].type === 'text') {
        content[lastIndex] = {
          type: 'text' as const,
          text: (content[lastIndex] as { text: string }).text + chunk,
        };
      } else {
        // 没有 text block 或最后一个不是 text → 创建新 text block
        content.push({ type: 'text' as const, text: chunk });
      }

      const updatedList = [...list];
      updatedList[targetIndex] = { ...target, content };

      const optimisticMessages = new Map(state.optimisticMessages);
      optimisticMessages.set(conversationId, updatedList);
      return { optimisticMessages };
    });
  },

  appendContentBlock: (conversationId, block) => {
    set((state) => {
      const streamingId = state.streamingMessages.get(conversationId);
      if (!streamingId) return state;

      const list = state.optimisticMessages.get(conversationId);
      if (!list) return state;

      const targetIndex = list.findIndex((m) => m.id === streamingId);
      if (targetIndex === -1) return state;

      const target = list[targetIndex];
      const content = [...target.content, block];

      const updatedList = [...list];
      updatedList[targetIndex] = { ...target, content };

      const optimisticMessages = new Map(state.optimisticMessages);
      optimisticMessages.set(conversationId, updatedList);
      return { optimisticMessages };
    });
  },

  replaceLastTextBlock: (conversationId, block) => {
    set((state) => {
      const streamingId = state.streamingMessages.get(conversationId);
      if (!streamingId) return state;

      const list = state.optimisticMessages.get(conversationId);
      if (!list) return state;

      const targetIndex = list.findIndex((m) => m.id === streamingId);
      if (targetIndex === -1) return state;

      const target = list[targetIndex];
      const content = [...target.content];

      // 找最后一个 text block，替换为 content_block_add 的完整版
      for (let i = content.length - 1; i >= 0; i--) {
        if (content[i].type === 'text') {
          content[i] = block;
          break;
        }
      }

      const updatedList = [...list];
      updatedList[targetIndex] = { ...target, content };

      const optimisticMessages = new Map(state.optimisticMessages);
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

  restoreStreamingBlocks: (conversationId, blocks, remainingText) => {
    set((state) => {
      const streamingId = state.streamingMessages.get(conversationId);
      if (!streamingId) return state;

      const optimisticMessages = new Map(state.optimisticMessages);
      const list = optimisticMessages.get(conversationId);
      if (!list) return state;

      // 构建 content：结构化 blocks + 剩余流式文字
      const content = [...blocks];
      if (remainingText) {
        content.push({ type: 'text' as const, text: remainingText });
      }

      const updatedList = list.map((m) =>
        m.id === streamingId
          ? { ...m, content }
          : m
      );

      optimisticMessages.set(conversationId, updatedList);
      return { optimisticMessages };
    });
  },

});
