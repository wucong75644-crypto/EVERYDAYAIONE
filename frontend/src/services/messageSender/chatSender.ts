/**
 * 聊天消息发送器
 * 从 useTextMessageHandler 提取核心逻辑
 */

import { sendMessageStream } from '../message';
import {
  createOptimisticUserMessage,
  createErrorMessage,
  getIncrementalTimestampISO,
} from '../../utils/messageFactory';
import { useChatStore } from '../../stores/useChatStore';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import type { ChatSenderParams } from './types';

export async function sendChatMessage(params: ChatSenderParams): Promise<void> {
  const {
    conversationId,
    content,
    imageUrl,
    modelId,
    thinkingEffort,
    deepThinkMode,
    clientRequestId,
    skipOptimisticUpdate = false,
    callbacks,
  } = params;

  const { onMessagePending, onMessageSent, onStreamContent, onStreamStart } = callbacks;

  // 生成递增时间戳（确保用户消息排在 AI 占位符之前）
  const userMessageTimestamp = getIncrementalTimestampISO();

  // 1. 乐观更新（可跳过）
  if (!skipOptimisticUpdate) {
    const optimisticUserMessage = createOptimisticUserMessage(
      content,
      conversationId,
      imageUrl ?? null,
      userMessageTimestamp,  // 使用递增时间戳
      clientRequestId
    );
    onMessagePending(optimisticUserMessage);
  }

  if (onStreamStart) onStreamStart(conversationId, modelId);

  // 2. 发送流式请求
  try {
    await sendMessageStream(
      conversationId,
      {
        content,
        model_id: modelId,
        image_url: imageUrl ?? null,
        thinking_effort: thinkingEffort,
        thinking_mode: deepThinkMode ? 'deep_think' : 'default',
        client_request_id: clientRequestId,
        created_at: userMessageTimestamp,  // 传递给后端，确保存储的时间戳与乐观消息一致
      },
      {
        onUserMessage: (userMessage) => {
          if (userMessage.client_request_id) {
            // ✅ 修复测试3：先尝试更新ID，如果失败则直接写入缓存
            const chatStore = useChatStore.getState();
            chatStore.updateMessageId(
              conversationId,
              userMessage.client_request_id,
              userMessage.id
            );

            // ✅ 确保用户消息写入缓存（如果updateMessageId失败，直接追加）
            const cached = chatStore.messageCache.get(conversationId);
            const messageExists = cached?.messages.some(m => m.id === userMessage.id);
            if (!messageExists) {
              chatStore.appendMessage(conversationId, userMessage);
            }

            useConversationRuntimeStore.getState().updateMessageId(
              conversationId,
              userMessage.client_request_id,
              userMessage.id
            );
          } else {
            onMessagePending(userMessage);
          }
        },
        onStart: () => {},
        onContent: (text) => {
          if (onStreamContent) onStreamContent(text, conversationId);
        },
        onDone: (assistantMessage) => onMessageSent(assistantMessage ?? null),
        onError: (error) => {
          onMessageSent(createErrorMessage(conversationId, 'AI 响应错误', error));
        },
      }
    );
  } catch (error) {
    onMessageSent(createErrorMessage(conversationId, '发送失败', error));
  }
}
