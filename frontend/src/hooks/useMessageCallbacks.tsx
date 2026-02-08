/**
 * 消息回调处理 Hook
 *
 * 职责：
 * - handleMessagePending：乐观更新用户消息
 * - handleMessageSent：处理 AI 回复完成
 * - 管理 conversationOptimisticUpdate 状态
 *
 * 注意：流式内容处理已迁移到 WebSocketContext（chat_chunk 消息）
 */

import { useState, useCallback, useRef, useLayoutEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMessageStore, type Message, getTextContent } from '../stores/useMessageStore';
import { useAuthStore } from '../stores/useAuthStore';
import { messageCoordinator } from '../utils/messageCoordinator';
import { tabSync } from '../utils/tabSync';
import toast from 'react-hot-toast';

/** Hook 参数接口 */
export interface UseMessageCallbacksParams {
  conversationTitle: string;
  currentConversationId: string | null;
  /** MessageArea 的 setMessages 兼容层（用于统一缓存写入） */
  setMessages?: (updater: Message[] | ((prev: Message[]) => Message[])) => void;
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
  conversationTitle,
  currentConversationId,
  setMessages,
}: UseMessageCallbacksParams): UseMessageCallbacksReturn {
  const navigate = useNavigate();
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
    startChatTask,
    completeChatTask,
    failChatTask,
    markNotificationRead,
    clearRecentlyCompleted,
    addErrorMessage,
    completeStreaming,
    appendMessage,
  } = useMessageStore();

  // 消息开始发送（乐观更新）
  const handleMessagePending = useCallback(
    (message: Message) => {
      const messageConversationId = message.conversation_id;

      // 启动任务追踪（所有对话都追踪，仅用户消息）
      if (messageConversationId && message.role === 'user') {
        startChatTask(messageConversationId, conversationTitle);
      }

      // 注意：RuntimeStore 消息添加已由 sendUnifiedMessage 处理，这里不再重复添加
      // sendUnifiedMessage 中调用了 runtimeStore.addOptimisticUserMessage()

      // 侧边栏乐观更新（只有当前对话）
      // 注意：只在临时消息（temp-）或占位符（streaming-）时更新
      // 真实用户消息返回时不更新，避免覆盖已完成任务的"图片已生成完成"状态
      const shouldUpdateSidebar =
        message.id.startsWith('temp-') || message.id.startsWith('streaming-');

      if (shouldUpdateSidebar && messageConversationId === currentConversationIdRef.current) {
        setConversationOptimisticUpdate({
          conversationId: currentConversationIdRef.current,
          lastMessage: getTextContent(message),
        });
      }
    },
    [
      conversationTitle,
      startChatTask,
    ]
  );

  // 消息发送成功（接收 AI 回复）
  const handleMessageSent = useCallback(
    (aiMessage?: Message | null) => {
      const messageConversationId = aiMessage?.conversation_id;

      // 完成任务追踪
      if (messageConversationId) {
        if (aiMessage?.is_error) {
          failChatTask(messageConversationId);
        } else {
          // 先标记未读，再完成任务（保持与原实现一致的顺序）
          messageCoordinator.markConversationUnread(messageConversationId);
          completeChatTask(messageConversationId);
        }
      }

      // 完成流式生成
      if (messageConversationId) {
        // 如果是错误消息，先添加错误消息再完成 streaming
        if (aiMessage && aiMessage.is_error) {
          addErrorMessage(messageConversationId, aiMessage);
          // 广播聊天失败事件给其他标签页
          tabSync.broadcast('chat_failed', {
            conversationId: messageConversationId,
            messageId: aiMessage.id,
          });
        } else if (aiMessage && (aiMessage.image_url || aiMessage.video_url)) {
          // 媒体消息完成：通过 setMessages 兼容层写入缓存（确保切换对话后秒显）
          if (setMessages) {
            setMessages((prev) => [...prev, aiMessage]);
          } else {
            // Fallback: 如果 setMessages 未传入，直接写入缓存
            appendMessage(messageConversationId, aiMessage);
          }

          // 更新侧边栏显示
          setConversationOptimisticUpdate({
            conversationId: messageConversationId,
            lastMessage: getTextContent(aiMessage),
          });
        } else if (aiMessage) {
          // 普通聊天流式生成完成
          completeStreaming(messageConversationId);
          // 通过 setMessages 兼容层写入缓存（确保切换对话后秒显）
          if (setMessages) {
            setMessages((prev) => [...prev, aiMessage]);
          } else {
            // Fallback: 如果 setMessages 未传入，直接写入缓存
            appendMessage(messageConversationId, aiMessage);
          }
          // 广播聊天完成事件给其他标签页
          tabSync.broadcast('chat_completed', {
            conversationId: messageConversationId,
            messageId: aiMessage.id,
          });
        }

        // 用户正在查看当前对话，清除闪烁状态
        if (messageConversationId === currentConversationIdRef.current) {
          clearRecentlyCompleted(messageConversationId);
        }
      }

      // 如果是其他对话的消息完成，显示通知
      if (
        messageConversationId &&
        messageConversationId !== currentConversationIdRef.current &&
        aiMessage &&
        !aiMessage.is_error
      ) {
        toast.success(
          (t) => (
            <span
              className="cursor-pointer"
              onClick={() => {
                toast.dismiss(t.id);
                markNotificationRead(messageConversationId);
                navigate(`/chat/${messageConversationId}`);
              }}
            >
              对话任务已完成，点击查看
            </span>
          ),
          { duration: 5000 }
        );
      }

      refreshUser();
    },
    [
      refreshUser,
      completeChatTask,
      failChatTask,
      markNotificationRead,
      clearRecentlyCompleted,
      navigate,
      addErrorMessage,
      completeStreaming,
      appendMessage,
      setMessages,
    ]
  );

  return {
    handleMessagePending,
    handleMessageSent,
    conversationOptimisticUpdate,
    setConversationOptimisticUpdate,
  };
}
