/**
 * 消息回调处理 Hook
 *
 * 职责：
 * - handleMessagePending：乐观更新用户消息
 * - handleMessageSent：处理 AI 回复完成
 * - 管理 conversationOptimisticUpdate 状态
 *
 * 注意：流式内容处理已迁移到 WebSocketContext（message_chunk 消息）
 */

import { useState, useCallback, useRef, useLayoutEffect } from 'react';
import { useMessageStore, type Message, getTextContent } from '../stores/useMessageStore';
import { useAuthStore } from '../stores/useAuthStore';
import { tabSync } from '../utils/tabSync';

/** Hook 参数接口 */
export interface UseMessageCallbacksParams {
  currentConversationId: string | null;
}

/** 侧边栏乐观更新状态 */
export interface ConversationOptimisticUpdate {
  conversationId: string;
  lastMessage: string;
}

/** Hook 返回接口 */
export interface UseMessageCallbacksReturn {
  /** 消息开始发送（乐观更新） */
  handleMessagePending: (message: Message) => void;
  /** 消息发送成功（接收 AI 回复） */
  handleMessageSent: (aiMessage?: Message | null) => void;
  /** 侧边栏乐观更新状态 */
  conversationOptimisticUpdate: ConversationOptimisticUpdate | null;
  /** 设置侧边栏乐观更新（供外部使用，如删除消息） */
  setConversationOptimisticUpdate: React.Dispatch<React.SetStateAction<ConversationOptimisticUpdate | null>>;
}

/**
 * 消息回调处理 Hook
 *
 * 提取自 Chat.tsx 的消息处理逻辑，包括：
 * - 乐观更新用户消息
 * - 处理 AI 回复完成
 * - 流式内容处理
 * - 侧边栏状态同步
 */
export function useMessageCallbacks({
  currentConversationId,
}: UseMessageCallbacksParams): UseMessageCallbacksReturn {
  const { refreshUser } = useAuthStore();

  // 对话列表乐观更新（发送消息时立即将对话移到最前）
  const [conversationOptimisticUpdate, setConversationOptimisticUpdate] =
    useState<ConversationOptimisticUpdate | null>(null);

  // 使用 ref 保存最新的 currentConversationId（避免 useCallback 闭包陷阱）
  const currentConversationIdRef = useRef<string | null>(null);

  // 使用 useLayoutEffect 同步更新 ref，避免在渲染期间访问
  useLayoutEffect(() => {
    currentConversationIdRef.current = currentConversationId;
  }, [currentConversationId]);

  // MessageStore actions
  const {
    clearRecentlyCompleted,
    addErrorMessage,
  } = useMessageStore();

  // 消息开始发送（乐观更新）
  // 路径协议:任务追踪由后端 task_limit_service 做单一事实来源,前端只做 UI 更新
  const handleMessagePending = useCallback(
    (message: Message) => {
      const messageConversationId = message.conversation_id;

      // 注意：消息添加已由 sendMessage 处理，这里不再重复添加
      // sendMessage 中调用了 messageStore.addMessage()

      // 侧边栏乐观更新（只有当前对话）
      // 注意：只在临时消息或流式占位符时更新
      // 真实用户消息返回时不更新，避免覆盖已完成任务的"图片已生成完成"状态
      const shouldUpdateSidebar =
        message.id.startsWith('temp-') || message.status === 'streaming';

      if (shouldUpdateSidebar && messageConversationId === currentConversationIdRef.current) {
        setConversationOptimisticUpdate({
          conversationId: currentConversationIdRef.current,
          lastMessage: getTextContent(message),
        });
      }
    },
    [],
  );

  // 消息发送失败处理（仅处理发送错误，正常完成由 WebSocketContext 处理）
  const handleMessageSent = useCallback(
    (aiMessage?: Message | null) => {
      const messageConversationId = aiMessage?.conversation_id;

      // 错误处理（现在只处理发送错误，正常完成已由 WebSocket 处理）
      if (messageConversationId && aiMessage?.is_error) {
        // 添加错误消息
        addErrorMessage(messageConversationId, aiMessage);

        // 广播聊天失败事件给其他标签页
        tabSync.broadcast('chat_failed', {
          conversationId: messageConversationId,
          messageId: aiMessage.id,
        });

        // 用户正在查看当前对话，清除闪烁状态
        if (messageConversationId === currentConversationIdRef.current) {
          clearRecentlyCompleted(messageConversationId);
        }
      }

      // 刷新用户信息（更新积分余额）
      refreshUser();
    },
    [
      refreshUser,
      addErrorMessage,
      clearRecentlyCompleted,
    ]
  );

  return {
    handleMessagePending,
    handleMessageSent,
    conversationOptimisticUpdate,
    setConversationOptimisticUpdate,
  };
}
