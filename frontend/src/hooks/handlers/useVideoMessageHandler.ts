/**
 * 视频消息处理 Hook
 * 负责视频生成（文本生视频、图片生视频），支持后台轮询
 */

import { type UnifiedModel } from '../../constants/models';
import { createMessage, type Message, type GenerationParams } from '../../services/message';
import {
  createStreamingPlaceholder,
  createMediaTimestamps,
  createMediaOptimisticPair,
} from '../../utils/messageFactory';
import {
  generateTextToVideo,
  generateImageToVideo,
  queryVideoTaskStatus as getVideoTaskStatus,
  type VideoModel,
  type VideoFrames,
  type VideoAspectRatio,
} from '../../services/video';
import { useTaskStore } from '../../stores/useTaskStore';
import { useChatStore } from '../../stores/useChatStore';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import { useAuthStore } from '../../stores/useAuthStore';
import {
  extractVideoUrl,
  handleGenerationError,
  type MediaResponse,
  type MediaGenConfig,
} from './mediaHandlerUtils';

interface UseVideoMessageHandlerParams {
  selectedModel: UnifiedModel;
  videoFrames: VideoFrames;
  videoAspectRatio: VideoAspectRatio;
  removeWatermark: boolean;
  conversationTitle: string;
  onMessagePending: (message: Message) => void;
  onMessageSent: (aiMessage?: Message | null) => void;
  onMediaTaskSubmitted?: () => void;
}

export function useVideoMessageHandler({
  selectedModel,
  videoFrames,
  videoAspectRatio,
  removeWatermark,
  conversationTitle,
  onMessagePending,
  onMessageSent,
  onMediaTaskSubmitted,
}: UseVideoMessageHandlerParams) {
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
        config.placeholderText || '视频生成中...',
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
          return { done: true, error: new Error(result.fail_msg || '视频生成失败') };
        }
        return { done: false };
      },
      {
        onSuccess: async (result: unknown) => {
          const mediaUrl = config.extractMediaUrl(result);
          try {
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
            console.error('保存视频消息失败:', err);
            failMediaTask(taskId);
          }
        },
        onError: async (error: Error) => {
          console.error('视频生成失败:', error);
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
        maxDuration: 30 * 60 * 1000,
      }
    );
  };

  const handleVideoGeneration = async (
    messageContent: string,
    currentConversationId: string,
    imageUrl: string | null = null
  ) => {
    const { replaceMediaPlaceholder } = useConversationRuntimeStore.getState();
    const isImageToVideo = imageUrl && selectedModel.capabilities.imageToVideo;

    const timestamps = createMediaTimestamps();
    const {
      tempPlaceholderId,
      userTimestamp: userMessageTimestamp,
      placeholderTimestamp,
    } = timestamps;

    const { userMessage, placeholder } = createMediaOptimisticPair(
      currentConversationId,
      messageContent,
      isImageToVideo ? imageUrl : null,
      '视频生成中...',
      timestamps
    );
    onMessagePending(userMessage);
    onMessagePending(placeholder);

    const videoGenerationParams: GenerationParams = {
      video: {
        frames: videoFrames,
        aspectRatio: videoAspectRatio,
        removeWatermark,
        model: selectedModel.id,
      },
    };

    try {
      const [, response] = await Promise.all([
        createMessage(currentConversationId, {
          content: messageContent,
          role: 'user',
          image_url: isImageToVideo ? imageUrl : null,
          created_at: userMessageTimestamp,
        }).then((realUserMessage) => onMessagePending(realUserMessage)),
        isImageToVideo
          ? generateImageToVideo({
              prompt: messageContent,
              image_url: imageUrl,
              model: selectedModel.id as VideoModel,
              n_frames: videoFrames,
              aspect_ratio: videoAspectRatio,
              remove_watermark: removeWatermark,
              wait_for_result: false,
            })
          : generateTextToVideo({
              prompt: messageContent,
              model: selectedModel.id as VideoModel,
              n_frames: videoFrames,
              aspect_ratio: videoAspectRatio,
              remove_watermark: removeWatermark,
              wait_for_result: false,
            }),
      ]);

      const successContent = isImageToVideo ? '视频生成完成（图生视频）' : '视频生成完成';

      if (response.status === 'pending' || response.status === 'processing') {
        handleMediaPolling(response, {
          type: 'video',
          conversationId: currentConversationId,
          conversationTitle,
          successContent,
          errorPrefix: '视频生成失败',
          pollInterval: 5000,
          creditsConsumed: response.credits_consumed,
          userMessageTimestamp,
          placeholderTimestamp,
          preCreatedPlaceholderId: tempPlaceholderId,
          generationParams: videoGenerationParams,
          pollFn: getVideoTaskStatus,
          extractMediaUrl: (r) => ({ video_url: extractVideoUrl(r) }),
        });
      } else if (response.status === 'success' && response.video_url) {
        const savedAiMessage = await createMessage(currentConversationId, {
          content: successContent,
          role: 'assistant',
          video_url: response.video_url,
          credits_cost: response.credits_consumed,
          created_at: placeholderTimestamp,
          generation_params: videoGenerationParams,
        });
        replaceMediaPlaceholder(currentConversationId, tempPlaceholderId, savedAiMessage);
        onMessageSent(savedAiMessage);
        if (onMediaTaskSubmitted) onMediaTaskSubmitted();
      } else {
        throw new Error('视频生成失败');
      }
    } catch (error) {
      const errorMessage = await handleGenerationError(
        currentConversationId,
        '视频生成失败',
        error,
        placeholderTimestamp,
        videoGenerationParams
      );
      replaceMediaPlaceholder(currentConversationId, tempPlaceholderId, errorMessage);
      onMessageSent(errorMessage);
      if (onMediaTaskSubmitted) onMediaTaskSubmitted();
    }
  };

  return { handleVideoGeneration };
}
