/**
 * 重新生成处理器 Hook
 *
 * 提供统一的消息重新生成入口，自动判断消息类型和失败/成功策略
 */

import { useCallback } from 'react';
import type { Message } from '../services/message';
import type { UnifiedModel } from '../constants/models';
import { useRegenerateAsNewMessage } from './regenerate/useRegenerateAsNewMessage';
import { regenerateMessage } from '../utils/regenerate';

interface RegenerateHandlersOptions {
  conversationId: string | null;
  conversationTitle: string;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  scrollToBottom: (smooth?: boolean) => void;
  onMessageUpdate?: (newLastMessage: string) => void;
  resetRegeneratingState: () => void;
  setRegeneratingId: (id: string | null) => void;
  setIsRegeneratingAI: (value: boolean) => void;
  modelId?: string | null;
  selectedModel?: UnifiedModel | null;
  userScrolledAway: boolean;
  onMediaTaskSubmitted?: () => void;
}

export function useRegenerateHandlers(options: RegenerateHandlersOptions) {
  const {
    conversationId,
    conversationTitle,
    setMessages,
    scrollToBottom,
    onMessageUpdate,
    resetRegeneratingState,
    setRegeneratingId,
    setIsRegeneratingAI,
    modelId,
    selectedModel,
    userScrolledAway,
    onMediaTaskSubmitted,
  } = options;

  // 聊天成功重新生成处理器（涉及流式处理）
  const handleChatAsNew = useRegenerateAsNewMessage({
    conversationId,
    modelId,
    selectedModel,
    userScrolledAway,
    scrollToBottom,
    setMessages,
    setRegeneratingId,
    setIsRegeneratingAI,
    onMessageUpdate,
    resetRegeneratingState,
  });

  // 统一重新生成入口
  const handleRegenerate = useCallback(
    async (targetMessage: Message, userMessage: Message) => {
      if (!conversationId) return;

      await regenerateMessage({
        messageId: targetMessage.id,
        conversationId,
        targetMessage,
        userMessage,
        generationParams: targetMessage.generation_params || undefined,
        conversationTitle,
        setMessages,
        setRegeneratingId,
        setIsRegeneratingAI,
        scrollToBottom,
        userScrolledAway,
        resetRegeneratingState,
        modelId,
        selectedModel,
        onSuccess: (msg) => onMessageUpdate?.(msg.content),
        onMessageUpdate,
        onMediaTaskSubmitted,
        handleChatAsNew,
      });
    },
    [
      conversationId,
      conversationTitle,
      setMessages,
      setRegeneratingId,
      setIsRegeneratingAI,
      scrollToBottom,
      userScrolledAway,
      resetRegeneratingState,
      modelId,
      selectedModel,
      onMessageUpdate,
      onMediaTaskSubmitted,
      handleChatAsNew,
    ]
  );

  return { handleRegenerate };
}
