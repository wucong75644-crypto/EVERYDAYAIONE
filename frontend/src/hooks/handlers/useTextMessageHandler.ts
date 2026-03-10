/**
 * 文本消息处理 Hook
 * 负责聊天消息的发送和流式响应
 *
 * 使用统一的 sendMessage API
 */

import { type UnifiedModel } from '../../constants/models';
import { type Message } from '../../stores/useMessageStore';
import { sendMessage, createTextContent, createTextWithImages, createTextWithFiles, createErrorMessage } from '../../services/messageSender';
import { useWebSocketContext } from '../../contexts/WebSocketContext';
import { tabSync } from '../../utils/tabSync';
import { logger } from '../../utils/logger';

interface UseTextMessageHandlerParams {
  selectedModel: UnifiedModel;
  thinkingEffort?: 'minimal' | 'low' | 'medium' | 'high';
  deepThinkMode?: boolean;
  temperature?: number;
  topP?: number;
  topK?: number;
  maxOutputTokens?: number;
  onMessagePending: (message: Message) => void;
  onMessageSent: (aiMessage?: Message | null) => void;
}

export function useTextMessageHandler({
  selectedModel,
  thinkingEffort,
  deepThinkMode,
  temperature,
  topP,
  topK,
  maxOutputTokens,
  onMessagePending,
  onMessageSent,
}: UseTextMessageHandlerParams) {
  // 获取 WebSocket 订阅函数
  const { subscribeTaskWithMapping } = useWebSocketContext();

  const handleChatMessage = async (
    messageContent: string,
    currentConversationId: string,
    imageUrls: string[] | null = null,
    files: { url: string; name: string; mime_type: string; size: number }[] | null = null,
  ) => {
    try {
      // 构建 content（优先级：files > images > text）
      const content = files?.length
        ? createTextWithFiles(messageContent, imageUrls, files)
        : imageUrls?.length
          ? createTextWithImages(messageContent, imageUrls)
          : createTextContent(messageContent);

      // 立即触发侧边栏乐观更新（不等待 API 返回）
      onMessagePending({
        id: 'temp-' + Date.now(),
        conversation_id: currentConversationId,
        role: 'user',
        content,
        status: 'completed',
        created_at: new Date().toISOString(),
      } as Message);

      // 广播聊天开始事件给其他标签页
      tabSync.broadcast('chat_started', { conversationId: currentConversationId });

      // 调用统一发送器
      await sendMessage({
        conversationId: currentConversationId,
        content,
        generationType: 'chat',
        model: selectedModel.id,
        params: {
          thinking_effort: thinkingEffort,
          thinking_mode: deepThinkMode ? 'deep_think' : undefined,
          temperature,
          top_p: topP,
          top_k: topK,
          max_output_tokens: maxOutputTokens,
        },
        subscribeTask: subscribeTaskWithMapping,
      });

      // 注意：流式内容由 WebSocketContext 处理（message_chunk 消息）
      // 消息完成也由 WebSocket 推送触发状态更新

    } catch (error) {
      logger.error('chatHandler', 'Chat message failed', error);
      onMessageSent(createErrorMessage(currentConversationId, error, '发送失败'));
    }
  };

  return { handleChatMessage };
}
