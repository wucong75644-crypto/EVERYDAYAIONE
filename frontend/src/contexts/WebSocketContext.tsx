/* eslint-disable react-refresh/only-export-components */
/**
 * WebSocket Context（统一版本）
 *
 * 简化设计：
 * 1. 统一消息处理：message_start/chunk/progress/done/error
 * 2. 使用 useMessageStore 统一状态管理
 * 3. 任务恢复走统一入口
 */

import { createContext, useContext, useEffect, useRef, useCallback, type ReactNode } from 'react';
import { useWebSocket, type WSMessageType } from '../hooks/useWebSocket';
import { useMessageStore, normalizeMessage, type Message, type ContentPart } from '../stores/useMessageStore';
import { useAuthStore } from '../stores/useAuthStore';
import { useTaskRestorationStore } from '../stores/useTaskRestorationStore';
import { initializeTaskRestoration } from '../utils/taskRestoration';
import { logger } from '../utils/logger';
import { tabSync } from '../utils/tabSync';

// ============================================================
// 类型定义
// ============================================================

/** 操作上下文（供完成回调使用） */
export interface OperationContext {
  type: 'chat' | 'image' | 'video' | 'audio';
  operation: 'send' | 'regenerate' | 'retry';
  conversationId: string;
  onComplete?: (message: Message) => void;
  onStreamChunk?: (chunk: string, accumulated: string) => void;
  onError?: (error: Error) => void;
}

