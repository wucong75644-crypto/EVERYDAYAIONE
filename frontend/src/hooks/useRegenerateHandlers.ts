/**
 * 重新生成处理器 Hook（组合器）
 *
 * 封装所有消息重新生成逻辑（聊天、图片、视频），避免 MessageArea 代码膨胀
 * 各策略逻辑已提取到独立 Hook 文件
 */

import { useCallback } from 'react';
import type { Message, GenerationParams } from '../services/message';
import type { Message as CacheMessage, MessageCacheEntry } from '../stores/useChatStore';
import type { UnifiedModel } from '../constants/models';
import { useRegenerateFailedMessage } from './regenerate/useRegenerateFailedMessage';
import { useRegenerateAsNewMessage } from './regenerate/useRegenerateAsNewMessage';
import { executeImageRegeneration, executeVideoRegeneration } from '../utils/mediaRegeneration';

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
  getCachedMessages: (conversationId: string) => MessageCacheEntry | null;
  updateCachedMessages: (conversationId: string, messages: CacheMessage[], hasMore?: boolean) => void;
  toStoreMessage: (msg: Message) => CacheMessage;
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
    getCachedMessages,
    updateCachedMessages,
    toStoreMessage,
    onMediaTaskSubmitted,
  } = options;

  // 策略 A：失败消息原地重新生成
  const regenerateFailedMessage = useRegenerateFailedMessage({
    conversationId,
    userScrolledAway,
    scrollToBottom,
    setMessages,
    setRegeneratingId,
    setIsRegeneratingAI,
    getCachedMessages,
    updateCachedMessages,
    toStoreMessage,
    onMessageUpdate,
    resetRegeneratingState,
  });

  // 策略 B：成功消息新增对话
  const regenerateAsNewMessage = useRegenerateAsNewMessage({
    conversationId,
    modelId,
    selectedModel,
    userScrolledAway,
    scrollToBottom,
    setMessages,
    setRegeneratingId,
    setIsRegeneratingAI,
    getCachedMessages,
    updateCachedMessages,
    toStoreMessage,
    onMessageUpdate,
    resetRegeneratingState,
  });

  // 策略 C：图片消息重新生成
  const regenerateImageMessage = useCallback(
    async (userMessage: Message, originalGenerationParams?: GenerationParams | null) => {
      if (!conversationId) return;

      await executeImageRegeneration({
        conversationId,
        userMessage,
        originalGenerationParams,
        modelId,
        selectedModel,
        setMessages,
        scrollToBottom,
        setRegeneratingId,
        setIsRegeneratingAI,
        conversationTitle,
        onMessageUpdate,
        resetRegeneratingState,
        onMediaTaskSubmitted,
      });
    },
    [conversationId, modelId, selectedModel, setMessages, scrollToBottom, conversationTitle, onMessageUpdate, resetRegeneratingState, setRegeneratingId, setIsRegeneratingAI, onMediaTaskSubmitted]
  );

  // 策略 D：视频消息重新生成
  const regenerateVideoMessage = useCallback(
    async (userMessage: Message, originalGenerationParams?: GenerationParams | null) => {
      if (!conversationId) return;

      await executeVideoRegeneration({
        conversationId,
        userMessage,
        originalGenerationParams,
        modelId,
        selectedModel,
        setMessages,
        scrollToBottom,
        setRegeneratingId,
        setIsRegeneratingAI,
        conversationTitle,
        onMessageUpdate,
        resetRegeneratingState,
        onMediaTaskSubmitted,
      });
    },
    [conversationId, modelId, selectedModel, setMessages, scrollToBottom, conversationTitle, onMessageUpdate, resetRegeneratingState, setRegeneratingId, setIsRegeneratingAI, onMediaTaskSubmitted]
  );

  return {
    regenerateFailedMessage,
    regenerateAsNewMessage,
    regenerateImageMessage,
    regenerateVideoMessage,
  };
}
