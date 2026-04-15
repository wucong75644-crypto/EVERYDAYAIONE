/**
 * WebSocket Hook
 *
 * 参考实现:
 * - https://github.com/robtaussig/react-use-websocket
 *
 * 功能:
 * - 自动连接/重连
 * - 心跳保活
 * - 消息订阅
 * - 断点续传支持
 */

import { useEffect, useLayoutEffect, useRef, useCallback, useState } from 'react';
import { useAuthStore } from '../stores/useAuthStore';
import { logger } from '../utils/logger';

// === 配置常量 ===

// WebSocket URL（自动从 API URL 推导）
function getWebSocketUrl(): string {
  // 优先使用环境变量
  if (import.meta.env.VITE_WS_URL) {
    return import.meta.env.VITE_WS_URL;
  }

  // 从 API URL 推导
  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || '';

  if (apiBaseUrl.startsWith('http://')) {
    return apiBaseUrl.replace('http://', 'ws://') + '/ws';
  }
  if (apiBaseUrl.startsWith('https://')) {
    return apiBaseUrl.replace('https://', 'wss://') + '/ws';
  }

  // 相对路径：使用当前页面的协议和主机
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host;
  return `${protocol}//${host}/api/ws`;
}

const HEARTBEAT_INTERVAL = 30000; // 30秒
const RECONNECT_INTERVAL_BASE = 1000; // 基础重连间隔
const RECONNECT_INTERVAL_MAX = 30000; // 最大重连间隔（之后每30s重试，无上限）

// === 消息类型 ===

export type WSMessageType =
  // 统一消息类型
  | 'message_pending'
  | 'message_start'
  | 'message_chunk'
  | 'message_progress'
  | 'message_done'
  | 'message_error'
  | 'agent_step'
  | 'routing_complete'
  // 系统消息
  | 'credits_changed'
  | 'memory_extracted'
  | 'notification'
  | 'ping'
  | 'pong'
  | 'subscribe'
  | 'unsubscribe'
  | 'subscribed'
  | 'conversation_updated'
  | 'server_restarting'
  | 'error'
  // 工具确认
  | 'tool_call'
  | 'tool_result'
  | 'tool_confirm_request'
  | 'tool_confirm_response'
  | 'content_block_add'
  | 'suggestions_ready'
  | 'thinking_chunk'
  | 'image_partial_update'
  // AI 主动沟通
  | 'ask_user_request';

export interface WSMessage {
  type: WSMessageType;
  payload: Record<string, unknown>;
  timestamp: number;
  task_id?: string;
  conversation_id?: string;
  message_index?: number;
}

// === 连接状态 ===

export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

// === 订阅回调 ===

type MessageHandler = (message: WSMessage) => void;

// === Hook 返回类型 ===

export interface UseWebSocketReturn {
  connectionState: ConnectionState;
  isConnected: boolean;
  isConnecting: boolean;
  subscribe: (type: WSMessageType, handler: MessageHandler) => () => void;
  subscribeTask: (taskId: string, lastIndex?: number) => void;
  unsubscribeTask: (taskId: string) => void;
  send: (message: Omit<WSMessage, 'timestamp'>) => void;
}

// === 获取 Token 函数 ===

function getToken(): string | null {
  return localStorage.getItem('access_token');
}

function isAuthenticated(): boolean {
  return !!getToken();
}

// === Hook 实现 ===

