/**
 * 对话运行时状态管理
 *
 * 管理对话的临时、瞬时状态，支持：
 * - 乐观更新的用户消息（temp-xxx）
 * - 流式生成的AI消息（streaming-xxx）
 * - 生成状态管理
 * - LRU自动清理
 *
 * 职责边界：
 * - 本Store：只管理临时、未持久化的状态
 * - useChatStore：管理已持久化到数据库的消息
 */

import { create } from 'zustand';
import { type Message } from '../services/message';

/** 单个对话的运行时状态 */
export interface ConversationRuntimeState {
  // 乐观更新的消息列表（包含temp-用户消息 + streaming-AI消息）
  optimisticMessages: Message[];

  // 当前是否有任务在生成（用于UI状态：显示"AI正在思考"）
  isGenerating: boolean;

  // 流式AI消息的ID（用于快速定位和更新）
  streamingMessageId: string | null;
}

/** Store接口定义 */
interface ConversationRuntimeStore {
  // 状态存储：Map<conversationId, RuntimeState>
  states: Map<string, ConversationRuntimeState>;

  // ========================================
  // 1. 乐观消息管理
  // ========================================

  /** 添加乐观用户消息（用户发送消息时调用） */
  addOptimisticUserMessage: (conversationId: string, message: Message) => void;

  /** 替换临时消息为真实消息（后端返回真实消息时调用） */
  replaceOptimisticMessage: (conversationId: string, realMessage: Message) => void;

  /** 添加错误消息（用于显示错误） */
  addErrorMessage: (conversationId: string, errorMessage: Message) => void;

  /** 添加媒体占位符消息（图片/视频生成中） */
  addMediaPlaceholder: (conversationId: string, placeholder: Message) => void;

  /** 替换媒体占位符为真实消息 */
  replaceMediaPlaceholder: (conversationId: string, placeholderId: string, realMessage: Message) => void;

  /** 移除乐观消息（后端返回真实消息后调用） */
  removeOptimisticMessage: (conversationId: string, messageId: string) => void;

  // ========================================
  // 2. 流式消息管理
  // ========================================

  /** 开始流式生成：创建streaming-AI消息 */
  startStreaming: (conversationId: string, streamingId: string, createdAt?: string) => void;

  /** 追加流式内容：累积增量内容到streaming-消息 */
  appendStreamingContent: (conversationId: string, chunk: string) => void;

  /** 完成流式生成：移除streaming-消息，更新状态 */
  completeStreaming: (conversationId: string) => void;

  /** 完成流式生成并替换为真实消息（用于图片/视频生成） */
  completeStreamingWithMessage: (conversationId: string, message: Message) => void;

  // ========================================
  // 3. 状态管理
  // ========================================

  /** 设置生成状态 */
  setGenerating: (conversationId: string, isGenerating: boolean) => void;

  /** 获取对话的运行时状态（不存在则返回默认值） */
  getState: (conversationId: string) => ConversationRuntimeState;

  /** 清空对话的所有运行时状态 */
  clearState: (conversationId: string) => void;

  // ========================================
  // 4. LRU清理
  // ========================================

  /** 清理旧对话状态，只保留指定的对话ID列表 */
  cleanup: (keepConversationIds: string[]) => void;
}

/** 默认运行时状态工厂 */
const createDefaultState = (): ConversationRuntimeState => ({
  optimisticMessages: [],
  isGenerating: false,
  streamingMessageId: null,
});

