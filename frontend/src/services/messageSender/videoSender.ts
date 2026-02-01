/**
 * 视频消息发送器
 * 负责：用户消息创建 + 乐观更新 + 调用核心生成逻辑
 *
 * @deprecated 使用 mediaSender.ts 的 sendMediaMessage 替代
 */

import { createMessage } from '../message';
import { createMediaTimestamps, createMediaOptimisticPair } from '../../utils/messageFactory';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import { executeVideoGenerationCore } from './mediaGenerationCore';
import { ALL_MODELS } from '../../constants/models';
import type { VideoSenderParams } from './types';

export async function sendVideoMessage(params: VideoSenderParams): Promise<void> {
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

  // 判断是否图生视频
  const modelConfig = ALL_MODELS.find((m) => m.id === modelId);
  const supportsI2V = modelConfig?.type === 'video' && modelConfig.capabilities.imageToVideo;
  const isImageToVideo = imageUrl && supportsI2V;

  // 1. 创建时间戳和乐观消息对
  const timestamps = createMediaTimestamps();
  const { tempPlaceholderId, userTimestamp, placeholderTimestamp } = timestamps;

  const { userMessage, placeholder } = createMediaOptimisticPair(
    conversationId,
    content,
    isImageToVideo ? imageUrl : null,
    '视频生成中...',
    timestamps
  );
  onMessagePending(userMessage);
  onMessagePending(placeholder);

  // 2. 保存用户消息
  try {
    const realUserMessage = await createMessage(conversationId, {
      content,
      role: 'user',
      image_url: isImageToVideo ? imageUrl : null,
      created_at: userTimestamp,
    });
    onMessagePending(realUserMessage);
  } catch (error) {
    console.error('保存用户消息失败:', error);
  }

  // 3. 调用核心生成逻辑
  await executeVideoGenerationCore({
    conversationId,
    prompt: content,
    imageUrl: isImageToVideo ? imageUrl : null,
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