export function useWebSocket(): UseWebSocketReturn {
  const wsRef = useRef<WebSocket | null>(null);
  const handlersRef = useRef<Map<WSMessageType, Set<MessageHandler>>>(new Map());
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heartbeatIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isCleaningUpRef = useRef(false);
  // 服务器重启期间的重连不应被误判为认证失败
  const isServerRestartingRef = useRef(false);
  // 用于打破 handleServerRestart <-> connect 循环依赖
  const connectRef = useRef<(() => void) | null>(null);
  // WS 未连接时缓存订阅请求，连接后自动重发
  const pendingSubscriptionsRef = useRef<Map<string, number>>(new Map());

  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');

  // 清理函数
  const cleanup = useCallback(() => {
    isCleaningUpRef.current = true;

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
      heartbeatIntervalRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close(1000, 'Client cleanup');
      wsRef.current = null;
    }

    isCleaningUpRef.current = false;
  }, []);

  // 分发消息给订阅者
  const dispatchMessage = useCallback((message: WSMessage) => {
    const handlers = handlersRef.current.get(message.type);
    if (handlers) {
      handlers.forEach((handler) => {
        try {
          handler(message);
        } catch (error) {
          logger.error('ws:dispatch', 'Handler error', error);
        }
      });
    }
  }, []);

  // 启动心跳
  const startHeartbeat = useCallback(() => {
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
    }

    heartbeatIntervalRef.current = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(
          JSON.stringify({
            type: 'pong',
            payload: {},
            timestamp: Date.now(),
          })
        );
      }
    }, HEARTBEAT_INTERVAL);
  }, []);

  // 计算重连延迟（指数退避）
  const getReconnectDelay = useCallback(() => {
    const delay = Math.min(
      RECONNECT_INTERVAL_BASE * Math.pow(2, reconnectAttemptsRef.current),
      RECONNECT_INTERVAL_MAX
    );
    return delay;
  }, []);

  // 处理服务器重启消息
  const handleServerRestart = useCallback(() => {
    logger.info('ws:connection', 'Server restarting, will reconnect with jitter');
    isServerRestartingRef.current = true;
    cleanup();

    // 增加随机抖动（3-8秒），错开重连峰值，给后端足够启动时间
    const jitter = 3000 + Math.random() * 5000;
    reconnectAttemptsRef.current = 0; // 重置重连计数

    reconnectTimeoutRef.current = setTimeout(() => {
      // 使用 ref 调用 connect，打破循环依赖
      connectRef.current?.();
    }, jitter);
  }, [cleanup]);

  // 连接 WebSocket
  const connect = useCallback(() => {
    const token = getToken();
    if (!token || !isAuthenticated()) {
      logger.info('ws:connection', 'Not authenticated, skip connection');
      return;
    }

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    if (isCleaningUpRef.current) {
      return;
    }

    cleanup();
    setConnectionState('connecting');

    const orgId = localStorage.getItem('current_org_id');
    const orgParam = orgId ? `&org_id=${encodeURIComponent(orgId)}` : '';
    const wsUrl = `${getWebSocketUrl()}?token=${encodeURIComponent(token)}${orgParam}`;
    logger.info('ws:connection', 'Connecting', { url: wsUrl.replace(/token=.*/, 'token=***') });

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      logger.info('ws:connection', 'Connected');
      isServerRestartingRef.current = false;
      setConnectionState('connected');
      reconnectAttemptsRef.current = 0;
      startHeartbeat();

      // 重连后重发 pending 订阅（WS 断线期间缓存的任务订阅）
      // 多租户隔离：如果 org 已切换，清空旧 org 的 pending 订阅
      if (pendingSubscriptionsRef.current.size > 0) {
        const currentOrg = localStorage.getItem('current_org_id') || '';
        const wsOrg = orgId || '';
        if (currentOrg !== wsOrg) {
          logger.info('ws:connection', 'Org changed, clearing pending subscriptions', {
            oldOrg: wsOrg, newOrg: currentOrg,
          });
          pendingSubscriptionsRef.current.clear();
        } else {
          logger.info('ws:connection', 'Flushing pending subscriptions', {
            count: pendingSubscriptionsRef.current.size,
          });
          pendingSubscriptionsRef.current.forEach((lastIndex, taskId) => {
            ws.send(JSON.stringify({
              type: 'subscribe',
              payload: { task_id: taskId, last_index: lastIndex },
              timestamp: Date.now(),
            }));
          });
          pendingSubscriptionsRef.current.clear();
        }
      }
    };

    ws.onclose = (event) => {
      logger.info('ws:connection', 'Closed', { code: event.code, reason: event.reason });
      setConnectionState('disconnected');

      if (heartbeatIntervalRef.current) {
        clearInterval(heartbeatIntervalRef.current);
        heartbeatIntervalRef.current = null;
      }

      // 认证失败：只有后端明确返回 4001/4002 才是 token 无效
      // 1006 是"异常关闭"（网络断开/服务器重启），不代表认证失败
      const isAuthError =
        event.code === 4001 ||
        event.code === 4002;

      if (isAuthError) {
        logger.warn('ws:connection', 'Auth failed, clearing token', { code: event.code });
        const loginOrgId = localStorage.getItem('login_org_id') || localStorage.getItem('current_org_id');
        localStorage.removeItem('access_token');
        localStorage.removeItem('user');
        localStorage.removeItem('current_org_id');
        localStorage.removeItem('current_org');
        if (window.location.pathname !== '/') {
          window.location.href = loginOrgId ? `/?org=${loginOrgId}` : '/';
        }
        return;
      }

      // 非主动清理时自动重连（含服务器重启 code=1000 的场景）
      if (
        !isCleaningUpRef.current &&
        isAuthenticated()
      ) {
        setConnectionState('reconnecting');
        const delay = getReconnectDelay();
        reconnectAttemptsRef.current++;
        logger.info('ws:connection', 'Reconnecting', { delay, attempt: reconnectAttemptsRef.current });

        reconnectTimeoutRef.current = setTimeout(() => {
          // 使用 ref 调用 connect，避免声明顺序问题
          connectRef.current?.();
        }, delay);
      }
    };

    ws.onerror = () => {
      logger.error('ws:connection', 'WebSocket error');
    };

    ws.onmessage = (event) => {
      try {
        const message: WSMessage = JSON.parse(event.data);

        // 处理心跳
        if (message.type === 'ping') {
          ws.send(
            JSON.stringify({
              type: 'pong',
              payload: {},
              timestamp: Date.now(),
            })
          );
          return;
        }

        // 处理服务器重启通知
        if (message.type === 'server_restarting') {
          handleServerRestart();
          return;
        }

        // 分发消息
        dispatchMessage(message);
      } catch (error) {
        logger.error('ws:message', 'Message parse error', error);
      }
    };
  }, [cleanup, startHeartbeat, getReconnectDelay, dispatchMessage, handleServerRestart]);

  // 更新 connectRef，供 handleServerRestart 使用（避免渲染期间修改 ref）
  useLayoutEffect(() => {
    connectRef.current = connect;
  });

  // 订阅消息类型
  const subscribe = useCallback((type: WSMessageType, handler: MessageHandler) => {
    if (!handlersRef.current.has(type)) {
      handlersRef.current.set(type, new Set());
    }
    handlersRef.current.get(type)!.add(handler);

    // 返回取消订阅函数
    return () => {
      handlersRef.current.get(type)?.delete(handler);
    };
  }, []);

  // 订阅任务（WS 未连接时入队列，连接后自动重发）
  const subscribeTask = useCallback((taskId: string, lastIndex: number = -1) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'subscribe',
          payload: { task_id: taskId, last_index: lastIndex },
          timestamp: Date.now(),
        })
      );
      logger.info('ws:subscribe', 'Subscribed to task', { taskId });
    } else {
      pendingSubscriptionsRef.current.set(taskId, lastIndex);
      logger.info('ws:subscribe', 'Queued pending subscription', { taskId });
    }
  }, []);

  // 取消订阅任务
  const unsubscribeTask = useCallback((taskId: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'unsubscribe',
          payload: { task_id: taskId },
          timestamp: Date.now(),
        })
      );
      logger.info('ws:subscribe', 'Unsubscribed from task', { taskId });
    }
  }, []);

  // 发送消息
  const send = useCallback((message: Omit<WSMessage, 'timestamp'>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          ...message,
          timestamp: Date.now(),
        })
      );
    }
  }, []);

  // 页面可见性变化：切回前台时检查并重连
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (
        document.visibilityState === 'visible' &&
        wsRef.current?.readyState !== WebSocket.OPEN &&
        isAuthenticated()
      ) {
        logger.info('ws:connection', 'Tab visible, reconnecting');
        reconnectAttemptsRef.current = 0;
        connectRef.current?.();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, []);

  // 网络恢复：offline → online 时重连
  useEffect(() => {
    const handleOnline = () => {
      if (
        wsRef.current?.readyState !== WebSocket.OPEN &&
        isAuthenticated()
      ) {
        logger.info('ws:connection', 'Network online, reconnecting');
        reconnectAttemptsRef.current = 0;
        connectRef.current?.();
      }
    };

    window.addEventListener('online', handleOnline);
    return () => window.removeEventListener('online', handleOnline);
  }, []);

  // 监听认证状态变化（Zustand 状态驱动，同 tab 登录/退出也能感知）
  useEffect(() => {
    const unsubscribe = useAuthStore.subscribe((state, prevState) => {
      if (state.isAuthenticated && !prevState.isAuthenticated) {
        // 登录：建立 WS 连接
        logger.info('ws:connection', 'Auth state changed to authenticated, connecting');
        connect();
      } else if (!state.isAuthenticated && prevState.isAuthenticated) {
        // 退出：断开 WS 连接
        logger.info('ws:connection', 'Auth state changed to unauthenticated, disconnecting');
        cleanup();
        setConnectionState('disconnected');
      }
    });

    return unsubscribe;
  }, [connect, cleanup]);

  // 自动连接
  useEffect(() => {
    if (isAuthenticated()) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      connect();
    }
    return cleanup;
  }, [connect, cleanup]);

  return {
    connectionState,
    isConnected: connectionState === 'connected',
    isConnecting: connectionState === 'connecting' || connectionState === 'reconnecting',
    subscribe,
    subscribeTask,
    unsubscribeTask,
    send,
  };
}
