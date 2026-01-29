/**
 * 图片消息处理 Hook
 * 负责图片生成和编辑，支持后台轮询
 */

import { type UnifiedModel } from '../../constants/models';
import { createMessage, type Message, type GenerationParams } from '../../services/message';
import {
  createStreamingPlaceholder,
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
import { useTaskStore } from '../../stores/useTaskStore';
import { useChatStore } from '../../stores/useChatStore';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import { useAuthStore } from '../../stores/useAuthStore';
import {
  extractImageUrl,
  handleGenerationError,
  type MediaResponse,
  type MediaGenConfig,
} from './mediaHandlerUtils';

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
    const { startMediaTask, startPolling, completeMediaTask, failMediaTask } =
      useTaskStore.getState();
    const { replaceMediaPlaceholder } = useConversationRuntimeStore.getState();
    const { addMessageToCache } = useChatStore.getState();
    const { refreshUser } = useAuthStore.getState();

    const taskId = response.task_id;
    const placeholderId = config.preCreatedPlaceholderId || `streaming-${taskId}`;
    const placeholderTimestamp = config.placeholderTimestamp;

    if (!config.preCreatedPlaceholderId) {
      const placeholderMessage = createStreamingPlaceholder(
        config.conversationId,
        placeholderId,
        config.placeholderText || '图片生成中...',
        placeholderTimestamp
      );
      onMessagePending(placeholderMessage);
    }

    startMediaTask({
      taskId,
      conversationId: config.conversationId,
      conversationTitle: config.conversationTitle,
      type: config.type,
      placeholderId,
    });

    if (onMediaTaskSubmitted) onMediaTaskSubmitted();

    startPolling(
      taskId,
      async () => {
        const result = await config.pollFn(taskId);
        if (result.status === 'success') return { done: true, result };
        if (result.status === 'failed') {
          return { done: true, error: new Error(result.fail_msg || '图片生成失败') };
        }
        return { done: false };
      },
      {
        onSuccess: async (result: unknown) => {
          const mediaUrl = config.extractMediaUrl(result);
          try {
            // 立即预加载图片（后台下载，加速显示）
            if (mediaUrl.image_url) {
              const img = new Image();
              img.src = mediaUrl.image_url;
            }

            const savedAiMessage = await createMessage(config.conversationId, {
              content: config.successContent,
              role: 'assistant',
              image_url: mediaUrl.image_url,
              video_url: mediaUrl.video_url,
              credits_cost: config.creditsConsumed,
              created_at: placeholderTimestamp,
              generation_params: config.generationParams,
            });

            const messageWithCorrectTime: Message = {
              ...savedAiMessage,
              created_at: placeholderTimestamp,
            };
            replaceMediaPlaceholder(config.conversationId, placeholderId, messageWithCorrectTime);

            addMessageToCache(config.conversationId, {
              id: savedAiMessage.id,
              role: savedAiMessage.role as 'user' | 'assistant',
              content: savedAiMessage.content,
              imageUrl: savedAiMessage.image_url ?? undefined,
              videoUrl: savedAiMessage.video_url ?? undefined,
              createdAt: placeholderTimestamp,
            });

            completeMediaTask(taskId);
            refreshUser();
            onMessageSent(savedAiMessage);
          } catch (err) {
            console.error('保存图片消息失败:', err);
            failMediaTask(taskId);
          }
        },
        onError: async (error: Error) => {
          console.error('图片生成失败:', error);
          const errorMessage = await handleGenerationError(
            config.conversationId,
            config.errorPrefix,
            error,
            placeholderTimestamp,
            config.generationParams
          );
          replaceMediaPlaceholder(config.conversationId, placeholderId, errorMessage);
          failMediaTask(taskId);
          onMessageSent(errorMessage);
        },
      },
      {
        interval: config.pollInterval,
        maxDuration: 10 * 60 * 1000,
      }
    );
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
              image_urls: [imageUrl],
              size: aspectRatio,
              output_format: outputFormat,
              wait_for_result: false,
            })
          : generateImage({
              prompt: messageContent,
              model: selectedModel.id as ImageModel,
              size: aspectRatio,
              output_format: outputFormat,
              resolution: selectedModel.supportsResolution ? resolution : undefined,
              wait_for_result: false,
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
          pollInterval: 2000,
          creditsConsumed: response.credits_consumed,
          userMessageTimestamp,
          placeholderTimestamp,
          preCreatedPlaceholderId: tempPlaceholderId,
          generationParams: imageGenerationParams,
          pollFn: getImageTaskStatus,
          extractMediaUrl: (r) => ({ image_url: extractImageUrl(r) }),
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
