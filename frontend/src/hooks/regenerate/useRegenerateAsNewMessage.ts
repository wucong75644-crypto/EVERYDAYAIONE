/**
 * 成功消息新增对话
 * 策略 B：在消息列表末尾添加新的用户消息+AI回复
 */

import { useCallback, useRef } from 'react';
import { sendMessageStream, type Message } from '../../services/message';
import toast from 'react-hot-toast';
import type { Message as CacheMessage, MessageCacheEntry } from '../../stores/useChatStore';
import { createTempMessagePair } from '../../utils/messageFactory';
import type { UnifiedModel } from '../../constants/models';

interface UseRegenerateAsNewMessageOptions {
  conversationId: string | null;
  modelId?: string | null;
  selectedModel?: UnifiedModel | null;
  userScrolledAway: boolean;
  scrollToBottom: (smooth?: boolean) => void;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  setRegeneratingId: (id: string | null) => void;
  setIsRegeneratingAI: (value: boolean) => void;
  getCachedMessages: (conversationId: string) => MessageCacheEntry | null;
  updateCachedMessages: (conversationId: string, messages: CacheMessage[], hasMore?: boolean) => void;
  toStoreMessage: (msg: Message) => CacheMessage;
  onMessageUpdate?: (newLastMessage: string) => void;
  resetRegeneratingState: () => void;
}

export function useRegenerateAsNewMessage({
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
}: UseRegenerateAsNewMessageOptions) {
  const regeneratingContentRef = useRef<string>('');

  return useCallback(
    async (userMessage: Message) => {
      if (!conversationId) return;

      const { tempUserMessage, tempAiMessage, tempUserId, newStreamingId } = createTempMessagePair(
        conversationId, userMessage, ''
      );

      setRegeneratingId(newStreamingId);
      setIsRegeneratingAI(true);
      regeneratingContentRef.current = '';
      setMessages((prev) => [...prev, tempUserMessage, tempAiMessage]);
      scrollToBottom();

      const chatModelId = modelId || selectedModel?.id || 'gemini-3-flash';

      await sendMessageStream(
        conversationId,
        { content: userMessage.content, model_id: chatModelId },
        {
          onUserMessage: (realUser: Message) => {
            setMessages((prev) => prev.map((m) => (m.id === tempUserId ? realUser : m)));
          },
          onContent: (content: string) => {
            regeneratingContentRef.current += content;
            setMessages((prev) =>
              prev.map((m) => (m.id === newStreamingId ? { ...m, content: regeneratingContentRef.current } : m))
            );
            if (!userScrolledAway) scrollToBottom();
          },
          onDone: (finalMessage: Message | null) => {
            if (finalMessage) {
              setMessages((prev) => {
                const updated = prev.map((m) => (m.id === newStreamingId ? finalMessage : m));
                queueMicrotask(() => {
                  const cached = getCachedMessages(conversationId!);
                  if (cached) updateCachedMessages(conversationId!, updated.map(toStoreMessage), cached.hasMore);
                });
                return updated;
              });
              if (onMessageUpdate) onMessageUpdate(finalMessage.content);
            }
            resetRegeneratingState();
          },
          onError: (error: string) => {
            setMessages((prev) => prev.filter((m) => m.id !== tempUserId && m.id !== newStreamingId));
            resetRegeneratingState();
            toast.error(`重新生成失败: ${error}`);
          },
        }
      );
    },
    [conversationId, modelId, selectedModel, userScrolledAway, scrollToBottom, getCachedMessages, updateCachedMessages, toStoreMessage, onMessageUpdate, resetRegeneratingState, setMessages, setRegeneratingId, setIsRegeneratingAI]
  );
}
