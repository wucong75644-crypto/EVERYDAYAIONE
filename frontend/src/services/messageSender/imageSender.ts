/**
 * 图片消息发送器
 * 负责：用户消息创建 + 乐观更新 + 调用核心生成逻辑
 *
 * @deprecated 使用 mediaSender.ts 的 sendMediaMessage 替代
 */

import { createMessage } from '../message';
import { createMediaTimestamps, createMediaOptimisticPair } from '../../utils/messageFactory';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import { executeImageGenerationCore } from './mediaGenerationCore';
import type { ImageSenderParams } from './types';

export async function sendImageMessage(params: ImageSenderParams): Promise<void> {
  const {
    conversationId,
    content,
    imageUrl,
    modelId,
    generationParams,
    conversationTitle = '',
    callbacks,
  } = params;

  const { onMessagePending, onMessageSent, onMediaTaskSubmitted } = callbacks;
  const { replaceMediaPlaceholder } = useConversationRuntimeStore.getState();

  // 1. 创建时间戳和乐观消息对
  const timestamps = createMediaTimestamps();
  const { tempPlaceholderId, userTimestamp, placeholderTimestamp } = timestamps;

  const { userMessage, placeholder } = createMediaOptimisticPair(
    conversationId,
    content,
    imageUrl ?? null,
    '图片生成中...',
    timestamps
  );
  onMessagePending(userMessage);
  onMessagePending(placeholder);

  // 2. 保存用户消息
  try {
    const realUserMessage = await createMessage(conversationId, {
      content,
      role: 'user',
      image_url: imageUrl ?? null,
      created_at: userTimestamp,
    });
    onMessagePending(realUserMessage);
  } catch (error) {
    console.error('保存用户消息失败:', error);
  }

  // 3. 调用核心生成逻辑
  await executeImageGenerationCore({
    conversationId,
    prompt: content,
    imageUrl,
    modelId,
    generationParams,
    conversationTitle,
    messageTimestamp: placeholderTimestamp,
    placeholderId: tempPlaceholderId,
    callbacks: {
      onSuccess: (savedMessage) => {
        replaceMediaPlaceholder(conversationId, tempPlaceholderId, savedMessage);
        onMessageSent(savedMessage);
      },
      onError: (errorMessage) => {
        replaceMediaPlaceholder(conversationId, tempPlaceholderId, errorMessage);
        onMessageSent(errorMessage);
      },
      onTaskSubmitted: onMediaTaskSubmitted,
    },
  });
}
