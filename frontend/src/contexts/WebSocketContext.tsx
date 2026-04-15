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
import { useWebSocket, type WSMessageType, type WSMessage } from '../hooks/useWebSocket';
import { useMessageStore, normalizeMessage, type Message } from '../stores/useMessageStore';
import { useTaskRestorationStore } from '../stores/useTaskRestorationStore';
import {
  restoreTaskPlaceholders,
  subscribeRestoredTasks,
  fetchPendingTasks,
  type RestorationResult,
} from '../utils/taskRestoration';
import { getMessages } from '../services/message';
import { logger } from '../utils/logger';
import { createWSMessageHandlers } from './wsMessageHandlers';
import ToolConfirmModal from '../components/chat/modals/ToolConfirmModal';

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
  subscribe: (type: WSMessageType, handler: (msg: WSMessage) => void) => () => void;
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
      send: ws.send,
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

  // WS 重连恢复：从 API 刷新断连期间已完成的媒体消息
  async function recoverMissedMediaCompletions() {
    const store = useMessageStore.getState();

    // 1. 从 store 中找出有 pending 媒体消息的对话
    const pendingMediaConversations: string[] = [];
    for (const [conversationId, messages] of Object.entries(store.messages)) {
      const hasPendingMedia = messages.some(
        (m: Message) =>
          m.role === 'assistant' &&
          m.status === 'pending' &&
          m.generation_params?.type &&
          ['image', 'video'].includes(m.generation_params.type)
      );
      if (hasPendingMedia) {
        pendingMediaConversations.push(conversationId);
      }
    }

    if (pendingMediaConversations.length === 0) return;

    logger.info('ws:reconnect', 'Checking for missed media completions', {
      conversations: pendingMediaConversations.length,
    });

    // 2. 调用 /tasks/pending 获取后端最新任务状态
    const tasks = await fetchPendingTasks();
    if (!tasks) return;

    // 3. 仍在运行的任务对应的对话
    const stillRunningConversations = new Set(
      tasks
        .filter((t) => t.status === 'pending' || t.status === 'running')
        .map((t) => t.conversation_id)
    );

    // 4. 前端认为 pending，但后端已完成 → 断连期间完成的任务
    const completedConversations = pendingMediaConversations.filter(
      (cid) => !stillRunningConversations.has(cid)
    );

    if (completedConversations.length === 0) return;

    logger.info('ws:reconnect', 'Recovering missed media completions', {
      conversations: completedConversations,
    });

    // 5. 重新从 API 加载这些对话的消息，替换 store 中的旧数据
    for (const cid of completedConversations) {
      try {
        const response = await getMessages(cid, 30, 0);
        if (response?.messages) {
          const messagesAsc = [...response.messages].map(normalizeMessage).reverse();
          store.setMessagesForConversation(cid, messagesAsc, response.messages.length >= 30);
        }
      } catch (error) {
        logger.error('ws:reconnect', 'Failed to refresh messages', error, {
          conversationId: cid,
        });
      }
    }
  }

  // Phase 3：WS 重连后，检查断连期间是否有媒体任务已完成
  // 区分首次连接 vs 重连：首次由 Phase 1/2 处理，重连才走此逻辑
  const wasEverConnectedRef = useRef(false);

  useEffect(() => {
    if (!ws.isConnected) return;

    // 首次连接由 Phase 1/2 处理，跳过
    if (!wasEverConnectedRef.current) {
      wasEverConnectedRef.current = true;
      return;
    }

    // 重连：检查是否有遗漏的媒体完成事件
    recoverMissedMediaCompletions();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ws.isConnected]);

  // 注册操作上下文
  const registerOperation = useCallback((taskId: string, context: OperationContext) => {
    operationContextRef.current.set(taskId, context);
    logger.debug('ws:operation', 'registered', { taskId, type: context.type });
  }, []);

  // 工具确认弹窗回调
  const toolConfirmRequest = useMessageStore((s) => s.toolConfirmRequest);

  const handleToolConfirm = useCallback((toolCallId: string) => {
    ws.send({
      type: 'tool_confirm_response' as const,
      payload: { tool_call_id: toolCallId, approved: true },
    });
    useMessageStore.getState().setToolConfirmRequest(null);
  }, [ws]);

  const handleToolReject = useCallback((toolCallId: string) => {
    ws.send({
      type: 'tool_confirm_response' as const,
      payload: { tool_call_id: toolCallId, approved: false },
    });
    useMessageStore.getState().setToolConfirmRequest(null);
  }, [ws]);

  // 用户打断（steer）— InputArea 通过 CustomEvent 触发
  useEffect(() => {
    const handler = (e: Event) => {
      const { taskId, conversationId, message } = (e as CustomEvent).detail;
      if (!taskId || !message) return;
      ws.send({
        type: 'user_steer' as const,
        payload: { task_id: taskId, conversation_id: conversationId, message },
      });
      logger.info('ws:steer', 'user_steer sent', { taskId, msgLen: message.length });
    };
    window.addEventListener('chat:user-steer', handler);
    return () => window.removeEventListener('chat:user-steer', handler);
  }, [ws]);

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
      <ToolConfirmModal
        request={toolConfirmRequest}
        onConfirm={handleToolConfirm}
        onReject={handleToolReject}
      />
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
