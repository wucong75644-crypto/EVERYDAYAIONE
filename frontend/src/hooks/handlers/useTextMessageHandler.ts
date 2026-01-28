/**
 * 文本消息处理 Hook
 * 负责聊天消息的发送和流式响应
 */

import { type UnifiedModel } from '../../constants/models';
import { sendMessageStream, type Message } from '../../services/message';
import { createOptimisticUserMessage, createErrorMessage } from '../../utils/messageFactory';

interface UseTextMessageHandlerParams {
  selectedModel: UnifiedModel;
  thinkingEffort?: 'minimal' | 'low' | 'medium' | 'high';
  deepThinkMode?: boolean;
  onMessagePending: (message: Message) => void;
  onMessageSent: (aiMessage?: Message | null) => void;
  onStreamContent?: (text: string, conversationId: string) => void;
  onStreamStart?: (conversationId: string, model: string) => void;
}

export function useTextMessageHandler({
  selectedModel,
  thinkingEffort,
  deepThinkMode,
  onMessagePending,
  onMessageSent,
  onStreamContent,
  onStreamStart,
}: UseTextMessageHandlerParams) {
  const handleChatMessage = async (
    messageContent: string,
    currentConversationId: string,
    imageUrl: string | null = null
  ) => {
    const optimisticUserMessage = createOptimisticUserMessage(
      messageContent,
      currentConversationId,
      imageUrl
    );
    onMessagePending(optimisticUserMessage);

    if (onStreamStart) onStreamStart(currentConversationId, selectedModel.id);

    try {
      await sendMessageStream(
        currentConversationId,
        {
          content: messageContent,
          model_id: selectedModel.id,
          image_url: imageUrl,
          thinking_effort: thinkingEffort,
          thinking_mode: deepThinkMode ? 'deep_think' : 'default',
        },
        {
          onUserMessage: (userMessage: Message) => onMessagePending(userMessage),
          onStart: () => {},
          onContent: (text: string) => {
            if (onStreamContent) onStreamContent(text, currentConversationId);
          },
          onDone: (assistantMessage: Message | null) => onMessageSent(assistantMessage ?? null),
          onError: (error: string) => {
            onMessageSent(createErrorMessage(currentConversationId, 'AI 响应错误', error));
          },
        }
      );
    } catch (error) {
      onMessageSent(createErrorMessage(currentConversationId, '发送失败', error));
    }
  };

  return { handleChatMessage };
}
