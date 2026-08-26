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
import { logger } from '../utils/logger';
import { logoutOnce, silentRefresh } from '../utils/tokenManager';

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
const TOKEN_REFRESH_SKEW_MS = 30000;

// === 消息类型 ===

export type WSMessageType =
  // 统一消息类型
  | 'message_pending'
  | 'message_start'
  | 'message_chunk'
  | 'message_progress'
  | 'message_done'
  | 'stream_end'
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
  | 'connection_ready'
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
  // 表单交互
  | 'form_submit'
  | 'form_submit_result'
  // 用户打断
  | 'user_steer';

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

/**
 * WebSocket 握手失败时浏览器不会暴露服务端的 HTTP 401/403，通常只会
 * 触发 code=1006。连接前主动检查 JWT 的 exp，避免用过期 token 无限重连。
 */
export function isAccessTokenExpired(
  token: string,
  nowMs: number = Date.now(),
  skewMs: number = TOKEN_REFRESH_SKEW_MS,
): boolean {
  const payloadPart = token.split('.')[1];
  if (!payloadPart) return false;

  try {
    const normalized = payloadPart.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    const payload = JSON.parse(atob(padded)) as { exp?: unknown };
    return (
      typeof payload.exp === 'number' &&
      payload.exp * 1000 <= nowMs + skewMs
    );
  } catch {
    // 非 JWT 或 payload 损坏时交给服务端认证，不因本地解析失败阻断连接。
    return false;
  }
}

// === Hook 实现 ===

