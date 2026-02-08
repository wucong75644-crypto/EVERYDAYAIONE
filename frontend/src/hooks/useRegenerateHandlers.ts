/**
 * 重新生成处理器 Hook
 *
 * 提供统一的消息重新生成入口，使用 sendMessage 实现
 */

import { useCallback } from 'react';
import { type Message } from '../stores/useMessageStore';
import { sendMessage, determineMessageType, extractModelId, extractGenerationParams } from '../services/messageSender';
import { useWebSocketContext } from '../contexts/WebSocketContext';
import toast from 'react-hot-toast';

interface RegenerateHandlersOptions {
  conversationId: string | null;
  setMessages: (updater: Message[] | ((prev: Message[]) => Message[])) => void;
}

export function useRegenerateHandlers(options: RegenerateHandlersOptions) {
  const { conversationId } = options;

  // 获取 WebSocket 上下文
  const { subscribeTaskWithMapping } = useWebSocketContext();

  // 统一重新生成入口
  const handleRegenerate = useCallback(
    async (targetMessage: Message, userMessage: Message) => {
      if (!conversationId) return;

      try {
        // 判断消息类型
        const type = determineMessageType(targetMessage);

        // 判断操作类型：错误消息重试，成功消息重新生成
        const operation = targetMessage.is_error ? 'retry' : 'regenerate';

        // 提取模型 ID
        const modelId = extractModelId(targetMessage);

        // 构建 content
        const content = userMessage.content;

        // 提取原消息的生成参数（用于重新生成时保持一致）
        const originalParams = extractGenerationParams(targetMessage);

        // 调用统一发送器
        await sendMessage({
          conversationId,
          content,
          generationType: type,
          model: modelId,
          operation,
          originalMessageId: targetMessage.id,
          subscribeTask: subscribeTaskWithMapping,
          params: originalParams,
        });

        // 注：retry 的本地状态更新已在 sendMessage 内部处理
      } catch (error) {
        console.error('Regenerate failed:', error);
        toast.error(error instanceof Error ? error.message : '重新生成失败');
      }
    },
    [conversationId, subscribeTaskWithMapping]
  );

  return { handleRegenerate };
}
