/**
 * 统一媒体消息发送器
 *
 * 合并图片/视频发送逻辑，消除重复代码
 * 负责：用户消息创建 + 乐观更新 + 调用核心生成逻辑
 */

import { createMediaTimestamps, createMediaOptimisticPair } from '../../utils/messageFactory';
import { createMessage } from '../message';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import { executeImageGenerationCore, executeVideoGenerationCore } from './mediaGenerationCore';
import { logger } from '../../utils/logger';
import type { ImageSenderParams, VideoSenderParams } from './types';

export type MediaSenderParams = ImageSenderParams | VideoSenderParams;

export async function sendMediaMessage(params: MediaSenderParams): Promise<void> {
  const {
    type,
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
  const loadingText = type === 'image' ? '图片生成中...' : '视频生成中...';
  const { userMessage, placeholder } = createMediaOptimisticPair(
    conversationId,
    content,
    imageUrl ?? null,
    loadingText,
    timestamps
  );

  // 2. 触发乐观更新
  onMessagePending(userMessage);
  onMessagePending(placeholder);

  // 3. 保存真实用户消息
  try {
    const realUserMessage = await createMessage(conversationId, {
      role: 'user',
      content,
      image_url: imageUrl ?? null,
      created_at: timestamps.userTimestamp,
    });
    onMessagePending(realUserMessage);
  } catch (error) {
    logger.error('media:sender', '保存用户消息失败', error, { conversationId, type });

    // 清理乐观消息（用户消息 + 占位符）
    const { removeOptimisticMessage, setGenerating } = useConversationRuntimeStore.getState();
    removeOptimisticMessage(conversationId, userMessage.id);
    removeOptimisticMessage(conversationId, timestamps.tempPlaceholderId);
    setGenerating(conversationId, false);

    // 向上抛出错误，让 InputArea 捕获并显示错误状态
    throw new Error('发送失败，请重试');
  }

  // 4. 调用对应的核心生成逻辑
  if (type === 'image') {
    await executeImageGenerationCore({
      conversationId,
      prompt: content,
      imageUrl,
      modelId,
      generationParams: generationParams as ImageSenderParams['generationParams'],
      conversationTitle,
      messageTimestamp: timestamps.placeholderTimestamp,
      placeholderId: timestamps.tempPlaceholderId,
      callbacks: {
        onSuccess: (savedMessage) => {
          replaceMediaPlaceholder(conversationId, timestamps.tempPlaceholderId, savedMessage);
          onMessageSent(savedMessage);
        },
        onError: (errorMessage) => {
          replaceMediaPlaceholder(conversationId, timestamps.tempPlaceholderId, errorMessage);
          onMessageSent(errorMessage);
        },
        onTaskSubmitted: onMediaTaskSubmitted,
      },
    });
  } else {
    await executeVideoGenerationCore({
      conversationId,
      prompt: content,
      imageUrl,
      modelId,
      generationParams: generationParams as VideoSenderParams['generationParams'],
      conversationTitle,
      messageTimestamp: timestamps.placeholderTimestamp,
      placeholderId: timestamps.tempPlaceholderId,
      callbacks: {
        onSuccess: (savedMessage) => {
          replaceMediaPlaceholder(conversationId, timestamps.tempPlaceholderId, savedMessage);
          onMessageSent(savedMessage);
        },
        onError: (errorMessage) => {
          replaceMediaPlaceholder(conversationId, timestamps.tempPlaceholderId, errorMessage);
          onMessageSent(errorMessage);
        },
        onTaskSubmitted: onMediaTaskSubmitted,
      },
    });
  }
}
