/**
 * WebSocket Context
 *
 * 提供全局 WebSocket 连接，避免重复连接
 * 自动处理消息分发到相应的 Store
 * 支持聊天任务恢复（WebSocket 订阅模式）
 */

import { createContext, useContext, useEffect, useRef, type ReactNode } from 'react';
import {
  useWebSocket,
  type WSMessage,
  type WSMessageType,
  type ConnectionState,
} from '../hooks/useWebSocket';
import { useChatStore } from '../stores/useChatStore';
import { useTaskStore } from '../stores/useTaskStore';
import { useAuthStore } from '../stores/useAuthStore';
import { useConversationRuntimeStore } from '../stores/useConversationRuntimeStore';
import { logger } from '../utils/logger';
import { tabSync } from '../utils/tabSync';

// === Context 类型 ===

interface WebSocketContextValue {
  connectionState: ConnectionState;
  isConnected: boolean;
  subscribe: (type: WSMessageType, handler: (msg: WSMessage) => void) => () => void;
  subscribeTask: (taskId: string, lastIndex?: number) => void;
  unsubscribeTask: (taskId: string) => void;
  send: (message: Omit<WSMessage, 'timestamp'>) => void;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

// === Provider 组件 ===

interface WebSocketProviderProps {
  children: ReactNode;
}

export function WebSocketProvider({ children }: WebSocketProviderProps) {
  const ws = useWebSocket();

  // 跟踪已订阅的任务（防止重复订阅）
  const subscribedTasksRef = useRef<Set<string>>(new Set());

  // 设置全局消息处理
  useEffect(() => {
    const runtimeStore = useConversationRuntimeStore.getState();
    const chatStore = useChatStore.getState();

    // 处理聊天流式内容块
    const unsubChatChunk = ws.subscribe('chat_chunk', (msg) => {
      const { text } = msg.payload as { text: string; accumulated?: string };
      const conversationId = msg.conversation_id;

      if (!conversationId) return;

      logger.debug('ws:chat', 'chunk received', { taskId: msg.task_id, textLen: text.length });

      // 追加流式内容到 RuntimeStore
      runtimeStore.appendStreamingContent(conversationId, text);
    });

    // 处理聊天开始
    const unsubChatStart = ws.subscribe('chat_start', (msg) => {
      const { assistant_message_id } = msg.payload as { model: string; assistant_message_id: string };
      const conversationId = msg.conversation_id;

      if (!conversationId) return;

      logger.info('ws:chat', 'start received', { taskId: msg.task_id, assistantMessageId: assistant_message_id });

      // 开始流式消息（如果尚未开始）
      const state = runtimeStore.getState(conversationId);
      if (!state.streamingMessageId) {
        runtimeStore.startStreaming(conversationId, assistant_message_id);
      }
    });

    // 处理聊天完成
    const unsubChatDone = ws.subscribe('chat_done', (msg) => {
      const { message_id, content, credits_consumed } = msg.payload as {
        message_id: string;
        content: string;
        credits_consumed: number;
        model: string;
      };
      const conversationId = msg.conversation_id;
      const taskId = msg.task_id;

      if (!conversationId) return;

      logger.info('ws:chat', 'done received', { taskId, messageId: message_id, creditsConsumed: credits_consumed });

      // 完成流式消息
      runtimeStore.completeStreaming(conversationId);

      // 添加最终消息到缓存
      const finalMessage = {
        id: message_id,
        conversation_id: conversationId,
        role: 'assistant' as const,
        content,
        created_at: new Date().toISOString(),
        credits_cost: credits_consumed,
      };
      chatStore.appendMessage(conversationId, finalMessage);

      // 从订阅列表移除
      if (taskId) {
        subscribedTasksRef.current.delete(taskId);
      }

      // 广播给其他标签页
      tabSync.broadcast('chat_completed', { conversationId, messageId: message_id });
    });

    // 处理聊天错误
    const unsubChatError = ws.subscribe('chat_error', (msg) => {
      const { error } = msg.payload as { error: string; error_code?: string };
      const conversationId = msg.conversation_id;
      const taskId = msg.task_id;

      if (!conversationId) return;

      logger.error('ws:chat', 'error received', undefined, { taskId, error });

      // 完成流式消息
      runtimeStore.completeStreaming(conversationId);

      // 添加错误消息
      const errorMsg = {
        id: `error-${taskId || Date.now()}`,
        conversation_id: conversationId,
        role: 'assistant' as const,
        content: error || '生成失败，请重试',
        created_at: new Date().toISOString(),
        credits_cost: 0,
        is_error: true,
      };
      runtimeStore.addErrorMessage(conversationId, errorMsg);

      // 从订阅列表移除
      if (taskId) {
        subscribedTasksRef.current.delete(taskId);
      }

      // 广播给其他标签页
      tabSync.broadcast('chat_failed', { conversationId, taskId });
    });

    // 处理任务状态更新（图片/视频生成完成）
    const unsubTaskStatus = ws.subscribe('task_status', (msg) => {
      logger.info('ws:task', 'status received', { taskId: msg.task_id });

      const { status, error_message } = msg.payload as {
        status: string;
        media_type?: string;
        urls?: string[];
        error_message?: string;
      };

      const taskId = msg.task_id;
      if (!taskId) return;

      if (status === 'completed') {
        // 任务完成，更新 TaskStore（使用媒体任务方法）
        useTaskStore.getState().completeMediaTask(taskId);

        // 标记对话有新消息
        if (msg.conversation_id) {
          useChatStore.getState().markConversationUnread(msg.conversation_id);
        }
      } else if (status === 'failed') {
        // 任务失败
        useTaskStore.getState().failMediaTask(taskId);
        logger.error('ws:task', 'task failed', undefined, { taskId, error: error_message });
      }
    });

    // 处理积分变化
    const unsubCredits = ws.subscribe('credits_changed', (msg) => {
      const { credits } = msg.payload as { credits: number };
      logger.info('ws:credits', 'credits changed', { credits });

      const currentUser = useAuthStore.getState().user;

      if (currentUser) {
        useAuthStore.getState().setUser({
          ...currentUser,
          credits,
        });
      }
    });

    // 处理订阅确认
    const unsubSubscribed = ws.subscribe('subscribed', (msg) => {
      const { task_id } = msg.payload as { task_id: string };
      logger.info('ws:subscribe', 'subscribed confirmed', { taskId: task_id });
    });

    // 处理错误消息
    const unsubError = ws.subscribe('error', (msg) => {
      const { message } = msg.payload as { message: string };
      logger.error('ws:error', 'error received', undefined, { error: message });
    });

    return () => {
      unsubChatStart();
      unsubChatChunk();
      unsubChatDone();
      unsubChatError();
      unsubTaskStatus();
      unsubCredits();
      unsubSubscribed();
      unsubError();
    };
  }, [ws]);

  // WebSocket 连接成功后，自动订阅进行中的聊天任务
  useEffect(() => {
    if (!ws.isConnected) return;

    const subscribePendingChatTasks = async () => {
      try {
        // 动态导入避免循环依赖
        const { fetchPendingTasks } = await import('../utils/taskRestoration');
        const tasks = await fetchPendingTasks();

        // 筛选进行中的聊天任务
        const pendingChatTasks = tasks.filter(
          (task) => task.type === 'chat' && (task.status === 'pending' || task.status === 'running')
        );

        if (pendingChatTasks.length === 0) return;

        logger.info('ws:restore', 'subscribing pending chat tasks', { count: pendingChatTasks.length });

        const runtimeStore = useConversationRuntimeStore.getState();

        for (const task of pendingChatTasks) {
          // 避免重复订阅
          if (subscribedTasksRef.current.has(task.id)) continue;

          subscribedTasksRef.current.add(task.id);

          // 初始化 streaming 状态
          const streamingId = task.assistant_message_id || task.id;
          runtimeStore.startStreaming(task.conversation_id, streamingId);

          // 如果有累积内容，先显示
          if (task.accumulated_content) {
            runtimeStore.appendStreamingContent(task.conversation_id, task.accumulated_content);
          }

          // 通过 WebSocket 订阅任务
          ws.subscribeTask(task.id);
        }
      } catch (error) {
        logger.error('ws:restore', 'failed to subscribe pending tasks', error);
      }
    };

    subscribePendingChatTasks();
  }, [ws.isConnected, ws]);

  return <WebSocketContext.Provider value={ws}>{children}</WebSocketContext.Provider>;
}

// === Hook ===

export function useWebSocketContext(): WebSocketContextValue {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error('useWebSocketContext must be used within WebSocketProvider');
  }
  return context;
}

// 导出类型
export type { WebSocketContextValue };
