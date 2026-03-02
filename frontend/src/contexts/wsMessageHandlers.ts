/**
 * WebSocket 消息处理器工厂
 *
 * 从 WebSocketContext.tsx 提取的纯函数逻辑，包括：
 * - 8 种 WS 消息类型的处理器
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
  markConversationCompleted: (conversationId: string) => void;
  setIsSending: (isSending: boolean) => void;
  getMessage: (messageId: string) => Message | undefined;
  setStreamingContent: (conversationId: string, content: string) => void;
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
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function handleTaskDoneWithMessage(deps: HandlerDeps, taskId: string, messageData: any, conversationId: string): boolean {
  const store = deps.getStore();
  const normalized = normalizeMessage(messageData);

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
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function handleTaskFailure(deps: HandlerDeps, taskId: string, error: any): void {
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
// Handler 工厂
// ============================================================

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function createWSMessageHandlers(deps: HandlerDeps): Record<string, (msg: any) => void> {
  return {
    // 消息开始（流式）
    message_start: (msg) => {
      const { message_id } = msg;
      if (!message_id) return;

      logger.info('ws:message', 'start received', { messageId: message_id });
      deps.getStore().setStatus(message_id, 'streaming');
    },

    // 流式内容块
    message_chunk: (msg) => {
      const { message_id, task_id, conversation_id } = msg;
      const chunk = msg.chunk || msg.payload?.chunk;
      if (!message_id || !chunk || !conversation_id) return;

      // L1: 累积到 buffer（不触发渲染）
      const bufferData = deps.chunkBufferRef.current.get(message_id);
      const prevChunk = bufferData?.chunk || '';
      const accumulated = prevChunk + chunk;

      deps.chunkBufferRef.current.set(message_id, {
        chunk: accumulated,
        conversationId: conversation_id,
      });

      // 流式回调仍然立即触发（用于外部消费者）
      if (task_id) {
        const context = deps.operationContextRef.current.get(task_id);
        if (context?.onStreamChunk) {
          context.onStreamChunk(chunk, accumulated);
        }
      }

      // L1: 50ms 防抖 flush
      if (!deps.flushTimerRef.current) {
        deps.flushTimerRef.current = setTimeout(() => flushChunkBuffer(deps), 50);
      }
    },

    // 进度更新
    message_progress: (msg) => {
      const { task_id } = msg;
      const progress = msg.progress ?? msg.payload?.progress;
      if (!task_id || progress === undefined) return;

      logger.debug('ws:message', 'progress update', { taskId: task_id, progress });
      deps.getStore().updateTaskProgress(task_id, progress);
    },

    // 生成完成
    message_done: (msg) => {
      const { task_id, message_id, conversation_id } = msg;
      const messageData = msg.message || msg.payload?.message;

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
        const normalized = normalizeMessage(messageData);
        store.updateMessage(message_id || messageData.id, { ...normalized, status: 'completed' });
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
    },

    // 生成失败
    message_error: (msg) => {
      const { task_id, message_id, conversation_id } = msg;
      const error = msg.error || msg.payload?.error;

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
          error: error || { code: 'UNKNOWN', message: errorText },
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
    },

    // 积分变更
    credits_changed: (msg) => {
      const credits = msg.credits ?? msg.payload?.credits;
      if (credits === undefined) return;

      logger.info('ws:credits', 'credits changed', { credits });

      const currentUser = useAuthStore.getState().user;
      if (currentUser) {
        useAuthStore.getState().setUser({ ...currentUser, credits });
      }
    },

    // 订阅确认
    subscribed: (msg) => {
      const { task_id, accumulated } = msg.payload || {};

      logger.info('ws:subscribe', 'confirmed', {
        taskId: task_id,
        accumulatedLen: accumulated?.length ?? 0,
      });

      // 用最新 accumulated 替换占位符内容（补全 Phase 1→2 间的差异）
      if (accumulated && accumulated.length > 0 && task_id) {
        const conversationId = deps.taskConversationMapRef.current.get(task_id);
        if (conversationId) {
          deps.getStore().setStreamingContent(conversationId, accumulated);
        }
      }
    },

    // 通用错误
    error: (msg) => {
      const message = msg.message ?? msg.payload?.message;
      logger.error('ws:error', 'error received', undefined, { error: message });
    },
  };
}
