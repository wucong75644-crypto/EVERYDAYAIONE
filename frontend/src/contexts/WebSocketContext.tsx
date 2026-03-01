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
import { useMessageStore, normalizeMessage, type Message } from '../stores/useMessageStore';
import { useAuthStore } from '../stores/useAuthStore';
import { useTaskRestorationStore } from '../stores/useTaskRestorationStore';
import {
  restoreTaskPlaceholders,
  subscribeRestoredTasks,
  type RestorationResult,
} from '../utils/taskRestoration';
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
  // 注意：不订阅整个 store（会导致每次 state 变化重建 handler）
  // handler 内部通过 useMessageStore.getState() 获取最新状态和方法

  // 已订阅任务（防止重复）
  const subscribedTasksRef = useRef<Set<string>>(new Set());

  // 任务 → 对话映射
  const taskConversationMapRef = useRef<Map<string, string>>(new Map());

  // 操作上下文映射
  const operationContextRef = useRef<Map<string, OperationContext>>(new Map());

  // L1: chunk 缓冲（50ms 批量刷新，避免每个 token 都触发渲染）
  // 改进：同时存储 conversationId，避免额外映射维护
  const chunkBufferRef = useRef<Map<string, { chunk: string; conversationId: string }>>(new Map());
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ws ref（避免回调重建）
  const wsRef = useRef(ws);
  wsRef.current = ws;

  // ========================================
  // 统一消息处理
  // ========================================
  useEffect(() => {
    // handler 内部通过 getState() 获取最新 store，避免闭包捕获导致频繁重建
    const getStore = () => useMessageStore.getState();

    // --- 辅助函数（减少嵌套） ---

    /** 清理任务订阅 */
    const cleanupTaskSubscription = (taskId: string) => {
      subscribedTasksRef.current.delete(taskId);
      taskConversationMapRef.current.delete(taskId);
      ws.unsubscribeTask(taskId);
    };

    /** 处理任务完成（有 messageData） */
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const handleTaskDoneWithMessage = (taskId: string, messageData: any, conversationId: string) => {
      const store = getStore();
      const normalized = normalizeMessage(messageData);

      // 幂等性检查：使用 Store 作为唯一真相来源
      const existingMessage = store.getMessage(normalized.id);
      if (existingMessage?.status === 'completed') {
        logger.warn('ws:done', 'message already completed in store', {
          taskId,
          messageId: normalized.id,
        });
        return;
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
      const context = operationContextRef.current.get(taskId);
      context?.onComplete?.(normalized);
      operationContextRef.current.delete(taskId);
    };

    /** 处理任务失败 */
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const handleTaskFailure = (taskId: string, error: any) => {
      const store = getStore();
      const errorMessage = error?.message || '生成失败';
      store.failTask(taskId, errorMessage);

      // 触发操作上下文回调
      const context = operationContextRef.current.get(taskId);
      context?.onError?.(new Error(errorMessage));
      operationContextRef.current.delete(taskId);

      cleanupTaskSubscription(taskId);
    };

    // --- L1: chunk 缓冲 flush ---

    /** 将缓冲的 chunk 批量刷新到 store */
    const flushChunkBuffer = () => {
      const buffer = chunkBufferRef.current;
      if (buffer.size === 0) return;

      const store = getStore();
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
      flushTimerRef.current = null;
    };

    // --- 消息处理器 ---

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const handlers: Record<string, (msg: any) => void> = {

      // 消息开始（流式）
      message_start: (msg) => {
        const { message_id } = msg;
        if (!message_id) return;

        logger.info('ws:message', 'start received', { messageId: message_id });
        getStore().setStatus(message_id, 'streaming');
      },

      // 流式内容块
      message_chunk: (msg) => {
        const { message_id, task_id, conversation_id } = msg;
        const chunk = msg.chunk || msg.payload?.chunk;
        if (!message_id || !chunk || !conversation_id) return;

        // L1: 累积到 buffer（不触发渲染）
        const bufferData = chunkBufferRef.current.get(message_id);
        const prevChunk = bufferData?.chunk || '';
        const accumulated = prevChunk + chunk;

        chunkBufferRef.current.set(message_id, {
          chunk: accumulated,
          conversationId: conversation_id,
        });

        // 流式回调仍然立即触发（用于外部消费者）
        if (task_id) {
          const context = operationContextRef.current.get(task_id);
          if (context?.onStreamChunk) {
            context.onStreamChunk(chunk, accumulated);
          }
        }

        // L1: 50ms 防抖 flush
        if (!flushTimerRef.current) {
          flushTimerRef.current = setTimeout(flushChunkBuffer, 50);
        }
      },

      // 进度更新
      message_progress: (msg) => {
        const { task_id } = msg;
        const progress = msg.progress ?? msg.payload?.progress;
        if (!task_id || progress === undefined) return;

        logger.debug('ws:message', 'progress update', { taskId: task_id, progress });
        getStore().updateTaskProgress(task_id, progress);
      },

      // 生成完成
      message_done: (msg) => {
        const { task_id, message_id, conversation_id } = msg;
        const messageData = msg.message || msg.payload?.message;

        // L1: 完成前立即 flush 缓冲的 chunk
        if (chunkBufferRef.current.size > 0) {
          if (flushTimerRef.current) {
            clearTimeout(flushTimerRef.current);
            flushTimerRef.current = null;
          }
          flushChunkBuffer();
        }

        logger.info('ws:message', 'done received', {
          taskId: task_id,
          messageId: message_id || messageData?.id,
          conversationId: conversation_id,
        });

        const store = getStore();

        // conversation_id 兜底：从 taskConversationMap 查找（后端可能不发送该字段）
        const effectiveConversationId = conversation_id
          || (task_id ? taskConversationMapRef.current.get(task_id) : undefined);

        // 1. 有 task_id：处理任务完成
        if (task_id) {
          if (messageData && effectiveConversationId) {
            handleTaskDoneWithMessage(task_id, messageData, effectiveConversationId);
          } else if (message_id) {
            store.setStatus(message_id, 'completed');
            // 尝试获取 conversationId 和任务类型
            store.completeTask(task_id);
          }
          cleanupTaskSubscription(task_id);
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

        // 完成流式状态
        if (effectiveConversationId) {
          store.completeStreaming(effectiveConversationId);
          store.markConversationCompleted(effectiveConversationId);
          store.setIsSending(false);
          tabSync.broadcast('message_completed', { conversationId: effectiveConversationId, messageId: message_id });
        }

        // Toast 提示
        import('react-hot-toast').then(({ default: toast }) => {
          toast.success('生成完成');
        });
      },

      // 生成失败
      message_error: (msg) => {
        const { task_id, message_id, conversation_id } = msg;
        const error = msg.error || msg.payload?.error;

        // L1: 错误时丢弃缓冲（避免 flush 到已失败的消息）
        if (message_id) {
          chunkBufferRef.current.delete(message_id);
        }
        if (flushTimerRef.current && chunkBufferRef.current.size === 0) {
          clearTimeout(flushTimerRef.current);
          flushTimerRef.current = null;
        }

        logger.error('ws:message', 'error received', undefined, {
          taskId: task_id,
          messageId: message_id,
          error,
        });

        const store = getStore();

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
          handleTaskFailure(task_id, error);
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

        // 用最新 accumulated 替换占位符内容（补全 Phase 1→2 间的差异）
        if (accumulated && accumulated.length > 0 && task_id) {
          const conversationId = taskConversationMapRef.current.get(task_id);
          if (conversationId) {
            getStore().setStreamingContent(conversationId, accumulated);
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

    return () => {
      unsubscribes.forEach((unsub) => unsub());
      // L1: 清理定时器
      if (flushTimerRef.current) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- handler 通过 getState() 获取最新 store，无需依赖 messageStore
  }, [ws]);

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
  // 任务恢复逻辑（两阶段）
  // ========================================

  // Phase 1 结果缓存（供 Phase 2 使用）
  const restorationResultRef = useRef<RestorationResult | null>(null);

  // Phase 1：hydrate 完成后立即执行（不等 WS）
  // 使用 zustand subscribe 监听 hydrateComplete，避免空依赖 useEffect 的竞态
  useEffect(() => {
    const runPhase1 = () => {
      if (!useTaskRestorationStore.getState().hydrateComplete) return;
      // 防重复：restorationResultRef 从 null → 非 null 表示已启动
      if (restorationResultRef.current !== null) return;
      restorationResultRef.current = { chatTasks: [], mediaTasks: [] };

      logger.info('ws:restore', 'Phase 1: Starting placeholder restoration (HTTP only)');
      restoreTaskPlaceholders().then((result) => {
        if (result) {
          restorationResultRef.current = result;
          logger.info('ws:restore', 'Phase 1 complete', {
            chat: result.chatTasks.length,
            media: result.mediaTasks.length,
          });
        }
        // 无论成功失败都标记就绪（不阻塞骨架屏）
        useTaskRestorationStore.getState().setPlaceholdersReady();

        // 如果 WS 已连接，立即执行 Phase 2
        if (result && wsRef.current.isConnected) {
          logger.info('ws:restore', 'Phase 2: WS already connected, subscribing immediately');
          subscribeRestoredTasks(result, subscribeTaskWithMappingRef.current);
        }
      });
    };

    // 立即检查（hydrate 可能已完成）
    runPhase1();

    // 订阅变化（hydrate 可能在挂载后异步完成）
    const unsub = useTaskRestorationStore.subscribe((state) => {
      if (state.hydrateComplete) runPhase1();
    });
    return unsub;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Phase 2：WS 就绪后，对 Phase 1 的任务执行 subscribe
  // 幂等：subscribedTasksRef 防止重复订阅
  useEffect(() => {
    if (!ws.isConnected) return;
    const result = restorationResultRef.current;
    if (!result || (result.chatTasks.length === 0 && result.mediaTasks.length === 0)) return;

    logger.info('ws:restore', 'Phase 2: WS connected, subscribing restored tasks');
    subscribeRestoredTasks(result, subscribeTaskWithMappingRef.current);
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
