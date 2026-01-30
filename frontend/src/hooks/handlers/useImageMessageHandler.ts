/**
 * 图片消息处理 Hook
 * 负责图片生成和编辑，支持后台轮询
 */

import { type UnifiedModel } from '../../constants/models';
import { createMessage, type Message, type GenerationParams } from '../../services/message';
import {
  createMediaTimestamps,
  createMediaOptimisticPair,
} from '../../utils/messageFactory';
import {
  generateImage,
  editImage,
  queryTaskStatus as getImageTaskStatus,
  type ImageModel,
  type AspectRatio,
  type ImageResolution,
  type ImageOutputFormat,
} from '../../services/image';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import {
  extractImageUrl,
  handleGenerationError,
  createMediaPollingHandler,
  type MediaResponse,
  type MediaGenConfig,
} from './mediaHandlerUtils';
import { IMAGE_TASK_TIMEOUT, IMAGE_POLL_INTERVAL } from '../../config/task';

interface UseImageMessageHandlerParams {
  selectedModel: UnifiedModel;
  aspectRatio: AspectRatio;
  resolution: ImageResolution;
  outputFormat: ImageOutputFormat;
  conversationTitle: string;
  onMessagePending: (message: Message) => void;
  onMessageSent: (aiMessage?: Message | null) => void;
  onMediaTaskSubmitted?: () => void;
}

export function useImageMessageHandler({
  selectedModel,
  aspectRatio,
  resolution,
  outputFormat,
  conversationTitle,
  onMessagePending,
  onMessageSent,
  onMediaTaskSubmitted,
}: UseImageMessageHandlerParams) {
  /** 通用媒体生成轮询处理 */
  const handleMediaPolling = (response: MediaResponse, config: MediaGenConfig) => {
    createMediaPollingHandler(response, config, {
      onMessagePending,
      onMessageSent,
      onMediaTaskSubmitted,
    });
  };

  const handleImageGeneration = async (
    messageContent: string,
    currentConversationId: string,
    imageUrl: string | null = null
  ) => {
    const { replaceMediaPlaceholder } = useConversationRuntimeStore.getState();

    const timestamps = createMediaTimestamps();
    const {
      tempPlaceholderId,
      userTimestamp: userMessageTimestamp,
      placeholderTimestamp,
    } = timestamps;

    const { userMessage, placeholder } = createMediaOptimisticPair(
      currentConversationId,
      messageContent,
      imageUrl,
      '图片生成中...',
      timestamps
    );
    onMessagePending(userMessage);
    onMessagePending(placeholder);

    const imageGenerationParams: GenerationParams = {
      image: {
        aspectRatio,
        resolution,
        outputFormat,
        model: selectedModel.id,
      },
    };

    try {
      const [, response] = await Promise.all([
        createMessage(currentConversationId, {
          content: messageContent,
          role: 'user',
          image_url: imageUrl,
          created_at: userMessageTimestamp,
        }).then((realUserMessage) => onMessagePending(realUserMessage)),
        imageUrl
          ? editImage({
              prompt: messageContent,
              // 解析逗号分隔的多图 URL 为数组
              image_urls: imageUrl.split(',').map(url => url.trim()).filter(Boolean),
              size: aspectRatio,
              output_format: outputFormat,
              wait_for_result: false,
              conversation_id: currentConversationId,
            })
          : generateImage({
              prompt: messageContent,
              model: selectedModel.id as ImageModel,
              size: aspectRatio,
              output_format: outputFormat,
              resolution: selectedModel.supportsResolution ? resolution : undefined,
              wait_for_result: false,
              conversation_id: currentConversationId,
            }),
      ]);

      const successContent = imageUrl ? '图片编辑完成' : '图片已生成完成';

      if (response.status === 'pending' || response.status === 'processing') {
        handleMediaPolling(response, {
          type: 'image',
          conversationId: currentConversationId,
          conversationTitle,
          successContent,
          errorPrefix: '图片处理失败',
          pollInterval: IMAGE_POLL_INTERVAL,
          maxDuration: IMAGE_TASK_TIMEOUT,
          creditsConsumed: response.credits_consumed,
          userMessageTimestamp,
          placeholderTimestamp,
          preCreatedPlaceholderId: tempPlaceholderId,
          generationParams: imageGenerationParams,
          pollFn: getImageTaskStatus,
          extractMediaUrl: (r) => ({ image_url: extractImageUrl(r) }),
          shouldPreloadImage: true,
        });
      } else if (response.status === 'success' && response.image_urls?.length) {
        const savedAiMessage = await createMessage(currentConversationId, {
          content: successContent,
          role: 'assistant',
          image_url: response.image_urls[0],
          credits_cost: response.credits_consumed,
          created_at: placeholderTimestamp,
          generation_params: imageGenerationParams,
        });
        replaceMediaPlaceholder(currentConversationId, tempPlaceholderId, savedAiMessage);
        onMessageSent(savedAiMessage);
        if (onMediaTaskSubmitted) onMediaTaskSubmitted();
      } else {
        throw new Error('图片处理失败');
      }
    } catch (error) {
      const errorMessage = await handleGenerationError(
        currentConversationId,
        '图片处理失败',
        error,
        placeholderTimestamp,
        imageGenerationParams
      );
      replaceMediaPlaceholder(currentConversationId, tempPlaceholderId, errorMessage);
      onMessageSent(errorMessage);
      if (onMediaTaskSubmitted) onMediaTaskSubmitted();
    }
  };

  return { handleImageGeneration };
}