/** 创建Store */
export const useConversationRuntimeStore = create<ConversationRuntimeStore>((set, get) => ({
  states: new Map(),

  // ========================================
  // 1. 乐观消息管理
  // ========================================

  addOptimisticUserMessage: (conversationId: string, message: Message) => {
    set((state) => {
      const current = state.states.get(conversationId) ?? createDefaultState();
      const newStates = new Map(state.states);

      newStates.set(conversationId, {
        ...current,
        optimisticMessages: [...current.optimisticMessages, message],
        isGenerating: true,  // 用户发送消息后，开始等待AI
      });

      return { states: newStates };
    });
  },

  replaceOptimisticMessage: (conversationId: string, realMessage: Message) => {
    set((state) => {
      const current = state.states.get(conversationId);
      if (!current) return state;

      // 查找并替换同样内容的temp-消息（匹配content和role）
      const replaced = current.optimisticMessages.map(m => {
        if (m.id.startsWith('temp-') &&
            m.role === realMessage.role &&
            m.content === realMessage.content) {
          return realMessage; // 使用后端的真实消息（包含真实时间戳）
        }
        return m;
      });

      // 如果没有找到匹配的temp-消息，直接添加（防御性编程）
      const hasReplaced = replaced.some(m => m.id === realMessage.id);
      let finalMessages = hasReplaced ? replaced : [...replaced, realMessage];

      // ✅ 如果是用户消息，且存在streaming消息，调整streaming消息的时间戳确保在用户消息之后
      if (hasReplaced && realMessage.role === 'user' && current.streamingMessageId) {
        const userTimestamp = new Date(realMessage.created_at).getTime();
        finalMessages = finalMessages.map(m => {
          if (m.id === current.streamingMessageId) {
            // 确保streaming消息时间戳晚于用户消息1ms
            return { ...m, created_at: new Date(userTimestamp + 1).toISOString() };
          }
          return m;
        });
      }

      const newStates = new Map(state.states);
      newStates.set(conversationId, {
        ...current,
        optimisticMessages: finalMessages,
      });

      return { states: newStates };
    });
  },

  addErrorMessage: (conversationId: string, errorMessage: Message) => {
    set((state) => {
      const current = state.states.get(conversationId) ?? createDefaultState();

      // 移除空的streaming消息（如果存在）
      const filteredMessages = current.optimisticMessages.filter(
        m => !(m.id.startsWith('streaming-') && !m.content.trim())
      );

      const newStates = new Map(state.states);
      newStates.set(conversationId, {
        ...current,
        optimisticMessages: [...filteredMessages, errorMessage],
        streamingMessageId: null,
        isGenerating: false,
      });

      return { states: newStates };
    });
  },

  removeOptimisticMessage: (conversationId: string, messageId: string) => {
    set((state) => {
      const current = state.states.get(conversationId);
      if (!current) return state;

      const newStates = new Map(state.states);
      newStates.set(conversationId, {
        ...current,
        optimisticMessages: current.optimisticMessages.filter(m => m.id !== messageId),
      });

      return { states: newStates };
    });
  },

  addMediaPlaceholder: (conversationId: string, placeholder: Message) => {
    set((state) => {
      const current = state.states.get(conversationId) ?? createDefaultState();
      const newStates = new Map(state.states);

      newStates.set(conversationId, {
        ...current,
        optimisticMessages: [...current.optimisticMessages, placeholder],
        isGenerating: true,
      });

      return { states: newStates };
    });
  },

  replaceMediaPlaceholder: (conversationId: string, placeholderId: string, realMessage: Message) => {
    set((state) => {
      const current = state.states.get(conversationId);
      if (!current) return state;

      // 查找并替换占位符消息
      const updatedMessages = current.optimisticMessages.map(m =>
        m.id === placeholderId ? realMessage : m
      );

      // 检查是否还有其他进行中的任务
      const hasOtherStreamingTasks = updatedMessages.some(
        m => m.id.startsWith('streaming-') && m.id !== realMessage.id
      );

      const newStates = new Map(state.states);
      newStates.set(conversationId, {
        ...current,
        optimisticMessages: updatedMessages,
        isGenerating: hasOtherStreamingTasks,
      });

      return { states: newStates };
    });
  },

  // ========================================
  // 2. 流式消息管理
  // ========================================

  startStreaming: (conversationId: string, streamingId: string, createdAt?: string) => {
    set((state) => {
      const current = state.states.get(conversationId) ?? createDefaultState();

      // 创建streaming-AI消息
      const streamingMessage: Message = {
        id: `streaming-${streamingId}`,
        conversation_id: conversationId,
        role: 'assistant',
        content: '',  // 初始为空，后续通过appendStreamingContent累积
        image_url: null,
        video_url: null,
        credits_cost: 0,
        created_at: createdAt || new Date().toISOString(),
      };

      const newStates = new Map(state.states);
      newStates.set(conversationId, {
        ...current,
        optimisticMessages: [...current.optimisticMessages, streamingMessage],
        streamingMessageId: streamingMessage.id,
        isGenerating: true,
      });

      return { states: newStates };
    });
  },

  appendStreamingContent: (conversationId: string, chunk: string) => {
    set((state) => {
      const current = state.states.get(conversationId);
      if (!current || !current.streamingMessageId) {
        console.warn(`[RuntimeStore] 尝试追加流式内容到不存在的对话: ${conversationId}`);
        return state;
      }

      // 找到streaming消息并累积内容
      const updatedMessages = current.optimisticMessages.map(m =>
        m.id === current.streamingMessageId
          ? { ...m, content: m.content + chunk }  // ✅ 累积增量内容
          : m
      );

      const newStates = new Map(state.states);
      newStates.set(conversationId, {
        ...current,
        optimisticMessages: updatedMessages,
      });

      return { states: newStates };
    });
  },

  completeStreaming: (conversationId: string) => {
    set((state) => {
      const current = state.states.get(conversationId);
      if (!current) return state;

      // ✅ 不立即移除streaming消息，保留等待后端持久化消息到达
      // streaming消息会在MessageArea的deduplication中被移除（当持久化消息到达时）
      const newStates = new Map(state.states);
      newStates.set(conversationId, {
        ...current,
        streamingMessageId: null,  // 清除streamingMessageId（停止追加内容）
        isGenerating: false,        // 标记生成完成
        // optimisticMessages 保持不变，streaming消息保留
      });

      return { states: newStates };
    });
  },

  completeStreamingWithMessage: (conversationId: string, message: Message) => {
    set((state) => {
      const current = state.states.get(conversationId);
      if (!current) return state;

      // 移除streaming消息，替换为真实AI消息
      const filteredMessages = current.optimisticMessages.filter(
        m => !m.id.startsWith('streaming-')
      );

      const newStates = new Map(state.states);
      newStates.set(conversationId, {
        ...current,
        optimisticMessages: [...filteredMessages, message],
        streamingMessageId: null,
        isGenerating: false,
      });

      return { states: newStates };
    });
  },

  // ========================================
  // 3. 状态管理
  // ========================================

  setGenerating: (conversationId: string, isGenerating: boolean) => {
    set((state) => {
      const current = state.states.get(conversationId) ?? createDefaultState();
      const newStates = new Map(state.states);

      newStates.set(conversationId, {
        ...current,
        isGenerating,
      });

      return { states: newStates };
    });
  },

  getState: (conversationId: string) => {
    return get().states.get(conversationId) ?? createDefaultState();
  },

  clearState: (conversationId: string) => {
    set((state) => {
      const newStates = new Map(state.states);
      newStates.delete(conversationId);
      return { states: newStates };
    });
  },

  // ========================================
  // 4. LRU清理
  // ========================================

  cleanup: (keepConversationIds: string[]) => {
    set((state) => {
      const keepSet = new Set(keepConversationIds);
      const newStates = new Map<string, ConversationRuntimeState>();

      // 只保留指定的对话ID
      for (const [id, runtimeState] of state.states.entries()) {
        if (keepSet.has(id)) {
          newStates.set(id, runtimeState);
        }
      }

      return { states: newStates };
    });
  },
}));