export function useWebSocket(
  currentOrgId: string | null = null,
  isAuthenticated = false,
): UseWebSocketReturn {
  const wsRef = useRef<WebSocket | null>(null);
  const handlersRef = useRef<Map<WSMessageType, Set<MessageHandler>>>(new Map());
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heartbeatIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // 每个物理连接都有独立的 generation。旧 socket 的异步回调不能再修改
  // 当前连接状态，也不能触发第二个重连循环。
  const connectionGenerationRef = useRef(0);
  const connectInFlightRef = useRef(false);
  // 服务器重启期间的重连不应被误判为认证失败
  const isServerRestartingRef = useRef(false);
  // 用于打破 handleServerRestart <-> connect 循环依赖
  const connectRef = useRef<(() => void) | null>(null);
  // 多次重连只允许共享同一个 Token 刷新请求。
  const tokenRefreshRef = useRef<Promise<string> | null>(null);
  const requestedOrgIdRef = useRef<string | null>(currentOrgId);
  requestedOrgIdRef.current = currentOrgId;
  // 认证状态是连接资格的唯一来源；localStorage 中的 token 只用于握手。
  // 这避免“登录页残留旧 token”被误当成仍处于登录会话。
  const authenticatedRef = useRef(isAuthenticated);
  authenticatedRef.current = isAuthenticated;

  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');

  // 清理函数
  const cleanup = useCallback(() => {
    connectionGenerationRef.current += 1;

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
      heartbeatIntervalRef.current = null;
    }
    if (wsRef.current) {
      const socket = wsRef.current;
      wsRef.current = null;
      if (
        socket.readyState === WebSocket.OPEN ||
        socket.readyState === WebSocket.CONNECTING
      ) {
        socket.close(1000, 'Client cleanup');
      }
    }
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
  const connect = useCallback(async () => {
    let token = getToken();
    if (!token || !authenticatedRef.current) {
      logger.info('ws:connection', 'Not authenticated, skip connection');
      return;
    }

    const currentReadyState = wsRef.current?.readyState;
    if (
      currentReadyState === WebSocket.OPEN ||
      currentReadyState === WebSocket.CONNECTING ||
      currentReadyState === WebSocket.CLOSING
    ) {
      return;
    }

    if (connectInFlightRef.current) {
      return;
    }

    connectInFlightRef.current = true;
    const requestGeneration = connectionGenerationRef.current;

    try {
      if (isAccessTokenExpired(token)) {
        logger.info('ws:connection', 'Access token expired or near expiry, refreshing before connect');
        const refreshPromise = tokenRefreshRef.current || (tokenRefreshRef.current = silentRefresh());
        try {
          token = await refreshPromise;
        } catch {
          return;
        } finally {
          if (tokenRefreshRef.current === refreshPromise) {
            tokenRefreshRef.current = null;
          }
        }
        if (!token || !authenticatedRef.current || requestGeneration !== connectionGenerationRef.current) {
          return;
        }
      }
      if (requestGeneration !== connectionGenerationRef.current || !authenticatedRef.current) {
        return;
      }

      // 清掉已经 CLOSED 的旧引用，但不要关闭/替换一个尚在 CONNECTING 的 socket。
      if (wsRef.current && wsRef.current.readyState === WebSocket.CLOSED) {
        wsRef.current = null;
      }
      const generation = ++connectionGenerationRef.current;
      const connectionOrgId = currentOrgId;
      setConnectionState('connecting');

      const orgParam = connectionOrgId ? `&org_id=${encodeURIComponent(connectionOrgId)}` : '';
      const wsUrl = `${getWebSocketUrl()}?token=${encodeURIComponent(token)}${orgParam}`;
      logger.info('ws:connection', 'Connecting', {
        url: wsUrl.replace(/token=.*/, 'token=***'),
        generation,
      });

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (
          wsRef.current !== ws ||
          connectionGenerationRef.current !== generation ||
          connectionOrgId !== requestedOrgIdRef.current
        ) {
          ws.close(1000, 'Stale WebSocket generation');
          return;
        }
        logger.info('ws:connection', 'Connected', { generation, orgId: connectionOrgId });
        isServerRestartingRef.current = false;
        setConnectionState('connected');
        reconnectAttemptsRef.current = 0;
        startHeartbeat();
        // 逻辑订阅由 WebSocketContext 统一恢复。Hook 只负责物理连接，
        // 避免这里和 Context 同时发送同一个 subscribe。
      };

      ws.onclose = (event) => {
        const isCurrent = wsRef.current === ws && connectionGenerationRef.current === generation;
        logger.info('ws:connection', 'Closed', {
          code: event.code,
          reason: event.reason,
          generation,
          current: isCurrent,
        });
        if (!isCurrent) return;

        wsRef.current = null;
        connectionGenerationRef.current += 1;
        setConnectionState('disconnected');

        if (heartbeatIntervalRef.current) {
          clearInterval(heartbeatIntervalRef.current);
          heartbeatIntervalRef.current = null;
        }

        // 认证失败：只有后端明确返回 4001/4002 才是 token 无效。
        const isAuthError = event.code === 4001 || event.code === 4002;

        if (isAuthError) {
          logger.warn('ws:connection', 'Auth failed, unified logout', { code: event.code });
          logoutOnce();
          return;
        }

        if (event.code === 4003) {
          logger.error('ws:connection', 'Stopped reconnecting after organization scope mismatch');
          return;
        }

        if (authenticatedRef.current && getToken()) {
          setConnectionState('reconnecting');
          const delay = getReconnectDelay();
          reconnectAttemptsRef.current++;
          logger.info('ws:connection', 'Reconnecting', { delay, attempt: reconnectAttemptsRef.current });

          reconnectTimeoutRef.current = setTimeout(() => {
            reconnectTimeoutRef.current = null;
            connectRef.current?.();
          }, delay);
        }
      };

      ws.onerror = () => {
        if (wsRef.current === ws && connectionGenerationRef.current === generation) {
          logger.error('ws:connection', 'WebSocket error', { generation });
        }
      };

      ws.onmessage = (event) => {
        if (
          wsRef.current !== ws ||
          connectionGenerationRef.current !== generation ||
          connectionOrgId !== requestedOrgIdRef.current
        ) return;
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

        if (message.type === 'connection_ready') {
          const acknowledgedOrgId =
            typeof message.payload.org_id === 'string'
              ? message.payload.org_id
              : null;
          if (acknowledgedOrgId !== requestedOrgIdRef.current) {
            logger.error('ws:connection', 'Server acknowledged an unexpected organization scope', {
              generation,
              expectedOrgId: requestedOrgIdRef.current,
              acknowledgedOrgId,
            });
            ws.close(4003, 'Organization scope mismatch');
            return;
          }
          logger.info('ws:connection', 'Organization scope verified', {
            generation,
            orgId: acknowledgedOrgId,
          });
          return;
        }

          // 分发消息
          dispatchMessage(message);
        } catch (error) {
          logger.error('ws:message', 'Message parse error', error);
        }
      };
    } finally {
      connectInFlightRef.current = false;
    }
  }, [currentOrgId, startHeartbeat, getReconnectDelay, dispatchMessage, handleServerRestart]);

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

  // 订阅任务。断线期间由 WebSocketContext 保存逻辑订阅并在连接恢复后重放。
  const subscribeTask = useCallback((taskId: string, lastIndex: number = -1) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'subscribe',
          payload: {
            task_id: taskId,
            last_index: lastIndex,
            last_delivery_seq: lastIndex,
          },
          timestamp: Date.now(),
        })
      );
      logger.info('ws:subscribe', 'Subscribed to task', { taskId });
    } else {
      logger.info('ws:subscribe', 'Deferred subscription until connection recovery', { taskId });
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
        authenticatedRef.current && getToken()
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
        authenticatedRef.current && getToken()
      ) {
        logger.info('ws:connection', 'Network online, reconnecting');
        reconnectAttemptsRef.current = 0;
        connectRef.current?.();
      }
    };

    window.addEventListener('online', handleOnline);
    return () => window.removeEventListener('online', handleOnline);
  }, []);

  // 认证状态由 WebSocketContext 作为显式输入传入。它比 localStorage 更早
  // 表达“未登录/初始化中”，也让登录和登出成为唯一的物理连接边界。
  useEffect(() => {
    if (isAuthenticated) {
      connect();
    } else {
      cleanup();
      setConnectionState('disconnected');
    }
    return cleanup;
  }, [isAuthenticated, connect, cleanup]);

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
