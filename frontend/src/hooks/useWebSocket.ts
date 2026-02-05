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

import { useEffect, useRef, useCallback, useState } from 'react';
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
const RECONNECT_INTERVAL_MAX = 30000; // 最大重连间隔
const MAX_RECONNECT_ATTEMPTS = 20;

// === 消息类型 ===

export type WSMessageType =
  | 'chat_start'
  | 'chat_chunk'
  | 'chat_done'
  | 'chat_error'
  | 'task_status'
  | 'task_progress'
  | 'credits_changed'
  | 'notification'
  | 'ping'
  | 'pong'
  | 'subscribe'
  | 'unsubscribe'
  | 'subscribed'
  | 'server_restarting'
  | 'error';

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
          logger.error('[WebSocket] Handler error:', error);
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
    logger.info('[WebSocket] Server restarting, will reconnect with jitter');
    cleanup();

    // 增加随机抖动（0-5秒），错开重连峰值
    const jitter = Math.random() * 5000;
    reconnectAttemptsRef.current = 0; // 重置重连计数

    reconnectTimeoutRef.current = setTimeout(() => {
      connect();
    }, jitter);
  }, [cleanup]);

  // 连接 WebSocket
  const connect = useCallback(() => {
    const token = getToken();
    if (!token || !isAuthenticated()) {
      logger.info('[WebSocket] Not authenticated, skip connection');
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

    const wsUrl = `${getWebSocketUrl()}?token=${encodeURIComponent(token)}`;
    logger.info('[WebSocket] Connecting to', wsUrl.replace(/token=.*/, 'token=***'));

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      logger.info('[WebSocket] Connected');
      setConnectionState('connected');
      reconnectAttemptsRef.current = 0;
      startHeartbeat();
    };

    ws.onclose = (event) => {
      logger.info(`[WebSocket] Closed: ${event.code} ${event.reason}`);
      setConnectionState('disconnected');

      if (heartbeatIntervalRef.current) {
        clearInterval(heartbeatIntervalRef.current);
        heartbeatIntervalRef.current = null;
      }

      // 非正常关闭且不是主动清理，尝试重连
      if (
        event.code !== 1000 &&
        !isCleaningUpRef.current &&
        reconnectAttemptsRef.current < MAX_RECONNECT_ATTEMPTS &&
        isAuthenticated()
      ) {
        setConnectionState('reconnecting');
        const delay = getReconnectDelay();
        reconnectAttemptsRef.current++;
        logger.info(`[WebSocket] Reconnecting in ${delay}ms (attempt ${reconnectAttemptsRef.current})`);

        reconnectTimeoutRef.current = setTimeout(() => {
          connect();
        }, delay);
      }
    };

    ws.onerror = (error) => {
      logger.error('[WebSocket] Error:', error);
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
        logger.error('[WebSocket] Message parse error:', error);
      }
    };
  }, [cleanup, startHeartbeat, getReconnectDelay, dispatchMessage, handleServerRestart]);

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

  // 订阅任务
  const subscribeTask = useCallback((taskId: string, lastIndex: number = -1) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'subscribe',
          payload: { task_id: taskId, last_index: lastIndex },
          timestamp: Date.now(),
        })
      );
      logger.info(`[WebSocket] Subscribed to task: ${taskId}`);
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
      logger.info(`[WebSocket] Unsubscribed from task: ${taskId}`);
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

  // 监听认证状态变化
  useEffect(() => {
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === 'access_token') {
        if (e.newValue) {
          // Token 设置，尝试连接
          connect();
        } else {
          // Token 清除，断开连接
          cleanup();
          setConnectionState('disconnected');
        }
      }
    };

    window.addEventListener('storage', handleStorageChange);
    return () => window.removeEventListener('storage', handleStorageChange);
  }, [connect, cleanup]);

  // 自动连接
  useEffect(() => {
    if (isAuthenticated()) {
      connect();
    }
    return cleanup;
  }, [connect, cleanup]);

  return {
    connectionState,
    isConnected: connectionState === 'connected',
    subscribe,
    subscribeTask,
    unsubscribeTask,
    send,
  };
}
