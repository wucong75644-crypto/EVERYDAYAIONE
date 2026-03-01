/* eslint-disable react-refresh/only-export-components */
/**
 * WebSocket Context（统一版本）
 *
 * 简化设计：
 * 1. 统一消息处理：message_start/chunk/progress/done/error
 * 2. 使用 useMessageStore 统一状态管理
 * 3. 任务恢复走统一入口
 *
 * 消息处理器逻辑提取到 wsMessageHandlers.ts
 */

import { createContext, useContext, useEffect, useRef, useCallback, type ReactNode } from 'react';
import { useWebSocket, type WSMessageType } from '../hooks/useWebSocket';
import { useMessageStore } from '../stores/useMessageStore';
import { useTaskRestorationStore } from '../stores/useTaskRestorationStore';
import {
  restoreTaskPlaceholders,
  subscribeRestoredTasks,
  type RestorationResult,
} from '../utils/taskRestoration';
import { logger } from '../utils/logger';
import { createWSMessageHandlers } from './wsMessageHandlers';
import type { Message } from '../stores/useMessageStore';

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

  // 统一消息处理
  useEffect(() => {
    const deps = {
      getStore: () => useMessageStore.getState(),
      subscribedTasksRef,
      taskConversationMapRef,
      operationContextRef,
      chunkBufferRef,
      flushTimerRef,
      unsubscribeTask: ws.unsubscribeTask,
    };

    const handlers = createWSMessageHandlers(deps);

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

  // 订阅任务（带映射）
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

  // 任务恢复逻辑（两阶段）
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

  // 注册操作上下文
  const registerOperation = useCallback((taskId: string, context: OperationContext) => {
    operationContextRef.current.set(taskId, context);
    logger.debug('ws:operation', 'registered', { taskId, type: context.type });
  }, []);

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

export function useWebSocketContext(): WebSocketContextValue {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error('useWebSocketContext must be used within WebSocketProvider');
  }
  return context;
}
