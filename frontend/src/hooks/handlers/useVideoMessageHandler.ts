/**
 * 视频消息处理 Hook
 * 负责视频生成（文本生视频、图片生视频），支持后台轮询
 */

import { type UnifiedModel } from '../../constants/models';
import { createMessage, type Message, type GenerationParams } from '../../services/message';
import {
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
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import {
  extractVideoUrl,
  handleGenerationError,
  createMediaPollingHandler,
  type MediaResponse,
  type MediaGenConfig,
} from './mediaHandlerUtils';
import { VIDEO_TASK_TIMEOUT, VIDEO_POLL_INTERVAL } from '../../config/task';

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
    createMediaPollingHandler(response, config, {
      onMessagePending,
      onMessageSent,
      onMediaTaskSubmitted,
    });
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
              conversation_id: currentConversationId,
            })
          : generateTextToVideo({
              prompt: messageContent,
              model: selectedModel.id as VideoModel,
              n_frames: videoFrames,
              aspect_ratio: videoAspectRatio,
              remove_watermark: removeWatermark,
              wait_for_result: false,
              conversation_id: currentConversationId,
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
          pollInterval: VIDEO_POLL_INTERVAL,
          maxDuration: VIDEO_TASK_TIMEOUT,
          creditsConsumed: response.credits_consumed,
          userMessageTimestamp,
          placeholderTimestamp,
          preCreatedPlaceholderId: tempPlaceholderId,
          generationParams: videoGenerationParams,
          pollFn: getVideoTaskStatus,
          extractMediaUrl: (r) => ({ video_url: extractVideoUrl(r) }),
          shouldPreloadImage: false,
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
