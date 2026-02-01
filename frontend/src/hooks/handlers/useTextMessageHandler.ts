/**
 * 文本消息处理 Hook
 * 负责聊天消息的发送和流式响应
 */

import { type UnifiedModel } from '../../constants/models';
import { type Message } from '../../services/message';
import { sendChatMessage } from '../../services/messageSender';

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
    imageUrl: string | null = null,
    clientRequestId?: string,
    skipOptimisticUpdate: boolean = false
  ) => {
    await sendChatMessage({
      type: 'chat',
      conversationId: currentConversationId,
      content: messageContent,
      imageUrl,
      modelId: selectedModel.id,
      thinkingEffort,
      deepThinkMode,
      clientRequestId,
      skipOptimisticUpdate,
      callbacks: {
        onMessagePending,
        onMessageSent,
        onStreamContent,
        onStreamStart,
      },
    });
  };

  return { handleChatMessage };
}
