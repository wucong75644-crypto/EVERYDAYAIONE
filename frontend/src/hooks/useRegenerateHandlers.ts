/**
 * 重新生成处理器 Hook
 *
 * 提供统一的消息重新生成入口，使用 sendMessage 实现
 */

import { useCallback, useRef } from 'react';
import { type Message } from '../stores/useMessageStore';
import { sendMessage, determineMessageType, extractModelId, extractGenerationParams } from '../services/messageSender';
import { useWebSocketContext } from '../contexts/WebSocketContext';
import { toast } from 'react-hot-toast';
import { logger } from '../utils/logger';
import { chatImageService } from '../services/chatImage';

interface RegenerateHandlersOptions {
  conversationId: string | null;
  setMessages: (updater: Message[] | ((prev: Message[]) => Message[])) => void;
}

export function useRegenerateHandlers(options: RegenerateHandlersOptions) {
  const { conversationId, setMessages } = options;
  const replayRequests = useRef(new Map<string, string>());

  // 获取 WebSocket 上下文
  const { subscribeTaskWithMapping } = useWebSocketContext();

  const replayFrozenImage = useCallback(async (message: Message) => {
    const sourceId = message.generation_params?.task_id;
    if (typeof sourceId !== 'string' || !conversationId) throw new Error('图片缺少原始任务身份');
    const key = `chat-image-replay:${conversationId}:${sourceId}`;
    const requestId = replayRequests.current.get(key) || sessionStorage.getItem(key) || crypto.randomUUID();
    replayRequests.current.set(key, requestId);
    sessionStorage.setItem(key, requestId);
    // Preserve this request ID after a lost HTTP response. The next click
    // recovers its receipt instead of creating a second paid generation.
    const accepted = await chatImageService.replay(sourceId, requestId);
    replayRequests.current.delete(key);
    sessionStorage.removeItem(key);
    setMessages(previous => [...previous, {
      id: accepted.message_id, conversation_id: conversationId, role: 'assistant',
      status: 'pending', content: [{ type: 'image', url: null }], created_at: new Date().toISOString(),
      generation_params: { origin: 'chat_image', task_id: accepted.task_id, type: 'image',
        num_images: 1, source_task_id: sourceId, submission_state: accepted.submission_state },
    }]);
    subscribeTaskWithMapping(accepted.task_id, conversationId);
  }, [conversationId, setMessages, subscribeTaskWithMapping]);

  // 统一重新生成入口
  const handleRegenerate = useCallback(
    async (targetMessage: Message, userMessage: Message) => {
      if (!conversationId) return;

      try {
        if (targetMessage.generation_params?.origin === 'chat_image') {
          await replayFrozenImage(targetMessage);
          return;
        }
        // 判断消息类型
        const type = determineMessageType(targetMessage);

        // 判断操作类型：错误消息重试，成功消息重新生成
        const operation = targetMessage.status === 'failed' || targetMessage.is_error
          ? 'retry'
          : 'regenerate';

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
        logger.error('regenerate', 'Regenerate failed', error);
        toast.error(error instanceof Error ? error.message : '重新生成失败');
      }
    },
    [conversationId, subscribeTaskWithMapping, replayFrozenImage]
  );

  // 单图重新生成
  const handleRegenerateSingle = useCallback(
    async (targetMessage: Message, imageIndex: number, userMessage: Message) => {
      if (!conversationId) return;

      try {
        if (targetMessage.generation_params?.origin === 'chat_image') {
          await replayFrozenImage(targetMessage);
          return;
        }
        const modelId = extractModelId(targetMessage);
        const originalParams = extractGenerationParams(targetMessage);

        await sendMessage({
          conversationId,
          content: userMessage.content,
          generationType: determineMessageType(targetMessage) === 'image_ecom' ? 'image_ecom' : 'image',
          model: modelId,
          operation: 'regenerate_single',
          originalMessageId: targetMessage.id,
          subscribeTask: subscribeTaskWithMapping,
          params: {
            ...originalParams,
            image_index: imageIndex,
          },
        });
      } catch (error) {
        logger.error('regenerate', 'Regenerate single failed', error);
        toast.error(error instanceof Error ? error.message : '重新生成失败');
      }
    },
    [conversationId, subscribeTaskWithMapping, replayFrozenImage]
  );

  return { handleRegenerate, handleRegenerateSingle };
}
