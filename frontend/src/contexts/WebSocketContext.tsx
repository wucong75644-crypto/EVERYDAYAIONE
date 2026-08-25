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
import { useAuthStore } from '../stores/useAuthStore';
import { useTaskRestorationStore } from '../stores/useTaskRestorationStore';
import {
  restoreTaskPlaceholders,
  subscribeRestoredTasks,
  fetchPendingTasks,
  reconcileChatTaskStates,
  type RestorationResult,
} from '../utils/taskRestoration';
import { getMessages } from '../services/message';
import { logger } from '../utils/logger';
import { createWSMessageHandlers } from './wsMessageHandlers';
import type { DeliveryCursor } from './wsMessageHandlerShared';
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
  subscribeTask: (taskId: string, lastIndex?: number) => void;
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
  const currentOrgId = useAuthStore((state) => state.currentOrgId);
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
  // 每个 task 的回放游标，避免重连后重复显示或跳过工具事件。
  const deliveryCursorRef = useRef<Map<string, DeliveryCursor>>(new Map());

  // ws ref（避免回调重建）
  const wsRef = useRef(ws);
  wsRef.current = ws;
  const wsSubscribe = ws.subscribe;
  const wsSubscribeTask = ws.subscribeTask;
  const wsUnsubscribeTask = ws.unsubscribeTask;
  const wsSend = ws.send;
  const isWsConnected = ws.isConnected;

  // 统一消息处理
  useEffect(() => {
    const deps = {
      getStore: () => useMessageStore.getState(),
      subscribedTasksRef,
      taskConversationMapRef,
      operationContextRef,
      chunkBufferRef,
      flushTimerRef,
      unsubscribeTask: wsUnsubscribeTask,
      send: wsSend,
      deliveryCursorRef,
    };

    const handlers = createWSMessageHandlers(deps);

    // 注册所有处理器
    const unsubscribes = Object.entries(handlers).map(([type, handler]) =>
      wsSubscribe(type as WSMessageType, handler)
    );

    return () => {
      unsubscribes.forEach((unsub) => unsub());
      // L1: 清理定时器
      if (flushTimerRef.current) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
    };
  // handler 通过 getState() 获取最新 store；只依赖稳定的传输方法，避免
  // WebSocket hook 每次渲染返回新对象时反复拆装全部消息处理器。
  }, [wsSubscribe, wsUnsubscribeTask, wsSend]);

  // 订阅任务（带映射）
  const subscribeTaskWithMapping = useCallback((taskId: string, conversationId: string) => {
    if (subscribedTasksRef.current.has(taskId)) {
      logger.debug('ws:subscribe', 'already subscribed', { taskId });
      return;
    }

    subscribedTasksRef.current.add(taskId);
    taskConversationMapRef.current.set(taskId, conversationId);
    const cursor = deliveryCursorRef.current.get(taskId);
    wsRef.current.subscribeTask(taskId, cursor?.lastSeq ?? 0);

    logger.debug('ws:subscribe', 'subscribed', { taskId, conversationId });
  }, []);

  // WebSocket 重连后主动恢复已有任务订阅，并携带最后确认的 delivery_seq。
  // Redis Pub/Sub 不提供断线回放，回放游标必须由客户端重新发送给服务端。
  useEffect(() => {
    if (!isWsConnected) return;
    subscribedTasksRef.current.forEach((taskId) => {
      const cursor = deliveryCursorRef.current.get(taskId);
      wsSubscribeTask(taskId, cursor?.lastSeq ?? 0);
    });
  }, [isWsConnected, wsSubscribeTask]);

  // subscribeTaskWithMapping ref（用于任务恢复，避免循环依赖）
  const subscribeTaskWithMappingRef = useRef(subscribeTaskWithMapping);
  subscribeTaskWithMappingRef.current = subscribeTaskWithMapping;

  // 任务恢复逻辑（两阶段）
  // Phase 1 结果缓存（供 Phase 2 使用）
  const restorationResultRef = useRef<RestorationResult | null>(null);

  // 企业上下文变化后，旧连接的所有临时映射都必须失效。
  useEffect(() => {
    subscribedTasksRef.current.clear();
    taskConversationMapRef.current.clear();
    operationContextRef.current.clear();
    chunkBufferRef.current.clear();
    deliveryCursorRef.current.clear();
    restorationResultRef.current = null;
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
  }, [currentOrgId]);
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
  }, []);

  // Phase 2：WS 就绪后，对 Phase 1 的任务执行 subscribe
  // 幂等：subscribedTasksRef 防止重复订阅
  useEffect(() => {
    if (!isWsConnected) return;
    const result = restorationResultRef.current;
    if (!result || (result.chatTasks.length === 0 && result.mediaTasks.length === 0)) return;

    logger.info('ws:restore', 'Phase 2: WS connected, subscribing restored tasks');
    subscribeRestoredTasks(result, subscribeTaskWithMappingRef.current);
  }, [isWsConnected]);

  // WS 重连恢复：聊天和媒体都以数据库状态为准，补偿可能遗漏的终态事件。
  async function recoverMissedCompletions() {
    await reconcileChatTaskStates();

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

    // 媒体任务仍复用原有占位符检查；聊天任务由 reconcileChatTaskStates 统一处理。
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

  // Phase 3：WS 重连后，检查断连期间已完成的聊天/媒体任务
  // 区分首次连接 vs 重连：首次由 Phase 1/2 处理，重连才走此逻辑
  const wasEverConnectedRef = useRef(false);

  useEffect(() => {
    if (!isWsConnected) return;

    // 首次连接由 Phase 1/2 处理，跳过
    if (!wasEverConnectedRef.current) {
      wasEverConnectedRef.current = true;
      return;
    }

    // 重连：检查是否有遗漏的终态事件
    recoverMissedCompletions();
  }, [isWsConnected]);

  // Phase 4：首次刷新或继续输出后，即使没有发生 WS 重连，也可能错过
  // message_done（例如任务在订阅窗口内完成）。用低频任务状态对账兜底，
  // 不按 token 查库，只在前端仍有 streaming 任务时轮询。
  useEffect(() => {
    if (!isWsConnected) return;

    const reconcile = () => {
      const store = useMessageStore.getState();
      if (store.streamingMessages.size === 0) return;
      reconcileChatTaskStates();
    };

    const initialCheck = window.setTimeout(reconcile, 1500);
    const interval = window.setInterval(reconcile, 3000);
    return () => {
      window.clearTimeout(initialCheck);
      window.clearInterval(interval);
    };
  }, [isWsConnected]);

  // 注册操作上下文
  const registerOperation = useCallback((taskId: string, context: OperationContext) => {
    operationContextRef.current.set(taskId, context);
    logger.debug('ws:operation', 'registered', { taskId, type: context.type });
  }, []);

  // 工具确认弹窗回调
  const toolConfirmRequest = useMessageStore((s) => s.toolConfirmRequest);

  const handleToolConfirm = useCallback((toolCallId: string) => {
    const request = useMessageStore.getState().toolConfirmRequest;
    if (!request || request.toolCallId !== toolCallId) return;
    wsSend({
      type: 'tool_confirm_response' as const,
      payload: {
        tool_call_id: toolCallId,
        task_id: request.taskId,
        conversation_id: request.conversationId,
        approved: true,
      },
    });
    useMessageStore.getState().setToolConfirmRequest(null);
  }, [wsSend]);

  const handleToolReject = useCallback((toolCallId: string) => {
    const request = useMessageStore.getState().toolConfirmRequest;
    if (!request || request.toolCallId !== toolCallId) return;
    wsSend({
      type: 'tool_confirm_response' as const,
      payload: {
        tool_call_id: toolCallId,
        task_id: request.taskId,
        conversation_id: request.conversationId,
        approved: false,
      },
    });
    useMessageStore.getState().setToolConfirmRequest(null);
  }, [wsSend]);

  // 用户打断（steer）— InputArea 通过 CustomEvent 触发
  useEffect(() => {
    const handler = (e: Event) => {
      const { taskId, conversationId, message } = (e as CustomEvent).detail;
      if (!taskId || !message) return;
      wsSend({
        type: 'user_steer' as const,
        payload: { task_id: taskId, conversation_id: conversationId, message },
      });
      logger.info('ws:steer', 'user_steer sent', { taskId, msgLen: message.length });
    };
    window.addEventListener('chat:user-steer', handler);
    return () => window.removeEventListener('chat:user-steer', handler);
  }, [wsSend]);

  // 表单提交 — FormBlock 通过 CustomEvent 触发
  useEffect(() => {
    const handler = (e: Event) => {
      const { formType, formData } = (e as CustomEvent).detail;
      if (!formType || !formData) return;
      wsSend({
        type: 'form_submit' as const,
        payload: { form_type: formType, form_data: formData },
      });
      logger.info('ws:form', 'form_submit sent', { formType });
    };
    window.addEventListener('chat:form-submit', handler);
    return () => window.removeEventListener('chat:form-submit', handler);
  }, [wsSend]);

  // 表单提交结果 — 后端返回 form_submit_result → 派发到 FormBlock
  useEffect(() => {
    const unsub = wsSubscribe('form_submit_result' as never, (msg) => {
      const payload = msg.payload as { success?: boolean; message?: string };
      window.dispatchEvent(
        new CustomEvent('chat:form-submit-result', { detail: payload }),
      );
      logger.info('ws:form', 'form_submit_result', { success: payload?.success });
    });
    return unsub;
  }, [wsSubscribe]);

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
