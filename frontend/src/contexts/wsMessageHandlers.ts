/**
 * WebSocket 消息处理器工厂
 *
 * 从 WebSocketContext.tsx 提取的纯函数逻辑，包括：
 * - 10 种 WS 消息类型的处理器
 * - chunk 缓冲 flush 机制
 * - 任务完成/失败辅助函数
 */

import { normalizeMessage, type Message } from '../stores/useMessageStore';
import { useAuthStore } from '../stores/useAuthStore';
import { logger } from '../utils/logger';
import { tabSync } from '../utils/tabSync';
import type { OperationContext } from './WebSocketContext';

// ============================================================
// 类型定义
// ============================================================

import type { MessageStatus } from '../types/message';
import type { WSMessage } from '../hooks/useWebSocket';
import { getAgentStepText, getToolCallText, getPlaceholderText } from '../constants/placeholder';

/**
 * WS 消息扩展类型 — 后端各消息类型可能携带的额外字段
 * 仅处理器内部使用，外部统一使用 WSMessage
 */
interface WSIncomingMessage extends WSMessage {
  message_id?: string;
  message?: unknown;
  chunk?: string;
  accumulated?: string;
  error?: { code?: string; message?: string };
  credits?: number;
  progress?: number;
  data?: Record<string, unknown>;
}

/** normalizeMessage 参数类型（避免导出内部 RawApiMessage） */
type NormalizeInput = Parameters<typeof normalizeMessage>[0];

/** MessageStore 需要的方法子集（避免导入完整 Store 类型） */
export interface MessageStoreActions {
  setStatus: (messageId: string, status: MessageStatus) => void;
  appendStreamingContent: (conversationId: string, chunk: string) => void;
  appendContent: (messageId: string, chunk: string) => void;
  updateTaskProgress: (taskId: string, progress: number) => void;
  updateMessage: (messageId: string, data: Partial<Message>) => void;
  addMessage: (conversationId: string, message: Message) => void;
  completeTask: (taskId: string) => void;
  failTask: (taskId: string, error: string) => void;
  completeStreaming: (conversationId: string) => void;
  completeStreamingWithMessage: (conversationId: string, message: Message) => void;
  markConversationCompleted: (conversationId: string) => void;
  setIsSending: (isSending: boolean) => void;
  getMessage: (messageId: string) => Message | undefined;
  setStreamingContent: (conversationId: string, content: string) => void;
  setAgentStepHint: (conversationId: string, hint: string) => void;
  clearAgentStepHint: (conversationId: string) => void;
  appendStreamingThinking: (conversationId: string, chunk: string) => void;
  appendContentBlock: (conversationId: string, block: Record<string, unknown>) => void;
  markForceRefresh: (conversationId: string) => void;
  setSuggestions: (conversationId: string, suggestions: string[]) => void;
}

/** handler 工厂的依赖 */
export interface HandlerDeps {
  getStore: () => MessageStoreActions;
  subscribedTasksRef: React.RefObject<Set<string>>;
  taskConversationMapRef: React.RefObject<Map<string, string>>;
  operationContextRef: React.RefObject<Map<string, OperationContext>>;
  chunkBufferRef: React.RefObject<Map<string, { chunk: string; conversationId: string }>>;
  flushTimerRef: React.RefObject<ReturnType<typeof setTimeout> | null>;
  unsubscribeTask: (taskId: string) => void;
}

// ============================================================
// 辅助函数
// ============================================================

/** 清理任务订阅 */
function cleanupTaskSubscription(deps: HandlerDeps, taskId: string): void {
  deps.subscribedTasksRef.current.delete(taskId);
  deps.taskConversationMapRef.current.delete(taskId);
  deps.unsubscribeTask(taskId);
}

/** 处理任务完成（有 messageData），返回是否为新处理的消息 */
function handleTaskDoneWithMessage(deps: HandlerDeps, taskId: string, messageData: Record<string, unknown>, conversationId: string): boolean {
  const store = deps.getStore();
  const normalized = normalizeMessage(messageData as NormalizeInput);

  // 幂等性检查：使用 Store 作为唯一真相来源
  const existingMessage = store.getMessage(normalized.id);
  if (existingMessage?.status === 'completed') {
    logger.warn('ws:done', 'message already completed in store, skipping', {
      taskId,
      messageId: normalized.id,
    });
    return false;
  }

  logger.info('ws:done', 'processing message', {
    taskId,
    conversationId,
    messageId: normalized.id,
  });

  const updateData = {
    ...normalized,
    status: 'completed' as const,
  };

  // 统一更新逻辑：updateMessage 自动处理 messages 和 optimisticMessages
  store.updateMessage(normalized.id, updateData);

  // 持久化到 messages（确保切换对话再切回来时不丢失）
  store.addMessage(conversationId, updateData);

  // 清理任务状态
  store.completeTask(taskId);

  // 触发操作上下文回调
  const context = deps.operationContextRef.current.get(taskId);
  context?.onComplete?.(normalized);
  deps.operationContextRef.current.delete(taskId);

  return true;
}