/** Context 值类型 */
export interface WebSocketContextValue {
  isConnected: boolean;
  isConnecting: boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  subscribe: (type: WSMessageType, handler: (msg: any) => void) => () => void;
  subscribeTask: (taskId: string) => void;
  unsubscribeTask: (taskId: string) => void;
  subscribeTaskWithMapping: (taskId: string, conversationId: string) => void;
  registerOperation: (taskId: string, context: OperationContext) => void;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

// ============================================================
// Provider 组件
// ============================================================

interface WebSocketProviderProps {
  children: ReactNode;
}

export function WebSocketProvider({ children }: WebSocketProviderProps) {
  const ws = useWebSocket();
  const messageStore = useMessageStore();

  // 已订阅任务（防止重复）
  const subscribedTasksRef = useRef<Set<string>>(new Set());

  // 任务 → 对话映射
  const taskConversationMapRef = useRef<Map<string, string>>(new Map());

  // 操作上下文映射
  const operationContextRef = useRef<Map<string, OperationContext>>(new Map());

  // ws ref（避免回调重建）
  const wsRef = useRef(ws);
  wsRef.current = ws;

  // ========================================
  // 统一消息处理
  // ========================================
  useEffect(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const handlers: Record<string, (msg: any) => void> = {

      // 消息开始（流式）
      message_start: (msg) => {
        const { message_id } = msg;
        if (!message_id) return;

        logger.info('ws:message', 'start received', { messageId: message_id });
        messageStore.setStatus(message_id, 'streaming');
      },

      // 流式内容块
      message_chunk: (msg) => {
        const { message_id, chunk } = msg;
        if (!message_id || !chunk) return;

        messageStore.appendContent(message_id, chunk);

        // 触发流式回调
        const taskId = msg.task_id;
        if (taskId) {
          const context = operationContextRef.current.get(taskId);
          if (context?.onStreamChunk) {
            const message = messageStore.getMessage(message_id);
            const accumulated = message?.content?.find(p => p.type === 'text')?.text || '';
            context.onStreamChunk(chunk, accumulated as string);
          }
        }
      },

      // 进度更新
      message_progress: (msg) => {
        const { task_id, progress } = msg;
        if (!task_id || progress === undefined) return;

        logger.debug('ws:message', 'progress update', { taskId: task_id, progress });
        messageStore.updateTaskProgress(task_id, progress);
      },

      // 生成完成
      message_done: (msg) => {
        const { task_id, message, message_id, conversation_id } = msg;

        logger.info('ws:message', 'done received', {
          taskId: task_id,
          messageId: message_id || message?.id,
          conversationId: conversation_id,
        });

        // 用后端返回的完整消息更新
        if (message) {
          const normalized = normalizeMessage(message);
          messageStore.updateMessage(message_id || message.id, {
            ...normalized,
            status: 'completed',
          });
        } else if (message_id) {
          messageStore.setStatus(message_id, 'completed');
        }

        // 完成任务
        if (task_id) {
          messageStore.completeTask(task_id);

          // 触发操作上下文回调
          const context = operationContextRef.current.get(task_id);
          if (context?.onComplete && message) {
            context.onComplete(normalizeMessage(message));
          }
          operationContextRef.current.delete(task_id);

          // 清理订阅
          subscribedTasksRef.current.delete(task_id);
          taskConversationMapRef.current.delete(task_id);
          ws.unsubscribeTask(task_id);
        }

        // 完成流式状态
        if (conversation_id) {
          messageStore.completeStreaming(conversation_id);
          tabSync.broadcast('message_completed', { conversationId: conversation_id, messageId: message_id });
        }

        // Toast 提示
        import('react-hot-toast').then(({ default: toast }) => {
          toast.success('生成完成');
        });
      },

      // 生成失败
      message_error: (msg) => {
        const { task_id, message_id, error, conversation_id } = msg;

        logger.error('ws:message', 'error received', undefined, {
          taskId: task_id,
          messageId: message_id,
          error,
        });

        // 更新消息状态
        if (message_id) {
          messageStore.updateMessage(message_id, {
            status: 'failed',
            error: error || { code: 'UNKNOWN', message: '生成失败' },
          });
        }

        // 失败任务
        if (task_id) {
          messageStore.failTask(task_id, error?.message || '生成失败');

          // 触发操作上下文回调
          const context = operationContextRef.current.get(task_id);
          if (context?.onError) {
            context.onError(new Error(error?.message || '生成失败'));
          }
          operationContextRef.current.delete(task_id);

          // 清理订阅
          subscribedTasksRef.current.delete(task_id);
          taskConversationMapRef.current.delete(task_id);
          ws.unsubscribeTask(task_id);
        }

        // 完成流式状态
        if (conversation_id) {
          messageStore.completeStreaming(conversation_id);
        }

        // Toast 提示
        import('react-hot-toast').then(({ default: toast }) => {
          toast.error(error?.message || '生成失败');
        });
      },

      // ========================================
      // 兼容旧消息类型（过渡期）
      // ========================================

      chat_start: (msg) => {
        const { assistant_message_id } = msg.payload || {};
        const conversation_id = msg.conversation_id;
        if (!assistant_message_id || !conversation_id) return;

        logger.info('ws:chat', 'start received (legacy)', { assistantMessageId: assistant_message_id });

        // 开始流式
        messageStore.startStreaming(conversation_id, assistant_message_id);
      },

      chat_chunk: (msg) => {
        const { text } = msg.payload || {};
        const messageId = messageStore.getStreamingMessageId(msg.conversation_id);

        if (!messageId || !text) return;

        messageStore.appendContent(messageId, text);
      },

      chat_done: (msg) => {
        const { message_id, content, credits_consumed, model } = msg.payload || {};
        const conversationId = msg.conversation_id;

        if (!conversationId || !message_id) return;

        logger.info('ws:chat', 'done received (legacy)', { messageId: message_id, credits: credits_consumed });

        // 构建完整消息
        const finalMessage: Message = {
          id: message_id,
          conversation_id: conversationId,
          role: 'assistant',
          content: [{ type: 'text', text: content || '' }],
          status: 'completed',
          credits_cost: credits_consumed,
          created_at: new Date().toISOString(),
          generation_params: model ? { model } : undefined,
        };

        // 更新消息
        const streamingId = messageStore.getStreamingMessageId(conversationId);
        if (streamingId) {
          messageStore.updateMessage(streamingId, finalMessage);
        } else {
          messageStore.addMessage(conversationId, finalMessage);
        }

        messageStore.completeStreaming(conversationId);

        // 触发回调
        const taskId = msg.task_id;
        if (taskId) {
          const context = operationContextRef.current.get(taskId);
          if (context?.onComplete) {
            context.onComplete(finalMessage);
          }
          operationContextRef.current.delete(taskId);
          subscribedTasksRef.current.delete(taskId);
          taskConversationMapRef.current.delete(taskId);
          ws.unsubscribeTask(taskId);
        }

        tabSync.broadcast('chat_completed', { conversationId, messageId: message_id });
      },

      chat_error: (msg) => {
        const { error } = msg.payload || {};
        const conversationId = msg.conversation_id;
        const taskId = msg.task_id;

        if (!conversationId) return;

        logger.error('ws:chat', 'error received (legacy)', undefined, { error });

        // 添加错误消息
        const streamingId = messageStore.getStreamingMessageId(conversationId);
        if (streamingId) {
          messageStore.updateMessage(streamingId, {
            status: 'failed',
            error: { code: 'CHAT_ERROR', message: error || '生成失败' },
          });
        }

        messageStore.completeStreaming(conversationId);

        if (taskId) {
          const context = operationContextRef.current.get(taskId);
          if (context?.onError) {
            context.onError(new Error(error || '生成失败'));
          }
          operationContextRef.current.delete(taskId);
          subscribedTasksRef.current.delete(taskId);
          ws.unsubscribeTask(taskId);
        }
      },

      task_status: async (msg) => {
        const { status, media_type, error_message, message: createdMessage } = msg.payload || {};
        const taskId = msg.task_id;
        const conversationId = msg.conversation_id;

        if (!taskId) return;

        logger.info('ws:task', 'status received (legacy)', {
          taskId,
          status,
          mediaType: media_type,
        });

        if (status === 'completed' && createdMessage && conversationId) {
          // 处理消息内容（支持新格式数组和旧格式单独字段）
          let content: ContentPart[];
          if (Array.isArray(createdMessage.content)) {
            // 新格式：content 已经是数组
            content = createdMessage.content;
          } else {
            // 旧格式：从单独字段转换
            content = [];
            if (typeof createdMessage.content === 'string' && createdMessage.content) {
              content.push({ type: 'text', text: createdMessage.content });
            }
            if (createdMessage.image_url) {
              content.push({ type: 'image', url: createdMessage.image_url });
            }
            if (createdMessage.video_url) {
              content.push({ type: 'video', url: createdMessage.video_url });
            }
          }

          const normalizedMessage: Message = {
            id: createdMessage.id,
            conversation_id: conversationId,
            role: 'assistant',
            content,
            status: 'completed',
            credits_cost: createdMessage.credits_cost,
            created_at: createdMessage.created_at,
          };

          // 查找并替换占位符
          // 优先查找 mediaTasks（图片/视频任务），fallback 到 tasks（统一任务）
          const mediaTask = messageStore.getMediaTask(taskId);
          const unifiedTask = messageStore.getTask(taskId);

          if (mediaTask?.placeholderId) {
            // 媒体任务：替换占位符
            messageStore.replaceMediaPlaceholder(conversationId, mediaTask.placeholderId, normalizedMessage);
            messageStore.completeMediaTask(taskId);
          } else if (unifiedTask?.messageId) {
            // 统一任务：更新消息
            messageStore.updateMessage(unifiedTask.messageId, normalizedMessage);
            messageStore.completeTask(taskId);
          } else {
            // 兜底：直接添加消息
            messageStore.addMessage(conversationId, normalizedMessage);
          }

          // 回调
          const context = operationContextRef.current.get(taskId);
          if (context?.onComplete) {
            context.onComplete(normalizedMessage);
          }
          operationContextRef.current.delete(taskId);

          const { default: toast } = await import('react-hot-toast');
          toast.success(`${media_type === 'image' ? '图片' : '视频'}生成完成`);

          tabSync.broadcast('message_updated', { conversationId, taskId });

        } else if (status === 'failed') {
          // 更新任务状态
          messageStore.failTask(taskId, error_message || '生成失败');

          // 更新消息状态为失败
          const mediaTask = messageStore.getMediaTask(taskId);
          const unifiedTask = messageStore.getTask(taskId);
          const messageId = mediaTask?.placeholderId || unifiedTask?.messageId;

          if (messageId && conversationId) {
            messageStore.updateMessage(messageId, {
              status: 'failed',
              is_error: true,
              error: { code: 'GENERATION_FAILED', message: error_message || '生成失败' },
            });
          }

          // 清理媒体任务
          if (mediaTask) {
            messageStore.failMediaTask(taskId);
          }

          // 设置发送状态
          messageStore.setIsSending(false);

          const context = operationContextRef.current.get(taskId);
          if (context?.onError) {
            context.onError(new Error(error_message || '生成失败'));
          }
          operationContextRef.current.delete(taskId);

          // Toast 提示
          import('react-hot-toast').then(({ default: toast }) => {
            toast.error(error_message || '生成失败');
          });
        }

        subscribedTasksRef.current.delete(taskId);
        taskConversationMapRef.current.delete(taskId);
      },

      // ========================================
      // 系统消息
      // ========================================

      credits_changed: (msg) => {
        const credits = msg.credits ?? msg.payload?.credits;
        if (credits === undefined) return;

        logger.info('ws:credits', 'credits changed', { credits });

        const currentUser = useAuthStore.getState().user;
        if (currentUser) {
          useAuthStore.getState().setUser({ ...currentUser, credits });
        }
      },

      subscribed: (msg) => {
        const { task_id, accumulated } = msg.payload || {};

        logger.info('ws:subscribe', 'confirmed', {
          taskId: task_id,
          accumulatedLen: accumulated?.length ?? 0,
        });

        // 恢复累积内容
        if (accumulated && accumulated.length > 0) {
          const task = messageStore.getTask(task_id);
          if (task?.messageId) {
            messageStore.appendContent(task.messageId, accumulated);
          }
        }
      },

      error: (msg) => {
        const message = msg.message ?? msg.payload?.message;
        logger.error('ws:error', 'error received', undefined, { error: message });
      },
    };

    // 注册所有处理器
    const unsubscribes = Object.entries(handlers).map(([type, handler]) =>
      ws.subscribe(type as WSMessageType, handler)
    );

    return () => unsubscribes.forEach((unsub) => unsub());
  }, [ws, messageStore]);

