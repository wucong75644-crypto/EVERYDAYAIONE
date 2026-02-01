/**
 * 聊天成功消息新增对话
 * 在消息列表末尾添加新的用户消息+AI回复（流式）
 */

import { useCallback, useRef } from 'react';
import { type Message } from '../../services/message';
import toast from 'react-hot-toast';
import { createTempMessagePair } from '../../utils/messageFactory';
import type { UnifiedModel } from '../../constants/models';
import { sendChatMessage } from '../../services/messageSender';

interface UseRegenerateAsNewMessageOptions {
  conversationId: string | null;
  modelId?: string | null;
  selectedModel?: UnifiedModel | null;
  userScrolledAway: boolean;
  scrollToBottom: (smooth?: boolean) => void;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  setRegeneratingId: (id: string | null) => void;
  setIsRegeneratingAI: (value: boolean) => void;
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

      await sendChatMessage({
        type: 'chat',
        conversationId,
        content: userMessage.content,
        modelId: chatModelId,
        skipOptimisticUpdate: true,
        callbacks: {
          onMessagePending: (msg) => {
            // 用户消息确认时，替换临时用户消息
            setMessages((prev) => prev.map((m) => (m.id === tempUserId ? msg : m)));
          },
          onMessageSent: (aiMessage) => {
            if (aiMessage?.is_error) {
              // 错误消息，删除临时消息并显示 toast
              setMessages((prev) => prev.filter((m) => m.id !== tempUserId && m.id !== newStreamingId));
              toast.error(`重新生成失败: ${aiMessage.content}`);
            } else if (aiMessage) {
              // 成功消息，替换临时 AI 消息
              setMessages((prev) => prev.map((m) => (m.id === newStreamingId ? aiMessage : m)));
              if (onMessageUpdate) onMessageUpdate(aiMessage.content);
            }
            resetRegeneratingState();
          },
          onStreamContent: (text) => {
            regeneratingContentRef.current += text;
            setMessages((prev) =>
              prev.map((m) => (m.id === newStreamingId ? { ...m, content: regeneratingContentRef.current } : m))
            );
            if (!userScrolledAway) scrollToBottom();
          },
        },
      });
    },
    [conversationId, modelId, selectedModel, userScrolledAway, scrollToBottom, onMessageUpdate, resetRegeneratingState, setMessages, setRegeneratingId, setIsRegeneratingAI]
  );
}