/** 处理任务失败 */
function handleTaskFailure(deps: HandlerDeps, taskId: string, error: { message?: string } | undefined): void {
  const store = deps.getStore();
  const errorMessage = error?.message || '生成失败';
  store.failTask(taskId, errorMessage);

  // 触发操作上下文回调
  const context = deps.operationContextRef.current.get(taskId);
  context?.onError?.(new Error(errorMessage));
  deps.operationContextRef.current.delete(taskId);

  cleanupTaskSubscription(deps, taskId);
}

/** 将缓冲的 chunk 批量刷新到 store */
export function flushChunkBuffer(deps: HandlerDeps): void {
  const buffer = deps.chunkBufferRef.current;
  if (buffer.size === 0) return;

  const store = deps.getStore();
  buffer.forEach((bufferData, messageId) => {
    const { conversationId } = bufferData;
    if (conversationId) {
      // 定向更新（跳过 getMessage 全局查找）
      store.appendStreamingContent(conversationId, bufferData.chunk);
    } else {
      // fallback: 旧路径（理论上不应该走到这里）
      store.appendContent(messageId, bufferData.chunk);
    }
  });
  buffer.clear();
  deps.flushTimerRef.current = null;
}

// ============================================================
// 独立 handler 函数（从工厂提取，降低单函数复杂度）
// ============================================================

/** 处理生成完成消息 */
function handleMessageDone(deps: HandlerDeps, msg: WSIncomingMessage): void {
  const { task_id, message_id, conversation_id } = msg;
  const messageData = (msg.message ?? msg.payload?.message) as Record<string, unknown> | undefined;

  // L1: 完成前立即 flush 缓冲的 chunk
  if (deps.chunkBufferRef.current.size > 0) {
    if (deps.flushTimerRef.current) {
      clearTimeout(deps.flushTimerRef.current);
      deps.flushTimerRef.current = null;
    }
    flushChunkBuffer(deps);
  }

  logger.info('ws:message', 'done received', {
    taskId: task_id,
    messageId: message_id || messageData?.id,
    conversationId: conversation_id,
  });

  const store = deps.getStore();

  // conversation_id 兜底：从 taskConversationMap 查找（后端可能不发送该字段）
  const effectiveConversationId = conversation_id
    || (task_id ? deps.taskConversationMapRef.current.get(task_id) : undefined);

  // 跟踪是否为新处理的消息（控制 toast 显示）
  let isNewlyCompleted = true;

  // 1. 有 task_id：处理任务完成
  if (task_id) {
    if (messageData && effectiveConversationId) {
      isNewlyCompleted = handleTaskDoneWithMessage(deps, task_id, messageData, effectiveConversationId);
    } else if (message_id) {
      store.setStatus(message_id, 'completed');
      store.completeTask(task_id);
    }
    cleanupTaskSubscription(deps, task_id);
  }
  // 2. 无 task_id 但有 messageData
  else if (messageData) {
    const normalized = normalizeMessage(messageData as NormalizeInput);
    store.updateMessage(message_id || (messageData.id as string), { ...normalized, status: 'completed' });
  }
  // 3. 只有 message_id
  else if (message_id) {
    store.setStatus(message_id, 'completed');
  }

  // 完成流式状态（仅在新完成时更新，避免重复触发）
  if (effectiveConversationId && isNewlyCompleted) {
    store.completeStreaming(effectiveConversationId);
    store.markConversationCompleted(effectiveConversationId);
    store.setIsSending(false);
    tabSync.broadcast('message_completed', { conversationId: effectiveConversationId, messageId: message_id });
  }

  // Toast 提示（仅在消息确实是新完成时才显示）
  if (isNewlyCompleted) {
    import('react-hot-toast').then(({ default: toast }) => {
      toast.success('生成完成');
    });
  }
}

