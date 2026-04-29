/**
 * 流式消息 Slice
 *
 * 管理流式消息状态和乐观更新
 */

import type { StateCreator } from 'zustand';
import type { Message, GenerationParams } from '../../types/message';
import { normalizeMessage } from '../../utils/messageUtils';

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
  appendContentBlock: (conversationId: string, block: Record<string, unknown>) => void;
  setStreamingContent: (conversationId: string, content: string) => void;
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
  updateContentBlock: (conversationId: string, toolCallId: string, updates: Record<string, unknown>) => void;

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

  // ========================================
  // 流式消息操作
  // ========================================

  startStreaming: (conversationId, messageId, options) => {
    set((state) => {
      const streamingMessages = new Map(state.streamingMessages);
      const targetId = messageId;
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
      const content = [...target.content, block as unknown as Message['content'][number]];

      const updatedList = [...list];
      updatedList[targetIndex] = { ...target, content };

      const optimisticMessages = new Map(state.optimisticMessages);
      optimisticMessages.set(conversationId, updatedList);
      return { optimisticMessages };
    });
  },

  updateContentBlock: (conversationId, toolCallId, updates) => {
    set((state) => {
      const streamingId = state.streamingMessages.get(conversationId);
      if (!streamingId) return state;
      const list = state.optimisticMessages.get(conversationId);
      if (!list) return state;
      const targetIndex = list.findIndex((m) => m.id === streamingId);
      if (targetIndex === -1) return state;
      const target = list[targetIndex];
      const content = target.content.map((block) =>
        block.type === 'tool_step' &&
        (block as { tool_call_id?: string }).tool_call_id === toolCallId
          ? { ...block, ...updates }
          : block,
      );
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

  completeStreaming: (conversationId) => {
    set((state) => {
      const streamingMessages = new Map(state.streamingMessages);
      streamingMessages.delete(conversationId);
      const streamingThinking = new Map(state.streamingThinking);
      streamingThinking.delete(conversationId);
      const agentStepHint = new Map(state.agentStepHint);
      agentStepHint.delete(conversationId);
      const suggestions = new Map(state.suggestions);
      suggestions.delete(conversationId);
      return { streamingMessages, streamingThinking, agentStepHint, suggestions, isSending: false };
    });
  },

  completeStreamingWithMessage: (conversationId, message) => {
    set((state) => {
      const streamingMessages = new Map(state.streamingMessages);
      const streamingId = streamingMessages.get(conversationId);
      streamingMessages.delete(conversationId);

      const optimisticMessages = new Map(state.optimisticMessages);
      const list = optimisticMessages.get(conversationId) || [];
      const filteredList = list.filter((m) => m.id !== streamingId);
      optimisticMessages.set(conversationId, [...filteredList, normalizeMessage(message)]);

      const streamingThinking = new Map(state.streamingThinking);
      streamingThinking.delete(conversationId);
      const agentStepHint = new Map(state.agentStepHint);
      agentStepHint.delete(conversationId);
      const suggestions = new Map(state.suggestions);
      suggestions.delete(conversationId);

      return { streamingMessages, optimisticMessages, streamingThinking, agentStepHint, suggestions, isSending: false };
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

      const streamingMessages = new Map(state.streamingMessages);
      const streamingId = streamingMessages.get(conversationId);
      const filteredList = list.filter((m) => m.id !== streamingId);
      optimisticMessages.set(conversationId, [...filteredList, normalizeMessage(errorMessage)]);

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

  getOptimisticMessages: (conversationId) => {
    return get().optimisticMessages.get(conversationId) || [];
  },

  // ========================================
  // 思考内容流式状态
  // ========================================

  appendStreamingThinking: (conversationId, chunk) => {
    set((state) => {
      const streamingThinking = new Map(state.streamingThinking);
      const prev = streamingThinking.get(conversationId) || '';
      streamingThinking.set(conversationId, prev + chunk);
      return { streamingThinking };
    });
  },

  getStreamingThinking: (conversationId) => {
    return get().streamingThinking.get(conversationId) || '';
  },

  // ========================================
  // 发送状态
  // ========================================

  // ========================================
  // Agent Loop 步骤提示
  // ========================================

  setAgentStepHint: (conversationId, hint) => {
    set((state) => {
      const agentStepHint = new Map(state.agentStepHint);
      agentStepHint.set(conversationId, hint);
      return { agentStepHint };
    });
  },

  clearAgentStepHint: (conversationId) => {
    set((state) => {
      const agentStepHint = new Map(state.agentStepHint);
      agentStepHint.delete(conversationId);
      return { agentStepHint };
    });
  },

  // ========================================
  // 建议问题
  // ========================================

  setSuggestions: (conversationId, suggestions) => {
    set((state) => {
      const newSuggestions = new Map(state.suggestions);
      newSuggestions.set(conversationId, suggestions);
      return { suggestions: newSuggestions };
    });
  },

  clearSuggestions: (conversationId) => {
    set((state) => {
      const newSuggestions = new Map(state.suggestions);
      newSuggestions.delete(conversationId);
      return { suggestions: newSuggestions };
    });
  },

  setToolConfirmRequest: (request) => set({ toolConfirmRequest: request }),

  setIsSending: (sending) => set({ isSending: sending }),
});
