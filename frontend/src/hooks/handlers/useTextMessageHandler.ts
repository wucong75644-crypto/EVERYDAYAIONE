/**
 * 文本消息处理 Hook
 * 负责聊天消息的发送和流式响应
 */

import { type UnifiedModel } from '../../constants/models';
import { sendMessageStream, type Message } from '../../services/message';
import { createOptimisticUserMessage, createErrorMessage } from '../../utils/messageFactory';
import { useChatStore } from '../../stores/useChatStore';

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
    // 如果未跳过乐观更新，创建并显示临时消息
    if (!skipOptimisticUpdate) {
      const optimisticUserMessage = createOptimisticUserMessage(
        messageContent,
        currentConversationId,
        imageUrl,
        undefined,
        clientRequestId
      );
      onMessagePending(optimisticUserMessage);
    }

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
          client_request_id: clientRequestId,  // 传递 client_request_id 到后端
        },
        {
          onUserMessage: (userMessage: Message) => {
            // 收到后端确认的用户消息，根据 client_request_id 替换临时消息
            if (userMessage.client_request_id) {
              // 转换为 Store 的 Message 类型（字段名映射）
              const storeMessage = {
                id: userMessage.id,
                role: userMessage.role as 'user' | 'assistant',  // 类型断言（用户消息只会是 user 或 assistant）
                content: userMessage.content,
                imageUrl: userMessage.image_url || undefined,
                videoUrl: userMessage.video_url || undefined,
                createdAt: userMessage.created_at,
                client_request_id: userMessage.client_request_id,
                status: userMessage.status,
              };
              useChatStore.getState().replaceOptimisticMessage(
                currentConversationId,
                userMessage.client_request_id,
                storeMessage
              );
            } else {
              // 兼容旧逻辑（没有 client_request_id 时，直接添加）
              onMessagePending(userMessage);
            }
          },
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