/** 处理生成失败消息 */
function handleMessageError(deps: HandlerDeps, msg: WSIncomingMessage): void {
  const { task_id, message_id, conversation_id } = msg;
  const error = (msg.error ?? msg.payload?.error) as { code?: string; message?: string } | undefined;

  // L1: 错误时丢弃缓冲（避免 flush 到已失败的消息）
  if (message_id) {
    deps.chunkBufferRef.current.delete(message_id);
  }
  if (deps.flushTimerRef.current && deps.chunkBufferRef.current.size === 0) {
    clearTimeout(deps.flushTimerRef.current);
    deps.flushTimerRef.current = null;
  }

  logger.error('ws:message', 'error received', undefined, {
    taskId: task_id,
    messageId: message_id,
    error,
  });

  const store = deps.getStore();

  // 更新消息状态（同步 content，与后端 on_error 保存的一致）
  if (message_id) {
    const errorText = error?.message || '生成失败';
    store.updateMessage(message_id, {
      status: 'failed',
      is_error: true,
      error: { code: error?.code ?? 'UNKNOWN', message: error?.message ?? errorText },
      content: [{ type: 'text', text: errorText }],
    });
  }

  // 处理任务失败
  if (task_id) {
    handleTaskFailure(deps, task_id, error);
  }

  // 完成流式状态
  if (conversation_id) {
    store.completeStreaming(conversation_id);
  }

  // 设置发送状态
  store.setIsSending(false);

  // Toast 提示
  import('react-hot-toast').then(({ default: toast }) => {
    toast.error(error?.message || '生成失败');
  });
}

/** 处理多图批次单张图片完成/失败通知 */
function handleImagePartialUpdate(deps: HandlerDeps, msg: WSIncomingMessage): void {
  const { message_id } = msg;
  const payload = (msg.payload || {}) as {
    image_index?: number;
    content_part?: Message['content'][number];
    completed_count?: number;
    total_count?: number;
    error?: string;
  };
  const { image_index, content_part, completed_count, total_count, error } = payload;

  if (!message_id || image_index === undefined) return;

  logger.info('ws:image', 'partial update', {
    messageId: message_id,
    imageIndex: image_index,
    progress: `${completed_count}/${total_count}`,
    hasError: !!error,
  });

  const store = deps.getStore();
  const existing = store.getMessage(message_id);
  if (!existing) return;

  // 克隆 content 数组，在对应 index 插入/替换
  const content = [...(existing.content || [])];

  // 确保数组长度足够
  while (content.length <= image_index) {
    content.push({ type: 'image', url: null } as unknown as Message['content'][number]);
  }

  if (error) {
    content[image_index] = { type: 'image', url: null, failed: true, error } as unknown as Message['content'][number];
  } else if (content_part) {
    content[image_index] = content_part;
  }

  store.updateMessage(message_id, { content });
}

// ============================================================
// Handler 工厂
// ============================================================