  // ========================================
  // 订阅任务（带映射）
  // ========================================
  const subscribeTaskWithMapping = useCallback((taskId: string, conversationId: string) => {
    if (subscribedTasksRef.current.has(taskId)) {
      logger.debug('ws:subscribe', 'already subscribed', { taskId });
      return;
    }

    subscribedTasksRef.current.add(taskId);
    taskConversationMapRef.current.set(taskId, conversationId);
    wsRef.current.subscribeTask(taskId);

    logger.debug('ws:subscribe', 'subscribed', { taskId, conversationId });
  }, []);

  // subscribeTaskWithMapping ref（用于任务恢复，避免循环依赖）
  const subscribeTaskWithMappingRef = useRef(subscribeTaskWithMapping);
  subscribeTaskWithMappingRef.current = subscribeTaskWithMapping;

  // ========================================
  // 任务恢复逻辑
  // ========================================

  // 同步 WebSocket 连接状态到 TaskRestorationStore
  useEffect(() => {
    const { setWsConnected } = useTaskRestorationStore.getState();
    setWsConnected(ws.isConnected);
  }, [ws.isConnected]);

  // 当条件满足时触发任务恢复
  useEffect(() => {
    const state = useTaskRestorationStore.getState();

    // 检查是否可以开始恢复
    if (state.hydrateComplete && ws.isConnected && !state.restorationComplete && !state.restorationInProgress) {
      logger.info('ws:restore', 'Conditions met, starting task restoration');
      initializeTaskRestoration(subscribeTaskWithMappingRef.current);
    }
  }, [ws.isConnected]);

  // ========================================
  // 注册操作上下文
  // ========================================
  const registerOperation = useCallback((taskId: string, context: OperationContext) => {
    operationContextRef.current.set(taskId, context);
    logger.debug('ws:operation', 'registered', { taskId, type: context.type });
  }, []);

  // ========================================
  // Context Value
  // ========================================
  const contextValue: WebSocketContextValue = {
    isConnected: ws.isConnected,
    isConnecting: ws.isConnecting,
    subscribe: ws.subscribe,
    subscribeTask: ws.subscribeTask,
    unsubscribeTask: ws.unsubscribeTask,
    subscribeTaskWithMapping,
    registerOperation,
  };

  return (
    <WebSocketContext.Provider value={contextValue}>
      {children}
    </WebSocketContext.Provider>
  );
}

// ============================================================
// Hook
// ============================================================

export function useWebSocketContext(): WebSocketContextValue {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error('useWebSocketContext must be used within WebSocketProvider');
  }
  return context;
}
