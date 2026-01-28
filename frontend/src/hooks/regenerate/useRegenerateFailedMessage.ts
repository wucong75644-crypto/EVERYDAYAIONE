/**
 * 失败消息原地重新生成
 * 策略 A：清空错误内容，在原位置重新生成
 */

import { useCallback } from 'react';
import { regenerateMessageStream, type Message } from '../../services/message';
import toast from 'react-hot-toast';
import type { Message as CacheMessage, MessageCacheEntry } from '../../stores/useChatStore';

interface UseRegenerateFailedMessageOptions {
  conversationId: string | null;
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

export function useRegenerateFailedMessage({
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
}: UseRegenerateFailedMessageOptions) {
  return useCallback(
    async (messageId: string, targetMessage: Message) => {
      if (!conversationId) return;

      const regenConvId = conversationId;
      setRegeneratingId(messageId);
      setIsRegeneratingAI(true);
      const contentRef = { current: '' };

      setMessages((prev) =>
        prev.map((m) => (m.id === messageId ? { ...m, content: '', is_error: false } : m))
      );

      try {
        await regenerateMessageStream(conversationId, messageId, {
          onContent: (content: string) => {
            contentRef.current += content;
            setMessages((prev) => {
              if (conversationId !== regenConvId) return prev;
              return prev.map((m) =>
                m.id === messageId ? { ...m, content: contentRef.current, is_error: false } : m
              );
            });
            if (!userScrolledAway) scrollToBottom();
          },
          onDone: (finalMessage: Message | null) => {
            if (!finalMessage) return;
            setMessages((prev) => {
              if (conversationId !== regenConvId) return prev;
              const updated = prev.map((m) => (m.id === messageId ? finalMessage : m));
              queueMicrotask(() => {
                const cached = getCachedMessages(conversationId);
                if (cached) updateCachedMessages(conversationId, updated.map(toStoreMessage), cached.hasMore);
              });
              return updated;
            });
            resetRegeneratingState();
            if (onMessageUpdate) onMessageUpdate(finalMessage.content);
          },
          onError: (error: string) => {
            setMessages((prev) => prev.map((m) => (m.id === messageId ? targetMessage : m)));
            resetRegeneratingState();
            toast.error(`重试失败: ${error}`);
          },
        });
      } catch {
        setMessages((prev) => prev.map((m) => (m.id === messageId ? targetMessage : m)));
        resetRegeneratingState();
        toast.error('重新生成失败，请重试');
      }
    },
    [conversationId, userScrolledAway, scrollToBottom, getCachedMessages, updateCachedMessages, toStoreMessage, onMessageUpdate, resetRegeneratingState, setMessages, setRegeneratingId, setIsRegeneratingAI]
  );
}