export function createWSMessageHandlers(deps: HandlerDeps): Record<string, (msg: WSMessage) => void> {
  // 内部使用 WSIncomingMessage 访问后端可能发送的额外字段
  const handlers: Record<string, (msg: WSIncomingMessage) => void> = {
    message_start: (msg) => {
      const { message_id } = msg;
      if (!message_id) return;

      logger.info('ws:message', 'start received', { messageId: message_id });
      deps.getStore().setStatus(message_id, 'streaming');
    },

    message_chunk: (msg) => {
      const { message_id, task_id, conversation_id } = msg;
      const chunk = msg.chunk || (msg.payload?.chunk as string | undefined);
      if (!message_id || !chunk || !conversation_id) return;

      const bufferData = deps.chunkBufferRef.current.get(message_id);
      const prevChunk = bufferData?.chunk || '';
      const accumulated = prevChunk + chunk;

      deps.chunkBufferRef.current.set(message_id, {
        chunk: accumulated,
        conversationId: conversation_id,
      });

      if (task_id) {
        const context = deps.operationContextRef.current.get(task_id);
        if (context?.onStreamChunk) {
          context.onStreamChunk(chunk, accumulated);
        }
      }

      // 首字节立即渲染，后续 chunk 用 16ms（约1帧）批量窗口
      const isFirstChunk = !bufferData;
      if (isFirstChunk) {
        if (deps.flushTimerRef.current) {
          clearTimeout(deps.flushTimerRef.current);
          deps.flushTimerRef.current = null;
        }
        flushChunkBuffer(deps);
        // flush 后 buffer 已清空，重新标记该消息（防止后续 chunk 被当成首字节）
        deps.chunkBufferRef.current.set(message_id, { chunk: '', conversationId: conversation_id });
      } else if (!deps.flushTimerRef.current) {
        deps.flushTimerRef.current = setTimeout(() => flushChunkBuffer(deps), 16);
      }
    },

    message_progress: (msg) => {
      const { task_id } = msg;
      const progress = msg.progress ?? (msg.payload?.progress as number | undefined);
      if (!task_id || progress === undefined) return;

      logger.debug('ws:message', 'progress update', { taskId: task_id, progress });
      deps.getStore().updateTaskProgress(task_id, progress);
    },

    message_done: (msg) => handleMessageDone(deps, msg),

    message_error: (msg) => handleMessageError(deps, msg),

    image_partial_update: (msg) => handleImagePartialUpdate(deps, msg),

    credits_changed: (msg) => {
      const credits = msg.credits ?? (msg.payload?.credits as number | undefined);
      if (credits === undefined) return;

      logger.info('ws:credits', 'credits changed', { credits });

      const currentUser = useAuthStore.getState().user;
      if (currentUser) {
        useAuthStore.getState().setUser({ ...currentUser, credits });
      }
    },

    subscribed: (msg) => {
      const { task_id, accumulated } = (msg.payload || {}) as { task_id?: string; accumulated?: string };

      logger.info('ws:subscribe', 'confirmed', {
        taskId: task_id,
        accumulatedLen: accumulated?.length ?? 0,
      });

      if (accumulated && accumulated.length > 0 && task_id) {
        const conversationId = deps.taskConversationMapRef.current.get(task_id);
        if (conversationId) {
          deps.getStore().setStreamingContent(conversationId, accumulated);
        }
      }
    },

    memory_extracted: (msg) => {
      const data = (msg.data ?? msg.payload) as { memories?: unknown[]; count?: number };
      if (!data?.memories) return;

      logger.info('ws:memory', 'memories extracted', { count: data.count });

      import('../stores/useMemoryStore').then(({ useMemoryStore }) => {
        useMemoryStore.getState().onMemoryExtracted(data.memories! as Array<{ id: string; memory: string }>);
      });
    },

    thinking_chunk: (msg) => {
      const { conversation_id } = msg;
      const chunk = msg.chunk || (msg.payload?.chunk as string | undefined);
      if (!conversation_id || !chunk) return;

      deps.getStore().appendStreamingThinking(conversation_id, chunk);
    },

    agent_step: (msg) => {
      const { conversation_id } = msg;
      const toolName = msg.payload?.tool_name as string | undefined;
      if (!conversation_id || !toolName) return;

      const hint = getAgentStepText(toolName);
      deps.getStore().setAgentStepHint(conversation_id, hint);
    },

    routing_complete: (msg) => {
      const { conversation_id, message_id } = msg;
      const genType = msg.payload?.generation_type as string | undefined;
      const model = msg.payload?.model as string | undefined;
      const genParams = msg.payload?.generation_params as Record<string, unknown> | undefined;
      if (!conversation_id || !genType || !message_id) return;

      const store = deps.getStore();

      if (genType === 'image' || genType === 'video' || genType === 'audio') {
        // 占位符变形：旋转圆点 → 媒体生成占位符
        const render = genParams?._render as Record<string, string> | undefined;
        const loadingText = render?.placeholder_text
          || getPlaceholderText(genType as 'image' | 'video' | 'audio');

        store.completeStreamingWithMessage(conversation_id, {
          id: message_id,
          conversation_id,
          role: 'assistant',
          content: [{ type: 'text', text: loadingText }],
          status: 'pending',
          created_at: new Date().toISOString(),
          generation_params: genParams ?? { model },
          task_id: msg.task_id,
        });
        store.setIsSending(true);
      } else {
        // chat 类型：更新 generation_params（路由确定的模型信息）
        store.updateMessage(message_id, {
          generation_params: genParams ?? { model },
        });
      }
    },

    conversation_updated: (msg) => {
      const { conversation_id } = msg;
      if (!conversation_id) return;

      logger.info('ws:conversation', 'conversation updated (wecom)', { conversationId: conversation_id });

      // 通知 ConversationList 刷新列表
      if (typeof window !== 'undefined') {
        window.dispatchEvent(
          new CustomEvent('conversation-list-refresh', {
            detail: { conversationId: conversation_id },
          }),
        );
      }

      // 标记该对话消息需要强制刷新（用户切入时重新加载）
      const store = deps.getStore();
      store.markForceRefresh(conversation_id);
    },

    tool_call: (msg) => {
      const { conversation_id } = msg;
      const toolCalls = msg.payload?.tool_calls as Array<{ name: string }> | undefined;
      const turn = msg.payload?.turn as number | undefined;
      if (!conversation_id || !toolCalls?.length) return;

      // 取第一个工具名展示提示（多工具时显示第一个）
      const hint = getToolCallText(toolCalls[0].name);
      const suffix = toolCalls.length > 1 ? ` 等${toolCalls.length}个工具` : '';
      deps.getStore().setAgentStepHint(conversation_id, `${hint}${suffix}`);

      logger.info('ws:tool', 'tool_call', { conversationId: conversation_id, tools: toolCalls.map(t => t.name), turn });
    },

    tool_result: (msg) => {
      const { conversation_id } = msg;
      const toolName = msg.payload?.tool_name as string | undefined;
      const success = msg.payload?.success as boolean | undefined;
      if (!conversation_id) return;

      // 工具完成后清除提示（下一轮 stream 开始时会自动更新）
      deps.getStore().clearAgentStepHint(conversation_id);

      logger.info('ws:tool', 'tool_result', { conversationId: conversation_id, tool: toolName, success });
    },

    content_block_add: (msg) => {
      const { conversation_id } = msg;
      const block = msg.payload?.block as Record<string, unknown> | undefined;
      if (!conversation_id || !block) return;

      deps.getStore().appendContentBlock(conversation_id, block);
      logger.info('ws:content', 'content_block_add', { conversationId: conversation_id, type: block.type });
    },

    suggestions_ready: (msg) => {
      const { conversation_id } = msg;
      const suggestions = msg.payload?.suggestions as string[] | undefined;
      if (!conversation_id || !suggestions?.length) return;

      deps.getStore().setSuggestions(conversation_id, suggestions);
      logger.info('ws:suggestions', 'suggestions_ready', { conversationId: conversation_id, count: suggestions.length });
    },

    tool_confirm_request: (msg) => {
      const { conversation_id, task_id } = msg;
      const toolName = msg.payload?.tool_name as string | undefined;
      const description = msg.payload?.description as string | undefined;
      if (!conversation_id || !toolName) return;

      // 显示确认提示（当前阶段仅显示提示，Phase 3 实现确认 UI）
      deps.getStore().setAgentStepHint(conversation_id, `⚠ ${description || toolName} — 等待确认`);

      logger.info('ws:tool', 'confirm_request', { conversationId: conversation_id, tool: toolName, taskId: task_id });
    },

    error: (msg) => {
      const message = msg.message ?? msg.payload?.message;
      logger.error('ws:error', 'error received', undefined, { error: message });
    },

    // ── 定时任务事件 ──
    scheduled_task_started: (msg) => {
      const data = (msg.data || msg.payload) as { task_id?: string; task_name?: string };
      if (!data?.task_id) return;
      logger.info('ws:scheduled-task', 'started', data);
      // 异步导入，避免循环依赖
      import('../stores/useScheduledTaskStore').then(({ useScheduledTaskStore }) => {
        useScheduledTaskStore.getState().optimisticUpdate(data.task_id!, {
          status: 'running',
        });
      });
    },

    scheduled_task_completed: (msg) => {
      const data = (msg.data || msg.payload) as {
        task_id?: string;
        task_name?: string;
        next_run_at?: string;
        summary?: string;
        push_status?: string;
      };
      if (!data?.task_id) return;
      logger.info('ws:scheduled-task', 'completed', data);
      import('../stores/useScheduledTaskStore').then(({ useScheduledTaskStore }) => {
        useScheduledTaskStore.getState().optimisticUpdate(data.task_id!, {
          status: 'active',
          last_run_at: new Date().toISOString(),
          last_summary: data.summary || null,
          next_run_at: data.next_run_at || null,
        });
        // 重新拉取执行历史
        useScheduledTaskStore.getState().fetchRuns(data.task_id!);
      });
    },

    scheduled_task_failed: (msg) => {
      const data = (msg.data || msg.payload) as {
        task_id?: string;
        task_name?: string;
        status?: string;
        error?: string;
        consecutive_failures?: number;
        will_retry?: boolean;
      };
      if (!data?.task_id) return;
      logger.warn('ws:scheduled-task', 'failed', data);
      import('../stores/useScheduledTaskStore').then(({ useScheduledTaskStore }) => {
        useScheduledTaskStore.getState().optimisticUpdate(data.task_id!, {
          status: (data.status as any) || 'error',
          consecutive_failures: data.consecutive_failures || 0,
        });
        useScheduledTaskStore.getState().fetchRuns(data.task_id!);
      });
    },

    scheduled_task_notification: (msg) => {
      const data = (msg.data || msg.payload) as {
        task_id?: string;
        task_name?: string;
        level?: string;
        message?: string;
      };
      if (!data?.message) return;
      logger.warn('ws:scheduled-task', 'notification', data);
      // 这里只记日志，实际 UI 提示由 toast 组件处理（如果有）
    },
  };
  // WSIncomingMessage extends WSMessage — 运行时对象包含所有字段，类型断言安全
  return handlers as Record<string, (msg: WSMessage) => void>;
}
